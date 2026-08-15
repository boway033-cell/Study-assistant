"""统计 API（docs/03-api.md §5）— 基于题目作答数据（卡片学习已取消）"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Attempt, Book, Chapter, Quiz
from backend.app.schemas import (
    ActivityResp,
    ChapterMastery,
    DailyActivity,
    MasteryResp,
    OverviewResp,
    WeaknessItem,
    WeaknessResp,
)

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _chapter_wrong_rate(db: Session, chapter_id: int) -> float:
    """章节错题率：该章题目最近一次作答错误的占比（无作答记录返回 0）。"""
    latest = (
        select(
            Attempt.quiz_id,
            Attempt.is_correct,
            func.row_number().over(
                partition_by=Attempt.quiz_id, order_by=Attempt.answered_at.desc()
            ).label("rn"),
        ).subquery()
    )
    latest_join = (
        select(latest.c.quiz_id, latest.c.is_correct)
        .where(latest.c.rn == 1)
        .join(Quiz, Quiz.id == latest.c.quiz_id)
        .where(Quiz.chapter_id == chapter_id)
        .subquery()
    )
    total = db.scalar(select(func.count()).select_from(latest_join)) or 0
    wrong = db.scalar(
        select(func.count()).select_from(latest_join).where(latest_join.c.is_correct == 0)
    ) or 0
    return round(wrong / total, 3) if total else 0.0


def _chapter_mastery(wrong_rate: float, has_data: bool) -> float:
    """掌握度 = 1 - 错题率；无作答数据返回 0。"""
    if not has_data:
        return 0.0
    return round(1 - wrong_rate, 3)


@router.get("/overview", response_model=OverviewResp)
def overview(db: Session = Depends(get_db)):
    book_count = db.scalar(select(func.count()).select_from(Book)) or 0
    quiz_count = db.scalar(select(func.count()).select_from(Quiz)) or 0
    attempts_total = db.scalar(select(func.count()).select_from(Attempt)) or 0

    # 平均掌握度：各章掌握度按题量加权
    chapters = db.scalars(select(Chapter)).all()
    weighted = 0.0
    total_quizzes = 0
    for ch in chapters:
        quizzes = db.scalar(
            select(func.count()).select_from(Quiz).where(Quiz.chapter_id == ch.id)
        ) or 0
        if not quizzes:
            continue
        wrong_rate = _chapter_wrong_rate(db, ch.id)
        weighted += _chapter_mastery(wrong_rate, True) * quizzes
        total_quizzes += quizzes
    avg_mastery = round(weighted / total_quizzes, 3) if total_quizzes else 0.0

    # 连续学习天数（按有作答记录的连续天数）
    dates = set(
        db.scalars(select(func.date(Attempt.answered_at)).distinct()).all()
    )
    streak = 0
    d = datetime.now().date()
    while d.isoformat() in dates:
        streak += 1
        d -= timedelta(days=1)

    return OverviewResp(
        book_count=book_count, quiz_count=quiz_count,
        attempts_total=attempts_total, avg_mastery=avg_mastery, streak_days=streak,
    )


@router.get("/mastery", response_model=MasteryResp)
def mastery(book_id: int, db: Session = Depends(get_db)):
    chapters = db.scalars(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_index)
    ).all()
    items = []
    for ch in chapters:
        quizzes = db.scalar(
            select(func.count()).select_from(Quiz).where(Quiz.chapter_id == ch.id)
        ) or 0
        wrong_rate = _chapter_wrong_rate(db, ch.id)
        items.append(ChapterMastery(
            chapter_id=ch.id, title=ch.title,
            mastery=_chapter_mastery(wrong_rate, quizzes > 0),
            quizzes=quizzes, wrong_rate=wrong_rate,
        ))
    return MasteryResp(book_id=book_id, chapters=items)


@router.get("/activity", response_model=ActivityResp)
def activity(days: int = 30, db: Session = Depends(get_db)):
    since = datetime.now() - timedelta(days=days)
    rows = db.execute(
        select(
            func.date(Attempt.answered_at).label("d"),
            func.count().label("cnt"),
        ).where(Attempt.answered_at >= since)
        .group_by(func.date(Attempt.answered_at))
    ).all()
    by_date = {r.d: r.cnt for r in rows}

    daily = []
    for i in range(days, -1, -1):
        d = (datetime.now() - timedelta(days=i)).date().isoformat()
        daily.append(DailyActivity(date=d, attempts=by_date.get(d, 0)))
    return ActivityResp(daily=daily)


@router.get("/weakness", response_model=WeaknessResp)
def weakness(limit: int = 10, db: Session = Depends(get_db)):
    chapters = db.scalars(select(Chapter)).all()
    items = []
    for ch in chapters:
        quizzes = db.scalar(
            select(func.count()).select_from(Quiz).where(Quiz.chapter_id == ch.id)
        ) or 0
        wrong_rate = _chapter_wrong_rate(db, ch.id)
        if quizzes == 0 and wrong_rate == 0:
            continue  # 无数据的章节不进薄弱榜
        book = db.get(Book, ch.book_id)
        m = _chapter_mastery(wrong_rate, quizzes > 0)
        items.append(WeaknessItem(
            book_id=ch.book_id, book_title=book.title if book else "",
            chapter_id=ch.id, chapter_title=ch.title, mastery=m,
            suggest="优先复习" if m < 0.5 else "保持",
        ))
    items.sort(key=lambda x: x.mastery)
    return WeaknessResp(items=items[:limit])
