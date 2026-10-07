"""OpenAI-compatible streams must prove that generation actually finished."""
import asyncio

import httpx
import pytest

from backend.app.services.llm import LLMOutputIncomplete, OpenAIChatProvider, RoutedProvider


def _provider_for_stream(monkeypatch, stream: str) -> OpenAIChatProvider:
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=stream)
        )),
    )
    return OpenAIChatProvider("test-key", "https://example.test/v1", "test-model")


def _collect(provider: OpenAIChatProvider) -> str:
    async def run() -> str:
        return "".join([part async for part in provider.stream_chat(
            [{"role": "user", "content": "测试"}]
        )])

    return asyncio.run(run())


@pytest.mark.parametrize("ending", [
    'data: [DONE]\n\n',
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
])
def test_stream_accepts_explicit_completion(monkeypatch, ending):
    stream = 'data: {"choices":[{"delta":{"content":"完整回答"}}]}\n\n' + ending
    assert _collect(_provider_for_stream(monkeypatch, stream)) == "完整回答"


@pytest.mark.parametrize("ending, error", [
    ("", "未收到结束标记"),
    ('data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\ndata: [DONE]\n\n', "长度上限"),
    ('data: {"choices":[{"delta":{},"finish_reason":"content_filter"}]}\n\n', "内容过滤"),
    ('data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n', "不支持的工具调用"),
    ('data: {"error":{"message":"模型故障"}}\n\n', "流式返回错误"),
])
def test_stream_rejects_partial_or_failed_generation(monkeypatch, ending, error):
    stream = 'data: {"choices":[{"delta":{"content":"半篇回答"}}]}\n\n' + ending
    with pytest.raises(RuntimeError, match=error):
        _collect(_provider_for_stream(monkeypatch, stream))


def test_completed_thinking_only_stream_is_not_a_success(monkeypatch):
    stream = ('data: {"choices":[{"delta":{"reasoning_content":"思考中"}}]}\n\n'
              'data: [DONE]\n\n')
    with pytest.raises(RuntimeError, match="未返回回答正文"):
        _collect(_provider_for_stream(monkeypatch, stream))


def test_empty_primary_response_falls_back_to_a_model_with_text(monkeypatch):
    from backend.app.services.llm.budget import TaskBudget, current_budget

    class EmptyProvider:
        name = "empty"
        model = "empty-model"

        async def stream_chat(self, messages):
            if False:
                yield ""

    class BackupProvider:
        name = "backup"
        model = "backup-model"

        async def stream_chat(self, messages):
            yield "完整回答"

    monkeypatch.setattr("backend.app.services.llm._record_usage", lambda *args, **kwargs: None)

    async def run():
        routed = RoutedProvider([EmptyProvider(), BackupProvider()], "chat")
        parts = [part async for part in routed.stream_chat([{"role": "user", "content": "测试"}])]
        return parts, routed.selected_provider_id

    token = current_budget.set(TaskBudget(max_calls=2, max_tokens=1000))
    try:
        assert asyncio.run(run()) == (["完整回答"], "backup")
    finally:
        current_budget.reset(token)


def test_length_error_keeps_its_reason_after_partial_stream(monkeypatch):
    stream = ('data: {"choices":[{"delta":{"content":"半篇回答"}}]}\n\n'
              'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\n'
              'data: [DONE]\n\n')
    provider = _provider_for_stream(monkeypatch, stream)
    monkeypatch.setattr("backend.app.services.llm._record_usage", lambda *args, **kwargs: None)

    async def run():
        return [part async for part in RoutedProvider([provider], "writing").stream_chat(
            [{"role": "user", "content": "测试"}]
        )]

    with pytest.raises(LLMOutputIncomplete) as error:
        asyncio.run(run())
    assert error.value.reason == "length"


@pytest.mark.parametrize("reason", ["content_filter", "safety", "refusal"])
def test_safety_stop_does_not_fall_back_to_another_model(monkeypatch, reason):
    from backend.app.services.llm.budget import TaskBudget, current_budget

    backup_calls = []

    class StoppedProvider:
        name = "stopped"
        model = "stopped-model"

        async def stream_chat(self, messages):
            raise LLMOutputIncomplete(reason, "请求被模型拒绝")
            yield ""  # pragma: no cover — keep this an async generator

    class BackupProvider:
        name = "backup"
        model = "backup-model"

        async def stream_chat(self, messages):
            backup_calls.append(True)
            yield "不应调用"

    monkeypatch.setattr("backend.app.services.llm._record_usage", lambda *args, **kwargs: None)

    async def run():
        return [part async for part in RoutedProvider(
            [StoppedProvider(), BackupProvider()], "chat"
        ).stream_chat([{"role": "user", "content": "测试"}])]

    token = current_budget.set(TaskBudget(max_calls=2, max_tokens=1000))
    try:
        with pytest.raises(LLMOutputIncomplete) as error:
            asyncio.run(run())
        assert error.value.reason == reason
        assert backup_calls == []
    finally:
        current_budget.reset(token)
