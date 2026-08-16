"""深度分析 API：三级标题目录提取/核对/AI补全/逐章总结/Markdown + 文献分类"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, BookDeep, Chapter, Chunk
from backend.app.services.deep_analysis import (
    build_section_texts,
    complete_with_ai,
    extract_titles_3level,
    summarize_by_toc,
    to_markdown,
    verify_toc,
)

router = APIRouter(prefix="/api", tags=["deep"])

CATEGORIES = ["数学", "管理学", "经济学", "计算机", "英语", "政治", "物理", "化学", "生物", "法学", "文学", "历史", "哲学", "其他"]


def _page_texts_from_chunks(db: Session, book_id: int) -> list[str]:
    """由 chunks 按 page_start 重建页文本（标题检测用首行即可）。"""
    chunks = db.scalars(
        select(Chunk).where(Chunk.book_id == book_id).order_by(Chunk.chunk_index)
    ).all()
    pages: dict[int, list[str]] = {}
    for c in chunks:
        p = c.page_start or 1
        pages.setdefault(p, []).append(c.content)
    maxp = max(pages.keys()) if pages else 0
    return ["\n".join(pages.get(i, [])) for i in range(1, maxp + 1)]


async def run_deep_analysis(record, book_id: int) -> dict:
    """执行深度分析：标题提取→核对→AI补全→逐章总结→Markdown。"""
    from backend.app.core.database import SessionLocal
    from backend.app.services.llm import LLMRouter, load_llm_config
    from backend.app.worker.tasks import update_progress

    db = SessionLocal()
    try:
        book = db.get(Book, book_id)
        if not book or book.status != "ready":
            raise ValueError("书籍不可用")

        deep = db.scalar(select(BookDeep).where(BookDeep.book_id == book_id))
        if not deep:
            deep = BookDeep(book_id=book_id, status="running")
            db.add(deep)
        else:
            deep.status = "running"
            deep.error_msg = None
        db.commit()

        update_progress(record, 0.1, "deep", "正在提取三级标题目录...")
        pages = _page_texts_from_chunks(db, book_id)
        toc = extract_titles_3level(pages)
        # 本地提取失败 → 用现有 chapters 表生成目录兜底
        if not toc:
            chapters = db.scalars(
                select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_index)
            ).all()
            toc = [{"title": ch.title, "level": ch.level or 1, "page": ch.start_page or 1} for ch in chapters]

        verify = verify_toc(toc)
        update_progress(record, 0.3, "deep", f"核对完成：{verify['chapters']}章/{verify['sections']}节，缺失 {len(verify['issues'])} 项")

        # AI 补全（有 Key 时）
        cfg = load_llm_config(db)
        provider = LLMRouter.get("auto", cfg)
        use_ai = bool(cfg.get("deepseek_api_key"))
        if use_ai and verify["issues"]:
            update_progress(record, 0.45, "deep", "AI 补全缺失标题...")
            toc = await complete_with_ai(provider, toc, verify["issues"], pages)
            verify = verify_toc(toc)
            update_progress(record, 0.55, "deep", f"补全后核对：缺失 {len(verify['issues'])} 项")

        # 逐章 AI 总结
        summaries: list[dict] = []
        if use_ai:
            update_progress(record, 0.6, "deep", "AI 按目录逐章精读总结...")
            chapters = db.scalars(
                select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_index)
            ).all()
            ch_texts: dict[int, str] = {}
            for ch in chapters:
                chunks = db.scalars(
                    select(Chunk).where(Chunk.chapter_id == ch.id).order_by(Chunk.chunk_index)
                ).all()
                ch_texts[ch.order_index] = "\n".join(c.content for c in chunks)
            def _sum_progress(i, total, title):
                update_progress(record, 0.6 + 0.2 * i / max(total, 1), "deep",
                                f"AI 精读总结 {i}/{total}：{title[:30]}")
            summaries = await summarize_by_toc(provider, book.title, toc, ch_texts,
                                               on_progress=_sum_progress)
            update_progress(record, 0.85, "deep", "正在生成 Markdown...")
        else:
            update_progress(record, 0.85, "deep", "未配置 AI，生成纯本地 Markdown（无 AI 总结）...")

        # Markdown
        section_texts = build_section_texts(
            [(c.page_start or 1, c.content) for c in db.scalars(
                select(Chunk).where(Chunk.book_id == book_id).order_by(Chunk.chunk_index)).all()],
            toc,
        )
        md = to_markdown(book.title, toc, summaries, section_texts)

        clean_toc = [{k: v for k, v in t.items() if k != "parent"} for t in toc]
        deep.toc_json = json.dumps(clean_toc, ensure_ascii=False)
        deep.summaries_json = json.dumps(summaries, ensure_ascii=False)
        deep.markdown = md
        deep.status = "done"
        db.commit()
        update_progress(record, 1.0, "deep", "完成")
        return {"toc": len(toc), "chapters": verify["chapters"], "sections": verify["sections"],
                "summaries": len(summaries), "markdown_chars": len(md), "ai": use_ai}
    except Exception as e:  # noqa: BLE001
        db.rollback()
        deep = db.scalar(select(BookDeep).where(BookDeep.book_id == book_id))
        if deep:
            deep.status = "failed"
            deep.error_msg = str(e)
            db.commit()
        raise
    finally:
        db.close()


@router.post("/books/{book_id}/deep-analyze", status_code=202)
def deep_analyze(book_id: int, db: Session = Depends(get_db)):
    """手动触发深度分析（后台任务）。"""
    from backend.app.worker.tasks import submit

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    if book.status != "ready":
        raise HTTPException(409, "书籍尚未解析完成")
    record = submit("deep", lambda rec: run_deep_analysis(rec, book_id))
    return {"task_id": record.id, "status": "running"}


@router.get("/books/{book_id}/deep")
def get_deep(book_id: int, db: Session = Depends(get_db)):
    """获取深度分析产物（状态/目录/总结/Markdown）。"""
    deep = db.scalar(select(BookDeep).where(BookDeep.book_id == book_id))
    if not deep:
        return {"status": "none", "toc": [], "summaries": [], "markdown": "", "error_msg": None}
    try:
        toc = json.loads(deep.toc_json) if deep.toc_json else []
    except (ValueError, TypeError):
        toc = []
    try:
        summaries = json.loads(deep.summaries_json) if deep.summaries_json else []
    except (ValueError, TypeError):
        summaries = []
    return {
        "status": deep.status, "toc": toc, "summaries": summaries,
        "markdown": deep.markdown or "", "error_msg": deep.error_msg,
        "updated_at": deep.updated_at.isoformat() if deep.updated_at else None,
    }


# ---------- 文献分类 ----------
async def _classify_book(provider, book: Book, keywords: list[str]) -> str:
    prompt = [
        {"role": "system", "content": (
            "你是文献分类助手。根据书名、章节与关键词判断所属学科类别。"
            f"只能从这些类别中选择一个：{'、'.join(CATEGORIES)}。只输出类别名，不要解释。"
        )},
        {"role": "user", "content": f"书名：《{book.title}》\n关键词：{','.join(keywords[:30]) or '无'}"},
    ]
    answer = ""
    try:
        async for delta in provider.stream_chat(prompt):
            answer += delta
    except Exception:  # noqa: BLE001
        return "其他"
    answer = answer.strip()
    for c in CATEGORIES:
        if c in answer:
            return c
    return "其他"


@router.post("/books/{book_id}/classify")
async def classify_book(book_id: int, db: Session = Depends(get_db)):
    """AI 分析文献主题并分类（存 books.category）。"""
    from backend.app.models import BookAnalysis
    from backend.app.services.llm import LLMRouter, load_llm_config

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    cfg = load_llm_config(db)
    if not cfg.get("deepseek_api_key"):
        raise HTTPException(400, "未配置 DeepSeek API Key，无法分类")
    provider = LLMRouter.get("auto", cfg)
    keywords: list[str] = []
    analysis = db.scalar(select(BookAnalysis).where(BookAnalysis.book_id == book_id))
    if analysis and analysis.keywords_json:
        try:
            keywords = json.loads(analysis.keywords_json)
        except (ValueError, TypeError):
            keywords = []
    category = await _classify_book(provider, book, keywords)
    book.category = category
    db.commit()
    return {"category": category}


class CategoryReq(BaseModel):
    category: str


@router.patch("/books/{book_id}/category")
def set_category(book_id: int, req: CategoryReq, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    book.category = req.category.strip() or None
    db.commit()
    return {"category": book.category}


@router.get("/deep/status")
def deep_status_all(db: Session = Depends(get_db)):
    """全部书籍的深度分析状态。"""
    from backend.app.models import BookDeep as _BookDeep
    rows = db.execute(select(_BookDeep.book_id, _BookDeep.status)).all()
    return {str(bid): st for bid, st in rows}


@router.post("/books/classify-all")
async def classify_all(db: Session = Depends(get_db)):
    """AI 一键分类全部书籍（逐个，失败跳过）。"""
    from backend.app.models import BookAnalysis as _BookAnalysis
    from backend.app.services.llm import LLMRouter, load_llm_config

    cfg = load_llm_config(db)
    if not cfg.get("deepseek_api_key"):
        raise HTTPException(400, "未配置 DeepSeek API Key，无法分类")
    provider = LLMRouter.get("auto", cfg)
    books = db.scalars(select(Book).where(Book.status == "ready")).all()
    done = {}
    for book in books:
        if book.category:
            done[book.id] = book.category
            continue
        analysis = db.scalar(select(_BookAnalysis).where(_BookAnalysis.book_id == book.id))
        keywords = []
        if analysis and analysis.keywords_json:
            try:
                keywords = json.loads(analysis.keywords_json)
            except (ValueError, TypeError):
                keywords = []
        cat = await _classify_book(provider, book, keywords)
        book.category = cat
        done[book.id] = cat
        db.commit()
    return {"classified": done}
