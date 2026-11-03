"""书籍数据生命周期：删除、来源版本暂存与受控切换（P1-1）。

重解析不再"先删除旧章节/分块再解析"。新解析产物写入 staged_* 暂存表，
只有解析与索引全部成功后才在一个事务里切换到主表；失败/取消/进程中断
只清理暂存版本，旧章节、分块、FTS 索引与引用保持可用。
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from backend.app.models import (
    Annotation,
    Attempt,
    Book,
    BookAnalysis,
    BookDeep,
    Chapter,
    ChatLog,
    Chunk,
    EvidenceCard,
    ImportTask,
    KnowledgeNode,
    KnowledgeNote,
    LiteratureAccessAttempt,
    Note,
    PaperProfile,
    PresentationDeck,
    Quiz,
    RetiredChunk,
    SourceVersion,
    StagedChapter,
    StagedChunk,
    TocRevision,
    book_tags,
)
from backend.app.services.source_identity import (
    chapter_key,
    content_hash,
    new_version_uid,
)

# 用户产物中带 chapter_id 的表；切换版本时按稳定章节身份重映射。
_CHAPTER_REF_MODELS = (Note, Quiz, KnowledgeNode, EvidenceCard, KnowledgeNote)


def _latest_seq(db: Session, book_id: int) -> int:
    seq = db.scalar(
        select(func.max(SourceVersion.seq)).where(SourceVersion.book_id == book_id)
    )
    return int(seq or 0)


def _discard_staged_rows(db: Session, version_id: int) -> None:
    db.execute(delete(StagedChunk).where(StagedChunk.version_id == version_id))
    db.execute(delete(StagedChapter).where(StagedChapter.version_id == version_id))


def start_staging_version(
    db: Session, book_id: int, *, file_hash: str | None = None, task_id: str | None = None
) -> SourceVersion:
    """为一次导入/重解析创建暂存版本。

    会先丢弃同一本书遗留的 staging 版本（进程中断、重复提交、重启恢复），
    这些数据只是可再生解析产物，不涉及用户资料。
    """
    book = db.get(Book, book_id)
    if book is None:
        raise ValueError(f"书籍不存在: {book_id}")
    for stale in db.scalars(
        select(SourceVersion).where(
            SourceVersion.book_id == book_id, SourceVersion.status == "staging"
        )
    ).all():
        _discard_staged_rows(db, stale.id)
        stale.status = "abandoned"
    version = SourceVersion(
        version_uid=new_version_uid(),
        book_id=book_id,
        seq=_latest_seq(db, book_id) + 1,
        status="staging",
        file_hash=file_hash,
        created_by_task=task_id,
        payload_json="{}",
    )
    db.add(version)
    db.flush()
    book.staging_source_version_id = version.id
    db.commit()
    return version


def stage_chapters(db: Session, version: SourceVersion, chapter_defs: list[dict]) -> dict[int, StagedChapter]:
    """把章节树写入暂存表，返回 order_index -> StagedChapter 映射。

    chapter_key 不依赖 order_index，只依赖父章节身份、层级、归一化标题与同名
    出现序号，因此章节重排后仍能匹配旧批注。
    """
    created: dict[int, StagedChapter] = {}
    occurrences: dict[tuple[str | None, int, str], int] = {}
    for ch_def in chapter_defs:
        parent = created.get(ch_def["parent_id"]) if ch_def.get("parent_id") is not None else None
        parent_key = parent.chapter_key if parent is not None else None
        normalized = " ".join((ch_def.get("title") or "").strip().lower().split())
        occ_key = (parent_key, int(ch_def.get("level") or 1), normalized)
        occurrence = occurrences.get(occ_key, 0)
        occurrences[occ_key] = occurrence + 1
        row = StagedChapter(
            version_id=version.id,
            book_id=version.book_id,
            parent_id=parent.id if parent is not None else None,
            title=ch_def["title"],
            level=ch_def["level"],
            order_index=ch_def["order_index"],
            start_page=ch_def.get("start_page"),
            end_page=ch_def.get("end_page"),
            chapter_key=chapter_key(parent_key, ch_def.get("title"), ch_def.get("level"), occurrence),
        )
        db.add(row)
        db.flush()
        created[ch_def["order_index"]] = row
    version.chapter_count = len(created)
    return created


def stage_chunks(db: Session, version: SourceVersion, chunks: list[dict]) -> int:
    for ch_def in chunks:
        db.add(
            StagedChunk(
                version_id=version.id,
                book_id=version.book_id,
                chapter_id=ch_def.get("chapter_id"),
                content=ch_def["content"],
                page_start=ch_def.get("page_start"),
                page_end=ch_def.get("page_end"),
                chunk_index=ch_def["chunk_index"],
                word_count=ch_def.get("word_count"),
                content_hash=content_hash(ch_def["content"]),
            )
        )
    version.chunk_count = len(chunks)
    return len(chunks)


def update_staged_payload(db: Session, version: SourceVersion, **values) -> None:
    """把分析/元数据等派生结果暂存到版本上，激活后才写入主表。"""
    payload = {}
    try:
        payload = json.loads(version.payload_json or "{}")
    except (TypeError, ValueError):
        payload = {}
    payload.update(values)
    version.payload_json = json.dumps(payload, ensure_ascii=False)


def abandon_staged_version(db: Session, book_id: int, version_id: int | None) -> None:
    """解析失败/取消：清理暂存产物，恢复书籍原有可用状态。"""
    if not version_id:
        return
    version = db.get(SourceVersion, version_id)
    if version is not None:
        _discard_staged_rows(db, version.id)
        version.status = "abandoned"
        version.payload_json = "{}"
    book = db.get(Book, book_id)
    if book is not None and book.staging_source_version_id == version_id:
        book.staging_source_version_id = None


def cleanup_stale_staging_versions(db: Session) -> int:
    """启动恢复：清理上次进程中断留下的 staging 版本。

    暂存内容不涉及用户资料，直接丢弃；对应任务由 recover_pending_tasks 重新排队。
    """
    count = 0
    for version in db.scalars(
        select(SourceVersion).where(SourceVersion.status == "staging")
    ).all():
        _discard_staged_rows(db, version.id)
        version.status = "abandoned"
        version.payload_json = "{}"
        book = db.get(Book, version.book_id)
        if book is not None and book.staging_source_version_id == version.id:
            book.staging_source_version_id = None
        count += 1
    if count:
        db.commit()
    return count


def _old_chapter_keys(db: Session, book_id: int) -> tuple[list[Chapter], dict[int, str]]:
    chapters = db.scalars(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_index, Chapter.id)
    ).all()
    by_id = {c.id: c for c in chapters}
    keys: dict[int, str] = {}
    occurrences: dict[tuple[str | None, int, str], int] = {}

    def resolve(chapter: Chapter, depth: int = 0) -> str:
        if chapter.id in keys:
            return keys[chapter.id]
        parent = by_id.get(chapter.parent_id) if chapter.parent_id is not None else None
        parent_key = resolve(parent, depth + 1) if parent is not None and depth < 64 else None
        normalized = " ".join((chapter.title or "").strip().lower().split())
        occ_key = (parent_key, int(chapter.level or 1), normalized)
        occurrence = occurrences.get(occ_key, 0)
        occurrences[occ_key] = occurrence + 1
        key = chapter.chapter_key or chapter_key(parent_key, chapter.title, chapter.level, occurrence)
        keys[chapter.id] = key
        return key

    for chapter in chapters:
        resolve(chapter, 0)
    return chapters, keys


def activate_staged_version(db: Session, book_id: int) -> dict:
    """在调用方事务内把暂存版本切换为主版本。

    调用方负责 commit/rollback；本函数内所有写操作（归档旧分块、重映射用户
    章节引用、替换 chapters/chunks、重建 FTS、更新来源映射）属于同一事务。
    """
    book = db.get(Book, book_id)
    if book is None:
        raise ValueError(f"书籍不存在: {book_id}")
    version = db.get(SourceVersion, book.staging_source_version_id) if book.staging_source_version_id else None
    if version is None or version.status != "staging":
        raise ValueError("没有可激活的暂存解析版本")

    staged_chapters = db.scalars(
        select(StagedChapter)
        .where(StagedChapter.version_id == version.id)
        .order_by(StagedChapter.order_index, StagedChapter.id)
    ).all()
    staged_chunks = db.scalars(
        select(StagedChunk)
        .where(StagedChunk.version_id == version.id)
        .order_by(StagedChunk.chunk_index, StagedChunk.id)
    ).all()
    if not staged_chunks:
        raise ValueError("暂存版本没有分块，拒绝激活")

    old_chapters, old_key_by_id = _old_chapter_keys(db, book_id)
    old_chunks = db.scalars(select(Chunk).where(Chunk.book_id == book_id)).all()
    old_version = db.get(SourceVersion, book.active_source_version_id) if book.active_source_version_id else None

    # 1) 归档旧分块原文：重解析后历史引用仍能找回引用时的正文。
    retired_at = datetime.utcnow()
    retired_rows = []
    for chunk in old_chunks:
        retired_rows.append(
            RetiredChunk(
                book_id=book_id,
                chunk_id=chunk.id,
                source_version_id=chunk.source_version_id or (old_version.id if old_version else None),
                chapter_id=chunk.chapter_id,
                content=chunk.content,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                chunk_index=chunk.chunk_index,
                word_count=chunk.word_count,
                content_hash=chunk.content_hash or content_hash(chunk.content or ""),
                retired_at=retired_at,
            )
        )
    if retired_rows:
        db.add_all(retired_rows)
        db.flush()

    # 2) 写入新章节（parent 需要新 id，逐条 flush 映射）。
    new_chapter_by_key: dict[str, Chapter] = {}
    new_chapter_by_staged_id: dict[int, Chapter] = {}
    for staged in staged_chapters:
        parent = new_chapter_by_staged_id.get(staged.parent_id) if staged.parent_id else None
        chapter = Chapter(
            book_id=book_id,
            parent_id=parent.id if parent is not None else None,
            title=staged.title,
            level=staged.level,
            order_index=staged.order_index,
            start_page=staged.start_page,
            end_page=staged.end_page,
            chapter_key=staged.chapter_key,
            source_version_id=version.id,
        )
        db.add(chapter)
        db.flush()
        new_chapter_by_staged_id[staged.id] = chapter
        if staged.chapter_key:
            new_chapter_by_key.setdefault(staged.chapter_key, chapter)

    # 3) 写入新分块（staged chapter_id -> 新 chapter_id）。
    new_chunks: list[Chunk] = []
    for staged in staged_chunks:
        chapter = new_chapter_by_staged_id.get(staged.chapter_id) if staged.chapter_id else None
        chunk = Chunk(
            book_id=book_id,
            chapter_id=chapter.id if chapter is not None else None,
            content=staged.content,
            page_start=staged.page_start,
            page_end=staged.page_end,
            chunk_index=staged.chunk_index,
            word_count=staged.word_count,
            content_hash=staged.content_hash or content_hash(staged.content),
            source_version_id=version.id,
        )
        db.add(chunk)
        new_chunks.append(chunk)
    db.flush()

    # 4) 用户章节引用按稳定身份重映射；匹配不到才置空（不删除用户内容）。
    old_ids = [c.id for c in old_chapters]
    remapped = 0
    if old_ids:
        for model in _CHAPTER_REF_MODELS:
            rows = db.scalars(
                select(model).where(model.book_id == book_id, model.chapter_id.in_(old_ids))
            ).all()
            for row in rows:
                target = new_chapter_by_key.get(old_key_by_id.get(row.chapter_id, ""))
                if target is not None:
                    row.chapter_id = target.id
                    remapped += 1
                else:
                    row.chapter_id = None

    # 5) 删除旧分块/章节：只删旧 id，绝不波及刚写入的新版本；
    #    先 flush 用户引用重映射，避免 autoflush=False 下旧 FK 仍在库中。
    old_chunk_ids = [c.id for c in old_chunks]
    db.flush()
    if old_ids:
        db.execute(
            update(Chapter).where(Chapter.parent_id.in_(old_ids)).values(parent_id=None)
        )
    if old_chunk_ids:
        db.execute(delete(Chunk).where(Chunk.id.in_(old_chunk_ids)))
    if old_ids:
        db.execute(delete(Chapter).where(Chapter.id.in_(old_ids)))
    db.flush()

    # 6) FTS 索引与主表切换在同一事务：不会出现"旧正文已删、新索引未建"的窗口。
    from backend.app.services.rag.fts import replace_book_index_in_session

    replace_book_index_in_session(db, book_id, new_chunks)

    # 7) 来源映射与派生产物从暂存 payload 应用。
    try:
        payload = json.loads(version.payload_json or "{}")
    except (TypeError, ValueError):
        payload = {}
    profiles = db.get(PaperProfile, book_id) or PaperProfile(book_id=book_id)
    metadata = payload.get("metadata") or {}
    for key in (
        "authors", "journal", "published_year", "doi", "arxiv_id",
        "language", "access_route", "provenance_json",
    ):
        value = metadata.get(key)
        if value is not None and not getattr(profiles, key, None):
            setattr(profiles, key, value)
    from backend.app.services.archive import build_source_map

    profiles.source_map_json = build_source_map(
        book_id, new_chunks, {c.id: c.title for c in new_chapter_by_staged_id.values()}
    )
    db.add(profiles)

    analysis = payload.get("analysis") or {}
    old_analysis = db.scalar(select(BookAnalysis).where(BookAnalysis.book_id == book_id))
    if old_analysis is not None:
        db.delete(old_analysis)
        db.flush()
    db.add(
        BookAnalysis(
            book_id=book_id,
            definitions_json=json.dumps(analysis.get("definitions", []), ensure_ascii=False),
            theorems_json=json.dumps(analysis.get("theorems", []), ensure_ascii=False),
            keywords_json=json.dumps(analysis.get("keywords", []), ensure_ascii=False),
            body_size=analysis.get("body_size"),
            header_count=analysis.get("header_count"),
            footer_count=analysis.get("footer_count"),
            table_pages=json.dumps(analysis.get("table_pages", []), ensure_ascii=False),
        )
    )

    # 8) 书籍状态与版本指针。
    if payload.get("total_pages"):
        book.total_pages = payload["total_pages"]
    if payload.get("category"):
        book.category = payload["category"]
    book.active_source_version_id = version.id
    book.staging_source_version_id = None
    book.status = "ready"
    book.error_msg = None
    # BookDeep / TocRevision 是可再生的分析缓存，重解析后必须重建，
    # 否则会继续展示旧章节摘要或旧目录修订。
    db.execute(delete(BookDeep).where(BookDeep.book_id == book_id))
    db.execute(delete(TocRevision).where(TocRevision.book_id == book_id))

    version.status = "active"
    version.activated_at = datetime.utcnow()
    if old_version is not None and old_version.id != version.id:
        old_version.status = "retired"
        old_version.retired_at = datetime.utcnow()

    _discard_staged_rows(db, version.id)
    db.flush()
    return {
        "version_uid": version.version_uid,
        "chapters": len(new_chapter_by_staged_id),
        "chunks": len(new_chunks),
        "retired_chunks": len(retired_rows),
        "remapped_refs": remapped,
    }


def backfill_legacy_source_versions(db: Session) -> int:
    """升级迁移：为已有书籍补建 legacy 来源版本、回填真实内容指纹与章节身份。

    只回填能证明的事实（当前正文的 sha256、当前章节结构的稳定键），不伪造
    历史指纹；旧引用仍按 legacy_unverified/needs_review 安全降级。
    """
    from backend.app.services.source_identity import PARSER_VERSION

    count = 0
    books = db.scalars(
        select(Book).where(Book.active_source_version_id.is_(None))
    ).all()
    for book in books:
        chunks = db.scalars(select(Chunk).where(Chunk.book_id == book.id)).all()
        if not chunks:
            continue
        chapters = db.scalars(
            select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.order_index, Chapter.id)
        ).all()
        version = SourceVersion(
            version_uid=new_version_uid(),
            book_id=book.id,
            # 旧库若已存在被放弃的暂存版本，仍占用 book_id+seq 唯一键；因此取实际
            # 最大序号 +1，只保证新 legacy 版本可写，不覆盖/重写任何已有版本行。
            seq=_latest_seq(db, book.id) + 1,
            status="legacy",
            file_hash=book.file_hash,
            parser_version=PARSER_VERSION,
            chunk_count=len(chunks),
            chapter_count=len(chapters),
            payload_json="{}",
        )
        db.add(version)
        db.flush()
        book.active_source_version_id = version.id

        keys: dict[int, str] = {}
        occurrences: dict[tuple[str | None, int, str], int] = {}
        for chapter in chapters:
            parent_key = keys.get(chapter.parent_id) if chapter.parent_id is not None else None
            normalized = " ".join((chapter.title or "").strip().lower().split())
            occ_key = (parent_key, int(chapter.level or 1), normalized)
            occurrence = occurrences.get(occ_key, 0)
            occurrences[occ_key] = occurrence + 1
            chapter.chapter_key = (
                chapter.chapter_key
                or chapter_key(parent_key, chapter.title, chapter.level, occurrence)
            )
            chapter.source_version_id = version.id
            keys[chapter.id] = chapter.chapter_key

        for chunk in chunks:
            chunk.source_version_id = version.id
            if not chunk.content_hash:
                chunk.content_hash = content_hash(chunk.content or "")
        count += 1
    if count:
        db.commit()
    return count


def delete_book_records(db: Session, book_id: int) -> list[str]:
    """按外键依赖顺序删除一本书，返回提交后可安全删除的汇报文件名。"""
    deck_files = list(
        db.scalars(
            select(PresentationDeck.file_path).where(
                PresentationDeck.book_id == book_id,
                PresentationDeck.file_path.is_not(None),
            )
        )
    )

    quiz_ids = select(Quiz.id).where(Quiz.book_id == book_id)
    node_ids = select(KnowledgeNode.id).where(KnowledgeNode.book_id == book_id)
    chapter_ids = select(Chapter.id).where(Chapter.book_id == book_id)

    # 跨书引用也必须先解除，否则删除目标知识节点会被外键阻止。
    db.execute(
        update(Annotation)
        .where(Annotation.knowledge_node_id.in_(node_ids))
        .values(knowledge_node_id=None)
    )
    db.execute(
        update(KnowledgeNode)
        .where(KnowledgeNode.ref_node_id.in_(node_ids))
        .values(ref_node_id=None)
    )
    db.execute(
        update(KnowledgeNode)
        .where(KnowledgeNode.parent_id.in_(node_ids))
        .values(parent_id=None)
    )
    db.execute(
        update(LiteratureAccessAttempt)
        .where(LiteratureAccessAttempt.book_id == book_id)
        .values(book_id=None)
    )
    db.execute(
        update(Chapter).where(Chapter.parent_id.in_(chapter_ids)).values(parent_id=None)
    )

    db.execute(delete(Attempt).where(Attempt.quiz_id.in_(quiz_ids)))
    for model in (
        ImportTask,
        PresentationDeck,
        Annotation,
        BookDeep,
        BookAnalysis,
        ChatLog,
        Note,
        PaperProfile,
        KnowledgeNode,
        Chunk,
        Quiz,
        TocRevision,
        Chapter,
    ):
        db.execute(delete(model).where(model.book_id == book_id))
    db.execute(delete(RetiredChunk).where(RetiredChunk.book_id == book_id))
    db.execute(delete(SourceVersion).where(SourceVersion.book_id == book_id))
    db.execute(delete(book_tags).where(book_tags.c.book_id == book_id))
    db.execute(delete(Book).where(Book.id == book_id))
    return [path for path in deck_files if path]
