"""Iteration B fixtures: different meanings, manual merge, stale sources and counterevidence."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base, get_db
from backend.app.main import app
from backend.app.models import Book, Chunk, ResearchItem, ResearchScopeSnapshot, Shelf
from backend.app.services import research_archive as archive
from backend.app.services import research_discovery as discovery
from backend.app.worker.tasks import TaskRecord


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def _book(db, title, content, page=1):
    book = Book(title=title, file_path=f"{title}.pdf", file_type="pdf", status="ready", file_hash=f"hash-{title}")
    db.add(book)
    db.flush()
    chunk = Chunk(book_id=book.id, chunk_index=0, content=content, page_start=page, page_end=page)
    db.add(chunk)
    db.flush()
    return book, chunk


def _snapshot(db, books):
    shelf = Shelf(name="平台劳动", books=list(books))
    db.add(shelf)
    db.commit()
    return shelf, db.get(ResearchScopeSnapshot, archive.create_snapshot(db, "shelf", shelf.id)["id"])


def _definition(source, original_term, meaning):
    return {"ref": source["ref"], "book_id": source["book_id"], "original_term": original_term,
            "meaning": meaning, "measurement": "访谈编码", "unit": "工人", "period_place": "某平台 2024 年",
            "population": "平台工人", "method": "质性访谈", "conclusion_scope": "该平台",
            "quote": source["content"][:24]}


def test_same_word_can_keep_distinct_senses_and_original_pages(db):
    a, a_chunk = _book(db, "排班研究", "自主性在本文指排班选择，算法透明提高了班次选择空间。", 7)
    b, _ = _book(db, "任务研究", "自主性在本文指任务执行裁量，算法透明可能增强自我规训。", 12)
    _, snapshot = _snapshot(db, [a, b])
    found = discovery.concept_sources(db, snapshot, "自主性")
    assert found["matched_books"] == 2
    raw = {"definitions": [_definition(source, "自主性", "排班选择" if source["book_id"] == a.id else "任务执行裁量")
                           for source in found["sources"]]}
    normalized = discovery.normalize_concepts(raw, found["sources"])
    ids = discovery.persist_concepts(db, snapshot, "自主性", normalized)
    cards = discovery.concept_cards(db, snapshot)
    assert len(ids) == 2 and len(cards) == 1
    assert {sense["detail"]["meaning"] for sense in cards[0]["senses"]} == {"排班选择", "任务执行裁量"}
    assert {sense["evidence"][0]["page_start"] for sense in cards[0]["senses"]} == {7, 12}
    assert all(sense["review_status"] == "unreviewed" for sense in cards[0]["senses"])
    inputs = discovery.alignment_inputs(db, snapshot, *ids)
    result = discovery.normalize_alignment({"status": "different", "reason": "前者比较排班选择，后者比较任务执行裁量，分析对象不同。", "unresolved": "需要同一样本的双重测量"})
    alignment_id = discovery.persist_alignment(db, snapshot, inputs, result)
    assert db.get(ResearchItem, alignment_id).review_status == "unreviewed"
    assert json.loads(db.get(ResearchItem, alignment_id).detail_json)["status"] == "different"
    alignment = db.get(ResearchItem, alignment_id)
    support = archive.add_evidence(db, alignment, snapshot, a.id, a_chunk.id,
                                   "算法透明提高了班次选择空间", "supports")
    repeated = archive.add_evidence(db, alignment, snapshot, a.id, a_chunk.id,
                                    "算法透明提高了班次选择空间", "supports")
    assert support["id"] == repeated["id"] and support["review_status"] == "confirmed"
    assert discovery.concept_cards(db, snapshot)[0]["alignments"][0]["evidence"][-1]["relation"] == "supports"


def test_concept_recall_prefers_distinct_pages_within_document(db):
    book, _ = _book(db, "多页讨论", "自主性首先指排班选择。", 1)
    for index, page in enumerate((1, 2, 3, 4, 5), start=1):
        db.add(Chunk(book_id=book.id, chunk_index=index,
                     content=f"自主性在第 {page} 页被进一步讨论。", page_start=page, page_end=page))
    _, snapshot = _snapshot(db, [book])
    found = discovery.concept_sources(db, snapshot, "自主性")
    pages = [source["page"] for source in found["sources"]]
    assert len(pages) == 4 and len(set(pages)) == 4


def test_different_words_are_merged_only_by_user_action(db):
    a, _ = _book(db, "自主性", "自主性是工人决定班次的能力。")
    b, _ = _book(db, "能动性", "能动性是工人调整班次的能力。")
    _, snapshot = _snapshot(db, [a, b])
    senses = []
    for term in ("自主性", "能动性"):
        found = discovery.concept_sources(db, snapshot, term)
        source = next(s for s in found["sources"] if s["book_id"] == (a.id if term == "自主性" else b.id))
        ids = discovery.persist_concepts(db, snapshot, term,
                                         discovery.normalize_concepts({"definitions": [_definition(source, term, "班次调整能力")]}, [source]))
        senses.extend(ids)
    assert len(discovery.concept_cards(db, snapshot)) == 2
    second = db.get(ResearchItem, senses[1])
    discovery.merge_concept_key(db, second, snapshot, "自主性")
    cards = discovery.concept_cards(db, snapshot)
    assert len(cards) == 1 and len(cards[0]["senses"]) == 2
    assert {sense["detail"]["original_term"] for sense in cards[0]["senses"]} == {"自主性", "能动性"}
    assert archive.list_revisions(db, second)[0]["before"]["concept_key"] == "能动性"


def test_unverifiable_quote_page_and_out_of_scope_book_are_rejected(db):
    a, chunk = _book(db, "范围内", "自主性指排班选择。", 4)
    outside, outside_chunk = _book(db, "范围外", "自主性指完全不同的对象。", 6)
    _, snapshot = _snapshot(db, [a])
    found = discovery.concept_sources(db, snapshot, "自主性")
    source = found["sources"][0]
    invented = _definition(source, "自主性", "排班")
    invented["quote"] = "原文根本没有这句"
    with pytest.raises(ValueError):
        discovery.normalize_concepts({"definitions": [invented]}, found["sources"])
    invented["quote"] = source["content"][:12]
    invented["page"] = 99
    with pytest.raises(ValueError):
        discovery.normalize_concepts({"definitions": [invented]}, found["sources"])
    invented.pop("page")
    invented["ref"] = f"B{outside.id}:C{outside_chunk.id}:P6"
    invented["book_id"] = outside.id
    with pytest.raises(ValueError):
        discovery.normalize_concepts({"definitions": [invented]}, found["sources"])
    with pytest.raises(HTTPException) as bad_page:
        discovery._source_is_current(db, snapshot, a.id, chunk.id, "自主性指排班选择", page=99)
    assert bad_page.value.status_code == 422
    with pytest.raises(HTTPException) as outside_scope:
        discovery._source_is_current(db, snapshot, outside.id, outside_chunk.id, "自主性指完全不同", page=6)
    assert outside_scope.value.status_code == 422


def test_reocr_invalidates_old_sense_and_blocks_new_scan(db):
    book, chunk = _book(db, "旧正文", "自主性指排班选择。", 3)
    _, snapshot = _snapshot(db, [book])
    source = discovery.concept_sources(db, snapshot, "自主性")["sources"][0]
    definition = discovery.normalize_concepts({"definitions": [_definition(source, "自主性", "排班选择")]}, [source])
    ids = discovery.persist_concepts(db, snapshot, "自主性", definition)
    chunk.content = "OCR 修正后，这页谈的是任务裁量。"
    db.commit()
    assert discovery.concept_cards(db, snapshot)[0]["senses"][0]["evidence"][0]["source_status"] == "source_changed"
    with pytest.raises(HTTPException) as stale:
        discovery.concept_sources(db, snapshot, "自主性")
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as invalid_confirmation:
        archive.update_item(db, db.get(ResearchItem, ids[0]), snapshot, {"review_status": "confirmed"})
    assert invalid_confirmation.value.status_code == 409


def test_counterevidence_scope_and_explicit_acceptance(db):
    a, chunk = _book(db, "限制条件", "研究发现算法透明未必提高自主性，在高强度考核中反而降低裁量权。", 9)
    b, outside_chunk = _book(db, "范围外", "这是范围外的反例。", 2)
    shelf, snapshot = _snapshot(db, [a])
    parent_row = archive.create_item(db, snapshot, "judgment", "算法透明提高自主性")
    parent = db.get(ResearchItem, parent_row["id"])

    def fake_retrieve(_question, *, book_ids, top_k):
        assert book_ids == [a.id] and top_k == 6
        return [{"book_id": b.id, "chunk_id": outside_chunk.id},
                {"book_id": a.id, "chunk_id": chunk.id, "snippet": chunk.content}]

    queries = discovery.normalize_queries({"queries": []}, parent.statement)
    assert {query["type"] for query in queries} == set(discovery.COUNTER_TYPES)
    sources = discovery.counter_sources(db, snapshot, queries, fake_retrieve)
    assert len(sources) == 1 and sources[0]["book_id"] == a.id
    raw = {"candidates": [{"ref": sources[0]["ref"], "quote": "在高强度考核中反而降低裁量权",
                           "type": "boundary", "relation": "limits", "reason": "高强度考核可能改变透明度的作用方向",
                           "why_it_might_change": "需要限定适用范围"}]}
    candidates = discovery.normalize_counterevidence(raw, sources)
    bad = {"candidates": [{**raw["candidates"][0], "quote": "模型虚构的原文"}]}
    with pytest.raises(ValueError):
        discovery.normalize_counterevidence(bad, sources)
    bad_page = {"candidates": [{**raw["candidates"][0], "page": 999}]}
    with pytest.raises(ValueError):
        discovery.normalize_counterevidence(bad_page, sources)
    ids = discovery.persist_counterevidence(db, snapshot, parent, candidates)
    candidate = db.get(ResearchItem, ids[0])
    assert candidate.review_status == "unreviewed"
    with pytest.raises(HTTPException) as bypass:
        archive.update_item(db, candidate, snapshot, {"review_status": "confirmed"})
    assert bypass.value.status_code == 422
    assert db.get(ResearchItem, parent.id).statement == "算法透明提高自主性"
    updated = discovery.accept_counterevidence(db, snapshot, candidate, parent,
                                                "算法透明在低考核强度下可能提高自主性")
    assert updated["statement"] == "算法透明在低考核强度下可能提高自主性"
    assert updated["evidence"][0]["relation"] == "limits"
    assert updated["evidence"][0]["review_status"] == "confirmed"
    assert archive.list_revisions(db, parent)[0]["trigger_evidence_id"] == updated["evidence"][0]["id"]
    with pytest.raises(HTTPException) as duplicate_accept:
        discovery.accept_counterevidence(db, snapshot, candidate, parent, "再次改写同一判断")
    assert duplicate_accept.value.status_code == 409
    assert db.get(Shelf, shelf.id) is not None


def test_research_api_scope_and_manual_alignment_review(db):
    a, _ = _book(db, "甲", "自主性是排班选择。", 1)
    b, _ = _book(db, "乙", "自主性是任务裁量。", 2)
    shelf, snapshot = _snapshot(db, [a, b])
    other = Shelf(name="其他书架")
    db.add(other)
    db.commit()
    found = discovery.concept_sources(db, snapshot, "自主性")
    definitions = [_definition(source, "自主性", "排班选择" if source["book_id"] == a.id else "任务裁量")
                   for source in found["sources"]]
    ids = discovery.persist_concepts(db, snapshot, "自主性",
                                     discovery.normalize_concepts({"definitions": definitions}, found["sources"]))
    alignment_id = discovery.persist_alignment(db, snapshot,
        discovery.alignment_inputs(db, snapshot, *ids),
        {"status": "partial", "reason": "二者都讨论自主性，但测量和分析单位并不一致。", "unresolved": "需要同一指标"})
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            params = {"scope_type": "shelf", "scope_id": shelf.id, "snapshot_id": snapshot.id}
            preview = client.post("/api/assistant/research/concepts/preview", json={**params, "term": "自主性"})
            assert preview.status_code == 200 and preview.json()["matched_books"] == 2
            assert client.post("/api/assistant/research/concepts/preview", json={
                **params, "term": "自主性", "aliases": ["x"]}).status_code == 422
            assert client.get("/api/assistant/research/concepts/cards", params=params).status_code == 200
            denied = client.get("/api/assistant/research/concepts/cards", params={**params, "scope_id": other.id})
            assert denied.status_code == 404
            reviewed = client.patch(f"/api/assistant/research/concepts/alignments/{alignment_id}", json={
                "scope_type": "shelf", "scope_id": shelf.id, "status": "different",
                "reason": "排班选择与任务执行裁量是两个不同维度。", "review_status": "confirmed"})
            assert reviewed.status_code == 200
            assert reviewed.json()["detail"]["status"] == "different"
            assert reviewed.json()["review_status"] == "confirmed"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_background_concept_and_counter_pipeline_keeps_proposals_unconfirmed(db, monkeypatch):
    from backend.app.api import research as api

    book, chunk = _book(db, "工作自主性", "自主性指排班选择，但高强度考核时透明度可能降低任务裁量。", 8)
    _, snapshot = _snapshot(db, [book])
    source = discovery.concept_sources(db, snapshot, "自主性")["sources"][0]
    parent_id = archive.create_item(db, snapshot, "judgment", "透明度提高自主性")["id"]
    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(api, "_research_config", lambda _db: {"configured": True})
    monkeypatch.setattr(api.LLMRouter, "get", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(api, "update_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api.retriever, "retrieve", lambda *_args, **_kwargs: [
        {"book_id": book.id, "chunk_id": chunk.id, "snippet": chunk.content}])

    async def fake_structured(_provider, _messages, normalize, _record, stage):
        if stage == "concepts":
            return normalize({"definitions": [_definition(source, "自主性", "排班选择")]})
        if stage == "queries":
            return normalize({"queries": []})
        if stage == "evaluation":
            return normalize({"candidates": [{"ref": source["ref"], "quote": "高强度考核时透明度可能降低任务裁量",
                "type": "boundary", "relation": "limits", "reason": "在高强度考核时方向可能相反",
                "why_it_might_change": "应限定考核强度"}]})
        raise AssertionError(stage)

    monkeypatch.setattr(api, "_structured", fake_structured)
    db.rollback()
    scan = await api._run_concept_scan(TaskRecord(id="test-concept"), snapshot.id, "自主性", [])
    assert len(scan["candidate_ids"]) == 1
    counter = await api._run_counter_search(TaskRecord(id="test-counter"), parent_id)
    assert len(counter["candidate_ids"]) == 1
    assert (counter["retrieved_books"], counter["total_books"]) == (1, 1)
    db.expire_all()
    assert db.get(ResearchItem, parent_id).statement == "透明度提高自主性"
    assert db.get(ResearchItem, counter["candidate_ids"][0]).review_status == "unreviewed"
    second_parent_id = archive.create_item(db, snapshot, "judgment", "另一个待检验判断")["id"]
    monkeypatch.setattr(api.retriever, "retrieve", lambda *_args, **_kwargs: [])
    db.rollback()
    empty = await api._run_counter_search(TaskRecord(id="test-counter-empty"), second_parent_id)
    assert empty["status"] == "not_found_in_scope" and empty["candidate_ids"] == []
    assert (empty["retrieved_books"], empty["total_books"]) == (0, 1)
