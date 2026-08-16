"""资料导入任务：上传文件 → 解析 → 版面分析 → 文本清洗 → 关键信息 → 切片 → FTS 索引

流水线（docs/01-architecture.md §4.1 增强版）：
  1. 解析（PDF/DOCX/PPTX），扫描版自动触发 OCR
  2. 版面分析：识别标题/正文/页眉页脚/表格/公式
  3. 文本清洗：去页眉页脚重复、去重、修复断行/重复字符
  4. 关键信息提取：定义句、定理、关键词 → book_analysis 表
  5. 切片 + FTS 索引（索引清洗后的正文）
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.database import SessionLocal
from backend.app.models import Book, BookAnalysis, Chapter, Chunk
from backend.app.services.rag.chunker import build_chapter_pages, build_chapters, split_pages_into_chunks
from backend.app.services.rag.fts import delete_book_index, index_chunk
from backend.app.services.parser import ParseError, parse_document
from backend.app.services.parser.ocr import detect_scanned, ocr_pdf
from backend.app.worker.tasks import TaskRecord, update_progress


def save_upload(file_name: str, content: bytes) -> Path:
    """保存上传文件到 data/uploads，返回路径。"""
    safe_name = Path(file_name).name  # 防路径穿越
    dest = settings.uploads_dir / safe_name
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
        update_progress(record, 0.08, "parsing", "正在解析文档...")
        file_path = settings.uploads_dir / book.file_path
        result = parse_document(file_path)

        # 1b. OCR：扫描版检测（仅 PDF；docx/pptx 必有文本层，跳过避免误判）
        if book.file_type == "pdf" and result.pages and detect_scanned(result.pages):
            update_progress(record, 0.15, "ocr", "检测到扫描版，正在 OCR 识别...")
            result.pages = ocr_pdf(file_path)
            result.total_pages = len(result.pages)

        if not result.pages or all(not p.strip() for p in result.pages):
            raise ParseError("未能从文档中提取到文本（可能是扫描版 PDF）")

        book.total_pages = result.total_pages
        db.commit()

        # 2. 版面分析（仅 PDF）
        layout = None
        if book.file_type == "pdf":
            update_progress(record, 0.25, "layout", "正在分析版面结构...")
            try:
                from backend.app.services.analyzer.layout import analyze_pdf
                layout = analyze_pdf(file_path)
            except Exception:  # noqa: BLE001
                layout = None  # 版面分析失败不阻塞导入

        # 3. 文本清洗（去页眉页脚、去重、修复残缺）
        update_progress(record, 0.32, "cleaning", "正在清洗文本...")
        from backend.app.services.analyzer.textclean import clean_text

        header_lines = layout.header_lines if layout else set()
        footer_lines = layout.footer_lines if layout else set()
        cleaned_pages = [
            clean_text(p, header_lines, footer_lines) for p in result.pages
        ]

        # 4. 关键信息提取
        update_progress(record, 0.40, "keyinfo", "正在提取关键信息...")
        from backend.app.services.analyzer.keyinfo import analyze_book_text
        keyinfo = analyze_book_text(cleaned_pages)

        # 5. 章节树（目录书签优先；无目录时启发式提取，再 LLM 兜底）
        update_progress(record, 0.5, "chapters", "正在构建章节树...")
        if not result.toc:
            # 5a. 启发式：扫描「第X章/第X节」标题（零成本）
            update_progress(record, 0.5, "chapters", "未检测到目录，正在启发式提取章节...")
            try:
                from backend.app.services.rag.toc_heuristic import extract_toc_heuristic
                heu_toc = extract_toc_heuristic(cleaned_pages)
                if heu_toc:
                    from backend.app.services.parser import TocItem
                    result.toc = [TocItem(title=t["title"], level=t["level"], page=t["page"])
                                  for t in heu_toc]
            except Exception:  # noqa: BLE001
                pass
        # 注：目录提取不再在导入时自动调用云端 LLM（隐私：教材正文不静默上传）。
        # 启发式未识别出目录时，用户可在「深度分析」中手动触发 LLM 补全。

        chapter_defs = build_chapters(result.toc, result.total_pages)
        id_map: dict[int, int] = {}
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

        chapters = db.scalars(
            select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.order_index)
        ).all()
        chapter_id_by_order = {c.order_index: c.id for c in chapters}
        chapter_pages = build_chapter_pages(chapter_defs, result.total_pages)
        chapter_pages_db = [
            (chapter_id_by_order.get(i), s, e) if i is not None else (None, s, e)
            for i, s, e in chapter_pages
        ]

        # 6. 切片（语义切块：段落边界 + 页码映射；字符窗口为兜底）
        update_progress(record, 0.6, "chunking", "正在语义切片...")
        try:
            from backend.app.services.rag.semantic_chunker import split_semantic_chunks
            chunks = split_semantic_chunks(cleaned_pages, chapter_pages_db)
        except Exception:  # noqa: BLE001
            from backend.app.services.rag.chunker import split_pages_into_chunks
            chunks = split_pages_into_chunks(cleaned_pages, chapter_pages_db)

        # 7. 写 chunks + FTS 索引
        delete_book_index(book.id)
        update_progress(record, 0.75, "indexing", "正在建立全文索引...")
        total = len(chunks)
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
        chunk_rows = db.scalars(
            select(Chunk).where(Chunk.book_id == book.id).order_by(Chunk.chunk_index)
        ).all()
        for i, ch in enumerate(chunk_rows):
            index_chunk(book.id, ch.chapter_id, ch.page_start, ch.id, ch.content)
            if i % 20 == 0:
                update_progress(record, 0.75 + 0.15 * (i + 1) / total, "indexing",
                                f"索引中 {i + 1}/{total}")

        # 7b. 向量化（可选，settings.vector_search 开启时）
        from backend.app.services.rag import vector
        if vector.is_enabled():
            update_progress(record, 0.9, "vectorizing", "正在向量化（首次需下载嵌入模型）...")
            vector.delete_book_vectors(book.id)
            vector.upsert_chunks(book.id, [
                {"id": ch.id, "content": ch.content, "chapter_id": ch.chapter_id,
                 "page_start": ch.page_start, "page_end": ch.page_end}
                for ch in chunk_rows
            ])

        # 8. 写分析结果
        update_progress(record, 0.95, "analysis", "正在保存分析结果...")
        _save_analysis(db, book.id, keyinfo, layout)

        book.status = "ready"
        db.commit()
        # 深度分析会向云端发送章节正文，故不自动触发——由用户在前端明确点击「深度分析」授权
        return {
            "book_id": book.id,
            "chapters": len(chapters),
            "chunks": total,
            "definitions": len(keyinfo["definitions"]),
            "theorems": len(keyinfo["theorems"]),
            "keywords": len(keyinfo["keywords"]),
            "ocr": bool(result.pages and detect_scanned(result.pages)),
        }
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


def _save_analysis(db, book_id: int, keyinfo: dict, layout) -> None:
    """保存智能分析结果到 book_analysis 表（幂等：先删后插）。"""
    old = db.scalar(select(BookAnalysis).where(BookAnalysis.book_id == book_id))
    if old:
        db.delete(old)
        db.flush()

    analysis = BookAnalysis(
        book_id=book_id,
        definitions_json=json.dumps(keyinfo["definitions"], ensure_ascii=False),
        theorems_json=json.dumps(keyinfo["theorems"], ensure_ascii=False),
        keywords_json=json.dumps(keyinfo["keywords"], ensure_ascii=False),
        body_size=layout.body_size if layout else None,
        header_count=len(layout.header_lines) if layout else 0,
        footer_count=len(layout.footer_lines) if layout else 0,
        table_pages=json.dumps(sorted(layout.table_pages)) if layout else None,
    )
    db.add(analysis)
    db.flush()
