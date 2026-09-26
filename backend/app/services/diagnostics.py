"""不修改环境的本机安装诊断，可供命令行和设置页共用。"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path


def collect_diagnostics(root: Path, data_dir: Path, *, check_packages: bool = True) -> dict:
    checks: list[dict] = []

    def add(key: str, ok: bool, detail: str, action: str = "") -> None:
        checks.append({"key": key, "ok": bool(ok), "detail": detail, "action": action})

    version_ok = sys.version_info >= (3, 12) and sys.maxsize > 2**32
    add("python", version_ok, f"Python {sys.version.split()[0]} · {'64 位' if sys.maxsize > 2**32 else '32 位'}",
        "安装 64 位 Python 3.12+，再运行 install.bat" if not version_ok else "")
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    add("venv", venv_python.is_file(), "虚拟环境已就绪" if venv_python.is_file() else "缺少 .venv",
        "运行 install.bat；安装会从失败步骤继续" if not venv_python.is_file() else "")
    if check_packages:
        missing = [name for name in ("fastapi", "sqlalchemy", "fitz", "docx", "pptx")
                   if importlib.util.find_spec(name) is None]
        add("packages", not missing, "核心依赖齐全" if not missing else "缺少依赖：" + ", ".join(missing),
            "运行 install.bat 安装 Python 依赖" if missing else "")
    dist = root / "frontend" / "dist" / "index.html"
    add("frontend", dist.is_file(), "前端页面已构建" if dist.is_file() else "缺少 frontend/dist/index.html",
        "运行 install.bat；源码安装需 Node.js 22+" if not dist.is_file() else "")
    add("data", True, "数据目录已存在" if data_dir.is_dir() else "新安装：启动后自动创建数据目录",
        "旧版数据可通过 install.bat 迁移" if not data_dir.is_dir() else "")
    database = data_dir / "study.db"
    if database.is_file():
        try:
            connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=2)
            try:
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                connection.close()
            add("database", integrity == "ok", f"数据库校验：{integrity}",
                "先备份整个 data 目录，再查看维护文档" if integrity != "ok" else "")
        except (sqlite3.Error, OSError) as exc:
            add("database", False, f"数据库无法只读打开：{type(exc).__name__}", "检查文件权限与磁盘状态")
    else:
        add("database", True, "新安装：启动后自动创建数据库")
    return {"ok": all(item["ok"] for item in checks), "checks": checks}
