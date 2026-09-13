"""资料导入任务：上传文件 → 解析 → 版面分析 → 文本清洗 → 关键信息 → 切片 → FTS 索引

流水线（docs/01-architecture.md §4.1 增强版）：
  1. 解析（PDF/DOCX/PPTX），扫描版自动触发 OCR
  2. 版面分析：识别标题/正文/页眉页脚/表格/公式
  3. 文本清洗：去页眉页脚重复、去重、修复断行/重复字符
  4. 关键信息提取：定义句、定理、关键词 → book_analysis 表
  5. 切片 + FTS 索引（索引清洗后的正文）
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.database import SessionLocal
from backend.app.models import Book, BookAnalysis, Chapter, Chunk, PaperProfile
from backend.app.services.rag.chunker import build_chapter_pages, build_chapters, split_pages_into_chunks
from backend.app.services.rag.fts import delete_book_index, index_chunk
from backend.app.services.parser import ParseError, parse_document
from backend.app.services.parser.ocr import (
    detect_scanned,
    has_ocr_engine,
    ocr_pdf,
    pages_requiring_ocr,
    schedule_ocr_engine_release,
)
from backend.app.worker.tasks import TaskCancelled, TaskRecord, update_progress

logger = logging.getLogger(__name__)


def save_upload(file_name: str, content: bytes) -> tuple[Path, str]:
    """保存上传文件到 data/uploads，返回 (路径, SHA-256 哈希)。"""
    import hashlib
    safe_name = Path(file_name).name  # 防路径穿越
    dest = settings.uploads_dir / safe_name
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        i = 1
        while dest.exists():
            dest = settings.uploads_dir / f"{stem}_{i}{suffix}"
            i += 1
    dest.write_bytes(content)
    file_hash = hashlib.sha256(content).hexdigest()
    return dest, file_hash


class _OcrProgressReporter:
    """OCR 页级进度上报：计数、ETA 与进度写入必须在同一把锁内完成。

    第二阶段起 `on_progress` 可能来自多个 OCR worker 线程（生产者的缓存命中页
    也会上报），若"锁内计数、锁外写进度"，先取到序号的回调可能后写入，
    造成 UI 进度倒退。这里把三件事原子化，并把写入值以 `record.progress`
    为下限：与 checkpoint 回调（写回当前进度）并发时同样不会回退。
    """

    def __init__(self, record: TaskRecord, weak_total: int) -> None:
        self._record = record
        self._weak_total = max(1, int(weak_total))
        self._started_at = time.monotonic()
        self._lock = threading.Lock()
        self._completed = 0
        self._fresh_completed = 0

    @property
    def completed(self) -> int:
        with self._lock:
            return self._completed

    def progress(self, page_no: int, total: int, cached: bool) -> None:
        """一页 OCR 结束（含命中缓存与空白页），进度 0.15 → 0.30。"""
        with self._lock:
            self._completed += 1
            if not cached:
                self._fresh_completed += 1
            done, fresh = self._completed, self._fresh_completed
            frac = 0.15 + 0.15 * (done / self._weak_total)
            tag = "（命中缓存）" if cached else ""
            eta = ""
            if fresh:
                per_page = (time.monotonic() - self._started_at) / fresh
                remaining_seconds = int(per_page * max(0, self._weak_total - done))
                if remaining_seconds >= 60:
                    eta = f" · 预计剩余约 {max(1, round(remaining_seconds / 60))} 分钟"
            target = max(self._record.progress, min(frac, 0.30))
            update_progress(
                self._record, target, "ocr",
                f"OCR {done}/{self._weak_total} · PDF 第 {page_no} 页{tag}{eta}",
            )

    def checkpoint(self, page_no: int, phase: str) -> None:
        """页内检查点：同时是协作式取消检查（update_progress 在此抛 TaskCancelled）。"""
        with self._lock:
            label = "检查页缓存" if phase == "cache" else "识别中"
            update_progress(
                self._record, self._record.progress, "ocr",
                f"OCR 第 {page_no} 页：{label}"
                f"（单页 {settings.ocr_page_timeout_seconds} 秒无结果将停止）",
            )


async def run_import(record: TaskRecord, book_id: int) -> dict:
    """执行完整导入流水线。book 需已创建且 status=pending。"""
    db = SessionLocal()
    ocr_used = False
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
        if book.file_type == "pdf" and result.pages:
            weak_pages = set(pages_requiring_ocr(
                result.pages, threshold=settings.ocr_page_threshold
            ))
            whole_document_scanned = detect_scanned(result.pages)
            # 混合 PDF 只补识别弱文本页；纯扫描件没有 OCR 时才阻断导入。
            if whole_document_scanned and not has_ocr_engine():
                # 无 OCR 引擎：标记 needs_ocr（非 failed），保留原文件供阅读器直接查看
                book.status = "needs_ocr"
                book.error_msg = (
                    "该 PDF 为扫描版（无文本层），当前未安装 OCR 引擎。"
                    "原文件已保留，可用内置阅读器直接查看；"
                    "如需检索/问答，请安装 OCR 引擎后重新解析。"
                )
                db.commit()
                return {"book_id": book.id, "status": "needs_ocr",
                        "message": "扫描版 PDF，需安装 OCR 引擎"}
            if weak_pages and has_ocr_engine():
                ocr_used = True
                update_progress(record, 0.15, "ocr", "检测到弱文本页，正在按页 OCR...")
                # OCR 逐页回调：页级进度细化（0.15 → 0.30），支持断点续跑。
                # 并发回调的计数与写入在 reporter 内原子完成，进度不会倒退。
                weak_total = len(weak_pages)
                ocr_reporter = _OcrProgressReporter(record, weak_total)
                ocr_summary: dict | None = None

                def _ocr_metrics(summary: dict) -> None:
                    """接收 OCR 结构化性能摘要（只有数值，正文/路径一律不落日志）。"""
                    nonlocal ocr_summary
                    ocr_summary = summary
                    logger.info(
                        "OCR 性能摘要 pages=%s seconds=%s avg_seconds_per_page=%s pages_per_minute=%s",
                        summary.get("pages"), summary.get("seconds"),
                        summary.get("avg_seconds_per_page"), summary.get("pages_per_minute"),
                    )

                try:
                    result.pages = await asyncio.to_thread(
                        ocr_pdf,
                        file_path,
                        on_progress=ocr_reporter.progress,
                        page_numbers=weak_pages,
                        base_pages=result.pages,
                        on_checkpoint=ocr_reporter.checkpoint,
                        page_timeout_seconds=settings.ocr_page_timeout_seconds,
                        on_metrics=_ocr_metrics,
                    )
                finally:
                    # 第一阶段：不再每份文档卸载一次模型，空闲 OCR_ENGINE_IDLE_SECONDS 后释放。
                    schedule_ocr_engine_release()
                if ocr_summary:
                    pages_summary = ocr_summary.get("pages", {})
                    failed_note = ""
                    if pages_summary.get("failed"):
                        failed_note = f"，失败 {pages_summary['failed']} 页（不写缓存，重跑会自动补识别）"
                    update_progress(
                        record, record.progress, "ocr",
                        f"OCR 完成 {ocr_reporter.completed}/{weak_total} 页 · 平均 "
                        f"{ocr_summary.get('avg_seconds_per_page', 0)} 秒/页"
                        f"（命中缓存 {pages_summary.get('cached', 0)} 页，"
                        f"空白跳过 {pages_summary.get('blank', 0)} 页{failed_note}）",
                    )
                result.total_pages = len(result.pages)
                if result.structured is not None:
                    from backend.app.services.parser.structured import replace_pages_with_ocr
                    from backend.app.services.parser.ocr import _file_hash, _ocr_cache_dir
                    result.structured = replace_pages_with_ocr(
                        result.structured, result.pages, weak_pages,
                        layout_cache_dir=_ocr_cache_dir(_file_hash(file_path)),
                    )

        if not result.pages or all(not p.strip() for p in result.pages):
            raise ParseError("未能从文档中提取到文本（可能是扫描版 PDF）")

        book.total_pages = result.total_pages
        db.commit()

        # 2. 版面分析（仅 PDF）
        layout = None
        if book.file_type == "pdf":
            update_progress(record, 0.25, "layout", "正在分析版面结构...")
            try:
                from backend.app.services.analyzer.layout import analyze_pdf, analyze_structured
                if settings.layout_backend in {"ppstructure", "pp-doclayout-m"}:
                    from backend.app.services.analyzer.pp_doclayout import (
                        LayoutEnhancementUnavailable,
                        apply_layout_roles,
                        run_pp_doclayout,
                    )
                    if result.structured is not None:
                        update_progress(record, 0.25, "layout", "正在按需启动 PP-DocLayout-M...")
                        try:
                            payloads = run_pp_doclayout(
                                file_path,
                                settings.structured_dir / "pp-layout" / (book.file_hash or str(book.id)),
                            )
                            result.structured = apply_layout_roles(result.structured, payloads)
                        except LayoutEnhancementUnavailable as exc:
                            update_progress(
                                record, 0.25, "layout",
                                f"版面增强暂不可用，已回退轻量分析：{exc}",
                            )
                layout = (
                    analyze_structured(result.structured)
                    if result.structured is not None else analyze_pdf(file_path)
                )
            except Exception:  # noqa: BLE001
                layout = None  # 版面分析失败不阻塞导入

        # 结构证据按文件哈希原子保存；Markdown、目录和后续修复均可重复派生。
        if result.structured is not None:
            try:
                structured_path = settings.structured_dir / f"{book.file_hash or book.id}.json"
                result.structured.save_json(structured_path)
            except OSError:
                pass
            finally:
                # 版面结果和证据 JSON 已落盘，后续阶段不再常驻完整坐标树。
                result.structured = None

        # 3. 文本清洗（去页眉页脚、去重、修复残缺）
        update_progress(record, 0.32, "cleaning", "正在清洗文本...")
        from backend.app.services.analyzer.textclean import clean_text

        header_lines = layout.header_lines if layout else set()
        footer_lines = layout.footer_lines if layout else set()
        cleaned_pages = [
            clean_text(p, header_lines, footer_lines) for p in result.pages
        ]
        result.pages = []  # 清洗完成后释放重复的原始页文本副本。

        # 3b. 文献归档元数据（仅本地文件/前几页，不联网）
        try:
            from backend.app.services.archive import extract_metadata
            meta = extract_metadata(file_path, cleaned_pages)
            profile = db.get(PaperProfile, book.id) or PaperProfile(book_id=book.id)
            for key in ("authors", "journal", "published_year", "doi", "arxiv_id",
                        "language", "access_route", "provenance_json"):
                value = meta.get(key)
                if value is not None and not getattr(profile, key, None):
                    setattr(profile, key, value)
            db.add(profile)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

        # 4. 关键信息提取
        update_progress(record, 0.40, "keyinfo", "正在提取关键信息...")
        from backend.app.services.analyzer.keyinfo import analyze_book_text
        keyinfo = analyze_book_text(cleaned_pages)

        # 5. 章节树：PDF 融合书签/全文编号/版面标题；Office 只保留原生结构。
        # DOCX/PPTX 的“页”是标题/幻灯片合成单元，套用 PDF 全文规则会把正文再次识别为目录。
        update_progress(record, 0.5, "chapters", "正在构建章节树...")
        try:
            from backend.app.services.parser import TocItem
            from backend.app.services.rag.toc_import import select_import_toc
            selected_toc = select_import_toc(book.file_type, result.toc, cleaned_pages, layout)
            result.toc = [
                TocItem(title=t["title"], level=t["level"], page=t["page"])
                for t in selected_toc
            ]
        except Exception:  # noqa: BLE001
            # 任一增强来源失败时仍保留原始书签，导入不被阻塞。
            pass
        # 注：目录提取不再在导入时自动调用云端 LLM（隐私：教材正文不静默上传）。
        # 启发式+版面未识别出目录时，用户可在「深度分析」中手动触发 LLM 补全。

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
            index_chunk(book.id, ch.chapter_id, ch.page_start, ch.id, ch.content, ch.page_end)
            if i % 20 == 0:
                update_progress(record, 0.75 + 0.15 * (i + 1) / total, "indexing",
                                f"索引中 {i + 1}/{total}")

        # 每个文本块的稳定来源映射，供精读、引用核验和阅读卡复用。
        try:
            from backend.app.services.archive import build_source_map
            profile = db.get(PaperProfile, book.id) or PaperProfile(book_id=book.id)
            profile.source_map_json = build_source_map(
                book.id, chunk_rows, {c.id: c.title for c in chapters}
            )
            db.add(profile)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

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

        # 8b. 自动分类（始终用本地分类，不静默调用云端 AI）
        try:
            from backend.app.services.analyzer.classify import classify_local
            chapters_for_class = [c.title for c in db.scalars(
                select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.order_index).limit(40)
            ).all()]
            # 导入时只用本地分类（隐私：不静默上传书名/关键词到云端）
            # 用户可在资料库手动点「自动分类」按钮触发 AI 分类
            book.category = classify_local(book.title, keyinfo["keywords"], chapters_for_class)
        except Exception:  # noqa: BLE001
            pass  # 分类失败不阻塞导入

        # 8c. 自动标签（关键词 + 文件类型 + 分类）
        try:
            from backend.app.api.tags import auto_generate_tags
            auto_generate_tags(db, book.id)
        except Exception:  # noqa: BLE001
            pass

        # 8d. 相似去重检测（基于正文前 2000 字的 SimHash）
        try:
            _detect_duplicates(db, book.id, cleaned_pages)
        except Exception:  # noqa: BLE001
            pass

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
            "ocr": ocr_used,
        }
    except TaskCancelled:
        # 用户主动取消 ≠ 解析失败。此前会被下面的通用分支捕获成 failed，
        # 导致资料库显示"解析失败"而任务中心显示"已取消"，两者自相矛盾。
        db.rollback()
        book = db.get(Book, book_id)
        if book is not None:
            book.status = "pending"
            book.error_msg = None
            db.commit()
        raise
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


def _simhash(text: str, hash_bits: int = 64) -> int:
    """简易 SimHash：对文本分词后加权哈希，生成指定位数的指纹。

    注意：词哈希必须用稳定的摘要算法（这里用 BLAKE2b），不能用内置 hash()。
    内置 hash 受 PYTHONHASHSEED 随机化影响，重启后同一份文本会算出不同指纹，
    去重结果无法跨进程复现。
    """
    import hashlib

    from backend.app.services.rag.chunker import _get_jieba
    jb = _get_jieba()
    words = [w.strip() for w in jb.cut(text) if w.strip() and len(w) >= 2]
    if not words:
        return 0
    digest_size = max(1, hash_bits // 8)
    v = [0] * hash_bits
    for w in words:
        h = int.from_bytes(hashlib.blake2b(w.encode("utf-8"), digest_size=digest_size).digest(), "big")
        h &= (1 << hash_bits) - 1
        for i in range(hash_bits):
            if h & (1 << i):
                v[i] += 1
            else:
                v[i] -= 1
    fingerprint = 0
    for i in range(hash_bits):
        if v[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint


def _hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _detect_duplicates(db, book_id: int, pages: list[str]) -> None:
    """检测与已有书籍的相似度，标记 duplicate_of。"""
    from backend.app.models import Book
    # 取正文前 2000 字
    full_text = " ".join(pages)[:2000]
    if not full_text.strip():
        return
    new_hash = _simhash(full_text)
    # 与其他 ready 书籍比对
    others = db.scalars(select(Book).where(Book.status == "ready", Book.id != book_id)).all()
    other_ids = [other.id for other in others]
    if other_ids:
        from backend.app.models import Chunk
        # 一次批量查询前 20 块（chunk_index < 20 等价于按 chunk_index 排序取前 20），
        # 在 Python 内按 book_id 分组后再算 SimHash，消除逐本查询的 N+1。
        chunks = db.scalars(
            select(Chunk).where(Chunk.book_id.in_(other_ids), Chunk.chunk_index < 20)
            .order_by(Chunk.book_id, Chunk.chunk_index)
        ).all()
        chunks_by_book: dict[int, list] = {}
        for ch in chunks:
            chunks_by_book.setdefault(ch.book_id, []).append(ch)
        for other in others:
            book_chunks = chunks_by_book.get(other.id, [])
            other_text = " ".join(c.content[:200] for c in book_chunks)[:2000]
            if not other_text.strip():
                continue
            other_hash = _simhash(other_text)
            dist = _hamming_distance(new_hash, other_hash)
            # 64 位 Hamming 距离 <= 10 视为高度相似（约 84%+ 相似度）
            if dist <= 10:
                book = db.get(Book, book_id)
                if book:
                    book.duplicate_of = other.id
                return
