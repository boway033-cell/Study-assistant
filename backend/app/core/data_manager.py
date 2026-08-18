"""数据层统一管理：索引版本、损坏检测、自动备份

职责：
1. FTS 索引版本管理：记录 schema 版本，变更时自动重建
2. SQLite 完整性检测：启动时 PRAGMA integrity_check
3. 自动备份：首次启动/定期备份 study.db
4. 数据迁移：统一版本号管理（schema_version 表）
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from backend.app.core.config import settings
from backend.app.core.database import engine

# 当前数据层版本（每次 schema 变更递增）
SCHEMA_VERSION = 3

# FTS 索引版本（FTS schema 变更时递增，init_fts 据此判断是否重建）
FTS_INDEX_VERSION = 2


def check_integrity() -> tuple[bool, str]:
    """SQLite 完整性检测。返回 (ok, message)。"""
    db_path = settings.db_path
    if not db_path.exists():
        return True, "数据库尚未创建（首次启动）"
    try:
        conn = sqlite3.connect(str(db_path))
        result = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        ok = result[0] == "ok"
        return ok, result[0]
    except Exception as e:
        return False, f"检测失败: {e}"


def auto_backup() -> str | None:
    """首次启动或数据库 >10MB 时自动备份到 data/backups/。返回备份路径或 None。"""
    db_path = settings.db_path
    if not db_path.exists():
        return None
    backup_dir = settings.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 检查是否已有今天的备份
    today = datetime.now().strftime("%Y%m%d")
    today_backup = backup_dir / f"study_{today}.db"
    if today_backup.exists():
        return None  # 今天已备份

    # 数据库 >5MB 或本周无备份时才备份（避免频繁复制大文件）
    size_mb = db_path.stat().st_size / (1024 * 1024)
    if size_mb < 5:
        # 小库只在每周首次运行时备份
        backups = sorted(backup_dir.glob("study_*.db"))
        if backups:
            last = backups[-1].name
            last_date = last.replace("study_", "").replace(".db", "")
            try:
                last_dt = datetime.strptime(last_date, "%Y%m%d")
                if (datetime.now() - last_dt).days < 7:
                    return None  # 一周内已有备份
            except ValueError:
                pass

    try:
        shutil.copy2(str(db_path), str(today_backup))
        # 清理旧备份（只保留最近 10 个）
        backups = sorted(backup_dir.glob("study_*.db"), key=lambda p: p.name)
        for old in backups[:-10]:
            old.unlink(missing_ok=True)
        return str(today_backup)
    except Exception:
        return None


def get_schema_version() -> int:
    """读取当前数据层版本号（0=老库无版本管理）。"""
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            # 检查 schema_version 表是否存在
            tables = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            )).fetchall()
            if not tables:
                return 0
            row = conn.execute(text("SELECT version FROM schema_version ORDER BY id DESC LIMIT 1")).fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


def set_schema_version(version: int) -> None:
    """写入数据层版本号。"""
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER, updated_at TEXT)"
            ))
            conn.execute(text(
                "INSERT INTO schema_version (version, updated_at) VALUES (:v, :t)"
            ), {"v": version, "t": datetime.now().isoformat()})
    except Exception:
        pass


def get_fts_index_version() -> int:
    """读取 FTS 索引版本号。"""
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT value FROM schema_version WHERE version = -1 ORDER BY id DESC LIMIT 1"
            )).fetchone()
            # version=-1 行的 value 列存 FTS 版本（hack：复用表）
            return int(row[0]) if row else 0
    except Exception:
        return 0


def set_fts_index_version(version: int) -> None:
    """写入 FTS 索引版本号。"""
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER, updated_at TEXT)"
            ))
            # version=-1 特殊行存 FTS 版本，updated_at 字段存版本号
            conn.execute(text(
                "INSERT INTO schema_version (version, updated_at) VALUES (-1, :v)"
            ), {"v": str(version)})
    except Exception:
        pass


def run_data_checks() -> dict:
    """启动时统一执行数据层检查。返回检查结果摘要。"""
    results: dict = {}

    # 1. 完整性检测
    ok, msg = check_integrity()
    results["integrity"] = {"ok": ok, "message": msg}
    if not ok:
        # 数据库损坏 → 尝试从最近备份恢复
        backup_dir = settings.data_dir / "backups"
        backups = sorted(backup_dir.glob("study_*.db"), key=lambda p: p.name, reverse=True)
        if backups:
            try:
                shutil.copy2(str(backups[0]), str(settings.db_path))
                results["integrity"]["restored_from"] = str(backups[0])
            except Exception:
                pass

    # 2. 自动备份
    backup_path = auto_backup()
    if backup_path:
        results["backup"] = backup_path

    # 3. 版本检查
    current = get_schema_version()
    results["schema_version"] = current
    if current < SCHEMA_VERSION:
        # 触发迁移（_migrate 已在各列检查中处理 ALTER TABLE）
        set_schema_version(SCHEMA_VERSION)
        results["migrated_to"] = SCHEMA_VERSION

    return results
