"""书籍/资料 API（docs/03-api.md §1）"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, Chapter, Chunk, Note, PaperProfile, Quiz
from backend.app.schemas import (
    BookDetailResp,
    BookListItem,
    BookListResp,
    BookRenameReq,
    ChapterNode,
    NoteCreateReq,
    NoteResp,
    NoteUpdateReq,
    PaperProfileResp,
    PaperProfileUpdateReq,
    SearchResultItem,
    SearchResp,
    TaskResp,
)
from backend.app.services.rag import fts
from backend.app.worker.import_task import run_import
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

    # 统计各书题目数
    quiz_counts = dict(db.execute(
        select(Quiz.book_id, func.count()).group_by(Quiz.book_id)
    ).all())
    chapter_counts = dict(db.execute(
        select(Chapter.book_id, func.count()).group_by(Chapter.book_id)
    ).all())

    from backend.app.models import BookDeep, ImportTask
    deep_statuses = dict(db.execute(
        select(BookDeep.book_id, BookDeep.status).where(BookDeep.status != "none")
    ).all())
    # 进行中任务消息（导入/OCR 进度等）
    task_msgs = dict(db.execute(
        select(ImportTask.book_id, ImportTask.message)
        .where(ImportTask.status.in_(["pending", "running"]))
    ).all())
    profiles = {
        p.book_id: p for p in db.scalars(
            select(PaperProfile).where(PaperProfile.book_id.in_([b.id for b in books]))
        ).all()
    } if books else {}
    items = [
        BookListItem(
            id=b.id, title=b.title, file_type=b.file_type, status=b.status,
            total_pages=b.total_pages, chapter_count=chapter_counts.get(b.id, 0),
            quiz_count=quiz_counts.get(b.id, 0), category=b.category,
            deep_status=deep_statuses.get(b.id, "none"),
            task_message=task_msgs.get(b.id),
            authors=profiles[b.id].authors if b.id in profiles else None,
            journal=profiles[b.id].journal if b.id in profiles else None,
            published_year=profiles[b.id].published_year if b.id in profiles else None,
            doi=profiles[b.id].doi if b.id in profiles else None,
            reading_status=profiles[b.id].reading_status if b.id in profiles else "unread",
            favorite=bool(profiles[b.id].favorite) if b.id in profiles else False,
            progress_page=profiles[b.id].progress_page if b.id in profiles else 1,
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
    profile = db.get(PaperProfile, book_id)
    if not profile:
        profile = PaperProfile(book_id=book_id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return BookDetailResp(
        id=book.id, title=book.title, file_type=book.file_type, status=book.status,
        total_pages=book.total_pages, error_msg=book.error_msg,
        chapters=_build_chapter_tree(chapters), analysis=analysis,
        archive=_profile_resp(profile),
    )


def _profile_resp(profile: PaperProfile) -> PaperProfileResp:
    return PaperProfileResp(
        book_id=profile.book_id, authors=profile.authors, journal=profile.journal,
        published_year=profile.published_year, doi=profile.doi, arxiv_id=profile.arxiv_id,
        language=profile.language, abstract=profile.abstract, source_url=profile.source_url,
        access_route=profile.access_route or "local_upload",
        reading_status=profile.reading_status or "unread", favorite=bool(profile.favorite),
        rating=profile.rating, progress_page=profile.progress_page or 1,
        last_read_at=profile.last_read_at,
    )


@router.get("/books/{book_id}/archive", response_model=PaperProfileResp)
def get_archive_profile(book_id: int, db: Session = Depends(get_db)):
    if not db.get(Book, book_id):
        raise HTTPException(404, "书籍不存在")
    profile = db.get(PaperProfile, book_id)
    if not profile:
        profile = PaperProfile(book_id=book_id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return _profile_resp(profile)


@router.patch("/books/{book_id}/archive", response_model=PaperProfileResp)
def update_archive_profile(book_id: int, req: PaperProfileUpdateReq, db: Session = Depends(get_db)):
    if not db.get(Book, book_id):
        raise HTTPException(404, "书籍不存在")
    profile = db.get(PaperProfile, book_id) or PaperProfile(book_id=book_id)
    values = req.model_dump(exclude_unset=True)
    if "reading_status" in values and values["reading_status"] not in ("unread", "reading", "read"):
        raise HTTPException(400, "reading_status 仅支持 unread/reading/read")
    for key, value in values.items():
        setattr(profile, key, int(value) if key == "favorite" and value is not None else value)
    if "progress_page" in values or "reading_status" in values:
        from datetime import datetime
        profile.last_read_at = datetime.now()
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return _profile_resp(profile)


@router.get("/books/{book_id}/source-map")
def get_source_map(book_id: int, db: Session = Depends(get_db)):
    import json
    if not db.get(Book, book_id):
        raise HTTPException(404, "书籍不存在")
    profile = db.get(PaperProfile, book_id)
    if not profile or not profile.source_map_json:
        return {"version": 1, "book_id": book_id, "locator_mode": "unavailable", "blocks": []}
    try:
        return json.loads(profile.source_map_json)
    except (ValueError, TypeError):
        return {"version": 1, "book_id": book_id, "locator_mode": "unavailable", "blocks": []}


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


def _validate_upload_path(path: Path, file_type: str) -> None:
    """文件签名校验（防改扩展名伪装）+ 压缩炸弹检查。"""
    with path.open("rb") as stream:
        head = stream.read(16)
    if file_type == "pdf":
        if not head.lstrip().startswith(b"%PDF-"):
            raise HTTPException(400, "文件内容不是有效的 PDF（缺少 PDF 签名）")
    else:  # docx / pptx 是 ZIP 容器
        _pk = b"PK" + bytes([3, 4])
        _pk5 = b"PK" + bytes([5, 6])
        _pk7 = b"PK" + bytes([7, 8])
        if not (head.startswith(_pk) or head.startswith(_pk5) or head.startswith(_pk7)):
            raise HTTPException(400, f"文件内容不是有效的 {file_type.upper()}（缺少 ZIP 结构）")
        import zipfile as _zip
        try:
            with _zip.ZipFile(path) as z:
                entries = z.infolist()
                if len(entries) > 10000:
                    raise HTTPException(400, "压缩包文件项过多，已拒绝导入")
                unpacked = sum(i.file_size for i in entries)
                if unpacked > 500 * 1024 * 1024:
                    raise HTTPException(400, "文件解压后过大，疑似压缩炸弹")
        except _zip.BadZipFile:
            raise HTTPException(400, f"文件内容不是有效的 {file_type.upper()}（ZIP 结构损坏）")


async def _store_validated_upload(file: UploadFile, file_type: str) -> tuple[Path, str, int]:
    """以固定 1MB 缓冲落盘并计算哈希，不在内存中保留整份文献。"""
    import hashlib
    from backend.app.core.config import settings as _settings

    MAX_SIZE = 200 * 1024 * 1024
    safe_name = Path(file.filename or f"upload.{file_type}").name
    path = _settings.uploads_dir / safe_name
    stem, suffix = path.stem, path.suffix
    index = 1
    while path.exists():
        path = _settings.uploads_dir / f"{stem}_{index}{suffix}"
        index += 1

    total = 0
    digest = hashlib.sha256()
    try:
        with path.open("xb") as destination:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_SIZE:
                    raise HTTPException(400, "文件超过 200MB 限制")
                destination.write(chunk)
                digest.update(chunk)
        if not total:
            raise HTTPException(400, "文件为空")
        _validate_upload_path(path, file_type)
        return path, digest.hexdigest(), total
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _check_duplicate(db: Session, file_hash: str) -> "Book | None":
    """按文件哈希查重，返回已存在的 Book（若有）。"""
    return db.scalar(select(Book).where(Book.file_hash == file_hash).limit(1))


@router.post("/books/upload", status_code=201)
async def upload_book(file: UploadFile, db: Session = Depends(get_db)):
    file_type = (file.filename or "").rsplit(".", 1)[-1].lower()
    if file_type not in ("pdf", "docx", "pptx"):
        raise HTTPException(400, f"不支持的文件类型: {file_type}，仅支持 pdf/docx/pptx")

    path, file_hash, file_size = await _store_validated_upload(file, file_type)

    # 去重：相同哈希的文件已存在则跳过解析
    existing = _check_duplicate(db, file_hash)
    if existing:
        try:
            path.unlink()
        except OSError:
            pass
        return {"id": existing.id, "title": existing.title, "file_type": existing.file_type,
                "status": existing.status, "task_id": None, "duplicate": True,
                "message": f"文件已存在：《{existing.title}》", "created_at": existing.created_at}

    book = Book(
        title=Path(file.filename).stem,
        file_path=path.name,
        file_type=file_type,
        file_size=file_size,
        file_hash=file_hash,
        status="pending",
    )
    db.add(book)
    db.commit()
    db.refresh(book)

    record = submit("import", lambda rec: run_import(rec, book.id), book_id=book.id)
    return {"id": book.id, "title": book.title, "file_type": book.file_type,
            "status": book.status, "task_id": record.id, "created_at": book.created_at}


@router.post("/books/upload-batch", status_code=201)
async def upload_books_batch(files: list[UploadFile], db: Session = Depends(get_db)):
    """批量上传多个文件。逐个校验+去重+提交解析任务（FIFO 队列串行执行）。"""
    results: list[dict] = []
    for file in files:
        file_type = (file.filename or "").rsplit(".", 1)[-1].lower()
        if file_type not in ("pdf", "docx", "pptx"):
            results.append({"filename": file.filename, "error": f"不支持的文件类型: {file_type}"})
            continue
        try:
            path, file_hash, file_size = await _store_validated_upload(file, file_type)

            existing = _check_duplicate(db, file_hash)
            if existing:
                try:
                    path.unlink()
                except OSError:
                    pass
                results.append({
                    "filename": file.filename, "id": existing.id, "title": existing.title,
                    "status": existing.status, "duplicate": True,
                    "message": f"文件已存在：《{existing.title}》",
                })
                continue

            book = Book(
                title=Path(file.filename).stem,
                file_path=path.name,
                file_type=file_type,
                file_size=file_size,
                file_hash=file_hash,
                status="pending",
            )
            db.add(book)
            db.commit()
            db.refresh(book)
            record = submit("import", lambda rec, bid=book.id: run_import(rec, bid), book_id=book.id)
            results.append({
                "filename": file.filename, "id": book.id, "title": book.title,
                "status": "pending", "task_id": record.id,
            })
        except HTTPException as e:
            results.append({"filename": file.filename, "error": e.detail})
        except Exception as e:  # noqa: BLE001
            results.append({"filename": file.filename, "error": str(e)})
    return {"results": results}


@router.patch("/books/{book_id}")
def rename_book(book_id: int, req: BookRenameReq, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    book.title = req.title
    db.commit()
    return {"id": book.id, "title": book.title}


@router.get("/books/{book_id}/file")
def get_book_file(book_id: int, db: Session = Depends(get_db)):
    """返回原始文件（浏览器可直接打开/渲染，支持 #page=N 定位）。"""
    from fastapi.responses import FileResponse
    from backend.app.core.config import settings as _settings

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    path = _settings.uploads_dir / book.file_path
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    media = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }.get(book.file_type, "application/octet-stream")
    # content_disposition_type=inline：浏览器内嵌展示（pdf.js / iframe 均可用），不触发下载
    return FileResponse(path, media_type=media, filename=book.file_path,
                        content_disposition_type="inline")


@router.get("/books/{book_id}/chunk/{chunk_id}")
def get_chunk_original(chunk_id: int, book_id: int, db: Session = Depends(get_db)):
    """返回 chunk 全文 + 页码区间（供右侧原文定位面板）。"""
    from backend.app.models import Chunk as ChunkModel

    ch = db.get(ChunkModel, chunk_id)
    if not ch or ch.book_id != book_id:
        raise HTTPException(404, "内容不存在")
    return {
        "chunk_id": ch.id,
        "book_id": ch.book_id,
        "chapter_id": ch.chapter_id,
        "content": ch.content,
        "page_start": ch.page_start,
        "page_end": ch.page_end,
    }


@router.get("/books/{book_id}/page/{page_no}")
def get_page_original(book_id: int, page_no: int, db: Session = Depends(get_db)):
    """返回指定页原文文本（PDF 页文本；docx/pptx 无页码概念则返回空）。"""
    from backend.app.core.config import settings as _settings
    from backend.app.services.parser import parse_document

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    if book.file_type != "pdf":
        return {"page": page_no, "text": "", "note": "该格式不支持按页查看"}
    result = parse_document(_settings.uploads_dir / book.file_path)
    if 1 <= page_no <= len(result.pages):
        return {"page": page_no, "text": result.pages[page_no - 1]}
    raise HTTPException(404, "页码超出范围")


@router.delete("/books/{book_id}", status_code=204)
def delete_book(book_id: int, db: Session = Depends(get_db)):
    from backend.app.core.config import settings as _settings
    from backend.app.services.book_lifecycle import delete_book_records

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    file_path = book.file_path
    try:
        deck_files = delete_book_records(db, book_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    # 数据库提交后再删文件，避免事务失败造成不可恢复的数据丢失。
    for deck_file in deck_files:
        try:
            (_settings.presentations_dir / Path(deck_file).name).unlink(missing_ok=True)
        except OSError:
            pass
    # 删除上传文件与 FTS 索引（仅项目 data 目录内）
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
    # 清理向量（若开启）
    try:
        from backend.app.services.rag import vector
        vector.delete_book_vectors(book_id)
    except Exception:  # noqa: BLE001
        pass


@router.post("/books/{book_id}/reparse")
def reparse_book(book_id: int, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    # 清空可再生解析产物；笔记/题目/知识节点保留，仅解除旧章节关联。
    from backend.app.services.book_lifecycle import prepare_book_for_reparse
    prepare_book_for_reparse(db, book_id)
    try:
        from backend.app.services.rag import vector
        vector.delete_book_vectors(book_id)
    except Exception:  # noqa: BLE001
        pass
    book.status = "pending"
    book.error_msg = None
    db.commit()
    record = submit("reimport", lambda rec: run_import(rec, book.id), book_id=book.id)
    return {"task_id": record.id}


@router.get("/search", response_model=SearchResp)
def search(
    q: str = Query(min_length=1),
    book_id: int | None = Query(default=None),
    book_ids: str | None = Query(default=None),  # 逗号分隔的 book_id 列表
    category: str | None = Query(default=None),
    tag_id: int | None = Query(default=None),
    chapter_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """跨资料全文+语义混合检索（RRF 融合）。

    支持按 book_id / book_ids / category / tag_id 过滤。
    """
    # 解析 book_ids 参数
    ids_list: list[int] | None = None
    if book_ids:
        try:
            ids_list = [int(x.strip()) for x in book_ids.split(",") if x.strip()]
        except ValueError:
            ids_list = None

    # 按 category / tag 过滤出 book_ids
    filter_ids: set[int] | None = None
    if category:
        cat_books = db.scalars(select(Book).where(Book.category == category)).all()
        filter_ids = {b.id for b in cat_books}
    if tag_id:
        from backend.app.models import book_tags
        tagged = db.execute(select(book_tags.c.book_id).where(book_tags.c.tag_id == tag_id)).all()
        tag_ids_set = {r[0] for r in tagged}
        filter_ids = tag_ids_set if filter_ids is None else (filter_ids & tag_ids_set)

    # 合并 book_id / book_ids / filter_ids
    final_ids: list[int] | None = None
    if ids_list:
        final_ids = ids_list
    elif book_id is not None:
        final_ids = [book_id]
    if filter_ids is not None:
        if final_ids:
            final_ids = [bid for bid in final_ids if bid in filter_ids]
        else:
            final_ids = list(filter_ids)

    # 走混合检索（RRF 融合：向量 + FTS + LIKE）
    from backend.app.services.rag import retriever
    items = retriever.retrieve(q, book_ids=final_ids, top_k=page_size)

    # 分页（混合检索结果已在内存中，手动切片）
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    paged = items[start:end]

    return SearchResp(
        total=total,
        items=[
            SearchResultItem(
                chunk_id=it.get("chunk_id", 0),
                book_id=it.get("book_id", 0),
                book_title=it.get("book_title", ""),
                chapter_id=it.get("chapter_id"),
                chapter_title=it.get("chapter_title"),
                page=it.get("page"),
                page_start=it.get("page_start") or it.get("page"),
                page_end=it.get("page_end") or it.get("page"),
                snippet=it.get("snippet", ""),
            ) for it in paged
        ],
    )


@router.get("/tasks/{task_id}", response_model=TaskResp)
def get_task_status(task_id: str):
    record = get_task(task_id)
    if not record:
        raise HTTPException(404, "任务不存在")
    return TaskResp(
        task_id=record.id, status=record.status, progress=record.progress,
        stage=record.stage, message=record.message, error=record.error,
        result=record.result,
    )


@router.get("/tasks")
def list_task_statuses(
    active_only: bool = Query(default=False),
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """全局任务中心：读取持久化任务，刷新或重启后仍可查看。"""
    from backend.app.models import ImportTask

    query = (
        select(ImportTask, Book.title)
        .outerjoin(Book, Book.id == ImportTask.book_id)
        .order_by(
            case((ImportTask.status.in_(["pending", "running"]), 0), else_=1),
            ImportTask.updated_at.desc(),
            ImportTask.created_at.desc(),
        )
        .limit(limit)
    )
    if active_only:
        query = query.where(ImportTask.status.in_(["pending", "running"]))
    rows = db.execute(query).all()
    return {
        "items": [
            {
                "task_id": task.id,
                "book_id": task.book_id,
                "book_title": title or "",
                "name": task.name,
                "status": task.status,
                "progress": task.progress or 0.0,
                "stage": task.stage or "",
                "message": task.message or "",
                "error": task.error,
                "retry_count": task.retry_count or 0,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
            }
            for task, title in rows
        ]
    }


@router.get("/books/{book_id}/document")
def get_document(book_id: int, db: Session = Depends(get_db)):
    """返回结构化文档（章节树 + 每章正文），供 docx/pptx 文本阅读器使用。"""
    from backend.app.models import Chunk as _Chunk
    from backend.app.models import Chapter as _Chapter

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    chapters = db.scalars(
        select(_Chapter).where(_Chapter.book_id == book_id).order_by(_Chapter.order_index)
    ).all()
    chunks = db.scalars(
        select(_Chunk).where(_Chunk.book_id == book_id).order_by(_Chunk.chunk_index)
    ).all()
    by_chapter: dict[int | None, list[str]] = {}
    for c in chunks:
        by_chapter.setdefault(c.chapter_id, []).append(c.content)
    return {
        "book_id": book.id,
        "title": book.title,
        "file_type": book.file_type,
        "chapters": [
            {"id": ch.id, "title": ch.title, "level": ch.level or 1, "order_index": ch.order_index,
             "parent_id": ch.parent_id}
            for ch in chapters
        ],
        "sections": [
            {"chapter_id": ch.id, "title": ch.title, "text": "\n\n".join(by_chapter.get(ch.id, []))}
            for ch in chapters
        ],
    }


@router.patch("/chapters/{chapter_id}")
def rename_chapter(chapter_id: int, req: dict, db: Session = Depends(get_db)):
    """重命名章节标题（docx/pptx 目录编辑）。"""
    from backend.app.models import Chapter as _Chapter

    ch = db.get(_Chapter, chapter_id)
    if not ch:
        raise HTTPException(404, "章节不存在")
    title = (req.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    ch.title = title
    db.commit()
    return {"id": ch.id, "title": ch.title}


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
