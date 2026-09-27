"""按文档类型选择目录证据，避免 Office 正文被 PDF 规则误判。"""
from __future__ import annotations

from difflib import SequenceMatcher
from statistics import median
from typing import Any


def _native_rows(native_toc: list[Any]) -> list[dict]:
    rows = []
    seen: set[tuple[str, int]] = set()
    previous_level = 0
    for item in native_toc:
        title = str(getattr(item, "title", None) or item.get("title") or "").replace("\x00", "").strip()
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


def _reliable_native_spine(rows: list[dict], cleaned_pages: list[str], excluded: set[int]) -> list[dict]:
    """Use physical PDF destinations when bookmarks form a real long-book spine."""
    from backend.app.services.rag.toc_heuristic import _norm_title_key

    total_pages = len(cleaned_pages)
    chrome = {"封面", "封底", "扉页", "书名", "书名页", "版权", "版权信息",
              "目录", "目次", "contents", "tableofcontents"}
    selected = [row for row in rows if row["page"] not in excluded and row["page"] <= total_pages
                and "".join(row["title"].lower().split()) not in chrome]
    pages = [row["page"] for row in selected]
    if (total_pages < 60 or len(selected) < 4 or len(set(pages)) < 4
            or pages != sorted(pages) or max(pages) - min(pages) < 6):
        return []
    # Some EPUB-derived PDFs place chapter bookmarks one physical page after
    # the chapter title leaf. Correct only when several long, exact headings
    # agree on that offset; a single repeated running header is not evidence.
    matches = []
    for row in selected:
        page = row["page"]
        key = _norm_title_key(row["title"])
        if len(key) < 6 or page < 2:
            continue
        previous = key in _norm_title_key(cleaned_pages[page - 2])
        current = key in _norm_title_key(cleaned_pages[page - 1])
        matches.append((previous, current))
    previous_only = sum(previous and not current for previous, current in matches)
    current_only = sum(current and not previous for previous, current in matches)
    shift_previous = previous_only >= 3 and previous_only >= current_only * 2
    first_level = selected[0]["level"]
    previous = 0
    spine = []
    for row in selected:
        page = row["page"]
        key = _norm_title_key(row["title"])
        if (shift_previous and len(key) >= 6 and page >= 2
                and key in _norm_title_key(cleaned_pages[page - 2])
                and key not in _norm_title_key(cleaned_pages[page - 1])
                and page - 1 not in excluded):
            page -= 1
        level = min(4, max(1, row["level"] - first_level + 1))
        level = min(level, previous + 1)
        spine.append({"title": row["title"], "level": level, "page": page,
                      "confidence": .95})
        previous = level
    return spine


def _anchored_printed_toc(entries: list[dict], candidates: list[dict],
                          excluded: set[int], total_pages: int) -> list[dict]:
    """Map printed entries to body title pages; never use printed page numbers directly."""
    from backend.app.services.rag.toc_heuristic import _norm_title_key

    body = [row for row in candidates if int(row["page"]) not in excluded
            and 1 <= int(row["page"]) <= total_pages]
    by_key: dict[str, list[dict]] = {}
    for row in body:
        by_key.setdefault(_norm_title_key(row["title"]), []).append(row)
    offsets = []
    for entry in entries:
        matches = {int(row["page"]) for row in by_key.get(_norm_title_key(entry["title"]), [])
                   if int(row["page"]) > entry["toc_page"]}
        if len(matches) == 1:
            offsets.append(next(iter(matches)) - entry["printed_page"])
    proposed = median(offsets) if len(offsets) >= 2 else None
    inliers = sum(abs(value - proposed) <= 2 for value in offsets) if proposed is not None else 0
    offset = proposed if proposed is not None and inliers >= max(2, len(offsets) * .8) else None

    mapped = []
    previous_page = 0
    active_levels: set[int] = set()
    for entry in entries:
        level = int(entry["level"])
        if level == 1:
            active_levels.clear()
        elif level - 1 not in active_levels:
            continue
        key = _norm_title_key(entry["title"])
        matches = [row for row in by_key.get(key, [])
                   if int(row["page"]) > entry["toc_page"] and int(row["page"]) >= previous_page]
        expected = entry["printed_page"] + offset if offset is not None else None
        if expected is not None:
            matches = [row for row in matches if abs(int(row["page"]) - expected) <= 3]
        if not matches and expected is not None and len(key) >= 5:
            # A PDF text block can join the chapter heading with the first body sentence.
            matches = [row for row in body if int(row["page"]) > entry["toc_page"]
                       and int(row["page"]) >= previous_page
                       and abs(int(row["page"]) - expected) <= 2
                       and _norm_title_key(row["title"]).startswith(key)]
        if not matches and offset is not None and len(key) >= 6:
            scored = [(SequenceMatcher(None, key, _norm_title_key(row["title"])).ratio(), row)
                      for row in body if entry["toc_page"] < int(row["page"])
                      and previous_page <= int(row["page"]) <= total_pages
                      and abs(int(row["page"]) - expected) <= 2]
            matches = [row for score, row in scored if score >= .88]
        if not matches:
            active_levels = {value for value in active_levels if value < level}
            continue
        if offset is not None:
            match = min(matches, key=lambda row: (abs(int(row["page"]) - expected), int(row["page"])))
        else:
            match = min(matches, key=lambda row: int(row["page"]))
        page = int(match["page"])
        if mapped and mapped[-1]["page"] == page and mapped[-1]["title"] == entry["title"]:
            active_levels.add(level)
            continue
        mapped.append({"title": entry["title"], "level": level,
                       "page": page, "confidence": .85})
        active_levels = {value for value in active_levels if value < level}
        active_levels.add(level)
        previous_page = page
    return mapped if len(mapped) >= 3 and len(mapped) >= len(entries) * .45 else []


def select_import_toc(file_type: str, native_toc: list[Any], cleaned_pages: list[str], layout=None) -> list[dict]:
    """PDF 融合书签/正文/版面；DOCX/PPTX 只采用其原生结构证据。"""
    native = _native_rows(native_toc)
    if file_type.lower() != "pdf":
        return native

    from backend.app.services.rag.toc_heuristic import (
        extract_toc_from_layout,
        extract_toc_heuristic,
        extract_contents_entries,
        merge_toc_sources,
        contents_page_numbers,
    )
    excluded = contents_page_numbers(cleaned_pages)
    if layout:
        layout_pages = ["\n".join(block.text for block in blocks) for blocks in layout.pages]
        excluded.update(contents_page_numbers(layout_pages))
    text_toc = extract_toc_heuristic(cleaned_pages, contents_pages=excluded)
    layout_toc = extract_toc_from_layout(layout, excluded) if layout else []
    sources = [[row for row in source if row["page"] not in excluded]
               for source in (native, text_toc, layout_toc)]
    reliable_native = _reliable_native_spine(sources[0], cleaned_pages, excluded)
    if reliable_native:
        return reliable_native
    printed = extract_contents_entries(cleaned_pages, excluded)
    anchored = _anchored_printed_toc(printed, sources[1] + sources[2], excluded, len(cleaned_pages))
    if anchored:
        return anchored
    return merge_toc_sources(*sources)
