"""多轮交互会话的落库读写（思维训练 / 绘图共用）。

为什么单独抽一层
--------------
这两类会话此前存在模块级 dict 里（`api/study.py` 的 `_sessions`、`api/draw.py` 的
`_draw_sessions`），服务一重启就报 404「会话不存在或已过期」，用户无感知地丢掉整段多轮历史。
注意它们**不是**后台任务：任务状态本来就持久化在 `import_tasks`（`worker/tasks.py:63-90`），
重启后由 `get_task` 恢复；这里补的是「用户工作态」那一半。

容量策略
--------
原先用内存 FIFO(100) / LRU(20) 兜底，这里改为：
1. 按 `updated_at` 清理超过 `SESSION_TTL_DAYS` 未继续的会话（对所有 kind 生效）；
2. 保留同样的每类条数上限（只对当前写入的 kind 生效）。
另外 `history` 入库前截断，避免单会话无限增长（训练提示词本来就只取最近 8 轮）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models import ChatSession

SESSION_TTL_DAYS = 7
SESSION_CAPS = {"train": 100, "draw": 20}
_HISTORY_LIMIT = 20


def row_state(row: ChatSession) -> dict:
    """把一行记录反序列化成业务状态字典（脏数据一律降级为空字典）。"""
    try:
        value = json.loads(row.state_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _trim_history(state: dict) -> dict:
    history = state.get("history")
    if isinstance(history, list) and len(history) > _HISTORY_LIMIT:
        return {**state, "history": history[-_HISTORY_LIMIT:]}
    return state


def _purge(db: Session, kind: str) -> None:
    """过期清理（全 kind）+ 容量上限（当前 kind，保留最近更新的）。"""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=SESSION_TTL_DAYS)
    db.execute(delete(ChatSession).where(ChatSession.updated_at < cutoff))
    cap = SESSION_CAPS.get(kind)
    if not cap:
        return
    keep = db.scalars(select(ChatSession.id).where(ChatSession.kind == kind)
                      .order_by(ChatSession.updated_at.desc()).limit(cap)).all()
    db.execute(delete(ChatSession).where(ChatSession.kind == kind, ~ChatSession.id.in_(keep)))


def load_session(db: Session, session_id: str, kind: str) -> dict | None:
    """按 id + kind 取会话状态；kind 不匹配视为不存在，避免跨功能误用同一个 id。"""
    row = db.get(ChatSession, session_id)
    if row is None or row.kind != kind:
        return None
    state = row_state(row)
    return state or None


def save_session(db: Session, session_id: str, kind: str, state: dict,
                 book_ids: list[int] | None = None) -> None:
    """整份覆盖写入（多轮会话每轮结束后调用一次）。"""
    state = _trim_history(state)
    books = [int(value) for value in (book_ids or []) if value is not None]
    payload = json.dumps(state, ensure_ascii=False, default=str)
    row = db.get(ChatSession, session_id)
    if row is None:
        row = ChatSession(id=session_id, kind=kind,
                          book_ids_json=json.dumps(books), state_json=payload)
        db.add(row)
    else:
        row.state_json = payload
        row.book_ids_json = json.dumps(books)
    db.flush()
    _purge(db, kind)
    db.commit()


def delete_session(db: Session, session_id: str, kind: str | None = None) -> None:
    row = db.get(ChatSession, session_id)
    if row is None or (kind and row.kind != kind):
        return
    db.delete(row)
    db.commit()


def list_sessions(db: Session, kind: str) -> list[ChatSession]:
    return list(db.scalars(select(ChatSession).where(ChatSession.kind == kind)
                           .order_by(ChatSession.updated_at.desc())).all())
