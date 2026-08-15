"""PDF 阅读器标注 API（高亮 + 笔记，可关联知识树节点）"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Annotation, Book
from backend.app.schemas import AnnotationCreateReq, AnnotationResp, AnnotationUpdateReq

router = APIRouter(prefix="/api", tags=["annotations"])


def _to_resp(a: Annotation) -> AnnotationResp:
    return AnnotationResp(
        id=a.id, book_id=a.book_id, page=a.page, rect_json=a.rect_json,
        text=a.text, color=a.color, note=a.note,
        knowledge_node_id=a.knowledge_node_id, created_at=a.created_at,
    )


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
    if not db.get(Book, book_id):
        raise HTTPException(404, "书籍不存在")
    a = Annotation(
        book_id=book_id, page=req.page, rect_json=req.rect_json,
        text=req.text, color=req.color, note=req.note,
        knowledge_node_id=req.knowledge_node_id,
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
    if req.knowledge_node_id is not None:
        a.knowledge_node_id = req.knowledge_node_id
    db.commit()
    db.refresh(a)
    return _to_resp(a)


@router.delete("/annotations/{annotation_id}", status_code=204)
def delete_annotation(annotation_id: int, db: Session = Depends(get_db)):
    a = db.get(Annotation, annotation_id)
    if not a:
        raise HTTPException(404, "标注不存在")
    db.delete(a)
    db.commit()
