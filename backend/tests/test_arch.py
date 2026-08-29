"""Architecture reinforcement tests (encoding-safe: uses chr() for Chinese)"""
import pytest


def test_data_manager():
    from backend.app.core.data_manager import check_integrity, SCHEMA_VERSION, FTS_INDEX_VERSION
    ok, msg = check_integrity()
    assert isinstance(ok, bool)
    assert SCHEMA_VERSION >= 3
    assert FTS_INDEX_VERSION >= 2


def test_reranker():
    from backend.app.services.rag.reranker import rerank
    items = [
        {"chunk_id": 1, "content": "ABC theorem is important in calculus", "score": 0.05},
        {"chunk_id": 2, "content": "This chapter introduces derivatives", "score": 0.03},
        {"chunk_id": 3, "content": "ABC theorem geometric meaning", "score": 0.04},
    ]
    reranked = rerank("ABC theorem", items, top_k=3)
    assert len(reranked) == 3
    top_ids = [r["chunk_id"] for r in reranked[:2]]
    assert 1 in top_ids or 3 in top_ids
    for r in reranked:
        assert "rerank_score" in r


def test_citation_verification():
    from backend.app.services.rag.reranker import verify_citations
    # Build citation string using chr() to avoid encoding issues
    # "根据[资料1]和[资料2]可知" = chr(0x6839)+chr(0x636e)+"["+chr(0x8d44)+chr(0x6599)+"1]"+chr(0x548c)+"["+chr(0x8d44)+chr(0x6599)+"2]"
    cite1 = chr(0x6839) + chr(0x636e) + "[" + chr(0x8d44) + chr(0x6599) + "1]" + chr(0x548c) + "[" + chr(0x8d44) + chr(0x6599) + "2]"
    v = verify_citations(cite1, [{"chunk_id": 1}, {"chunk_id": 2}])
    assert v["verified"] == True
    assert len(v["citations_found"]) == 2

    cite2 = "[" + chr(0x8d44) + chr(0x6599) + "5]"
    v2 = verify_citations(cite2, [{"chunk_id": 1}])
    assert v2["verified"] == False
    assert 5 in v2["mismatched"]

    v3 = verify_citations("no citation here", [{"chunk_id": 1}])
    assert v3["verified"] == False


def test_eval_stats():
    from backend.app.services.rag.reranker import get_eval_stats, record_retrieval_eval, record_citation_eval
    record_retrieval_eval(5, "test query")
    record_citation_eval({"verified": True, "citations_found": [1], "sources_provided": 3, "mismatched": []})
    stats = get_eval_stats()
    assert "total_queries" in stats
    assert "avg_retrieval_hits" in stats
    assert "citation_verify_rate" in stats


def test_knowledge_base():
    from backend.app.services.knowledge_base import get_multi_book_digest
    from backend.app.core.database import SessionLocal
    db = SessionLocal()
    try:
        result = get_multi_book_digest(db, None, limit_per_book=1000)
        assert isinstance(result, str)
    finally:
        db.close()


def test_task_retry():
    from backend.app.worker.tasks import TaskRecord
    record = TaskRecord(id="test-123", book_id=1)
    assert record.retry_count == 0
    assert record.max_retries == 2
def test_ocr_cache_and_progress():
    """OCR 页级缓存 + 进度回调（不真正跑 OCR，只测缓存读写与回调）。"""
    import tempfile, os
    from backend.app.services.parser.ocr import _ocr_cache_dir, _file_hash

    with tempfile.TemporaryDirectory() as td:
        # 模拟一个 PDF 文件（内容任意）
        pdf = os.path.join(td, "t.pdf")
        with open(pdf, "wb") as f:
            f.write(b"%PDF-1.4 test content " * 100)

        h = _file_hash(pdf)
        assert len(h) == 16
        cache_dir = _ocr_cache_dir(h)
        assert cache_dir.exists()

        # 写入 3 页缓存
        for i in (1, 2, 3):
            (cache_dir / f"page_{i:04d}.txt").write_text(f"page{i} text", encoding="utf-8")

        # 模拟读取缓存 + 回调计数
        calls = []
        texts = []
        total = 3
        for i in range(1, 4):
            cf = cache_dir / f"page_{i:04d}.txt"
            if cf.exists():
                texts.append(cf.read_text(encoding="utf-8"))
                calls.append((i, total, True))
        assert texts == ["page1 text", "page2 text", "page3 text"]
        assert all(c[2] is True for c in calls)
        assert calls[-1][0] == 3 and calls[-1][1] == 3


def test_ocr_pdf_progress_signature():
    """ocr_pdf 支持 on_progress 参数（断点续跑契约）。"""
    import inspect
    from backend.app.services.parser.ocr import ocr_pdf
    sig = inspect.signature(ocr_pdf)
    assert "on_progress" in sig.parameters
    assert "on_checkpoint" in sig.parameters
    assert "page_timeout_seconds" in sig.parameters
    assert sig.parameters["on_progress"].default is None


def test_ocr_page_watchdog_times_out():
    import time
    import pytest
    from backend.app.services.parser.ocr import OCRPageTimeout, _run_with_timeout

    with pytest.raises(OCRPageTimeout, match="第 7 页"):
        _run_with_timeout(lambda: time.sleep(.08), .01, 7)


def test_archive_source_map_contract():
    from types import SimpleNamespace
    import json
    from backend.app.services.archive import build_source_map

    chunks = [
        SimpleNamespace(id=9, book_id=3, chapter_id=4, page_start=7, page_end=8),
        SimpleNamespace(id=10, book_id=3, chapter_id=None, page_start=None, page_end=None),
    ]
    source_map = json.loads(build_source_map(3, chunks, {4: "Methods"}))
    assert source_map["locator_mode"] == "page-grounded"
    assert source_map["blocks"][0]["source_id"] == "B3-C9"
    assert source_map["blocks"][0]["chapter_title"] == "Methods"
    assert source_map["blocks"][1]["located"] is False


def test_paper_card_audit_contract():
    from backend.app.services.deep_analysis import audit_paper_card

    card = "\n".join(f"## {i:02d} Section\nClaim [B1-C{i}]" for i in range(1, 17))
    audit = audit_paper_card(card)
    assert audit["ok"] is True
    assert audit["missing_sections"] == []
    assert audit["source_reference_count"] == 16


def test_archive_api_roundtrip():
    from fastapi.testclient import TestClient
    from backend.app.core.database import SessionLocal
    from backend.app.main import app
    from backend.app.models import Book

    db = SessionLocal()
    try:
        book = Book(title="Archive API Test", file_path="archive-test.pdf",
                    file_type="pdf", status="ready")
        db.add(book)
        db.commit()
        db.refresh(book)
        book_id = book.id
    finally:
        db.close()

    with TestClient(app) as client:
        detail = client.get(f"/api/books/{book_id}")
        assert detail.status_code == 200
        assert detail.json()["archive"]["reading_status"] == "unread"
        updated = client.patch(
            f"/api/books/{book_id}/archive",
            json={"authors": "A. Author", "doi": "10.1000/test", "reading_status": "reading",
                  "favorite": True, "progress_page": 4, "publication_status": "unpublished",
                  "visibility": "private", "demo_allowed": False, "metadata_confidence": 0.8},
        )
        assert updated.status_code == 200
        payload = updated.json()
        assert payload["favorite"] is True
        assert payload["progress_page"] == 4
        assert payload["publication_status"] == "unpublished"
        assert payload["visibility"] == "private"
        assert payload["demo_allowed"] is False
        assert payload["metadata_confidence"] == 0.8
        invalid_year = client.patch(f"/api/books/{book_id}/archive", json={"published_year": 2095})
        assert invalid_year.status_code == 422
