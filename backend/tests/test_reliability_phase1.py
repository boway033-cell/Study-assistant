"""第一阶段可靠性回归测试。"""
from __future__ import annotations

import sqlite3
import asyncio
import io
import tracemalloc
from pathlib import Path
from types import SimpleNamespace


def test_upload_streaming_has_bounded_memory():
    from starlette.datastructures import UploadFile
    from backend.app.api.books import _store_validated_upload

    payload = b"%PDF-1.7\n" + (b"x" * (3 * 1024 * 1024))
    upload = UploadFile(io.BytesIO(payload), filename="bounded-memory.pdf")
    tracemalloc.start()
    path, digest, size = asyncio.run(_store_validated_upload(upload, "pdf"))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    try:
        assert size == len(payload)
        assert len(digest) == 64
        assert peak < 4 * 1024 * 1024
    finally:
        path.unlink(missing_ok=True)


def test_fts_version_round_trip():
    from backend.app.core.data_manager import (
        FTS_INDEX_VERSION,
        get_fts_index_version,
        set_fts_index_version,
    )

    set_fts_index_version(17)
    assert get_fts_index_version() == 17
    set_fts_index_version(FTS_INDEX_VERSION)


def test_global_task_center_reads_persisted_tasks():
    from fastapi.testclient import TestClient
    from backend.app.api.books import delete_book
    from backend.app.core.database import SessionLocal
    from backend.app.main import app
    from backend.app.models import Book, ImportTask

    db = SessionLocal()
    try:
        book = Book(title="Task center", file_path="missing.pdf", file_type="pdf", status="pending")
        db.add(book)
        db.flush()
        db.add(ImportTask(
            id="deep-task-center", book_id=book.id, name="deep", status="running",
            progress=0.42, stage="deep", message="正在分析",
        ))
        db.commit()
        book_id = book.id
        client = TestClient(app)
        response = client.get("/api/tasks", params={"active_only": True})
        client.close()
        assert response.status_code == 200
        item = next(item for item in response.json()["items"] if item["task_id"] == "deep-task-center")
        assert item["book_title"] == "Task center"
        assert item["progress"] == 0.42
        delete_book(book_id, db)
    finally:
        db.close()


def test_persisted_task_can_be_cancelled():
    from fastapi.testclient import TestClient
    from backend.app.core.database import SessionLocal
    from backend.app.main import app
    from backend.app.models import Book, ImportTask

    db = SessionLocal()
    try:
        book = Book(title="Cancellable OCR", file_path="missing.pdf", file_type="pdf", status="pending")
        db.add(book)
        db.flush()
        db.add(ImportTask(id="import-cancellable", book_id=book.id, name="import", status="pending", stage="ocr"))
        db.commit()
        client = TestClient(app)
        response = client.post("/api/tasks/import-cancellable/cancel")
        client.close()
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        db.expire_all()
        assert db.get(ImportTask, "import-cancellable").status == "cancelled"
    finally:
        db.close()


def test_sqlite_online_backup_includes_wal_commits(tmp_path: Path):
    from backend.app.core.data_manager import _sqlite_backup

    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    conn = sqlite3.connect(source)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE sample(value TEXT)")
    conn.execute("INSERT INTO sample VALUES ('committed-in-wal')")
    conn.commit()

    _sqlite_backup(source, backup)
    with sqlite3.connect(backup) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT value FROM sample").fetchone()[0] == "committed-in-wal"
    conn.close()


def test_fts_rebuild_crosses_small_batches(monkeypatch):
    from sqlalchemy import text
    from backend.app.api.books import delete_book
    from backend.app.core.database import SessionLocal, engine
    from backend.app.models import Book, Chunk
    from backend.app.services.rag import fts

    db = SessionLocal()
    try:
        book = Book(title="FTS batches", file_path="missing.pdf", file_type="pdf", status="ready")
        db.add(book)
        db.flush()
        db.add_all([
            Chunk(book_id=book.id, content=f"batch content {index}", chunk_index=index)
            for index in range(3)
        ])
        db.commit()
        book_id = book.id
        monkeypatch.setattr(fts, "REBUILD_BATCH_SIZE", 2)

        fts.init_fts(force_rebuild=True)
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM fts_books WHERE book_id=:book_id"),
                {"book_id": book_id},
            ).scalar_one()
        assert count == 3
        delete_book(book_id, db)
    finally:
        db.close()


def test_delete_book_cleans_foreign_keys_and_keeps_access_audit():
    from backend.app.api.books import delete_book
    from backend.app.core.database import SessionLocal
    from backend.app.models import (
        Annotation,
        Attempt,
        Book,
        Chapter,
        Chunk,
        ImportTask,
        KnowledgeNode,
        LiteratureAccessAttempt,
        Note,
        Quiz,
    )

    db = SessionLocal()
    try:
        book = Book(title="Delete lifecycle", file_path="missing.pdf", file_type="pdf", status="ready")
        other = Book(title="Cross reference", file_path="other.pdf", file_type="pdf", status="ready")
        db.add_all([book, other])
        db.flush()
        chapter = Chapter(book_id=book.id, title="One", level=1, order_index=0)
        db.add(chapter)
        db.flush()
        chunk = Chunk(book_id=book.id, chapter_id=chapter.id, content="text", chunk_index=0)
        quiz = Quiz(book_id=book.id, chapter_id=chapter.id, q_type="short", question="Q", answer="A")
        note = Note(book_id=book.id, chapter_id=chapter.id, page=1, content="note")
        node = KnowledgeNode(title="source", book_id=book.id, chapter_id=chapter.id)
        db.add_all([chunk, quiz, note, node])
        db.flush()
        db.add_all([
            Attempt(quiz_id=quiz.id, user_answer="A", is_correct=1),
            ImportTask(id="import-delete-lifecycle", book_id=book.id, name="import", status="done"),
            LiteratureAccessAttempt(query="doi", status="done", book_id=book.id),
            KnowledgeNode(title="external", book_id=other.id, ref_node_id=node.id),
            Annotation(book_id=other.id, page=1, rect_json="[]", knowledge_node_id=node.id),
        ])
        db.commit()
        book_id, other_id = book.id, other.id

        delete_book(book_id, db)

        assert db.get(Book, book_id) is None
        assert db.get(ImportTask, "import-delete-lifecycle") is None
        audit = db.query(LiteratureAccessAttempt).filter_by(query="doi").one()
        assert audit.book_id is None
        external = db.query(KnowledgeNode).filter_by(book_id=other_id).one()
        assert external.ref_node_id is None
        annotation = db.query(Annotation).filter_by(book_id=other_id).one()
        assert annotation.knowledge_node_id is None
        delete_book(other_id, db)
    finally:
        db.close()


def test_reparse_preserves_user_content_without_stale_chapter_fk(monkeypatch):
    import backend.app.api.books as books_api
    from backend.app.core.database import SessionLocal
    from backend.app.models import Book, Chapter, Chunk, KnowledgeNode, Note, Quiz

    monkeypatch.setattr(books_api, "submit", lambda *args, **kwargs: SimpleNamespace(id="reimport-test"))
    db = SessionLocal()
    try:
        book = Book(title="Reparse lifecycle", file_path="missing.pdf", file_type="pdf", status="ready")
        db.add(book)
        db.flush()
        chapter = Chapter(book_id=book.id, title="Old", level=1, order_index=0)
        db.add(chapter)
        db.flush()
        db.add_all([
            Chunk(book_id=book.id, chapter_id=chapter.id, content="old", chunk_index=0),
            Note(book_id=book.id, chapter_id=chapter.id, page=1, content="keep"),
            Quiz(book_id=book.id, chapter_id=chapter.id, q_type="short", question="keep", answer="yes"),
            KnowledgeNode(title="keep", book_id=book.id, chapter_id=chapter.id),
        ])
        db.commit()
        book_id = book.id

        assert books_api.reparse_book(book_id, db) == {"task_id": "reimport-test"}
        assert db.query(Chapter).filter_by(book_id=book_id).count() == 0
        assert db.query(Chunk).filter_by(book_id=book_id).count() == 0
        assert db.query(Note).filter_by(book_id=book_id).one().chapter_id is None
        assert db.query(Quiz).filter_by(book_id=book_id).one().chapter_id is None
        assert db.query(KnowledgeNode).filter_by(book_id=book_id).one().chapter_id is None
        books_api.delete_book(book_id, db)
    finally:
        db.close()
