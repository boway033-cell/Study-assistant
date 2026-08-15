"""统计 API（docs/03-api.md §5）"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Attempt, Book, Card, Chapter, Quiz, ReviewLog
from backend.app.schemas import (
    ChapterMastery,
    DailyReview,
    MasteryResp,
    OverviewResp,
    ReviewHistoryResp,
    WeaknessItem,
    WeaknessResp,
)

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _chapter_mastery(cards_total: int, cards_due: int, wrong_rate: float) -> float:
    """掌握度 = 0.6×(1-到期卡占比) + 0.4×(1-错题率)。"""
    if cards_total == 0:
        return 0.0
    card_score = 1 - (cards_due / cards_total)
    return round(0.6 * card_score + 0.4 * (1 - wrong_rate), 3)


@router.get("/overview", response_model=OverviewResp)
def overview(db: Session = Depends(get_db)):
    now = datetime.now()
    book_count = db.scalar(select(func.count()).select_from(Book)) or 0
    card_count = db.scalar(select(func.count()).select_from(Card)) or 0
    due_today = db.scalar(
        select(func.count()).select_from(Card).where(Card.due <= now)
    ) or 0
    reviews_done = db.scalar(select(func.count()).select_from(ReviewLog)) or 0
    quiz_count = db.scalar(select(func.count()).select_from(Quiz)) or 0

    # 平均掌握度：全部章节卡片数的加权
    chapters = db.scalars(select(Chapter)).all()
    total_cards = 0
    weighted = 0.0
    for ch in chapters:
        cards = db.scalar(
            select(func.count()).select_from(Card).where(Card.chapter_id == ch.id)
        ) or 0
        due = db.scalar(
            select(func.count()).select_from(Card).where(
                Card.chapter_id == ch.id, Card.due <= now
            )
        ) or 0
        wrong = db.scalar(
            select(func.count()).select_from(Attempt)
            .join(Quiz, Quiz.id == Attempt.quiz_id)
            .where(Quiz.chapter_id == ch.id, Attempt.is_correct == 0)
        ) or 0
        total_attempts = db.scalar(
            select(func.count()).select_from(Attempt)
            .join(Quiz, Quiz.id == Attempt.quiz_id)
            .where(Quiz.chapter_id == ch.id)
        ) or 0
        wrong_rate = wrong / total_attempts if total_attempts else 0.0
        m = _chapter_mastery(cards, due, wrong_rate)
        weighted += m * cards
        total_cards += cards
    avg_mastery = round(weighted / total_cards, 3) if total_cards else 0.0

    # 连续复习天数（简单实现：最近有复习记录的连续天数）
    dates = set(
        db.scalars(select(func.date(ReviewLog.reviewed_at)).distinct()).all()
    )
    streak = 0
    d = datetime.now().date()
    while d.isoformat() in dates:
        streak += 1
        d -= timedelta(days=1)

    return OverviewResp(
        book_count=book_count, card_count=card_count, due_today=due_today,
        reviews_done=reviews_done, quiz_count=quiz_count,
        avg_mastery=avg_mastery, streak_days=streak,
    )


@router.get("/mastery", response_model=MasteryResp)
def mastery(book_id: int, db: Session = Depends(get_db)):
    now = datetime.now()
    chapters = db.scalars(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_index)
    ).all()
    items = []
    for ch in chapters:
        cards = db.scalar(
            select(func.count()).select_from(Card).where(Card.chapter_id == ch.id)
        ) or 0
        due = db.scalar(
            select(func.count()).select_from(Card).where(
                Card.chapter_id == ch.id, Card.due <= now
            )
        ) or 0
        wrong = db.scalar(
            select(func.count()).select_from(Attempt)
            .join(Quiz, Quiz.id == Attempt.quiz_id)
            .where(Quiz.chapter_id == ch.id, Attempt.is_correct == 0)
        ) or 0
        total_attempts = db.scalar(
            select(func.count()).select_from(Attempt)
            .join(Quiz, Quiz.id == Attempt.quiz_id)
            .where(Quiz.chapter_id == ch.id)
        ) or 0
        wrong_rate = round(wrong / total_attempts, 3) if total_attempts else 0.0
        items.append(ChapterMastery(
            chapter_id=ch.id, title=ch.title,
            mastery=_chapter_mastery(cards, due, wrong_rate),
            cards=cards, due=due, wrong_rate=wrong_rate,
        ))
    return MasteryResp(book_id=book_id, chapters=items)


@router.get("/review-history", response_model=ReviewHistoryResp)
def review_history(days: int = 30, db: Session = Depends(get_db)):
    since = datetime.now() - timedelta(days=days)
    rows = db.execute(
        select(
            func.date(ReviewLog.reviewed_at).label("d"),
            func.count().label("reviews"),
        ).where(ReviewLog.reviewed_at >= since)
        .group_by(func.date(ReviewLog.reviewed_at))
    ).all()
    by_date = {r.d: r.reviews for r in rows}

    daily = []
    for i in range(days, -1, -1):
        d = (datetime.now() - timedelta(days=i)).date().isoformat()
        daily.append(DailyReview(date=d, reviews=by_date.get(d, 0), new_cards=0, due=0))
    return ReviewHistoryResp(daily=daily)


@router.get("/weakness", response_model=WeaknessResp)
def weakness(limit: int = 10, db: Session = Depends(get_db)):
    now = datetime.now()
    chapters = db.scalars(select(Chapter)).all()
    items = []
    for ch in chapters:
        cards = db.scalar(
            select(func.count()).select_from(Card).where(Card.chapter_id == ch.id)
        ) or 0
        due = db.scalar(
            select(func.count()).select_from(Card).where(
                Card.chapter_id == ch.id, Card.due <= now
            )
        ) or 0
        wrong = db.scalar(
            select(func.count()).select_from(Attempt)
            .join(Quiz, Quiz.id == Attempt.quiz_id)
            .where(Quiz.chapter_id == ch.id, Attempt.is_correct == 0)
        ) or 0
        total_attempts = db.scalar(
            select(func.count()).select_from(Attempt)
            .join(Quiz, Quiz.id == Attempt.quiz_id)
            .where(Quiz.chapter_id == ch.id)
        ) or 0
        wrong_rate = wrong / total_attempts if total_attempts else 0.0
        m = _chapter_mastery(cards, due, wrong_rate)
        if cards == 0 and total_attempts == 0:
            continue  # 无数据的章节不进薄弱榜
        book = db.get(Book, ch.book_id)
        items.append(WeaknessItem(
            book_id=ch.book_id, book_title=book.title if book else "",
            chapter_id=ch.id, chapter_title=ch.title, mastery=m,
            suggest="优先复习" if m < 0.5 else "保持",
        ))
    items.sort(key=lambda x: x.mastery)
    return WeaknessResp(items=items[:limit])
