"""pytest 全局配置：初始化数据库表与 FTS 虚拟表。

CI 全新环境（无 backend/data/study.db）下，检索类测试会报
"no such table: fts_books"。此 fixture 在测试前幂等建表，保证可重复运行。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    """确保普通表 + FTS 虚拟表存在（应用未启动时测试也能跑）。"""
    from backend.app.core.database import Base, engine
    from backend.app.services.rag import fts
    import backend.app.models  # noqa: F401  注册 ORM 模型到 metadata

    Base.metadata.create_all(bind=engine)
    fts.init_fts()
    yield
