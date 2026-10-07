"""Thinking tokens use the same task budget as visible output."""
import asyncio
import json

import httpx
import pytest

from backend.app.services.llm import DeepSeekProvider, OpenAIChatProvider, RoutedProvider
from backend.app.services.llm.budget import BudgetExceeded, TaskBudget, current_budget


def _mock_sse(monkeypatch, stream: str, requests: list):
    real_client = httpx.AsyncClient

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, text=stream)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler)
    ))


@pytest.mark.parametrize("base_url,model,expected_param", [
    ("https://api.deepseek.com", "deepseek-flash", "max_tokens"),
    ("https://api.openai.com/v1", "gpt-4o", "max_completion_tokens"),
    ("https://api.openai.com/v1", "unknown-model", None),
    ("https://gateway.example.test/v1", "model-test", None),
])
def test_only_official_chat_endpoints_receive_their_documented_output_cap(
    monkeypatch, base_url, model, expected_param,
):
    requests = []
    _mock_sse(monkeypatch, 'data: {"choices":[{"delta":{"content":"完成"}}]}\n\ndata: [DONE]\n\n', requests)
    provider = OpenAIChatProvider("test-key", base_url, model)
    budget = TaskBudget(max_tokens=100)
    budget.start_call(2)  # one estimated input token

    async def run():
        return "".join([part async for part in provider.stream_chat(
            [{"role": "user", "content": "测试"}]
        )])

    token = current_budget.set(budget)
    try:
        assert asyncio.run(run()) == "完成"
    finally:
        current_budget.reset(token)

    if expected_param:
        assert requests[0][expected_param] == 99
    else:
        assert "max_tokens" not in requests[0]
        assert "max_completion_tokens" not in requests[0]


def test_official_openai_cap_does_not_exceed_known_model_limit(monkeypatch):
    requests = []
    _mock_sse(monkeypatch, 'data: {"choices":[{"delta":{"content":"完成"}}]}\n\ndata: [DONE]\n\n', requests)
    provider = OpenAIChatProvider("test-key", "https://api.openai.com/v1", "gpt-4o")
    budget = TaskBudget(max_tokens=200000)
    budget.start_call(2)

    async def run():
        return [part async for part in provider.stream_chat([{"role": "user", "content": "测试"}])]

    token = current_budget.set(budget)
    try:
        asyncio.run(run())
    finally:
        current_budget.reset(token)
    assert requests[0]["max_completion_tokens"] == 16384


def test_openai_family_limit_matches_versioned_models():
    from backend.app.services.llm import _openai_output_limit

    assert _openai_output_limit("gpt-5.4-mini") == 128000
    assert _openai_output_limit("gpt-4.1-2025-04-14") == 32768


def test_hidden_reasoning_consumes_budget_before_visible_answer(monkeypatch):
    requests = []
    _mock_sse(monkeypatch, (
        'data: {"choices":[{"delta":{"reasoning_content":"思考思考思考思考"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"完整回答"}}]}\n\n'
        'data: [DONE]\n\n'
    ), requests)
    monkeypatch.setattr("backend.app.services.llm._record_usage", lambda *args, **kwargs: None)
    monkeypatch.setattr("backend.app.services.chinese_writing_style.apply_chinese_writing_style",
                        lambda messages, profile: messages)
    provider = DeepSeekProvider("test-key", "https://api.deepseek.com", "deepseek-flash")
    budget = TaskBudget(max_tokens=6, max_calls=1)

    async def run():
        routed = RoutedProvider([provider], "chat")
        return [part async for part in routed.stream_chat([{"role": "user", "content": "测试"}])]

    token = current_budget.set(budget)
    try:
        with pytest.raises(BudgetExceeded, match="Token"):
            asyncio.run(run())
    finally:
        current_budget.reset(token)

    assert provider.reasoning_chars == 8
    assert budget.used_calls == 1
    assert budget.used_tokens == 5  # input 1 + hidden reasoning 4
    assert requests[0]["max_tokens"] == 5


def test_call_does_not_start_when_input_exhausts_budget():
    budget = TaskBudget(max_tokens=1, max_calls=1)
    with pytest.raises(BudgetExceeded, match="Token"):
        budget.start_call(2)
    assert budget.used_calls == 0
