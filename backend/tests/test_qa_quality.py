"""问答质量三维度：多轮上下文解析、指标计算、端点契约。

这些用例锁的是**行为契约**而非实现细节：

- 意图分类与查询改写决定「追问能不能被检索到」，必须可回归；
- 指标计算决定「有没有人真的在用指标」，空样本必须诚实地返回 None 而不是 0；
- 端点契约保证前端能拿到意图、澄清与时延字段。
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base
from backend.app.models import Book, ChatLog, Shelf
from backend.app.services.qa import context as qa_context
from backend.app.services.qa import metrics as qa_metrics
from backend.app.services.qa import prompts as qa_prompts
from backend.app.services.qa.context import (INTENT_CLARIFY, INTENT_FOLLOWUP, INTENT_NEW,
INTENT_SUMMARIZE, Turn)


def _factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def _turns(*pairs) -> list[Turn]:
    return [Turn(question=q, answer=a) for q, a in pairs]


# ---------- 意图分类 ----------


def test_first_turn_self_contained_question_stays_intact():
    turn = qa_context.resolve_turn("双重差分法的关键假设是什么", [])
    assert turn.intent == INTENT_NEW
    assert turn.search_query == "双重差分法的关键假设是什么"
    assert turn.history_block == ""


def test_pronoun_followup_gets_anchor_prepended():
    turn = qa_context.resolve_turn(
        "它有什么前提", _turns(("双重差分法的关键假设是什么", "关键假设是平行趋势。")))
    assert turn.intent == INTENT_FOLLOWUP
    assert turn.unresolved_reference is True
    assert turn.anchor, "追问必须带上话题锚点"
    assert turn.search_query.endswith("它有什么前提")
    assert turn.anchor in turn.search_query
    assert turn.history_block, "追问必须能看到历史才能承接话题"


def test_ellipsis_without_anaphora_is_still_followup():
    """旧策略只在出现指代词时才补上下文，这类省略型追问会完全检索不到。"""
    turn = qa_context.resolve_turn(
        "为什么是这样", _turns(("二分查找的时间复杂度是多少", "时间复杂度为O(log n)。")))
    assert turn.intent == INTENT_FOLLOWUP
    assert turn.search_query != "为什么是这样"
    assert len(turn.search_query) > len("为什么是这样")


def test_summary_intent_does_not_require_retrieval():
    turn = qa_context.resolve_turn(
        "总结一下我们刚才讨论的内容",
        _turns(("双重差分法的关键假设是什么", "平行趋势。"),
               ("内部效度关注什么", "因果结论可信度。")))
    assert turn.intent == INTENT_SUMMARIZE
    assert turn.history_block


def test_first_turn_pronoun_asks_for_clarification():
    turn = qa_context.resolve_turn("它的局限是什么", [])
    assert turn.intent == INTENT_CLARIFY
    assert turn.unresolved_reference is True
    assert turn.clarification
    assert turn.reason == "unresolved_reference"


def test_multi_referent_comparison_asks_which_two():
    """『两者哪个更重要』的对象在历史里是两条不同命题，擅自挑一个就是错答。"""
    turn = qa_context.resolve_turn(
        "两者哪个更重要",
        _turns(("内部效度关注什么", "关注因果结论可信度。"),
               ("外部效度与结论推广有什么关系", "关注推广程度。")))
    assert turn.intent == INTENT_CLARIFY
    assert turn.reason == "ambiguous_contrast"


def test_named_comparison_does_not_ask():
    turn = qa_context.resolve_turn(
        "《方法论》和其他这类书的区别在哪",
        _turns(("内部效度关注什么", "关注因果结论可信度。"),
               ("外部效度与结论推广有什么关系", "关注推广程度。")))
    assert turn.intent != INTENT_CLARIFY


def test_topic_switch_is_not_contaminated_by_previous_anchor():
    turn = qa_context.resolve_turn(
        "广度优先搜索使用什么数据结构",
        _turns(("二分查找要求什么前提", "数据必须有序。"), ("它的复杂度是多少", "O(log n)。")))
    assert turn.intent == INTENT_NEW
    assert turn.search_query == "广度优先搜索使用什么数据结构"


def test_history_block_respects_char_budget():
    long_answer = "平行趋势。" * 900
    turn = qa_context.resolve_turn(
        "再说说它", _turns(*[("第%d轮问题关于双重差分" % i, long_answer) for i in range(6)]))
    assert len(turn.history_block) <= 2600


# ---------- 提示词契约 ----------


SOURCES = [{"chunk_id": 1, "book_id": 1, "book_title": "测试文献", "chapter_title": "第一章",
            "page_start": 1, "page_end": 2,
            "context": "双重差分法通过比较处理组与对照组识别平均处理效应。",
            "snippet": "双重差分法通过比较处理组与对照组识别平均处理效应。"}]


def test_new_question_prompt_excludes_history_but_keeps_citation_rule():
    messages = qa_prompts.build_messages(INTENT_NEW, "它有什么前提", SOURCES,
                                         "- 已讨论话题：双重差分")
    assert "历史问答" not in messages[1]["content"]
    assert "[资料N]" in messages[0]["content"]


def test_followup_prompt_marks_history_as_non_evidence():
    messages = qa_prompts.build_messages(INTENT_FOLLOWUP, "它有什么前提", SOURCES,
                                         "- 已讨论话题：双重差分")
    assert "历史问答" in messages[1]["content"]
    assert "不构成事实证据" in messages[1]["content"] or "不是可引证的原文" in messages[0]["content"]
    assert "[资料N]" in messages[0]["content"]


def test_summary_prompt_asks_for_bullets_before_citations():
    messages = qa_prompts.build_messages(INTENT_SUMMARIZE, "梳理一下", SOURCES,
                                         "- 已讨论话题：双重差分")
    assert "3–6 条要点" in messages[0]["content"]


def test_empty_sources_prompt_forbids_fabrication():
    messages = qa_prompts.build_messages(INTENT_FOLLOWUP, "它有什么前提", [],
                                         "- 已讨论话题：双重差分")
    assert "未找到相关内容" in messages[0]["content"]


# ---------- 指标计算 ----------


def test_citation_support_rate_detects_unsupported_claims():
    answer = ("双重差分法通过比较处理组与对照组在政策前后的变化识别平均处理效应，"
              "关键假设是平行趋势[资料1]。它还额外要求研究者随机分配处理组[资料1]。")
    sources = [{"context": "双重差分法通过比较处理组与对照组在政策前后的变化识别平均处理效应，"
                            "关键假设是平行趋势。"}]
    rate, details = qa_metrics.citation_support_rate(answer, sources)
    assert len(details) == 2
    assert details[0]["supported"] is True, "与原文措辞一致的论断应判为被支撑"
    assert details[1]["supported"] is False, "原文没有的论断应判为未被支撑"
    assert rate == 0.5


def test_citation_support_rate_flags_out_of_range_reference():
    rate, details = qa_metrics.citation_support_rate("凭空断言[资料9]。", SOURCES)
    assert details[0]["reason"] == "out_of_range"
    assert rate == 0.0


def test_abstention_detection():
    assert qa_metrics.is_abstention("资料中未找到相关内容。") is True
    assert qa_metrics.is_abstention("平行趋势是核心假设[资料1]。") is False


def test_redundant_source_ratio_flags_single_book():
    same = [{"book_title": "甲"}, {"book_title": "甲"}, {"book_title": "乙"}]
    assert qa_metrics.redundant_source_ratio(same) == pytest.approx(0.667, abs=1e-3)
    assert qa_metrics.redundant_source_ratio([]) is None


def test_percentile_refuses_to_invent_precision_on_tiny_samples():
    assert qa_metrics.percentile([10.0, 20.0], 0.5) is None
    # 最近邻法：20 个样本的 p95 取下标 round(0.95*19)=18 → 值 19.0
    assert qa_metrics.percentile([float(i) for i in range(1, 21)], 0.95) == 19.0


def test_summarize_reports_three_dimensions_and_honest_empty_state():
    qa_metrics.reset()
    empty = qa_metrics.summarize()
    assert empty["sample_count"] == 0
    assert empty["accuracy"]["citation_support_rate"] is None
    assert empty["latency"]["ttft_p95"] is None
    qa_metrics.record_turn({"intent": "followup", "ttft_ms": 900, "e2e_ms": 4000,
                            "retrieval_ms": 40, "source_count": 3, "cited_count": 2,
                            "citation_valid": True, "citation_support_rate": 0.8,
                            "abstained": False, "history_used": True,
                            "prompt_chars": 4200})
    report = qa_metrics.summarize()
    assert report["sample_count"] == 1
    assert report["accuracy"]["citation_support_rate"] == 0.8
    assert report["relevance"]["history_use_rate"] == 1.0
    assert report["latency"]["ttft_p95"] is None, "样本不足时不应给出 p95"
    qa_metrics.reset()


def test_gate_marks_missing_metrics_as_insufficient_not_passed():
    """样本不足时门禁必须报 insufficient，不能因为「没有失败项」就放绿灯。"""
    gate = qa_metrics.gate_report({"accuracy": {"citation_support_rate": None}},
                                  {"accuracy.citation_support_rate": 0.6})
    assert gate["passed"] is False
    assert gate["checks"]["accuracy.citation_support_rate"]["status"] == "insufficient"
    assert gate["insufficient"] == ["accuracy.citation_support_rate"]


# ---------- 端点契约 ----------


def test_chat_metrics_endpoint_exposes_three_dimensions():
    from backend.app.api.chat import chat_metrics

    qa_metrics.reset()
    payload = chat_metrics()
    assert set(payload) == {"thresholds", "summary", "gate"}
    assert set(payload["summary"]) == {"sample_count", "accuracy", "relevance", "latency"}
    assert payload["summary"]["sample_count"] == 0
    assert "accuracy.citation_support_rate" in payload["thresholds"]


def test_static_chat_routes_are_declared_before_path_parameter():
    """/chat/eval 与 /chat/metrics 必须在 /chat/{chat_id} 之前，否则永远 422。"""
    from backend.app.api.chat import router

    paths = [getattr(route, "path", "") for route in router.routes]
    assert "/api/chat/metrics" in paths and "/api/chat/eval" in paths
    assert paths.index("/api/chat/metrics") < paths.index("/api/chat/{chat_id}")
    assert paths.index("/api/chat/eval") < paths.index("/api/chat/{chat_id}")


def test_load_turns_filters_by_scope_and_returns_oldest_first():
    engine, factory = _factory()
    with factory() as db:
        for index in range(3):
            db.add(ChatLog(book_id=None, shelf_id=1 if index < 2 else 2,
                           question=f"第{index}轮问题", answer=f"第{index}轮回答",
                           conversation_id="conv123456", mode="test",
                           sources_json=json.dumps([{"book_title": "甲"}], ensure_ascii=False)))
        db.commit()
        turns = qa_context.load_turns(db, "conv123456", scope_type="shelf", scope_id=1, limit=6)
        assert [turn.question for turn in turns] == ["第0轮问题", "第1轮问题"]
        assert turns[0].sources[0]["book_title"] == "甲"
        assert qa_context.load_turns(db, None) == []
        assert qa_context.load_turns(db, "conv123456", scope_type="shelf", scope_id=9) == []
    engine.dispose()


def test_clarification_round_never_calls_the_model():
    """本地澄清记录真实耗时，并保存为可继续的会话记录。"""
    from backend.app.api.chat import _clarification_stream
    from backend.app.schemas import ChatReq
    import time

    turn = qa_context.TurnContext(INTENT_CLARIFY, "它的局限是什么", "它的局限是什么", "",
                                   unresolved_reference=True, clarification="你指的是哪一项？",
                                   clarification_options=["《甲》 第一章", "《乙》 第二章"])
    async def collect() -> str:
        chunks = [chunk async for chunk in response.body_iterator]
        return "".join(chunks)

    import asyncio

    engine, factory = _factory()
    with factory() as db:
        response = _clarification_stream(turn, ChatReq(question=turn.question), db,
                                         "conv123456", time.perf_counter() - 0.02, 1.0)
        assert response.media_type == "text/event-stream"
        body = asyncio.run(collect())
        assert db.get(ChatLog, 1).conversation_id == "conv123456"
    engine.dispose()
    assert "event: token" in body and "你指的是哪一项？" in body
    assert "《甲》 第一章" in body
    done_payload = json.loads(body.split("event: done\ndata: ")[1].strip())
    assert done_payload["qa"]["intent"] == INTENT_CLARIFY
    assert done_payload["qa"]["ttft_ms"] >= 20
    assert done_payload["qa"]["model_called"] is False
    assert done_payload["chat_id"] == 1
    assert done_payload["qa"]["source_count"] == 0


def test_first_turn_does_not_query_history_table(monkeypatch):
    """新会话不该查历史表：既省一次 IO，也不该把解析建立在「一定有历史」上。"""
    from backend.app.api import chat as chat_api
    from backend.app.schemas import ChatReq

    queried = []

    class MinimalDb:
        """只实现新问题路径真正用到的接口。"""

        def scalars(self, *_args, **_kwargs):
            queried.append("scalars")
            return []

        def get(self, *_args, **_kwargs):
            return None

        def add(self, _value):
            return None

        def commit(self):
            return None

        def refresh(self, value):
            value.id = 1

    class Provider:
        name = "fake"
        model = "fake-model"

        async def stream_chat(self, _messages):
            yield "资料中未找到相关内容。"

    monkeypatch.setattr(chat_api, "load_llm_config", lambda *_args: {})
    monkeypatch.setattr(chat_api.LLMRouter, "get", lambda *_args: Provider())
    monkeypatch.setattr(chat_api.retriever, "retrieve", lambda *_args, **_kwargs: [])

    import asyncio

    response = asyncio.run(chat_api.chat(ChatReq(question="独立问题"), MinimalDb()))
    body = asyncio.run(_drain(response))
    assert "event: done" in body
    assert not queried, "没有范围也没有历史时不应执行历史范围查询"


async def _drain(response) -> str:
    return "".join([chunk async for chunk in response.body_iterator])


def test_scope_resolution_failure_is_reported_before_any_llm_call():
    """范围为空时必须在调用模型之前报错，否则用户白等一个长任务。"""
    import asyncio

    from fastapi import HTTPException

    from backend.app.api import chat as chat_api
    from backend.app.schemas import ChatReq

    engine, factory = _factory()
    with factory() as db:
        shelf = Shelf(name="空书架")
        db.add(shelf)
        db.add(Book(title="未就绪", file_path="x.pdf", file_type="pdf", status="uploading"))
        db.commit()
        db.refresh(shelf)
        with pytest.raises(HTTPException) as caught:
            asyncio.run(chat_api.chat(ChatReq(scope_type="shelf", scope_id=shelf.id,
                                             question="任意问题"), db))
        assert caught.value.status_code == 409
    engine.dispose()
