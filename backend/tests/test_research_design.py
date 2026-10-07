"""Iteration C: rival predictions, verifiable reading tasks and typed coaching."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base, get_db
from backend.app.main import app
from backend.app.models import Book, Chunk, ResearchItem, ResearchScopeSnapshot, SensemakingArtifact, Shelf
from backend.app.services import research_archive as archive, research_design as design, research_discovery as discovery
from backend.app.services import sensemaking as sm
from backend.app.worker.tasks import TaskRecord


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def setup_scope(db):
    book = Book(title="平台劳动", file_path="platform.pdf", file_type="pdf", status="ready", file_hash="hash")
    db.add(book)
    db.flush()
    chunk = Chunk(book_id=book.id, chunk_index=0,
                  content="算法透明提高排班选择空间，但任务执行裁量可能因持续监控下降。",
                  page_start=8, page_end=8)
    db.add(chunk)
    shelf = Shelf(name="研究", books=[book])
    db.add(shelf)
    db.commit()
    snapshot = db.get(ResearchScopeSnapshot, archive.create_snapshot(db, "shelf", shelf.id)["id"])
    parent = db.get(ResearchItem, archive.create_item(db, snapshot, "judgment", "透明度提高自主性")["id"])
    design.set_rivals(db, parent, snapshot, [
        {"statement": "透明带来选择权", "premise": "排班信息可被利用", "prediction": "班次选择增加"},
        {"statement": "透明带来自我规训", "premise": "监控改变工人行为", "prediction": "任务裁量减少"},
    ], "关注同一批工人")
    return book, chunk, shelf, snapshot, parent


def source(book, chunk):
    return {"ref": discovery._ref(book.id, chunk), "book_id": book.id,
            "chunk_id": chunk.id, "page": chunk.page_start, "book_title": book.title,
            "text": chunk.content}


def proposal(source):
    return {"tasks": [{"target_type": "source_page", "ref": source["ref"],
        "quote": "算法透明提高排班选择空间", "question": "两种自主性是否朝相反方向变化？",
        "expected_a": "排班选择增加", "expected_b": "任务裁量减少",
        "why_change": "同一群体的两类指标可改变原判断", "evidence_gap": "缺少同一样本的双重测量",
        "ranking": {"judgment_change": 2, "evidence_gap": 2, "user_interest": 2}}]}


def test_rivals_require_distinct_predictions_and_append_revision(db):
    _, _, _, snapshot, parent = setup_scope(db)
    assert design.rivals(parent)[0]["prediction"] == "班次选择增加"
    revisions = archive.list_revisions(db, parent)
    assert len(revisions) == 1 and revisions[0]["before"]["detail"] == {}
    bad = [dict(entry) for entry in design.rivals(parent)]
    bad[1]["prediction"] = bad[0]["prediction"]
    with pytest.raises(HTTPException):
        design.set_rivals(db, parent, snapshot, bad)
    assert design.rivals(parent)[1]["prediction"] == "任务裁量减少"


def test_reading_task_rejects_fabricated_page_and_external_source(db):
    book, chunk, _, snapshot, parent = setup_scope(db)
    anchor = source(book, chunk)
    bad = proposal(anchor)
    bad["tasks"][0]["page"] = 99
    with pytest.raises(ValueError):
        design.normalize_tasks(bad, [anchor])
    bad = proposal(anchor)
    bad["tasks"][0]["quote"] = "原文不存在的发明句子"
    with pytest.raises(ValueError):
        design.normalize_tasks(bad, [anchor])
    external = {"tasks": [{**proposal(anchor)["tasks"][0],
        "target_type": "external_data", "external_data_type": "同一工人的两种自主性测量"}]}
    with pytest.raises(ValueError):
        design.normalize_tasks(external, [anchor])
    external["tasks"][0].pop("ref")
    external["tasks"][0].pop("quote")
    tasks = design.normalize_tasks(external, [anchor])
    ids = design.persist_tasks(db, snapshot, parent, tasks)
    assert len(ids) == 1
    item = db.get(ResearchItem, ids[0])
    assert archive.item_row(db, item, snapshot)["evidence"] == []
    with pytest.raises(HTTPException):
        design.record_outcome(db, snapshot, item, parent, "supports_a", "查看了别处的调查")
    result = design.record_outcome(db, snapshot, item, parent, "supports_a", "查看了别处的调查",
                                   external_reference="某机构 2024 年公开调查表")
    assert result["detail"]["external_reference"] == "某机构 2024 年公开调查表"
    fallback = design.external_fallback(parent)
    assert fallback["target_type"] == "external_data" and "同一研究对象" in fallback["external_data_type"]


def test_page_task_outcome_revises_parent_and_prioritizes_unresolved(db):
    book, chunk, _, snapshot, parent = setup_scope(db)
    anchor = source(book, chunk)
    task = design.normalize_tasks(proposal(anchor), [anchor])[0]
    task_id = design.persist_tasks(db, snapshot, parent, [task])[0]
    assert design.persist_tasks(db, snapshot, parent, [task]) == []
    row = db.get(ResearchItem, task_id)
    assert row.review_status == "unreviewed"
    result = design.record_outcome(db, snapshot, row, parent, "supports_b", "第八页显示任务裁量下降", "透明度作用于不同的自主性维度")
    assert result["detail"]["outcome"] == "supports_b"
    assert db.get(ResearchItem, parent.id).statement == "透明度作用于不同的自主性维度"
    revisions = archive.list_revisions(db, parent)
    assert revisions[0]["trigger_evidence_id"] is not None
    assert revisions[0]["before"]["statement"] == "透明度提高自主性"
    with pytest.raises(HTTPException):
        design.record_outcome(db, snapshot, row, parent, "supports_a", "重复")
    chunk.content = "重新 OCR 后已无原句。"
    db.commit()
    listed = design.reading_tasks(db, parent, snapshot)
    assert listed[0]["evidence"][0]["source_status"] != "current"


def test_material_specific_coach_question_and_feedback(db):
    book, chunk, _, snapshot, parent = setup_scope(db)
    seen = set()
    for kind in ("quantitative", "qualitative", "theoretical", "review"):
        artifact = SensemakingArtifact(kind="reading", book_ids_json=json.dumps([book.id]), focus="",
            payload_json=json.dumps({"material_type": kind, "coverage": {"complete": True},
                "nodes": [{"id": "n1", "statement": "算法透明改变自主性", "reasoning": "需要辨认测量与机制",
                           "evidence": [{"book_id": book.id, "chunk_id": chunk.id,
                                         "quote": "算法透明提高排班选择空间"}]}]}, ensure_ascii=False),
            source_versions_json=json.dumps(sm.document_digest(db, book.id)),
            prompt_version=sm.PROMPT_VERSION, model_name="test")
        db.add(artifact)
        db.commit()
        context = design.coach_source(db, snapshot, book.id)
        assert context["material_type"] == kind
        seen.add(context["guidance"])
        normalized = design.normalize_question({"ref": context["ref"],
            "quote": "算法透明提高排班选择空间", "question": f"请解释这里的{kind}薄弱点",
            "weak_point": "证据如何支持主张"}, context)
        turn_id = design.persist_question(db, snapshot, parent, context, normalized)
        turn = db.get(ResearchItem, turn_id)
        design.submit_answer(db, turn, "我认为需要进一步区分两类自主性的测量。")
        feedback = design.normalize_feedback({"ref": context["ref"],
            "source_quote": "算法透明提高排班选择空间",
            "source_says": "原文报告排班选择空间提高",
            "user_inference": "用户认为需要区分两类自主性",
            "ai_suggestion": "复核任务裁量的相关段落",
            "remaining_doubt": "是否使用同一样本"}, context)
        design.persist_feedback(db, snapshot, turn, feedback)
        assert json.loads(turn.detail_json)["feedback"]["source_says"]
        assert archive.list_revisions(db, turn)[0]["actor"] == "ai"
    assert len(seen) == 4


def test_api_scope_and_workflow_guards(db):
    book, chunk, shelf, snapshot, parent = setup_scope(db)
    anchor = source(book, chunk)
    task_id = design.persist_tasks(db, snapshot, parent, design.normalize_tasks(proposal(anchor), [anchor]))[0]
    other = Shelf(name="其他")
    db.add(other)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            payload = {"scope_type": "shelf", "scope_id": shelf.id, "item_id": parent.id}
            assert client.post("/api/assistant/research/reading-tasks/preview", json=payload).status_code == 200
            assert client.get(f"/api/assistant/research/items/{parent.id}/reading-tasks",
                params={"scope_type": "shelf", "scope_id": other.id}).status_code == 404
            assert client.patch(f"/api/assistant/research/items/{task_id}", json={
                "scope_type": "shelf", "scope_id": shelf.id, "review_status": "confirmed"}).status_code == 422
            assert client.post("/api/assistant/research/reading-tasks/generate", json=payload).status_code == 400
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_background_reading_plan_keeps_proposals_unconfirmed(db, monkeypatch):
    from backend.app.api import research_design as api
    book, chunk, _, _, parent = setup_scope(db)
    anchor = source(book, chunk)
    monkeypatch.setattr(api, "SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(api, "_research_config", lambda _db: {"configured": True})
    monkeypatch.setattr(api.LLMRouter, "get", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(api, "update_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api.retriever, "retrieve", lambda *_args, **_kwargs: [
        {"book_id": book.id, "chunk_id": chunk.id, "snippet": chunk.content}])

    async def structured(_provider, _messages, normalizer, _record, _stage):
        return normalizer(proposal(anchor))

    monkeypatch.setattr(api, "_structured", structured)
    db.rollback()
    first = await api._run_reading_plan(TaskRecord(id="c1"), parent.id)
    second = await api._run_reading_plan(TaskRecord(id="c2"), parent.id)
    assert len(first["task_ids"]) == 1 and second["task_ids"] == []
    assert db.get(ResearchItem, first["task_ids"][0]).review_status == "unreviewed"
    monkeypatch.setattr(api.retriever, "retrieve", lambda *_args, **_kwargs: [])
    db.rollback()
    empty = await api._run_reading_plan(TaskRecord(id="c3"), parent.id)
    assert empty["status"] == "no_page_in_scope"
    assert db.get(ResearchItem, empty["task_ids"][0]).detail_json.find("external_data") >= 0


@pytest.mark.asyncio
async def test_background_coach_round_keeps_user_answer_and_source_separate(db, monkeypatch):
    from backend.app.api import research_design as api
    book, chunk, _, snapshot, parent = setup_scope(db)
    artifact = SensemakingArtifact(kind="reading", book_ids_json=json.dumps([book.id]), focus="",
        payload_json=json.dumps({"material_type": "qualitative", "coverage": {"complete": True},
            "nodes": [{"id": "n1", "statement": "访谈显示裁量下降", "reasoning": "需要寻找负例",
                       "evidence": [{"book_id": book.id, "chunk_id": chunk.id,
                                     "quote": "任务执行裁量可能因持续监控下降"}]}]}, ensure_ascii=False),
        source_versions_json=json.dumps(sm.document_digest(db, book.id)),
        prompt_version=sm.PROMPT_VERSION, model_name="test")
    db.add(artifact)
    db.commit()
    monkeypatch.setattr(api, "SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(api, "_research_config", lambda _db: {"configured": True})
    monkeypatch.setattr(api.LLMRouter, "get", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(api, "update_progress", lambda *_args, **_kwargs: None)

    async def structured(_provider, messages, normalizer, _record, stage):
        ref = discovery._ref(book.id, chunk)
        if stage == "coach_question":
            assert '"material_type": "qualitative"' in messages[1]["content"]
            return normalizer({"ref": ref, "quote": "任务执行裁量可能因持续监控下降",
                "question": "作者是否讨论了与这一解释不符的负例？", "weak_point": "负例尚未解释"})
        if stage == "coach_feedback":
            return normalizer({"ref": ref, "source_quote": "任务执行裁量可能因持续监控下降",
                "source_says": "原文仅说裁量可能下降", "user_inference": "用户推断不存在负例",
                "ai_suggestion": "查找访谈中的相反案例", "remaining_doubt": "负例数量不明"})
        raise AssertionError(stage)

    monkeypatch.setattr(api, "_structured", structured)
    db.rollback()
    result = await api._run_coach_question(TaskRecord(id="cq"), parent.id, book.id)
    turn = db.get(ResearchItem, result["turn_id"])
    assert turn.review_status == "unreviewed"
    design.submit_answer(db, turn, "我认为作者未说明与这一解释不符的案例。")
    feedback = await api._run_coach_feedback(TaskRecord(id="cf"), turn.id)
    assert feedback["turn_id"] == turn.id
    db.expire_all()
    detail = json.loads(db.get(ResearchItem, turn.id).detail_json)
    assert detail["feedback"]["source_says"] == "任务执行裁量可能因持续监控下降"
    assert detail["feedback"]["ai_source_interpretation"] == "原文仅说裁量可能下降"
    assert db.get(ResearchItem, parent.id).statement == "透明度提高自主性"
