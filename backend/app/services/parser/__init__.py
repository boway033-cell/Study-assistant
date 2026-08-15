"""文档解析器：PDF / DOCX / PPTX → (章节列表, 每页文本)

设计要点（见 docs/01-architecture.md §4.1）：
- PDF 优先用 PyMuPDF（快，目录/文字/页码齐全）
- DOCX 用 python-docx（无 pandoc 依赖）
- PPTX 用 python-pptx
- 输出统一为 PageText 结构，方便后续切片与建索引
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TocItem:
    """目录条目（从 PDF 书签或标题启发式提取）"""
    title: str
    level: int
    page: int  # 1-based


@dataclass
class ParseResult:
    """解析结果：章节树 + 每页文本"""
    pages: list[str] = field(default_factory=list)   # pages[i] = 第 i+1 页文本
    toc: list[TocItem] = field(default_factory=list)  # 有序目录
    total_pages: int = 0


class ParseError(Exception):
    pass


def parse_document(file_path: str | Path) -> ParseResult:
    """按扩展名分发解析。"""
    p = Path(file_path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(p)
    if suffix == ".docx":
        return _parse_docx(p)
    if suffix == ".pptx":
        return _parse_pptx(p)
    raise ParseError(f"不支持的文件类型: {suffix}")


# ---------- PDF ----------
def _parse_pdf(p: Path) -> ParseResult:
    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover
        raise ParseError("PyMuPDF 未安装，请运行 pip install pymupdf") from e

    doc = fitz.open(p)
    result = ParseResult(total_pages=doc.page_count)

    # 1. 书签目录
    raw_toc = doc.get_toc(simple=True)  # [(level, title, page), ...]
    for level, title, page in raw_toc:
        title = title.strip()
        if title:
            result.toc.append(TocItem(title=title, level=level, page=page))

    # 2. 逐页文本
    for page in doc:
        result.pages.append(page.get_text("text"))

    doc.close()
    return result


# ---------- DOCX ----------
def _parse_docx(p: Path) -> ParseResult:
    try:
        import docx
    except ImportError as e:  # pragma: no cover
        raise ParseError("python-docx 未安装") from e

    d = docx.Document(p)
    result = ParseResult()

    # 收集标题（Heading 样式 → 目录候选）与正文段落
    heading_levels = {"Heading 1": 1, "Heading 2": 2, "Heading 3": 3,
                      "Heading 4": 4, "Heading 5": 5, "Title": 1}
    page_text: list[str] = []
    current = []
    page_no = 1

    def flush():
        nonlocal current, page_no
        if current:
            page_text.append("\n".join(current))
            current = []
            page_no += 1

    for para in d.paragraphs:
        style = para.style.name if para.style is not None else ""
        text = para.text.strip()
        if not text:
            continue
        if style in heading_levels:
            flush()
            level = heading_levels[style]
            result.toc.append(TocItem(title=text, level=level, page=page_no))
        current.append(text)

    flush()
    result.pages = page_text or [""]
    result.total_pages = len(page_text)
    return result


# ---------- PPTX ----------
def _parse_pptx(p: Path) -> ParseResult:
    try:
        from pptx import Presentation
    except ImportError as e:  # pragma: no cover
        raise ParseError("python-pptx 未安装") from e

    prs = Presentation(p)
    result = ParseResult(total_pages=len(prs.slides))
    page_texts: list[str] = []

    for i, slide in enumerate(prs.slides, start=1):
        texts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                t = shape.text_frame.text.strip()
                if t:
                    texts.append(t)
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    texts.append(" | ".join(cells))
        page_texts.append("\n".join(texts))

    result.pages = page_texts
    return result
