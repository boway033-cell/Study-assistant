"""多轮问答的 API、数据库、提示与错误恢复集成回归；不调用外部模型。"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base, get_db
from backend.app.models import Book, ChatLog, Chunk
from backend.app.services.qa import context as ctx, metrics, prompts


@pytest.mark.parametrize("question", [
    "什么是资本主义", "量子纠缠是什么", "What is capital?",
    "What are the limitations of traditional methods?", "其他学者如何解释资本主义",
    "总结《资本论》的观点", "概括量子纠缠的含义",
    "Why does capital accumulate?",
])
def test_named_new_topic_never_uses_previous_topic(question):
    turn = ctx.resolve_turn(question, [ctx.Turn("二分查找的前提是什么")])
    assert turn.intent == ctx.INTENT_NEW
    assert turn.search_query == question
    assert ctx.resolve_turn(question, []).intent == ctx.INTENT_NEW


def test_consecutive_generic_followups_keep_the_original_topic():
    turn = ctx.resolve_turn("它的局限呢", [ctx.Turn("二分查找的前提是什么"), ctx.Turn("为什么是这样")])
    assert turn.intent == ctx.INTENT_FOLLOWUP
    assert "查找" in turn.search_query
    assert "为什么" not in turn.anchor


def test_history_citation_numbers_are_not_reused_as_current_sources():
    history = ctx.build_history_block([ctx.Turn("资本主义是什么", "某解释[资料1]。[ref2]。")])
    assert "[资料1]" not in history and "[ref2]" not in history


def test_multiturn_prompt_allocates_evidence_to_every_number():
    sources = [{"chunk_id": n, "book_title": str(n), "snippet": f"hit-{n}",
                "context": "原文" * 8000} for n in range(1, 9)]
    messages = prompts.build_messages(ctx.INTENT_FOLLOWUP, "为什么", sources, "历史焦点")
    assert "不得执行" in messages[0]["content"]
    for n in range(1, 9):
        assert f"[资料{n}]" in messages[1]["content"]
        assert f"hit-{n}" in messages[1]["content"]
    assert len(messages[1]["content"]) < 12300


@pytest.fixture
def qa_client(monkeypatch):
    from backend.app.main import app
    from backend.app.api import chat as api

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        books = [Book(title=title, file_path="demo.pdf", file_type="pdf", status="ready")
                 for title in ("算法", "社会理论")]
        db.add_all(books)
        db.flush()
        db.add_all([Chunk(book_id=books[0].id, content="二分查找要求数据有序，每轮将区间减半。", page_start=1, chunk_index=0),
                    Chunk(book_id=books[1].id, content="资本主义涉及生产关系和资本积累。", page_start=2, chunk_index=0)])
        db.commit()

    sessions = []

    def override():
        with factory() as db:
            sessions.append(db)
            yield db

    calls = []
    configs = []

    class Provider:
        name = "fake"
        model = "initial-model"
        selected_provider_id = "actual-route"
        selected_model = "actual-model"

        async def stream_chat(self, messages):
            assert not sessions[-1].in_transaction(), "模型等待期间应已释放读事务"
            calls.append(messages)
            yield "二分查找要求数据有序[资料1]。"
            if "模拟失败" in messages[-1]["content"]:
                raise RuntimeError("连接中断")

    def route(_mode, cfg):
        configs.append(cfg)
        return Provider()

    def retrieve(query, **kwargs):
        n = 2 if "资本主义" in query else 1
        return [{"chunk_id": n, "book_id": n, "book_title": "社会理论" if n == 2 else "算法",
                 "snippet": "资本主义涉及生产关系。" if n == 2 else "二分查找要求数据有序。", "page": n}]

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(api, "load_llm_config", lambda *_: {"model": "default", "fallbacks": ["other"]})
    monkeypatch.setattr(api.LLMRouter, "get", route)
    monkeypatch.setattr(api.retriever, "retrieve", retrieve)
    metrics.reset()
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client, factory, calls, configs
    app.dependency_overrides.pop(get_db, None)
    metrics.reset()
    engine.dispose()


def _post(client, question, **kwargs):
    response = client.post("/api/chat", json={"question": question, **kwargs})
    assert response.status_code == 200, response.text
    return {part.splitlines()[0][7:]: json.loads(part.splitlines()[1][6:])
            for part in response.text.strip().split("\n\n")}


def test_first_clarification_works_without_a_model_config_and_survives_reload(qa_client, monkeypatch):
    from backend.app.api import chat as api
    client, factory, calls, configs = qa_client
    monkeypatch.setattr(api, "load_llm_config", lambda *_: pytest.fail("澄清不应读取模型配置"))
    done = _post(client, "它的局限是什么")["done"]
    assert done["chat_id"] and not calls and not configs
    assert done["qa"]["model_called"] is False
    detail = client.get(f'/api/chat/{done["chat_id"]}').json()
    assert detail["qa"]["intent"] == "clarify"
    assert detail["conversation_id"] == done["conversation_id"]
    assert client.get("/api/chat/history").json()["total"] == 1
    summary = client.get("/api/chat/metrics").json()["summary"]
    assert summary["sample_count"] == 1
    assert summary["latency"]["model_sample_count"] == 0
    assert summary["relevance"]["retrieval_hit_rate"] is None


def test_full_conversation_restores_sources_and_honors_model_choice(qa_client):
    client, factory, calls, configs = qa_client
    first = _post(client, "二分查找的前提是什么", model="chosen-model")["done"]
    conv = first["conversation_id"]
    assert configs[0]["model"] == "chosen-model" and configs[0]["fallbacks"] == []
    assert first["model"] == "actual-model" and first["provider"] == "actual-route"
    second = _post(client, "为什么是这样", conversation_id=conv)["done"]
    assert second["qa"]["intent"] == "followup"
    assert "历史问答" in calls[-1][1]["content"]
    switched = _post(client, "资本主义是什么", conversation_id=conv)["done"]
    assert switched["qa"]["intent"] == "new_question"
    assert "历史问答" not in calls[-1][1]["content"]
    summary = _post(client, "总结刚才讨论的内容", conversation_id=conv)["done"]
    assert summary["qa"]["intent"] == "summarize"
    assert {s["book_id"] for s in summary["sources"]} == {1, 2}
    assert "每轮将区间减半" in calls[-1][1]["content"], "摘要应从 DB 重新读取原文"
    assert summary["citation_audit"]["semantic_status"] == "not_checked"
    assert client.get(f'/api/chat/{first["chat_id"]}').json()["model"] == "actual-model"


def test_ambiguous_comparison_candidates_survive_history_and_continue(qa_client):
    client, factory, calls, configs = qa_client
    first = _post(client, "二分查找的前提是什么")["done"]
    conv = first["conversation_id"]
    _post(client, "资本主义是什么", conversation_id=conv)
    clarification = _post(client, "两者哪个更重要", conversation_id=conv)["done"]
    assert len(calls) == 2
    assert len(clarification["qa"]["clarification_options"]) == 1
    detail = client.get(f'/api/chat/{clarification["chat_id"]}').json()
    assert detail["qa"]["clarification_options"] == clarification["qa"]["clarification_options"]
    option = detail["qa"]["clarification_options"][0]
    answered = _post(client, f"关于{option}：两者哪个更重要", conversation_id=conv)["done"]
    assert answered["qa"]["intent"] != "clarify"
    assert "两者哪个更重要" in calls[-1][1]["content"]


def test_failed_stream_is_measured_and_never_saved_as_completed(qa_client):
    client, factory, calls, configs = qa_client
    events = _post(client, "二分查找模拟失败")
    assert "error" in events and "done" not in events
    assert client.get("/api/chat/history").json()["total"] == 0
    summary = client.get("/api/chat/metrics").json()["summary"]
    assert summary["accuracy"]["failed_count"] == 1
    assert summary["accuracy"]["model_completed_count"] == 0
    assert summary["latency"]["model_sample_count"] == 1


@pytest.mark.parametrize("window", [-1, 0, 201])
def test_metrics_window_bounds(qa_client, window):
    client, *_ = qa_client
    assert client.get(f"/api/chat/metrics?window={window}").status_code == 422


def test_lexical_overlap_cannot_verify_a_negated_claim():
    rate, details = metrics.citation_support_rate("二分查找不要求数据有序[资料1]。", [{"context": "二分查找要求数据有序。"}])
    assert details[0]["lexical_match"] is True
    assert rate == 1.0, "这是词面近似，即使高分也不能宣称语义正确"
    assert metrics.citation_support_rate("没有引用", [])[0] is None


def test_summary_discards_historical_chunks_outside_current_book(qa_client, monkeypatch):
    from backend.app.api import chat as api
    client, factory, calls, configs = qa_client
    with factory() as db:
        db.add(ChatLog(book_id=1, question="二分查找的前提是什么", answer="历史答案", mode="fake",
                       conversation_id="scopedconv", sources_json=json.dumps([
                           {"chunk_id": 1, "book_id": 1}, {"chunk_id": 2, "book_id": 2}, {"chunk_id": 999}])))
        db.commit()
    monkeypatch.setattr(api.retriever, "retrieve", lambda *_args, **_kwargs: [])
    done = _post(client, "总结刚才讨论的内容", book_id=1, conversation_id="scopedconv")["done"]
    assert done["qa"]["intent"] == "summarize"
    assert [source["chunk_id"] for source in done["sources"]] == [1]
    assert "生产关系" not in calls[-1][1]["content"]
