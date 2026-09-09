"""按页生成可选择的 PDF 文字坐标层。

原生 PDF 由浏览器 PDF.js 负责；这里只为无文本层的扫描页按需运行 RapidOCR。
结果按文件哈希和页码缓存，不在内存中常驻整本文档或整页位图。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from backend.app.services.parser.ocr import (
    _file_hash,
    _get_rapid_engine,
    _OCR_ENGINE_LOCK,
    _ocr_cache_dir,
    rapid_result_rows,
)

_OCR_LAYER_LOCK = threading.Lock()


def _layout_path(pdf_path: Path, page_no: int) -> Path:
    return _ocr_cache_dir(_file_hash(pdf_path)) / f"page_{page_no:04d}.layout.json"


def _read_cached(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("version") == 1 and isinstance(value.get("items"), list):
            value["cached"] = True
            return value
    except (OSError, ValueError, TypeError):
        return None
    return None


def _normal_box(box, width: int, height: int) -> dict | None:
    try:
        points = [[float(p[0]), float(p[1])] for p in box]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x0, x1 = max(0.0, min(xs)), min(float(width), max(xs))
        y0, y1 = max(0.0, min(ys)), min(float(height), max(ys))
        if x1 <= x0 or y1 <= y0:
            return None
        return {
            "x": round(x0 / width, 6), "y": round(y0 / height, 6),
            "w": round((x1 - x0) / width, 6), "h": round((y1 - y0) / height, 6),
        }
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        return None


def get_ocr_text_layer(pdf_path: str | Path, page_no: int, *, generate: bool = True) -> dict:
    """返回单页 OCR 文字框；无缓存时仅识别这一页。页码为 1-based。"""
    pdf_path = Path(pdf_path)
    cache_path = _layout_path(pdf_path, page_no)
    cached = _read_cached(cache_path)
    if cached is not None:
        return cached
    if not generate:
        return {"version": 1, "page": page_no, "source": "ocr", "status": "missing", "items": []}

    import fitz
    import numpy as np
    import cv2

    with _OCR_LAYER_LOCK:
        cached = _read_cached(cache_path)
        if cached is not None:
            return cached
        with fitz.open(pdf_path) as doc:
            if page_no < 1 or page_no > doc.page_count:
                raise ValueError("页码超出范围")
            page = doc.load_page(page_no - 1)
            from backend.app.core.config import settings
            pix = page.get_pixmap(dpi=settings.ocr_render_dpi, alpha=False)
            width, height = pix.width, pix.height
            channels = pix.n
            array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(height, width, channels)
            bgr = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
            del pix, array

        try:
            engine = _get_rapid_engine()
            with _OCR_ENGINE_LOCK:
                response = engine(bgr)
            rows = rapid_result_rows(response)
            items = []
            for row in rows or []:
                if not row or len(row) < 2:
                    continue
                rect = _normal_box(row[0], width, height)
                text = str(row[1] or "").strip()
                if not rect or not text:
                    continue
                score = float(row[2]) if len(row) > 2 and row[2] is not None else None
                items.append({"text": text, "confidence": round(score, 4) if score is not None else None, **rect})
            result = {
                "version": 1, "page": page_no, "source": "ocr", "status": "ready",
                "image_width": width, "image_height": height, "cached": False, "items": items,
            }
            temporary = cache_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temporary.replace(cache_path)
            return result
        finally:
            del bgr
            # 连续阅读复用同一模型，避免每翻一页都重新载入 ONNX 权重。


def find_quote_in_cached_layer(pdf_path: str | Path, page_no: int, quote: str) -> list[dict]:
    """在已有 OCR 坐标缓存中寻找引用，不为自动修复隐式触发重型 OCR。"""
    cached = _read_cached(_layout_path(Path(pdf_path), page_no))
    if not cached or not quote:
        return []
    compact = "".join(quote.split())
    matches = []
    for item in cached.get("items", []):
        text = "".join(str(item.get("text", "")).split())
        if text and (text in compact or compact in text):
            matches.append({key: item[key] for key in ("x", "y", "w", "h")})
    return matches
