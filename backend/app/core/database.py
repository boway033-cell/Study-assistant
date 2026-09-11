"""数据库引擎与会话管理"""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from backend.app.core.config import settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    engine = create_engine(
        f"sqlite:///{settings.db_path}",
        # timeout: sqlite3 驱动层等待锁的秒数，与下面的 busy_timeout 互补。
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _record):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        # 吞吐调优（读多写少的本地单机场景）：WAL 下 synchronous=NORMAL 兼顾崩溃安全
        # 与写入延迟；busy_timeout 让并发写排队而不是立刻 SQLITE_BUSY；page cache 与
        # mmap 减少重复 read() 系统调用。任一条失败都不应阻断连接建立。
        for stmt in (
            "PRAGMA synchronous=NORMAL",
            "PRAGMA busy_timeout=15000",
            "PRAGMA temp_store=MEMORY",
            "PRAGMA cache_size=-32000",
            "PRAGMA mmap_size=268435456",
        ):
            try:
                cur.execute(stmt)
            except Exception:  # noqa: BLE001
                pass
        cur.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """FastAPI 依赖：请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
