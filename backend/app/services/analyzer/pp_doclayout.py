"""PP-DocLayout-M 可选适配层。

增强模型从不进入主进程：仅在用户显式配置后启动 PaddleOCR CLI 子进程，完成即退出。
基础安装、普通 PDF 和低内存机器不会加载 Paddle 或模型。
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


class LayoutEnhancementUnavailable(RuntimeError):
    pass


def available_memory_bytes() -> int | None:
    """返回系统当前可用内存；无法可靠获取时返回 None。"""
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("avail_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended_virtual", ctypes.c_ulonglong),
            ]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.avail_phys)
        return None
    try:
        return int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return None


def build_command(source: Path, output_dir: Path) -> list[str]:
    """构造独立 LayoutDetection worker 命令，不调用 PP-StructureV3。"""
    project_root = Path(__file__).resolve().parents[4]
    worker = project_root / "scripts" / "pp_doclayout_worker.py"
    if not worker.exists():
        raise LayoutEnhancementUnavailable("缺少 PP-DocLayout-M worker")
    bundled_environment = project_root / ".pp-doclayout-venv" / "Scripts" / "python.exe"
    python_executable = os.environ.get(
        "PP_DOCLAYOUT_PYTHON",
        str(bundled_environment) if bundled_environment.exists() else sys.executable,
    )
    if not Path(python_executable).exists():
        raise LayoutEnhancementUnavailable("PP_DOCLAYOUT_PYTHON 指向的解释器不存在")
    return [
        python_executable,
        str(worker),
        "--input", str(source),
        "--output", str(output_dir),
        "--model", "PP-DocLayout-M",
        "--cpu-threads", os.environ.get("PP_DOCLAYOUT_CPU_THREADS", "2"),
        "--image-size", os.environ.get("PP_DOCLAYOUT_IMAGE_SIZE", "960"),
    ]


def run_pp_doclayout(
    source: str | Path,
    output_dir: str | Path,
    *,
    minimum_free_gb: float = 3.0,
    timeout_seconds: int = 900,
) -> list[dict[str, Any]]:
    """隔离运行 PP-DocLayout-M 单模块，并读取 JSON 证据；不会保留服务进程。"""
    available = available_memory_bytes()
    required = int(minimum_free_gb * 1024**3)
    if available is not None and available < required:
        raise LayoutEnhancementUnavailable(
            f"可用内存不足 {minimum_free_gb:g} GB，版面增强任务应留在任务中心等待"
        )
    source_path = Path(source).resolve()
    target = Path(output_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    for stale_result in target.glob("page_*.json"):
        try:
            stale_result.unlink()
        except OSError:
            pass
    child_env = os.environ.copy()
    child_env.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
    completed = subprocess.run(
        build_command(source_path, target),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        shell=False,
        env=child_env,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "未知错误")[-800:]
        raise LayoutEnhancementUnavailable(f"PP-DocLayout-M 单模块执行失败：{detail}")
    payloads: list[dict[str, Any]] = []
    for result_file in target.rglob("*.json"):
        try:
            payload = json.loads(result_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payloads.append(payload)
            elif isinstance(payload, list):
                payloads.extend(item for item in payload if isinstance(item, dict))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
    return payloads


def apply_layout_roles(document, payloads: list[dict[str, Any]]):
    """把模型标签映射到已有文本块；不采用模型生成文本，也不改原文。"""
    role_aliases = {
        "doc_title": "title", "paragraph_title": "title", "title": "title",
        "header": "header", "footer": "footer", "page_number": "footer",
        "table": "table", "figure": "figure", "image": "figure",
        "figure_caption": "caption", "table_caption": "caption",
        "formula": "formula", "reference": "text", "text": "text",
    }
    for fallback_page, payload in enumerate(payloads, start=1):
        root = payload.get("res") if isinstance(payload.get("res"), dict) else payload
        page_no = int(root.get("page_index", root.get("page_id", fallback_page - 1))) + 1
        page = next((item for item in document.pages if item.page == page_no), None)
        if page is None:
            continue
        regions = (
            root.get("boxes") or root.get("parsing_res_list")
            or root.get("layout_det_res") or []
        )
        if isinstance(regions, dict):
            regions = regions.get("boxes") or regions.get("results") or []
        for region in regions if isinstance(regions, list) else []:
            if not isinstance(region, dict):
                continue
            label = str(region.get("block_label") or region.get("label") or "").lower()
            role = role_aliases.get(label)
            bbox = region.get("block_bbox") or region.get("coordinate") or region.get("bbox")
            if role is None or not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                continue
            rx0, ry0, rx1, ry1 = (float(value) for value in bbox[:4])
            best = None
            best_overlap = 0.0
            for block in page.blocks:
                bx0, by0, bx1, by1 = block.bbox
                overlap = max(0.0, min(rx1, bx1) - max(rx0, bx0)) * max(
                    0.0, min(ry1, by1) - max(ry0, by0)
                )
                if overlap > best_overlap:
                    best, best_overlap = block, overlap
            if best is not None:
                best.role = role
                best.source = f"{best.source}+pp-doclayout-m"
    return document
