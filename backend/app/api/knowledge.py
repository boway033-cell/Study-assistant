"""知识树 API — 用户自主搭建知识结构，可关联书籍章节展示原文"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, Chapter, Chunk, KnowledgeNode
from backend.app.schemas import (
    KnowledgeAiGenerateReq,
    KnowledgeImportReq,
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


@router.post("/import-chapters", response_model=KnowledgeNodeResp, status_code=201)
def import_chapters(req: KnowledgeImportReq, db: Session = Depends(get_db)):
    """从书籍章节树一键导入知识树骨架（本地数据，无需 AI）。"""
    book = db.get(Book, req.book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")

    # 确定父节点：指定了就用它，否则新建《书名》根节点
    if req.parent_node_id is not None:
        parent = _get_node(db, req.parent_node_id)
        parent_id = parent.id
        root_created = None
    else:
        root = KnowledgeNode(parent_id=None, title=f"《{book.title}》章节骨架", order_index=0)
        db.add(root)
        db.flush()
        parent_id = root.id
        root_created = root

    chapters = db.scalars(
        select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.order_index)
    ).all()
    # 按 parent_id 组装层级
    by_parent: dict[int | None, list[Chapter]] = {}
    for ch in chapters:
        by_parent.setdefault(ch.parent_id, []).append(ch)

    def build(parent_db_id: int, children: list[Chapter]) -> None:
        for ch in children:
            node = KnowledgeNode(
                parent_id=parent_db_id, title=ch.title, book_id=book.id,
                chapter_id=ch.id, order_index=ch.order_index,
            )
            db.add(node)
            db.flush()
            if ch.id in by_parent:
                build(node.id, by_parent[ch.id])

    build(parent_id, by_parent.get(None, []))
    db.commit()
    if root_created is not None:
        db.refresh(root_created)
        return _to_resp(root_created, db)
    parent = _get_node(db, parent_id)
    return _to_resp(parent, db)


@router.post("/ai-generate", status_code=202)
def ai_generate(req: KnowledgeAiGenerateReq, db: Session = Depends(get_db)):
    """AI（DeepSeek）分析教材章节与关键词，生成课程知识框架树（后台任务）。"""
    from backend.app.services.llm import LLMRouter, load_llm_config
    from backend.app.worker.tasks import submit

    book = db.get(Book, req.book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    if book.status != "ready":
        raise HTTPException(409, "书籍尚未解析完成")
    if req.parent_node_id is not None:
        _get_node(db, req.parent_node_id)

    chapters = db.scalars(
        select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.order_index)
    ).all()
    if not chapters:
        raise HTTPException(400, "书籍没有章节")

    from backend.app.models import BookAnalysis
    import json as _json

    analysis = db.scalar(select(BookAnalysis).where(BookAnalysis.book_id == book.id))
    keywords = []
    if analysis and analysis.keywords_json:
        try:
            keywords = _json.loads(analysis.keywords_json)[:40]
        except (ValueError, TypeError):
            keywords = []
    chapter_titles = [f"{ch.order_index}. {ch.title}" for ch in chapters][:60]
    material = {
        "book_title": book.title,
        "chapters": chapter_titles,
        "keywords": keywords,
    }

    async def run(record):
        from backend.app.worker.tasks import update_progress
        update_progress(record, 0.1, "ai", "正在分析教材章节结构...")
        cfg = load_llm_config(db)
        cfg = {**cfg, "deepseek_model": "flash"}  # 批量生成固定用 flash
        provider = LLMRouter.get("auto", cfg)
        prompt = [
            {"role": "system", "content": (
                "你是课程知识结构化助手。根据教材的章节目录和关键词，生成一份课程知识框架树"
                "（帮助复习用的顶层结构，3 层以内）。只输出 JSON 数组，格式："
                '[{"title":"一级主题","children":[{"title":"二级主题","children":[{"title":"三级主题","children":[]}]}]}]。',
                "要求：1) 一级 3-8 个；2) 主题用概括性术语（可不同于原章节名）；"
                "3) 覆盖全部关键词；4) 不要输出解释文字。"
            )},
            {"role": "user", "content": _json.dumps(material, ensure_ascii=False)},
        ]
        answer = ""
        try:
            async for delta in provider.stream_chat(prompt):
                answer += delta
            from backend.app.services.llm import parse_json_response
            data = parse_json_response(answer)
            if not isinstance(data, list):
                raise ValueError("非数组")
        except Exception:  # noqa: BLE001
            raise RuntimeError("AI 生成失败，请重试或改用手动/章节导入")

        update_progress(record, 0.6, "ai", "正在写入知识树...")
        total = _create_ai_tree(db, req, data)
        update_progress(record, 1.0, "ai", "完成")
        return {"created": total}

    record = submit("knowledge-ai", run)
    return {"task_id": record.id, "status": "running", "stage": "ai"}


def _create_ai_tree(db: Session, req: KnowledgeAiGenerateReq, data: list[dict]) -> int:
    """把 AI 返回的 JSON 树写入 knowledge_nodes，返回创建数。"""
    total = 0

    def walk(items: list[dict], parent_id: int | None, depth: int) -> None:
        nonlocal total
        if depth > 3:
            return
        for i, item in enumerate(items[:12]):
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            node = KnowledgeNode(parent_id=parent_id, title=title, order_index=i)
            db.add(node)
            db.flush()
            total += 1
            children = item.get("children") or []
            if isinstance(children, list) and children:
                walk(children, node.id, depth + 1)

    walk(data, req.parent_node_id, 0)
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
