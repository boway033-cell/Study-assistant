"""独立后台服务进程：供 VBS 与 study-assistant:// 协议按需启动。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parent


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8000
    if not 8000 <= port <= 8010:
        raise SystemExit("port outside allowed range")
    os.chdir(ROOT)
    # pythonw 没有控制台；日志由进程自身持有，启动 shell 退出不影响文件句柄。
    with (ROOT / "server.log").open("a", encoding="utf-8", buffering=1) as stdout, \
         (ROOT / "server.err.log").open("a", encoding="utf-8", buffering=1) as stderr:
        sys.stdout = stdout
        sys.stderr = stderr
        uvicorn.run("backend.app.main:app", host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
