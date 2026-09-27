"""Resolve assistant scopes once, before retrieval or memory selection."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import AssistantProject, Book, Shelf, shelf_books


def resolve_scope(db: Session, scope_type: str, scope_id: int) -> tuple[list[int], str]:
    if scope_type == "shelf":
        shelf = db.get(Shelf, scope_id)
        if shelf is None:
            raise HTTPException(404, "书架不存在")
        ids = db.scalars(select(shelf_books.c.book_id).where(
            shelf_books.c.shelf_id == scope_id).order_by(shelf_books.c.order_index)).all()
        return list(dict.fromkeys(ids)), shelf.name
    if scope_type == "project":
        project = db.get(AssistantProject, scope_id)
        if project is None:
            raise HTTPException(404, "项目不存在")
        ids = {book.id for book in project.books}
        for shelf in project.shelves:
            ids.update(db.scalars(select(shelf_books.c.book_id).where(
                shelf_books.c.shelf_id == shelf.id)).all())
        return sorted(ids), project.name
    if scope_type == "book":
        book = db.get(Book, scope_id)
        if book is None:
            raise HTTPException(404, "书籍不存在")
        return [book.id], book.title
    raise HTTPException(422, "范围类型仅支持 book、shelf、project")
