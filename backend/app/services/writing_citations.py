"""Separate machine-readable evidence anchors from reader-facing prose."""
from __future__ import annotations

import re


ANCHOR_RE = re.compile(
    r"\[(B\d+(?::(?:CH|C|P|NOTE)\d+(?:-\d+)?)*|(?:NOTE|EVIDENCE|REPORT):\d+)\]"
)
_SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _superscript(number: int) -> str:
    return str(number).translate(_SUPERSCRIPT)


def database_source_labels(db, anchors: set[str]) -> dict[str, str]:
    """Resolve internal anchors into titles/pages without exposing database ids."""
    from backend.app.models import Book, Chapter, KnowledgeNote

    labels: dict[str, str] = {}
    for anchor in anchors:
        book_match = re.search(r"^B(\d+)", anchor)
        if not book_match:
            continue
        book = db.get(Book, int(book_match.group(1)))
        parts = [f"《{book.title}》" if book else "所选文献"]
        chapter_match = re.search(r":CH(\d+)", anchor)
        note_match = re.search(r":NOTE(\d+)", anchor)
        page_match = re.search(r":P(\d+)(?:-(\d+))?", anchor)
        if chapter_match:
            chapter = db.get(Chapter, int(chapter_match.group(1)))
            if chapter:
                parts.append(chapter.title)
        elif note_match:
            note = db.get(KnowledgeNote, int(note_match.group(1)))
            if note:
                parts.append(f"笔记“{note.title}”")
        if page_match:
            start, end = page_match.group(1), page_match.group(2)
            parts.append(f"第 {start} 页" if not end or end == start else f"第 {start}–{end} 页")
        labels[anchor] = "，".join(parts)
    return labels


def readable_citations(text: str, *, valid_anchors: set[str] | None = None,
                       labels: dict[str, str] | None = None,
                       append_source_index: bool = True) -> tuple[str, list[dict]]:
    """Replace ``[B…]`` tokens with compact note numbers and return the audit map.

    Invalid anchors are removed from reader prose; callers retain them in audit metadata.
    """
    valid = {value.strip().strip("[]") for value in valid_anchors} if valid_anchors is not None else None
    labels = labels or {}
    number_by_anchor: dict[str, int] = {}
    citations: list[dict] = []

    def replace(match: re.Match) -> str:
        anchor = match.group(1)
        if valid is not None and anchor not in valid:
            return ""
        if anchor not in number_by_anchor:
            number = len(number_by_anchor) + 1
            number_by_anchor[anchor] = number
            citations.append({"number": number, "anchor": anchor,
                              "label": labels.get(anchor) or "所选文献来源"})
        return _superscript(number_by_anchor[anchor])

    rendered = ANCHOR_RE.sub(replace, text or "")
    rendered = re.sub(r"[ \t]+([，。；：！？])", r"\1", rendered)
    if append_source_index and citations:
        notes = "\n".join(f"{item['number']}. {item['label']}" for item in citations)
        rendered = rendered.rstrip() + "\n\n## 来源索引\n\n" + notes
    return rendered, citations
