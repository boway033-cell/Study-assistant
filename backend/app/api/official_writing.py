"""Official-document workbench API. AI operations require explicit transmission consent."""
from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.app.core.database import get_db
from backend.app.models import Setting, WritingOutput
from backend.app.services import official_writing as service

router = APIRouter(prefix="/api/writing/official", tags=["official-writing"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Brief(StrictRequest):
    title: str = Field(min_length=1, max_length=255)
    genre: str = Field(default="通知", max_length=30)
    issuer: str = Field(default="", max_length=200)
    recipient: str = Field(default="", max_length=300)
    relationship: Literal["上行", "下行", "平行", "内部"] = "内部"
    facts: str = Field(min_length=1, max_length=24000)
    length: int = Field(default=1500, ge=100, le=10000)

    @field_validator("genre")
    @classmethod
    def known_genre(cls, value):
        if value not in service.GENRES:
            raise ValueError("不支持的公文文种")
        return value

    @field_validator("title", "issuer", "recipient")
    @classmethod
    def single_line(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError("字段不能包含换行或控制字符")
        return value


class OutlineRequest(StrictRequest):
    brief: Brief
    allow_model: Literal[True]


class VersionRequest(StrictRequest):
    etag: str = Field(min_length=64, max_length=64)


class TextRequest(VersionRequest):
    title: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=60000)

    @field_validator("title")
    @classmethod
    def single_line(cls, value):
        return Brief.single_line(value)


class ModelRequest(VersionRequest):
    allow_model: Literal[True]


class ReviseRequest(ModelRequest):
    instructions: str = Field(min_length=1, max_length=6000)


class ExportRequest(VersionRequest):
    overrides: dict = Field(default_factory=dict)
    review_acknowledged: Literal[True]


class ProfileRequest(StrictRequest):
    overrides: dict
    etag: str = Field(min_length=64, max_length=64)
    confirmed: Literal[True]


def get_row(db, output_id, etag=None, stage=None):
    row = db.get(WritingOutput, output_id)
    if not row or row.kind != service.KIND:
        raise HTTPException(404, "公文版本不存在")
    if etag is not None and service.serialize(row)["etag"] != etag:
        raise HTTPException(409, "版本已变化，请重新加载后操作")
    if stage and service.audit_of(row).get("stage") != stage:
        raise HTTPException(409, "当前阶段不支持此操作")
    return row


async def generate(db, stage, payload):
    try:
        return await service.model_text(db, stage, payload)
    except asyncio.TimeoutError:
        raise HTTPException(504, "模型响应超时，已保存的版本不受影响") from None
    except Exception:
        # Provider errors can contain headers/URLs; never return them to the client.
        raise HTTPException(502, "生成失败，请检查写作模型配置后重试；已有版本未改动") from None


@router.get("/options")
def options(db: Session = Depends(get_db)):
    return {"genres": service.GENRES, "skills": service.SKILLS, "format": service.profile_record(db)}


@router.put("/profile")
def save_profile(req: ProfileRequest, db: Session = Depends(get_db)):
    current = service.profile_record(db)
    if current["etag"] != req.etag:
        raise HTTPException(409, "默认排版已变化，请重新加载设置")
    try:
        service.checked_profile(req.overrides)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None
    setting = db.get(Setting, service.PROFILE_KEY)
    if setting is None:
        setting = Setting(key=service.PROFILE_KEY, value="{}")
        db.add(setting)
    setting.value = json.dumps(req.overrides, ensure_ascii=False)
    db.commit()
    return service.profile_record(db)


@router.get("/outputs")
def history(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50), db: Session = Depends(get_db)):
    condition = WritingOutput.kind == service.KIND
    total = db.scalar(select(func.count()).select_from(WritingOutput).where(condition)) or 0
    rows = db.scalars(select(WritingOutput).where(condition).order_by(WritingOutput.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{"id": r.id, "title": r.title, "stage": service.audit_of(r).get("stage"),
                       "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows], "total": total}


@router.post("/outline")
async def outline(req: OutlineRequest, db: Session = Depends(get_db)):
    brief = req.brief.model_dump()
    text = await generate(db, "outline", {"brief": brief})
    return service.serialize(service.new_version(db, brief=brief, title=brief["title"], text=text, stage="outline"))


@router.get("/outputs/{output_id}")
def detail(output_id: int, db: Session = Depends(get_db)):
    return service.serialize(get_row(db, output_id))


@router.post("/outputs/{output_id}/confirm")
def confirm(output_id: int, req: TextRequest, db: Session = Depends(get_db)):
    row = get_row(db, output_id, req.etag, "outline")
    brief = {**service.audit_of(row)["brief"], "title": req.title}
    return service.serialize(service.new_version(db, brief=brief, title=req.title, text=req.text,
                                                 stage="outline", parent=row, confirmed=True))


@router.post("/outputs/{output_id}/draft")
async def draft(output_id: int, req: ModelRequest, db: Session = Depends(get_db)):
    row = get_row(db, output_id, req.etag, "outline")
    audit = service.audit_of(row)
    if not audit.get("confirmed"):
        raise HTTPException(409, "请先确认提纲")
    text = await generate(db, "draft", {"brief": audit["brief"], "confirmed_outline": row.output_text})
    return service.serialize(service.new_version(db, brief=audit["brief"], title=row.title, text=text, stage="draft", parent=row))


@router.post("/outputs/{output_id}/save")
def save(output_id: int, req: TextRequest, db: Session = Depends(get_db)):
    row = get_row(db, output_id, req.etag)
    audit = service.audit_of(row)
    brief = {**audit["brief"], "title": req.title}
    return service.serialize(service.new_version(db, brief=brief, title=req.title, text=req.text,
                                                 stage=audit["stage"], parent=row))


@router.post("/outputs/{output_id}/review")
async def review(output_id: int, req: ModelRequest, db: Session = Depends(get_db)):
    row = get_row(db, output_id, req.etag, "draft")
    audit = service.audit_of(row)
    review_text = await generate(db, "review", {"brief": audit["brief"], "text": row.output_text, "checks": audit["checks"]})
    # Refresh after network wait so a concurrent export is not lost.
    db.refresh(row)
    audit = service.audit_of(row)
    audit["review"] = review_text
    row.audit_json = json.dumps(audit, ensure_ascii=False)
    db.commit()
    return service.serialize(row)


@router.post("/outputs/{output_id}/revise")
async def revise(output_id: int, req: ReviseRequest, db: Session = Depends(get_db)):
    row = get_row(db, output_id, req.etag, "draft")
    audit = service.audit_of(row)
    text = await generate(db, "revise", {"brief": audit["brief"], "text": row.output_text,
                                        "instructions": req.instructions, "checks": audit["checks"]})
    revised = service.new_version(db, brief=audit["brief"], title=row.title, text=text, stage="draft", parent=row)
    new_audit = service.audit_of(revised)
    new_audit["revision_instructions"] = req.instructions
    revised.audit_json = json.dumps(new_audit, ensure_ascii=False)
    db.commit()
    return service.serialize(revised)


@router.post("/outputs/{output_id}/export")
async def export(output_id: int, req: ExportRequest, db: Session = Depends(get_db)):
    row = get_row(db, output_id, req.etag, "draft")
    try:
        result = await run_in_threadpool(service.export_word, row, req.overrides)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        raise HTTPException(500, "Word 排版失败，原稿已保留") from None
    db.refresh(row)
    audit = service.audit_of(row)
    audit["export"] = {k: v for k, v in result.items() if k != "path"}
    row.output_file_path = result["path"]
    row.audit_json = json.dumps(audit, ensure_ascii=False)
    db.commit()
    return service.serialize(row)
