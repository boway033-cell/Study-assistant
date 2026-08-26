"""知识树 API — 用户自主搭建知识结构，可关联书籍章节展示原文"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Annotation, Book, Chapter, Chunk, EvidenceCard, KnowledgeNode, KnowledgeNote
from backend.app.schemas import (
    KnowledgeAiGenerateReq,
    KnowledgeBatchDeleteReq,
    KnowledgeImportReq,
    KnowledgeNodeExpandReq,
    KnowledgeMoveReq,
    KnowledgeNodeCreateReq,
    KnowledgeNodeResp,
    KnowledgeNodeUpdateReq,
    KnowledgeSourceResp,
    KnowledgeTreeResp,
    EvidenceCardCreateReq,
    EvidenceCardUpdateReq,
    KnowledgeNoteCreateReq,
    KnowledgeNoteUpdateReq,
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
        node_type=node.node_type or "concept", mastery=node.mastery or "unknown",
        ref_node_id=node.ref_node_id, order_index=node.order_index,
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
def get_tree(book_ids: list[int] | None = Query(default=None), db: Session = Depends(get_db)):
    roots = db.scalars(
        select(KnowledgeNode).where(KnowledgeNode.parent_id.is_(None))
        .order_by(KnowledgeNode.order_index, KnowledgeNode.id)
    ).all()
    items = [_to_resp(r, db) for r in roots]
    scope = set(book_ids or [])
    if scope:
        def belongs(node: KnowledgeNodeResp) -> bool:
            return node.book_id in scope or any(belongs(child) for child in node.children)
        items = [item for item in items if belongs(item)]
    def count_nodes(nodes: list[KnowledgeNodeResp]) -> int:
        return sum(1 + count_nodes(n.children) for n in nodes)
    return KnowledgeTreeResp(total=count_nodes(items), items=items)


@router.post("/nodes", response_model=KnowledgeNodeResp, status_code=201)
def create_node(req: KnowledgeNodeCreateReq, db: Session = Depends(get_db)):
    parent = None
    if req.parent_id is not None:
        parent = _get_node(db, req.parent_id)  # 校验父节点存在
    # 兄弟排序：新节点放在末尾
    siblings = db.scalars(
        select(KnowledgeNode).where(KnowledgeNode.parent_id == req.parent_id)
    ).all()
    book_id = req.book_id if req.book_id is not None else (parent.book_id if parent else None)
    if req.chapter_id is not None:
        chapter = db.get(Chapter, req.chapter_id)
        if not chapter:
            raise HTTPException(404, "章节不存在")
        book_id = chapter.book_id
    elif book_id is not None and not db.get(Book, book_id):
        raise HTTPException(404, "书籍不存在")
    node = KnowledgeNode(
        parent_id=req.parent_id, title=req.title.strip(), book_id=book_id,
        chapter_id=req.chapter_id, note=req.note, node_type=req.node_type,
        order_index=max((s.order_index for s in siblings), default=-1) + 1,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return _to_resp(node, db)


@router.get("/notes")
def list_knowledge_notes(
    book_ids: list[int] | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
):
    """兼容旧前端的聚合视图；标注和知识笔记保持独立记录，不再通过关联关系合并。"""
    scope = sorted({int(book_id) for book_id in (book_ids or []) if int(book_id) > 0})
    term = (q or "").strip().lower()
    node_stmt = select(KnowledgeNode, Book).outerjoin(Book, Book.id == KnowledgeNode.book_id).where(KnowledgeNode.node_type == "note")
    ann_stmt = select(Annotation, Book).join(Book, Book.id == Annotation.book_id)
    if scope:
        node_stmt = node_stmt.where(KnowledgeNode.book_id.in_(scope))
        ann_stmt = ann_stmt.where(Annotation.book_id.in_(scope))
    items = []
    for node, book in db.execute(node_stmt.order_by(KnowledgeNode.created_at.desc(), KnowledgeNode.id.desc())).all():
        searchable = f"{node.title} {node.note or ''}".lower()
        if term and term not in searchable:
            continue
        items.append({
            "id": node.id, "title": node.title, "content": node.note or "",
            "book_id": node.book_id, "book_title": book.title if book else None,
            "chapter_id": node.chapter_id, "page": None, "quote": None,
            "annotation_id": None, "mastery": node.mastery, "source_type": "note",
            "origin": "user", "created_at": node.created_at.isoformat(),
        })
    for annotation, book in db.execute(ann_stmt.order_by(Annotation.created_at.desc(), Annotation.id.desc())).all():
        searchable = f"{annotation.text or ''} {annotation.note or ''}".lower()
        if term and term not in searchable:
            continue
        label = "划线" if getattr(annotation, "mark_type", "highlight") == "underline" else "高亮"
        items.append({
            "id": f"annotation:{annotation.id}", "title": (annotation.note or annotation.text or label)[:80],
            "content": annotation.note or "", "book_id": annotation.book_id, "book_title": book.title,
            "chapter_id": None, "page": annotation.page, "quote": annotation.text,
            "annotation_id": annotation.id, "mastery": "unknown", "source_type": "annotation",
            "mark_type": getattr(annotation, "mark_type", "highlight"),
            "color": annotation.color, "origin": getattr(annotation, "origin", "user"),
            "created_at": annotation.created_at.isoformat(),
        })
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"total": len(items), "book_ids": scope, "items": items}


@router.get("/records")
def list_knowledge_records(
    book_ids: list[int] | None = Query(default=None),
    record_types: list[str] | None = Query(default=None),
    chapter_id: int | None = Query(default=None),
    color: str | None = Query(default=None),
    tag: str | None = Query(default=None, max_length=80),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
):
    """知识沉淀聚合读模型：返回统一 DTO，但绝不合并底层实体。"""
    scope = sorted({int(value) for value in (book_ids or []) if int(value) > 0})
    wanted = set(record_types or ["highlight", "underline", "annotation", "note", "evidence"])
    term = (q or "").strip().lower()
    records: list[dict] = []

    ann_stmt = select(Annotation, Book).join(Book, Book.id == Annotation.book_id)
    if scope:
        ann_stmt = ann_stmt.where(Annotation.book_id.in_(scope))
    if color:
        ann_stmt = ann_stmt.where(Annotation.color == color)
    if date_from:
        ann_stmt = ann_stmt.where(Annotation.created_at >= date_from)
    if date_to:
        ann_stmt = ann_stmt.where(Annotation.created_at <= date_to)
    for annotation, book in db.execute(ann_stmt).all():
        mark_type = getattr(annotation, "mark_type", "highlight") or "highlight"
        record_type = "annotation" if (annotation.note or "").strip() else mark_type
        if record_type not in wanted:
            continue
        searchable = f"{annotation.text or ''} {annotation.note or ''}".lower()
        if term and term not in searchable:
            continue
        records.append({
            "key": f"annotation:{annotation.id}", "entity_type": "annotation", "record_type": record_type,
            "id": annotation.id, "title": (annotation.note or annotation.text or "阅读标注")[:100],
            "content": annotation.note or "", "quote": annotation.text or "", "book_id": annotation.book_id,
            "book_title": book.title, "chapter_id": None, "chapter_title": None, "page": annotation.page,
            "color": annotation.color, "tags": [], "origin": getattr(annotation, "origin", "user"),
            "source_link": f"/reader/{annotation.book_id}?page={annotation.page}",
            "created_at": annotation.created_at.isoformat(),
        })

    if "note" in wanted:
        node_stmt = select(KnowledgeNote, Book, Chapter).join(Book, Book.id == KnowledgeNote.book_id).outerjoin(Chapter, Chapter.id == KnowledgeNote.chapter_id)
        if scope:
            node_stmt = node_stmt.where(KnowledgeNote.book_id.in_(scope))
        if chapter_id:
            node_stmt = node_stmt.where(KnowledgeNote.chapter_id == chapter_id)
        if date_from:
            node_stmt = node_stmt.where(KnowledgeNote.created_at >= date_from)
        if date_to:
            node_stmt = node_stmt.where(KnowledgeNote.created_at <= date_to)
        for node, book, chapter in db.execute(node_stmt).all():
            tags = json.loads(node.tags_json or "[]")
            if tag and tag not in tags:
                continue
            if term and term not in f"{node.title} {node.content or ''}".lower():
                continue
            page = chapter.start_page if chapter else None
            records.append({
                "key": f"note:{node.id}", "entity_type": "knowledge_note", "record_type": "note", "id": node.id,
                "title": node.title, "content": node.content or "", "quote": "", "book_id": node.book_id,
                "book_title": book.title if book else None, "chapter_id": node.chapter_id,
                "chapter_title": chapter.title if chapter else None, "page": node.page or page, "color": None, "tags": tags,
                "origin": node.origin, "source_annotation_id": node.source_annotation_id,
                "source_link": f"/reader/{node.book_id}?page={node.page or page or 1}" if node.book_id else None,
                "created_at": node.created_at.isoformat(),
            })

    if "evidence" in wanted:
        card_stmt = select(EvidenceCard, Book, Chapter).join(Book, Book.id == EvidenceCard.book_id).outerjoin(Chapter, Chapter.id == EvidenceCard.chapter_id)
        if scope:
            card_stmt = card_stmt.where(EvidenceCard.book_id.in_(scope))
        if chapter_id:
            card_stmt = card_stmt.where(EvidenceCard.chapter_id == chapter_id)
        if date_from:
            card_stmt = card_stmt.where(EvidenceCard.created_at >= date_from)
        if date_to:
            card_stmt = card_stmt.where(EvidenceCard.created_at <= date_to)
        for card, book, chapter in db.execute(card_stmt).all():
            tags = json.loads(card.tags_json or "[]")
            if tag and tag not in tags:
                continue
            if term and term not in f"{card.title} {card.evidence_text} {card.claim_text or ''}".lower():
                continue
            records.append({
                "key": f"evidence:{card.id}", "entity_type": "evidence_card", "record_type": "evidence", "id": card.id,
                "title": card.title, "content": card.claim_text or "", "quote": card.evidence_text,
                "book_id": card.book_id, "book_title": book.title, "chapter_id": card.chapter_id,
                "chapter_title": chapter.title if chapter else None, "page": card.page, "color": None,
                "tags": tags, "origin": card.origin, "verification_status": card.verification_status,
                "source_link": f"/reader/{card.book_id}?page={card.page or (chapter.start_page if chapter else 1)}",
                "created_at": card.created_at.isoformat(),
            })
    records.sort(key=lambda item: item["created_at"], reverse=True)
    return {"total": len(records), "book_ids": scope, "items": records}


@router.post("/annotations/{annotation_id}/promote", status_code=201)
def promote_annotation(annotation_id: int, db: Session = Depends(get_db)):
    annotation = db.get(Annotation, annotation_id)
    if not annotation:
        raise HTTPException(404, "标注不存在")
    existing = db.scalar(select(KnowledgeNote).where(KnowledgeNote.source_annotation_id == annotation_id))
    if existing:
        return {"id": existing.id, "title": existing.title, "book_id": existing.book_id}
    seed = (annotation.note or annotation.text or "阅读笔记").strip()
    note = KnowledgeNote(title=seed[:80], book_id=annotation.book_id, page=annotation.page,
                         content=annotation.note or annotation.text or "",
                         source_annotation_id=annotation.id, origin="user")
    db.add(note); db.commit(); db.refresh(note)
    return {"id": note.id, "title": note.title, "book_id": note.book_id}


def _note_payload(note: KnowledgeNote, db: Session) -> dict:
    book = db.get(Book, note.book_id)
    return {"id": note.id, "book_id": note.book_id, "book_title": book.title if book else None,
            "chapter_id": note.chapter_id, "page": note.page, "title": note.title,
            "content": note.content, "tags": json.loads(note.tags_json or "[]"),
            "origin": note.origin, "source_annotation_id": note.source_annotation_id,
            "created_at": note.created_at.isoformat()}


@router.post("/notes", status_code=201)
def create_knowledge_note(req: KnowledgeNoteCreateReq, db: Session = Depends(get_db)):
    if not db.get(Book, req.book_id):
        raise HTTPException(404, "书籍不存在")
    note = KnowledgeNote(book_id=req.book_id, chapter_id=req.chapter_id, page=req.page,
                         title=req.title.strip(), content=req.content,
                         tags_json=json.dumps(req.tags[:20], ensure_ascii=False), origin=req.origin)
    db.add(note); db.commit(); db.refresh(note)
    return _note_payload(note, db)


@router.patch("/notes/{note_id}")
def update_knowledge_note(note_id: int, req: KnowledgeNoteUpdateReq, db: Session = Depends(get_db)):
    note = db.get(KnowledgeNote, note_id)
    if not note:
        raise HTTPException(404, "知识笔记不存在")
    if req.title is not None:
        note.title = req.title.strip()
    if req.content is not None:
        note.content = req.content
    if req.tags is not None:
        note.tags_json = json.dumps(req.tags[:20], ensure_ascii=False)
    db.commit(); db.refresh(note)
    return _note_payload(note, db)


@router.delete("/notes/{note_id}", status_code=204)
def delete_knowledge_note(note_id: int, db: Session = Depends(get_db)):
    note = db.get(KnowledgeNote, note_id)
    if not note:
        raise HTTPException(404, "知识笔记不存在")
    db.delete(note); db.commit()


@router.post("/notes/{note_id}/add-to-tree", response_model=KnowledgeNodeResp, status_code=201)
def add_note_to_tree(note_id: int, parent_id: int | None = None, db: Session = Depends(get_db)):
    note = db.get(KnowledgeNote, note_id)
    if not note:
        raise HTTPException(404, "知识笔记不存在")
    if parent_id is not None:
        _get_node(db, parent_id)
    node = KnowledgeNode(parent_id=parent_id, title=note.title, book_id=note.book_id,
                         chapter_id=note.chapter_id, note=note.content, node_type="point")
    db.add(node); db.commit(); db.refresh(node)
    return _to_resp(node, db)


def _card_payload(card: EvidenceCard, db: Session) -> dict:
    book = db.get(Book, card.book_id)
    return {"id": card.id, "book_id": card.book_id, "book_title": book.title if book else None,
            "chapter_id": card.chapter_id, "page": card.page, "title": card.title,
            "evidence_text": card.evidence_text, "claim_text": card.claim_text,
            "tags": json.loads(card.tags_json or "[]"), "origin": card.origin,
            "verification_status": card.verification_status, "created_at": card.created_at.isoformat()}


@router.post("/evidence-cards", status_code=201)
def create_evidence_card(req: EvidenceCardCreateReq, db: Session = Depends(get_db)):
    if not db.get(Book, req.book_id):
        raise HTTPException(404, "书籍不存在")
    if req.chapter_id:
        chapter = db.get(Chapter, req.chapter_id)
        if not chapter or chapter.book_id != req.book_id:
            raise HTTPException(422, "章节与书目不匹配")
    card = EvidenceCard(book_id=req.book_id, chapter_id=req.chapter_id, page=req.page,
                        title=req.title.strip(), evidence_text=req.evidence_text.strip(),
                        claim_text=(req.claim_text or "").strip() or None,
                        tags_json=json.dumps(req.tags[:20], ensure_ascii=False), origin=req.origin,
                        verification_status=req.verification_status,
                        source_ref_json=json.dumps({"book_id": req.book_id, "chapter_id": req.chapter_id, "page": req.page}))
    db.add(card); db.commit(); db.refresh(card)
    return _card_payload(card, db)


@router.patch("/evidence-cards/{card_id}")
def update_evidence_card(card_id: int, req: EvidenceCardUpdateReq, db: Session = Depends(get_db)):
    card = db.get(EvidenceCard, card_id)
    if not card:
        raise HTTPException(404, "证据卡片不存在")
    for field in ("title", "evidence_text", "claim_text", "verification_status"):
        value = getattr(req, field)
        if value is not None:
            setattr(card, field, value.strip() if isinstance(value, str) else value)
    if req.tags is not None:
        card.tags_json = json.dumps(req.tags[:20], ensure_ascii=False)
    db.commit(); db.refresh(card)
    return _card_payload(card, db)


@router.delete("/evidence-cards/{card_id}", status_code=204)
def delete_evidence_card(card_id: int, db: Session = Depends(get_db)):
    card = db.get(EvidenceCard, card_id)
    if not card:
        raise HTTPException(404, "证据卡片不存在")
    db.delete(card); db.commit()


@router.patch("/nodes/{node_id}", response_model=KnowledgeNodeResp)
def update_node(node_id: int, req: KnowledgeNodeUpdateReq, db: Session = Depends(get_db)):
    node = _get_node(db, node_id)
    if req.title is not None:
        node.title = req.title.strip()
    if req.note is not None:
        node.note = req.note
    if req.node_type is not None:
        node.node_type = req.node_type
    if req.mastery is not None:
        node.mastery = req.mastery
    if req.ref_node_id is not None:
        if req.ref_node_id == node.id:
            raise HTTPException(400, "不能引用自身")
        _get_node(db, req.ref_node_id)  # 校验存在
        node.ref_node_id = req.ref_node_id
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
    # 删除知识组织不应连带删除阅读器中的原文高亮，只解除挂接关系。
    db.execute(update(Annotation).where(Annotation.knowledge_node_id.in_(ids)).values(knowledge_node_id=None))
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


@router.post("/import-chapters", response_model=list[KnowledgeNodeResp], status_code=201)
def import_chapters(req: KnowledgeImportReq, db: Session = Depends(get_db)):
    """一次可选多本，但每本创建独立根树，来源不会彼此混合。"""
    parent_id = _get_node(db, req.parent_node_id).id if req.parent_node_id is not None else None
    created_roots: list[KnowledgeNode] = []
    for root_order, book_id in enumerate(dict.fromkeys(req.book_ids)):
        book = db.get(Book, book_id)
        if not book:
            raise HTTPException(404, f"书籍 {book_id} 不存在")
        root = KnowledgeNode(parent_id=parent_id, title=f"《{book.title}》章节骨架",
                             book_id=book.id, order_index=root_order)
        db.add(root)
        db.flush()
        created_roots.append(root)
        chapters = db.scalars(
            select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.order_index)
        ).all()
        by_parent: dict[int | None, list[Chapter]] = {}
        for ch in chapters:
            by_parent.setdefault(ch.parent_id, []).append(ch)
        def build(parent_db_id: int, children: list[Chapter]) -> None:
            for ch in children:
                node = KnowledgeNode(parent_id=parent_db_id, title=ch.title, book_id=book.id,
                                     chapter_id=ch.id, order_index=ch.order_index)
                db.add(node)
                db.flush()
                build(node.id, by_parent.get(ch.id, []))
        build(root.id, by_parent.get(None, []))
    db.commit()
    return [_to_resp(root, db) for root in created_roots]


@router.post("/ai-generate", status_code=202)
def ai_generate(req: KnowledgeAiGenerateReq, db: Session = Depends(get_db)):
    """AI（DeepSeek）分析教材章节与关键词，生成课程知识框架树（后台任务）。"""
    from backend.app.services.llm import LLMRouter, load_llm_config
    from backend.app.worker.tasks import submit

    books = [db.get(Book, book_id) for book_id in dict.fromkeys(req.book_ids)]
    if any(book is None for book in books):
        raise HTTPException(404, "所选书籍中存在已删除项目")
    if any(book.status != "ready" for book in books):
        raise HTTPException(409, "所选书籍中存在尚未解析完成的项目")
    if req.parent_node_id is not None:
        _get_node(db, req.parent_node_id)

    from backend.app.models import BookAnalysis
    import json as _json
    materials = []
    for book in books:
        chapters = db.scalars(select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.order_index)).all()
        if not chapters:
            raise HTTPException(400, f"《{book.title}》没有章节")
        analysis = db.scalar(select(BookAnalysis).where(BookAnalysis.book_id == book.id))
        try: keywords = _json.loads(analysis.keywords_json or "[]")[:40] if analysis else []
        except (ValueError, TypeError): keywords = []
        materials.append({"book_id": book.id, "book_title": book.title,
                          "chapters": [f"{ch.order_index}. {ch.title}" for ch in chapters][:60],
                          "keywords": keywords})

    async def run(record):
        from backend.app.core.database import SessionLocal
        from backend.app.worker.tasks import update_progress
        update_progress(record, 0.1, "ai", "正在分析教材章节结构...")
        # 后台线程独立 Session（不共享请求级 Session）
        db2 = SessionLocal()
        try:
            cfg = load_llm_config(db2)
            cfg = {**cfg, "deepseek_model": "flash"}  # 批量生成固定用 flash
            provider = LLMRouter.get("auto", cfg)
            total = 0
            for material_index, material in enumerate(materials):
                update_progress(record, 0.1 + 0.75 * material_index / len(materials), "ai",
                                f"正在分析《{material['book_title']}》...")
                prompt = [
                {"role": "system", "content": (
                    "你是课程知识结构化助手。根据教材的章节目录和关键词，生成一份课程知识框架树"
                    "（帮助复习用的顶层结构，3 层以内）。只输出 JSON 数组，格式："
                    '[{"title":"一级主题","children":[{"title":"二级主题","children":[{"title":"三级主题","children":[]}]}]}]。'
                    "要求：1) 一级 3-8 个；2) 主题用概括性术语（可不同于原章节名）；"
                    "3) 覆盖全部关键词；4) 不要输出解释文字。"
                )},
                {"role": "user", "content": _json.dumps(material, ensure_ascii=False)},
                ]
                data = None
                last_err = ""
                for attempt in range(3):
                    try:
                        answer = ""
                        async for delta in provider.stream_chat(prompt): answer += delta
                        from backend.app.services.llm import parse_json_response
                        data = parse_json_response(answer)
                        if isinstance(data, list) and data: break
                        last_err = "AI 返回内容无法解析为 JSON 数组"
                    except Exception as e: last_err = str(e)
                    await asyncio.sleep(2 * (attempt + 1))
                if not isinstance(data, list) or not data:
                    raise RuntimeError(f"《{material['book_title']}》生成失败：{last_err}")
                total += _create_ai_tree(db2, req.parent_node_id, data, material)
            update_progress(record, 1.0, "ai", "完成")
            return {"created": total}
        finally:
            db2.close()

    record = submit("knowledge-ai", run)
    return {"task_id": record.id, "status": "running", "stage": "ai"}


def _create_ai_tree(db: Session, parent_node_id: int | None, data: list[dict], material: dict) -> int:
    """把 AI 返回的 JSON 树写入 knowledge_nodes，返回创建数。"""
    total = 0

    root = KnowledgeNode(parent_id=parent_node_id, title=f"《{material['book_title']}》AI 知识框架",
                         book_id=material["book_id"], order_index=0)
    db.add(root); db.flush(); total = 1
    def walk(items: list[dict], parent_id: int | None, depth: int) -> None:
        nonlocal total
        if depth > 3:
            return
        for i, item in enumerate(items[:12]):
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            node = KnowledgeNode(parent_id=parent_id, title=title, book_id=material["book_id"], order_index=i)
            db.add(node)
            db.flush()
            total += 1
            children = item.get("children") or []
            if isinstance(children, list) and children:
                walk(children, node.id, depth + 1)

    walk(data, root.id, 0)
    db.commit()
    return total

@router.get("/nodes/{node_id}/annotations", response_model=list)
def node_annotations(node_id: int, db: Session = Depends(get_db)):
    """知识树节点关联的 PDF 批注列表（双向联动）。"""
    from backend.app.models import Annotation
    from backend.app.schemas import AnnotationResp

    _get_node(db, node_id)
    items = db.scalars(
        select(Annotation).where(Annotation.knowledge_node_id == node_id)
        .order_by(Annotation.book_id, Annotation.page)
    ).all()
    out = []
    for a in items:
        book = db.get(Book, a.book_id)
        out.append({
            "id": a.id, "book_id": a.book_id, "book_title": book.title if book else "",
            "page": a.page, "rect_json": a.rect_json, "text": a.text or "",
            "color": a.color, "note": a.note or "", "created_at": a.created_at.isoformat(),
        })
    return out


@router.post("/nodes/expand", status_code=202)
def expand_node(req: KnowledgeNodeExpandReq, db: Session = Depends(get_db)):
    """AI 展开节点：只返回建议，不直接写入正式知识树。"""
    from backend.app.services.llm import LLMRouter, load_llm_config
    from backend.app.worker.tasks import submit

    node = _get_node(db, req.node_id)
    cfg = load_llm_config(db)
    if not cfg.get("deepseek_api_key"):
        raise HTTPException(400, "未配置 DeepSeek API Key")

    # 素材：节点标题 + 关联章节文本（若有）
    context = f"知识点：{node.title}"
    if node.chapter_id:
        chunks = db.scalars(
            select(Chunk).where(Chunk.chapter_id == node.chapter_id).order_by(Chunk.chunk_index).limit(10)
        ).all()
        body = "\n".join(c.content[:500] for c in chunks)[:6000]
        context += f"\n关联章节内容：\n{body}"

    async def run(record):
        from backend.app.core.database import SessionLocal
        from backend.app.worker.tasks import update_progress
        update_progress(record, 0.2, "expand", "AI 正在展开知识点…")
        # 后台线程独立 Session（不共享请求级 Session）
        db2 = SessionLocal()
        try:
            provider = LLMRouter.get("auto", cfg)
            prompt = [
                {"role": "system", "content": (
                    "你是知识结构化助手。给定一个知识点和（可选的）关联章节内容，"
                    "把它展开成 3-8 个下级知识点。只输出 JSON 数组："
                    '[{"title":"下级知识点","node_type":"concept|theorem|point|example|question"}]。'
                    "要求：下级知识点具体、可学习；类型合理（概念/定理/考点/例题/疑问）。"
                )},
                {"role": "user", "content": context},
            ]
            answer = ""
            for attempt in range(3):
                try:
                    answer = ""
                    async for delta in provider.stream_chat(prompt):
                        answer += delta
                    from backend.app.services.llm import parse_json_response
                    data = parse_json_response(answer)
                    if isinstance(data, list) and data:
                        break
                except Exception:  # noqa: BLE001
                    data = None
                import asyncio
                await asyncio.sleep(2)
            if not isinstance(data, list) or not data:
                raise RuntimeError("AI 展开失败，请稍后重试")

            update_progress(record, 0.8, "expand", "正在整理建议节点…")
            suggestions = []
            for item in data[:8]:
                title = str(item.get("title", "")).strip()
                if not title:
                    continue
                node_type = str(item.get("node_type", "concept"))[:20]
                if node_type not in {"concept", "theorem", "point", "example", "question"}:
                    node_type = "concept"
                suggestions.append({"title": title[:255], "node_type": node_type, "status": "suggested"})
            update_progress(record, 1.0, "expand", "完成")
            return {"created": 0, "suggestions": suggestions, "requires_confirmation": True}
        finally:
            db2.close()

    record = submit("knowledge-expand", run)
    return {"task_id": record.id, "status": "running"}


class ApplyNodeSuggestionsReq(BaseModel):
    suggestions: list[dict] = Field(min_length=1, max_length=8)


@router.post("/nodes/{node_id}/apply-suggestions", response_model=list[KnowledgeNodeResp], status_code=201)
def apply_node_suggestions(node_id: int, req: ApplyNodeSuggestionsReq, db: Session = Depends(get_db)):
    parent = _get_node(db, node_id)
    siblings = db.scalars(select(KnowledgeNode).where(KnowledgeNode.parent_id == parent.id)).all()
    base = max((item.order_index for item in siblings), default=-1)
    created = []
    for index, item in enumerate(req.suggestions[:8]):
        title = str(item.get("title") or "").strip()[:255]
        if not title:
            continue
        node_type = str(item.get("node_type") or "concept")[:20]
        if node_type not in {"concept", "theorem", "point", "example", "question"}:
            node_type = "concept"
        node = KnowledgeNode(parent_id=parent.id, title=title, book_id=parent.book_id,
                             chapter_id=parent.chapter_id, node_type=node_type, order_index=base + 1 + index)
        db.add(node); db.flush(); created.append(node)
    db.commit()
    return [_to_resp(node, db) for node in created]


@router.post("/batch-delete", status_code=204)
def batch_delete(req: KnowledgeBatchDeleteReq, db: Session = Depends(get_db)):
    """批量删除节点（含子树）。"""
    ids = set(req.node_ids)
    all_ids: set[int] = set()
    for nid in list(ids):
        node = db.get(KnowledgeNode, nid)
        if node:
            all_ids.update(_collect_ids(node, db))
    if all_ids:
        db.query(KnowledgeNode).filter(KnowledgeNode.id.in_(all_ids)).delete(synchronize_session=False)
        db.commit()


@router.post("/nodes/{node_id}/review-note")
async def review_note(node_id: int, db: Session = Depends(get_db)):
    """AI 批改节点笔记：评价 + 补充建议（基于节点标题与关联章节）。"""
    from backend.app.services.llm import LLMRouter, load_llm_config

    node = _get_node(db, node_id)
    cfg = load_llm_config(db)
    if not cfg.get("deepseek_api_key"):
        raise HTTPException(400, "未配置 DeepSeek API Key")
    provider = LLMRouter.get("auto", cfg)
    # 关联章节上下文（用于核对笔记准确性）
    ctx = ""
    if node.chapter_id:
        chunks = db.scalars(select(Chunk).where(Chunk.chapter_id == node.chapter_id).order_by(Chunk.chunk_index).limit(8)).all()
        ctx = "\n".join(c.content[:400] for c in chunks)[:4000]

    prompt = [
        {"role": "system", "content": (
            "你是学习笔记批改助手。用户为一个知识点写了笔记，请批改并给出建议："
            "1) 准确性：笔记是否准确（对照关联章节内容）2) 完整性：是否遗漏关键点 "
            "3) 表达：是否清晰。最后给一段优化后的笔记（Markdown）。用中文，分三段：【评价】【建议】【优化笔记】。"
        )},
        {"role": "user", "content": f"知识点：{node.title}\n用户笔记：{node.note or '（空）'}\n关联章节：{ctx[:3000] or '无'}"},
    ]
    answer = ""
    try:
        async for delta in provider.stream_chat(prompt):
            answer += delta
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"AI 批改失败: {e}")
    return {"review": answer}
