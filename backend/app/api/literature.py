"""Open-access and institutional-browser literature intake API."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.models import Book, LiteratureAccessAttempt, PaperProfile, Setting
from backend.app.services.literature_access import (PROVIDER_CAPABILITIES, build_library_handoff,
    download_verified_pdf, resolve_candidates)
from backend.app.worker.import_task import run_import
from backend.app.worker.tasks import submit

router = APIRouter(prefix="/api/literature", tags=["literature-access"])


class AccessConfigReq(BaseModel):
    library_resource_url: str = Field(default="", max_length=1000)
    unpaywall_email: str = Field(default="", max_length=255)


class ResolveReq(BaseModel):
    query: str = Field(min_length=3, max_length=1200)
    include_si: bool | None = None


class ImportReq(ResolveReq):
    url: str = Field(min_length=8, max_length=2000)
    title: str = Field(default="", max_length=255)
    provider: str = Field(default="direct_oa", max_length=50)


def _get(db, key):
    row = db.get(Setting, key); return row.value if row else ""


def _set(db, key, value):
    row = db.get(Setting, key)
    if row: row.value = value
    else: db.add(Setting(key=key, value=value))


@router.get("/config")
def get_config(db: Session = Depends(get_db)):
    return {"library_resource_url": _get(db, "library_resource_url"),
            "unpaywall_email": _get(db, "unpaywall_email"), "providers": PROVIDER_CAPABILITIES,
            "privacy": "浏览器交接不会读取 Cookie、密码、localStorage 或会话文件。"}


@router.put("/config")
def update_config(req: AccessConfigReq, db: Session = Depends(get_db)):
    if req.library_resource_url and not req.library_resource_url.startswith("https://"):
        raise HTTPException(400, "图书馆入口必须是 HTTPS 地址")
    _set(db, "library_resource_url", req.library_resource_url.strip())
    _set(db, "unpaywall_email", req.unpaywall_email.strip()); db.commit()
    return {"ok": True}


@router.post("/resolve")
async def resolve(req: ResolveReq, db: Session = Depends(get_db)):
    if req.include_si is None: raise HTTPException(409, "请先明确是否需要补充材料（Supporting Information）")
    try: candidates = await resolve_candidates(req.query, _get(db, "unpaywall_email"))
    except (ValueError, OSError) as exc: raise HTTPException(400, str(exc)) from exc
    attempt = LiteratureAccessAttempt(query=req.query, include_si=int(req.include_si), route="open_access",
                                      status="resolved" if candidates else "needs_browser",
                                      manifest_json=json.dumps({"version": 1, "include_si": req.include_si,
                                          "candidates": [c.__dict__ for c in candidates], "secrets_included": False}, ensure_ascii=False))
    db.add(attempt); db.commit(); db.refresh(attempt)
    return {"attempt_id": attempt.id, "candidates": [c.__dict__ for c in candidates],
            "next": "download" if candidates else "library_handoff"}


@router.post("/import", status_code=202)
async def import_open_access(req: ImportReq, db: Session = Depends(get_db)):
    if req.include_si is None: raise HTTPException(409, "请先明确是否需要补充材料（Supporting Information）")
    attempt = LiteratureAccessAttempt(query=req.query, include_si=int(req.include_si), route=req.provider, status="downloading")
    db.add(attempt); db.commit(); db.refresh(attempt)
    target = settings.uploads_dir / f"oa-{attempt.id}.pdf"
    try:
        verified = await download_verified_pdf(req.url, target)
        existing = db.scalar(select(Book).where(Book.file_hash == verified["sha256"]).limit(1))
        if existing:
            target.unlink(missing_ok=True); attempt.status = "duplicate"; attempt.book_id = existing.id
            attempt.manifest_json = json.dumps({**verified, "provider": req.provider, "include_si": req.include_si,
                                                 "duplicate": True, "secrets_included": False}, ensure_ascii=False)
            db.commit(); return {"book_id": existing.id, "duplicate": True, "task_id": None}
        book = Book(title=req.title.strip() or Path(req.query).stem[:255] or "开放获取文献", file_path=target.name,
                    file_type="pdf", file_size=verified["bytes"], file_hash=verified["sha256"], status="pending")
        db.add(book); db.flush()
        profile = PaperProfile(book_id=book.id, source_url=verified["resolved_url"], access_route=req.provider,
                               provenance_json=json.dumps({**verified, "provider": req.provider,
                                   "include_si": req.include_si, "secrets_included": False}, ensure_ascii=False))
        db.add(profile); attempt.book_id = book.id; attempt.status = "importing"
        attempt.manifest_json = profile.provenance_json; db.commit(); db.refresh(book)
        task = submit("import", lambda rec: run_import(rec, book.id), book_id=book.id)
        return {"book_id": book.id, "attempt_id": attempt.id, "task_id": task.id, "manifest": verified}
    except Exception as exc:
        target.unlink(missing_ok=True); attempt.status = "failed"; attempt.error_msg = str(exc); db.commit()
        raise HTTPException(400, str(exc)) from exc


@router.post("/library-handoff")
def library_handoff(req: ResolveReq, db: Session = Depends(get_db)):
    if req.include_si is None: raise HTTPException(409, "请先明确是否需要补充材料（Supporting Information）")
    library_url = _get(db, "library_resource_url")
    if not library_url: raise HTTPException(400, "请先在全文获取设置中配置学校图书馆或 CARSI 入口")
    try: url = build_library_handoff(library_url, req.query)
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    attempt = LiteratureAccessAttempt(query=req.query, include_si=int(req.include_si), route="institutional_browser",
                                      status="browser_handoff", manifest_json=json.dumps({"version": 1,
                                          "include_si": req.include_si, "handoff_url": url,
                                          "browser_state_read": False, "secrets_included": False}, ensure_ascii=False))
    db.add(attempt); db.commit(); db.refresh(attempt)
    return {"attempt_id": attempt.id, "url": url,
            "instruction": "请在当前 Chrome 中完成学校登录并合法下载，再回到资料库导入 PDF。应用不会读取浏览器凭据。"}


@router.get("/attempts")
def attempts(db: Session = Depends(get_db)):
    rows = db.scalars(select(LiteratureAccessAttempt).order_by(LiteratureAccessAttempt.created_at.desc()).limit(50)).all()
    return [{"id": r.id, "query": r.query, "include_si": None if r.include_si is None else bool(r.include_si),
             "route": r.route, "status": r.status, "book_id": r.book_id, "error_msg": r.error_msg,
             "manifest": json.loads(r.manifest_json) if r.manifest_json else {}, "created_at": r.created_at} for r in rows]
