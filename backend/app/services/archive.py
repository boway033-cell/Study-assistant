"""本地文献归档与来源映射。

只从用户上传文件及其解析文本提取元数据，不联网、不猜测缺失书目信息。
"""
from __future__ import annotations

import json
import re
from pathlib import Path


_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_ARXIV_RE = re.compile(r"\b(?:arXiv\s*:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _clean_meta(value) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip(" ;,")
    return text or None


def extract_metadata(file_path: str | Path, pages: list[str]) -> dict:
    """提取内嵌元数据和稳定标识；所有字段均可追溯到本地文件。"""
    path = Path(file_path)
    metadata: dict = {}
    try:
        if path.suffix.lower() == ".pdf":
            import fitz
            with fitz.open(path) as doc:
                raw = doc.metadata or {}
            metadata.update({
                "title": _clean_meta(raw.get("title")),
                "authors": _clean_meta(raw.get("author")),
                "journal": _clean_meta(raw.get("subject")),
                "keywords": _clean_meta(raw.get("keywords")),
            })
        elif path.suffix.lower() == ".docx":
            import docx
            props = docx.Document(path).core_properties
            metadata.update({
                "title": _clean_meta(props.title),
                "authors": _clean_meta(props.author),
                "journal": _clean_meta(props.subject),
                "keywords": _clean_meta(props.keywords),
            })
        elif path.suffix.lower() == ".pptx":
            from pptx import Presentation
            props = Presentation(path).core_properties
            metadata.update({
                "title": _clean_meta(props.title),
                "authors": _clean_meta(props.author),
                "journal": _clean_meta(props.subject),
                "keywords": _clean_meta(props.keywords),
            })
    except Exception:  # metadata failure must never block import
        pass

    sample = "\n".join(pages[:3])[:20000]
    doi = _DOI_RE.search(sample)
    arxiv = _ARXIV_RE.search(sample)
    years = [int(y) for y in _YEAR_RE.findall(sample[:5000])]
    cjk = len(re.findall(r"[\u4e00-\u9fff]", sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    metadata.update({
        "doi": doi.group(0).rstrip(".,;)") if doi else None,
        "arxiv_id": arxiv.group(1) if arxiv else None,
        "published_year": max(years) if years else None,
        "language": "zh" if cjk > latin * 0.35 else "en",
        "access_route": "local_upload",
        "provenance_json": json.dumps({
            "metadata_source": "embedded_and_first_pages",
            "file_name": path.name,
            "network_used": False,
        }, ensure_ascii=False),
    })
    return metadata


def build_source_map(book_id: int, chunks, chapters_by_id: dict[int, str]) -> str:
    """构建稳定的 chunk→页码→章节来源映射。"""
    blocks = []
    for chunk in chunks:
        blocks.append({
            "source_id": f"B{book_id}-C{chunk.id}",
            "chunk_id": chunk.id,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "chapter_id": chunk.chapter_id,
            "chapter_title": chapters_by_id.get(chunk.chapter_id),
            "located": bool(chunk.page_start and chunk.page_start > 0),
        })
    return json.dumps({
        "version": 1,
        "book_id": book_id,
        "locator_mode": "page-grounded" if any(x["located"] for x in blocks) else "structure-grounded",
        "blocks": blocks,
    }, ensure_ascii=False)
