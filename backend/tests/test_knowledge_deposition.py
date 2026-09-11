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


def test_note_inbox_search_semantics_after_sql_pushdown():
    """`/knowledge/notes` 的过滤下推到 SQL 后，检索行为必须与旧的全量 Python 子串过滤一致。

    锁住三个易被误判的边界：跨字段带空格的搜索词、LIKE 通配符须按字面量处理、
    非 note 类型不得进入收件箱。
    """
    from backend.app.api.knowledge import list_knowledge_notes
    from backend.app.core.database import SessionLocal
    from backend.app.models import Annotation, Book, KnowledgeNode

    db = SessionLocal()
    book = Book(title=f"pushdown-{uuid4().hex}", file_path="pushdown.pdf", file_type="pdf", status="ready")
    db.add(book); db.commit(); db.refresh(book)
    try:
        db.add_all([
            KnowledgeNode(node_type="note", title="研究方法", note="需要继续核对样本量", book_id=book.id),
            KnowledgeNode(node_type="note", title="50% 覆盖率", note=None, book_id=book.id),
            KnowledgeNode(node_type="section", title="研究方法", note="越界", book_id=book.id),
            Annotation(book_id=book.id, page=3, rect_json="[]", text="原文证据", note="需要继续核对"),
        ])
        db.commit()

        def titles(q):
            return sorted(item["title"] for item in list_knowledge_notes([book.id], q, db)["items"])

        # 关键词同时命中 note 与标注两种来源
        assert list_knowledge_notes([book.id], "需要继续核对", db)["total"] == 2
        # 跨字段带空格：拼接串语义（旧实现同样命中）
        assert titles("研究方法 需要继续核对") == ["研究方法"]
        # % 与 _ 必须按字面量处理，不得被当作 LIKE 通配符
        assert titles("50%") == ["50% 覆盖率"]
        assert titles("50%x") == []
        # 非 note 类型不得进入收件箱
        assert titles("研究方法") == ["研究方法"]
        # 纯空白等价于无过滤
        assert list_knowledge_notes([book.id], "  ", db)["total"] == list_knowledge_notes([book.id], None, db)["total"]
    finally:
        db.query(Annotation).filter(Annotation.book_id == book.id).delete(synchronize_session=False)
        db.query(KnowledgeNode).filter(KnowledgeNode.book_id == book.id).delete(synchronize_session=False)
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
    from backend.app.api.study import _evidence_summary, _normalize_claims, _normalize_hypotheses, _normalize_plan

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
         "status": "supported", "confidence": "high", "reason": "原文直接支持",
         "synthesis_relation": "conflict", "evidence_quality": "moderate",
         "bias_flags": ["选择偏倚"], "alternative_explanations": ["反向因果"]},
        {"claim": "伪造来源", "source_refs": ["B2:CH999"], "status": "supported"},
    ], {"B2:CH4:P7:C9"})
    assert claims[0]["status"] == "supported"
    assert claims[0]["claim_type"] == "causal"
    assert claims[0]["synthesis_relation"] == "conflict"
    assert claims[0]["evidence_quality"] == "moderate"
    assert _evidence_summary(claims)["relations"]["conflict"] == 1
    assert claims[1]["source_refs"] == []
    assert claims[1]["status"] == "needs_review"
    hypotheses = _normalize_hypotheses([{"statement": "候选机制", "source_refs": ["B2:CH4:P7:C9"],
                                         "rival_explanations": ["共同原因"], "falsifier": "干预后无变化"}],
                                       {"B2:CH4:P7:C9"})
    assert hypotheses[0]["status"] == "candidate"
    assert hypotheses[0]["human_review_required"] is True


def test_critical_review_deposit_is_idempotent():
    import json
    from backend.app.api.study import DepositStudyReportReq, deposit_report
    from backend.app.core.database import SessionLocal
    from backend.app.models import Book, EvidenceCard, KnowledgeNote, StudyReport

    db = SessionLocal()
    book = Book(title=f"deposit-{uuid4().hex}", file_path="deposit.pdf", file_type="pdf", status="ready")
    db.add(book); db.commit(); db.refresh(book)
    claim = {"claim": "两组证据结论冲突", "source_refs": [f"B{book.id}:P2"], "status": "partial",
             "synthesis_relation": "conflict", "evidence_quality": "moderate", "reason": "方向不一致",
             "counterpoint": "样本不同", "bias_flags": ["选择偏倚"], "alternative_explanations": ["测量口径不同"]}
    report = StudyReport(book_ids_json=json.dumps([book.id]), focus="比较效应",
                         selection_json=json.dumps({"research_mode": "critical", "evidence_summary": {"relations": {"conflict": 1}}}),
                         claims_json=json.dumps([claim], ensure_ascii=False), content="# 批判性审查\n\n结论存在冲突。")
    db.add(report); db.commit(); db.refresh(report)
    try:
        first = deposit_report(report.id, DepositStudyReportReq(), db)
        second = deposit_report(report.id, DepositStudyReportReq(), db)
        assert first["note_id"] == second["note_id"]
        assert first["evidence_card_ids"] == second["evidence_card_ids"]
        assert first["claim_count"] == 1
        note = db.get(KnowledgeNote, first["note_id"])
        card = db.get(EvidenceCard, first["evidence_card_ids"][0])
        assert "批判性审查" in note.title
        assert "冲突证据" in card.evidence_text
        assert card.verification_status == "partial"
    finally:
        db.query(EvidenceCard).filter(EvidenceCard.source_report_id == report.id).delete(synchronize_session=False)
        db.query(KnowledgeNote).filter(KnowledgeNote.source_report_id == report.id).delete(synchronize_session=False)
        db.delete(report); db.delete(book); db.commit(); db.close()


def test_human_claim_review_updates_ledger_and_rejects_new_anchors():
    import json
    from backend.app.api.study import StudyClaimsUpdateReq, update_report_claims
    from backend.app.core.database import SessionLocal
    from backend.app.models import Book, StudyReport

    db = SessionLocal()
    book = Book(title=f"ledger-{uuid4().hex}", file_path="ledger.pdf", file_type="pdf", status="ready")
    db.add(book); db.flush()
    anchor = f"B{book.id}:P2"
    report = StudyReport(book_ids_json=json.dumps([book.id]), focus="复核台账",
                         selection_json="{}", content="报告",
                         claims_json=json.dumps([{"claim": "原主张", "source_refs": [anchor],
                                                  "status": "needs_review"}], ensure_ascii=False))
    db.add(report); db.commit(); db.refresh(report)
    try:
        payload = update_report_claims(report.id, StudyClaimsUpdateReq(claims=[{
            "claim": "人工修订主张", "source_refs": [anchor, "B999999:P1"], "status": "supported",
            "synthesis_relation": "conflict", "evidence_quality": "moderate",
            "reason": "人工核验原文", "human_review_required": False,
        }]), db)
        assert payload["claims"][0]["claim"] == "人工修订主张"
        assert payload["claims"][0]["source_refs"] == [anchor]
        assert payload["selection"]["claims_reviewed_by"] == "user"
        assert payload["selection"]["evidence_summary"]["relations"]["conflict"] == 1
    finally:
        db.delete(report); db.delete(book); db.commit(); db.close()


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
            source_report_id=17, source_book_ids=[book.id], source_refs=[f"B{book.id}:P2"],
        ), db)
        records = list_knowledge_records([book.id], None, None, None, None, None, None, None, db)
        assert {item["entity_type"] for item in records["items"]} == {
            "annotation", "knowledge_note", "evidence_card",
        }
        assert card["verification_status"] == "needs_review"
        assert card["source_report_id"] == 17
        assert card["source_refs"] == [f"B{book.id}:P2"]
    finally:
        db.query(EvidenceCard).filter(EvidenceCard.book_id == book.id).delete(synchronize_session=False)
        db.query(KnowledgeNote).filter(KnowledgeNote.book_id == book.id).delete(synchronize_session=False)
        db.query(Annotation).filter(Annotation.book_id == book.id).delete(synchronize_session=False)
        db.query(KnowledgeNode).filter(KnowledgeNode.book_id == book.id).delete(synchronize_session=False)
        db.delete(book); db.commit(); db.close()


def test_ai_report_note_detail_preserves_markdown_and_source_link():
    from backend.app.api.knowledge import create_knowledge_note, get_knowledge_note
    from backend.app.core.database import SessionLocal
    from backend.app.models import Book, KnowledgeNote
    from backend.app.schemas import KnowledgeNoteCreateReq

    db = SessionLocal()
    book = Book(title=f"report-note-{uuid4().hex}", file_path="report-note.pdf", file_type="pdf", status="ready")
    db.add(book); db.commit(); db.refresh(book)
    markdown = "# 综合研究报告\n\n## 核心判断\n\n这是可完整阅读的 AI 报告。\n\n- 证据一\n- 证据二"
    try:
        created = create_knowledge_note(KnowledgeNoteCreateReq(
            book_id=book.id, title="综合研究报告", content=markdown,
            tags=["研究报告"], origin="ai", source_report_id=23,
            source_book_ids=[book.id], source_refs=[f"B{book.id}:P1"],
            source_scope={"chapter_ids": [101, 102]},
        ), db)
        detail = get_knowledge_note(created["id"], db)
        assert detail["content"] == markdown
        assert detail["origin"] == "ai"
        assert detail["tags"] == ["研究报告"]
        assert detail["source_link"] == f"/reader/{book.id}?page=1"
        assert detail["source_report_id"] == 23
        assert detail["source_scope"]["book_ids"] == [book.id]
        assert detail["source_refs"] == [f"B{book.id}:P1"]
    finally:
        db.query(KnowledgeNote).filter(KnowledgeNote.book_id == book.id).delete(synchronize_session=False)
        db.delete(book); db.commit(); db.close()
