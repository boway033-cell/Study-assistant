from uuid import uuid4

import pytest
from fastapi import HTTPException


def test_scoped_note_inbox_and_tree_deletion_preserve_highlight():
    from backend.app.api.knowledge import create_node, delete_node, list_knowledge_notes
    from backend.app.core.database import SessionLocal
    from backend.app.models import Annotation, Book, KnowledgeNote
    from backend.app.schemas import KnowledgeNodeCreateReq

    db = SessionLocal()
    book = Book(title=f"scope-{uuid4().hex}", file_path="scope.pdf", file_type="pdf", status="ready")
    db.add(book); db.commit(); db.refresh(book)
    try:
        root = create_node(KnowledgeNodeCreateReq(title="研究主题", book_id=book.id), db)
        note = create_node(KnowledgeNodeCreateReq(parent_id=root.id, title="阅读判断", node_type="note", note="需要继续核对"), db)
        assert note.book_id == book.id

        annotation = Annotation(book_id=book.id, page=3, rect_json="[]", text="原文证据", note="需要继续核对", knowledge_node_id=note.id)
        db.add(annotation); db.commit(); db.refresh(annotation)
        result = list_knowledge_notes([book.id], None, db)
        assert result["total"] == 2
        assert {item["source_type"] for item in result["items"]} == {"annotation", "note"}
        assert next(item for item in result["items"] if item["source_type"] == "annotation")["page"] == 3

        annotation_id = annotation.id
        delete_node(root.id, db)
        db.expire_all()
        assert db.get(Annotation, annotation_id).knowledge_node_id is None
    finally:
        db.query(Annotation).filter(Annotation.book_id == book.id).delete(synchronize_session=False)
        db.query(KnowledgeNote).filter(KnowledgeNote.book_id == book.id).delete(synchronize_session=False)
        db.delete(book); db.commit(); db.close()


def test_study_overview_rejects_implicit_all_scope():
    from backend.app.api.study import StudyOverviewReq, study_overview
    from backend.app.core.database import SessionLocal

    db = SessionLocal()
    try:
        with pytest.raises(HTTPException, match="至少选择一本"):
            study_overview(StudyOverviewReq(book_ids=[], focus="比较观点"), db)
        with pytest.raises(HTTPException, match="研究问题"):
            study_overview(StudyOverviewReq(book_ids=[1], focus="  "), db)
    finally:
        db.close()


def test_research_plan_and_claim_audit_are_bounded():
    from backend.app.api.study import _normalize_claims, _normalize_plan

    plan = _normalize_plan({
        "material_type": "公共管理教材与案例论文",
        "subquestions": [f"问题{i}" for i in range(10)],
        "analysis_axes": ["概念", "机制", "证据"],
        "evidence_needs": ["反例与适用边界"],
        "report_outline": ["问题界定", "证据综合", "保留意见"],
    }, "adaptive")
    assert plan["mode"] == "adaptive"
    assert len(plan["subquestions"]) == 6

    claims = _normalize_claims([
        {"claim": "有精确来源", "claim_type": "causal", "source_refs": ["B2:CH4:P7:C9"],
         "status": "supported", "confidence": "high", "reason": "原文直接支持"},
        {"claim": "伪造来源", "source_refs": ["B2:CH999"], "status": "supported"},
    ], {"B2:CH4:P7:C9"})
    assert claims[0]["status"] == "supported"
    assert claims[0]["claim_type"] == "causal"
    assert claims[1]["source_refs"] == []
    assert claims[1]["status"] == "needs_review"


def test_multi_book_vector_retrieval_stays_in_scope(monkeypatch):
    from backend.app.services.rag import retriever

    calls = []
    monkeypatch.setattr(retriever.vector, "is_enabled", lambda: True)
    monkeypatch.setattr(retriever.vector, "vector_search", lambda question, book_id, top_k: calls.append(book_id) or [])
    monkeypatch.setattr(retriever.fts, "search", lambda *args, **kwargs: {"items": []})
    monkeypatch.setattr(retriever, "fallback_search", lambda *args, **kwargs: [])
    monkeypatch.setattr(retriever, "get_book_outline", lambda *args, **kwargs: {"chunk_id": 0, "is_outline": True})

    retriever.retrieve("治理机制", book_ids=[11, 22], top_k=3)
    assert calls == [11, 22]


def test_annotation_note_and_evidence_are_independent_entities():
    from backend.app.api.annotations import create_annotation
    from backend.app.api.knowledge import (
        create_evidence_card, list_knowledge_records, promote_annotation,
    )
    from backend.app.core.database import SessionLocal
    from backend.app.models import Annotation, Book, EvidenceCard, KnowledgeNode, KnowledgeNote
    from backend.app.schemas import AnnotationCreateReq, EvidenceCardCreateReq

    db = SessionLocal()
    book = Book(title=f"entities-{uuid4().hex}", file_path="entities.pdf", file_type="pdf", status="ready")
    db.add(book); db.commit(); db.refresh(book)
    try:
        annotation = create_annotation(book.id, AnnotationCreateReq(
            page=2, rect_json='[{"x":0.1,"y":0.2,"w":0.3,"h":0.04}]',
            text="可独立保存的原文", mark_type="underline", note="",
        ), db)
        assert annotation.mark_type == "underline"
        assert db.query(KnowledgeNode).filter(KnowledgeNode.book_id == book.id).first() is None

        promoted = promote_annotation(annotation.id, db)
        note = db.get(KnowledgeNote, promoted["id"])
        assert note is not None and note.source_annotation_id == annotation.id
        assert db.get(Annotation, annotation.id).knowledge_node_id is None

        card = create_evidence_card(EvidenceCardCreateReq(
            book_id=book.id, page=2, title="证据卡", evidence_text="可独立保存的原文",
            claim_text="该原文支持一个待核验主张", verification_status="needs_review",
        ), db)
        records = list_knowledge_records([book.id], None, None, None, None, None, None, None, db)
        assert {item["entity_type"] for item in records["items"]} == {
            "annotation", "knowledge_note", "evidence_card",
        }
        assert card["verification_status"] == "needs_review"
    finally:
        db.query(EvidenceCard).filter(EvidenceCard.book_id == book.id).delete(synchronize_session=False)
        db.query(KnowledgeNote).filter(KnowledgeNote.book_id == book.id).delete(synchronize_session=False)
        db.query(Annotation).filter(Annotation.book_id == book.id).delete(synchronize_session=False)
        db.query(KnowledgeNode).filter(KnowledgeNode.book_id == book.id).delete(synchronize_session=False)
        db.delete(book); db.commit(); db.close()
