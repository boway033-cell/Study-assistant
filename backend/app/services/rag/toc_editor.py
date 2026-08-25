"""目录人工编辑的事务化保存与下游映射更新。"""
from __future__ import annotations

import json

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend.app.models import (
    Book, BookDeep, Chapter, Chunk, KnowledgeNode, Note, PaperProfile, Quiz, TocRevision,
)
from backend.app.services.archive import build_source_map
from backend.app.services.rag.toc_logic import analyze_toc_rows


def chapter_snapshot(chapters: list[Chapter]) -> list[dict]:
    return [{"id": ch.id, "title": ch.title, "level": ch.level, "parent_id": ch.parent_id,
             "order_index": ch.order_index, "start_page": ch.start_page, "end_page": ch.end_page}
            for ch in sorted(chapters, key=lambda row: row.order_index)]


def review_chapters(chapters: list[Chapter]) -> dict:
    ordered = sorted(chapters, key=lambda row: row.order_index)
    index_by_id = {chapter.id: index for index, chapter in enumerate(ordered)}
    rows = [{"id": chapter.id, "title": chapter.title, "level": chapter.level,
             "page": chapter.start_page or 1,
             "parent_index": index_by_id.get(chapter.parent_id)} for chapter in ordered]
    audit = analyze_toc_rows(rows)
    for item in audit["items"]:
        parent_index = item.get("inferred_parent_index")
        item["inferred_parent_id"] = ordered[parent_index].id if parent_index is not None else None
        item["parent_id"] = ordered[item["parent_index"]].id if item.get("parent_index") is not None else None
    return audit


def _validate_items(items: list[dict], total_pages: int) -> None:
    if not items:
        raise ValueError("目录至少保留一项")
    keys = [item["client_key"] for item in items]
    if len(keys) != len(set(keys)):
        raise ValueError("目录项 client_key 重复")
    seen: dict[str, int] = {}
    previous_page = 0
    for index, item in enumerate(items):
        title = str(item.get("title") or "").strip()
        if not title:
            raise ValueError(f"第 {index + 1} 项标题不能为空")
        page = int(item.get("start_page") or 1)
        if page < previous_page:
            raise ValueError(f"第 {index + 1} 项页码小于上一项，请先调整顺序")
        if page < 1 or (total_pages and page > total_pages):
            raise ValueError(f"第 {index + 1} 项页码超出文献范围")
        parent_key = item.get("parent_key")
        if parent_key is not None and parent_key not in seen:
            raise ValueError(f"第 {index + 1} 项的父级必须位于它之前")
        expected_level = seen[parent_key] + 1 if parent_key is not None else 1
        if int(item.get("level") or 1) != expected_level:
            raise ValueError(f"第 {index + 1} 项层级应为 {expected_level}（由父级决定）")
        seen[item["client_key"]] = expected_level
        previous_page = page


def replace_book_toc(db: Session, book: Book, items: list[dict], source: str, note: str | None = None) -> dict:
    """整体保存目录，保留未删除节点 ID，并重建 chunk/source-map 归属。"""
    _validate_items(items, int(book.total_pages or 0))
    existing = list(db.scalars(select(Chapter).where(Chapter.book_id == book.id)
                               .order_by(Chapter.order_index)).all())
    existing_by_id = {chapter.id: chapter for chapter in existing}
    before = chapter_snapshot(existing)
    requested_ids = [int(item["id"]) for item in items if item.get("id") is not None]
    if len(requested_ids) != len(set(requested_ids)):
        raise ValueError("同一目录节点不能重复使用")
    if any(chapter_id not in existing_by_id for chapter_id in requested_ids):
        raise ValueError("目录包含不属于当前文献的节点")

    by_key: dict[str, Chapter] = {}
    for order, item in enumerate(items):
        chapter = existing_by_id.get(item.get("id"))
        if chapter is None:
            chapter = Chapter(book_id=book.id)
            db.add(chapter)
        chapter.title = item["title"].strip()
        chapter.level = int(item["level"])
        chapter.order_index = order
        chapter.start_page = int(item.get("start_page") or 1)
        chapter.parent_id = None
        by_key[item["client_key"]] = chapter
    db.flush()
    for item in items:
        chapter = by_key[item["client_key"]]
        parent_key = item.get("parent_key")
        chapter.parent_id = by_key[parent_key].id if parent_key else None

    ordered = [by_key[item["client_key"]] for item in items]
    total_pages = max(1, int(book.total_pages or 1))
    for index, chapter in enumerate(ordered):
        boundary = next((candidate for candidate in ordered[index + 1:] if candidate.level <= chapter.level), None)
        chapter.end_page = max(chapter.start_page, (boundary.start_page - 1) if boundary else total_pages)

    kept_ids = {chapter.id for chapter in ordered}
    removed = [chapter for chapter in existing if chapter.id not in kept_ids]
    retained_by_order = sorted(ordered, key=lambda chapter: chapter.order_index)
    for chapter in removed:
        replacement = next((candidate.id for candidate in reversed(retained_by_order)
                            if candidate.order_index <= chapter.order_index), None)
        if replacement is None and retained_by_order:
            replacement = retained_by_order[0].id
        for model in (Chunk, Note, Quiz, KnowledgeNode):
            db.execute(update(model).where(model.chapter_id == chapter.id).values(chapter_id=replacement))
    if removed:
        removed_ids = [chapter.id for chapter in removed]
        db.execute(update(Chapter).where(Chapter.parent_id.in_(removed_ids)).values(parent_id=None))
        db.execute(delete(Chapter).where(Chapter.id.in_(removed_ids)))

    # 按页码与目录顺序重新归属 chunk；同页多标题时取当页最后一项。
    chunks = list(db.scalars(select(Chunk).where(Chunk.book_id == book.id).order_by(Chunk.chunk_index)).all())
    for chunk in chunks:
        page = int(chunk.page_start or 1)
        owner = next((chapter for chapter in reversed(ordered) if chapter.start_page <= page), ordered[0])
        chunk.chapter_id = owner.id

    db.execute(delete(BookDeep).where(BookDeep.book_id == book.id))
    db.flush()
    after = chapter_snapshot(ordered)
    revision = TocRevision(book_id=book.id, source=source, note=(note or "")[:255] or None,
                           before_json=json.dumps(before, ensure_ascii=False),
                           after_json=json.dumps(after, ensure_ascii=False))
    db.add(revision)

    profile = db.get(PaperProfile, book.id) or PaperProfile(book_id=book.id)
    profile.source_map_json = build_source_map(book.id, chunks, {chapter.id: chapter.title for chapter in ordered})
    db.add(profile)
    db.commit()
    db.refresh(revision)

    # FTS 的 chapter_id 是冗余定位字段，目录修订后必须同步。
    from backend.app.services.rag import fts
    fts.delete_book_index(book.id)
    for chunk in chunks:
        fts.index_chunk(book.id, chunk.chapter_id, chunk.page_start, chunk.id, chunk.content, chunk.page_end)
    return {"revision_id": revision.id, "chapters": after, "audit": review_chapters(ordered)}
