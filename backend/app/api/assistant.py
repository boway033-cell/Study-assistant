"""Shelf and project assistant: source scope, preparation status, confirmed memory."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.api.sensemaking import _reading_estimate, _research_config, _run_reading
from backend.app.api.settings import get_ai_price_rates
from backend.app.core.database import get_db
from backend.app.models import AssistantMemory, AssistantProject, Book, Chunk, ImportTask
from backend.app.services.llm import load_llm_config
from backend.app.services import sensemaking as sm
from backend.app.services.assistant_context import current_reading, latest_reading, reading_sources
from backend.app.services.assistant_scope import resolve_scope
from backend.app.services import research_archive as archive
from backend.app.worker.tasks import has_active_task, submit

router = APIRouter(prefix="/api/assistant", tags=["assistant"])
ScopeType = Literal["shelf", "project"]
MemoryKind = Literal["goal", "opinion", "question", "preference"]


class ProjectWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(default="", max_length=2000)
    shelf_ids: list[int] = Field(default_factory=list, max_length=30)
    book_ids: list[int] = Field(default_factory=list, max_length=100)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    goal: str | None = Field(default=None, max_length=2000)
    shelf_ids: list[int] | None = Field(default=None, max_length=30)
    book_ids: list[int] | None = Field(default=None, max_length=100)


class PrepareReq(BaseModel):
    scope_type: ScopeType
    scope_id: int = Field(gt=0)
    book_ids: list[int] = Field(min_length=1, max_length=8)
    acknowledged: bool = False


class MemoryWrite(BaseModel):
    scope_type: ScopeType
    scope_id: int = Field(gt=0)
    kind: MemoryKind
    content: str = Field(min_length=2, max_length=2000)


class MemoryPatch(BaseModel):
    kind: MemoryKind | None = None
    content: str | None = Field(default=None, min_length=2, max_length=2000)


def _project_row(row: AssistantProject) -> dict:
    return {"id": row.id, "name": row.name, "goal": row.goal,
            "shelf_ids": sorted(shelf.id for shelf in row.shelves),
            "book_ids": sorted(book.id for book in row.books),
            "created_at": row.created_at.isoformat()}


def _set_project_sources(db: Session, row: AssistantProject,
                         shelf_ids: list[int] | None, book_ids: list[int] | None) -> None:
    from backend.app.models import Shelf
    if shelf_ids is not None:
        unique = set(shelf_ids)
        shelves = db.scalars(select(Shelf).where(Shelf.id.in_(unique))).all() if unique else []
        if len(shelves) != len(unique):
            raise HTTPException(400, "项目引用了不存在的书架")
        row.shelves = shelves
    if book_ids is not None:
        unique = set(book_ids)
        books = db.scalars(select(Book).where(Book.id.in_(unique))).all() if unique else []
        if len(books) != len(unique):
            raise HTTPException(400, "项目引用了不存在的资料")
        row.books = books


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    rows = db.scalars(select(AssistantProject).order_by(AssistantProject.updated_at.desc(),
                                                       AssistantProject.id.desc())).all()
    return [_project_row(row) for row in rows]


@router.post("/projects", status_code=201)
def create_project(req: ProjectWrite, db: Session = Depends(get_db)):
    if not req.name.strip():
        raise HTTPException(422, "项目名称不能为空")
    row = AssistantProject(name=req.name.strip(), goal=req.goal.strip())
    _set_project_sources(db, row, req.shelf_ids, req.book_ids)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _project_row(row)


@router.patch("/projects/{project_id}")
def update_project(project_id: int, req: ProjectPatch, db: Session = Depends(get_db)):
    row = db.get(AssistantProject, project_id)
    if row is None:
        raise HTTPException(404, "项目不存在")
    values = req.model_dump(exclude_unset=True)
    if "name" in values:
        if not values["name"].strip():
            raise HTTPException(422, "项目名称不能为空")
        row.name = values["name"].strip()
    if "goal" in values:
        row.goal = values["goal"].strip()
    _set_project_sources(db, row, values.get("shelf_ids"), values.get("book_ids"))
    row.updated_at = datetime.now()
    db.commit()
    db.refresh(row)
    return _project_row(row)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    row = db.get(AssistantProject, project_id)
    if row is None:
        raise HTTPException(404, "项目不存在")
    archive.delete_scope_archive(db, "project", project_id)
    db.delete(row)
    db.commit()


def _book_state(db: Session, book: Book, chunk_count: int, text_count: int,
                task: ImportTask | None) -> dict:
    artifact = latest_reading(db, book.id)
    current = current_reading(db, book.id) if artifact else None
    state = "ready_to_prepare"
    if book.status != "ready":
        state = "needs_source"
    elif not text_count:
        state = "no_text"
    elif task and task.status in {"pending", "running", "cancelling"}:
        state = "processing"
    elif current:
        state = "prepared"
    elif artifact:
        state = "stale"
    elif task and task.status == "failed":
        state = "failed"
    try:
        coverage = json.loads(current.payload_json).get("coverage", {}) if current else {}
    except (ValueError, TypeError, AttributeError):
        coverage = {}
    return {"book_id": book.id, "title": book.title, "source_status": book.status,
            "chunk_count": chunk_count, "text_chunk_count": text_count, "state": state,
            "task_id": task.id if task and state in {"processing", "failed"} else None,
            "task_progress": task.progress if task and state == "processing" else None,
            "task_error": task.error if task and state == "failed" else None,
            "artifact_id": current.id if current else None,
            "coverage": coverage}


@router.get("/status")
def assistant_status(scope_type: ScopeType, scope_id: int, db: Session = Depends(get_db)):
    book_ids, name = resolve_scope(db, scope_type, scope_id)
    if not book_ids:
        return {"scope_type": scope_type, "scope_id": scope_id, "name": name,
                "total": 0, "prepared": 0, "books": []}
    books = {book.id: book for book in db.scalars(select(Book).where(Book.id.in_(book_ids))).all()}
    counts = dict(db.execute(select(Chunk.book_id, func.count()).where(Chunk.book_id.in_(book_ids))
                             .group_by(Chunk.book_id)).all())
    text_counts = dict(db.execute(select(Chunk.book_id, func.count()).where(
        Chunk.book_id.in_(book_ids), func.length(func.trim(Chunk.content)) > 0)
        .group_by(Chunk.book_id)).all())
    tasks = {}
    for task in db.scalars(select(ImportTask).where(
        ImportTask.book_id.in_(book_ids),
        ImportTask.name.in_(["assistant_reading", "understanding"]))
        .order_by(ImportTask.created_at.desc(), ImportTask.id.desc())).all():
        tasks.setdefault(task.book_id, task)
    rows = [_book_state(db, books[book_id], counts.get(book_id, 0),
                        text_counts.get(book_id, 0), tasks.get(book_id))
            for book_id in book_ids if book_id in books]
    return {"scope_type": scope_type, "scope_id": scope_id, "name": name,
            "total": len(rows), "prepared": sum(row["state"] == "prepared" for row in rows),
            "books": rows}


@router.get("/prepare/estimate")
def prepare_estimate(scope_type: ScopeType, scope_id: int, db: Session = Depends(get_db)):
    status = assistant_status(scope_type, scope_id, db)
    cfg = load_llm_config(db, "research")
    price = get_ai_price_rates(db)
    rate = price["rates"].get(f"{cfg.get('provider_id')}:{cfg.get('model')}", {})
    rate = rate if isinstance(rate, dict) else {}
    max_rate = max(float(rate.get("input") or 0), float(rate.get("output") or 0))
    items = []
    for row in status["books"]:
        if row["state"] not in {"ready_to_prepare", "stale", "failed"}:
            continue
        estimate = _reading_estimate(sm.reading_windows(db, row["book_id"]))
        items.append({"book_id": row["book_id"], "title": row["title"], **estimate,
                      "estimated_cost_cny": round(estimate["estimated_tokens"] * max_rate / 1_000_000, 2)
                      if max_rate else None})
    return {"scope_type": scope_type, "scope_id": scope_id, "books": items,
            "total_calls": sum(item["estimated_calls"] for item in items),
            "total_tokens": sum(item["estimated_tokens"] for item in items),
            "provider": cfg.get("provider_name"), "model": cfg.get("model"),
            "estimated_cost_cny": round(sum(item["estimated_tokens"] for item in items) * max_rate / 1_000_000, 2)
            if max_rate else None,
            "cost_notice": "按用户填写的输入/输出较高费率粗估；未填写费率时不显示金额，实际以供应商账单为准。",
            "batch_limit": 8}


@router.post("/prepare")
def prepare_scope(req: PrepareReq, db: Session = Depends(get_db)):
    if not req.acknowledged:
        raise HTTPException(400, "请先查看预计调用量并确认本批全文研读")
    scope_ids, _ = resolve_scope(db, req.scope_type, req.scope_id)
    if len(set(req.book_ids)) != len(req.book_ids) or not set(req.book_ids).issubset(scope_ids):
        raise HTTPException(400, "所选资料不在当前范围内或有重复")
    _research_config(db)
    for book_id in req.book_ids:
        book = db.get(Book, book_id)
        if not book or book.status != "ready":
            raise HTTPException(409, f"资料 {book_id} 尚未完成解析")
        if current_reading(db, book_id):
            raise HTTPException(409, f"《{book.title}》已有当前版本的全文理解")
        if has_active_task("understanding", book_id) or has_active_task("assistant_reading", book_id):
            raise HTTPException(409, f"《{book.title}》已有理解任务在运行")
        estimate = _reading_estimate(sm.reading_windows(db, book_id))
        if not estimate["window_count"] or not estimate["within_budget"]:
            raise HTTPException(409, f"《{book.title}》没有可读正文或超过当前单任务预算")
    jobs = []
    for book_id in req.book_ids:
        record = submit("assistant_reading", lambda task, bid=book_id: _run_reading(task, bid, ""),
                        book_id=book_id)
        jobs.append({"book_id": book_id, "task_id": record.id})
    return {"jobs": jobs}


@router.get("/connections")
def assistant_connections(scope_type: ScopeType, scope_id: int, db: Session = Depends(get_db)):
    """Show source-bound passages that may answer a saved user question."""
    book_ids, _ = resolve_scope(db, scope_type, scope_id)
    book_ids = db.scalars(select(Book.id).where(Book.id.in_(book_ids), Book.status == "ready")).all() if book_ids else []
    payloads = {}
    for book_id in book_ids:
        artifact = current_reading(db, book_id)
        if artifact:
            try:
                payloads[book_id] = json.loads(artifact.payload_json)
            except (ValueError, TypeError):
                pass
    if not payloads:
        return []
    if scope_type == "shelf":
        criterion = AssistantMemory.shelf_id == scope_id
    else:
        project = db.get(AssistantProject, scope_id)
        shelf_ids = [shelf.id for shelf in project.shelves]
        criterion = or_(AssistantMemory.project_id == scope_id,
                        AssistantMemory.shelf_id.in_(shelf_ids))
    memories = db.scalars(select(AssistantMemory).where(
        criterion, AssistantMemory.kind.in_(["question", "opinion"]))
        .order_by(AssistantMemory.id.desc()).limit(12)).all()
    result = []
    for memory in memories:
        matches = reading_sources(db, book_ids, memory.content, [], limit=1,
                                  reading_payloads=payloads)
        if not matches:
            continue
        source = matches[0]
        if source.get("matching_score", 0) < 1:
            continue
        result.append({"memory_id": memory.id, "memory_kind": memory.kind,
                       "memory": memory.content, "source": {
                           "book_id": source["book_id"], "chunk_id": source["chunk_id"],
                           "book_title": source["book_title"], "chapter_title": source["chapter_title"],
                           "page_start": source["page_start"], "page_end": source["page_end"],
                           "snippet": source["snippet"]},
                       "status": "possible_link"})
        if len(result) >= 8:
            break
    return result


def _memory_row(row: AssistantMemory) -> dict:
    return {"id": row.id, "scope_type": "shelf" if row.shelf_id else "project",
            "scope_id": row.shelf_id or row.project_id, "kind": row.kind,
            "content": row.content, "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat()}


@router.get("/memories")
def list_memories(scope_type: ScopeType, scope_id: int, db: Session = Depends(get_db)):
    resolve_scope(db, scope_type, scope_id)
    criterion = AssistantMemory.shelf_id == scope_id if scope_type == "shelf" else AssistantMemory.project_id == scope_id
    rows = db.scalars(select(AssistantMemory).where(criterion)
                      .order_by(AssistantMemory.id.desc()).limit(200)).all()
    return [_memory_row(row) for row in rows]


@router.post("/memories", status_code=201)
def create_memory(req: MemoryWrite, db: Session = Depends(get_db)):
    if not req.content.strip():
        raise HTTPException(422, "记忆内容不能为空")
    resolve_scope(db, req.scope_type, req.scope_id)
    row = AssistantMemory(shelf_id=req.scope_id if req.scope_type == "shelf" else None,
                          project_id=req.scope_id if req.scope_type == "project" else None,
                          kind=req.kind, content=req.content.strip())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _memory_row(row)


@router.patch("/memories/{memory_id}")
def update_memory(memory_id: int, req: MemoryPatch, db: Session = Depends(get_db)):
    row = db.get(AssistantMemory, memory_id)
    if row is None:
        raise HTTPException(404, "记忆不存在")
    if req.kind is not None:
        row.kind = req.kind
    if req.content is not None:
        if not req.content.strip():
            raise HTTPException(422, "记忆内容不能为空")
        row.content = req.content.strip()
    db.commit()
    db.refresh(row)
    return _memory_row(row)


@router.delete("/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: int, db: Session = Depends(get_db)):
    row = db.get(AssistantMemory, memory_id)
    if row is None:
        raise HTTPException(404, "记忆不存在")
    db.delete(row)
    db.commit()


ResearchKind = Literal["question", "judgment", "explanation"]
ResearchReview = Literal["confirmed", "unreviewed", "unclear", "rejected"]
EvidenceRelation = Literal["supports", "challenges", "defines", "limits", "unclear"]


class ResearchScopeReq(BaseModel):
    scope_type: ScopeType
    scope_id: int = Field(gt=0)


class ResearchItemWrite(ResearchScopeReq):
    snapshot_id: int = Field(gt=0)
    kind: ResearchKind
    statement: str = Field(min_length=2, max_length=4000)
    detail: dict = Field(default_factory=dict)
    origin: Literal["user", "ai"] = "user"


class ResearchItemPatch(ResearchScopeReq):
    statement: str | None = Field(default=None, min_length=2, max_length=4000)
    detail: dict | None = None
    review_status: ResearchReview | None = None
    trigger_evidence_id: int | None = Field(default=None, gt=0)


class ResearchEvidenceWrite(ResearchScopeReq):
    book_id: int = Field(gt=0)
    chunk_id: int = Field(gt=0)
    quote: str = Field(min_length=2, max_length=1000)
    relation: EvidenceRelation


@router.post("/research/snapshots", status_code=201)
def create_research_snapshot(req: ResearchScopeReq, db: Session = Depends(get_db)):
    return archive.create_snapshot(db, req.scope_type, req.scope_id)


@router.get("/research/snapshots")
def list_research_snapshots(scope_type: ScopeType, scope_id: int, db: Session = Depends(get_db)):
    return archive.list_snapshots(db, scope_type, scope_id)


@router.get("/research/snapshots/{snapshot_id}/diff")
def research_snapshot_diff(snapshot_id: int, scope_type: ScopeType, scope_id: int,
                           db: Session = Depends(get_db)):
    snapshot = archive.require_snapshot(db, snapshot_id, scope_type, scope_id)
    return archive.snapshot_diff(db, snapshot)


@router.get("/research/items")
def list_research_items(scope_type: ScopeType, scope_id: int, db: Session = Depends(get_db)):
    return archive.list_items(db, scope_type, scope_id)


@router.post("/research/items", status_code=201)
def create_research_item(req: ResearchItemWrite, db: Session = Depends(get_db)):
    snapshot = archive.require_snapshot(db, req.snapshot_id, req.scope_type, req.scope_id)
    return archive.create_item(db, snapshot, req.kind, req.statement, req.detail, req.origin)


@router.get("/research/items/{item_id}")
def get_research_item(item_id: int, scope_type: ScopeType, scope_id: int,
                      db: Session = Depends(get_db)):
    item, snapshot = archive.require_item(db, item_id, scope_type, scope_id)
    return archive.item_row(db, item, snapshot)


@router.patch("/research/items/{item_id}")
def patch_research_item(item_id: int, req: ResearchItemPatch, db: Session = Depends(get_db)):
    item, snapshot = archive.require_item(db, item_id, req.scope_type, req.scope_id)
    if item.kind in {"reading_task", "coach_turn"}:
        raise HTTPException(422, "请使用阅读复核或陪练接口更新该记录")
    changes = req.model_dump(exclude_unset=True, exclude={"scope_type", "scope_id", "trigger_evidence_id"})
    return archive.update_item(db, item, snapshot, changes, req.trigger_evidence_id)


@router.post("/research/items/{item_id}/evidence", status_code=201)
def create_research_evidence(item_id: int, req: ResearchEvidenceWrite,
                             db: Session = Depends(get_db)):
    item, snapshot = archive.require_item(db, item_id, req.scope_type, req.scope_id)
    if item.kind in {"reading_task", "coach_turn"}:
        raise HTTPException(422, "阅读任务和陪练来源由系统核验并绑定")
    return archive.add_evidence(db, item, snapshot, req.book_id, req.chunk_id, req.quote, req.relation)


@router.get("/research/items/{item_id}/revisions")
def research_item_revisions(item_id: int, scope_type: ScopeType, scope_id: int,
                            db: Session = Depends(get_db)):
    item, _ = archive.require_item(db, item_id, scope_type, scope_id)
    return archive.list_revisions(db, item)
