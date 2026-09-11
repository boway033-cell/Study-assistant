"""按需把 DOCX/PPTX 交给本机 Office 渲染为高保真 PDF。

渲染进程不常驻；结果按文件哈希缓存。宏自动化被禁用，源文件只读打开。
"""
from __future__ import annotations

import os
import asyncio
import hashlib
import shutil
import subprocess
import tempfile
import threading
import concurrent.futures
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT, settings

_render_lock = threading.Lock()

# 独立单线程执行器：Office COM 不宜并发，且避免占用 FastAPI 默认线程池。
_render_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="office-render")


async def render_office_pdf_async(source: Path, file_type: str, file_hash: str | None, timeout: int = 120) -> Path:
    """在独立单线程执行器内执行渲染，释放事件循环线程。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_render_executor, render_office_pdf, source, file_type, file_hash, timeout)


def office_renderer_available(file_type: str) -> bool:
    if os.name != "nt" or file_type not in {"docx", "pptx"}:
        return False
    progid = "Word.Application" if file_type == "docx" else "PowerPoint.Application"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\CLSID"):
            return True
    except OSError:
        return False


def _source_hash(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rendered_pdf_path(file_hash: str) -> Path:
    root = settings.structured_dir / "office-rendered"
    root.mkdir(parents=True, exist_ok=True)
    safe_hash = "".join(ch for ch in file_hash.lower() if ch in "0123456789abcdef")[:64]
    if len(safe_hash) < 16:
        raise ValueError("文件哈希无效")
    return root / f"{safe_hash}.pdf"


def render_office_pdf(source: Path, file_type: str, file_hash: str | None, timeout: int = 120) -> Path:
    if file_type not in {"docx", "pptx"}:
        raise ValueError("仅 DOCX/PPTX 需要 Office 原版渲染")
    if not source.is_file():
        raise FileNotFoundError("原始 Office 文件不存在")
    destination = rendered_pdf_path(file_hash or _source_hash(source))
    if destination.is_file() and destination.stat().st_size > 100:
        return destination
    if not office_renderer_available(file_type):
        raise RuntimeError(f"未检测到 Microsoft {'Word' if file_type == 'docx' else 'PowerPoint'}")
    script = PROJECT_ROOT / "scripts" / "render_office_pdf.ps1"
    if not script.is_file():
        raise RuntimeError("Office 渲染脚本缺失")
    with _render_lock:
        if destination.is_file() and destination.stat().st_size > 100:
            return destination
        with tempfile.TemporaryDirectory(prefix="office-render-", dir=destination.parent) as temp_dir:
            # 通过 ASCII 临时路径交给 Windows COM，规避部分中文系统代码页传参乱码。
            temporary_source = Path(temp_dir) / f"source.{file_type}"
            shutil.copyfile(source, temporary_source)
            temporary = Path(temp_dir) / "rendered.pdf"
            command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                       "-File", str(script), "-InputFile", str(temporary_source.resolve()),
                       "-OutputPdf", str(temporary.resolve())]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=flags, text=True)
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # 仅回收本模块自己拉起的进程树（powershell + 其子 COM 进程），
                # 绝不波及用户自己打开的 Office 进程。
                try:
                    if proc.pid:
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                       capture_output=True, check=False, creationflags=flags)
                except Exception:
                    pass
                raise RuntimeError(f"Office 原版渲染超过 {timeout} 秒") from None
            if proc.returncode != 0 or not temporary.is_file():
                detail = (err or out or "Office 未生成 PDF")[-800:]
                raise RuntimeError(detail)
            with temporary.open("rb") as stream:
                if not stream.read(1024).lstrip().startswith(b"%PDF-"):
                    raise RuntimeError("Office 渲染结果不是有效 PDF")
            os.replace(temporary, destination)
    return destination
