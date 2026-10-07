"""Anthropic and Gemini streams must not archive partial or thought-only answers."""

import asyncio

import httpx
import pytest

from backend.app.services.llm import (AnthropicMessagesProvider, GoogleGenerateProvider,
                                      LLMOutputIncomplete)


def _collect(monkeypatch, protocol: str, stream: str) -> str:
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=stream))
    ))
    provider_type = AnthropicMessagesProvider if protocol == "anthropic" else GoogleGenerateProvider
    provider = provider_type("key", "https://example.test/v1", "model", protocol, protocol)

    async def run():
        return "".join([part async for part in provider.stream_chat(
            [{"role": "user", "content": "测试"}]
        )])

    return asyncio.run(run())


@pytest.mark.parametrize("ending,reason", [
    ('data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n'
     'data: {"type":"message_stop"}\n\n', None),
    ('data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"}}\n\n'
     'data: {"type":"message_stop"}\n\n', "length"),
    ('', "interrupted"),
])
def test_anthropic_requires_normal_stop(monkeypatch, ending, reason):
    stream = ('data: {"type":"content_block_delta","delta":{"type":"text_delta",'
              '"text":"正文"}}\n\n' + ending)
    if reason is None:
        assert _collect(monkeypatch, "anthropic", stream) == "正文"
    else:
        with pytest.raises(LLMOutputIncomplete) as error:
            _collect(monkeypatch, "anthropic", stream)
        assert error.value.reason == reason


@pytest.mark.parametrize("finish,reason", [
    ("STOP", None), ("MAX_TOKENS", "length"), ("SAFETY", "safety"),
    (None, "interrupted"),
])
def test_gemini_requires_normal_finish(monkeypatch, finish, reason):
    end = f',"finishReason":"{finish}"' if finish else ""
    stream = ('data: {"candidates":[{"content":{"parts":[{"text":"正文"}]}'
              + end + '}]}\n\n')
    if reason is None:
        assert _collect(monkeypatch, "google", stream) == "正文"
    else:
        with pytest.raises(LLMOutputIncomplete) as error:
            _collect(monkeypatch, "google", stream)
        assert error.value.reason == reason


def test_gemini_thought_part_is_not_shown_as_answer(monkeypatch):
    stream = ('data: {"candidates":[{"content":{"parts":['
              '{"text":"内部思路","thought":true},{"text":"可见回答"}]},'
              '"finishReason":"STOP"}]}\n\n')
    assert _collect(monkeypatch, "google", stream) == "可见回答"
