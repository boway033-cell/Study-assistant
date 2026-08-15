"""卡片 API（docs/03-api.md §3）"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.models import Book, Card, Chapter, Chunk, ReviewLog
from backend.app.schemas import (
    CardCreateReq,
    CardResp,
    CardUpdateReq,
    ReviewQueueItem,
    ReviewQueueResp,
    ReviewReq,
    ReviewResp,
)
from backend.app.services.srs.fsrs_service import new_card_due, review_card
from backend.app.worker.tasks import get_task, submit

router = APIRouter(prefix="/api", tags=["cards"])


def _to_resp(card: Card, db: Session) -> CardResp:
    book = db.get(Book, card.book_id)
    chapter = db.get(Chapter, card.chapter_id) if card.chapter_id else None
    return CardResp(
        id=card.id, book_id=card.book_id, chapter_id=card.chapter_id,
        front=card.front, back=card.back, tags=card.tags, state=card.state,
        due=card.due, reps=card.reps, lapses=card.lapses,
        book_title=book.title if book else "",
        chapter_title=chapter.title if chapter else None,
    )


@router.post("/books/{book_id}/generate-cards")
def generate_cards(book_id: int, db: Session = Depends(get_db)):
    """生成卡片（P0 简化：直接调用 LLM 生成并入库，见 docs/03-api.md §3.1）。"""
    from backend.app.services.rag import fts, retriever
    from backend.app.services.llm import LLMRouter
    import json

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    if book.status != "ready":
        raise HTTPException(409, "书籍尚未解析完成")

    chapters = db.scalars(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_index)
    ).all()
    if not chapters:
        raise HTTPException(400, "书籍没有章节")

    async def run(record):
        provider = LLMRouter.get()
        created = 0
        failed = 0
        for i, ch in enumerate(chapters):
            record.progress = i / len(chapters)
            record.stage = f"生成卡片: {ch.title}"
            try:
                cards = await _gen_cards_for_chapter(provider, db, book_id, ch)
                created += len(cards)
            except Exception:  # noqa: BLE001
                failed += 1
        return {"generated": created, "failed": failed}

    record = submit("cards", run)
    return {"task_id": record.id, "estimated": len(chapters) * 10}


async def _gen_cards_for_chapter(provider, db: Session, book_id: int, chapter: Chapter) -> int:
    """对一章生成概念卡片：检索该章文本 → LLM 提取问答对 → 入库。"""
    import json

    from backend.app.services.rag import fts

    # 取该章前几个 chunk 作为素材
    chunks = db.scalars(
        select(Chunk).where(Chunk.chapter_id == chapter.id).order_by(Chunk.chunk_index).limit(8)
    ).all()
    if not chunks:
        return 0
    material = "\n\n".join(c.content[:800] for c in chunks)[:6000]

    prompt = [
        {"role": "system", "content": (
            "你是专业课学习辅助工具。根据提供的教材片段，提炼 5 个最重要的知识点，"
            "每个知识点生成一张问答卡片。只输出 JSON 数组，格式："
            '[{"front": "问题", "back": "答案"}]。答案要准确、简洁。'
        )},
        {"role": "user", "content": material},
    ]

    answer = ""
    async for delta in provider.stream_chat(prompt):
        answer += delta

    try:
        data = json.loads(answer.strip().strip("`").removeprefix("json"))
        if not isinstance(data, list):
            return 0
    except json.JSONDecodeError:
        return 0

    from backend.app.models import Chunk as _Chunk
    for item in data[:5]:
        front = str(item.get("front", "")).strip()
        back = str(item.get("back", "")).strip()
        if not front or not back:
            continue
        db.add(Card(book_id=book_id, chapter_id=chapter.id, front=front, back=back,
                    source="auto", state="New", due=new_card_due()))
    db.commit()
    return len(data[:5])


@router.get("/cards/review-queue", response_model=ReviewQueueResp)
def review_queue(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    now = datetime.now()
    # 到期卡（due <= now）
    due_cards = db.scalars(
        select(Card).where(Card.due <= now).order_by(Card.due).limit(limit)
    ).all()
    # 新卡限量（daily_new_cards）
    new_count = settings.daily_new_cards
    new_cards = db.scalars(
        select(Card).where(Card.state == "New").order_by(Card.created_at).limit(new_count)
    ).all()

    # 合并去重（新卡如果也 due<=now 会重复）
    seen: set[int] = set()
    items: list[ReviewQueueItem] = []
    for card in due_cards + new_cards:
        if card.id in seen:
            continue
        seen.add(card.id)
        book = db.get(Book, card.book_id)
        chapter = db.get(Chapter, card.chapter_id) if card.chapter_id else None
        items.append(ReviewQueueItem(
            id=card.id, front=card.front, back=card.back, state=card.state,
            due=card.due, book_title=book.title if book else "",
            chapter_title=chapter.title if chapter else None,
        ))
        if len(items) >= limit:
            break

    due_count = db.scalar(select(func.count()).select_from(Card).where(Card.due <= now)) or 0
    return ReviewQueueResp(due_count=due_count, new_count=len(new_cards), items=items)


@router.post("/cards/{card_id}/review", response_model=ReviewResp)
def review(card_id: int, req: ReviewReq, db: Session = Depends(get_db)):
    card = db.get(Card, card_id)
    if not card:
        raise HTTPException(404, "卡片不存在")
    try:
        summary = review_card(card, req.rating)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    # 复习记录
    db.add(ReviewLog(
        card_id=card.id, rating=req.rating,
        state_before=summary["state_before"], state_after=summary["state"],
        elapsed_days=summary["elapsed_days"], scheduled_days=summary["scheduled_days"],
    ))
    db.commit()
    return ReviewResp(
        card_id=card.id, state=card.state, stability=card.stability,
        difficulty=card.difficulty, due=card.due, scheduled_days=card.scheduled_days,
    )


@router.get("/cards", response_model=list[CardResp])
def list_cards(
    book_id: int | None = Query(default=None),
    chapter_id: int | None = Query(default=None),
    state: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = select(Card)
    if book_id:
        q = q.where(Card.book_id == book_id)
    if chapter_id:
        q = q.where(Card.chapter_id == chapter_id)
    if state:
        q = q.where(Card.state == state)
    if tag:
        q = q.where(Card.tags.like(f"%{tag}%"))
    cards = db.scalars(q.order_by(Card.created_at.desc()).limit(500)).all()
    return [_to_resp(c, db) for c in cards]


@router.post("/cards", response_model=CardResp, status_code=201)
def create_card(req: CardCreateReq, db: Session = Depends(get_db)):
    if not db.get(Book, req.book_id):
        raise HTTPException(404, "书籍不存在")
    card = Card(book_id=req.book_id, chapter_id=req.chapter_id, front=req.front,
                back=req.back, tags=req.tags, source="manual", state="New",
                due=new_card_due())
    db.add(card)
    db.commit()
    db.refresh(card)
    return _to_resp(card, db)


@router.patch("/cards/{card_id}", response_model=CardResp)
def update_card(card_id: int, req: CardUpdateReq, db: Session = Depends(get_db)):
    card = db.get(Card, card_id)
    if not card:
        raise HTTPException(404, "卡片不存在")
    if req.front is not None:
        card.front = req.front
    if req.back is not None:
        card.back = req.back
    if req.tags is not None:
        card.tags = req.tags
    db.commit()
    return _to_resp(card, db)


@router.delete("/cards/{card_id}", status_code=204)
def delete_card(card_id: int, db: Session = Depends(get_db)):
    card = db.get(Card, card_id)
    if not card:
        raise HTTPException(404, "卡片不存在")
    db.delete(card)
    db.commit()
