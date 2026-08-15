"""刷题 API（docs/03-api.md §4）"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Attempt, Book, Chapter, Quiz
from backend.app.schemas import (
    AttemptReq,
    AttemptResp,
    QuizImportItem,
    QuizImportReq,
    QuizListItem,
    QuizListResp,
    SelfGradeReq,
)
from backend.app.worker.tasks import submit

router = APIRouter(prefix="/api", tags=["quizzes"])


def _parse_options(options_json: str | None) -> list[str] | None:
    if not options_json:
        return None
    try:
        data = json.loads(options_json)
        if isinstance(data, list):
            return [str(x) for x in data]
        return None
    except json.JSONDecodeError:
        return None


@router.post("/books/{book_id}/generate-quizzes")
def generate_quizzes(book_id: int, db: Session = Depends(get_db)):
    """AI 生成题目（P0 简化：后台任务直接调用 LLM 并入库）。"""
    from backend.app.services.llm import LLMRouter, load_llm_config

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
        from backend.app.models import Chunk
        # 从数据库读取 LLM 配置（设置页的切换/Key 才能生效）
        cfg = load_llm_config(db)
        provider = LLMRouter.get("auto", cfg)
        created = 0
        for i, ch in enumerate(chapters):
            record.progress = i / len(chapters)
            record.stage = f"生成题目: {ch.title}"
            chunks = db.scalars(
                select(Chunk).where(Chunk.chapter_id == ch.id).order_by(Chunk.chunk_index).limit(6)
            ).all()
            if not chunks:
                continue
            material = "\n\n".join(c.content[:600] for c in chunks)[:4000]
            prompt = [
                {"role": "system", "content": (
                    "你是专业课出题老师。根据教材片段，生成 5 道单项选择题和 5 道简答题。"
                    "只输出 JSON 数组，格式："
                    '[{"type":"choice","question":"题干","options":["A.…","B.…","C.…","D.…"],'
                    '"answer":"A","explanation":"解析"},'
                    '{"type":"short","question":"题干","answer":"参考答案","explanation":"要点"}]'
                )},
                {"role": "user", "content": material},
            ]
            answer = ""
            try:
                async for delta in provider.stream_chat(prompt):
                    answer += delta
                data = json.loads(answer.strip().strip("`").removeprefix("json"))
                for item in data[:10]:
                    q_type = "choice" if item.get("type") == "choice" else "short"
                    opts = item.get("options")
                    db.add(Quiz(
                        book_id=book_id, chapter_id=ch.id, q_type=q_type,
                        question=str(item.get("question", "")),
                        options_json=json.dumps(opts, ensure_ascii=False) if opts else None,
                        answer=str(item.get("answer", "")),
                        explanation=item.get("explanation"),
                        source="auto",
                    ))
                    created += 1
                db.commit()
            except Exception:  # noqa: BLE001
                continue
        return {"generated": created}

    record = submit("quizzes", run)
    return {"task_id": record.id, "estimated": len(chapters) * 10}


@router.post("/quizzes/batch-import")
def batch_import(req: QuizImportReq, db: Session = Depends(get_db)):
    """导入题目（AI 生成预览确认后调用，P1 支持）。"""
    count = 0
    for item in req.quizzes:
        quiz = Quiz(
            book_id=item.chapter_id and db.get(Chapter, item.chapter_id).book_id if item.chapter_id else None,
            chapter_id=item.chapter_id, q_type=item.q_type, question=item.question,
            options_json=item.options_json, answer=item.answer, explanation=item.explanation,
            source="manual",
        )
        if quiz.book_id is None:
            raise HTTPException(400, "chapter_id 无效或未提供")
        db.add(quiz)
        count += 1
    db.commit()
    return {"imported": count}


@router.get("/quizzes", response_model=QuizListResp)
def list_quizzes(
    book_id: int | None = Query(default=None),
    chapter_id: int | None = Query(default=None),
    q_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    q = select(Quiz)
    if book_id:
        q = q.where(Quiz.book_id == book_id)
    if chapter_id:
        q = q.where(Quiz.chapter_id == chapter_id)
    if q_type:
        q = q.where(Quiz.q_type == q_type)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    quizzes = db.scalars(q.order_by(Quiz.id).offset((page - 1) * page_size).limit(page_size)).all()

    items = []
    for quiz in quizzes:
        book = db.get(Book, quiz.book_id)
        chapter = db.get(Chapter, quiz.chapter_id) if quiz.chapter_id else None
        items.append(QuizListItem(
            id=quiz.id, q_type=quiz.q_type, question=quiz.question,
            options=_parse_options(quiz.options_json), difficulty=quiz.difficulty,
            book_title=book.title if book else "",
            chapter_title=chapter.title if chapter else None,
        ))
    return QuizListResp(total=total, items=items)


@router.post("/quizzes/{quiz_id}/attempt", response_model=AttemptResp)
def attempt(quiz_id: int, req: AttemptReq, db: Session = Depends(get_db)):
    quiz = db.get(Quiz, quiz_id)
    if not quiz:
        raise HTTPException(404, "题目不存在")

    user_answer = req.user_answer.strip()
    if quiz.q_type in ("choice", "blank"):
        # 选择/填空：忽略大小写与首尾空白比对
        is_correct = user_answer.upper() == quiz.answer.strip().upper()
    else:
        # 简答：不自动判分，需自评
        is_correct = 0
        db.add(Attempt(quiz_id=quiz_id, user_answer=user_answer, is_correct=0, is_self_graded=1))
        db.commit()
        return AttemptResp(is_correct=False, answer=quiz.answer, explanation=quiz.explanation)

    db.add(Attempt(quiz_id=quiz_id, user_answer=user_answer, is_correct=1 if is_correct else 0))
    db.commit()

    # 正确率统计
    correct = db.scalar(
        select(func.count()).select_from(Attempt).where(Attempt.quiz_id == quiz_id, Attempt.is_correct == 1)
    ) or 0
    total = db.scalar(
        select(func.count()).select_from(Attempt).where(Attempt.quiz_id == quiz_id)
    ) or 1
    return AttemptResp(
        is_correct=is_correct, answer=quiz.answer, explanation=quiz.explanation,
        correct_rate=round(correct / total, 2),
    )


@router.post("/quizzes/{quiz_id}/self-grade", response_model=AttemptResp)
def self_grade(quiz_id: int, req: SelfGradeReq, db: Session = Depends(get_db)):
    quiz = db.get(Quiz, quiz_id)
    if not quiz:
        raise HTTPException(404, "题目不存在")
    db.add(Attempt(quiz_id=quiz_id, user_answer="", is_correct=1 if req.is_correct else 0, is_self_graded=1))
    db.commit()
    return AttemptResp(
        is_correct=req.is_correct, answer=quiz.answer, explanation=quiz.explanation,
    )


@router.get("/quizzes/wrong", response_model=QuizListResp)
def wrong_quizzes(
    book_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """错题本：最近一次作答错误的题目。"""
    sub = (
        select(
            Attempt.quiz_id,
            Attempt.is_correct,
            func.row_number().over(
                partition_by=Attempt.quiz_id, order_by=Attempt.answered_at.desc()
            ).label("rn"),
        ).subquery()
    )
    latest_wrong = select(sub.c.quiz_id).where(sub.c.rn == 1, sub.c.is_correct == 0)
    q = select(Quiz).where(Quiz.id.in_(latest_wrong))
    if book_id:
        q = q.where(Quiz.book_id == book_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    quizzes = db.scalars(q.order_by(Quiz.id).offset((page - 1) * page_size).limit(page_size)).all()
    items = []
    for quiz in quizzes:
        book = db.get(Book, quiz.book_id)
        chapter = db.get(Chapter, quiz.chapter_id) if quiz.chapter_id else None
        items.append(QuizListItem(
            id=quiz.id, q_type=quiz.q_type, question=quiz.question,
            options=_parse_options(quiz.options_json), difficulty=quiz.difficulty,
            book_title=book.title if book else "",
            chapter_title=chapter.title if chapter else None,
        ))
    return QuizListResp(total=total, items=items)


@router.patch("/quizzes/{quiz_id}")
def update_quiz(quiz_id: int, req: dict, db: Session = Depends(get_db)):
    quiz = db.get(Quiz, quiz_id)
    if not quiz:
        raise HTTPException(404, "题目不存在")
    for key in ("question", "answer", "explanation", "difficulty"):
        if key in req and req[key] is not None:
            setattr(quiz, key, req[key])
    db.commit()
    return {"id": quiz.id, "ok": True}


@router.delete("/quizzes/{quiz_id}", status_code=204)
def delete_quiz(quiz_id: int, db: Session = Depends(get_db)):
    quiz = db.get(Quiz, quiz_id)
    if not quiz:
        raise HTTPException(404, "题目不存在")
    db.delete(quiz)
    db.commit()
