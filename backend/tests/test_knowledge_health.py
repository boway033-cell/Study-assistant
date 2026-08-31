from uuid import uuid4


def test_knowledge_health_reports_actionable_local_issues():
    from fastapi.testclient import TestClient
    from sqlalchemy import delete

    from backend.app.core.database import SessionLocal
    from backend.app.main import app
    from backend.app.models import Book, Chapter, Chunk, KnowledgeNote, PaperProfile

    marker = uuid4().hex
    db = SessionLocal()
    created_ids: list[int] = []
    try:
        pending = Book(
            title=f"health-pending-{marker}", file_path="pending.pdf", file_type="pdf",
            status="pending", total_pages=8,
        )
        weak = Book(
            title=f"health-weak-{marker}", file_path="weak.pdf", file_type="pdf",
            status="ready", total_pages=10, file_hash=f"duplicate-{marker}",
        )
        duplicate = Book(
            title=f"health-duplicate-{marker}", file_path="duplicate.pdf", file_type="pdf",
            status="ready", total_pages=10, file_hash=f"duplicate-{marker}",
        )
        sound = Book(
            title=f"health-sound-{marker}", file_path="sound.docx", file_type="docx",
            status="ready", total_pages=2,
        )
        db.add_all([pending, weak, duplicate, sound])
        db.flush()
        created_ids = [pending.id, weak.id, duplicate.id, sound.id]

        sound_chapter = Chapter(
            book_id=sound.id, title="完整目录", level=1, order_index=0,
            start_page=1, end_page=2,
        )
        db.add(sound_chapter)
        db.flush()
        db.add_all([
            Chunk(book_id=weak.id, content="短文本", page_start=1, page_end=1, chunk_index=0),
            Chunk(book_id=duplicate.id, content="短文本", page_start=1, page_end=1, chunk_index=0),
            Chunk(
                book_id=sound.id, chapter_id=sound_chapter.id,
                content="有来源的完整文本" * 80, page_start=1, page_end=2, chunk_index=0,
            ),
            PaperProfile(book_id=sound.id, authors="Test Author", published_year=2025),
            KnowledgeNote(
                book_id=sound.id, title="失效引用", content="用于健康审计",
                source_refs_json=f'["B{sound.id}:CH999999:P1"]',
            ),
        ])
        db.commit()

        response = TestClient(app).get("/api/books/health")
        assert response.status_code == 200
        payload = response.json()
        relevant = [item for item in payload["items"] if item["book_id"] in created_ids]
        kinds_by_book = {
            book_id: {item["type"] for item in relevant if item["book_id"] == book_id}
            for book_id in created_ids
        }

        assert "unparsed" in kinds_by_book[pending.id]
        assert "low_quality_ocr" in kinds_by_book[weak.id]
        assert "missing_toc" in kinds_by_book[weak.id]
        assert "missing_metadata" in kinds_by_book[weak.id]
        assert "duplicate" in kinds_by_book[weak.id]
        assert "duplicate" in kinds_by_book[duplicate.id]
        assert kinds_by_book[sound.id] == {"broken_anchor"}
        assert payload["categories"]["broken_anchor"]["count"] >= 1
        broken = next(item for item in relevant if item["book_id"] == sound.id and item["type"] == "broken_anchor")
        assert broken["evidence"]["objects"][0]["type"] == "note"
        assert broken["evidence"]["objects"][0]["action_path"].startswith("/notes?")
        assert 0 <= payload["score"] <= 100
        assert payload["score"] < 100
    finally:
        if created_ids:
            db.execute(delete(KnowledgeNote).where(KnowledgeNote.book_id.in_(created_ids)))
            db.execute(delete(Chunk).where(Chunk.book_id.in_(created_ids)))
            db.execute(delete(Chapter).where(Chapter.book_id.in_(created_ids)))
            db.execute(delete(PaperProfile).where(PaperProfile.book_id.in_(created_ids)))
            db.execute(delete(Book).where(Book.id.in_(created_ids)))
            db.commit()
        db.close()


def test_health_batch_repair_returns_safe_manual_routes():
    from backend.app.api.books import HealthRepairReq, repair_health_issues
    from backend.app.core.database import SessionLocal
    from backend.app.models import Book

    db = SessionLocal()
    book = Book(title="manual-health", file_path="manual.pdf", file_type="pdf", status="ready")
    db.add(book); db.commit(); db.refresh(book)
    try:
        result = repair_health_issues(HealthRepairReq(issue_ids=[
            f"missing_toc:{book.id}", f"missing_metadata:{book.id}", "bad-id",
        ]), db)
        assert result["submitted"] == 0
        assert result["results"][0]["action_path"] == f"/reader/{book.id}?toc=review"
        assert result["results"][1]["action_path"] == f"/library?bookId={book.id}"
        assert result["results"][2]["status"] == "invalid"
    finally:
        db.delete(book); db.commit(); db.close()


def test_broken_knowledge_anchor_does_not_repair_a_valid_annotation():
    from sqlalchemy import delete

    from backend.app.api.books import HealthRepairReq, repair_health_issues
    from backend.app.core.database import SessionLocal
    from backend.app.models import Annotation, Book, KnowledgeNote

    db = SessionLocal()
    book = Book(
        title="anchor-safety", file_path="anchor-safety.pdf", file_type="pdf",
        status="ready", total_pages=5, file_hash="anchor-safety-hash",
    )
    db.add(book); db.flush()
    annotation = Annotation(
        book_id=book.id, page=1, rect_json="[]", text="有效批注", status="active",
        schema_version=2,
        anchor_json='{"document_fingerprint":"anchor-safety-hash","segments":[]}',
    )
    note = KnowledgeNote(
        book_id=book.id, title="失效知识锚点", content="需要人工修正",
        source_refs_json=f'["B{book.id}:CH999999:P1"]',
    )
    db.add_all([annotation, note]); db.commit()
    annotation_id = annotation.id
    book_id = book.id
    try:
        result = repair_health_issues(
            HealthRepairReq(issue_ids=[f"broken_anchor:{book_id}"]), db,
        )
        db.refresh(annotation)
        assert result["results"][0]["status"] == "manual_required"
        assert result["results"][0]["repaired"] == 0
        assert annotation.status == "active"
        assert annotation.anchor_json == '{"document_fingerprint":"anchor-safety-hash","segments":[]}'
    finally:
        db.execute(delete(KnowledgeNote).where(KnowledgeNote.book_id == book_id))
        db.execute(delete(Annotation).where(Annotation.id == annotation_id))
        db.execute(delete(Book).where(Book.id == book_id))
        db.commit(); db.close()
