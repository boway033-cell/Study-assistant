"""章节/选段到中文可编辑 PPTX 的产品 API。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.models import (Book, Chunk, EvidenceCard, KnowledgeNote, PresentationDeck,
                                StudyReport)
from backend.app.services.presentation_deck import (audit_claim_sources, collect_multi_selection,
    generate_deck_task, generate_outline_task, render_deck_task, source_fingerprint, validate_outline)
from backend.app.worker.tasks import submit

router = APIRouter(prefix="/api/presentations", tags=["presentations"])


class DeckGenerateReq(BaseModel):
    book_id: int
    source_book_ids: list[int] = Field(default_factory=list, max_length=50)
    source_report_id: int | None = Field(default=None, ge=1)
    report_ids: list[int] = Field(default_factory=list, max_length=20)
    knowledge_note_ids: list[int] = Field(default_factory=list, max_length=80)
    evidence_card_ids: list[int] = Field(default_factory=list, max_length=80)
    chapter_ids: list[int] = Field(default_factory=list)
    chunk_ids: list[int] = Field(default_factory=list)
    resource_ids: list[int] = Field(default_factory=list)
    selected_text: str = Field(default="", max_length=20000)
    audience: str = Field(default="课题组", max_length=50)
    purpose: str = Field(default="文献精读汇报", max_length=100)
    duration_minutes: int = Field(default=15, ge=5, le=90)
    slide_count: int = Field(default=12, ge=6, le=18)
    include_figures: bool = True
    max_source_chars: int = Field(default=52000, ge=8000, le=100000)
    use_scope: str = Field(default="personal", pattern=r"^(personal|internal|public|commercial)$")
    rights_acknowledged: bool = False


class OutlineSlide(BaseModel):
    title: str = Field(min_length=1, max_length=70)
    kind: str = Field(default="content", max_length=20)
    claim: str = Field(default="", max_length=220)
    bullets: list[str] = Field(default_factory=list, max_length=4)
    source_ids: list[str] = Field(default_factory=list, max_length=20)


class OutlineUpdateReq(BaseModel):
    slides: list[OutlineSlide] = Field(min_length=2, max_length=24)


class RenderReq(BaseModel):
    use_scope: str = Field(default="personal", pattern=r"^(personal|internal|public|commercial)$")
    include_figures: bool = True
    rights_acknowledged: bool = False
    confirm_unsupported_claims: bool = False


def _resp(row: PresentationDeck, *, detail: bool = True) -> dict:
    def load(raw, fallback):
        try: return json.loads(raw) if raw else fallback
        except (ValueError, TypeError): return fallback
    qa = load(row.qa_json, {})
    visual = qa.get("visual", {}) if isinstance(qa, dict) else {}
    selection = load(row.selection_json, {})
    outline = load(row.outline_json, [])
    response = {"id": row.id, "book_id": row.book_id, "title": row.title, "status": row.status,
            "paper_type": row.paper_type,
            "coverage": selection.get("coverage", {}),
            "source_book_count": len(selection.get("books", [])),
            "slide_count": len(outline),
            "outline_editable": row.status in {"outline_ready", "done"},
            "preview_count": visual.get("slide_count", 0) if visual.get("ok") else 0,
            "download_ready": bool(row.file_path and row.status == "done"), "error_msg": row.error_msg,
            "created_at": row.created_at, "updated_at": row.updated_at}
    if detail:
        response.update({"selection": selection, "options": load(row.options_json, {}),
                         "outline": outline, "manifest": load(row.manifest_json, {}), "qa": qa})
    return response


def _source_freshness(db: Session, selection: dict) -> dict:
    changed, missing = [], []
    checked = 0
    legacy = False
    for source in selection.get("sources", []) if isinstance(selection.get("sources"), list) else []:
        source_id = str(source.get("source_id") or "")
        expected = source.get("snapshot_hash")
        if source_id == "selection:user":
            continue
        try:
            kind, raw_id = source_id.split(":", 1)
            object_id = int(raw_id)
        except (ValueError, TypeError):
            legacy = True
            continue
        model = {"chunk": Chunk, "note": KnowledgeNote, "evidence": EvidenceCard,
                 "report": StudyReport}.get(kind)
        current = db.get(model, object_id) if model else None
        if not current:
            missing.append({"source_id": source_id, "title": source.get("chapter_title")})
            continue
        if not expected:
            legacy = True
            continue
        checked += 1
        if kind == "chunk":
            actual = source_fingerprint(current.content, current.page_start, current.page_end, current.chapter_id)
        elif kind == "note":
            actual = source_fingerprint(current.title, current.content, current.source_refs_json)
        elif kind == "evidence":
            actual = source_fingerprint(current.title, current.claim_text, current.evidence_text,
                                        current.source_ref_json, current.verification_status)
        else:
            actual = source_fingerprint(current.focus, current.content, current.claims_json,
                                        current.selection_json)
        if actual != expected:
            changed.append({"source_id": source_id, "title": source.get("chapter_title")})
    if missing:
        status, message = "missing", "部分取材对象已被删除，PPTX 中的主张和引用需要重新生成。"
    elif changed:
        status, message = "stale", "取材对象在生成后发生变化，请重新生成提纲或 PPTX。"
    elif checked:
        status, message = "fresh", "已选知识对象和文献片段与生成时一致。"
    elif legacy:
        status, message = "unknown", "旧版汇报没有来源指纹，无法自动判断是否过期。"
    else:
        status, message = "not_applicable", "没有可复核的来源快照。"
    return {"status": status, "message": message, "checked_sources": checked,
            "changed": changed, "missing": missing}


@router.post("/generate", status_code=202)
def generate(req: DeckGenerateReq, db: Session = Depends(get_db)):
    book = db.get(Book, req.book_id)
    if not book or book.status != "ready":
        raise HTTPException(400, "文献尚未完成解析")
    try:
        selection = collect_multi_selection(db, req.book_id, req.source_book_ids, req.chapter_ids,
                                            req.chunk_ids, req.selected_text, req.resource_ids,
                                            req.max_source_chars, req.source_report_id,
                                            req.knowledge_note_ids, req.evidence_card_ids, req.report_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    options = req.model_dump(exclude={"book_id", "source_book_ids", "source_report_id", "report_ids", "knowledge_note_ids", "evidence_card_ids", "chapter_ids", "chunk_ids", "resource_ids", "selected_text"})
    deck_title = f"{book.title} 等 {len(selection.get('books', []))} 本｜中文文献汇报" if len(selection.get("books", [])) > 1 else f"{book.title}｜中文文献汇报"
    deck = PresentationDeck(book_id=book.id, title=deck_title,
                            selection_json=json.dumps(selection, ensure_ascii=False),
                            options_json=json.dumps(options, ensure_ascii=False), status="pending")
    db.add(deck); db.commit(); db.refresh(deck)
    task = submit("deck", lambda rec: generate_deck_task(rec, deck.id), book_id=book.id)
    return {"deck_id": deck.id, "task_id": task.id}


@router.post("/outline", status_code=202)
def create_outline(req: DeckGenerateReq, db: Session = Depends(get_db)):
    book = db.get(Book, req.book_id)
    if not book or book.status != "ready":
        raise HTTPException(400, "文献尚未完成解析")
    try:
        selection = collect_multi_selection(db, req.book_id, req.source_book_ids, req.chapter_ids,
                                            req.chunk_ids, req.selected_text, req.resource_ids,
                                            req.max_source_chars, req.source_report_id,
                                            req.knowledge_note_ids, req.evidence_card_ids, req.report_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    options = req.model_dump(exclude={"book_id", "source_book_ids", "source_report_id", "report_ids", "knowledge_note_ids", "evidence_card_ids", "chapter_ids", "chunk_ids", "resource_ids", "selected_text"})
    deck_title = f"{book.title} 等 {len(selection.get('books', []))} 本｜中文文献汇报" if len(selection.get("books", [])) > 1 else f"{book.title}｜中文文献汇报"
    deck = PresentationDeck(book_id=book.id, title=deck_title,
                            selection_json=json.dumps(selection, ensure_ascii=False),
                            options_json=json.dumps(options, ensure_ascii=False), status="pending")
    db.add(deck); db.commit(); db.refresh(deck)
    task = submit("deck_outline", lambda record: generate_outline_task(record, deck.id), book_id=book.id)
    return {"deck_id": deck.id, "task_id": task.id, "coverage": selection.get("coverage", {})}


@router.patch("/{deck_id}/outline")
def update_outline(deck_id: int, req: OutlineUpdateReq, db: Session = Depends(get_db)):
    deck = db.get(PresentationDeck, deck_id)
    if not deck:
        raise HTTPException(404, "汇报不存在")
    try:
        selection = json.loads(deck.selection_json or "{}")
        outline = validate_outline([slide.model_dump() for slide in req.slides], selection.get("sources", []),
                                   selection.get("knowledge_source_ids", []))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    claim_audit = audit_claim_sources(outline, selection.get("sources", []))
    deck.outline_json = json.dumps(outline, ensure_ascii=False)
    deck.qa_json = json.dumps({"stage": "outline", "claim_source": claim_audit,
                               "coverage": selection.get("coverage", {})}, ensure_ascii=False)
    deck.status = "outline_ready"; deck.error_msg = None; db.commit(); db.refresh(deck)
    return _resp(deck)


@router.post("/{deck_id}/render", status_code=202)
def render(deck_id: int, req: RenderReq, db: Session = Depends(get_db)):
    deck = db.get(PresentationDeck, deck_id)
    if not deck:
        raise HTTPException(404, "汇报不存在")
    if not deck.outline_json:
        raise HTTPException(409, "请先生成并确认提纲")
    options = json.loads(deck.options_json or "{}")
    options.update(req.model_dump()); deck.options_json = json.dumps(options, ensure_ascii=False)
    deck.status = "pending"; deck.error_msg = None; db.commit()
    task = submit("deck_render", lambda record: render_deck_task(record, deck.id), book_id=deck.book_id)
    return {"deck_id": deck.id, "task_id": task.id}


@router.get("")
def list_decks(book_id: int | None = None, page: int = 1, page_size: int = 20,
               db: Session = Depends(get_db)):
    q = select(PresentationDeck)
    if book_id: q = q.where(PresentationDeck.book_id == book_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(PresentationDeck.created_at.desc())
                      .offset((max(page, 1) - 1) * min(max(page_size, 1), 100))
                      .limit(min(max(page_size, 1), 100))).all()
    return {"total": total, "page": max(page, 1), "page_size": min(max(page_size, 1), 100),
            "items": [_resp(row, detail=False) for row in rows]}


@router.get("/{deck_id}")
def get_deck(deck_id: int, db: Session = Depends(get_db)):
    row = db.get(PresentationDeck, deck_id)
    if not row: raise HTTPException(404, "汇报不存在")
    response = _resp(row)
    response["source_freshness"] = _source_freshness(db, response.get("selection", {}))
    return response


@router.get("/{deck_id}/download")
def download_deck(deck_id: int, db: Session = Depends(get_db)):
    row = db.get(PresentationDeck, deck_id)
    if not row or row.status != "done" or not row.file_path: raise HTTPException(404, "汇报文件尚未生成")
    path = (settings.presentations_dir / Path(row.file_path).name).resolve()
    if path.parent != settings.presentations_dir.resolve() or not path.exists(): raise HTTPException(404, "汇报文件不存在")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        filename=f"{row.title}.pptx")


@router.get("/{deck_id}/preview/{slide_no}")
def preview_slide(deck_id: int, slide_no: int, db: Session = Depends(get_db)):
    if slide_no < 1 or not db.get(PresentationDeck, deck_id):
        raise HTTPException(404, "预览不存在")
    folder = (settings.presentations_dir / "previews" / str(deck_id)).resolve()
    root = (settings.presentations_dir / "previews").resolve()
    if folder.parent != root or not folder.exists():
        raise HTTPException(404, "预览尚未生成")
    files = sorted((path for path in folder.iterdir() if path.suffix.lower() == ".png"),
                   key=lambda path: int("".join(filter(str.isdigit, path.stem)) or 10**9))
    if slide_no > len(files):
        raise HTTPException(404, "预览页不存在")
    return FileResponse(files[slide_no - 1], media_type="image/png")


@router.delete("/{deck_id}", status_code=204)
def delete_deck(deck_id: int, db: Session = Depends(get_db)):
    row = db.get(PresentationDeck, deck_id)
    if not row: raise HTTPException(404, "汇报不存在")
    path = settings.presentations_dir / Path(row.file_path).name if row.file_path else None
    preview_dir = settings.presentations_dir / "previews" / str(row.id)
    db.delete(row); db.commit()
    if path and path.exists(): path.unlink()
    if preview_dir.exists():
        shutil.rmtree(preview_dir)
