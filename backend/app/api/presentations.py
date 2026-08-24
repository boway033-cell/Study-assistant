"""章节/选段到中文可编辑 PPTX 的产品 API。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.models import Book, PresentationDeck
from backend.app.services.presentation_deck import collect_selection, generate_deck_task
from backend.app.worker.tasks import submit

router = APIRouter(prefix="/api/presentations", tags=["presentations"])


class DeckGenerateReq(BaseModel):
    book_id: int
    chapter_ids: list[int] = Field(default_factory=list)
    chunk_ids: list[int] = Field(default_factory=list)
    selected_text: str = Field(default="", max_length=20000)
    audience: str = Field(default="课题组", max_length=50)
    purpose: str = Field(default="文献精读汇报", max_length=100)
    duration_minutes: int = Field(default=15, ge=5, le=90)
    slide_count: int = Field(default=12, ge=6, le=18)
    include_figures: bool = True


def _resp(row: PresentationDeck) -> dict:
    def load(raw, fallback):
        try: return json.loads(raw) if raw else fallback
        except (ValueError, TypeError): return fallback
    return {"id": row.id, "book_id": row.book_id, "title": row.title, "status": row.status,
            "paper_type": row.paper_type, "selection": load(row.selection_json, {}),
            "options": load(row.options_json, {}), "outline": load(row.outline_json, []),
            "manifest": load(row.manifest_json, {}), "qa": load(row.qa_json, {}),
            "download_ready": bool(row.file_path and row.status == "done"), "error_msg": row.error_msg,
            "created_at": row.created_at, "updated_at": row.updated_at}


@router.post("/generate", status_code=202)
def generate(req: DeckGenerateReq, db: Session = Depends(get_db)):
    book = db.get(Book, req.book_id)
    if not book or book.status != "ready":
        raise HTTPException(400, "文献尚未完成解析")
    try:
        selection = collect_selection(db, req.book_id, req.chapter_ids, req.chunk_ids, req.selected_text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    options = req.model_dump(exclude={"book_id", "chapter_ids", "chunk_ids", "selected_text"})
    deck = PresentationDeck(book_id=book.id, title=f"{book.title}｜中文文献汇报",
                            selection_json=json.dumps(selection, ensure_ascii=False),
                            options_json=json.dumps(options, ensure_ascii=False), status="pending")
    db.add(deck); db.commit(); db.refresh(deck)
    task = submit("deck", lambda rec: generate_deck_task(rec, deck.id), book_id=book.id)
    return {"deck_id": deck.id, "task_id": task.id}


@router.get("")
def list_decks(book_id: int | None = None, db: Session = Depends(get_db)):
    q = select(PresentationDeck)
    if book_id: q = q.where(PresentationDeck.book_id == book_id)
    return [_resp(x) for x in db.scalars(q.order_by(PresentationDeck.created_at.desc())).all()]


@router.get("/{deck_id}")
def get_deck(deck_id: int, db: Session = Depends(get_db)):
    row = db.get(PresentationDeck, deck_id)
    if not row: raise HTTPException(404, "汇报不存在")
    return _resp(row)


@router.get("/{deck_id}/download")
def download_deck(deck_id: int, db: Session = Depends(get_db)):
    row = db.get(PresentationDeck, deck_id)
    if not row or row.status != "done" or not row.file_path: raise HTTPException(404, "汇报文件尚未生成")
    path = (settings.presentations_dir / Path(row.file_path).name).resolve()
    if path.parent != settings.presentations_dir.resolve() or not path.exists(): raise HTTPException(404, "汇报文件不存在")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        filename=f"{row.title}.pptx")


@router.delete("/{deck_id}", status_code=204)
def delete_deck(deck_id: int, db: Session = Depends(get_db)):
    row = db.get(PresentationDeck, deck_id)
    if not row: raise HTTPException(404, "汇报不存在")
    path = settings.presentations_dir / Path(row.file_path).name if row.file_path else None
    db.delete(row); db.commit()
    if path and path.exists(): path.unlink()
