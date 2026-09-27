"""An image is sent through the same configured transport as text."""
import asyncio
import json

import httpx
import pytest

from backend.app.services.llm import (AnthropicMessagesProvider, GoogleGenerateProvider,
                                      OpenAIChatProvider, parse_json_response, request_reasoning_effort)


def test_page_image_is_the_only_image_api_and_settings_have_no_separate_vision_key():
    from backend.app.main import app
    from backend.app.schemas import SettingsResp, SettingsUpdateReq

    paths = set(app.openapi()["paths"])
    assert "/api/ai/page-image" in paths
    assert "/api/ai/vision" not in paths
    assert not any(name.startswith("vision_") for name in SettingsResp.model_fields)
    assert not any(name.startswith("vision_") for name in SettingsUpdateReq.model_fields)


def test_nested_json_after_short_preface_remains_parseable():
    assert parse_json_response('结果如下：{"claims":[{"evidence":[{"ref":"B1:P1:C2"}]}]}') == {
        "claims": [{"evidence": [{"ref": "B1:P1:C2"}]}]}


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "google"])
def test_page_image_payload_uses_provider_protocol(monkeypatch, protocol):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if protocol == "anthropic":
            data = 'data: {"delta":{"text":"图表说明。"}}\n\n'
        elif protocol == "google":
            data = 'data: {"candidates":[{"content":{"parts":[{"text":"图表说明。"}]}}]}\n\n'
        else:
            data = 'data: {"choices":[{"delta":{"content":"图表说明。"}}]}\n\n'
        return httpx.Response(200, text=data)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))
    args = ("test-key", "https://example.test/v1", "image-model", "test", "测试模型")
    provider = {"openai": OpenAIChatProvider, "anthropic": AnthropicMessagesProvider,
                "google": GoogleGenerateProvider}[protocol](*args)
    messages = [{"role": "system", "content": "说明图像。"},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                                               {"type": "text", "text": "解释图表。"}]}]

    async def run():
        return "".join([part async for part in provider.stream_chat(messages)])

    assert asyncio.run(run()) == "图表说明。"
    payload = requests[0]
    if protocol == "anthropic":
        assert payload["messages"][0]["content"][0]["source"] == {
            "type": "base64", "media_type": "image/png", "data": "AA=="}
    elif protocol == "google":
        assert payload["contents"][0]["parts"][0]["inlineData"] == {
            "mimeType": "image/png", "data": "AA=="}
    else:
        assert payload["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/png")


def test_qwen_structured_reading_uses_low_reasoning_only_in_its_request(monkeypatch):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"好"}}]}\n\n')

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))
    provider = OpenAIChatProvider("test-key", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                                  "qwen3.8-max-0902", "qwen", "通义千问")

    async def run():
        token = request_reasoning_effort.set("low")
        try:
            return "".join([part async for part in provider.stream_chat([{"role": "user", "content": "提取论点"}])])
        finally:
            request_reasoning_effort.reset(token)

    assert asyncio.run(run()) == "好"
    assert captured[0]["reasoning_effort"] == "low"
    assert request_reasoning_effort.get() is None
