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
from backend.app.models import Book, LiteratureAccessAttempt, LiteratureResource, PaperProfile, Setting
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


class BrowserHandoffReq(ResolveReq):
    url: str = Field(min_length=8, max_length=2000)


RESOURCE_ROLES = {"main", "supplementary", "figure", "table", "dataset", "other"}
RIGHTS_STATUSES = {"not_evaluated", "undetermined", "in_copyright", "open_license",
                   "permission_granted", "public_domain", "restricted"}


class ResourceWrite(BaseModel):
    resource_book_id: int | None = None
    role: str = "supplementary"
    title: str = Field(min_length=1, max_length=255)
    source_url: str | None = Field(default=None, max_length=2000)
    license_expression: str | None = Field(default=None, max_length=120)
    rights_statement_uri: str | None = Field(default=None, max_length=1000)
    rights_status: str = "not_evaluated"
    rights_holder: str | None = Field(default=None, max_length=255)
    attribution: str | None = Field(default=None, max_length=2000)
    permission_note: str | None = Field(default=None, max_length=4000)
    allow_reuse: bool = False


class ResourcePatch(BaseModel):
    role: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    source_url: str | None = Field(default=None, max_length=2000)
    license_expression: str | None = Field(default=None, max_length=120)
    rights_statement_uri: str | None = Field(default=None, max_length=1000)
    rights_status: str | None = None
    rights_holder: str | None = Field(default=None, max_length=255)
    attribution: str | None = Field(default=None, max_length=2000)
    permission_note: str | None = Field(default=None, max_length=4000)
    allow_reuse: bool | None = None


def _validate_resource(values: dict, db: Session, owner_book_id: int, resource_book_id: int | None = None) -> None:
    role = values.get("role")
    status = values.get("rights_status")
    if role is not None and role not in RESOURCE_ROLES:
        raise HTTPException(400, "不支持的资源类型")
    if status is not None and status not in RIGHTS_STATUSES:
        raise HTTPException(400, "不支持的权利状态")
    uri = values.get("rights_statement_uri")
    if uri and not (uri.startswith("https://rightsstatements.org/vocab/") or
                    uri.startswith("http://rightsstatements.org/vocab/")):
        raise HTTPException(400, "Rights Statement 必须使用 rightsstatements.org 标准 URI")
    source_url = values.get("source_url")
    if source_url and not source_url.startswith("https://"):
        raise HTTPException(400, "来源地址必须使用 HTTPS")
    linked_id = resource_book_id if resource_book_id is not None else values.get("resource_book_id")
    if linked_id is not None and not db.get(Book, linked_id):
        raise HTTPException(400, "关联资料不存在")
    if not db.get(Book, owner_book_id):
        raise HTTPException(404, "文献不存在")


def _resource_resp(row: LiteratureResource) -> dict:
    return {"id": row.id, "book_id": row.book_id, "resource_book_id": row.resource_book_id,
            "role": row.role, "title": row.title, "source_url": row.source_url,
            "license_expression": row.license_expression,
            "rights_statement_uri": row.rights_statement_uri, "rights_status": row.rights_status,
            "rights_holder": row.rights_holder, "attribution": row.attribution,
            "permission_note": row.permission_note, "allow_reuse": bool(row.allow_reuse),
            "created_at": row.created_at, "updated_at": row.updated_at}


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
            "next": "download" if any(c.direct_download for c in candidates) else "browser_handoff"}


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
        db.add(LiteratureResource(book_id=book.id, resource_book_id=book.id, role="main",
                                  title=book.title, source_url=verified["resolved_url"],
                                  rights_status="not_evaluated", allow_reuse=0,
                                  provenance_json=profile.provenance_json))
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


@router.post("/browser-handoff")
def browser_source_handoff(req: BrowserHandoffReq, db: Session = Depends(get_db)):
    """把已发现的精确全文入口交给用户当前浏览器，不接触登录态。"""
    if req.include_si is None:
        raise HTTPException(409, "请先明确是否需要补充材料（Supporting Information）")
    from backend.app.services.literature_access import validate_public_https_url
    try:
        url = validate_public_https_url(req.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    attempt = LiteratureAccessAttempt(
        query=req.query, include_si=int(req.include_si), route="browser_handoff", status="browser_handoff",
        manifest_json=json.dumps({"version": 1, "include_si": req.include_si,
                                  "source_url": req.query, "handoff_url": url,
                                  "browser_state_read": False, "secrets_included": False}, ensure_ascii=False),
    )
    db.add(attempt); db.commit(); db.refresh(attempt)
    return {"attempt_id": attempt.id, "url": url,
            "instruction": "请在已登录 Chrome 中合法下载 PDF，再将下载文件导入资料库。应用不会读取或保存 Cookie、密码或会话文件。"}


@router.get("/attempts")
def attempts(db: Session = Depends(get_db)):
    rows = db.scalars(select(LiteratureAccessAttempt).order_by(LiteratureAccessAttempt.created_at.desc()).limit(50)).all()
    return [{"id": r.id, "query": r.query, "include_si": None if r.include_si is None else bool(r.include_si),
             "route": r.route, "status": r.status, "book_id": r.book_id, "error_msg": r.error_msg,
             "manifest": json.loads(r.manifest_json) if r.manifest_json else {}, "created_at": r.created_at} for r in rows]


@router.get("/books/{book_id}/resources")
def list_resources(book_id: int, db: Session = Depends(get_db)):
    if not db.get(Book, book_id):
        raise HTTPException(404, "文献不存在")
    rows = db.scalars(select(LiteratureResource).where(LiteratureResource.book_id == book_id)
                      .order_by(LiteratureResource.role, LiteratureResource.created_at)).all()
    return [_resource_resp(row) for row in rows]


@router.post("/books/{book_id}/resources", status_code=201)
def create_resource(book_id: int, req: ResourceWrite, db: Session = Depends(get_db)):
    values = req.model_dump()
    _validate_resource(values, db, book_id)
    values["allow_reuse"] = int(values["allow_reuse"])
    row = LiteratureResource(book_id=book_id, **values)
    db.add(row); db.commit(); db.refresh(row)
    return _resource_resp(row)


@router.patch("/resources/{resource_id}")
def update_resource(resource_id: int, req: ResourcePatch, db: Session = Depends(get_db)):
    row = db.get(LiteratureResource, resource_id)
    if not row:
        raise HTTPException(404, "资源不存在")
    values = req.model_dump(exclude_unset=True)
    _validate_resource(values, db, row.book_id, row.resource_book_id)
    if "allow_reuse" in values:
        values["allow_reuse"] = int(values["allow_reuse"])
    for key, value in values.items():
        setattr(row, key, value)
    db.commit(); db.refresh(row)
    return _resource_resp(row)


@router.delete("/resources/{resource_id}", status_code=204)
def delete_resource(resource_id: int, db: Session = Depends(get_db)):
    row = db.get(LiteratureResource, resource_id)
    if not row:
        raise HTTPException(404, "资源不存在")
    db.delete(row); db.commit()
