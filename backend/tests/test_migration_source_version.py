"""P1-1 迁移回归：旧版数据库补列、回填 legacy 来源版本，不伪造历史指纹。"""
from __future__ import annotations

import importlib

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.app.core.database import Base
from backend.app.models import Book, Chapter, Chunk, Note
from backend.app.services import book_lifecycle
from backend.app.services.source_identity import (
    STATUS_LEGACY_UNVERIFIED,
    content_hash,
    verify_chunk_ref,
)

NEW_COLUMNS = {
    "books": ("active_source_version_id", "staging_source_version_id"),
    "chapters": ("chapter_key", "source_version_id"),
    "chunks": ("content_hash", "source_version_id"),
}
NEW_INDEXES = (
    "ix_books_active_source_version_id",
    "ix_books_staging_source_version_id",
    "ix_chapters_chapter_key",
    "ix_chapters_source_version_id",
    "ix_chunks_content_hash",
    "ix_chunks_source_version_id",
)


def _make_legacy_database(path):
    """建当前 schema 后回退成旧版：新增列与版本表全部移除，模拟真实旧库。"""
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = Book(title="旧库文献", file_path="legacy.pdf", file_type="pdf", status="ready")
        db.add(book)
        db.flush()
        chapter = Chapter(book_id=book.id, title="第一章", level=1, order_index=0)
        db.add(chapter)
        db.flush()
        chunk = Chunk(book_id=book.id, chapter_id=chapter.id, content="旧正文内容",
                      chunk_index=0, word_count=3)
        db.add(chunk)
        db.add(Note(book_id=book.id, chapter_id=chapter.id, page=1, content="用户旧笔记"))
        db.commit()
        ids = (book.id, chapter.id, chunk.id)
    with engine.begin() as conn:
        for name in NEW_INDEXES:
            conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        for table, columns in NEW_COLUMNS.items():
            for column in columns:
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        for table in ("staged_chunks", "staged_chapters", "retired_chunks", "source_versions"):
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
    return engine, ids


def _columns(engine, table):
    with engine.connect() as conn:
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}


def _indexes(engine):
    with engine.connect() as conn:
        return {row[0] for row in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index'")).fetchall()}


def test_migrate_and_backfill_legacy_database(tmp_path, monkeypatch):
    main = importlib.import_module("backend.app.main")
    engine, (book_id, chapter_id, chunk_id) = _make_legacy_database(tmp_path / "legacy.db")
    try:
        assert "active_source_version_id" not in _columns(engine, "books")
        # 启动顺序与 lifespan 一致：create_all 补新表，_migrate 给已有表补列。
        Base.metadata.create_all(engine)
        monkeypatch.setattr(main, "engine", engine)
        main._migrate()

        for table, columns in NEW_COLUMNS.items():
            assert set(columns) <= _columns(engine, table)
        assert set(NEW_INDEXES) <= _indexes(engine)

        with Session(engine) as db:
            first = book_lifecycle.backfill_legacy_source_versions(db)
            assert first == 1
            db.expire_all()
            book = db.get(Book, book_id)
            assert book.active_source_version_id is not None
            from backend.app.models import SourceVersion

            row = db.get(SourceVersion, book.active_source_version_id)
            assert row.status == "legacy"
            assert row.seq == 1
            chunk = db.get(Chunk, chunk_id)
            assert chunk.source_version_id == row.id
            assert chunk.content_hash == content_hash("旧正文内容")
            chapter = db.get(Chapter, chapter_id)
            assert chapter.source_version_id == row.id
            assert chapter.chapter_key
            note = db.query(Note).filter_by(book_id=book_id).one()
            assert note.chapter_id == chapter_id
            # 旧引用仍明确标为未验证，而不是伪造成已验证。
            verdict = verify_chunk_ref(db, book_id, chunk_id)
            assert verdict["status"] == STATUS_LEGACY_UNVERIFIED
            assert verdict["content"] == "旧正文内容"
            # 重复启动幂等：不重复建版本、不覆盖已有回填。
            assert book_lifecycle.backfill_legacy_source_versions(db) == 0
            assert db.query(SourceVersion).count() == 1

        main._migrate()  # 二次迁移不抛错、不重复加列
        assert set(NEW_COLUMNS["books"]) <= _columns(engine, "books")
    finally:
        engine.dispose()
