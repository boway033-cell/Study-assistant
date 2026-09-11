"""从已保存的版面证据重建目录，并用小范围 OCR 补足编号缺口。"""
from __future__ import annotations

from difflib import SequenceMatcher
import json
from pathlib import Path
import re

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.database import SessionLocal
from backend.app.models import Book, Chapter
from backend.app.services.analyzer.layout import analyze_structured
from backend.app.services.parser.structured import StructuredDocument
from backend.app.services.rag.toc_editor import replace_book_toc
from backend.app.services.rag.toc_import import select_import_toc


_CN = "一二三四五六七八九十"
_NUMBERED = re.compile(r"^([一二三四五六七八九十])\s*[、，.]\s*(.+)$")


def _native_toc(path: Path) -> list[dict]:
    import fitz
    document = fitz.open(path)
    try:
        return [{"level": level, "title": title, "page": page}
                for level, title, page, *_ in document.get_toc(simple=False)]
    finally:
        document.close()


def _ocr_layout_path(path: Path, page: int) -> Path:
    from backend.app.services.parser.ocr import _file_hash, _ocr_cache_dir
    return _ocr_cache_dir(_file_hash(path)) / f"page_{page:04d}.layout.json"


def _heading_from_ocr(path: Path, page: int) -> str | None:
    cache = _ocr_layout_path(path, page)
    if not cache.exists():
        return None
    try:
        items = json.loads(cache.read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError):
        return None
    body_heights = sorted(float(item.get("h") or 0) for item in items
                          if 0.1 < float(item.get("y") or 0) < 0.9 and len(str(item.get("text") or "")) > 12)
    body = body_heights[len(body_heights) // 2] if body_heights else .012
    candidates = []
    for item in items:
        text = re.sub(r"\s+", "", str(item.get("text") or "")).strip("、，. ")
        x, y, w, h = (float(item.get(key) or 0) for key in ("x", "y", "w", "h"))
        if not (2 <= len(text) <= 36 and .08 <= y <= .82 and h >= body * 1.22):
            continue
        if re.search(r"[。！？；:]|摘要|关键词|期第|http|copyright", text, re.I):
            continue
        centered = 1 - min(1.0, abs((x + w / 2) - .5) * 3)
        candidates.append((h / max(body, .001) + centered + float(item.get("confidence") or 0), text))
    return max(candidates, default=(0, None))[1]


def _fill_number_gaps(path: Path, rows: list[dict], document: StructuredDocument, record=None) -> list[dict]:
    numbered = []
    for index, row in enumerate(rows):
        match = _NUMBERED.match(str(row.get("title") or ""))
        if match:
            numbered.append((index, _CN.index(match.group(1)) + 1, int(row.get("page") or 1)))
    additions = []
    for (left_index, left_number, left_page), (_, right_number, right_page) in zip(numbered, numbered[1:]):
        gap = right_number - left_number
        pages = list(range(left_page + 1, right_page + 1))
        if not (2 <= gap <= 3 and 0 < len(pages) <= 6):
            continue
        missing = list(range(left_number + 1, right_number))
        uncached = {page for page in pages if not _ocr_layout_path(path, page).exists()}
        if uncached:
            try:
                from backend.app.services.parser.ocr import ocr_pdf, schedule_ocr_engine_release
                if record:
                    from backend.app.worker.tasks import update_progress
                    update_progress(record, record.progress, "toc-ocr", f"正在 OCR 核对目录缺号页 {min(uncached)}–{max(uncached)}")
                try:
                    ocr_pdf(path, page_numbers=uncached, base_pages=document.page_texts(),
                            page_timeout_seconds=min(settings.ocr_page_timeout_seconds, 90))
                finally:
                    # 目录补页 OCR 结束后也走延迟释放，避免把下一段导入的热模型卸掉。
                    schedule_ocr_engine_release()
            except Exception:  # OCR 增强失败不阻断已有目录重建
                pass
        used_titles = {re.sub(r"\s+", "", str(row.get("title") or "")) for row in rows}
        candidates = [(page, _heading_from_ocr(path, page)) for page in pages]
        candidates = [(page, title) for page, title in candidates if title and title not in used_titles]
        for page, title in candidates:
            match = _NUMBERED.match(title)
            if not match or _CN.index(match.group(1)) + 1 not in missing:
                continue
            additions.append({"title": title, "level": rows[left_index].get("level", 1),
                              "page": page, "source": "ocr-gap", "confidence": .68,
                              "evidence": ["OCR 大字号标题", "原文中存在的编号"]})
    return sorted([*rows, *additions], key=lambda row: (int(row.get("page") or 1),
                                                         0 if row.get("source") != "ocr-gap" else 1))


def _replacement_items(db, book: Book, rows: list[dict]) -> list[dict]:
    existing = list(db.scalars(select(Chapter).where(Chapter.book_id == book.id)
                               .order_by(Chapter.order_index)).all())
    unused = {row.id for row in existing}
    items, stack = [], []
    for index, row in enumerate(rows):
        title = str(row.get("title") or "").strip()[:255]
        page = max(1, int(row.get("page") or 1))
        requested = max(1, min(4, int(row.get("level") or 1)))
        level = 1 if not items else min(requested, items[-1]["level"] + 1)
        candidates = [chapter for chapter in existing if chapter.id in unused and
                      abs(int(chapter.start_page or 1) - page) <= 1]
        matched = max(candidates, key=lambda chapter: SequenceMatcher(
            None, re.sub(r"\s+", "", chapter.title), re.sub(r"\s+", "", title)).ratio(), default=None)
        if matched and SequenceMatcher(None, re.sub(r"\s+", "", matched.title),
                                       re.sub(r"\s+", "", title)).ratio() < .42:
            matched = None
        if matched:
            unused.discard(matched.id)
        while len(stack) >= level:
            stack.pop()
        key = f"id:{matched.id}" if matched else f"rebuild:{index}"
        parent_key = stack[-1] if level > 1 and stack else None
        item = {"client_key": key, "id": matched.id if matched else None, "parent_key": parent_key,
                "title": title, "level": level, "start_page": page}
        items.append(item); stack.append(key)
    return items


def rebuild_book_toc(db, book: Book, record=None, *, allow_ocr: bool = True) -> dict:
    structured_path = settings.structured_dir / f"{book.file_hash or book.id}.json"
    source_path = settings.uploads_dir / book.file_path
    if book.file_type != "pdf" or not structured_path.exists() or not source_path.exists():
        return {"book_id": book.id, "status": "skipped", "reason": "缺少 PDF 或结构化证据"}
    document = StructuredDocument.load_json(structured_path)
    from backend.app.services.parser.structured import hydrate_ocr_geometry
    from backend.app.services.parser.ocr import _file_hash, _ocr_cache_dir
    hydrate_ocr_geometry(document, _ocr_cache_dir(_file_hash(source_path)))
    layout = analyze_structured(document)
    cleaned = [layout.clean_page_text(index) for index in range(len(layout.pages))]
    rows = select_import_toc("pdf", _native_toc(source_path), cleaned, layout)
    if allow_ocr:
        rows = _fill_number_gaps(source_path, rows, document, record)
    if not rows:
        return {"book_id": book.id, "status": "skipped", "reason": "未发现可信目录"}
    if record:
        from backend.app.worker.tasks import update_progress
        update_progress(record, .9, 'toc-rebuild', '正在保存目录修订与来源映射')
    result = replace_book_toc(db, book, _replacement_items(db, book, rows), "rebuild",
                              "版面清洗与编号缺口 OCR 重识别")
    return {"book_id": book.id, "status": "rebuilt", "chapters": len(result["chapters"]),
            "revision_id": result["revision_id"]}


async def rebuild_one_toc(record, book_id: int) -> dict:
    import asyncio
    from backend.app.worker.tasks import update_progress
    def rebuild():
        with SessionLocal() as db:
            book = db.get(Book, book_id)
            if book is None:
                raise ValueError('书籍不存在')
            update_progress(record, .1, 'toc-rebuild', '正在复用本书版面和 OCR 坐标缓存')
            result = rebuild_book_toc(db, book, record, allow_ocr=False)
            update_progress(record, 1, 'toc-rebuild', '目录重识别完成' if result['status'] == 'rebuilt' else result['reason'])
            return result
    return await asyncio.to_thread(rebuild)


async def rebuild_all_tocs(record) -> dict:
    from backend.app.worker.tasks import update_progress
    db = SessionLocal()
    results = []
    try:
        books = list(db.scalars(select(Book).where(Book.status == "ready", Book.file_type == "pdf")
                                .order_by(Book.id)).all())
        for index, book in enumerate(books, start=1):
            update_progress(record, .05 + .9 * ((index - 1) / max(1, len(books))), "toc-rebuild",
                            f"正在重识别目录 {index}/{len(books)}：《{book.title}》")
            try:
                results.append(rebuild_book_toc(db, book, record))
            except Exception as exc:  # 单本文献异常不阻断全库维护
                db.rollback(); results.append({"book_id": book.id, "status": "failed", "reason": str(exc)})
        update_progress(record, 1.0, "toc-rebuild", "知识库目录重识别完成")
        return {"total": len(books), "rebuilt": sum(item["status"] == "rebuilt" for item in results),
                "skipped": sum(item["status"] == "skipped" for item in results),
                "failed": sum(item["status"] == "failed" for item in results), "items": results}
    finally:
        db.close()
