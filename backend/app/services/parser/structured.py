"""轻量结构化文档中间层。

解析器先保留页、文本块、行、坐标和字体证据，再派生纯文本/Markdown。这样目录修复、
段落重排和来源定位共享同一份证据，不再把 OCR/PDF 的每一行直接当 Markdown 段落。
PDFText 是首选的 Apache-2.0 文本层后端；未安装或失败时回退到现有 PyMuPDF。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
from typing import Any


BBox = tuple[float, float, float, float]


@dataclass
class DocumentBlock:
    page: int
    text: str
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)
    lines: list[str] = field(default_factory=list)
    role: str = "text"
    font_size: float | None = None
    font_weight: int | None = None
    source: str = "pdf-text"
    confidence: float = 1.0
    reading_order: int = 0
    asset_id: str | None = None
    parent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StructuredPage:
    page: int
    width: float
    height: float
    blocks: list[DocumentBlock] = field(default_factory=list)

    def text(self) -> str:
        return "\n\n".join(block.text.strip() for block in self.blocks if block.text.strip())


@dataclass
class StructuredDocument:
    pages: list[StructuredPage] = field(default_factory=list)
    backend: str = "unknown"
    version: int = 2

    def page_texts(self) -> list[str]:
        return [page.text() for page in self.pages]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    def save_json(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(self.to_json(), encoding="utf-8")
        temporary.replace(target)

    @classmethod
    def load_json(cls, path: str | Path) -> "StructuredDocument":
        """恢复已落盘的版面证据，供目录重识别复用，避免重新扫描整本 PDF。"""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        pages: list[StructuredPage] = []
        for page_value in raw.get("pages", []):
            blocks = []
            for value in page_value.get("blocks", []):
                blocks.append(DocumentBlock(
                    page=int(value.get("page") or page_value.get("page") or 1),
                    text=str(value.get("text") or ""), bbox=_bbox(value.get("bbox")),
                    lines=list(value.get("lines") or []), role=str(value.get("role") or "text"),
                    font_size=value.get("font_size"), font_weight=value.get("font_weight"),
                    source=str(value.get("source") or "pdf-text"),
                    confidence=float(value.get("confidence") or 0),
                    reading_order=int(value.get("reading_order") or 0),
                    asset_id=value.get("asset_id"), parent_id=value.get("parent_id"),
                    metadata=dict(value.get("metadata") or {}),
                ))
            pages.append(StructuredPage(
                page=int(page_value.get("page") or len(pages) + 1),
                width=float(page_value.get("width") or 0),
                height=float(page_value.get("height") or 0), blocks=blocks,
            ))
        return cls(pages=pages, backend=str(raw.get("backend") or "unknown"),
                   version=int(raw.get("version") or 2))


def _bbox(value: Any) -> BBox:
    try:
        values = tuple(float(x) for x in value[:4])
        if len(values) == 4:
            return values  # type: ignore[return-value]
    except (TypeError, ValueError):
        pass
    return (0.0, 0.0, 0.0, 0.0)


def _join_line_texts(lines: list[str]) -> str:
    """按语言边界恢复块内段落，保留列表/标题的独立性。"""
    from backend.app.services.analyzer.textclean import reflow_paragraphs

    return reflow_paragraphs("\n".join(line.strip() for line in lines if line.strip()))


def _order_page_blocks(page: StructuredPage) -> None:
    """按全宽块分段，段内先左栏后右栏，避免双栏论文逐行左右穿插。"""
    if not page.blocks:
        return
    figures = [item for item in page.blocks if item.role == "figure"]
    for block in page.blocks:
        if block.role == "text" and re.match(r"^(?:图\s*[0-9一二三四五六七八九十]+|Fig(?:ure)?\.?\s*\d+)", block.text.strip(), re.I):
            block.role = "caption"
            if figures:
                nearest = min(figures, key=lambda figure: abs(figure.bbox[3] - block.bbox[1]))
                block.parent_id = nearest.asset_id
    midpoint = page.width / 2 if page.width else 0
    full_width: list[DocumentBlock] = []
    narrow: list[DocumentBlock] = []
    ordered: list[DocumentBlock] = []

    def flush_narrow(before_y: float | None = None) -> None:
        ready = [block for block in narrow if before_y is None or block.bbox[1] < before_y]
        if not ready:
            return
        ready.sort(key=lambda block: (0 if (block.bbox[0] + block.bbox[2]) / 2 < midpoint else 1,
                                      block.bbox[1], block.bbox[0]))
        ordered.extend(ready)
        for block in ready:
            narrow.remove(block)

    for block in sorted(page.blocks, key=lambda item: (item.bbox[1], item.bbox[0])):
        width = max(0.0, block.bbox[2] - block.bbox[0])
        crosses_middle = block.bbox[0] < midpoint < block.bbox[2]
        if page.width and (crosses_middle or width >= page.width * .72):
            flush_narrow(block.bbox[1])
            ordered.append(block); full_width.append(block)
        else:
            narrow.append(block)
    flush_narrow()
    page.blocks = ordered
    for index, block in enumerate(page.blocks):
        block.reading_order = index


def _append_pymupdf_images(path: Path, pages: list[StructuredPage]) -> None:
    """只保存图片引用与坐标，不把二进制图片塞入结构 JSON。"""
    import fitz

    doc = fitz.open(path)
    try:
        for page_index, pdf_page in enumerate(doc):
            if page_index >= len(pages):
                break
            target = pages[page_index]
            seen: set[tuple[int, tuple[float, float, float, float]]] = set()
            for image in pdf_page.get_images(full=True):
                xref = int(image[0])
                for rect in pdf_page.get_image_rects(xref):
                    box = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
                    key = (xref, box)
                    if key in seen or rect.width < 8 or rect.height < 8:
                        continue
                    seen.add(key)
                    target.blocks.append(DocumentBlock(
                        page=page_index + 1, text="[图像]", bbox=box, role="figure",
                        source="pymupdf-image", confidence=1.0,
                        asset_id=f"pdf:{page_index + 1}:xref:{xref}",
                        metadata={"xref": xref, "width": float(rect.width), "height": float(rect.height)},
                    ))
    finally:
        doc.close()


def _from_pdftext(path: Path) -> StructuredDocument:
    from pdftext.extraction import dictionary_output

    try:
        raw_pages = dictionary_output(str(path), sort=True, keep_chars=False, workers=1)
    except TypeError:
        # 兼容未暴露 workers 参数的 PDFText 版本；外层任务锁仍保证单任务运行。
        raw_pages = dictionary_output(str(path), sort=True, keep_chars=False)
    pages: list[StructuredPage] = []
    for index, raw_page in enumerate(raw_pages, start=1):
        page_bbox = _bbox(raw_page.get("bbox"))
        page = StructuredPage(
            page=index,
            width=max(0.0, page_bbox[2] - page_bbox[0]),
            height=max(0.0, page_bbox[3] - page_bbox[1]),
        )
        for raw_block in raw_page.get("blocks", []):
            line_texts: list[str] = []
            sizes: list[float] = []
            weights: list[int] = []
            for raw_line in raw_block.get("lines", []):
                spans = raw_line.get("spans", [])
                text = "".join(str(span.get("text") or "") for span in spans).strip()
                if text:
                    line_texts.append(text)
                for span in spans:
                    font = span.get("font") or {}
                    if font.get("size") is not None:
                        sizes.append(float(font["size"]))
                    if font.get("weight") is not None:
                        weights.append(int(font["weight"]))
            text = _join_line_texts(line_texts)
            if not text:
                continue
            page.blocks.append(DocumentBlock(
                page=index,
                text=text,
                bbox=_bbox(raw_block.get("bbox")),
                lines=line_texts,
                font_size=max(sizes) if sizes else None,
                font_weight=max(weights) if weights else None,
                source="pdftext",
            ))
        pages.append(page)
    _append_pymupdf_images(path, pages)
    for page in pages:
        _order_page_blocks(page)
    return StructuredDocument(pages=pages, backend="pdftext")


def _from_pymupdf(path: Path) -> StructuredDocument:
    import fitz

    doc = fitz.open(path)
    pages: list[StructuredPage] = []
    try:
        for index, pdf_page in enumerate(doc, start=1):
            page = StructuredPage(index, float(pdf_page.rect.width), float(pdf_page.rect.height))
            data = pdf_page.get_text("dict", sort=True, flags=fitz.TEXTFLAGS_TEXT)
            for raw_block in data.get("blocks", []):
                line_texts: list[str] = []
                sizes: list[float] = []
                weights: list[int] = []
                for raw_line in raw_block.get("lines", []):
                    spans = raw_line.get("spans", [])
                    text = "".join(str(span.get("text") or "") for span in spans).strip()
                    if text:
                        line_texts.append(text)
                    sizes.extend(float(span.get("size") or 0) for span in spans)
                    weights.extend(700 if int(span.get("flags") or 0) & 16 else 400 for span in spans)
                text = _join_line_texts(line_texts)
                if text:
                    page.blocks.append(DocumentBlock(
                        page=index, text=text, bbox=_bbox(raw_block.get("bbox")),
                        lines=line_texts, font_size=max(sizes) if sizes else None,
                        font_weight=max(weights) if weights else None,
                        source="pymupdf",
                    ))
            pages.append(page)
    finally:
        doc.close()
    _append_pymupdf_images(path, pages)
    for page in pages:
        _order_page_blocks(page)
    return StructuredDocument(pages=pages, backend="pymupdf")


def extract_structured_pdf(path: str | Path, prefer_pdftext: bool = True) -> StructuredDocument:
    """提取结构化 PDF；可选 PDFText 失败时无损回退，不阻塞导入。"""
    source = Path(path)
    if prefer_pdftext:
        try:
            return _from_pdftext(source)
        except (ImportError, RuntimeError, TypeError, ValueError, OSError):
            pass
    return _from_pymupdf(source)


def replace_pages_with_ocr(document: StructuredDocument, page_texts: list[str],
                           page_numbers: set[int], layout_cache_dir: Path | None = None) -> StructuredDocument:
    """把 OCR 页写回统一结构；未识别页继续保留原始坐标块。"""
    by_page = {page.page: page for page in document.pages}
    for page_no in page_numbers:
        if not (1 <= page_no <= len(page_texts)):
            continue
        page = by_page.get(page_no)
        if page is None:
            page = StructuredPage(page_no, 0.0, 0.0)
            document.pages.append(page)
        text = page_texts[page_no - 1].strip()
        page.blocks = [DocumentBlock(
            page=page_no,
            text=text,
            lines=[line.strip() for line in text.splitlines() if line.strip()],
            bbox=(0.0, 0.0, page.width, page.height),
            role="text",
            source="ocr",
            confidence=0.8,
        )] if text else []
    document.pages.sort(key=lambda item: item.page)
    if layout_cache_dir is not None:
        hydrate_ocr_geometry(document, layout_cache_dir)
    return document


def hydrate_ocr_geometry(document: StructuredDocument, cache_dir: Path) -> int:
    """Restore per-line OCR geometry from local caches, without running OCR.

    Old imports stored a single full-page block; its top edge was then mistaken
    for a header. Only replace OCR/empty pages, preserving native text evidence.
    """
    restored = 0
    for page in document.pages:
        if page.blocks and not all(block.source == 'ocr' for block in page.blocks):
            continue
        cache = Path(cache_dir) / f'page_{page.page:04d}.layout.json'
        try:
            payload = json.loads(cache.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        blocks = []
        if page.width <= 0 or page.height <= 0:
            continue
        for item in payload.get('items', []):
            try:
                x, y, w, h = (float(item[k]) for k in ('x', 'y', 'w', 'h'))
                text = str(item.get('text') or '').strip()
                if not text or min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.001 or y + h > 1.001:
                    continue
                blocks.append(DocumentBlock(page=page.page, text=text, lines=[text],
                    bbox=(x * page.width, y * page.height, (x + w) * page.width, (y + h) * page.height),
                    font_size=h * page.height, source='ocr', confidence=float(item.get('confidence') or .8),
                    reading_order=len(blocks)))
            except (KeyError, TypeError, ValueError):
                continue
        if blocks:
            page.blocks = blocks
            restored += 1
    return restored


def repeated_margin_lines(document: StructuredDocument, min_pages: int = 3) -> tuple[set[str], set[str]]:
    """根据坐标和跨页重复识别页眉页脚，不靠正文内容猜测。"""
    top: dict[str, set[int]] = {}
    bottom: dict[str, set[int]] = {}
    for page in document.pages:
        if page.height <= 0:
            continue
        for block in page.blocks:
            if block.role not in {"text", "title", "caption"}:
                continue
            key = re.sub(r"\s+", " ", block.text.strip())
            if not key or len(key) > 80:
                continue
            if block.bbox[1] <= page.height * 0.09:
                top.setdefault(key, set()).add(page.page)
            if block.bbox[3] >= page.height * 0.91:
                bottom.setdefault(key, set()).add(page.page)
    return (
        {text for text, pages in top.items() if len(pages) >= min_pages},
        {text for text, pages in bottom.items() if len(pages) >= min_pages},
    )


def remove_margin_blocks(document: StructuredDocument) -> StructuredDocument:
    headers, footers = repeated_margin_lines(document)
    for page in document.pages:
        page.blocks = [block for block in page.blocks if block.text.strip() not in headers | footers]
    return document


def structured_to_markdown(document: StructuredDocument) -> str:
    """从结构化证据渲染 Markdown；渲染是末端产物，不反向覆盖底层 JSON。"""
    from backend.app.services.rag.toc_heuristic import classify_heading

    parts: list[str] = []
    pending = ""
    pending_block: DocumentBlock | None = None
    sentence_end = re.compile(r"[。！？!?；;.]\s*$")
    for page in document.pages:
        for block in page.blocks:
            text = block.text.strip()
            if not text:
                continue
            heading = classify_heading(text.replace("\n", " "))
            if heading:
                if pending:
                    parts.append(pending)
                    pending = ""
                    pending_block = None
                title, level = heading
                parts.append(f"{'#' * level} {title}")
                continue
            if block.role in {"table", "formula", "caption", "list", "figure"}:
                if pending:
                    parts.append(pending)
                    pending = ""
                    pending_block = None
                parts.append(text if block.role != "figure" else
                             f"> 原文图像（第 {block.page} 页，资源 {block.asset_id or '未编号'}；请在原版视图查看）")
                continue
            same_column = True
            if pending_block and pending_block.page == block.page and page.width:
                previous_center = (pending_block.bbox[0] + pending_block.bbox[2]) / 2
                current_center = (block.bbox[0] + block.bbox[2]) / 2
                previous_narrow = (pending_block.bbox[2] - pending_block.bbox[0]) < page.width * .72
                current_narrow = (block.bbox[2] - block.bbox[0]) < page.width * .72
                same_column = not (previous_narrow and current_narrow and
                                   (previous_center < page.width / 2) != (current_center < page.width / 2))
            if pending and not sentence_end.search(pending) and same_column:
                if re.search(r"[\u4e00-\u9fff，、：；]$", pending) and re.match(
                    r"[\u4e00-\u9fff]", text
                ):
                    pending += text
                else:
                    pending += " " + text
            else:
                if pending:
                    parts.append(pending)
                pending = text
            pending_block = block
        if pending and sentence_end.search(pending):
            parts.append(pending)
            pending = ""
            pending_block = None
    if pending:
        parts.append(pending)
    return "\n\n".join(part.strip() for part in parts if part.strip())
