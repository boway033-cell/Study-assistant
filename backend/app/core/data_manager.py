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
SCHEMA_VERSION = 10

# FTS 索引版本（FTS schema 变更时递增，init_fts 据此判断是否重建）
FTS_INDEX_VERSION = 2

_METADATA_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS app_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    """用 SQLite 在线备份 API 创建一致快照；固定页批次避免内存峰值。"""
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(source_path), timeout=30)
    target = sqlite3.connect(str(destination_path), timeout=30)
    try:
        source.backup(target, pages=256, sleep=0.01)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError(f"备份完整性检查失败: {result}")
    finally:
        target.close()
        source.close()


def _restore_backup(backup_path: Path, database_path: Path) -> None:
    """先恢复到临时库并校验，再原子替换当前数据库。"""
    recovery_path = database_path.with_suffix(database_path.suffix + ".recovering")
    recovery_path.unlink(missing_ok=True)
    try:
        _sqlite_backup(backup_path, recovery_path)
        engine.dispose()
        # 损坏主库留下的 WAL/SHM 不能与恢复快照混用。
        Path(str(database_path) + "-wal").unlink(missing_ok=True)
        Path(str(database_path) + "-shm").unlink(missing_ok=True)
        recovery_path.replace(database_path)
    finally:
        recovery_path.unlink(missing_ok=True)


def check_integrity_status() -> tuple[str, str]:
    """SQLite 完整性检测。返回 (status, message)，status ∈ {"ok", "corrupt", "unknown"}。

    必须区分"确证损坏"与"无法检测"：连接被占用、文件被杀软锁定等都会让检测抛异常，
    这类 unknown 状态绝不能等同于损坏，否则会触发无确认的备份回滚，丢失上次备份之后的全部改动。
    """
    db_path = settings.db_path
    if not db_path.exists():
        return "ok", "数据库尚未创建（首次启动）"
    conn = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result:
            return "unknown", "完整性检测未返回结果"
        return ("ok" if result[0] == "ok" else "corrupt"), result[0]
    except Exception as e:  # noqa: BLE001
        return "unknown", f"检测失败（未确认损坏）: {e}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def check_integrity() -> tuple[bool, str]:
    """SQLite 完整性检测。返回 (ok, message)（保持旧签名）。"""
    status, message = check_integrity_status()
    return status == "ok", message


def _snapshot_before_restore(db_path: Path, backup_dir: Path) -> str | None:
    """回滚前保留当前数据库现场（含 WAL/SHM），避免误判损坏后无法追回。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"pre-restore-{stamp}.db"
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        if db_path.exists():
            shutil.copy2(db_path, target)
        for suffix in ("-wal", "-shm"):
            side = Path(str(db_path) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(target) + suffix))
        return str(target)
    except Exception:  # noqa: BLE001
        # 无法保留现场时不阻断恢复（真损坏场景下拷贝也可能失败），但调用方会记录未保留。
        return None


def auto_backup() -> str | None:
    """首次启动或数据库 >10MB 时自动备份到 data/backups/。返回备份路径或 None。"""
    db_path = settings.db_path
    if not db_path.exists():
        return None
    backup_dir = settings.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    prune_backups(backup_dir)

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
        temporary = today_backup.with_suffix(".db.tmp")
        temporary.unlink(missing_ok=True)
        _sqlite_backup(db_path, temporary)
        temporary.replace(today_backup)
        prune_backups(backup_dir)
        return str(today_backup)
    except Exception:
        today_backup.with_suffix(".db.tmp").unlink(missing_ok=True)
        return None


def prune_backups(backup_dir: Path, keep_each: int = 3, max_bytes: int = 224 * 1024 * 1024) -> dict:
    """每日快照和操作前快照各保留最近三份，并设置总容量上限。"""
    groups = [list(backup_dir.glob("study_*.db")), list(backup_dir.glob("before_*.db"))]
    removed: list[str] = []
    for files in groups:
        ordered = sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)
        for old in ordered[keep_each:]:
            old.unlink(missing_ok=True); removed.append(old.name)
    remaining = sorted(backup_dir.glob("*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    total = sum(path.stat().st_size for path in remaining)
    # 至少保留最新两份，容量超限时从最旧项开始释放。
    for old in reversed(remaining[2:]):
        if total <= max_bytes:
            break
        size = old.stat().st_size
        old.unlink(missing_ok=True); removed.append(old.name); total -= size
    return {"removed": removed, "remaining": len(list(backup_dir.glob('*.db'))), "bytes": total}


def _trim_log(path: Path, max_bytes: int = 2 * 1024 * 1024, keep_bytes: int = 512 * 1024) -> bool:
    if not path.exists() or path.stat().st_size <= max_bytes:
        return False
    with path.open("rb") as source:
        source.seek(-min(keep_bytes, path.stat().st_size), 2)
        tail = source.read()
    marker = b"\n--- earlier log entries removed by capacity policy ---\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(marker + tail)
    temporary.replace(path)
    return True


def prune_regenerable_cache(root: Path, max_bytes: int) -> dict:
    """只治理可重新生成的缓存；用户原文、笔记和 PPTX 永不参与。"""
    if not root.exists():
        return {"removed": 0, "bytes": 0}
    files = sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: path.stat().st_mtime)
    total = sum(path.stat().st_size for path in files)
    removed = 0
    target = int(max_bytes * 0.8)
    for path in files:
        if total <= max_bytes:
            break
        size = path.stat().st_size
        path.unlink(missing_ok=True); total -= size; removed += 1
        if total <= target:
            break
    for folder in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        try: folder.rmdir()
        except OSError: pass
    return {"removed": removed, "bytes": total}


def _get_metadata_int(key: str) -> int | None:
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            exists = conn.execute(text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_metadata'"
            )).first()
            if not exists:
                return None
            value = conn.execute(
                text("SELECT value FROM app_metadata WHERE key = :key"), {"key": key}
            ).scalar_one_or_none()
            return int(value) if value is not None else None
    except Exception:
        return None


def _set_metadata_int(key: str, value: int) -> None:
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text(_METADATA_TABLE_SQL))
        conn.execute(text("""
            INSERT INTO app_metadata(key, value, updated_at) VALUES (:key, :value, :time)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """), {"key": key, "value": str(value), "time": datetime.now().isoformat()})


def get_schema_version() -> int:
    """读取当前数据层版本号（0=老库无版本管理）。"""
    from sqlalchemy import text
    current = _get_metadata_int("schema_version")
    if current is not None:
        return current
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
    try:
        _set_metadata_int("schema_version", version)
    except Exception:
        pass


def get_fts_index_version() -> int:
    """读取 FTS 索引版本号。"""
    return _get_metadata_int("fts_index_version") or 0


def set_fts_index_version(version: int) -> None:
    """写入 FTS 索引版本号。"""
    try:
        _set_metadata_int("fts_index_version", version)
    except Exception:
        pass


def run_data_checks() -> dict:
    """启动时统一执行数据层检查。返回检查结果摘要。"""
    results: dict = {}

    # 1. 完整性检测
    status, msg = check_integrity_status()
    results["integrity"] = {"ok": status == "ok", "message": msg, "status": status}
    # 仅在"确证损坏"时回滚；unknown（被锁/无法检测）绝不回滚。
    if status == "corrupt":
        # 数据库损坏 → 尝试从最近备份恢复
        backup_dir = settings.data_dir / "backups"
        backups = sorted(backup_dir.glob("study_*.db"), key=lambda p: p.name, reverse=True)
        if backups:
            try:
                snapshot = _snapshot_before_restore(settings.db_path, backup_dir)
                if snapshot:
                    results["integrity"]["pre_restore_snapshot"] = snapshot
                _restore_backup(backups[0], settings.db_path)
                results["integrity"]["restored_from"] = str(backups[0])
            except Exception:
                pass

    # 2. 自动备份
    backup_path = auto_backup()
    if backup_path:
        results["backup"] = backup_path

    # 只治理日志和可再生缓存；上传原文、知识库和生成文件不自动删除。
    results["logs_trimmed"] = sum(_trim_log(path) for path in (
        settings.data_dir.parent.parent / "server.log",
        settings.data_dir.parent.parent / "server.err.log",
        settings.data_dir / "runtime" / "launcher.log",
    ))
    results["cache"] = {
        "ocr": prune_regenerable_cache(settings.data_dir / "ocr_cache", 128 * 1024 * 1024),
        "structured": prune_regenerable_cache(settings.structured_dir, 192 * 1024 * 1024),
        "previews": prune_regenerable_cache(settings.presentations_dir / "previews", 256 * 1024 * 1024),
    }

    # 3. 版本检查
    current = get_schema_version()
    results["schema_version"] = current
    if current < SCHEMA_VERSION:
        # 触发迁移（_migrate 已在各列检查中处理 ALTER TABLE）
        set_schema_version(SCHEMA_VERSION)
        results["migrated_to"] = SCHEMA_VERSION

    return results
