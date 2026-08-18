"""一键启动器：自检端口/依赖 → 启动服务 → 自动打开浏览器

用法:
  python launcher.py            # 默认 8000 端口
  python launcher.py 8001       # 指定端口
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def get_port() -> int:
    for arg in sys.argv[1:]:
        if arg.isdigit():
            return int(arg)
    return int(os.environ.get("PORT", "8000"))


def health_ok(port: int) -> bool:
    """探测 /api/health，确认应用已在运行。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def print_banner(port: int) -> None:
    """打印启动横幅。"""
    url = f"http://127.0.0.1:{port}"
    print()
    print("  ============================================")
    print("  |                                          |")
    print("  |    Study Assistant - Learning Toolbox    |")
    print("  |                                          |")
    print(f"  |    {url:<33}       |")
    print("  |                                          |")
    print("  |    Press Ctrl+C to stop                  |")
    print("  |                                          |")
    print("  ============================================")
    print()


def main() -> int:
    port = get_port()
    url = f"http://127.0.0.1:{port}"

    # 0. 已在运行？直接打开浏览器
    if health_ok(port):
        print(f"[launcher] Application already running at {url}")
        webbrowser.open(url)
        return 0

    # 1. 端口被其他程序占用？明确提示
    if port_in_use(port):
        print(f"[launcher] Port {port} is occupied by another program.")
        print(f"          Try: python launcher.py {port + 1}")
        input("Press Enter to exit...")
        return 1

    # 2. 启动服务（子进程，日志同时输出到控制台与 server.log）
    python = sys.executable
    cmd = [python, "-m", "uvicorn", "backend.app.main:app",
           "--host", "127.0.0.1", "--port", str(port)]
    print(f"[launcher] 启动服务: {url}")
    with open(ROOT / "server.log", "a", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
    # 写 PID 文件，供 stop.bat 精确停止本服务（避免误杀占用同端口的其他程序）
    try:
        (ROOT / "server.pid").write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass

    # 3. 等待就绪（最长 60s），就绪后自动打开浏览器
    ready = False
    for _ in range(60):
        if proc.poll() is not None:
            print("[launcher] 服务启动失败，请查看 server.log")
            return 1
        if health_ok(port):
            ready = True
            break
        time.sleep(1)

    if ready:
        print_banner(port)
        webbrowser.open(url)
    else:
        print("[launcher] 服务启动超时，请查看 server.log")
        proc.terminate()
        return 1

    # 4. 常驻：跟随服务退出
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n[launcher] Shutting down...")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        # 清理 PID 文件
        try:
            (ROOT / "server.pid").unlink(missing_ok=True)
        except (OSError, AttributeError):
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())