"""pytest 全局配置：初始化数据库表与 FTS 虚拟表。

CI 全新环境（无 backend/data/study.db）下，检索类测试会报
"no such table: fts_books"。此 fixture 在测试前幂等建表，保证可重复运行。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# CI 全新环境：使用临时数据目录，避免权限/路径问题
_test_data = Path(tempfile.mkdtemp(prefix="study_test_"))
os.environ.setdefault("DATA_DIR", str(_test_data))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    """确保普通表 + FTS 虚拟表存在（应用未启动时测试也能跑）。"""
    from backend.app.core.database import Base, engine
    from backend.app.services.rag import fts
    import backend.app.models  # noqa: F401  注册 ORM 模型到 metadata

    # 确保数据目录存在
    from backend.app.core.config import settings
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)

    # 建普通表
    Base.metadata.create_all(bind=engine)

    # 建 FTS 虚拟表（直接调用，不经过 data_manager 的版本检查，避免依赖 schema_version 表）
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text(fts._CREATE_SQL))

    yield
