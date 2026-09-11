"""多轮会话落库（chat_sessions）：重启可续、容量与过期清理、kind 隔离。

背景：思维训练与绘图改图的状态此前存在模块级 dict（`study.py:_sessions`、
`draw.py:_draw_sessions`），服务重启即 404。这里把「重启后仍能读回」当成硬断言，
用新的 Session（等价于新连接）复读来模拟重启，而不是只看同一个连接内的内存值。
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base
from backend.app.models import ChatSession
from backend.app.services import chat_sessions as store


def _factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def test_session_survives_new_connection():
    """换一个连接（等价于进程重启）后，会话状态必须还在。"""
    engine, factory = _factory()
    with factory() as db:
        store.save_session(db, "abc123", "train", {
            "mode": "quiz", "topic": "治理", "book_ids": [7],
            "history": [{"role": "user", "content": "开始"}], "round": 1, "done": False,
        }, [7])
    with factory() as db:
        state = store.load_session(db, "abc123", "train")
        assert state is not None, "重启后会话不应丢失"
        assert state["round"] == 1
        assert state["history"][0]["content"] == "开始"
        assert json.loads(db.get(ChatSession, "abc123").book_ids_json) == [7]
    engine.dispose()


def test_session_kind_is_isolated():
    engine, factory = _factory()
    with factory() as db:
        store.save_session(db, "dup1", "draw", {"xml": "<x/>"})
        assert store.load_session(db, "dup1", "train") is None
        assert store.load_session(db, "dup1", "draw")["xml"] == "<x/>"
    engine.dispose()


def test_history_is_truncated_on_save():
    engine, factory = _factory()
    with factory() as db:
        history = [{"role": "user", "content": f"第{i}轮"} for i in range(50)]
        store.save_session(db, "h1", "train", {"history": history})
    with factory() as db:
        history = store.load_session(db, "h1", "train")["history"]
        assert len(history) == store._HISTORY_LIMIT
        assert history[-1]["content"] == "第49轮"
    engine.dispose()


def test_expired_sessions_are_purged_on_next_save():
    engine, factory = _factory()
    with factory() as db:
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=store.SESSION_TTL_DAYS + 1)
        db.add(ChatSession(id="stale1", kind="train", book_ids_json="[]", state_json="{}",
                           updated_at=stale))
        db.commit()
        store.save_session(db, "fresh1", "train", {"round": 0})
    with factory() as db:
        assert db.get(ChatSession, "stale1") is None, "过期会话应被清理"
        assert db.get(ChatSession, "fresh1") is not None
    engine.dispose()


def test_draw_sessions_keep_capacity_cap():
    engine, factory = _factory()
    with factory() as db:
        for index in range(store.SESSION_CAPS["draw"] + 5):
            store.save_session(db, f"d{index:03d}", "draw", {"xml": f"<x{index}/>"})
    with factory() as db:
        assert len(store.list_sessions(db, "draw")) == store.SESSION_CAPS["draw"]
    engine.dispose()


def test_delete_session_is_kind_scoped():
    engine, factory = _factory()
    with factory() as db:
        store.save_session(db, "k1", "draw", {"xml": "x"})
        store.delete_session(db, "k1", "train")   # kind 不匹配，不应误删
        assert store.load_session(db, "k1", "draw") is not None
        store.delete_session(db, "k1", "draw")
        assert store.load_session(db, "k1", "draw") is None
    engine.dispose()


def test_draw_endpoints_read_from_database():
    """绘图端点必须直接走库：换连接后仍能列出、取回、删除。"""
    from backend.app.api.draw import delete_draw_session, get_draw_session, list_draw_sessions

    engine, factory = _factory()
    with factory() as db:
        store.save_session(db, "e2e", "draw", {
            "xml": "<mxGraphModel/>",
            "history": [{"role": "user", "content": "画个流程"}],
            "book_ids": [3],
        }, [3])
    with factory() as db:
        listed = list_draw_sessions(db)["sessions"]
        assert [item["id"] for item in listed] == ["e2e"]
        assert listed[0]["book_ids"] == [3]
        assert listed[0]["preview"] == "<mxGraphModel/>"
        assert get_draw_session("e2e", db)["xml"] == "<mxGraphModel/>"
        with pytest.raises(HTTPException):
            get_draw_session("missing", db)
        delete_draw_session("e2e", db)
        assert store.load_session(db, "e2e", "draw") is None
    engine.dispose()
