"""书籍/资料 API（docs/03-api.md §1）"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, Card, Chapter, Chunk, Note, Quiz
from backend.app.schemas import (
    BookDetailResp,
    BookListItem,
    BookListResp,
    BookRenameReq,
    ChapterNode,
    NoteCreateReq,
    NoteResp,
    NoteUpdateReq,
    SearchResp,
    TaskResp,
)
from backend.app.services.rag import fts
from backend.app.worker.import_task import run_import, save_upload
from backend.app.worker.tasks import get_task, submit

router = APIRouter(prefix="/api", tags=["books"])


def _build_chapter_tree(chapters: list[Chapter]) -> list[ChapterNode]:
    nodes = {
        c.id: ChapterNode(
            id=c.id, title=c.title, level=c.level, order_index=c.order_index,
            start_page=c.start_page, end_page=c.end_page, children=[],
        )
        for c in chapters
    }
    roots: list[ChapterNode] = []
    for c in chapters:
        node = nodes[c.id]
        if c.parent_id and c.parent_id in nodes:
            nodes[c.parent_id].children.append(node)
        else:
            roots.append(node)
    return roots


@router.get("/books", response_model=BookListResp)
def list_books(
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    q = select(Book)
    if status:
        q = q.where(Book.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    books = db.scalars(q.order_by(Book.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()

    # 统计各书的卡片/题目数
    card_counts = dict(db.execute(
        select(Card.book_id, func.count()).group_by(Card.book_id)
    ).all())
    quiz_counts = dict(db.execute(
        select(Quiz.book_id, func.count()).group_by(Quiz.book_id)
    ).all())
    chapter_counts = dict(db.execute(
        select(Chapter.book_id, func.count()).group_by(Chapter.book_id)
    ).all())

    items = [
        BookListItem(
            id=b.id, title=b.title, file_type=b.file_type, status=b.status,
            total_pages=b.total_pages, chapter_count=chapter_counts.get(b.id, 0),
            card_count=card_counts.get(b.id, 0), quiz_count=quiz_counts.get(b.id, 0),
            created_at=b.created_at,
        )
        for b in books
    ]
    return BookListResp(total=total, items=items)


@router.get("/books/{book_id}", response_model=BookDetailResp)
def get_book(book_id: int, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    chapters = db.scalars(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_index)
    ).all()
    analysis = _get_analysis(db, book_id)
    return BookDetailResp(
        id=book.id, title=book.title, file_type=book.file_type, status=book.status,
        total_pages=book.total_pages, error_msg=book.error_msg,
        chapters=_build_chapter_tree(chapters), analysis=analysis,
    )


def _get_analysis(db: Session, book_id: int):
    """读取智能分析结果。"""
    import json
    from backend.app.models import BookAnalysis

    a = db.scalar(select(BookAnalysis).where(BookAnalysis.book_id == book_id))
    if not a:
        return None
    def _load(s):
        try:
            return json.loads(s) if s else []
        except json.JSONDecodeError:
            return []
    table_pages = _load(a.table_pages)
    return {
        "definitions": _load(a.definitions_json),
        "theorems": _load(a.theorems_json),
        "keywords": _load(a.keywords_json),
        "body_size": a.body_size,
        "header_count": a.header_count,
        "footer_count": a.footer_count,
        "table_pages": table_pages,
    }


@router.post("/books/upload", status_code=201)
async def upload_book(file: UploadFile, db: Session = Depends(get_db)):
    file_type = (file.filename or "").rsplit(".", 1)[-1].lower()
    if file_type not in ("pdf", "docx", "pptx"):
        raise HTTPException(400, f"不支持的文件类型: {file_type}，仅支持 pdf/docx/pptx")

    content = await file.read()
    if not content:
        raise HTTPException(400, "文件为空")
    if len(content) > 200 * 1024 * 1024:
        raise HTTPException(400, "文件超过 200MB 限制")

    path = save_upload(file.filename, content)
    book = Book(
        title=Path(file.filename).stem,
        file_path=path.name,
        file_type=file_type,
        file_size=len(content),
        status="pending",
    )
    db.add(book)
    db.commit()
    db.refresh(book)

    record = submit("import", lambda rec: run_import(rec, book.id))
    return {"id": book.id, "title": book.title, "file_type": book.file_type,
            "status": book.status, "task_id": record.id, "created_at": book.created_at}


@router.patch("/books/{book_id}")
def rename_book(book_id: int, req: BookRenameReq, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    book.title = req.title
    db.commit()
    return {"id": book.id, "title": book.title}


@router.delete("/books/{book_id}", status_code=204)
def delete_book(book_id: int, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    file_path = book.file_path
    db.delete(book)
    db.commit()
    # 删除上传文件与 FTS 索引（仅项目 data 目录内）
    from backend.app.core.config import settings as _settings
    f = _settings.uploads_dir / file_path
    try:
        if f.exists():
            f.unlink()
    except OSError:
        pass
    try:
        fts.delete_book_index(book_id)
    except Exception:  # noqa: BLE001
        pass


@router.post("/books/{book_id}/reparse")
def reparse_book(book_id: int, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    # 清空旧章节/chunks
    db.query(Chunk).filter(Chunk.book_id == book_id).delete()
    db.query(Chapter).filter(Chapter.book_id == book_id).delete()
    book.status = "pending"
    book.error_msg = None
    db.commit()
    record = submit("import", lambda rec: run_import(rec, book.id))
    return {"task_id": record.id}


@router.get("/search", response_model=SearchResp)
def search(
    q: str = Query(min_length=1),
    book_id: int | None = Query(default=None),
    chapter_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    result = fts.search(q, book_id=book_id, chapter_id=chapter_id, page=page, page_size=page_size)
    return result


@router.get("/tasks/{task_id}", response_model=TaskResp)
def get_task_status(task_id: str):
    record = get_task(task_id)
    if not record:
        raise HTTPException(404, "任务不存在")
    return TaskResp(
        task_id=record.id, status=record.status, progress=record.progress,
        stage=record.stage, message=record.message, result=record.result,
    )


@router.get("/books/{book_id}/notes", response_model=list[NoteResp])
def list_notes(book_id: int, db: Session = Depends(get_db)):
    notes = db.scalars(
        select(Note).where(Note.book_id == book_id).order_by(Note.page)
    ).all()
    return [NoteResp(id=n.id, book_id=n.book_id, chapter_id=n.chapter_id, page=n.page,
                     content=n.content, created_at=n.created_at) for n in notes]


@router.post("/books/{book_id}/notes", response_model=NoteResp, status_code=201)
def create_note(book_id: int, req: NoteCreateReq, db: Session = Depends(get_db)):
    if not db.get(Book, book_id):
        raise HTTPException(404, "书籍不存在")
    note = Note(book_id=book_id, page=req.page, content=req.content,
                highlight_json=req.highlight_json)
    db.add(note)
    db.commit()
    db.refresh(note)
    return NoteResp(id=note.id, book_id=note.book_id, chapter_id=note.chapter_id,
                    page=note.page, content=note.content, created_at=note.created_at)


@router.patch("/notes/{note_id}", response_model=NoteResp)
def update_note(note_id: int, req: NoteUpdateReq, db: Session = Depends(get_db)):
    note = db.get(Note, note_id)
    if not note:
        raise HTTPException(404, "笔记不存在")
    if req.content is not None:
        note.content = req.content
    if req.highlight_json is not None:
        note.highlight_json = req.highlight_json
    db.commit()
    return NoteResp(id=note.id, book_id=note.book_id, chapter_id=note.chapter_id,
                    page=note.page, content=note.content, created_at=note.created_at)


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note = db.get(Note, note_id)
    if not note:
        raise HTTPException(404, "笔记不存在")
    db.delete(note)
    db.commit()
