"""FSRS 间隔重复调度（docs/01-architecture.md §4.3）

基于 fsrs 6.x 库：Scheduler.review_card(card, rating) -> (new_card, review_log)。
DB 中存储 naive datetime（本地时间），fsrs 内部使用 aware datetime（UTC）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fsrs import Card as FSRSCard
from fsrs import Rating, Scheduler, State as FState

from backend.app.models import Card

_scheduler = Scheduler()

_RATING_MAP = {
    "again": Rating.Again,
    "hard": Rating.Hard,
    "good": Rating.Good,
    "easy": Rating.Easy,
}

_STATE_STR = {
    "New": "New",
    "Learning": "Learning",
    "Review": "Review",
    "Relearning": "Relearning",
}

# fsrs 库无 New 状态：DB New → fsrs Learning(step=0)
_FSRS_STATE = {
    "New": FState.Learning,
    "Learning": FState.Learning,
    "Review": FState.Review,
    "Relearning": FState.Relearning,
}

_FSRS_STATE_BACK = {
    FState.Learning: "Learning",
    FState.Review: "Review",
    FState.Relearning: "Relearning",
}


def _to_utc(dt: datetime) -> datetime:
    """naive → aware UTC（视为本地时间转换，简单起见直接假设 UTC 等价处理）。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_naive(dt: datetime) -> datetime:
    """aware → naive（存库）。"""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def new_card_due() -> datetime:
    """新卡默认 due = 现在。"""
    return datetime.now()


def _to_fsrs_card(card: Card) -> FSRSCard:
    """DB Card → fsrs.Card（New → Learning step=0）。"""
    state = _FSRS_STATE.get(card.state, FState.Learning)
    step = 0 if state == FState.Learning else None
    return FSRSCard(
        card_id=card.id,
        state=state,
        step=step,
        stability=card.stability if card.stability > 0 else None,
        difficulty=card.difficulty if card.difficulty > 0 else None,
        due=_to_utc(card.due) if card.due else datetime.now(timezone.utc),
        last_review=_to_utc(card.last_review) if card.last_review else None,
    )


def review_card(card: Card, rating: str, now: datetime | None = None) -> dict:
    """对卡片进行一次复习评级，更新 FSRS 参数并写回 DB Card。

    返回更新后的摘要 dict（供 API 响应）。
    """
    if rating not in _RATING_MAP:
        raise ValueError(f"无效评级: {rating}，可选 again/hard/good/easy")

    now = now or datetime.now()
    fsrs_card = _to_fsrs_card(card)
    new_card, _review_log = _scheduler.review_card(fsrs_card, _RATING_MAP[rating], review_datetime=_to_utc(now))

    state_before = card.state
    old_last_review = card.last_review
    elapsed_days = max(0, (now - old_last_review).days) if old_last_review else 0
    # 写回 DB：fsrs Learning 且曾为 New 时保持 "New" 语义？不——fsrs 会推进到
    # Learning/Review/Relearning，直接映射即可（Anki 中首次复习后就是 Learning/Review）
    card.state = _FSRS_STATE_BACK.get(new_card.state, "Learning")
    card.stability = new_card.stability or 0.0
    card.difficulty = new_card.difficulty or 0.0
    card.due = _to_naive(new_card.due)
    card.last_review = now
    card.elapsed_days = elapsed_days
    # ORM 默认值在未 flush 前为 None，需兜底
    card.reps = (card.reps or 0) + 1
    if rating == "again":
        card.lapses = (card.lapses or 0) + 1

    scheduled_days = max(0, (card.due - now).days)
    card.scheduled_days = scheduled_days

    return {
        "card_id": card.id,
        "state": card.state,
        "stability": round(card.stability, 2),
        "difficulty": round(card.difficulty, 2),
        "due": card.due,
        "scheduled_days": scheduled_days,
        "state_before": state_before,
        "elapsed_days": elapsed_days,
    }
