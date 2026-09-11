"""文献工作台写作实验室 API。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.models import (Book, Chunk, EvidenceCard, KnowledgeNote, StudyReport,
                                WritingDnaProfile, WritingDnaRevision, WritingOutput)
from backend.app.services.writing_lab import (ai_flavor_violations, apply_docx_changes,
    apply_text_changes, clean_blocks, content_fingerprint, create_word_output,
    distill_profile_task, docx_blocks, generate_literature_review, imitate, output_path,
    text_blocks, validate_corpus)
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
    knowledge_note_ids: list[int] = Field(default_factory=list, max_length=40)
    evidence_card_ids: list[int] = Field(default_factory=list, max_length=40)
    report_ids: list[int] = Field(default_factory=list, max_length=10)


class LiteratureReviewReq(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    question: str = Field(min_length=4, max_length=1200)
    book_ids: list[int] = Field(min_length=2, max_length=50)
    review_type: str = Field(default="narrative", pattern="^(narrative|scoping|evidence_map)$")
    discipline: str = Field(default="auto", pattern="^(auto|social_science|humanities|natural_biomedical)$")
    length: int = Field(default=3500, ge=1200, le=20000)
    profile_id: int | None = None
    ai_tone_constraints: bool = True


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
            "logic_dna": row.logic_dna or "",
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


def _source_freshness(db: Session, row: WritingOutput) -> dict:
    audit = _loads(row.audit_json, {})
    changed: list[dict] = []
    missing: list[dict] = []
    checked = 0
    legacy = False
    for item in audit.get("knowledge_objects", []) if isinstance(audit.get("knowledge_objects"), list) else []:
        kind, object_id, expected = item.get("type"), item.get("id"), item.get("fingerprint")
        model = {"note": KnowledgeNote, "evidence": EvidenceCard, "report": StudyReport}.get(kind)
        current = db.get(model, object_id) if model and object_id else None
        if not current:
            missing.append({"type": kind, "id": object_id, "title": item.get("title")})
            continue
        if not expected:
            legacy = True
            continue
        checked += 1
        if kind == "note":
            actual = content_fingerprint(current.title, current.content, current.source_refs_json)
        elif kind == "evidence":
            actual = content_fingerprint(current.title, current.claim_text, current.evidence_text,
                                         current.source_ref_json, current.verification_status)
        else:
            actual = content_fingerprint(current.focus, current.content, current.claims_json,
                                         current.selection_json)
        if actual != expected:
            changed.append({"type": kind, "id": object_id, "title": item.get("title")})
    manifests = audit.get("evidence_manifest", []) if isinstance(audit.get("evidence_manifest"), list) else []
    for manifest in manifests:
        book_id = manifest.get("book_id")
        book = db.get(Book, book_id) if book_id else None
        if not book:
            missing.append({"type": "book", "id": book_id, "title": manifest.get("title")})
            continue
        expected_book_hash = manifest.get("book_file_hash")
        if expected_book_hash and expected_book_hash != book.file_hash:
            changed.append({"type": "book", "id": book_id, "title": manifest.get("title")})
        fingerprints = manifest.get("source_fingerprints")
        if not isinstance(fingerprints, list):
            legacy = True
            continue
        for source in fingerprints:
            chunk_id, expected = source.get("chunk_id"), source.get("fingerprint")
            chunk = db.get(Chunk, chunk_id) if chunk_id else None
            if not chunk:
                missing.append({"type": "chunk", "id": chunk_id, "book_id": book_id})
                continue
            checked += 1
            actual = content_fingerprint(chunk.content, chunk.page_start, chunk.page_end, chunk.chapter_id)
            if expected and actual != expected:
                changed.append({"type": "chunk", "id": chunk_id, "book_id": book_id})
    if missing:
        status, message = "missing", "部分原始知识对象或文献片段已被删除，请重新生成后再引用。"
    elif changed:
        status, message = "stale", "生成后来源内容发生变化，当前正文与引用需要重新核对。"
    elif checked:
        status, message = "fresh", "生成时使用的知识对象与文献片段未发生变化。"
    elif legacy:
        status, message = "unknown", "这是旧版输出，未保存来源指纹，无法自动判断是否过期。"
    else:
        status, message = "not_applicable", "该输出没有可复核的知识库来源快照。"
    return {"status": status, "message": message, "checked_sources": checked,
            "changed": changed, "missing": missing,
            "checked_at": datetime.now(timezone.utc).isoformat()}


def _safe_writing_file(folder: str, filename: str | None) -> Path | None:
    if not filename:
        return None
    base = (settings.writing_dir / folder).resolve()
    path = (base / Path(filename).name).resolve()
    return path if path.parent == base else None


def _task_book_id(db: Session, candidates: list[int] | None = None) -> int:
    """后台任务需要一个真实 book_id。

    `import_tasks.book_id` 是到 `books.id` 的外键，而 SQLite 连接层开了
    `PRAGMA foreign_keys=ON`（core/database.py:23），传 0 会让 `_persist`
    静默失败（tasks.py:89 吞异常），任务就只剩进程内存、重启后无法恢复。
    因此先按给定候选找有效的书，找不到再退回书库里最小的 book id。
    """
    for book_id in (candidates or []):
        if book_id and db.get(Book, book_id):
            return book_id
    return db.scalar(select(func.min(Book.id))) or 0


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
    book_ids = _loads(row.book_ids_json, [])
    existing_ids = set(db.scalars(select(Book.id).where(Book.id.in_(book_ids))).all()) if book_ids else set()
    missing_book_ids = [book_id for book_id in book_ids if book_id not in existing_ids]
    return {**_profile_summary(row), "corpus_manifest": _loads(row.corpus_manifest_json, []),
            "missing_book_ids": missing_book_ids,
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


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    row = db.get(WritingDnaProfile, profile_id)
    if not row:
        raise HTTPException(404, "Writing DNA 项目不存在")
    if row.status in {"pending", "running"}:
        raise HTTPException(409, "该项目正在蒸馏，请等待完成或先取消任务后再删除")
    output_count = db.scalar(select(func.count()).select_from(WritingOutput).where(WritingOutput.profile_id == profile_id)) or 0
    revision_count = db.scalar(select(func.count()).select_from(WritingDnaRevision)
                               .where(WritingDnaRevision.profile_id == profile_id)) or 0
    # 输出保留（profile_id 置空，仍可在“写作输出”中查看）；版本历史随项目一并删除。
    db.execute(update(WritingOutput).where(WritingOutput.profile_id == profile_id).values(profile_id=None))
    db.query(WritingDnaRevision).filter(WritingDnaRevision.profile_id == profile_id).delete()
    db.delete(row)
    db.commit()
    return {"deleted": True, "profile_id": profile_id,
            "removed_revisions": revision_count, "kept_outputs": output_count}


@router.post("/profiles/{profile_id}/refine", status_code=202)
def refine_profile(profile_id: int, req: ProfileRefineReq, db: Session = Depends(get_db)):
    row = db.get(WritingDnaProfile, profile_id)
    if not row:
        raise HTTPException(404, "Writing DNA 项目不存在")
    if row.status in {"pending", "running"}:
        raise HTTPException(409, "当前版本仍在蒸馏，请等待完成后再提交下一版")
    removed = set(req.remove_book_ids)
    stored_ids = [book_id for book_id in _loads(row.book_ids_json, []) if book_id not in removed]
    # 语料里的书目可能已被从书库删除；refine 时自动剔除，否则整个档案会被失效引用卡死。
    existing_ids = set(db.scalars(select(Book.id).where(Book.id.in_(stored_ids))).all()) if stored_ids else set()
    pruned = [book_id for book_id in stored_ids if book_id not in existing_ids]
    ids = list(dict.fromkeys([book_id for book_id in stored_ids if book_id in existing_ids] + req.book_ids))
    try:
        manifest = validate_corpus(db, ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    row.book_ids_json = json.dumps(ids); row.corpus_manifest_json = json.dumps(manifest, ensure_ascii=False)
    row.feedback = req.feedback.strip() or row.feedback; row.status = "pending"; row.error_msg = None
    db.commit()
    task = submit("writing_dna", lambda record: distill_profile_task(record, row.id), book_id=ids[0])
    return {"profile_id": row.id, "task_id": task.id, "corpus_count": len(ids),
            "next_version": row.current_version + 1, "pruned_book_ids": pruned}


async def _imitate_task(record, payload: dict) -> dict:
    from backend.app.core.database import SessionLocal
    from backend.app.worker.tasks import update_progress

    db = SessionLocal()
    try:
        update_progress(record, 0.06, "evidence", "正在整理已选知识对象与语料校准样本...")
        update_progress(record, 0.25, "writing", "正在按 Writing DNA 撰写初稿（长文可能需要几分钟）...")
        row = await imitate(db, payload["profile_id"], payload["topic"], payload["genre"],
                            payload["length"], payload["brief"], payload["knowledge_note_ids"],
                            payload["evidence_card_ids"], payload["report_ids"])
        update_progress(record, 0.9, "document", "正在生成可编辑 Word 与引用审计...")
        path = output_path(); create_word_output(row.title, row.output_text, path)
        row.output_file_path = path.name; db.commit(); db.refresh(row)
        update_progress(record, 1.0, "done", "独立新作已生成")
        return {"output_id": row.id}
    finally:
        db.close()


@router.post("/profiles/{profile_id}/imitate", status_code=202)
def imitate_with_profile(profile_id: int, req: ImitateReq, db: Session = Depends(get_db)):
    profile = db.get(WritingDnaProfile, profile_id)
    if not profile:
        raise HTTPException(404, "Writing DNA 项目不存在")
    payload = {"profile_id": profile_id, **req.model_dump()}
    task = submit("imitate", lambda record: _imitate_task(record, payload),
                  book_id=_task_book_id(db, _loads(profile.book_ids_json, [])))
    return {"task_id": task.id}


async def _literature_review_task(record, payload: dict) -> dict:
    from backend.app.core.database import SessionLocal
    from backend.app.worker.tasks import update_progress

    db = SessionLocal()
    try:
        update_progress(record, 0.08, "evidence", "正在为每篇文献建立均衡证据包...")
        row = await generate_literature_review(
            db, question=payload["question"], title=payload["title"], book_ids=payload["book_ids"],
            review_type=payload["review_type"], discipline=payload["discipline"], length=payload["length"],
            profile_id=payload.get("profile_id"), ai_tone_constraints=payload["ai_tone_constraints"],
        )
        update_progress(record, 0.88, "document", "正在生成可编辑 Word 与引用审计...")
        path = output_path(); create_word_output(row.title, row.output_text, path)
        row.output_file_path = path.name; db.commit(); db.refresh(row)
        update_progress(record, 1.0, "done", "多文献综述已生成")
        return {"output_id": row.id}
    finally:
        db.close()


@router.post("/literature-review", status_code=202)
def literature_review(req: LiteratureReviewReq):
    payload = req.model_dump()
    payload["question"] = req.question.strip(); payload["title"] = req.title.strip()
    task = submit("literature-review", lambda record: _literature_review_task(record, payload),
                  book_id=req.book_ids[0])
    return {"task_id": task.id}


async def _clean_text_task(record, payload: dict) -> dict:
    from backend.app.core.database import SessionLocal
    from backend.app.worker.tasks import update_progress

    db = SessionLocal()
    try:
        update_progress(record, 0.1, "analyze", "正在定位 13 类可改写痕迹...")
        changes, audit = await clean_blocks(db, text_blocks(payload["text"]), payload.get("profile_id"))
        update_progress(record, 0.8, "apply", "正在应用白名单改写并生成 Word...")
        cleaned = apply_text_changes(payload["text"], changes)
        audit["remaining_violations"] = ai_flavor_violations(cleaned)
        row = WritingOutput(profile_id=payload.get("profile_id"), kind="ai_tone", title=payload["title"],
                            input_type="text", source_text=payload["text"], output_text=cleaned,
                            audit_json=json.dumps({**audit, "changes": changes}, ensure_ascii=False))
        path = output_path(); create_word_output(payload["title"], cleaned, path)
        row.output_file_path = path.name
        db.add(row); db.commit(); db.refresh(row)
        update_progress(record, 1.0, "done", "去 AI 味候选改写已生成")
        return {"output_id": row.id}
    finally:
        db.close()


@router.post("/clean-text", status_code=202)
def clean_text(req: CleanTextReq, db: Session = Depends(get_db)):
    payload = req.model_dump()
    task = submit("clean-text", lambda record: _clean_text_task(record, payload),
                  book_id=_task_book_id(db))
    return {"task_id": task.id}


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


async def _clean_docx_task(record, payload: dict) -> dict:
    from backend.app.core.database import SessionLocal
    from backend.app.worker.tasks import update_progress

    # 请求已提前返回 202，上传的原稿必须由任务自己负责生命周期。
    # 成功时保留（review_output 要靠它回放改动以保住原格式），失败时清理。
    source = Path(payload["source_path"])
    db = SessionLocal()
    try:
        update_progress(record, 0.1, "analyze", "正在解析 Word 段落与表格...")
        document, blocks, refs = docx_blocks(source)
        if not blocks:
            raise ValueError("Word 文档没有可分析文字")
        update_progress(record, 0.3, "clean", "正在定位 13 类可改写痕迹...")
        changes, audit = await clean_blocks(db, blocks, payload.get("profile_id"))
        update_progress(record, 0.8, "apply", "正在应用改写并输出 Word...")
        applied = apply_docx_changes(document, refs, changes)
        destination = output_path(); document.save(destination)
        output_text = "\n\n".join(refs[block["id"]].text for block in blocks)
        audit["remaining_violations"] = ai_flavor_violations(output_text)
        row = WritingOutput(profile_id=payload.get("profile_id"), kind="ai_tone", title=payload["title"],
                            input_type="docx", source_text=None, output_text=output_text,
                            source_file_path=source.name, output_file_path=destination.name,
                            audit_json=json.dumps({**audit, "changes": changes, "applied": applied,
                                                   "paragraph_blocks": len(blocks)}, ensure_ascii=False))
        db.add(row); db.commit(); db.refresh(row)
        update_progress(record, 1.0, "done", "Word 候选改写已生成")
        return {"output_id": row.id}
    except Exception:
        if source.exists():
            source.unlink(missing_ok=True)
        raise
    finally:
        db.close()


@router.post("/clean-docx", status_code=202)
async def clean_docx(file: UploadFile = File(...), profile_id: int | None = Form(default=None),
                     db: Session = Depends(get_db)):
    source = await _save_docx(file)
    payload = {"source_path": str(source), "title": Path(file.filename or "稿件").stem,
               "profile_id": profile_id}
    try:
        task = submit("clean-docx", lambda record: _clean_docx_task(record, payload),
                      book_id=_task_book_id(db))
    except Exception:
        source.unlink(missing_ok=True)
        raise
    return {"task_id": task.id}


@router.get("/outputs")
def list_outputs(page: int = 1, page_size: int = 20, exclude_official: bool = False,
                 kind: str | None = None, db: Session = Depends(get_db)):
    safe_page = max(1, page); safe_size = min(max(page_size, 1), 100)
    conditions = []
    if exclude_official:
        conditions.append(WritingOutput.kind != "official_document")
    if kind:
        conditions.append(WritingOutput.kind == kind)
    condition = and_(*conditions) if conditions else True
    total = db.scalar(select(func.count()).select_from(WritingOutput).where(condition)) or 0
    rows = db.scalars(select(WritingOutput).where(condition).order_by(WritingOutput.created_at.desc())
                      .offset((safe_page - 1) * safe_size).limit(safe_size)).all()
    return {"items": [_output(row, detail=False) for row in rows], "total": total,
            "page": safe_page, "page_size": safe_size}


@router.get("/outputs/{output_id}")
def get_output(output_id: int, db: Session = Depends(get_db)):
    row = db.get(WritingOutput, output_id)
    if not row:
        raise HTTPException(404, "写作输出不存在")
    return {**_output(row), "source_freshness": _source_freshness(db, row)}


@router.patch("/outputs/{output_id}")
def update_output(output_id: int, req: OutputUpdateReq, db: Session = Depends(get_db)):
    row = db.get(WritingOutput, output_id)
    if not row:
        raise HTTPException(404, "写作输出不存在")
    if row.kind == "official_document":
        raise HTTPException(409, "请在公文写作中保存新版本，以保留提纲确认和排版审计")
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
    # 旧版输出可能仍包含 [B…] 机器锚点；首次下载时就地升级为读者脚注版。
    if row.kind == "literature_review" and "[B" in (row.output_text or ""):
        audit = _loads(row.audit_json, {})
        valid = {str(anchor).strip("[]") for item in audit.get("evidence_manifest", [])
                 for anchor in (item.get("anchors") or [])}
        from backend.app.services.writing_citations import database_source_labels, readable_citations
        cleaned, notes = readable_citations(row.output_text, valid_anchors=valid,
                                             labels=database_source_labels(db, valid))
        row.output_text = cleaned
        if notes:
            audit["citation_notes"] = notes
        audit["citation_display_version"] = 1
        row.audit_json = json.dumps(audit, ensure_ascii=False)
        _replace_word_output(row, cleaned)
        db.commit(); db.refresh(row)
    path = (settings.writing_dir / "outputs" / Path(row.output_file_path).name).resolve()
    if path.parent != (settings.writing_dir / "outputs").resolve() or not path.exists():
        raise HTTPException(404, "Word 输出文件丢失")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        filename=f"{row.title}-输出.docx")
