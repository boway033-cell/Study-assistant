"""P1-1 回归：重解析中途失败/取消不得破坏旧版本、旧引用与检索。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.app.core.database import SessionLocal
from backend.app.models import (
    Book,
    Chapter,
    Chunk,
    ImportTask,
    Note,
    SourceVersion,
    StagedChapter,
    StagedChunk,
)
from backend.app.services import book_lifecycle
from backend.app.services.rag import fts
from backend.app.services.source_identity import content_hash, verify_chunk_ref


def _make_ready_book(db, title="Atomic reparse", content="original text", hashed=True):
    book = Book(title=title, file_path="virtual.pdf", file_type="pdf", status="ready")
    db.add(book)
    db.flush()
    chapter = Chapter(book_id=book.id, title="Chapter One", level=1, order_index=0)
    db.add(chapter)
    db.flush()
    chunk = Chunk(
        book_id=book.id,
        chapter_id=chapter.id,
        content=content,
        chunk_index=0,
        content_hash=content_hash(content) if hashed else None,
    )
    db.add_all([chunk, Note(book_id=book.id, chapter_id=chapter.id, page=1, content="keep")])
    db.commit()
    fts.replace_book_index_in_session(db, book.id, [chunk])
    db.commit()
    return book.id, chapter.id, chunk.id


def _stage_new_version(db, book_id, content="new version text", title="Chapter One"):
    version = book_lifecycle.start_staging_version(db, book_id)
    staged = book_lifecycle.stage_chapters(db, version, [{
        "title": title, "level": 1, "parent_id": None, "order_index": 0,
        "start_page": 1, "end_page": 1,
    }])
    book_lifecycle.stage_chunks(db, version, [{
        "chapter_id": staged[0].id, "content": content, "page_start": 1, "page_end": 1,
        "chunk_index": 0, "word_count": 3,
    }])
    db.commit()
    return version


def test_run_import_failure_keeps_old_version_and_fts(monkeypatch):
    from backend.app.worker import import_task

    db = SessionLocal()
    try:
        book_id, chapter_id, chunk_id = _make_ready_book(db)

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated parser crash")

        monkeypatch.setattr(import_task, "parse_document", _boom)
        import asyncio

        record = import_task.TaskRecord(id="atomic-fail", name="reimport", book_id=book_id)
        with pytest.raises(RuntimeError):
            asyncio.run(import_task.run_import(record, book_id))

        db.expire_all()
        book = db.get(Book, book_id)
        assert book.status == "ready"
        assert db.query(Chunk).filter_by(book_id=book_id).count() == 1
        assert db.query(Chunk).filter_by(book_id=book_id).one().content == "original text"
        assert db.query(Note).filter_by(book_id=book_id).one().chapter_id == chapter_id
        assert book.staging_source_version_id is None
        versions = db.scalars(select(SourceVersion).where(SourceVersion.book_id == book_id)).all()
        assert all(v.status != "staging" for v in versions)
        assert db.query(StagedChapter).filter_by(book_id=book_id).count() == 0
        assert db.query(StagedChunk).filter_by(book_id=book_id).count() == 0
        assert fts.search("original", book_id=book_id)["items"]
    finally:
        db.close()


def test_abandon_staging_keeps_old_content(monkeypatch):
    db = SessionLocal()
    try:
        book_id, chapter_id, chunk_id = _make_ready_book(db)
        version = _stage_new_version(db, book_id)
        assert db.get(Book, book_id).staging_source_version_id == version.id

        book_lifecycle.abandon_staged_version(db, book_id, version.id)
        db.commit()

        db.expire_all()
        assert db.get(Book, book_id).status == "ready"
        assert db.get(Book, book_id).staging_source_version_id is None
        assert db.get(SourceVersion, version.id).status == "abandoned"
        assert db.query(StagedChunk).filter_by(book_id=book_id).count() == 0
        assert db.query(Chunk).filter_by(book_id=book_id).one().content == "original text"
        assert db.query(Note).filter_by(book_id=book_id).one().chapter_id == chapter_id
        assert fts.search("original", book_id=book_id)["items"]
    finally:
        db.close()


def test_cleanup_stale_staging_on_restart_keeps_old_version():
    db = SessionLocal()
    try:
        book_id, chapter_id, chunk_id = _make_ready_book(db)
        version = _stage_new_version(db, book_id)
        removed = book_lifecycle.cleanup_stale_staging_versions(db)
        assert removed >= 1
        db.expire_all()
        assert db.get(SourceVersion, version.id).status == "abandoned"
        assert db.get(Book, book_id).staging_source_version_id is None
        assert db.get(Book, book_id).status == "ready"
        assert db.query(Chunk).filter_by(book_id=book_id).one().content == "original text"
    finally:
        db.close()


def test_queued_cancel_does_not_touch_content(monkeypatch):
    from backend.app.worker import tasks

    db = SessionLocal()
    try:
        book_id, chapter_id, chunk_id = _make_ready_book(db)
        db.add(ImportTask(id="queued-reparse", book_id=book_id, name="reimport", status="pending",
                          progress=0.0))
        db.commit()
        # 排队阶段没有内存记录：cancel_task 应直接从持久化状态取消。
        monkeypatch.setattr(tasks, "_persist", lambda record: None)
        record = tasks.cancel_task("queued-reparse")
        assert record is not None and record.status == "cancelled"
        db.expire_all()
        assert db.get(Book, book_id).status == "ready"
        assert db.query(Chunk).filter_by(book_id=book_id).one().content == "original text"
        assert fts.search("original", book_id=book_id)["items"]
    finally:
        db.close()


def test_recover_resumes_reimport_for_ready_book(monkeypatch):
    from backend.app.worker import tasks

    db = SessionLocal()
    try:
        book_id, chapter_id, chunk_id = _make_ready_book(db)
        db.add(ImportTask(id="recover-reparse", book_id=book_id, name="reimport", status="pending",
                          progress=0.0))
        db.commit()
        stale = _stage_new_version(db, book_id)
        # 模拟进程中断：数据库中残留 staging 版本 + pending 任务。
        captured = []
        monkeypatch.setattr(tasks, "_ensure_backend", lambda: None)
        monkeypatch.setattr(tasks, "_loop_for", lambda name: None)
        monkeypatch.setattr(tasks, "_queue_for", lambda name: SimpleNamespace(put=lambda rec: None))
        monkeypatch.setattr(
            tasks.asyncio, "run_coroutine_threadsafe",
            lambda coro, loop: SimpleNamespace(),
        )
        recovered = tasks.recover_pending_tasks()
        assert "recover-reparse" in recovered
        db.expire_all()
        # 旧版本仍可使用；暂存版本被清理；任务没有因为 ready 被误标 done。
        assert db.get(Book, book_id).status == "ready"
        assert db.get(SourceVersion, stale.id).status == "abandoned"
        assert db.query(Chunk).filter_by(book_id=book_id).one().content == "original text"
        row = db.get(ImportTask, "recover-reparse")
        assert row.status == "pending"
        assert fts.search("original", book_id=book_id)["items"]
    finally:
        db.close()


def test_duplicate_reparse_submission_does_not_mutate_content(monkeypatch):
    import backend.app.api.books as books_api
    from backend.app.worker.tasks import DuplicateTaskError

    db = SessionLocal()
    try:
        book_id, chapter_id, chunk_id = _make_ready_book(db)

        def _duplicate(*args, **kwargs):
            raise DuplicateTaskError("已有任务")

        # 冲突检查与预留现在在 submit_unique 的同一把锁内完成。
        monkeypatch.setattr(books_api, "submit_unique", _duplicate)

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            books_api.reparse_book(book_id, db)
        assert exc.value.status_code == 409
        assert db.query(Chunk).filter_by(book_id=book_id).one().content == "original text"
        assert db.get(Book, book_id).status == "ready"
    finally:
        db.close()


def test_activation_failure_rolls_back_content_fts_and_refs(monkeypatch):
    """在 FTS 写入之后抛错：整个激活事务回滚，旧正文/FTS/章节引用原样可用。"""
    db = SessionLocal()
    try:
        book_id, chapter_id, chunk_id = _make_ready_book(db)
        version = _stage_new_version(db, book_id, content="should never be committed")

        real_replace = fts.replace_book_index_in_session

        def _replace_then_crash(session, bid, chunks):
            real_replace(session, bid, chunks)
            raise RuntimeError("simulated activation crash after FTS write")

        monkeypatch.setattr(fts, "replace_book_index_in_session", _replace_then_crash)
        with pytest.raises(RuntimeError):
            book_lifecycle.activate_staged_version(db, book_id)
        db.rollback()

        db.expire_all()
        book = db.get(Book, book_id)
        assert book.status == "ready"
        assert book.active_source_version_id is None
        assert book.staging_source_version_id == version.id
        assert db.get(SourceVersion, version.id).status == "staging"
        assert db.query(Chunk).filter_by(book_id=book_id).one().content == "original text"
        assert db.query(Note).filter_by(book_id=book_id).one().chapter_id == chapter_id
        hits = fts.search("original", book_id=book_id)["items"]
        assert hits and hits[0]["chunk_id"] == chunk_id
        assert not fts.search("never", book_id=book_id)["items"]

        # 恢复后重新激活应成功，证明失败没有留下半切换状态。
        monkeypatch.undo()
        book_lifecycle.activate_staged_version(db, book_id)
        db.commit()
        db.expire_all()
        assert db.get(Book, book_id).status == "ready"
        assert db.query(Chunk).filter_by(book_id=book_id).one().content == "should never be committed"
        assert fts.search("never", book_id=book_id)["items"]
    finally:
        db.close()


@pytest.mark.parametrize("outcome", ["failure", "cancel", "success"])
def test_reparse_layout_changes_only_on_activation(tmp_path, monkeypatch, outcome):
    from backend.app.services.parser import ParseResult
    from backend.app.services.parser.structured import StructuredDocument
    from backend.app.worker import import_task

    monkeypatch.setattr(book_lifecycle.settings, "structured_dir", tmp_path)
    with SessionLocal() as db:
        book_id, _, _ = _make_ready_book(db)
        book = db.get(Book, book_id)
        legacy_path = book_lifecycle.active_layout_path(db, book)
        StructuredDocument(backend="old-layout").save_json(legacy_path)

    parsed = ParseResult(pages=["Synthetic new text for a parsed document. " * 8], total_pages=1,
                         structured=StructuredDocument(backend="new-layout"))
    monkeypatch.setattr(import_task, "parse_document", lambda *_: parsed)
    record = import_task.TaskRecord(id="layout-audit", name="reimport", book_id=book_id)
    real_activate = import_task.activate_staged_version

    def activate(session, bid):
        assert book_lifecycle.active_layout_path(session, session.get(Book, bid)) == legacy_path
        result = real_activate(session, bid)
        if outcome == "failure":
            raise RuntimeError("failure after cache write and database activation")
        if outcome == "cancel":
            record.cancel_requested = True
        return result

    monkeypatch.setattr(import_task, "activate_staged_version", activate)
    if outcome == "success":
        asyncio.run(import_task.run_import(record, book_id))
    else:
        expected = RuntimeError if outcome == "failure" else import_task.TaskCancelled
        with pytest.raises(expected):
            asyncio.run(import_task.run_import(record, book_id))

    with SessionLocal() as db:
        book = db.get(Book, book_id)
        assert book.status == "ready"
        assert book.staging_source_version_id is None
        assert StructuredDocument.load_json(legacy_path).backend == "old-layout"
        path = book_lifecycle.active_layout_path(db, book)
        if outcome == "success":
            assert path != legacy_path
            assert StructuredDocument.load_json(path).backend == "new-layout"
            path.unlink()
            assert book_lifecycle.active_layout_path(db, book) == path
        else:
            assert path == legacy_path
            assert db.scalar(select(Chunk.content).where(Chunk.book_id == book_id)) == "original text"
            assert list(tmp_path.iterdir()) == [legacy_path]
