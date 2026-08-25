"""虚拟书架 API：层级集合、多书架归属，不移动底层文件。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, Shelf, shelf_books

router = APIRouter(prefix="/api/shelves", tags=["shelves"])


class ShelfWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: int | None = None
    description: str | None = Field(default=None, max_length=1000)
    color: str = Field(default="#8B5A2B", pattern=r"^#[0-9A-Fa-f]{6}$")


class ShelfPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: int | None = None
    description: str | None = Field(default=None, max_length=1000)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    order_index: int | None = None


class ShelfBooksWrite(BaseModel):
    book_ids: list[int] = Field(min_length=1, max_length=500)
    mode: str = "add"


def _would_cycle(db: Session, shelf_id: int, parent_id: int | None) -> bool:
    current = parent_id
    seen = {shelf_id}
    while current is not None:
        if current in seen:
            return True
        seen.add(current)
        row = db.get(Shelf, current)
        current = row.parent_id if row else None
    return False


def _row(shelf: Shelf, count: int = 0) -> dict:
    return {"id": shelf.id, "name": shelf.name, "parent_id": shelf.parent_id,
            "description": shelf.description, "color": shelf.color,
            "order_index": shelf.order_index, "book_count": count}


@router.get("")
def list_shelves(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(shelf_books.c.shelf_id, func.count())
                             .group_by(shelf_books.c.shelf_id)).all())
    rows = db.scalars(select(Shelf).order_by(Shelf.order_index, Shelf.created_at)).all()
    return [_row(row, counts.get(row.id, 0)) for row in rows]


@router.post("", status_code=201)
def create_shelf(req: ShelfWrite, db: Session = Depends(get_db)):
    if req.parent_id is not None and not db.get(Shelf, req.parent_id):
        raise HTTPException(400, "父书架不存在")
    row = Shelf(**req.model_dump(), order_index=db.scalar(select(func.count()).select_from(Shelf)) or 0)
    db.add(row); db.commit(); db.refresh(row)
    return _row(row)


@router.patch("/{shelf_id}")
def update_shelf(shelf_id: int, req: ShelfPatch, db: Session = Depends(get_db)):
    row = db.get(Shelf, shelf_id)
    if not row:
        raise HTTPException(404, "书架不存在")
    values = req.model_dump(exclude_unset=True)
    if "parent_id" in values:
        if values["parent_id"] is not None and not db.get(Shelf, values["parent_id"]):
            raise HTTPException(400, "父书架不存在")
        if _would_cycle(db, shelf_id, values["parent_id"]):
            raise HTTPException(400, "书架不能移动到自身或其子书架")
    for key, value in values.items():
        setattr(row, key, value)
    db.commit(); db.refresh(row)
    return _row(row)


@router.delete("/{shelf_id}", status_code=204)
def delete_shelf(shelf_id: int, db: Session = Depends(get_db)):
    row = db.get(Shelf, shelf_id)
    if not row:
        raise HTTPException(404, "书架不存在")
    db.delete(row); db.commit()


@router.put("/{shelf_id}/books")
def put_shelf_books(shelf_id: int, req: ShelfBooksWrite, db: Session = Depends(get_db)):
    if not db.get(Shelf, shelf_id):
        raise HTTPException(404, "书架不存在")
    if req.mode not in {"add", "replace"}:
        raise HTTPException(400, "mode 仅支持 add/replace")
    valid = set(db.scalars(select(Book.id).where(Book.id.in_(set(req.book_ids)))).all())
    if len(valid) != len(set(req.book_ids)):
        raise HTTPException(400, "部分文献不存在")
    if req.mode == "replace":
        db.execute(delete(shelf_books).where(shelf_books.c.shelf_id == shelf_id))
    existing = set(db.scalars(select(shelf_books.c.book_id).where(shelf_books.c.shelf_id == shelf_id)).all())
    for index, book_id in enumerate(req.book_ids):
        if book_id not in existing:
            db.execute(insert(shelf_books).values(shelf_id=shelf_id, book_id=book_id, order_index=index))
    db.commit()
    return {"shelf_id": shelf_id, "book_ids": sorted(valid), "mode": req.mode}


@router.delete("/{shelf_id}/books/{book_id}", status_code=204)
def remove_shelf_book(shelf_id: int, book_id: int, db: Session = Depends(get_db)):
    db.execute(delete(shelf_books).where(shelf_books.c.shelf_id == shelf_id,
                                         shelf_books.c.book_id == book_id))
    db.commit()
