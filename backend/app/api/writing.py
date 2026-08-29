"""文献工作台写作实验室 API。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.models import WritingDnaProfile, WritingDnaRevision, WritingOutput
from backend.app.services.writing_lab import (apply_docx_changes, apply_text_changes,
    clean_blocks, create_word_output, distill_profile_task, docx_blocks, imitate,
    output_path, text_blocks, validate_corpus)
from backend.app.worker.tasks import submit

router = APIRouter(prefix="/api/writing", tags=["writing"])


class ProfileCreateReq(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_author: str | None = Field(default=None, max_length=120)
    book_ids: list[int] = Field(min_length=20, max_length=80)
    rights_acknowledged: bool


class ProfileRefineReq(BaseModel):
    book_ids: list[int] = Field(default_factory=list, max_length=80)
    remove_book_ids: list[int] = Field(default_factory=list, max_length=80)
    feedback: str = Field(default="", max_length=4000)


class ImitateReq(BaseModel):
    topic: str = Field(min_length=2, max_length=300)
    genre: str = Field(default="深度文章", max_length=80)
    length: int = Field(default=1800, ge=300, le=12000)
    brief: str = Field(default="", max_length=4000)


class CleanTextReq(BaseModel):
    text: str = Field(min_length=1, max_length=120000)
    title: str = Field(default="去 AI 味稿件", max_length=255)
    profile_id: int | None = None


class OutputUpdateReq(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    output_text: str = Field(min_length=1, max_length=200000)


class OutputReviewReq(BaseModel):
    accepted_indexes: list[int] = Field(default_factory=list, max_length=500)


def _loads(raw: str | None, fallback):
    try:
        return json.loads(raw) if raw else fallback
    except (TypeError, ValueError):
        return fallback


def _profile_summary(row: WritingDnaProfile) -> dict:
    return {"id": row.id, "name": row.name, "target_author": row.target_author,
            "book_ids": _loads(row.book_ids_json, []), "corpus_count": len(_loads(row.book_ids_json, [])),
            "status": row.status, "current_version": row.current_version,
            "rights_acknowledged": bool(row.rights_acknowledged), "feedback": row.feedback or "",
            "error_msg": row.error_msg, "created_at": row.created_at, "updated_at": row.updated_at}


def _revision(row: WritingDnaRevision) -> dict:
    return {"id": row.id, "profile_id": row.profile_id, "version": row.version,
            "language_dna": row.language_dna, "structure_patterns": row.structure_patterns,
            "cognitive_framework": row.cognitive_framework, "visual_style_guide": row.visual_style_guide,
            "writing_dna": row.writing_dna, "quality": _loads(row.quality_json, {}),
            "feedback": row.feedback or "", "created_at": row.created_at}


def _output(row: WritingOutput, detail: bool = True) -> dict:
    value = {"id": row.id, "profile_id": row.profile_id, "kind": row.kind, "title": row.title,
             "input_type": row.input_type, "audit": _loads(row.audit_json, {}),
             "download_ready": bool(row.output_file_path), "created_at": row.created_at}
    if detail:
        value.update({"source_text": row.source_text or "", "output_text": row.output_text})
    else:
        value.update({"output_preview": (row.output_text or "")[:180], "output_length": len(row.output_text or "")})
    return value


def _safe_writing_file(folder: str, filename: str | None) -> Path | None:
    if not filename:
        return None
    base = (settings.writing_dir / folder).resolve()
    path = (base / Path(filename).name).resolve()
    return path if path.parent == base else None


def _replace_word_output(row: WritingOutput, text: str) -> None:
    destination = _safe_writing_file("outputs", row.output_file_path)
    if destination is None:
        destination = output_path()
        row.output_file_path = destination.name
    temporary = destination.with_name(f".{destination.stem}-{uuid.uuid4().hex[:8]}.tmp.docx")
    try:
        create_word_output(row.title, text, temporary)
        try:
            temporary.replace(destination)
        except PermissionError as exc:
            raise HTTPException(409, "Word 输出正在被其他程序占用，请关闭文件后重试") from exc
    finally:
        temporary.unlink(missing_ok=True)


@router.get("/profiles")
def list_profiles(db: Session = Depends(get_db)):
    rows = db.scalars(select(WritingDnaProfile).order_by(WritingDnaProfile.updated_at.desc())).all()
    return {"items": [_profile_summary(row) for row in rows]}


@router.get("/profiles/{profile_id}")
def get_profile(profile_id: int, db: Session = Depends(get_db)):
    row = db.get(WritingDnaProfile, profile_id)
    if not row:
        raise HTTPException(404, "Writing DNA 项目不存在")
    revisions = db.scalars(select(WritingDnaRevision).where(WritingDnaRevision.profile_id == profile_id)
                           .order_by(WritingDnaRevision.version.desc())).all()
    return {**_profile_summary(row), "corpus_manifest": _loads(row.corpus_manifest_json, []),
            "revisions": [_revision(revision) for revision in revisions]}


@router.post("/profiles", status_code=202)
def create_profile(req: ProfileCreateReq, db: Session = Depends(get_db)):
    if not req.rights_acknowledged:
        raise HTTPException(400, "请确认你有权处理所选文章；未经授权的原文不能用于蒸馏")
    try:
        manifest = validate_corpus(db, req.book_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    row = WritingDnaProfile(name=req.name.strip(), target_author=(req.target_author or "").strip() or None,
                            book_ids_json=json.dumps(list(dict.fromkeys(req.book_ids))),
                            corpus_manifest_json=json.dumps(manifest, ensure_ascii=False),
                            rights_acknowledged=1, status="pending")
    db.add(row); db.commit(); db.refresh(row)
    task = submit("writing_dna", lambda record: distill_profile_task(record, row.id), book_id=req.book_ids[0])
    return {"profile_id": row.id, "task_id": task.id, "corpus_count": len(manifest)}


@router.post("/profiles/{profile_id}/refine", status_code=202)
def refine_profile(profile_id: int, req: ProfileRefineReq, db: Session = Depends(get_db)):
    row = db.get(WritingDnaProfile, profile_id)
    if not row:
        raise HTTPException(404, "Writing DNA 项目不存在")
    if row.status in {"pending", "running"}:
        raise HTTPException(409, "当前版本仍在蒸馏，请等待完成后再提交下一版")
    removed = set(req.remove_book_ids)
    ids = list(dict.fromkeys([book_id for book_id in _loads(row.book_ids_json, []) if book_id not in removed] + req.book_ids))
    try:
        manifest = validate_corpus(db, ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    row.book_ids_json = json.dumps(ids); row.corpus_manifest_json = json.dumps(manifest, ensure_ascii=False)
    row.feedback = req.feedback.strip() or row.feedback; row.status = "pending"; row.error_msg = None
    db.commit()
    task = submit("writing_dna", lambda record: distill_profile_task(record, row.id), book_id=ids[0])
    return {"profile_id": row.id, "task_id": task.id, "corpus_count": len(ids), "next_version": row.current_version + 1}


@router.post("/profiles/{profile_id}/imitate")
async def imitate_with_profile(profile_id: int, req: ImitateReq, db: Session = Depends(get_db)):
    try:
        row = await imitate(db, profile_id, req.topic, req.genre, req.length, req.brief)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    path = output_path(); create_word_output(row.title, row.output_text, path)
    row.output_file_path = path.name; db.commit(); db.refresh(row)
    return _output(row)


@router.post("/clean-text")
async def clean_text(req: CleanTextReq, db: Session = Depends(get_db)):
    try:
        changes, audit = await clean_blocks(db, text_blocks(req.text), req.profile_id)
        cleaned = apply_text_changes(req.text, changes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = WritingOutput(profile_id=req.profile_id, kind="ai_tone", title=req.title,
                        input_type="text", source_text=req.text, output_text=cleaned,
                        audit_json=json.dumps({**audit, "changes": changes}, ensure_ascii=False))
    path = output_path(); create_word_output(req.title, cleaned, path); row.output_file_path = path.name
    db.add(row); db.commit(); db.refresh(row)
    return _output(row)


async def _save_docx(file: UploadFile) -> Path:
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(400, "只支持 DOCX Word 文件")
    folder = settings.writing_dir / "sources"; folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"source-{uuid.uuid4().hex[:16]}.docx"
    total = 0
    with path.open("xb") as destination:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > 50 * 1024 * 1024:
                path.unlink(missing_ok=True); raise HTTPException(400, "Word 文件不能超过 50MB")
            destination.write(chunk)
    with path.open("rb") as source:
        signature = source.read(2)
    if total < 4 or signature != b"PK":
        path.unlink(missing_ok=True); raise HTTPException(400, "文件不是有效的 DOCX")
    return path


@router.post("/clean-docx")
async def clean_docx(file: UploadFile = File(...), profile_id: int | None = Form(default=None),
                     db: Session = Depends(get_db)):
    source = await _save_docx(file)
    try:
        document, blocks, refs = docx_blocks(source)
        if not blocks:
            raise HTTPException(422, "Word 文档没有可分析文字")
        changes, audit = await clean_blocks(db, blocks, profile_id)
        applied = apply_docx_changes(document, refs, changes)
        destination = output_path(); document.save(destination)
        output_text = "\n\n".join(refs[block["id"]].text for block in blocks)
        row = WritingOutput(profile_id=profile_id, kind="ai_tone", title=Path(file.filename or "稿件").stem,
                            input_type="docx", source_text=None, output_text=output_text,
                            source_file_path=source.name, output_file_path=destination.name,
                            audit_json=json.dumps({**audit, "changes": changes, "applied": applied,
                                                   "paragraph_blocks": len(blocks)}, ensure_ascii=False))
        db.add(row); db.commit(); db.refresh(row)
        return _output(row)
    except Exception:
        if source.exists():
            source.unlink(missing_ok=True)
        raise


@router.get("/outputs")
def list_outputs(page: int = 1, page_size: int = 20, db: Session = Depends(get_db)):
    safe_page = max(1, page); safe_size = min(max(page_size, 1), 100)
    total = db.scalar(select(func.count()).select_from(WritingOutput)) or 0
    rows = db.scalars(select(WritingOutput).order_by(WritingOutput.created_at.desc())
                      .offset((safe_page - 1) * safe_size).limit(safe_size)).all()
    return {"items": [_output(row, detail=False) for row in rows], "total": total,
            "page": safe_page, "page_size": safe_size}


@router.get("/outputs/{output_id}")
def get_output(output_id: int, db: Session = Depends(get_db)):
    row = db.get(WritingOutput, output_id)
    if not row:
        raise HTTPException(404, "写作输出不存在")
    return _output(row)


@router.patch("/outputs/{output_id}")
def update_output(output_id: int, req: OutputUpdateReq, db: Session = Depends(get_db)):
    row = db.get(WritingOutput, output_id)
    if not row:
        raise HTTPException(404, "写作输出不存在")
    if row.kind == "ai_tone" and row.input_type == "docx":
        raise HTTPException(409, "Word 原稿请使用逐条审阅，以免丢失原有段落和表格格式")
    row.title = req.title.strip(); row.output_text = req.output_text
    audit = _loads(row.audit_json, {})
    audit.update({"user_edited": True, "edited_at": datetime.now(timezone.utc).isoformat()})
    row.audit_json = json.dumps(audit, ensure_ascii=False)
    _replace_word_output(row, row.output_text)
    db.commit(); db.refresh(row)
    return _output(row)


@router.post("/outputs/{output_id}/review")
def review_output(output_id: int, req: OutputReviewReq, db: Session = Depends(get_db)):
    row = db.get(WritingOutput, output_id)
    if not row or row.kind != "ai_tone":
        raise HTTPException(404, "去 AI 味输出不存在")
    audit = _loads(row.audit_json, {})
    changes = audit.get("changes") if isinstance(audit.get("changes"), list) else []
    indexes = sorted(set(req.accepted_indexes))
    if any(index < 0 or index >= len(changes) for index in indexes):
        raise HTTPException(422, "审阅项超出有效范围")
    selected = [changes[index] for index in indexes]
    if row.input_type == "docx":
        source = _safe_writing_file("sources", row.source_file_path)
        if source is None or not source.exists():
            raise HTTPException(409, "Word 原稿已丢失，无法保持格式重新生成")
        document, blocks, refs = docx_blocks(source)
        applied = apply_docx_changes(document, refs, selected)
        destination = _safe_writing_file("outputs", row.output_file_path) or output_path()
        row.output_file_path = destination.name
        temporary = destination.with_name(f".{destination.stem}-{uuid.uuid4().hex[:8]}.tmp.docx")
        try:
            document.save(temporary)
            try:
                temporary.replace(destination)
            except PermissionError as exc:
                raise HTTPException(409, "Word 输出正在被其他程序占用，请关闭文件后重试") from exc
        finally:
            temporary.unlink(missing_ok=True)
        row.output_text = "\n\n".join(refs[block["id"]].text for block in blocks)
    else:
        if row.source_text is None:
            raise HTTPException(409, "原始文本已丢失，无法重新审阅")
        row.output_text = apply_text_changes(row.source_text, selected)
        _replace_word_output(row, row.output_text)
        applied = len(selected)
    audit.update({"accepted_indexes": indexes, "applied": applied, "reviewed": True,
                  "reviewed_at": datetime.now(timezone.utc).isoformat()})
    row.audit_json = json.dumps(audit, ensure_ascii=False)
    db.commit(); db.refresh(row)
    return _output(row)


@router.get("/outputs/{output_id}/download")
def download_output(output_id: int, db: Session = Depends(get_db)):
    row = db.get(WritingOutput, output_id)
    if not row or not row.output_file_path:
        raise HTTPException(404, "Word 输出不存在")
    path = (settings.writing_dir / "outputs" / Path(row.output_file_path).name).resolve()
    if path.parent != (settings.writing_dir / "outputs").resolve() or not path.exists():
        raise HTTPException(404, "Word 输出文件丢失")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        filename=f"{row.title}-输出.docx")
