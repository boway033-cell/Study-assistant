"""统计 API（docs/03-api.md §5）— 基于题目作答数据（卡片学习已取消）"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
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


def _chapter_quiz_stats(db: Session, book_id: int | None = None) -> dict[int, dict]:
    """一次查询汇总各章题量与最近作答，避免按章节循环查询。"""
    latest = (
        select(
            Attempt.quiz_id,
            Attempt.is_correct,
            func.row_number().over(
                partition_by=Attempt.quiz_id, order_by=Attempt.answered_at.desc()
            ).label("rn"),
        ).subquery()
    )
    latest_only = select(latest.c.quiz_id, latest.c.is_correct).where(latest.c.rn == 1).subquery()
    stmt = (
        select(
            Quiz.chapter_id,
            func.count(Quiz.id).label("quizzes"),
            func.count(latest_only.c.quiz_id).label("attempted"),
            func.sum(case((latest_only.c.is_correct == 0, 1), else_=0)).label("wrong"),
        )
        .outerjoin(latest_only, latest_only.c.quiz_id == Quiz.id)
        .where(Quiz.chapter_id.is_not(None))
        .group_by(Quiz.chapter_id)
    )
    if book_id is not None:
        stmt = stmt.join(Chapter, Chapter.id == Quiz.chapter_id).where(Chapter.book_id == book_id)
    return {int(row.chapter_id): {"quizzes": int(row.quizzes or 0), "attempted": int(row.attempted or 0),
                                  "wrong": int(row.wrong or 0),
                                  "wrong_rate": round((row.wrong or 0) / row.attempted, 3) if row.attempted else 0.0}
            for row in db.execute(stmt).all()}


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
    chapter_stats = _chapter_quiz_stats(db)
    weighted = 0.0
    attempted_total = 0
    for stats in chapter_stats.values():
        if not stats["attempted"]:
            continue
        weighted += _chapter_mastery(stats["wrong_rate"], True) * stats["attempted"]
        attempted_total += stats["attempted"]
    avg_mastery = round(weighted / attempted_total, 3) if attempted_total else 0.0

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
    stats_by_chapter = _chapter_quiz_stats(db, book_id)
    items = []
    for ch in chapters:
        stats = stats_by_chapter.get(ch.id, {"quizzes": 0, "attempted": 0, "wrong_rate": 0.0})
        items.append(ChapterMastery(
            chapter_id=ch.id, title=ch.title,
            mastery=_chapter_mastery(stats["wrong_rate"], stats["attempted"] > 0),
            quizzes=stats["quizzes"], wrong_rate=stats["wrong_rate"],
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
    rows = db.execute(select(Chapter, Book).join(Book, Book.id == Chapter.book_id)).all()
    stats_by_chapter = _chapter_quiz_stats(db)
    items = []
    for ch, book in rows:
        stats = stats_by_chapter.get(ch.id)
        if not stats or not stats["attempted"]:
            continue
        m = _chapter_mastery(stats["wrong_rate"], True)
        items.append(WeaknessItem(
            book_id=ch.book_id, book_title=book.title if book else "",
            chapter_id=ch.id, chapter_title=ch.title, mastery=m,
            suggest="优先复习" if m < 0.5 else "保持",
        ))
    items.sort(key=lambda x: x.mastery)
    return WeaknessResp(items=items[:limit])
