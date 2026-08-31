"""个人知识库洞察：质量、结构覆盖、知识沉淀、来源链与研究输出。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import (
    Annotation,
    Book,
    Chapter,
    Chunk,
    EvidenceCard,
    KnowledgeNode,
    KnowledgeNote,
    PaperProfile,
    PresentationDeck,
    StudyReport,
    WritingOutput,
)

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _count(db: Session, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    if where:
        stmt = stmt.where(*where)
    return int(db.scalar(stmt) or 0)


def _distinct_books(db: Session, model, *where) -> set[int]:
    stmt = select(model.book_id).distinct()
    if where:
        stmt = stmt.where(*where)
    return {int(value) for value in db.scalars(stmt).all() if value is not None}


def _rate(value: int, total: int) -> float:
    return round(value / total, 3) if total else 0.0


def _daily_counts(db: Session, model, column, since: datetime) -> dict[str, int]:
    rows = db.execute(
        select(func.date(column).label("day"), func.count().label("count"))
        .select_from(model).where(column >= since).group_by(func.date(column))
    ).all()
    return {str(row.day): int(row.count or 0) for row in rows}


@router.get("/knowledge-base")
def knowledge_base_insights(
    days: int = Query(30, ge=7, le=365),
    db: Session = Depends(get_db),
):
    """Return one read-only snapshot aligned with the personal knowledge-base lifecycle."""
    from backend.app.api.books import knowledge_base_health

    books = list(db.scalars(select(Book).order_by(Book.created_at.desc())).all())
    book_count = len(books)
    ready_ids = {book.id for book in books if book.status == "ready"}
    indexed_ids = _distinct_books(db, Chunk)
    toc_ids = _distinct_books(db, Chapter)
    annotation_ids = _distinct_books(db, Annotation, Annotation.status == "active")
    note_ids = _distinct_books(db, KnowledgeNote)
    evidence_ids = _distinct_books(db, EvidenceCard)
    knowledge_book_ids = note_ids | evidence_ids

    metadata_ids = {
        int(book_id) for book_id in db.scalars(
            select(PaperProfile.book_id).where(or_(
                PaperProfile.authors.is_not(None), PaperProfile.journal.is_not(None),
                PaperProfile.published_year.is_not(None), PaperProfile.doi.is_not(None),
                PaperProfile.arxiv_id.is_not(None),
            ))
        ).all()
    }
    metadata_ids &= {int(book.id) for book in books}

    annotation_count = _count(db, Annotation, Annotation.status == "active")
    note_count = _count(db, KnowledgeNote)
    evidence_count = _count(db, EvidenceCard)
    node_count = _count(db, KnowledgeNode)
    knowledge_object_count = annotation_count + note_count + evidence_count + node_count
    traced_notes = _count(
        db, KnowledgeNote,
        KnowledgeNote.source_refs_json.is_not(None), KnowledgeNote.source_refs_json != "[]",
    )
    traced_evidence = _count(
        db, EvidenceCard,
        EvidenceCard.source_ref_json.is_not(None), EvidenceCard.source_ref_json != "{}",
    )
    traceable_total = note_count + evidence_count
    report_count = _count(db, StudyReport)
    writing_count = _count(db, WritingOutput)
    deck_count = _count(db, PresentationDeck)
    finished_decks = _count(db, PresentationDeck, PresentationDeck.status == "done")

    health = knowledge_base_health(db)
    health_categories = [
        {"key": key, **value}
        for key, value in health.get("categories", {}).items()
        if value.get("count", 0)
    ]

    coverage = [
        {"key": "ready", "label": "解析就绪", "value": len(ready_ids), "total": book_count,
         "rate": _rate(len(ready_ids), book_count), "path": "/library"},
        {"key": "indexed", "label": "全文可检索", "value": len(indexed_ids & ready_ids), "total": len(ready_ids),
         "rate": _rate(len(indexed_ids & ready_ids), len(ready_ids)), "path": "/knowledge-health"},
        {"key": "toc", "label": "具备目录", "value": len(toc_ids & ready_ids), "total": len(ready_ids),
         "rate": _rate(len(toc_ids & ready_ids), len(ready_ids)), "path": "/knowledge-health"},
        {"key": "metadata", "label": "具备基本元数据", "value": len(metadata_ids), "total": book_count,
         "rate": _rate(len(metadata_ids), book_count), "path": "/knowledge-health"},
        {"key": "evidence", "label": "已有原文取证", "value": len(annotation_ids), "total": len(ready_ids),
         "rate": _rate(len(annotation_ids), len(ready_ids)), "path": "/knowledge-hub?view=notes"},
        {"key": "knowledge", "label": "已形成知识对象", "value": len(knowledge_book_ids), "total": len(ready_ids),
         "rate": _rate(len(knowledge_book_ids), len(ready_ids)), "path": "/knowledge-hub?view=notes"},
    ]

    since = datetime.now() - timedelta(days=days)
    imports = _daily_counts(db, Book, Book.created_at, since)
    annotations = _daily_counts(db, Annotation, Annotation.created_at, since)
    notes = _daily_counts(db, KnowledgeNote, KnowledgeNote.created_at, since)
    evidence = _daily_counts(db, EvidenceCard, EvidenceCard.created_at, since)
    reports = _daily_counts(db, StudyReport, StudyReport.created_at, since)
    writing = _daily_counts(db, WritingOutput, WritingOutput.created_at, since)
    decks = _daily_counts(db, PresentationDeck, PresentationDeck.created_at, since)
    activity = []
    for offset in range(days - 1, -1, -1):
        day = (datetime.now() - timedelta(days=offset)).date().isoformat()
        activity.append({
            "date": day,
            "imports": imports.get(day, 0),
            "evidence": annotations.get(day, 0),
            "knowledge": notes.get(day, 0) + evidence.get(day, 0),
            "outputs": reports.get(day, 0) + writing.get(day, 0) + decks.get(day, 0),
        })

    verification_rows = dict(db.execute(
        select(EvidenceCard.verification_status, func.count(EvidenceCard.id))
        .group_by(EvidenceCard.verification_status)
    ).all())
    evidence_audit = {
        key: int(verification_rows.get(key, 0) or 0)
        for key in ("supported", "partial", "needs_review", "unsupported")
    }

    recent_outputs = []
    for report in db.scalars(select(StudyReport).order_by(StudyReport.created_at.desc()).limit(5)).all():
        recent_outputs.append({"type": "report", "label": "研究报告", "title": report.focus or "综合研读报告",
                               "created_at": report.created_at, "path": "/knowledge-hub?view=study"})
    for output in db.scalars(select(WritingOutput).order_by(WritingOutput.created_at.desc()).limit(5)).all():
        recent_outputs.append({"type": "writing", "label": "写作输出", "title": output.title,
                               "created_at": output.created_at, "path": "/writing"})
    for deck in db.scalars(select(PresentationDeck).order_by(PresentationDeck.created_at.desc()).limit(5)).all():
        recent_outputs.append({"type": "deck", "label": "PPTX", "title": deck.title,
                               "created_at": deck.created_at, "path": "/literature-workbench?tab=records"})
    recent_outputs.sort(key=lambda item: item["created_at"] or datetime.min, reverse=True)
    for item in recent_outputs:
        value = item["created_at"]
        item["created_at"] = value.isoformat() if value else None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "overview": {
            "book_count": book_count,
            "ready_count": len(ready_ids),
            "health_score": int(health.get("score", 100)),
            "knowledge_object_count": knowledge_object_count,
            "traceable_rate": _rate(traced_notes + traced_evidence, traceable_total),
            "report_count": report_count,
        },
        "coverage": coverage,
        "health": {
            "issue_count": int(health.get("issue_count", 0)),
            "healthy_book_count": int(health.get("healthy_book_count", 0)),
            "categories": health_categories,
            "items": health.get("items", [])[:8],
        },
        "knowledge": {
            "annotations": annotation_count, "notes": note_count, "evidence_cards": evidence_count,
            "tree_nodes": node_count, "traceable_objects": traced_notes + traced_evidence,
            "evidence_audit": evidence_audit,
        },
        "outputs": {
            "reports": report_count, "writing": writing_count, "decks": deck_count,
            "finished_decks": finished_decks, "recent": recent_outputs[:8],
        },
        "activity": activity,
        "boundary": "本页衡量知识库结构、来源链和研究产出，不以在线时长、连续打卡或 AI 生成数量评价学习质量。",
    }
