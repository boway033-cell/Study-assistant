"""书籍/资料 API（docs/03-api.md §1）"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, Chapter, Chunk, Note, PaperProfile, Quiz, TocRevision, shelf_books
from backend.app.schemas import (
    BookDetailResp,
    BookLibraryStats,
    BookListItem,
    BookListResp,
    BookRenameReq,
    ChapterNode,
    NoteCreateReq,
    NoteResp,
    NoteUpdateReq,
    PaperProfileResp,
    PaperProfileUpdateReq,
    SearchResultItem,
    SearchResp,
    TaskResp,
)
from backend.app.services.rag import fts
from backend.app.worker.import_task import run_import
from backend.app.worker.tasks import cancel_task, get_task, retry_task, submit

router = APIRouter(prefix="/api", tags=["books"])


class TocEditItem(BaseModel):
    client_key: str = Field(min_length=1, max_length=80)
    id: int | None = None
    parent_key: str | None = Field(default=None, max_length=80)
    title: str = Field(min_length=1, max_length=255)
    level: int = Field(ge=1, le=4)
    start_page: int = Field(ge=1)


class TocReplaceReq(BaseModel):
    items: list[TocEditItem] = Field(min_length=1, max_length=1000)
    note: str | None = Field(default=None, max_length=255)


class TocRepairReq(BaseModel):
    apply: bool = False


class HealthRepairReq(BaseModel):
    issue_ids: list[str] = Field(min_length=1, max_length=100)


class BookOrderReq(BaseModel):
    book_ids: list[int] = Field(min_length=2, max_length=100)
    shelf_id: int | None = None


def _build_chapter_tree(chapters: list[Chapter]) -> list[ChapterNode]:
    nodes = {
        c.id: ChapterNode(
            id=c.id, title=c.title, level=c.level, order_index=c.order_index,
            start_page=c.start_page, end_page=c.end_page, children=[],
        )
        for c in chapters
    }
    roots: list[ChapterNode] = []
    for c in chapters:
        node = nodes[c.id]
        if c.parent_id and c.parent_id in nodes:
            nodes[c.parent_id].children.append(node)
        else:
            roots.append(node)
    return roots


@router.get("/books", response_model=BookListResp)
def list_books(
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=120),
    ids: list[int] | None = Query(default=None),
    category: str | None = Query(default=None, max_length=50),
    reading_status: str | None = Query(default=None, pattern="^(unread|reading|read)$"),
    favorite: bool | None = Query(default=None),
    unfiled: bool = Query(default=False),
    statuses: list[str] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    shelf_id: int | None = Query(default=None),
    sort_by: str = Query(
        default="custom",
        pattern="^(custom|newest|oldest|title_asc|title_desc|author_asc|year_desc|year_asc|last_read|progress_desc)$",
    ),
    db: Session = Depends(get_db),
):
    global_total = db.scalar(select(func.count(Book.id))) or 0
    reading_total = db.scalar(select(func.count(PaperProfile.book_id)).where(
        PaperProfile.reading_status == "reading"
    )) or 0
    favorite_total = db.scalar(select(func.count(PaperProfile.book_id)).where(
        PaperProfile.favorite == 1
    )) or 0
    attention_total = db.scalar(select(func.count(Book.id)).where(
        Book.status.in_({"failed", "needs_ocr", "parsing"})
    )) or 0
    unfiled_total = db.scalar(select(func.count(Book.id)).where(
        Book.id.not_in(select(shelf_books.c.book_id))
    )) or 0
    library_stats = BookLibraryStats(
        total=global_total,
        reading=reading_total,
        favorite=favorite_total,
        attention=attention_total,
        unfiled=unfiled_total,
    )
    stmt = select(Book)
    if status:
        stmt = stmt.where(Book.status == status)
    if statuses:
        stmt = stmt.where(Book.status.in_(set(statuses)))
    if category:
        stmt = stmt.where(Book.category == category)
    if ids:
        stmt = stmt.where(Book.id.in_(set(ids)))
    if q and q.strip():
        keyword = f"%{q.strip()}%"
        profile_books = select(PaperProfile.book_id).where(or_(
            PaperProfile.authors.ilike(keyword),
            PaperProfile.journal.ilike(keyword),
            PaperProfile.doi.ilike(keyword),
        ))
        stmt = stmt.where(or_(Book.title.ilike(keyword), Book.id.in_(profile_books)))
    if reading_status:
        stmt = stmt.where(Book.id.in_(select(PaperProfile.book_id).where(PaperProfile.reading_status == reading_status)))
    if favorite is not None:
        stmt = stmt.where(Book.id.in_(select(PaperProfile.book_id).where(PaperProfile.favorite == int(favorite))))
    if unfiled:
        stmt = stmt.where(Book.id.not_in(select(shelf_books.c.book_id)))
    if shelf_id is not None:
        stmt = stmt.join(shelf_books, shelf_books.c.book_id == Book.id).where(shelf_books.c.shelf_id == shelf_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if sort_by in {"author_asc", "year_desc", "year_asc", "last_read", "progress_desc"}:
        stmt = stmt.outerjoin(PaperProfile, PaperProfile.book_id == Book.id)
    orderings = {
        "newest": (Book.created_at.desc(), Book.id.desc()),
        "oldest": (Book.created_at.asc(), Book.id.asc()),
        "title_asc": (func.lower(Book.title).asc(), Book.id.asc()),
        "title_desc": (func.lower(Book.title).desc(), Book.id.desc()),
        "author_asc": (func.lower(func.coalesce(PaperProfile.authors, "")).asc(), func.lower(Book.title).asc()),
        "year_desc": (func.coalesce(PaperProfile.published_year, -1).desc(), func.lower(Book.title).asc()),
        "year_asc": (func.coalesce(PaperProfile.published_year, 9999).asc(), func.lower(Book.title).asc()),
        "last_read": (PaperProfile.last_read_at.desc(), Book.created_at.desc()),
        "progress_desc": (func.coalesce(PaperProfile.progress_page, 0).desc(), func.lower(Book.title).asc()),
    }
    if sort_by == "custom":
        order_by = ((shelf_books.c.order_index.asc(), Book.id.asc()) if shelf_id is not None
                    else (Book.library_order.asc(), Book.created_at.desc(), Book.id.desc()))
    else:
        order_by = orderings[sort_by]
    books = db.scalars(stmt.order_by(*order_by).offset((page - 1) * page_size).limit(page_size)).all()
    book_ids = [book.id for book in books]

    # 只统计当前页，避免资料增长后每次列表请求扫描全库。
    quiz_counts = dict(db.execute(
        select(Quiz.book_id, func.count()).where(Quiz.book_id.in_(book_ids)).group_by(Quiz.book_id)
    ).all()) if book_ids else {}
    chapter_counts = dict(db.execute(
        select(Chapter.book_id, func.count()).where(Chapter.book_id.in_(book_ids)).group_by(Chapter.book_id)
    ).all()) if book_ids else {}

    from backend.app.models import BookDeep, ImportTask
    deep_statuses = dict(db.execute(
        select(BookDeep.book_id, BookDeep.status)
        .where(BookDeep.book_id.in_(book_ids), BookDeep.status != "none")
    ).all()) if book_ids else {}
    # 进行中任务消息（导入/OCR 进度等）
    task_msgs = dict(db.execute(
        select(ImportTask.book_id, ImportTask.message)
        .where(ImportTask.book_id.in_(book_ids), ImportTask.status.in_(["pending", "running", "cancelling"]))
    ).all()) if book_ids else {}
    profiles = {
        p.book_id: p for p in db.scalars(
            select(PaperProfile).where(PaperProfile.book_id.in_(book_ids))
        ).all()
    } if books else {}
    shelf_map: dict[int, list[int]] = {b.id: [] for b in books}
    if books:
        for book_id, assigned_shelf_id in db.execute(
            select(shelf_books.c.book_id, shelf_books.c.shelf_id)
            .where(shelf_books.c.book_id.in_(book_ids))
        ).all():
            shelf_map.setdefault(book_id, []).append(assigned_shelf_id)
    items = [
        BookListItem(
            id=b.id, title=b.title, file_type=b.file_type, status=b.status,
            total_pages=b.total_pages, chapter_count=chapter_counts.get(b.id, 0),
            quiz_count=quiz_counts.get(b.id, 0), category=b.category,
            deep_status=deep_statuses.get(b.id, "none"),
            task_message=task_msgs.get(b.id),
            authors=profiles[b.id].authors if b.id in profiles else None,
            journal=profiles[b.id].journal if b.id in profiles else None,
            published_year=profiles[b.id].published_year if b.id in profiles else None,
            doi=profiles[b.id].doi if b.id in profiles else None,
            publication_status=profiles[b.id].publication_status if b.id in profiles else "unknown",
            visibility=profiles[b.id].visibility if b.id in profiles else "private",
            demo_allowed=bool(profiles[b.id].demo_allowed) if b.id in profiles else False,
            metadata_confidence=profiles[b.id].metadata_confidence if b.id in profiles else 0.0,
            reading_status=profiles[b.id].reading_status if b.id in profiles else "unread",
            favorite=bool(profiles[b.id].favorite) if b.id in profiles else False,
            progress_page=profiles[b.id].progress_page if b.id in profiles else 1,
            library_order=b.library_order,
            shelf_ids=shelf_map.get(b.id, []),
            created_at=b.created_at,
        )
        for b in books
    ]
    return BookListResp(total=total, items=items, stats=library_stats)


@router.put("/books/order")
def reorder_books(req: BookOrderReq, db: Session = Depends(get_db)):
    """重排全库或某个书架中的可见子集，并归一化整个范围的顺序。"""
    requested = list(req.book_ids)
    if len(requested) != len(set(requested)):
        raise HTTPException(400, "排序列表中不能包含重复文献")

    if req.shelf_id is not None:
        from backend.app.models import Shelf

        if not db.get(Shelf, req.shelf_id):
            raise HTTPException(404, "书架不存在")
        current = list(db.scalars(
            select(shelf_books.c.book_id)
            .where(shelf_books.c.shelf_id == req.shelf_id)
            .order_by(shelf_books.c.order_index.asc(), shelf_books.c.book_id.asc())
        ).all())
    else:
        current = list(db.scalars(
            select(Book.id).order_by(Book.library_order.asc(), Book.created_at.desc(), Book.id.desc())
        ).all())

    requested_set = set(requested)
    if not requested_set.issubset(set(current)):
        raise HTTPException(400, "部分文献不属于当前排序范围")
    positions = [index for index, book_id in enumerate(current) if book_id in requested_set]
    reordered = list(current)
    for position, book_id in zip(positions, requested, strict=True):
        reordered[position] = book_id

    if req.shelf_id is not None:
        for index, book_id in enumerate(reordered):
            db.execute(
                update(shelf_books)
                .where(shelf_books.c.shelf_id == req.shelf_id, shelf_books.c.book_id == book_id)
                .values(order_index=index)
            )
    else:
        for index, book_id in enumerate(reordered):
            db.execute(update(Book).where(Book.id == book_id).values(library_order=index))
    db.commit()
    return {"book_ids": requested, "shelf_id": req.shelf_id, "updated": len(requested)}


@router.get("/books/health")
def knowledge_base_health(db: Session = Depends(get_db)):
    """Explainable, read-only health audit for the personal knowledge base."""
    from datetime import datetime, timezone
    from backend.app.models import Annotation, EvidenceCard, KnowledgeNote

    books = list(db.scalars(select(Book).order_by(Book.created_at.desc())).all())
    if not books:
        return {"score": 100, "book_count": 0, "healthy_book_count": 0, "issue_count": 0,
                "categories": {}, "items": [], "checked_at": datetime.now(timezone.utc).isoformat()}
    book_ids = [book.id for book in books]
    chapter_counts = dict(db.execute(select(Chapter.book_id, func.count(Chapter.id))
                                     .where(Chapter.book_id.in_(book_ids)).group_by(Chapter.book_id)).all())
    chunk_stats = {book_id: {"chunks": int(count or 0), "chars": int(chars or 0), "replacement": int(replacement or 0)}
                   for book_id, count, chars, replacement in db.execute(
        select(Chunk.book_id, func.count(Chunk.id), func.sum(func.length(Chunk.content)),
               func.sum(func.length(Chunk.content) - func.length(func.replace(Chunk.content, "�", ""))))
        .where(Chunk.book_id.in_(book_ids)).group_by(Chunk.book_id)).all()}
    profiles = {item.book_id: item for item in db.scalars(
        select(PaperProfile).where(PaperProfile.book_id.in_(book_ids))).all()}
    duplicate_hashes = {value for value, count in db.execute(
        select(Book.file_hash, func.count(Book.id)).where(Book.file_hash.is_not(None))
        .group_by(Book.file_hash).having(func.count(Book.id) > 1)).all() if value}
    valid_books, totals = set(book_ids), {book.id: int(book.total_pages or 0) for book in books}
    chapter_books = dict(db.execute(select(Chapter.id, Chapter.book_id)).all())
    chunk_books = dict(db.execute(select(Chunk.id, Chunk.book_id)).all())
    broken_anchors = {book.id: 0 for book in books}
    broken_objects: dict[int, list[dict]] = {book.id: [] for book in books}
    anchor_pattern = re.compile(r"^B(\d+)(?::CH(\d+))?(?::P(\d+)(?:-(\d+))?)?(?::C(\d+))?$")

    def check_ref(raw, fallback_book_id: int, source: dict) -> None:
        value = str(raw.get("ref") if isinstance(raw, dict) else raw or "").strip().strip("[]")
        match = anchor_pattern.match(value)
        if not match:
            broken_anchors[fallback_book_id] = broken_anchors.get(fallback_book_id, 0) + 1
            broken_objects.setdefault(fallback_book_id, []).append({**source, "ref": value, "reason": "来源锚点格式无效"})
            return
        ref_book, chapter_id, page_start, page_end, chunk_id = (int(item) if item else None for item in match.groups())
        invalid = (ref_book not in valid_books
                   or (chapter_id is not None and chapter_books.get(chapter_id) != ref_book)
                   or (chunk_id is not None and chunk_books.get(chunk_id) != ref_book)
                   or (page_start is not None and (page_start < 1 or (totals.get(ref_book, 0) and page_start > totals[ref_book])))
                   or (page_end is not None and totals.get(ref_book, 0) and page_end > totals[ref_book]))
        if invalid:
            broken_anchors[fallback_book_id] = broken_anchors.get(fallback_book_id, 0) + 1
            broken_objects.setdefault(fallback_book_id, []).append({**source, "ref": value, "reason": "来源对象已不存在或页码越界"})

    for note in db.scalars(select(KnowledgeNote)).all():
        try: refs = json.loads(note.source_refs_json or "[]")
        except (TypeError, ValueError): refs = ["invalid-json"]
        for ref in refs: check_ref(ref, note.book_id, {"type": "note", "id": note.id, "title": note.title,
                                                       "action_path": f"/notes?noteId={note.id}"})
    for card in db.scalars(select(EvidenceCard)).all():
        try:
            payload = json.loads(card.source_ref_json or "{}")
            refs = payload.get("refs", []) if isinstance(payload, dict) else []
        except (TypeError, ValueError): refs = ["invalid-json"]
        for ref in refs: check_ref(ref, card.book_id, {"type": "evidence", "id": card.id, "title": card.title,
                                                       "action_path": f"/knowledge-hub?record=evidence:{card.id}"})
    book_map = {book.id: book for book in books}
    for annotation in db.scalars(select(Annotation)).all():
        invalid = annotation.status != "active" or annotation.page < 0
        total = totals.get(annotation.book_id, 0)
        if total and annotation.page > total:
            invalid = True
        if annotation.anchor_json:
            try:
                anchor = json.loads(annotation.anchor_json)
                fingerprint = anchor.get("document_fingerprint") if isinstance(anchor, dict) else None
                book = book_map.get(annotation.book_id)
                if fingerprint and book and book.file_hash and fingerprint != book.file_hash:
                    invalid = True
            except (TypeError, ValueError):
                invalid = True
        if invalid:
            broken_anchors[annotation.book_id] = broken_anchors.get(annotation.book_id, 0) + 1
            broken_objects.setdefault(annotation.book_id, []).append({
                "type": "annotation", "id": annotation.id, "title": (annotation.text or "批注")[:80],
                "page": annotation.page, "reason": "批注位置或文档指纹失效",
                "action_path": f"/reader/{annotation.book_id}?page={max(annotation.page, 1)}",
            })

    category_meta = {
        "unparsed": ("未解析", "danger", "重新解析"),
        "low_quality_ocr": ("疑似低质量 OCR", "warning", "重新解析并核对 OCR"),
        "missing_toc": ("无目录", "warning", "打开阅读器修复目录"),
        "missing_metadata": ("无元数据", "info", "补全文献档案"),
        "duplicate": ("重复资料", "warning", "核对重复项"),
        "broken_anchor": ("失效锚点", "danger", "核对来源回链"),
    }
    issues, affected_books = [], set()

    def add_issue(book: Book, kind: str, reason: str, evidence: dict | None = None) -> None:
        label, severity, action = category_meta[kind]
        issues.append({"id": f"{kind}:{book.id}", "type": kind, "label": label,
                       "severity": severity, "book_id": book.id, "book_title": book.title,
                       "reason": reason, "evidence": evidence or {}, "action_label": action,
                       "action_path": f"/reader/{book.id}" if kind in {"missing_toc", "broken_anchor"} else f"/library?bookId={book.id}"})
        affected_books.add(book.id)

    for book in books:
        stats = chunk_stats.get(book.id, {"chunks": 0, "chars": 0, "replacement": 0})
        if book.status != "ready" or stats["chunks"] == 0:
            add_issue(book, "unparsed", book.error_msg or f"当前状态为 {book.status}，全文检索与知识功能不可可靠使用",
                      {"status": book.status, "chunk_count": stats["chunks"]})
        if book.status == "ready" and book.file_type == "pdf" and (book.total_pages or 0) >= 3:
            avg_chars = round(stats["chars"] / max(book.total_pages or 1, 1), 1)
            replacement_ratio = round(stats["replacement"] / max(stats["chars"], 1), 4)
            if avg_chars < 180 or replacement_ratio > .01:
                add_issue(book, "low_quality_ocr", "可检索文本过少或包含较多无法识别字符，需要对照原页抽查",
                          {"average_chars_per_page": avg_chars, "replacement_ratio": replacement_ratio})
        if book.status == "ready" and chapter_counts.get(book.id, 0) == 0:
            add_issue(book, "missing_toc", "没有可导航目录，阅读、检索定位和汇报取材会退化", {"chapter_count": 0})
        profile = profiles.get(book.id)
        if not profile or not any([profile.authors, profile.journal, profile.published_year, profile.doi, profile.arxiv_id]):
            add_issue(book, "missing_metadata", "缺少作者、来源、年份、DOI 或 arXiv 等基本档案字段")
        if book.duplicate_of or (book.file_hash and book.file_hash in duplicate_hashes):
            add_issue(book, "duplicate", "文件哈希或正文相似性提示该资料可能与库内记录重复",
                      {"duplicate_of": book.duplicate_of, "file_hash": book.file_hash})
        if broken_anchors.get(book.id, 0):
            add_issue(book, "broken_anchor", f"发现 {broken_anchors[book.id]} 条无法回到当前原文的来源锚点",
                      {"count": broken_anchors[book.id], "objects": broken_objects.get(book.id, [])[:50]})

    categories = {kind: {"label": meta[0], "count": sum(item["type"] == kind for item in issues),
                         "severity": meta[1]} for kind, meta in category_meta.items()}
    # Normalize issue burden by library size without rounding small libraries' issues away.
    # A budget of two maximum-severity issues per book maps to a zero score.
    weights = {"danger": 1.0, "warning": 0.625, "info": 0.25}
    issue_burden = sum(weights[item["severity"]] for item in issues)
    score = max(0, round(100 * (1 - min(issue_burden / max(len(books) * 2, 1), 1))))
    return {"score": score, "book_count": len(books), "healthy_book_count": len(books) - len(affected_books),
            "issue_count": len(issues), "categories": categories, "items": issues,
            "boundary": "健康检查为可解释的本地启发式审计；低质量 OCR 与重复资料需要人工核对后再处理。",
            "checked_at": datetime.now(timezone.utc).isoformat()}


@router.post("/books/health/repair", status_code=202)
def repair_health_issues(req: HealthRepairReq, db: Session = Depends(get_db)):
    """批量执行确定性安全修复；需判断/删除的项目只返回精确人工入口。"""
    from backend.app.models import Annotation, ImportTask
    from backend.app.api.annotations import repair_annotation

    results = []
    for issue_id in dict.fromkeys(req.issue_ids):
        match = re.fullmatch(r"(unparsed|low_quality_ocr|missing_toc|missing_metadata|duplicate|broken_anchor):(\d+)", issue_id)
        if not match:
            results.append({"issue_id": issue_id, "status": "invalid", "message": "问题标识无效"})
            continue
        kind, raw_book_id = match.groups()
        book_id = int(raw_book_id)
        book = db.get(Book, book_id)
        if not book:
            results.append({"issue_id": issue_id, "status": "gone", "message": "资料已不存在"})
            continue
        if kind in {"unparsed", "low_quality_ocr"}:
            active = db.scalar(select(ImportTask).where(
                ImportTask.book_id == book_id,
                ImportTask.status.in_(["pending", "running", "cancelling"]),
            ).order_by(ImportTask.created_at.desc()))
            if active:
                results.append({"issue_id": issue_id, "status": "already_running", "task_id": active.id,
                                "message": "已有解析任务在运行"})
            else:
                task = reparse_book(book_id, db)
                results.append({"issue_id": issue_id, "status": "submitted", "task_id": task["task_id"],
                                "message": "已提交重新解析"})
            continue
        if kind == "broken_anchor":
            # A book-level broken-anchor issue can be caused by a note or evidence card.
            # Only pass annotations that fail the same checks as the health audit to
            # repair_annotation; touching every valid annotation would silently move
            # healthy user highlights.
            annotations = []
            for annotation in db.scalars(select(Annotation).where(Annotation.book_id == book_id)).all():
                invalid = annotation.status != "active" or annotation.page < 0
                if book.total_pages and annotation.page > book.total_pages:
                    invalid = True
                if annotation.anchor_json:
                    try:
                        anchor = json.loads(annotation.anchor_json)
                        fingerprint = anchor.get("document_fingerprint") if isinstance(anchor, dict) else None
                        if fingerprint and book.file_hash and fingerprint != book.file_hash:
                            invalid = True
                    except (TypeError, ValueError):
                        invalid = True
                if invalid:
                    annotations.append(annotation)
            repaired = 0
            unresolved = 0
            for annotation in annotations:
                before = annotation.status
                try:
                    response = repair_annotation(annotation.id, db)
                    if response.status == "active":
                        repaired += 1
                    elif before != "active" or response.status == "needs_reanchor":
                        unresolved += 1
                except Exception:  # one damaged annotation must not abort the batch
                    unresolved += 1
            results.append({"issue_id": issue_id, "status": "repaired" if repaired else "manual_required",
                            "repaired": repaired, "unresolved": unresolved,
                            "action_path": f"/reader/{book_id}",
                            "message": f"自动恢复 {repaired} 条批注；其余知识对象需人工核对"})
            continue
        action_path = f"/reader/{book_id}?toc=review" if kind == "missing_toc" else f"/library?bookId={book_id}"
        results.append({"issue_id": issue_id, "status": "manual_required", "action_path": action_path,
                        "message": {"missing_toc": "目录需要对照原页确认", "missing_metadata": "档案字段需要人工确认",
                                    "duplicate": "重复资料涉及删除或合并，必须人工决定"}[kind]})
    return {"results": results, "submitted": sum(item["status"] == "submitted" for item in results),
            "repaired": sum(item["status"] == "repaired" for item in results)}


@router.get("/books/{book_id}", response_model=BookDetailResp)
def get_book(book_id: int, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    chapters = db.scalars(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_index)
    ).all()
    analysis = _get_analysis(db, book_id)
    profile = db.get(PaperProfile, book_id)
    if not profile:
        profile = PaperProfile(book_id=book_id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return BookDetailResp(
        id=book.id, title=book.title, file_type=book.file_type, status=book.status,
        total_pages=book.total_pages, error_msg=book.error_msg,
        chapters=_build_chapter_tree(chapters), analysis=analysis,
        archive=_profile_resp(profile),
    )


def _profile_resp(profile: PaperProfile) -> PaperProfileResp:
    return PaperProfileResp(
        book_id=profile.book_id, authors=profile.authors, journal=profile.journal,
        published_year=profile.published_year, doi=profile.doi, arxiv_id=profile.arxiv_id,
        language=profile.language, abstract=profile.abstract, source_url=profile.source_url,
        access_route=profile.access_route or "local_upload",
        publication_status=profile.publication_status or "unknown",
        visibility=profile.visibility or "private", demo_allowed=bool(profile.demo_allowed),
        metadata_confidence=profile.metadata_confidence or 0.0,
        reading_status=profile.reading_status or "unread", favorite=bool(profile.favorite),
        rating=profile.rating, progress_page=profile.progress_page or 1,
        last_read_at=profile.last_read_at,
    )


@router.get("/books/{book_id}/archive", response_model=PaperProfileResp)
def get_archive_profile(book_id: int, db: Session = Depends(get_db)):
    if not db.get(Book, book_id):
        raise HTTPException(404, "书籍不存在")
    profile = db.get(PaperProfile, book_id)
    if not profile:
        profile = PaperProfile(book_id=book_id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return _profile_resp(profile)


@router.patch("/books/{book_id}/archive", response_model=PaperProfileResp)
def update_archive_profile(book_id: int, req: PaperProfileUpdateReq, db: Session = Depends(get_db)):
    if not db.get(Book, book_id):
        raise HTTPException(404, "书籍不存在")
    profile = db.get(PaperProfile, book_id) or PaperProfile(book_id=book_id)
    values = req.model_dump(exclude_unset=True)
    if "reading_status" in values and values["reading_status"] not in ("unread", "reading", "read"):
        raise HTTPException(400, "reading_status 仅支持 unread/reading/read")
    for key, value in values.items():
        setattr(profile, key, int(value) if key == "favorite" and value is not None else value)
    if "progress_page" in values or "reading_status" in values:
        from datetime import datetime
        profile.last_read_at = datetime.now()
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return _profile_resp(profile)


@router.get("/books/{book_id}/source-map")
def get_source_map(book_id: int, db: Session = Depends(get_db)):
    import json
    if not db.get(Book, book_id):
        raise HTTPException(404, "书籍不存在")
    profile = db.get(PaperProfile, book_id)
    if not profile or not profile.source_map_json:
        return {"version": 1, "book_id": book_id, "locator_mode": "unavailable", "blocks": []}
    try:
        return json.loads(profile.source_map_json)
    except (ValueError, TypeError):
        return {"version": 1, "book_id": book_id, "locator_mode": "unavailable", "blocks": []}


def _get_analysis(db: Session, book_id: int):
    """读取智能分析结果。"""
    import json
    from backend.app.models import BookAnalysis

    a = db.scalar(select(BookAnalysis).where(BookAnalysis.book_id == book_id))
    if not a:
        return None
    def _load(s):
        try:
            return json.loads(s) if s else []
        except json.JSONDecodeError:
            return []
    table_pages = _load(a.table_pages)
    return {
        "definitions": _load(a.definitions_json),
        "theorems": _load(a.theorems_json),
        "keywords": _load(a.keywords_json),
        "body_size": a.body_size,
        "header_count": a.header_count,
        "footer_count": a.footer_count,
        "table_pages": table_pages,
    }


def _validate_upload_path(path: Path, file_type: str) -> None:
    """文件签名校验（防改扩展名伪装）+ 压缩炸弹检查。"""
    with path.open("rb") as stream:
        head = stream.read(16)
    if file_type == "pdf":
        if not head.lstrip().startswith(b"%PDF-"):
            raise HTTPException(400, "文件内容不是有效的 PDF（缺少 PDF 签名）")
    else:  # docx / pptx 是 ZIP 容器
        _pk = b"PK" + bytes([3, 4])
        _pk5 = b"PK" + bytes([5, 6])
        _pk7 = b"PK" + bytes([7, 8])
        if not (head.startswith(_pk) or head.startswith(_pk5) or head.startswith(_pk7)):
            raise HTTPException(400, f"文件内容不是有效的 {file_type.upper()}（缺少 ZIP 结构）")
        import zipfile as _zip
        try:
            with _zip.ZipFile(path) as z:
                entries = z.infolist()
                if len(entries) > 10000:
                    raise HTTPException(400, "压缩包文件项过多，已拒绝导入")
                unpacked = sum(i.file_size for i in entries)
                if unpacked > 500 * 1024 * 1024:
                    raise HTTPException(400, "文件解压后过大，疑似压缩炸弹")
        except _zip.BadZipFile:
            raise HTTPException(400, f"文件内容不是有效的 {file_type.upper()}（ZIP 结构损坏）")


async def _store_validated_upload(file: UploadFile, file_type: str) -> tuple[Path, str, int]:
    """以固定 1MB 缓冲落盘并计算哈希，不在内存中保留整份文献。"""
    import hashlib
    from backend.app.core.config import settings as _settings

    MAX_SIZE = 200 * 1024 * 1024
    safe_name = Path(file.filename or f"upload.{file_type}").name
    path = _settings.uploads_dir / safe_name
    stem, suffix = path.stem, path.suffix
    index = 1
    while path.exists():
        path = _settings.uploads_dir / f"{stem}_{index}{suffix}"
        index += 1

    total = 0
    digest = hashlib.sha256()
    try:
        with path.open("xb") as destination:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_SIZE:
                    raise HTTPException(400, "文件超过 200MB 限制")
                destination.write(chunk)
                digest.update(chunk)
        if not total:
            raise HTTPException(400, "文件为空")
        _validate_upload_path(path, file_type)
        return path, digest.hexdigest(), total
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _check_duplicate(db: Session, file_hash: str) -> "Book | None":
    """按文件哈希查重，返回已存在的 Book（若有）。"""
    return db.scalar(select(Book).where(Book.file_hash == file_hash).limit(1))


@router.post("/books/upload", status_code=201)
async def upload_book(file: UploadFile, db: Session = Depends(get_db)):
    file_type = (file.filename or "").rsplit(".", 1)[-1].lower()
    if file_type not in ("pdf", "docx", "pptx"):
        raise HTTPException(400, f"不支持的文件类型: {file_type}，仅支持 pdf/docx/pptx")

    path, file_hash, file_size = await _store_validated_upload(file, file_type)

    # 去重：相同哈希的文件已存在则跳过解析
    existing = _check_duplicate(db, file_hash)
    if existing:
        try:
            path.unlink()
        except OSError:
            pass
        return {"id": existing.id, "title": existing.title, "file_type": existing.file_type,
                "status": existing.status, "task_id": None, "duplicate": True,
                "message": f"文件已存在：《{existing.title}》", "created_at": existing.created_at}

    book = Book(
        title=Path(file.filename).stem,
        file_path=path.name,
        file_type=file_type,
        file_size=file_size,
        file_hash=file_hash,
        status="pending",
        library_order=(db.scalar(select(func.min(Book.library_order))) or 0) - 1,
    )
    db.add(book)
    db.commit()
    db.refresh(book)

    record = submit("import", lambda rec: run_import(rec, book.id), book_id=book.id)
    return {"id": book.id, "title": book.title, "file_type": book.file_type,
            "status": book.status, "task_id": record.id, "created_at": book.created_at}


@router.post("/books/upload-batch", status_code=201)
async def upload_books_batch(files: list[UploadFile], db: Session = Depends(get_db)):
    """批量上传多个文件。逐个校验+去重+提交解析任务（FIFO 队列串行执行）。"""
    results: list[dict] = []
    for file in files:
        file_type = (file.filename or "").rsplit(".", 1)[-1].lower()
        if file_type not in ("pdf", "docx", "pptx"):
            results.append({"filename": file.filename, "error": f"不支持的文件类型: {file_type}"})
            continue
        try:
            path, file_hash, file_size = await _store_validated_upload(file, file_type)

            existing = _check_duplicate(db, file_hash)
            if existing:
                try:
                    path.unlink()
                except OSError:
                    pass
                results.append({
                    "filename": file.filename, "id": existing.id, "title": existing.title,
                    "status": existing.status, "duplicate": True,
                    "message": f"文件已存在：《{existing.title}》",
                })
                continue

            book = Book(
                title=Path(file.filename).stem,
                file_path=path.name,
                file_type=file_type,
                file_size=file_size,
                file_hash=file_hash,
                status="pending",
                library_order=(db.scalar(select(func.min(Book.library_order))) or 0) - 1,
            )
            db.add(book)
            db.commit()
            db.refresh(book)
            record = submit("import", lambda rec, bid=book.id: run_import(rec, bid), book_id=book.id)
            results.append({
                "filename": file.filename, "id": book.id, "title": book.title,
                "status": "pending", "task_id": record.id,
            })
        except HTTPException as e:
            results.append({"filename": file.filename, "error": e.detail})
        except Exception as e:  # noqa: BLE001
            results.append({"filename": file.filename, "error": str(e)})
    return {"results": results}


@router.patch("/books/{book_id}")
def rename_book(book_id: int, req: BookRenameReq, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    book.title = req.title
    db.commit()
    return {"id": book.id, "title": book.title}


@router.get("/books/{book_id}/file")
def get_book_file(book_id: int, db: Session = Depends(get_db)):
    """返回原始文件（浏览器可直接打开/渲染，支持 #page=N 定位）。"""
    from fastapi.responses import FileResponse
    from backend.app.core.config import settings as _settings

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    path = _settings.uploads_dir / book.file_path
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    media = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }.get(book.file_type, "application/octet-stream")
    # content_disposition_type=inline：浏览器内嵌展示（pdf.js / iframe 均可用），不触发下载
    return FileResponse(path, media_type=media, filename=book.file_path,
                        content_disposition_type="inline")


@router.get("/books/{book_id}/rendered-file")
def get_rendered_book_file(book_id: int, db: Session = Depends(get_db)):
    """DOCX/PPTX 的高保真原版 PDF；首次访问按需渲染，之后按哈希缓存。"""
    from fastapi.responses import FileResponse
    from backend.app.core.config import settings as _settings
    from backend.app.services.office_render import render_office_pdf

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    if book.file_type == "pdf":
        path = _settings.uploads_dir / book.file_path
    elif book.file_type in {"docx", "pptx"}:
        try:
            path = render_office_pdf(_settings.uploads_dir / book.file_path, book.file_type, book.file_hash)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise HTTPException(503, f"原版渲染暂不可用：{exc}") from exc
    else:
        raise HTTPException(400, "该格式不支持原版渲染")
    return FileResponse(path, media_type="application/pdf", filename=f"{Path(book.file_path).stem}.pdf",
                        content_disposition_type="inline")


@router.get("/books/{book_id}/chunk/{chunk_id}")
def get_chunk_original(chunk_id: int, book_id: int, db: Session = Depends(get_db)):
    """返回 chunk 全文 + 页码区间（供右侧原文定位面板）。"""
    from backend.app.models import Chunk as ChunkModel

    ch = db.get(ChunkModel, chunk_id)
    if not ch or ch.book_id != book_id:
        raise HTTPException(404, "内容不存在")
    return {
        "chunk_id": ch.id,
        "book_id": ch.book_id,
        "chapter_id": ch.chapter_id,
        "content": ch.content,
        "page_start": ch.page_start,
        "page_end": ch.page_end,
    }


@router.get("/books/{book_id}/page/{page_no}")
def get_page_original(book_id: int, page_no: int, db: Session = Depends(get_db)):
    """返回指定页原文文本（PDF 页文本；docx/pptx 无页码概念则返回空）。"""
    from backend.app.core.config import settings as _settings
    from backend.app.services.parser import parse_document

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    if book.file_type != "pdf":
        return {"page": page_no, "text": "", "note": "该格式不支持按页查看"}
    result = parse_document(_settings.uploads_dir / book.file_path)
    if 1 <= page_no <= len(result.pages):
        return {"page": page_no, "text": result.pages[page_no - 1]}
    raise HTTPException(404, "页码超出范围")


@router.delete("/books/{book_id}", status_code=204)
def delete_book(book_id: int, db: Session = Depends(get_db)):
    from backend.app.core.config import settings as _settings
    from backend.app.services.book_lifecycle import delete_book_records

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    file_path = book.file_path
    try:
        deck_files = delete_book_records(db, book_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    # 数据库提交后再删文件，避免事务失败造成不可恢复的数据丢失。
    for deck_file in deck_files:
        try:
            (_settings.presentations_dir / Path(deck_file).name).unlink(missing_ok=True)
        except OSError:
            pass
    # 删除上传文件与 FTS 索引（仅项目 data 目录内）
    f = _settings.uploads_dir / file_path
    try:
        if f.exists():
            f.unlink()
    except OSError:
        pass
    try:
        fts.delete_book_index(book_id)
    except Exception:  # noqa: BLE001
        pass
    # 清理向量（若开启）
    try:
        from backend.app.services.rag import vector
        vector.delete_book_vectors(book_id)
    except Exception:  # noqa: BLE001
        pass


@router.post("/books/{book_id}/reparse")
def reparse_book(book_id: int, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    # 清空可再生解析产物；笔记/题目/知识节点保留，仅解除旧章节关联。
    from backend.app.services.book_lifecycle import prepare_book_for_reparse
    prepare_book_for_reparse(db, book_id)
    try:
        from backend.app.services.rag import vector
        vector.delete_book_vectors(book_id)
    except Exception:  # noqa: BLE001
        pass
    book.status = "pending"
    book.error_msg = None
    db.commit()
    record = submit("reimport", lambda rec: run_import(rec, book.id), book_id=book.id)
    return {"task_id": record.id}


@router.get("/search", response_model=SearchResp)
def search(
    q: str = Query(min_length=1),
    book_id: int | None = Query(default=None),
    book_ids: str | None = Query(default=None),  # 逗号分隔的 book_id 列表
    category: str | None = Query(default=None),
    tag_id: int | None = Query(default=None),
    chapter_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """跨资料全文+语义混合检索（RRF 融合）。

    支持按 book_id / book_ids / category / tag_id 过滤。
    """
    # 解析 book_ids 参数
    ids_list: list[int] | None = None
    if book_ids:
        try:
            ids_list = [int(x.strip()) for x in book_ids.split(",") if x.strip()]
        except ValueError:
            ids_list = None

    # 按 category / tag 过滤出 book_ids
    filter_ids: set[int] | None = None
    if category:
        cat_books = db.scalars(select(Book).where(Book.category == category)).all()
        filter_ids = {b.id for b in cat_books}
    if tag_id:
        from backend.app.models import book_tags
        tagged = db.execute(select(book_tags.c.book_id).where(book_tags.c.tag_id == tag_id)).all()
        tag_ids_set = {r[0] for r in tagged}
        filter_ids = tag_ids_set if filter_ids is None else (filter_ids & tag_ids_set)

    # 合并 book_id / book_ids / filter_ids
    final_ids: list[int] | None = None
    if ids_list:
        final_ids = ids_list
    elif book_id is not None:
        final_ids = [book_id]
    if filter_ids is not None:
        if final_ids:
            final_ids = [bid for bid in final_ids if bid in filter_ids]
        else:
            final_ids = list(filter_ids)

    # 走混合检索（RRF 融合：向量 + FTS + LIKE）
    from backend.app.services.rag import retriever
    items = retriever.retrieve(q, book_ids=final_ids, top_k=page_size)

    # 分页（混合检索结果已在内存中，手动切片）
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    paged = items[start:end]

    return SearchResp(
        total=total,
        items=[
            SearchResultItem(
                chunk_id=it.get("chunk_id", 0),
                book_id=it.get("book_id", 0),
                book_title=it.get("book_title", ""),
                chapter_id=it.get("chapter_id"),
                chapter_title=it.get("chapter_title"),
                page=it.get("page"),
                page_start=it.get("page_start") or it.get("page"),
                page_end=it.get("page_end") or it.get("page"),
                snippet=it.get("snippet", ""),
            ) for it in paged
        ],
    )


@router.get("/tasks/{task_id}", response_model=TaskResp)
def get_task_status(task_id: str):
    record = get_task(task_id)
    if not record:
        raise HTTPException(404, "任务不存在")
    return TaskResp(
        task_id=record.id, status=record.status, progress=record.progress,
        stage=record.stage, message=record.message, error=record.error,
        result=record.result,
    )


@router.get("/tasks/{task_id}/events")
async def task_status_events(task_id: str):
    """单任务 SSE：仅状态变化时推送，替代页面每两秒重复请求。"""
    if not get_task(task_id):
        raise HTTPException(404, "任务不存在")

    async def stream():
        previous = None
        for tick in range(43200):  # 最长保持约 6 小时，覆盖超长 OCR 与研究任务。
            record = get_task(task_id)
            if not record:
                yield 'event: error\ndata: {"detail":"任务不存在"}\n\n'
                return
            payload = {"task_id": record.id, "status": record.status, "progress": record.progress,
                       "stage": record.stage, "message": record.message, "error": record.error,
                       "result": record.result}
            encoded = json.dumps(payload, ensure_ascii=False)
            if encoded != previous:
                yield f"data: {encoded}\n\n"
                previous = encoded
            elif tick and tick % 30 == 0:
                # 注释型 SSE 心跳不会触发前端业务事件，但能避免本地代理误判空闲连接。
                yield ": keep-alive\n\n"
            if record.status in {"done", "failed", "cancelled"}:
                return
            await asyncio.sleep(0.5)
        yield 'event: timeout\ndata: {"detail":"任务仍在运行，请转到任务中心查看"}\n\n'

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/tasks/{task_id}/cancel")
def cancel_task_status(task_id: str):
    record = cancel_task(task_id)
    if not record:
        raise HTTPException(404, "任务不存在")
    return {
        "task_id": record.id,
        "status": record.status,
        "message": record.message,
    }


@router.post("/tasks/{task_id}/retry", status_code=202)
def retry_task_status(task_id: str):
    try:
        record = retry_task(task_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": record.id, "status": record.status, "book_id": record.book_id,
            "message": "已重新排队；OCR 页面缓存将继续复用"}


@router.get("/tasks")
def list_task_statuses(
    active_only: bool = Query(default=False),
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """全局任务中心：读取持久化任务，刷新或重启后仍可查看。"""
    from backend.app.models import ImportTask

    query = (
        select(ImportTask, Book.title)
        .outerjoin(Book, Book.id == ImportTask.book_id)
        .order_by(
            case((ImportTask.status.in_(["pending", "running", "cancelling"]), 0), else_=1),
            ImportTask.updated_at.desc(),
            ImportTask.created_at.desc(),
        )
        .limit(limit)
    )
    if active_only:
        query = query.where(ImportTask.status.in_(["pending", "running", "cancelling"]))
    rows = db.execute(query).all()
    return {
        "items": [
            {
                "task_id": task.id,
                "book_id": task.book_id,
                "book_title": title or "",
                "name": task.name,
                "status": task.status,
                "progress": task.progress or 0.0,
                "stage": task.stage or "",
                "message": task.message or "",
                "error": task.error,
                "result": json.loads(task.result_json) if task.result_json else None,
                "retry_count": task.retry_count or 0,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
            }
            for task, title in rows
        ]
    }


@router.get("/books/{book_id}/document")
def get_document(book_id: int, db: Session = Depends(get_db)):
    """返回结构化文档（章节树 + 每章正文），供 docx/pptx 文本阅读器使用。"""
    from backend.app.models import Chunk as _Chunk
    from backend.app.models import Chapter as _Chapter

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    chapters = db.scalars(
        select(_Chapter).where(_Chapter.book_id == book_id).order_by(_Chapter.order_index)
    ).all()
    chunks = db.scalars(
        select(_Chunk).where(_Chunk.book_id == book_id).order_by(_Chunk.chunk_index)
    ).all()
    by_chapter: dict[int | None, list[str]] = {}
    for c in chunks:
        by_chapter.setdefault(c.chapter_id, []).append(c.content)
    fresh_pages: list[str] = []
    if book.file_type in {"docx", "pptx"}:
        try:
            from backend.app.core.config import settings as _settings
            from backend.app.services.parser import ParseError, parse_document
            fresh = parse_document(_settings.uploads_dir / book.file_path)
            if len(fresh.pages) == len(chapters):
                fresh_pages = fresh.pages
        except (OSError, RuntimeError, ValueError, ParseError):
            fresh_pages = []
    return {
        "book_id": book.id,
        "title": book.title,
        "file_type": book.file_type,
        "chapters": [
            {"id": ch.id, "title": ch.title, "level": ch.level or 1, "order_index": ch.order_index,
             "parent_id": ch.parent_id}
            for ch in chapters
        ],
        "sections": [
            {"chapter_id": ch.id, "title": ch.title,
             "text": fresh_pages[index] if fresh_pages else "\n\n".join(by_chapter.get(ch.id, []))}
            for index, ch in enumerate(chapters)
        ],
        "source_view": "office_pdf" if book.file_type in {"docx", "pptx"} else "native_pdf",
        "structure_source": "live_ooxml" if fresh_pages else "indexed_chunks",
    }


@router.patch("/chapters/{chapter_id}")
def rename_chapter(chapter_id: int, req: dict, db: Session = Depends(get_db)):
    """重命名章节标题（docx/pptx 目录编辑）。"""
    from backend.app.models import Chapter as _Chapter

    ch = db.get(_Chapter, chapter_id)
    if not ch:
        raise HTTPException(404, "章节不存在")
    title = (req.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    from backend.app.services.archive import build_source_map
    from backend.app.services.rag.toc_editor import chapter_snapshot
    chapters = list(db.scalars(select(_Chapter).where(_Chapter.book_id == ch.book_id)
                               .order_by(_Chapter.order_index)).all())
    before = chapter_snapshot(chapters)
    ch.title = title
    after = chapter_snapshot(chapters)
    revision = TocRevision(book_id=ch.book_id, source="user", note="重命名目录标题",
                           before_json=json.dumps(before, ensure_ascii=False),
                           after_json=json.dumps(after, ensure_ascii=False))
    db.add(revision)
    chunks = list(db.scalars(select(Chunk).where(Chunk.book_id == ch.book_id)
                             .order_by(Chunk.chunk_index)).all())
    profile = db.get(PaperProfile, ch.book_id) or PaperProfile(book_id=ch.book_id)
    profile.source_map_json = build_source_map(ch.book_id, chunks, {row.id: row.title for row in chapters})
    db.add(profile)
    db.commit()
    return {"id": ch.id, "title": ch.title, "revision_id": revision.id}


@router.get("/books/{book_id}/toc-review")
def review_book_toc(book_id: int, db: Session = Depends(get_db)):
    if not db.get(Book, book_id):
        raise HTTPException(404, "书籍不存在")
    chapters = list(db.scalars(select(Chapter).where(Chapter.book_id == book_id)
                               .order_by(Chapter.order_index)).all())
    from backend.app.services.rag.toc_editor import review_chapters
    return review_chapters(chapters)


@router.post("/maintenance/toc-rebuild-all", status_code=202)
def rebuild_all_book_tocs():
    """复用已落盘的版面证据重识别全库目录；每本目录均生成可恢复修订。"""
    from backend.app.services.rag.toc_rebuild import rebuild_all_tocs
    record = submit("toc-rebuild", rebuild_all_tocs)
    return {"task_id": record.id}


@router.post("/books/{book_id}/toc-auto-repair")
def auto_repair_book_toc(book_id: int, req: TocRepairReq, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    chapters = list(db.scalars(select(Chapter).where(Chapter.book_id == book_id)
                               .order_by(Chapter.order_index)).all())
    from backend.app.services.rag.toc_editor import replace_book_toc
    from backend.app.services.rag.toc_logic import build_toc_repair_preview
    index_by_id = {chapter.id: index for index, chapter in enumerate(chapters)}
    preview = build_toc_repair_preview([
        {"id": chapter.id, "title": chapter.title, "level": chapter.level,
         "page": chapter.start_page or 1, "parent_index": index_by_id.get(chapter.parent_id)}
        for chapter in chapters
    ])
    if not req.apply or not preview["can_apply"]:
        return {"applied": False, "audit": preview["audit"],
                "preview": {key: value for key, value in preview.items() if key not in {"audit", "repaired"}}}
    items = []
    stack = []
    for item in preview["repaired"]:
        level = item["level"]
        while len(stack) >= level:
            stack.pop()
        if level > len(stack) + 1:
            raise HTTPException(409, "自动修正仍存在层级跳跃，已阻止写入；请在目录工作台人工确认")
        parent = stack[-1] if level > 1 else None
        items.append({"client_key": f"id:{item['id']}", "id": item["id"],
                      "parent_key": f"id:{parent['id']}" if parent else None,
                      "title": item["title"], "level": level, "start_page": item["page"]})
        stack.append(item)
    try:
        result = replace_book_toc(db, book, items, "auto", "编号逻辑安全修正")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"applied": True, **result}


@router.put("/books/{book_id}/toc")
def replace_toc(book_id: int, req: TocReplaceReq, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    from backend.app.services.rag.toc_editor import replace_book_toc
    try:
        return replace_book_toc(db, book, [item.model_dump() for item in req.items], "user", req.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/books/{book_id}/toc-revisions")
def list_toc_revisions(book_id: int, db: Session = Depends(get_db)):
    if not db.get(Book, book_id):
        raise HTTPException(404, "书籍不存在")
    rows = db.scalars(select(TocRevision).where(TocRevision.book_id == book_id)
                      .order_by(TocRevision.created_at.desc()).limit(30)).all()
    return [{"id": row.id, "source": row.source, "note": row.note, "created_at": row.created_at}
            for row in rows]


@router.post("/books/{book_id}/toc-revisions/{revision_id}/restore")
def restore_toc_revision(book_id: int, revision_id: int, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    revision = db.get(TocRevision, revision_id)
    if not book or not revision or revision.book_id != book_id:
        raise HTTPException(404, "目录修订不存在")
    try:
        snapshot = json.loads(revision.before_json or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(409, "历史修订快照损坏，无法恢复") from exc
    current = list(db.scalars(select(Chapter).where(Chapter.book_id == book_id)).all())
    from backend.app.services.rag.toc_editor import replace_book_toc, revision_snapshot_to_items
    items = revision_snapshot_to_items(snapshot, {row.id for row in current}, f"restore:{revision_id}")
    try:
        return replace_book_toc(db, book, items, "user", f"恢复到修订 {revision_id} 之前")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, f"恢复预检失败：{exc}") from exc


@router.get("/books/{book_id}/notes", response_model=list[NoteResp])
def list_notes(book_id: int, db: Session = Depends(get_db)):
    notes = db.scalars(
        select(Note).where(Note.book_id == book_id).order_by(Note.page)
    ).all()
    return [NoteResp(id=n.id, book_id=n.book_id, chapter_id=n.chapter_id, page=n.page,
                     content=n.content, created_at=n.created_at) for n in notes]


@router.post("/books/{book_id}/notes", response_model=NoteResp, status_code=201)
def create_note(book_id: int, req: NoteCreateReq, db: Session = Depends(get_db)):
    if not db.get(Book, book_id):
        raise HTTPException(404, "书籍不存在")
    note = Note(book_id=book_id, page=req.page, content=req.content,
                highlight_json=req.highlight_json)
    db.add(note)
    db.commit()
    db.refresh(note)
    return NoteResp(id=note.id, book_id=note.book_id, chapter_id=note.chapter_id,
                    page=note.page, content=note.content, created_at=note.created_at)


@router.patch("/notes/{note_id}", response_model=NoteResp)
def update_note(note_id: int, req: NoteUpdateReq, db: Session = Depends(get_db)):
    note = db.get(Note, note_id)
    if not note:
        raise HTTPException(404, "笔记不存在")
    if req.content is not None:
        note.content = req.content
    if req.highlight_json is not None:
        note.highlight_json = req.highlight_json
    db.commit()
    return NoteResp(id=note.id, book_id=note.book_id, chapter_id=note.chapter_id,
                    page=note.page, content=note.content, created_at=note.created_at)


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note = db.get(Note, note_id)
    if not note:
        raise HTTPException(404, "笔记不存在")
    db.delete(note)
    db.commit()
