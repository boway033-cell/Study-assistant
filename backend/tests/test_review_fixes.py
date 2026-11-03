"""Isolated behavioral regressions for historical citations, reparse and backup."""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from backend.app.api.chat import _decorate_history_source
from backend.app.core.database import Base
from backend.app.models import Book, Chunk, RetiredChunk
from backend.app.services import book_lifecycle, source_identity
from backend.app.services.rag import fts


@pytest.fixture
def db():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(fts._CREATE_SQL))
    with Session(engine, autoflush=False) as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize("expected_hash", [None, "a" * 64])
def test_history_does_not_replace_unverified_identity_with_current_hash(db, expected_hash):
    book = Book(title="Demo", file_path="demo.pdf", file_type="pdf", status="ready")
    db.add(book)
    db.flush()
    chunk = Chunk(book_id=book.id, content="different new text", chunk_index=0)
    db.add(chunk)
    db.flush()
    db.add(RetiredChunk(book_id=book.id, chunk_id=chunk.id, content="old text",
                        content_hash=source_identity.content_hash("old text"), chunk_index=0))
    db.commit()

    original = {"book_id": book.id, "chunk_id": chunk.id, "snippet": "old text"}
    if expected_hash:
        original["content_hash"] = expected_hash
    decorated = _decorate_history_source(db, original)
    assert decorated["identity_status"] == ("invalid" if expected_hash else "needs_review")
    assert decorated.get("content_hash") == expected_hash
    # The frontend uses these exact fields when it loads the source panel.
    fetched = source_identity.verify_chunk_ref(
        db, book.id, chunk.id, decorated.get("content_hash"), decorated.get("source_version_uid")
    )
    assert fetched["identity_status"] != "ok"
    assert fetched["content"] is None


def test_valid_historical_identity_still_recovers_archived_text(db):
    book = Book(title="Demo", file_path="demo.pdf", file_type="pdf", status="ready")
    db.add(book)
    db.flush()
    chunk = Chunk(book_id=book.id, content="new text", chunk_index=0)
    db.add(chunk)
    db.flush()
    old_hash = source_identity.content_hash("original text")
    db.add(RetiredChunk(book_id=book.id, chunk_id=chunk.id, content="original text",
                        content_hash=old_hash, chunk_index=0))
    db.commit()
    decorated = _decorate_history_source(db, {"book_id": book.id, "chunk_id": chunk.id,
                                              "content_hash": old_hash, "snippet": "original"})
    fetched = source_identity.verify_chunk_ref(db, book.id, chunk.id, decorated["content_hash"])
    assert decorated["identity_status"] == fetched["identity_status"] == "stale"
    assert fetched["content"] == "original text"


@pytest.mark.parametrize("fail_activation", [False, True])
def test_reparse_without_old_chapters_replaces_or_rolls_back(db, monkeypatch, fail_activation):
    book = Book(title="Demo", file_path="demo.pdf", file_type="pdf", status="ready")
    db.add(book)
    db.flush()
    old = Chunk(book_id=book.id, chapter_id=None, content="old unique text", chunk_index=0)
    db.add(old)
    db.flush()
    old_id = old.id
    fts.replace_book_index_in_session(db, book.id, [old])
    db.commit()
    version = book_lifecycle.start_staging_version(db, book.id)
    chapters = book_lifecycle.stage_chapters(db, version, [{"title": "New", "level": 1,
                                                             "order_index": 0}])
    book_lifecycle.stage_chunks(db, version, [{"chapter_id": chapters[0].id,
                                               "content": "new unique text", "chunk_index": 0}])
    db.commit()
    if fail_activation:
        def fail_index(*_args):
            raise RuntimeError("simulated index failure")
        monkeypatch.setattr(fts, "replace_book_index_in_session", fail_index)
        with pytest.raises(RuntimeError, match="simulated"):
            book_lifecycle.activate_staged_version(db, book.id)
        db.rollback()
    else:
        book_lifecycle.activate_staged_version(db, book.id)
        db.commit()
    chunks = db.scalars(select(Chunk).where(Chunk.book_id == book.id)).all()
    assert [item.content for item in chunks] == ["old unique text" if fail_activation else "new unique text"]
    indexed = db.execute(text("SELECT chunk_id FROM fts_books WHERE book_id = :id"), {"id": book.id}).scalars().all()
    assert list(map(int, indexed)) == [chunks[0].id]
    if not fail_activation:
        archived = source_identity.verify_chunk_ref(db, book.id, old_id, source_identity.content_hash("old unique text"))
        assert archived["identity_status"] == "stale"
        assert archived["content"] == "old unique text"


def test_backup_resolves_dotenv_like_application(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".env").write_text('ROOT_DIR="from file"\nDATA_DIR=${ROOT_DIR}/library\n', encoding="utf-8")
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setenv("ROOT_DIR", "from environment")
    script = Path(__file__).resolve().parents[2] / "scripts/maintenance/backup.py"
    spec = importlib.util.spec_from_file_location("backup_under_test", script)
    backup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backup)
    application = subprocess.run(
        [sys.executable, "-c", "import json,os,sys; from dotenv import load_dotenv; "
         "load_dotenv(sys.argv[1]); print(json.dumps(os.environ.get('DATA_DIR')))" , str(root / ".env")],
        env=os.environ.copy(), capture_output=True, text=True, check=True,
    )
    resolved, _ = backup.resolve_data_dir(None, root)
    assert resolved == (root / json.loads(application.stdout)).resolve()
    monkeypatch.setenv("DATA_DIR", "override")
    assert backup.resolve_data_dir(None, root)[0] == (root / "override").resolve()


def test_backup_uses_interpolated_directory_and_restores_data(tmp_path, monkeypatch):
    root = tmp_path / "project"
    data = root / "actual" / "library"
    data.mkdir(parents=True)
    (root / ".env").write_text("ROOT_DIR=actual\nDATA_DIR=${ROOT_DIR}/library\n", encoding="utf-8")
    (data / "uploads").mkdir()
    (data / "uploads" / "fiction.txt").write_text("fictional attachment", encoding="utf-8")
    with sqlite3.connect(data / "study.db") as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('fictional data')")
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("ROOT_DIR", raising=False)
    script = Path(__file__).resolve().parents[2] / "scripts/maintenance/backup.py"
    spec = importlib.util.spec_from_file_location("backup_restore_test", script)
    backup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backup)
    monkeypatch.setattr(backup, "detect_running_service", lambda *_args: [])
    archive = tmp_path / "backup.zip"
    assert backup.main(["--project-root", str(root), "--output", str(archive),
                        "--work-dir", str(tmp_path), "--quiet"]) == 0
    restored = tmp_path / "restored"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(restored)
    assert (restored / "uploads" / "fiction.txt").read_text(encoding="utf-8") == "fictional attachment"
    with sqlite3.connect(restored / "study.db") as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("fictional data",)
