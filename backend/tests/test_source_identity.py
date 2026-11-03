"""P1-1 回归：来源身份与旧引用校验语义。"""
from __future__ import annotations

from sqlalchemy import select

from backend.app.core.database import SessionLocal
from backend.app.models import Book, Chapter, Chunk, SourceVersion
from backend.app.services import book_lifecycle, source_identity
from backend.app.services.source_identity import (
    STATUS_INVALID,
    STATUS_LEGACY_UNVERIFIED,
    STATUS_MISSING,
    STATUS_NEEDS_REVIEW,
    STATUS_OK,
    STATUS_STALE,
    content_hash,
    reference_identity,
    verify_chunk_ref,
)


def _book(db, title, content="legacy body", with_hash=False):
    book = Book(title=title, file_path="virtual.pdf", file_type="pdf", status="ready")
    db.add(book)
    db.flush()
    chapter = Chapter(book_id=book.id, title="Intro", level=1, order_index=0)
    db.add(chapter)
    db.flush()
    chunk = Chunk(
        book_id=book.id, chapter_id=chapter.id, content=content, chunk_index=0,
        content_hash=content_hash(content) if with_hash else None,
    )
    db.add(chunk)
    db.commit()
    return book.id, chapter.id, chunk.id


def _reparse(db, book_id, new_content="reparsed body"):
    # 与真实启动流程一致：旧库先补 legacy 版本，归档时才能记录来源版本。
    book_lifecycle.backfill_legacy_source_versions(db)
    version = book_lifecycle.start_staging_version(db, book_id)
    staged = book_lifecycle.stage_chapters(db, version, [{
        "title": "Intro", "level": 1, "parent_id": None, "order_index": 0,
        "start_page": 1, "end_page": 1,
    }])
    book_lifecycle.stage_chunks(db, version, [{
        "chapter_id": staged[0].id, "content": new_content, "page_start": 1, "page_end": 1,
        "chunk_index": 0, "word_count": 2,
    }])
    db.commit()
    return book_lifecycle.activate_staged_version(db, book_id)


def test_legacy_reference_without_fingerprint_is_explicitly_unverified():
    db = SessionLocal()
    try:
        book_id, chapter_id, chunk_id = _book(db, "Legacy unverified")
        result = verify_chunk_ref(db, book_id, chunk_id)
        assert result["status"] == STATUS_LEGACY_UNVERIFIED
        assert result["identity_verified"] is False
        assert result["content"] == "legacy body"
        assert result["chapter_id"] == chapter_id
    finally:
        db.close()


def test_reparsed_reference_returns_archived_original_or_needs_review():
    db = SessionLocal()
    try:
        book_id, chapter_id, chunk_id = _book(db, "Reparse identity", content="before reparse")
        original_hash = content_hash("before reparse")
        _reparse(db, book_id, "after reparse")
        db.commit()

        verified = verify_chunk_ref(db, book_id, chunk_id, expected_hash=original_hash)
        assert verified["status"] == STATUS_STALE
        assert verified["identity_verified"] is True
        assert verified["content"] == "before reparse"
        assert verified["source_version_uid"]

        unverifiable = verify_chunk_ref(db, book_id, chunk_id)
        assert unverifiable["status"] == STATUS_NEEDS_REVIEW
        assert unverifiable["identity_verified"] is False
        assert unverifiable["content"] is None

        # 当前新版本的引用可正常校验。
        new_chunk = db.scalar(select(Chunk).where(Chunk.book_id == book_id))
        ok = verify_chunk_ref(db, book_id, new_chunk.id, expected_hash=content_hash("after reparse"))
        assert ok["status"] == STATUS_OK
        assert ok["content"] == "after reparse"
    finally:
        db.close()


def test_invalid_hash_cannot_silently_open_new_body():
    db = SessionLocal()
    try:
        book_id, chapter_id, chunk_id = _book(db, "Invalid hash", content="current body")
        result = verify_chunk_ref(db, book_id, chunk_id, expected_hash=content_hash("some other text"))
        assert result["status"] == STATUS_INVALID
        assert result["identity_verified"] is False
        assert result["content"] is None
    finally:
        db.close()


def test_missing_and_cross_book_references_are_rejected():
    db = SessionLocal()
    try:
        book_a, _, _ = _book(db, "Book A")
        _, _, chunk_b = _book(db, "Book B", content="belongs to b")
        missing = verify_chunk_ref(db, book_a, 999999)
        assert missing["status"] == STATUS_MISSING
        cross = verify_chunk_ref(db, book_a, chunk_b)
        assert cross["status"] == STATUS_INVALID
        assert cross["content"] is None
    finally:
        db.close()


def test_reference_identity_records_hash_and_version():
    db = SessionLocal()
    try:
        book_id, _, chunk_id = _book(db, "Reference identity", content="identity body")
        _reparse(db, book_id, "identity body")  # 内容相同的重解析也应有版本指纹
        db.commit()
        active = source_identity.active_version(db, book_id)
        assert active is not None
        new_chunk = db.scalar(select(Chunk).where(Chunk.book_id == book_id))
        identity = reference_identity(db, book_id, new_chunk.id)
        assert identity["content_hash"] == content_hash("identity body")
        assert identity["source_version_uid"] == active.version_uid
        assert identity["source_version_id"] == active.id
    finally:
        db.close()


def test_backfill_creates_legacy_version_without_faking_fingerprints():
    db = SessionLocal()
    try:
        book_id, chapter_id, chunk_id = _book(db, "Backfill legacy", content="raw legacy text")
        assert db.get(Book, book_id).active_source_version_id is None
        count = book_lifecycle.backfill_legacy_source_versions(db)
        assert count >= 1
        db.expire_all()
        book = db.get(Book, book_id)
        version = db.get(SourceVersion, book.active_source_version_id)
        assert version.status == "legacy"
        assert version.seq == 1
        chunk = db.get(Chunk, chunk_id)
        assert chunk.source_version_id == version.id
        assert chunk.content_hash == content_hash("raw legacy text")
        chapter = db.get(Chapter, chapter_id)
        assert chapter.source_version_id == version.id
        assert chapter.chapter_key
        # 旧引用没有指纹：仍明确标为 legacy_unverified，而不是伪造成已验证。
        assert verify_chunk_ref(db, book_id, chunk_id)["status"] == STATUS_LEGACY_UNVERIFIED
    finally:
        db.close()
