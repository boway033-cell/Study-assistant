"""标签 API：多对多关联书籍，支持自动/手动标签。

自动标签来源：
- 导入时从 keywords 取 top-5 自动建标签
- 文件类型标签（pdf/docx/pptx）
- 分类标签
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, BookAnalysis, Tag, book_tags

router = APIRouter(prefix="/api", tags=["tags"])


@router.get("/tags")
def list_tags(db: Session = Depends(get_db)):
    """列出全部标签 + 各标签关联的书籍数。"""
    tags = db.scalars(select(Tag).order_by(Tag.name)).all()
    result = []
    for t in tags:
        count = db.execute(
            select(book_tags.c.book_id).where(book_tags.c.tag_id == t.id)
        ).all()
        result.append({
            "id": t.id, "name": t.name, "color": t.color,
            "auto_generated": bool(t.auto_generated),
            "book_count": len(count),
        })
    return {"items": result}


@router.post("/tags")
def create_tag(req: dict, db: Session = Depends(get_db)):
    """创建新标签。"""
    name = (req.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")
    color = req.get("color") or "#8B5A2B"
    existing = db.scalar(select(Tag).where(Tag.name == name))
    if existing:
        return {"id": existing.id, "name": existing.name, "color": existing.color}
    tag = Tag(name=name, color=color)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return {"id": tag.id, "name": tag.name, "color": tag.color}


@router.post("/books/{book_id}/tags")
def add_book_tags(book_id: int, req: dict, db: Session = Depends(get_db)):
    """为书籍添加标签（传入 tag_ids 列表 或 tag_names 列表）。"""
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    tag_ids = req.get("tag_ids") or []
    tag_names = req.get("tag_names") or []

    # 按 name 创建/查找标签
    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        tag = db.scalar(select(Tag).where(Tag.name == name))
        if not tag:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        tag_ids.append(tag.id)

    # 关联（去重）
    existing_ids = set(r[0] for r in db.execute(
        select(book_tags.c.tag_id).where(book_tags.c.book_id == book_id)
    ).all())
    for tid in tag_ids:
        if tid not in existing_ids:
            db.execute(book_tags.insert().values(book_id=book_id, tag_id=tid))
    db.commit()
    return {"book_id": book_id, "tag_ids": list(set(tag_ids))}


@router.delete("/books/{book_id}/tags/{tag_id}", status_code=204)
def remove_book_tag(book_id: int, tag_id: int, db: Session = Depends(get_db)):
    """移除书籍的一个标签。"""
    db.execute(
        book_tags.delete().where(
            book_tags.c.book_id == book_id, book_tags.c.tag_id == tag_id
        )
    )
    db.commit()


@router.get("/books/{book_id}/tags")
def get_book_tags(book_id: int, db: Session = Depends(get_db)):
    """获取书籍的全部标签。"""
    rows = db.execute(
        select(Tag).join(book_tags, book_tags.c.tag_id == Tag.id)
        .where(book_tags.c.book_id == book_id)
    ).scalars().all()
    return {"items": [{"id": t.id, "name": t.name, "color": t.color,
                        "auto_generated": bool(t.auto_generated)} for t in rows]}


def auto_generate_tags(db: Session, book_id: int, max_tags: int = 5) -> list[int]:
    """导入完成后自动从关键词生成标签（供 import_task 调用）。

    返回创建/关联的 tag_id 列表。
    """
    book = db.get(Book, book_id)
    if not book:
        return []
    analysis = db.scalar(select(BookAnalysis).where(BookAnalysis.book_id == book_id))
    keywords: list[str] = []
    if analysis and analysis.keywords_json:
        try:
            keywords = json.loads(analysis.keywords_json)
        except (ValueError, TypeError):
            keywords = []

    tag_ids: list[int] = []
    # 文件类型标签
    type_tag = db.scalar(select(Tag).where(Tag.name == book.file_type))
    if not type_tag:
        type_tag = Tag(name=book.file_type, auto_generated=1)
        db.add(type_tag)
        db.flush()
    tag_ids.append(type_tag.id)

    # 分类标签
    if book.category:
        cat_tag = db.scalar(select(Tag).where(Tag.name == book.category))
        if not cat_tag:
            cat_tag = Tag(name=book.category, auto_generated=1)
            db.add(cat_tag)
            db.flush()
        tag_ids.append(cat_tag.id)

    # 关键词标签（top-N）
    for kw in keywords[:max_tags]:
        kw = kw.strip()
        if not kw or len(kw) < 2:
            continue
        tag = db.scalar(select(Tag).where(Tag.name == kw))
        if not tag:
            tag = Tag(name=kw, auto_generated=1)
            db.add(tag)
            db.flush()
        tag_ids.append(tag.id)

    # 关联（去重）
    existing_ids = set(r[0] for r in db.execute(
        select(book_tags.c.tag_id).where(book_tags.c.book_id == book_id)
    ).all())
    for tid in tag_ids:
        if tid not in existing_ids:
            db.execute(book_tags.insert().values(book_id=book_id, tag_id=tid))
    db.commit()
    return tag_ids
