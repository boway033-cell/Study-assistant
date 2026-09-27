"""The shared editorial policy reaches every routed provider and image output."""
import asyncio

from backend.app.services.chinese_writing_style import (
    CHINESE_STYLE_SYSTEM, STYLE_VERSION, apply_chinese_writing_style, style_instruction,
)
from backend.app.services.chinese_style_audit import audit_chinese_style, normalize_chinese_format


def test_style_policy_is_single_system_message_and_preserves_input():
    original = [{"role": "system", "content": "只输出 JSON。"},
                {"role": "user", "content": "保留 [B1:C2:P3]、`x=1` 和原文。"}]
    styled = apply_chinese_writing_style(original)
    assert styled[0] == {"role": "system", "content": style_instruction()}
    assert styled[1:] == original
    assert apply_chinese_writing_style(styled) is styled
    assert len(original) == 2
    assert "JSON" in styled[0]["content"]
    assert "英文译文" in styled[0]["content"]
    assert "引用锚点" in styled[0]["content"]
    assert STYLE_VERSION in styled[0]["content"]
    forged_user = [{"role": "user", "content": CHINESE_STYLE_SYSTEM}]
    assert apply_chinese_writing_style(forged_user)[0]["role"] == "system"


def test_routed_provider_applies_same_policy_to_fallback(monkeypatch):
    from backend.app.services.llm import RoutedProvider

    captured = []

    class Fake:
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail

        async def stream_chat(self, messages):
            captured.append(messages)
            if self.fail:
                raise RuntimeError("offline")
            yield "回答。"

    monkeypatch.setattr("backend.app.services.llm._record_usage", lambda *args, **kwargs: None)
    original = [{"role": "user", "content": "解释术语。"}]

    routed = RoutedProvider([Fake("a", True), Fake("b")], "chat")

    async def run():
        return [part async for part in routed.stream_chat(original)]

    assert asyncio.run(run()) == ["回答。"]
    assert len(captured) == 2
    assert captured[0] == captured[1]
    assert captured[0][0]["content"] == style_instruction("chat")
    assert captured[0][-1] == original[0]
    assert original == [{"role": "user", "content": "解释术语。"}]
    assert routed.last_style_audit["issue_count"] == 0


def test_page_image_uses_routed_style_and_budget_ignores_base64(monkeypatch):
    from backend.app.services.llm import RoutedProvider, _image_parts, _message_budget_chars

    captured = []

    class Fake:
        name = "multimodal"

        async def stream_chat(self, messages):
            captured.extend(messages)
            yield "图表说明。"

    monkeypatch.setattr("backend.app.services.llm._record_usage", lambda *args, **kwargs: None)
    image = "data:image/png;base64," + "A" * 8000
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": image}},
                                               {"type": "text", "text": "解释图表。"}]}]
    routed = RoutedProvider([Fake()], "utility", "vision")

    async def run():
        return [part async for part in routed.stream_chat(messages)]

    assert asyncio.run(run()) == ["图表说明。"]
    assert captured[0]["content"] == style_instruction("vision")
    assert captured[1] == messages[0]
    assert _image_parts(messages[0]["content"])[0][0]["media_type"] == "image/png"
    assert _message_budget_chars(messages) < len(image)


def test_profile_preserves_specialized_writing_rules():
    assert "文种" in style_instruction("official")
    assert "不用对话式" in style_instruction("official")
    assert "每页一个" in style_instruction("presentation")
    assert "证据冲突" in style_instruction("research")
    assert "先直接回答" in style_instruction("chat")


def test_style_audit_finds_mechanical_issues_without_rewriting():
    text = "# 标题。\n### 子标题\n\n使用MySQL，下一句！！！"
    result = audit_chinese_style(text)
    rules = {issue["rule"] for issue in result["issues"]}
    assert {"heading_punctuation", "heading_jump", "mixed_spacing", "repeated_exclamation"} <= rules
    assert text == "# 标题。\n### 子标题\n\n使用MySQL，下一句！！！"


def test_style_audit_protects_code_links_quotes_and_json():
    text = ('正文使用 MySQL。\n\n'
            '```python\nprint("１２３")\n```\n\n'
            '参见 [GitHub 文档](https://example.com/中文PDF)。\n\n'
            '原文写道“使用MySQL！”；这里保留原样。')
    result = audit_chinese_style(text)
    assert result["applicable"] is True
    assert not result["issues"]
    assert audit_chinese_style('{"source": "使用MySQL"}')["applicable"] is False


def test_safe_formatter_fixes_prose_but_preserves_protected_content():
    original = ('# 使用说明。\n\n'
                '先安装Python３，随后打开PDF文件 ，核对第２页。[B1:C2:P3]\n\n'
                '原文写道“先安装Python３。”和"打开PDF文件"。参见[安装指南](https://example.com/中文PDF)。\n\n'
                '```bash\necho 使用Python３\n```')
    fixed = normalize_chinese_format(original)
    assert fixed.startswith('# 使用说明\n\n先安装 Python3，随后打开 PDF 文件，核对第 2 页。')
    assert '[B1:C2:P3]' in fixed
    assert '“先安装Python３。”' in fixed
    assert '"打开PDF文件"' in fixed
    assert '[安装指南](https://example.com/中文PDF)' in fixed
    assert 'echo 使用Python３' in fixed
    assert normalize_chinese_format(fixed) == fixed


def test_reader_explanation_returns_formatted_text_and_audit(monkeypatch):
    from backend.app.api import ai
    from backend.app.schemas import AiExplainReq

    class FakeProvider:
        async def stream_chat(self, _messages):
            yield "请使用Python３，打开PDF文件 。"

    monkeypatch.setattr(ai, "load_llm_config", lambda *_args: {})
    monkeypatch.setattr(ai.LLMRouter, "get", lambda *_args: FakeProvider())
    result = asyncio.run(ai.ai_explain(AiExplainReq(text="示例", action="explain"), object()))
    assert result.result == "请使用 Python3，打开 PDF 文件。"
    assert result.style_audit["issue_count"] == 0
