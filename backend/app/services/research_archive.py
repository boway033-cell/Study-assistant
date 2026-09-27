"""Immutable scope snapshots and source-bound, user-revisable research records."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models import (AssistantProject, Book, Chunk, ResearchEvidence,
                                ResearchItem, ResearchRevision, ResearchScopeSnapshot)
from backend.app.services import sensemaking as sm
from backend.app.services.assistant_scope import resolve_scope

ITEM_KINDS = {"question", "judgment", "explanation", "concept", "alignment", "counterevidence", "reading_task", "coach_turn"}
REVIEW_STATES = {"confirmed", "unreviewed", "unclear", "rejected"}
EVIDENCE_RELATIONS = {"supports", "challenges", "defines", "limits", "unclear"}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _scope_books(db: Session, scope_type: str, scope_id: int) -> tuple[list[int], list[int]]:
    book_ids, _ = resolve_scope(db, scope_type, scope_id)
    if scope_type == "project":
        project = db.get(AssistantProject, scope_id)
        shelf_ids = sorted(shelf.id for shelf in project.shelves)
    else:
        shelf_ids = [scope_id]
    return sorted(set(book_ids)), shelf_ids


def _versions(db: Session, book_ids: list[int]) -> list[dict]:
    books = {book.id: book for book in db.scalars(
        select(Book).where(Book.id.in_(book_ids))).all()} if book_ids else {}
    result = []
    for book_id in book_ids:
        book = books.get(book_id)
        if not book:
            continue
        text_version = sm.document_digest(db, book_id)
        result.append({"book_id": book_id, "title": book.title,
                       "file_hash": book.file_hash or "",
                       "text_digest": text_version["digest"],
                       "chunk_count": text_version["chunk_count"]})
    return result


def snapshot_row(row: ResearchScopeSnapshot) -> dict:
    return {"id": row.id, "scope_type": row.scope_type, "scope_id": row.scope_id,
            "shelf_ids": json.loads(row.shelf_ids_json),
            "books": json.loads(row.book_versions_json), "digest": row.digest,
            "created_at": row.created_at.isoformat() if row.created_at else None}


def create_snapshot(db: Session, scope_type: str, scope_id: int) -> dict:
    book_ids, shelf_ids = _scope_books(db, scope_type, scope_id)
    versions = _versions(db, book_ids)
    digest = hashlib.sha256(_json({"scope_type": scope_type, "scope_id": scope_id,
                                    "shelf_ids": shelf_ids, "books": versions}).encode("utf-8")).hexdigest()
    existing = db.scalar(select(ResearchScopeSnapshot).where(
        ResearchScopeSnapshot.scope_type == scope_type,
        ResearchScopeSnapshot.scope_id == scope_id,
        ResearchScopeSnapshot.digest == digest).order_by(ResearchScopeSnapshot.id.desc()))
    if existing:
        return snapshot_row(existing)
    row = ResearchScopeSnapshot(scope_type=scope_type, scope_id=scope_id,
                                shelf_ids_json=_json(shelf_ids),
                                book_versions_json=_json(versions), digest=digest)
    db.add(row)
    db.commit()
    db.refresh(row)
    return snapshot_row(row)


def list_snapshots(db: Session, scope_type: str, scope_id: int) -> list[dict]:
    resolve_scope(db, scope_type, scope_id)
    rows = db.scalars(select(ResearchScopeSnapshot).where(
        ResearchScopeSnapshot.scope_type == scope_type,
        ResearchScopeSnapshot.scope_id == scope_id)
        .order_by(ResearchScopeSnapshot.id.desc()).limit(100)).all()
    return [snapshot_row(row) for row in rows]


def require_snapshot(db: Session, snapshot_id: int, scope_type: str, scope_id: int) -> ResearchScopeSnapshot:
    resolve_scope(db, scope_type, scope_id)
    row = db.get(ResearchScopeSnapshot, snapshot_id)
    if row is None or row.scope_type != scope_type or row.scope_id != scope_id:
        raise HTTPException(404, "范围快照不存在")
    return row


def snapshot_diff(db: Session, snapshot: ResearchScopeSnapshot) -> dict:
    current_ids, current_shelves = _scope_books(db, snapshot.scope_type, snapshot.scope_id)
    current = {row["book_id"]: row for row in _versions(db, current_ids)}
    before = {row["book_id"]: row for row in json.loads(snapshot.book_versions_json)}
    added = [current[key] for key in sorted(current.keys() - before.keys())]
    removed = [before[key] for key in sorted(before.keys() - current.keys())]
    changed = [{"before": before[key], "current": current[key]}
               for key in sorted(current.keys() & before.keys()) if current[key] != before[key]]
    return {"snapshot_id": snapshot.id, "added": added, "removed": removed,
            "changed": changed,
            "shelves_changed": current_shelves != json.loads(snapshot.shelf_ids_json),
            "is_current": not (added or removed or changed or
                               current_shelves != json.loads(snapshot.shelf_ids_json))}


def require_item(db: Session, item_id: int, scope_type: str, scope_id: int) -> tuple[ResearchItem, ResearchScopeSnapshot]:
    resolve_scope(db, scope_type, scope_id)
    item = db.get(ResearchItem, item_id)
    snapshot = db.get(ResearchScopeSnapshot, item.snapshot_id) if item else None
    if snapshot is None or snapshot.scope_type != scope_type or snapshot.scope_id != scope_id:
        raise HTTPException(404, "研究条目不存在")
    return item, snapshot


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _evidence_status(db: Session, evidence: ResearchEvidence, version: dict,
                     digest_cache: dict[int, dict]) -> str:
    book = db.get(Book, evidence.book_id)
    chunk = db.get(Chunk, evidence.chunk_id)
    if not book or not chunk or chunk.book_id != evidence.book_id:
        return "missing"
    if sm.source_hash(chunk.content or "") != evidence.chunk_hash:
        return "source_changed"
    if evidence.book_id not in digest_cache:
        digest_cache[evidence.book_id] = sm.document_digest(db, evidence.book_id)
    current = digest_cache[evidence.book_id]
    if (book.file_hash or "") != version.get("file_hash") or current["digest"] != version.get("text_digest"):
        return "document_changed"
    if chunk.page_start is None:
        return "missing_page"
    if chunk.page_start != evidence.page_start or chunk.page_end != evidence.page_end:
        return "source_changed"
    return "current"


def evidence_row(db: Session, row: ResearchEvidence, versions: dict[int, dict],
                 digest_cache: dict[int, dict]) -> dict:
    return {"id": row.id, "item_id": row.item_id, "book_id": row.book_id,
            "chunk_id": row.chunk_id, "book_title": row.book_title,
            "page_start": row.page_start, "page_end": row.page_end,
            "quote": row.quote, "relation": row.relation,
            "review_status": row.review_status,
            "source_status": _evidence_status(db, row, versions.get(row.book_id, {}), digest_cache),
            "created_at": row.created_at.isoformat() if row.created_at else None}


def item_row(db: Session, item: ResearchItem, snapshot: ResearchScopeSnapshot) -> dict:
    versions = {row["book_id"]: row for row in json.loads(snapshot.book_versions_json)}
    evidence = db.scalars(select(ResearchEvidence).where(ResearchEvidence.item_id == item.id)
                          .order_by(ResearchEvidence.id)).all()
    digest_cache: dict[int, dict] = {}
    return {"id": item.id, "snapshot_id": item.snapshot_id, "kind": item.kind,
            "concept_key": item.concept_key, "statement": item.statement,
            "detail": json.loads(item.detail_json), "origin": item.origin,
            "review_status": item.review_status,
            "evidence": [evidence_row(db, row, versions, digest_cache) for row in evidence],
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None}


def list_items(db: Session, scope_type: str, scope_id: int) -> list[dict]:
    resolve_scope(db, scope_type, scope_id)
    snapshots = db.scalars(select(ResearchScopeSnapshot).where(
        ResearchScopeSnapshot.scope_type == scope_type,
        ResearchScopeSnapshot.scope_id == scope_id)).all()
    if not snapshots:
        return []
    by_id = {snapshot.id: snapshot for snapshot in snapshots}
    rows = db.scalars(select(ResearchItem).where(ResearchItem.snapshot_id.in_(by_id))
                      .order_by(ResearchItem.updated_at.desc(), ResearchItem.id.desc()).limit(200)).all()
    return [item_row(db, row, by_id[row.snapshot_id]) for row in rows]


def create_item(db: Session, snapshot: ResearchScopeSnapshot, kind: str,
                statement: str, detail: dict | None = None, origin: str = "user") -> dict:
    if kind not in ITEM_KINDS:
        raise HTTPException(422, "条目类型无效")
    if not statement.strip():
        raise HTTPException(422, "研究条目不能为空")
    if origin not in {"user", "ai"}:
        raise HTTPException(422, "条目来源无效")
    row = ResearchItem(snapshot_id=snapshot.id, kind=kind, statement=statement.strip(),
                       detail_json=_json(detail or {}), origin=origin,
                       review_status="unreviewed" if origin == "ai" else "confirmed")
    db.add(row)
    db.commit()
    db.refresh(row)
    return item_row(db, row, snapshot)


def _item_state(item: ResearchItem) -> dict:
    return {"statement": item.statement, "detail": json.loads(item.detail_json),
            "review_status": item.review_status, "concept_key": item.concept_key}


def update_item(db: Session, item: ResearchItem, snapshot: ResearchScopeSnapshot,
                changes: dict, trigger_evidence_id: int | None = None) -> dict:
    if trigger_evidence_id is not None:
        evidence = db.get(ResearchEvidence, trigger_evidence_id)
        if not evidence or evidence.item_id != item.id:
            raise HTTPException(422, "触发修订的证据不属于该条目")
    if "review_status" in changes:
        if changes["review_status"] not in REVIEW_STATES:
            raise HTTPException(422, "复核状态无效")
        if item.kind == "counterevidence" and changes["review_status"] == "confirmed":
            raise HTTPException(422, "请通过采纳反证并修订判断来确认候选")
        if item.kind in {"concept", "alignment"} and changes["review_status"] == "confirmed":
            evidence_rows = db.scalars(select(ResearchEvidence).where(
                ResearchEvidence.item_id == item.id)).all()
            versions = {row["book_id"]: row for row in json.loads(snapshot.book_versions_json)}
            if not evidence_rows or any(_evidence_status(db, row, versions.get(row.book_id, {}), {}) != "current"
                                        for row in evidence_rows):
                raise HTTPException(409, "义项来源已变化，请重新分析后确认")
    before = _item_state(item)
    if "statement" in changes:
        if changes["statement"] is None or not changes["statement"].strip():
            raise HTTPException(422, "研究条目不能为空")
        item.statement = changes["statement"].strip()
    if "detail" in changes:
        if changes["detail"] is None:
            raise HTTPException(422, "条目详情不能为空")
        item.detail_json = _json(changes["detail"])
    if "review_status" in changes:
        item.review_status = changes["review_status"]
    if "concept_key" in changes:
        concept_key = changes["concept_key"]
        if not isinstance(concept_key, str) or not concept_key.strip() or len(concept_key) > 160:
            raise HTTPException(422, "概念归并键无效")
        item.concept_key = concept_key.strip()
    after = _item_state(item)
    if after != before:
        item.updated_at = datetime.now()
        db.add(ResearchRevision(item_id=item.id, before_json=_json(before),
                                after_json=_json(after),
                                trigger_evidence_id=trigger_evidence_id, actor="user"))
        db.commit()
        db.refresh(item)
    return item_row(db, item, snapshot)


def add_evidence(db: Session, item: ResearchItem, snapshot: ResearchScopeSnapshot,
                 book_id: int, chunk_id: int, quote: str, relation: str) -> dict:
    if relation not in EVIDENCE_RELATIONS:
        raise HTTPException(422, "证据关系无效")
    versions = {row["book_id"]: row for row in json.loads(snapshot.book_versions_json)}
    if book_id not in versions:
        raise HTTPException(422, "证据文献不在该判断的范围快照内")
    chunk = db.get(Chunk, chunk_id)
    if not chunk or chunk.book_id != book_id:
        raise HTTPException(422, "原文块不属于该文献")
    if item.kind in {"concept", "alignment", "counterevidence"} and chunk.page_start is None:
        raise HTTPException(422, "概念与反证证据需要可定位的原页")
    current = sm.document_digest(db, book_id)
    book = db.get(Book, book_id)
    if not book or current["digest"] != versions[book_id]["text_digest"] or (book.file_hash or "") != versions[book_id]["file_hash"]:
        raise HTTPException(409, "来源正文已变化，请新建范围快照")
    if not _normalize(quote) or _normalize(quote) not in _normalize(chunk.content):
        raise HTTPException(422, "短引无法在原文块中核对")
    if item.kind in {"concept", "alignment", "counterevidence"} and len(_normalize(quote)) < 8:
        raise HTTPException(422, "概念与反证短引至少需要八个可核对字符")
    existing = db.scalar(select(ResearchEvidence).where(
        ResearchEvidence.item_id == item.id, ResearchEvidence.book_id == book_id,
        ResearchEvidence.chunk_id == chunk_id, ResearchEvidence.quote == quote.strip(),
        ResearchEvidence.relation == relation))
    if existing:
        return evidence_row(db, existing, versions, {})
    row = ResearchEvidence(item_id=item.id, book_id=book_id, chunk_id=chunk_id,
                           book_title=versions[book_id]["title"],
                           page_start=chunk.page_start, page_end=chunk.page_end,
                           quote=quote.strip(), chunk_hash=sm.source_hash(chunk.content),
                           relation=relation, review_status="confirmed")
    db.add(row)
    db.flush()
    db.add(ResearchRevision(item_id=item.id, before_json=_json({"evidence_added": None}),
                            after_json=_json({"evidence_added": row.id}),
                            trigger_evidence_id=row.id, actor="user"))
    db.commit()
    db.refresh(row)
    return evidence_row(db, row, versions, {})


def list_revisions(db: Session, item: ResearchItem) -> list[dict]:
    rows = db.scalars(select(ResearchRevision).where(ResearchRevision.item_id == item.id)
                      .order_by(ResearchRevision.id.desc())).all()
    return [{"id": row.id, "before": json.loads(row.before_json),
             "after": json.loads(row.after_json),
             "trigger_evidence_id": row.trigger_evidence_id,
             "actor": row.actor,
             "created_at": row.created_at.isoformat() if row.created_at else None}
            for row in rows]


def delete_scope_archive(db: Session, scope_type: str, scope_id: int) -> None:
    """Remove the archive when its owning project or shelf is explicitly deleted."""
    snapshot_ids = list(db.scalars(select(ResearchScopeSnapshot.id).where(
        ResearchScopeSnapshot.scope_type == scope_type,
        ResearchScopeSnapshot.scope_id == scope_id)).all())
    if not snapshot_ids:
        return
    item_ids = list(db.scalars(select(ResearchItem.id).where(
        ResearchItem.snapshot_id.in_(snapshot_ids))).all())
    if item_ids:
        db.execute(delete(ResearchRevision).where(ResearchRevision.item_id.in_(item_ids)))
        db.execute(delete(ResearchEvidence).where(ResearchEvidence.item_id.in_(item_ids)))
        db.execute(delete(ResearchItem).where(ResearchItem.id.in_(item_ids)))
    db.execute(delete(ResearchScopeSnapshot).where(ResearchScopeSnapshot.id.in_(snapshot_ids)))
