"""PDF 阅读器标注 API（多页高亮、原文锚点、OCR 文字层与笔记）。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Annotation, Book, KnowledgeNode
from backend.app.schemas import AnnotationCreateReq, AnnotationResp, AnnotationUpdateReq

router = APIRouter(prefix="/api", tags=["annotations"])


def _to_resp(a: Annotation) -> AnnotationResp:
    return AnnotationResp(
        id=a.id, book_id=a.book_id, page=a.page, rect_json=a.rect_json,
        text=a.text, color=a.color, note=a.note,
        mark_type=getattr(a, "mark_type", "highlight") or "highlight",
        origin=getattr(a, "origin", "user") or "user",
        knowledge_node_id=a.knowledge_node_id,
        schema_version=getattr(a, "schema_version", 1) or 1,
        anchor_json=getattr(a, "anchor_json", None),
        status=getattr(a, "status", "active") or "active",
        created_at=a.created_at,
    )


def _rects_from_json(value: str) -> list[dict]:
    try:
        rects = json.loads(value or "[]")
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "rect_json 不是合法 JSON") from exc
    if not isinstance(rects, list):
        raise HTTPException(422, "rect_json 必须是矩形数组")
    normalized = []
    for rect in rects:
        if not isinstance(rect, dict):
            raise HTTPException(422, "矩形格式错误")
        try:
            x, y, w, h = (float(rect[key]) for key in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, "矩形坐标缺失或不是数字") from exc
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1.0001 or y + h > 1.0001:
            raise HTTPException(422, "矩形坐标必须位于页面 0～1 范围内")
        normalized.append({"x": round(x, 6), "y": round(y, 6), "w": round(w, 6), "h": round(h, 6)})
    return normalized


def _anchor_payload(req, book: Book | None = None) -> tuple[int, str, str | None, int]:
    """返回 page、v1 rect_json、anchor_json、schema_version。"""
    if req.anchor is None:
        rects = _rects_from_json(req.rect_json or "[]")
        return req.page, json.dumps(rects, separators=(",", ":")), None, 1
    anchor = req.anchor.model_dump()
    if book and not anchor.get("document_fingerprint"):
        anchor["document_fingerprint"] = book.file_hash
    if anchor.get("kind") == "office":
        locator = anchor.get("office")
        if not locator or locator["end_offset"] <= locator["start_offset"]:
            raise HTTPException(422, "Office 标注缺少有效的章节文字锚点")
        if book and book.file_type not in {"docx", "pptx"}:
            raise HTTPException(422, "Office 锚点只能用于 DOCX/PPTX")
        return (0, "[]", json.dumps(anchor, ensure_ascii=False, separators=(",", ":")), 3)
    segments = anchor["segments"]
    if not segments:
        raise HTTPException(422, "PDF 标注至少需要一个页面矩形")
    for segment in segments:
        if book and book.total_pages and segment["page"] > book.total_pages:
            raise HTTPException(422, f"批注页码 {segment['page']} 超出文档范围")
        # Pydantic 已验证单个值，这里再验证 x+w/y+h。
        for rect in segment["rects"]:
            if rect["x"] + rect["w"] > 1.0001 or rect["y"] + rect["h"] > 1.0001:
                raise HTTPException(422, "批注矩形超出页面范围")
    first = segments[0]
    return (
        first["page"],
        json.dumps(first["rects"], ensure_ascii=False, separators=(",", ":")),
        json.dumps(anchor, ensure_ascii=False, separators=(",", ":")),
        2,
    )


def _book_pdf_path(book: Book) -> Path:
    from backend.app.core.config import settings
    root = settings.uploads_dir.resolve()
    path = (root / book.file_path).resolve()
    if root not in path.parents or not path.is_file() or book.file_type != "pdf":
        raise HTTPException(404, "PDF 原文件不存在")
    return path


@router.get("/books/{book_id}/annotations", response_model=list[AnnotationResp])
def list_annotations(
    book_id: int,
    page: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = select(Annotation).where(Annotation.book_id == book_id)
    if page is not None:
        q = q.where(Annotation.page == page)
    items = db.scalars(q.order_by(Annotation.page, Annotation.id)).all()
    return [_to_resp(a) for a in items]


@router.post("/books/{book_id}/annotations", response_model=AnnotationResp, status_code=201)
def create_annotation(book_id: int, req: AnnotationCreateReq, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    page, rect_json, anchor_json, schema_version = _anchor_payload(req, book)
    # 标注与知识树是不同数据类型：只有用户显式指定/提升时才建立关联。
    node_id = req.knowledge_node_id
    if node_id is not None and not db.get(KnowledgeNode, node_id):
        raise HTTPException(404, "知识树节点不存在")
    a = Annotation(
        book_id=book_id, page=page, rect_json=rect_json,
        text=req.text, color=req.color, note=req.note,
        mark_type=req.mark_type, origin=req.origin,
        knowledge_node_id=node_id, schema_version=schema_version,
        anchor_json=anchor_json, status="active",
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return _to_resp(a)


@router.patch("/annotations/{annotation_id}", response_model=AnnotationResp)
def update_annotation(annotation_id: int, req: AnnotationUpdateReq, db: Session = Depends(get_db)):
    a = db.get(Annotation, annotation_id)
    if not a:
        raise HTTPException(404, "标注不存在")
    if req.note is not None:
        a.note = req.note
    if req.color is not None:
        a.color = req.color
    if req.mark_type is not None:
        a.mark_type = req.mark_type
    if req.knowledge_node_id is not None:
        a.knowledge_node_id = req.knowledge_node_id
    if req.text is not None:
        a.text = req.text
    if req.anchor is not None:
        book = db.get(Book, a.book_id)
        page, rect_json, anchor_json, schema_version = _anchor_payload(req, book)
        a.page, a.rect_json = page, rect_json
        a.anchor_json, a.schema_version, a.status = anchor_json, schema_version, "active"
    elif req.rect_json is not None:
        a.rect_json = json.dumps(_rects_from_json(req.rect_json), separators=(",", ":"))
        if req.page is not None:
            a.page = req.page
    if req.status is not None:
        a.status = req.status
    db.commit()
    db.refresh(a)
    return _to_resp(a)


@router.get("/books/{book_id}/annotations/audit")
def audit_annotations(book_id: int, db: Session = Depends(get_db)):
    if not db.get(Book, book_id):
        raise HTTPException(404, "书籍不存在")
    items = db.scalars(select(Annotation).where(Annotation.book_id == book_id)).all()
    issues = []
    for annotation in items:
        try:
            _rects_from_json(annotation.rect_json)
            if annotation.schema_version >= 2 and annotation.anchor_json:
                anchor = json.loads(annotation.anchor_json)
                for segment in anchor.get("segments", []):
                    _rects_from_json(json.dumps(segment.get("rects", [])))
        except (HTTPException, ValueError, TypeError) as exc:
            issues.append({"id": annotation.id, "page": annotation.page, "reason": getattr(exc, "detail", "锚点格式错误")})
    return {"book_id": book_id, "total": len(items), "issue_count": len(issues), "issues": issues}


@router.post("/annotations/{annotation_id}/repair", response_model=AnnotationResp)
def repair_annotation(annotation_id: int, db: Session = Depends(get_db)):
    """使用原文重新定位旧批注；失败时保留原记录并标为待人工重选。"""
    a = db.get(Annotation, annotation_id)
    if not a:
        raise HTTPException(404, "标注不存在")
    book = db.get(Book, a.book_id)
    path = _book_pdf_path(book)
    quote = (a.text or "").strip()
    rects: list[dict] = []
    source = "pdf-text"
    if quote and a.page >= 1:
        import fitz
        with fitz.open(path) as doc:
            if a.page <= doc.page_count:
                pdf_page = doc.load_page(a.page - 1)
                page_rect = pdf_page.rect
                for match in pdf_page.search_for(quote):
                    rects.append({
                        "x": round(match.x0 / page_rect.width, 6),
                        "y": round(match.y0 / page_rect.height, 6),
                        "w": round(match.width / page_rect.width, 6),
                        "h": round(match.height / page_rect.height, 6),
                    })
        if not rects:
            from backend.app.services.parser.ocr_layer import find_quote_in_cached_layer
            rects = find_quote_in_cached_layer(path, a.page, quote)
            source = "ocr"
    if not rects:
        a.status = "needs_reanchor"
        db.commit()
        db.refresh(a)
        return _to_resp(a)
    anchor = {
        "schema_version": 2, "document_fingerprint": book.file_hash,
        "quote": {"exact": quote, "prefix": "", "suffix": ""},
        "segments": [{"page": a.page, "source": source, "rects": rects}],
    }
    a.rect_json = json.dumps(rects, separators=(",", ":"))
    a.anchor_json = json.dumps(anchor, ensure_ascii=False, separators=(",", ":"))
    a.schema_version, a.status = 2, "active"
    db.commit()
    db.refresh(a)
    return _to_resp(a)


@router.get("/books/{book_id}/pdf-text-layer/{page_no}")
def pdf_text_layer(book_id: int, page_no: int, generate: bool = Query(default=True), db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    if page_no < 1 or (book.total_pages and page_no > book.total_pages):
        raise HTTPException(422, "页码超出范围")
    try:
        from backend.app.services.parser.ocr_layer import get_ocr_text_layer
        return get_ocr_text_layer(_book_pdf_path(book), page_no, generate=generate)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/annotations/{annotation_id}", status_code=204)
def delete_annotation(annotation_id: int, db: Session = Depends(get_db)):
    a = db.get(Annotation, annotation_id)
    if not a:
        raise HTTPException(404, "标注不存在")
    db.delete(a)
    db.commit()
