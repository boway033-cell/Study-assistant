"""Regression checks for the user-visible AI evidence boundary."""
import asyncio
import json


def test_chat_reports_reference_check_without_claiming_semantic_support(monkeypatch):
    from backend.app.api import chat as chat_api
    from backend.app.schemas import ChatReq
    from backend.app.services.rag import reranker

    source = {"chunk_id": 7, "book_id": 3, "page": 2, "page_start": 2,
              "page_end": 2, "book_title": "测试文献", "chapter_title": "第一章",
              "snippet": "证据片段"}
    monkeypatch.setattr(chat_api.retriever, "retrieve", lambda *_args, **_kwargs: [source])
    monkeypatch.setattr(chat_api.retriever, "build_prompt", lambda *_args: [])
    monkeypatch.setattr(chat_api, "load_llm_config", lambda *_args: {})
    monkeypatch.setattr(chat_api.LLMRouter, "get", lambda *_args: Provider())
    recorded = []
    monkeypatch.setattr(chat_api, "record_citation_eval", recorded.append)

    class FakeDb:
        def add(self, _value): pass
        def commit(self): pass
        def refresh(self, value): value.id = 42

    class Provider:
        name = "fake"
        model = "fake-model"

        async def stream_chat(self, _messages):
            yield "依据[资料1]可以说明这一点。"

    async def run():
        response = await chat_api.chat(ChatReq(question="问题"), FakeDb())
        return "".join([part async for part in response.body_iterator])

    events = asyncio.run(run())
    done_line = next(line for line in events.splitlines() if line.startswith("data: ") and '"chat_id"' in line)
    done = json.loads(done_line.removeprefix("data: "))
    assert done["citation_reference_valid"] is True
    # 词面筛查不等于语义校验；两种状态必须区分。
    assert done["citation_audit"]["semantic_status"] == "not_checked"
    assert done["citation_audit"]["support_method"] == "lexical_overlap_proxy"
    assert "support_rate" in done["citation_audit"]
    # 引用语义仍未被判定为「已核实」——词面重合不能冒充蕴含证明。
    assert done["citation_verified"] is False
    assert done["qa"]["intent"] == "new_question"
    assert recorded[0]["verified"] is True
    assert chat_api.verify_citations is reranker.verify_citations


def test_research_plan_reserves_scoped_search_for_evidence_needs():
    from backend.app.api.study import _retrieve_research_candidates

    calls = []

    def retrieve(query, *, book_ids, top_k):
        calls.append((query, tuple(book_ids), top_k))
        if query == "反例" and book_ids == [2]:
            return [{"book_id": 2, "chunk_id": 22, "page_start": 3, "page_end": 3}]
        if query == "主题":
            return [{"book_id": book_ids[0], "chunk_id": 10 + book_ids[0],
                     "page_start": 1, "page_end": 1}]
        return []

    items, checks = _retrieve_research_candidates(
        retrieve, "主题", {"evidence_needs": ["反例"], "subquestions": ["机制"]}, [1, 2])
    assert calls[0][:2] == ("反例", (1,))
    assert calls[1][:2] == ("反例", (2,))
    assert {item["book_id"] for item in items} == {1, 2}
    assert checks[0]["status"] == "candidate_found"
    assert checks[0]["source_refs"] == ["B2:P3:C22"]
    assert all(book_ids in {(1,), (2,)} for _, book_ids, _ in calls)
