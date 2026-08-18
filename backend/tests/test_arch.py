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
