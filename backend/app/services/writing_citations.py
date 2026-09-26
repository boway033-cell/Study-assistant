"""Separate machine-readable evidence anchors from reader-facing prose."""
from __future__ import annotations

import re


ANCHOR_RE = re.compile(
    r"\[(B\d+(?::(?:CH|C|P|NOTE)\d+(?:-\d+)?)*"          # 本地文献片段 / 章节 / 笔记
    r"|(?:NOTE|EVIDENCE|REPORT):\d+"                      # 库内知识对象
    r"|WEB:[A-Za-z0-9][A-Za-z0-9._/:\-]*)\]"              # 联网元数据快照
)
_SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")

WEB_ANCHOR_RE = re.compile(r"^WEB:([A-Za-z0-9._\-]+):(.+)$")


def web_anchor(provider: str, provider_id: str) -> str:
    """Stable machine anchor for an external metadata snapshot."""
    safe_provider = re.sub(r"[^A-Za-z0-9._\-]", "-", str(provider or "provider"))[:40]
    safe_id = re.sub(r"[^A-Za-z0-9._/:\-]", "-", str(provider_id or "")).strip("-")[:160]
    return f"WEB:{safe_provider}:{safe_id}"


def web_source_labels(sources: list[dict] | None) -> dict[str, str]:
    """Reader-facing footnote labels for online snapshots.

    The label always names the provider and, when present, the DOI/URL plus the
    evidence level, so a reader can tell "摘要级依据" from "元数据线索".
    """
    labels: dict[str, str] = {}
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        provider = str(source.get("provider") or "外部来源")
        anchor = web_anchor(provider, source.get("provider_id"))
        bits = [f"{provider}｜{str(source.get('title') or '').strip() or '未标题名'}"]
        authors, year = str(source.get("authors") or "").strip(), source.get("year")
        if authors or year:
            bits.append(f"{authors}{'，' if authors and year else ''}{year or ''}".strip("，"))
        locator = str(source.get("doi") or "").strip() or str(source.get("url") or "").strip()
        if locator:
            bits.append(f"DOI {locator}" if locator.startswith("10.") else locator)
        level = str(source.get("evidence_level") or "metadata")
        bits.append("摘要级依据" if level == "abstract" else "元数据线索（不可作为事实依据）")
        if source.get("retrieved_at"):
            bits.append(f"检索于 {str(source['retrieved_at'])[:10]}")
        labels[anchor] = "，".join(bit for bit in bits if bit)
    return labels


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
