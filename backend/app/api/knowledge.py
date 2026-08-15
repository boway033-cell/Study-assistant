"""知识树 API — 用户自主搭建知识结构，可关联书籍章节展示原文"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, Chapter, Chunk, KnowledgeNode
from backend.app.schemas import (
    KnowledgeMoveReq,
    KnowledgeNodeCreateReq,
    KnowledgeNodeResp,
    KnowledgeNodeUpdateReq,
    KnowledgeSourceResp,
    KnowledgeTreeResp,
)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _to_resp(node: KnowledgeNode, db: Session) -> KnowledgeNodeResp:
    children = db.scalars(
        select(KnowledgeNode).where(KnowledgeNode.parent_id == node.id)
        .order_by(KnowledgeNode.order_index, KnowledgeNode.id)
    ).all()
    return KnowledgeNodeResp(
        id=node.id, parent_id=node.parent_id, title=node.title,
        book_id=node.book_id, chapter_id=node.chapter_id, note=node.note,
        order_index=node.order_index,
        children=[_to_resp(c, db) for c in children],
    )


def _get_node(db: Session, node_id: int) -> KnowledgeNode:
    node = db.get(KnowledgeNode, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    return node


def _collect_ids(node: KnowledgeNode, db: Session) -> list[int]:
    ids = [node.id]
    children = db.scalars(
        select(KnowledgeNode).where(KnowledgeNode.parent_id == node.id)
    ).all()
    for c in children:
        ids.extend(_collect_ids(c, db))
    return ids


@router.get("/tree", response_model=KnowledgeTreeResp)
def get_tree(db: Session = Depends(get_db)):
    roots = db.scalars(
        select(KnowledgeNode).where(KnowledgeNode.parent_id.is_(None))
        .order_by(KnowledgeNode.order_index, KnowledgeNode.id)
    ).all()
    total = db.scalar(select(func.count()).select_from(KnowledgeNode)) or 0
    return KnowledgeTreeResp(total=total, items=[_to_resp(r, db) for r in roots])


@router.post("/nodes", response_model=KnowledgeNodeResp, status_code=201)
def create_node(req: KnowledgeNodeCreateReq, db: Session = Depends(get_db)):
    if req.parent_id is not None:
        _get_node(db, req.parent_id)  # 校验父节点存在
    # 兄弟排序：新节点放在末尾
    siblings = db.scalars(
        select(KnowledgeNode).where(KnowledgeNode.parent_id == req.parent_id)
    ).all()
    node = KnowledgeNode(
        parent_id=req.parent_id, title=req.title.strip(),
        order_index=max((s.order_index for s in siblings), default=-1) + 1,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return _to_resp(node, db)


@router.patch("/nodes/{node_id}", response_model=KnowledgeNodeResp)
def update_node(node_id: int, req: KnowledgeNodeUpdateReq, db: Session = Depends(get_db)):
    node = _get_node(db, node_id)
    if req.title is not None:
        node.title = req.title.strip()
    if req.note is not None:
        node.note = req.note
    if req.chapter_id is not None:
        ch = db.get(Chapter, req.chapter_id)
        if not ch:
            raise HTTPException(404, "章节不存在")
        node.chapter_id = req.chapter_id
        # 章节所属书籍为准（保证书/章一致）
        node.book_id = ch.book_id
    elif req.book_id is not None:
        if not db.get(Book, req.book_id):
            raise HTTPException(404, "书籍不存在")
        node.book_id = req.book_id
    db.commit()
    db.refresh(node)
    return _to_resp(node, db)


@router.delete("/nodes/{node_id}", status_code=204)
def delete_node(node_id: int, db: Session = Depends(get_db)):
    node = _get_node(db, node_id)
    ids = _collect_ids(node, db)
    db.query(KnowledgeNode).filter(KnowledgeNode.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


@router.post("/nodes/{node_id}/move", response_model=KnowledgeNodeResp)
def move_node(node_id: int, req: KnowledgeMoveReq, db: Session = Depends(get_db)):
    node = _get_node(db, node_id)
    if req.parent_id == node_id:
        raise HTTPException(400, "不能移动到自身")
    # 防环：目标父节点不能是 node 的后代
    if req.parent_id is not None:
        parent = _get_node(db, req.parent_id)
        if node_id in _collect_ids(parent, db):
            raise HTTPException(400, "不能移动到自己的子节点下")
        node.parent_id = req.parent_id
        # 移到目标父节点的末尾
        siblings = db.scalars(
            select(KnowledgeNode).where(KnowledgeNode.parent_id == req.parent_id)
        ).all()
        node.order_index = max((s.order_index for s in siblings if s.id != node.id), default=-1) + 1
    else:
        node.parent_id = None
        roots = db.scalars(
            select(KnowledgeNode).where(KnowledgeNode.parent_id.is_(None))
        ).all()
        node.order_index = max((s.order_index for s in roots if s.id != node.id), default=-1) + 1
    db.commit()
    db.refresh(node)
    return _to_resp(node, db)


@router.get("/nodes/{node_id}/source", response_model=KnowledgeSourceResp)
def node_source(node_id: int, db: Session = Depends(get_db)):
    """关联章节的原文：合并该章全部 chunk 文本，供右侧原文面板展示。"""
    node = _get_node(db, node_id)
    if not node.chapter_id:
        return KnowledgeSourceResp(node_id=node.id, node_title=node.title, text="")
    chapter = db.get(Chapter, node.chapter_id)
    if not chapter:
        return KnowledgeSourceResp(node_id=node.id, node_title=node.title, text="")
    book = db.get(Book, chapter.book_id)
    chunks = db.scalars(
        select(Chunk).where(Chunk.chapter_id == chapter.id)
        .order_by(Chunk.chunk_index)
    ).all()
    text = "\n\n".join(c.content for c in chunks)
    return KnowledgeSourceResp(
        node_id=node.id, node_title=node.title,
        book_id=chapter.book_id, book_title=book.title if book else None,
        chapter_id=chapter.id, chapter_title=chapter.title,
        page_start=chapter.start_page, page_end=chapter.end_page,
        text=text,
    )
