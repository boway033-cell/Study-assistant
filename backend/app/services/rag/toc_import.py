"""按文档类型选择目录证据，避免 Office 正文被 PDF 规则误判。"""
from __future__ import annotations

from typing import Any


def _native_rows(native_toc: list[Any]) -> list[dict]:
    rows = []
    seen: set[tuple[str, int]] = set()
    previous_level = 0
    for item in native_toc:
        title = str(getattr(item, "title", None) or item.get("title") or "").strip()
        page = max(1, int(getattr(item, "page", None) or item.get("page") or 1))
        if not title or len(title) > 255:
            continue
        key = (title, page)
        if key in seen:
            continue
        seen.add(key)
        requested = max(1, min(4, int(getattr(item, "level", None) or item.get("level") or 1)))
        # 首项必须有根；后续最多下降一级。保留来源层级，但不制造幽灵父节点。
        level = 1 if not rows else min(requested, previous_level + 1)
        rows.append({"title": title, "level": level, "page": page})
        previous_level = level
    return rows


def select_import_toc(file_type: str, native_toc: list[Any], cleaned_pages: list[str], layout=None) -> list[dict]:
    """PDF 融合书签/正文/版面；DOCX/PPTX 只采用其原生结构证据。"""
    native = _native_rows(native_toc)
    if file_type.lower() != "pdf":
        return native

    from backend.app.services.rag.toc_heuristic import (
        extract_toc_from_layout,
        extract_toc_heuristic,
        merge_toc_sources,
        contents_page_numbers,
    )
    text_toc = extract_toc_heuristic(cleaned_pages)
    layout_toc = extract_toc_from_layout(layout) if layout else []
    excluded = contents_page_numbers(cleaned_pages)
    if layout:
        for blocks in layout.pages:
            if blocks and contents_page_numbers(["\n".join(block.text for block in blocks)]):
                excluded.add(blocks[0].page)
    sources = [[row for row in source if row["page"] not in excluded]
               for source in (native, text_toc, layout_toc)]
    return merge_toc_sources(*sources)
