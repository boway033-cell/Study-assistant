"""Assistant scope must remain bounded as shelves, projects and sources change."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.assistant import (MemoryWrite, ProjectWrite, assistant_status,
                                       assistant_connections, create_memory, create_project,
                                       list_memories, prepare_estimate)
from backend.app.api.chat import chat
from backend.app.core.database import Base, get_db
from backend.app.main import app
from backend.app.models import (AssistantProject, Book, ChatLog, Chunk, KnowledgeNote,
                                SensemakingArtifact, Setting, Shelf)
from backend.app.schemas import ChatReq
from backend.app.services import sensemaking as sm
from backend.app.services.assistant_context import current_reading, personal_context
from backend.app.services.assistant_scope import resolve_scope
from backend.app.services.rag import retriever
from backend.app.services.llm import load_llm_config


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def _book(db, title: str) -> Book:
    book = Book(title=title, file_path=title + ".pdf", file_type="pdf", status="ready")
    db.add(book)
    db.flush()
    return book


def test_project_reuses_live_shelf_membership_without_copying_books(db):
    first, second = _book(db, "甲书"), _book(db, "乙书")
    shelf = Shelf(name="主题书架", books=[first])
    db.add(shelf)
    db.commit()
    assert resolve_scope(db, "shelf", shelf.id)[0] == [first.id]

    project = create_project(ProjectWrite(name="论文", goal="比较两个观点", shelf_ids=[shelf.id]), db)
    assert resolve_scope(db, "project", project["id"])[0] == [first.id]
    shelf.books.append(second)
    db.commit()
    assert resolve_scope(db, "project", project["id"])[0] == [first.id, second.id]


def test_empty_scope_does_not_fall_back_to_entire_library(db, monkeypatch):
    _book(db, "全库中的书")
    shelf = Shelf(name="空书架")
    db.add(shelf)
    db.commit()
    assert resolve_scope(db, "shelf", shelf.id)[0] == []
    monkeypatch.setattr(retriever, "record_retrieval_eval", lambda *_: None)
    monkeypatch.setattr(retriever.fts, "search", lambda *_args, **_kwargs: pytest.fail("空范围触发了全文检索"))
    assert retriever.retrieve("任意问题", book_ids=[]) == []


@pytest.mark.asyncio
async def test_empty_shelf_chat_fails_before_model_call(db):
    shelf = Shelf(name="空书架")
    db.add(shelf)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        await chat(ChatReq(scope_type="shelf", scope_id=shelf.id, question="这里有什么资料？"), db)
    assert exc.value.status_code == 409


def test_full_text_asset_becomes_stale_after_source_edit(db):
    book = _book(db, "研究资料")
    chunk = Chunk(book_id=book.id, content="原始论点与相关证据", chunk_index=0, page_start=1)
    db.add(chunk)
    db.commit()
    artifact = SensemakingArtifact(kind="reading", book_ids_json=json.dumps([book.id]), focus="",
                                  payload_json=json.dumps({"nodes": [], "coverage": {"complete": True}}),
                                  source_versions_json=json.dumps(sm.document_digest(db, book.id)),
                                  model_name="test", prompt_version=sm.PROMPT_VERSION)
    db.add(artifact)
    db.commit()
    assert current_reading(db, book.id).id == artifact.id
    chunk.content = "材料已修改"
    db.commit()
    assert current_reading(db, book.id) is None


def test_previous_verified_reading_stays_available_but_incomplete_one_is_not(db):
    book = _book(db, "历史研读")
    db.add(Chunk(book_id=book.id, content="旧版研读的原始资料仍然有效。", chunk_index=0, page_start=1))
    db.commit()
    artifact = SensemakingArtifact(kind="reading", book_ids_json=json.dumps([book.id]), focus="",
                                   payload_json=json.dumps({"coverage": {"complete": True}}),
                                   source_versions_json=json.dumps(sm.document_digest(db, book.id)),
                                   model_name="test", prompt_version="sensemaking-v2-fulltext")
    db.add(artifact)
    db.commit()
    assert current_reading(db, book.id).id == artifact.id
    artifact.payload_json = json.dumps({"coverage": {"complete": False}})
    db.commit()
    assert current_reading(db, book.id) is None


def test_only_current_scope_user_memory_enters_context(db):
    book_a, book_b = _book(db, "甲书"), _book(db, "乙书")
    shelf_a = Shelf(name="甲", books=[book_a])
    shelf_b = Shelf(name="乙", books=[book_b])
    db.add_all([shelf_a, shelf_b])
    db.commit()
    create_memory(MemoryWrite(scope_type="shelf", scope_id=shelf_a.id,
                              kind="opinion", content="我认为治理需要公开参与"), db)
    create_memory(MemoryWrite(scope_type="shelf", scope_id=shelf_b.id,
                              kind="opinion", content="我认为治理只需封闭决策"), db)
    db.add(KnowledgeNote(book_id=book_a.id, title="治理观察", content="治理依赖透明沟通", origin="user"))
    db.commit()
    context = personal_context(db, "shelf", shelf_a.id, [book_a.id], "治理如何参与？")
    assert "公开参与" in context and "透明沟通" in context
    assert "封闭决策" not in context
    assert len(list_memories("shelf", shelf_a.id, db)) == 1


def test_status_exposes_prepared_coverage_and_unparsed_source(db):
    ready, unparsed = _book(db, "已读"), _book(db, "等待 OCR")
    unparsed.status = "needs_ocr"
    db.add(Chunk(book_id=ready.id, content="可读取正文", chunk_index=0, page_start=1))
    shelf = Shelf(name="主题", books=[ready, unparsed])
    db.add(shelf)
    db.commit()
    status = assistant_status("shelf", shelf.id, db)
    assert status["total"] == 2
    assert {row["state"] for row in status["books"]} == {"ready_to_prepare", "needs_source"}


def test_confirmed_question_finds_original_passage_from_current_reading(db):
    book = _book(db, "新资料")
    chunk = Chunk(book_id=book.id, content="公众参与能够提升治理的透明度，也会增加协调成本。",
                  chunk_index=0, page_start=7)
    shelf = Shelf(name="治理", books=[book])
    db.add_all([chunk, shelf])
    db.commit()
    payload = {"nodes": [{"statement": "公众参与与治理透明度有关", "reasoning": "讨论协调成本",
                          "evidence": [{"chunk_id": chunk.id, "quote": "公众参与能够提升治理的透明度"}]}],
               "reading_passes": [], "coverage": {"complete": True}}
    db.add(SensemakingArtifact(kind="reading", book_ids_json=json.dumps([book.id]), focus="",
                               payload_json=json.dumps(payload, ensure_ascii=False),
                               source_versions_json=json.dumps(sm.document_digest(db, book.id)),
                               model_name="test", prompt_version=sm.PROMPT_VERSION))
    db.commit()
    create_memory(MemoryWrite(scope_type="shelf", scope_id=shelf.id,
                              kind="question", content="公众参与如何影响治理透明度？"), db)
    links = assistant_connections("shelf", shelf.id, db)
    assert len(links) == 1
    assert links[0]["source"]["chunk_id"] == chunk.id
    assert links[0]["source"]["page_start"] == 7
    chunk.content = "完全不同的新正文"
    db.commit()
    assert assistant_connections("shelf", shelf.id, db) == []


def test_preparation_preview_uses_user_model_rate(db):
    book = _book(db, "待研读资料")
    db.add(Chunk(book_id=book.id, content="研究问题与方法。" * 80, chunk_index=0, page_start=1))
    shelf = Shelf(name="主题", books=[book])
    db.add(shelf)
    cfg = load_llm_config(db, "research")
    key = f"{cfg['provider_id']}:{cfg['model']}"
    db.add(Setting(key="ai_price_rates", value=json.dumps({key: {"input": 2.0, "output": 4.0}})))
    db.commit()
    estimate = prepare_estimate("shelf", shelf.id, db)
    assert estimate["model"] == cfg["model"]
    assert estimate["books"][0]["estimated_calls"] > 0
    assert estimate["books"][0]["estimated_cost_cny"] is not None


def test_http_scope_and_memory_contract(db):
    book = _book(db, "我的资料")
    shelf = Shelf(name="我的书架", books=[book])
    db.add(shelf)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            status = client.get("/api/assistant/status", params={"scope_type": "shelf", "scope_id": shelf.id})
            assert status.status_code == 200
            assert status.json()["books"][0]["book_id"] == book.id
            saved = client.post("/api/assistant/memories", json={
                "scope_type": "shelf", "scope_id": shelf.id, "kind": "goal", "content": "理解这个领域"})
            assert saved.status_code == 201
            assert client.get("/api/assistant/memories", params={
                "scope_type": "shelf", "scope_id": shelf.id}).json()[0]["content"] == "理解这个领域"
    finally:
        app.dependency_overrides.clear()


def test_existing_chat_history_columns_are_migrated(monkeypatch):
    import importlib
    main = importlib.import_module("backend.app.main")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE chat_logs"))
        conn.execute(text("""CREATE TABLE chat_logs (
            id INTEGER PRIMARY KEY, book_id INTEGER, question TEXT NOT NULL,
            answer TEXT NOT NULL, sources_json TEXT, mode VARCHAR(20) NOT NULL,
            model_name VARCHAR(100), created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""))
    # Startup creates newly introduced tables before applying additive migrations.
    Base.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)
    main._migrate()
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(chat_logs)"))}
    assert {"shelf_id", "project_id", "conversation_id"}.issubset(cols)
    engine.dispose()


@pytest.mark.asyncio
async def test_follow_up_uses_only_questions_from_same_scope(db, monkeypatch):
    import importlib
    chat_api = importlib.import_module("backend.app.api.chat")
    book_a, book_b = _book(db, "甲书"), _book(db, "乙书")
    shelf_a = Shelf(name="甲", books=[book_a])
    shelf_b = Shelf(name="乙", books=[book_b])
    db.add_all([shelf_a, shelf_b])
    db.flush()
    session_id = "abcdefghijklmnop"
    db.add_all([
        ChatLog(shelf_id=shelf_a.id, conversation_id=session_id,
                question="公众参与如何影响治理？", answer="待核对", mode="test"),
        ChatLog(shelf_id=shelf_b.id, conversation_id=session_id,
                question="秘密预算是多少？", answer="待核对", mode="test"),
    ])
    db.commit()
    captured = {}

    class FakeProvider:
        name = "test"
        model = "fake"

        async def stream_chat(self, messages):
            captured["messages"] = messages
            yield "资料中未找到相关内容"

    monkeypatch.setattr(chat_api.LLMRouter, "get", lambda *_args, **_kwargs: FakeProvider())
    def no_sources(question, **_kwargs):
        captured["query"] = question
        return []
    monkeypatch.setattr(chat_api.retriever, "retrieve", no_sources)
    response = await chat_api.chat(ChatReq(scope_type="shelf", scope_id=shelf_a.id,
                                          conversation_id=session_id, question="它有什么限制？"), db)
    async for _ in response.body_iterator:
        pass
    prompt = "\n".join(message["content"] for message in captured["messages"])
    assert "公众参与如何影响治理" in prompt
    assert "秘密预算" not in prompt
    # 改写后的检索查询是「同范围锚点实词 + 本轮问句」，不再是整句拼接；
    # 这里断言行为契约：另一书架的问题不得泄漏进本轮上下文或检索查询。
    query = captured["query"]
    assert "它有什么限制" in query
    assert "公众" in query and "参与" in query
    assert "秘密" not in query and "预算" not in query
