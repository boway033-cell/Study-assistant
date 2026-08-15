"""资料导入任务：上传文件 → 解析 → 章节 → 切片 → FTS 索引（docs/01-architecture.md §4.1）"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.database import SessionLocal
from backend.app.models import Book, Chapter, Chunk
from backend.app.services.rag.chunker import build_chapter_pages, build_chapters, split_pages_into_chunks
from backend.app.services.rag.fts import delete_book_index, index_chunk
from backend.app.services.parser import ParseError, parse_document
from backend.app.worker.tasks import TaskRecord, update_progress


def save_upload(file_name: str, content: bytes) -> Path:
    """保存上传文件到 data/uploads，返回路径。"""
    safe_name = Path(file_name).name  # 防路径穿越
    dest = settings.uploads_dir / safe_name
    # 重名加后缀
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        i = 1
        while dest.exists():
            dest = settings.uploads_dir / f"{stem}_{i}{suffix}"
            i += 1
    dest.write_bytes(content)
    return dest


async def run_import(record: TaskRecord, book_id: int) -> dict:
    """执行完整导入流水线。book 需已创建且 status=pending。"""
    db = SessionLocal()
    try:
        book = db.get(Book, book_id)
        if book is None:
            raise ValueError(f"书籍不存在: {book_id}")
        book.status = "parsing"
        db.commit()

        # 1. 解析
        update_progress(record, 0.1, "parsing", "正在解析文档...")
        result = parse_document(settings.uploads_dir / book.file_path)

        if not result.pages or all(not p.strip() for p in result.pages):
            raise ParseError("未能从文档中提取到文本（可能是扫描版 PDF，暂不支持 OCR）")

        book.total_pages = result.total_pages
        db.commit()

        # 2. 章节树
        update_progress(record, 0.35, "chapters", "正在构建章节树...")
        chapter_defs = build_chapters(result.toc, result.total_pages)
        id_map: dict[int, int] = {}  # 定义序 -> DB id
        for ch_def in chapter_defs:
            parent_id = id_map.get(ch_def["parent_id"]) if ch_def["parent_id"] is not None else None
            ch = Chapter(
                book_id=book.id,
                parent_id=parent_id,
                title=ch_def["title"],
                level=ch_def["level"],
                order_index=ch_def["order_index"],
                start_page=ch_def["start_page"],
                end_page=ch_def["end_page"],
            )
            db.add(ch)
            db.flush()
            id_map[ch_def["order_index"]] = ch.id
        db.commit()

        # 章节 id 列表（按 order_index 排序）
        chapters = db.scalars(
            select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.order_index)
        ).all()
        chapter_id_by_order = {c.order_index: c.id for c in chapters}
        chapter_pages = build_chapter_pages(chapter_defs, result.total_pages)
        # 把定义序换成 DB id
        chapter_pages_db = [
            (chapter_id_by_order.get(i), s, e) if i is not None else (None, s, e)
            for i, s, e in chapter_pages
        ]

        # 3. 切片
        update_progress(record, 0.55, "chunking", "正在切片...")
        chunks = split_pages_into_chunks(result.pages, chapter_pages_db)

        # 4. 写 chunks + FTS 索引
        delete_book_index(book.id)
        update_progress(record, 0.7, "indexing", "正在建立全文索引...")
        total = len(chunks)
        # 4a. 先批量写入 chunks 并 commit（释放写锁，避免与 FTS 写冲突）
        for ch_def in chunks:
            db.add(Chunk(
                book_id=book.id,
                chapter_id=ch_def["chapter_id"],
                content=ch_def["content"],
                page_start=ch_def["page_start"],
                page_end=ch_def["page_end"],
                chunk_index=ch_def["chunk_index"],
                word_count=ch_def["word_count"],
            ))
        db.commit()
        # 4b. 取回 id，写入 FTS（独立连接，此时无锁冲突）
        chunk_rows = db.scalars(
            select(Chunk).where(Chunk.book_id == book.id).order_by(Chunk.chunk_index)
        ).all()
        for i, ch in enumerate(chunk_rows):
            index_chunk(book.id, ch.chapter_id, ch.page_start, ch.id, ch.content)
            if i % 20 == 0:
                update_progress(record, 0.7 + 0.29 * (i + 1) / total, "indexing",
                                f"索引中 {i + 1}/{total}")
        db.commit()

        book.status = "ready"
        db.commit()
        return {"book_id": book.id, "chapters": len(chapters), "chunks": total}
    except Exception as e:  # noqa: BLE001
        db.rollback()
        book = db.get(Book, book_id)
        if book is not None:
            book.status = "failed"
            book.error_msg = str(e)
            db.commit()
        raise
    finally:
        db.close()
