from uuid import uuid4


def test_knowledge_base_insights_follow_the_research_lifecycle():
    from fastapi.testclient import TestClient
    from sqlalchemy import delete

    from backend.app.core.database import SessionLocal
    from backend.app.main import app
    from backend.app.models import (
        Annotation, Book, Chapter, Chunk, EvidenceCard, KnowledgeNote, PaperProfile,
        PresentationDeck, StudyReport, WritingOutput,
    )

    marker = uuid4().hex
    db = SessionLocal()
    book_id = None
    output_id = report_id = deck_id = None
    try:
        book = Book(
            title=f"insights-{marker}", file_path="insights.docx", file_type="docx",
            status="ready", total_pages=2,
        )
        db.add(book)
        db.flush()
        book_id = book.id
        chapter = Chapter(book_id=book.id, title="研究方法", level=1, order_index=0, start_page=1, end_page=2)
        db.add(chapter)
        db.flush()
        chunk = Chunk(book_id=book.id, chapter_id=chapter.id, content="可追溯的研究证据" * 80,
                      page_start=1, page_end=2, chunk_index=0)
        db.add(chunk)
        db.flush()
        db.add_all([
            PaperProfile(book_id=book.id, authors="Researcher", published_year=2026),
            Annotation(book_id=book.id, page=1, rect_json="[]", text="原文证据", status="active"),
            KnowledgeNote(book_id=book.id, title="研究笔记", content="解释",
                          source_refs_json=f'["B{book.id}:CH{chapter.id}:P1:C{chunk.id}"]'),
            EvidenceCard(book_id=book.id, chapter_id=chapter.id, page=1, title="证据",
                         evidence_text="原文证据", source_ref_json=f'{{"refs":["B{book.id}:P1:C{chunk.id}"]}}',
                         verification_status="supported"),
        ])
        report = StudyReport(book_ids_json=f"[{book.id}]", focus="跨文献审查", content="报告")
        output = WritingOutput(kind="imitation", title="知识对象写作", input_type="text", output_text="文章")
        deck = PresentationDeck(book_id=book.id, title="研究汇报", status="done")
        db.add_all([report, output, deck])
        db.commit()
        report_id, output_id, deck_id = report.id, output.id, deck.id

        client = TestClient(app)
        response = client.get("/api/stats/knowledge-base?days=30")
        assert response.status_code == 200
        payload = response.json()
        assert payload["overview"]["ready_count"] >= 1
        assert payload["overview"]["knowledge_object_count"] >= 3
        assert payload["overview"]["traceable_rate"] > 0
        assert {item["key"] for item in payload["coverage"]} == {
            "ready", "indexed", "toc", "metadata", "evidence", "knowledge",
        }
        assert payload["knowledge"]["evidence_audit"]["supported"] >= 1
        assert payload["outputs"]["reports"] >= 1
        assert payload["outputs"]["finished_decks"] >= 1
        assert len(payload["activity"]) == 30
        assert "连续打卡" in payload["boundary"]
        assert client.get("/api/plan").status_code == 404
    finally:
        if book_id:
            db.execute(delete(EvidenceCard).where(EvidenceCard.book_id == book_id))
            db.execute(delete(KnowledgeNote).where(KnowledgeNote.book_id == book_id))
            db.execute(delete(Annotation).where(Annotation.book_id == book_id))
            db.execute(delete(Chunk).where(Chunk.book_id == book_id))
            db.execute(delete(Chapter).where(Chapter.book_id == book_id))
            db.execute(delete(PaperProfile).where(PaperProfile.book_id == book_id))
            db.execute(delete(PresentationDeck).where(PresentationDeck.id == deck_id))
            db.execute(delete(StudyReport).where(StudyReport.id == report_id))
            db.execute(delete(WritingOutput).where(WritingOutput.id == output_id))
            db.execute(delete(Book).where(Book.id == book_id))
            db.commit()
        db.close()
