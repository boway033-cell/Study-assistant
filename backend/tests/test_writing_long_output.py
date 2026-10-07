"""Long-form writing must not archive a provider-limited partial answer."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from backend.app.core.database import Base
from backend.app.models import WritingDnaProfile, WritingDnaRevision, WritingOutput
from backend.app.services import writing_lab
from backend.app.services.llm import LLMOutputIncomplete


def _patch_writing_inputs(monkeypatch, provider):
    monkeypatch.setattr(writing_lab, "load_llm_config", lambda *args: {})
    monkeypatch.setattr(writing_lab.LLMRouter, "get", lambda *args: provider)
    monkeypatch.setattr(writing_lab, "collect_literature_evidence",
                        lambda *args, **kwargs: ("可用的原始证据", [], set()))
    monkeypatch.setattr(writing_lab, "collect_writing_knowledge",
                        lambda *args, **kwargs: ("可用的知识笔记", [], set()))


@pytest.mark.asyncio
async def test_review_resumes_at_length_limit_then_saves_one_complete_output(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    class Provider:
        calls = 0

        async def stream_chat(self, messages):
            self.calls += 1
            if self.calls == 1:
                yield "# 长文\n\n这里是已完成的一段长文内容。"
                raise LLMOutputIncomplete("length", "输出达到模型长度上限")
            assert messages[0]["role"] == "system"
            assert "可用的原始证据" in messages[1]["content"]
            assert messages[-2]["role"] == "assistant"
            assert "已完成的一段长文内容" in messages[-2]["content"]
            yield "已完成的一段长文内容。接续论证。\n\n## 结论\n论证已经结束。"

    provider = Provider()
    _patch_writing_inputs(monkeypatch, provider)
    try:
        with Session(engine) as db:
            row = await writing_lab.generate_literature_review(
                db, question="证据如何关联？", title="长文", book_ids=[1, 2], length=3500)
            assert provider.calls == 2
            assert row.output_text.count("已完成的一段长文内容") == 1
            assert "## 结论" in row.output_text
            assert json.loads(row.audit_json)["generation_segments"] == 2
            assert db.scalar(select(func.count()).select_from(WritingOutput)) == 1
    finally:
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["length", "interrupted"])
async def test_incomplete_review_never_creates_output(monkeypatch, reason):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    class Provider:
        calls = 0

        async def stream_chat(self, messages):
            self.calls += 1
            yield f"# 未完成文章\n\n第 {self.calls} 次仍未写完。"
            raise LLMOutputIncomplete(reason, "模型没有正常完成")

    provider = Provider()
    _patch_writing_inputs(monkeypatch, provider)
    try:
        with Session(engine) as db:
            with pytest.raises((LLMOutputIncomplete, RuntimeError)):
                await writing_lab.generate_literature_review(
                    db, question="证据如何关联？", title="长文", book_ids=[1, 2], length=3500)
            assert provider.calls == (3 if reason == "length" else 1)
            assert db.scalar(select(func.count()).select_from(WritingOutput)) == 0
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_incomplete_imitation_never_creates_output(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    class Provider:
        async def stream_chat(self, messages):
            yield "# 未完成仿写\n\n事实尚未阐述完。"
            raise LLMOutputIncomplete("interrupted", "模型流中断")

    _patch_writing_inputs(monkeypatch, Provider())
    try:
        with Session(engine) as db:
            profile = WritingDnaProfile(name="测试写作档案", book_ids_json="[]",
                                        status="ready", current_version=1, rights_acknowledged=1)
            db.add(profile)
            db.flush()
            db.add(WritingDnaRevision(profile_id=profile.id, version=1,
                                      language_dna="克制", structure_patterns="围绕问题",
                                      cognitive_framework="证据优先", visual_style_guide="两级标题",
                                      writing_dna="清晰"))
            db.commit()
            with pytest.raises(LLMOutputIncomplete, match="中断"):
                await writing_lab.imitate(db, profile.id, "议题", "评论", 900, "")
            assert db.scalar(select(func.count()).select_from(WritingOutput)) == 0
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_repeated_continuation_does_not_complete_partial_draft():
    class Provider:
        calls = 0

        async def stream_chat(self, messages):
            self.calls += 1
            yield "# 标题\n\n尚未完成的论证。"
            if self.calls == 1:
                raise LLMOutputIncomplete("length", "输出达到上限")

    with pytest.raises(RuntimeError, match="没有增加正文内容"):
        await writing_lab._call_long_writing(
            Provider(), [{"role": "user", "content": "写一篇论证"}], 1000
        )


@pytest.mark.asyncio
async def test_long_draft_with_normal_but_early_stop_is_continued():
    class Provider:
        calls = 0

        async def stream_chat(self, messages):
            self.calls += 1
            if self.calls == 1:
                yield "甲" * 3000
            else:
                assert "明显短于目标篇幅" in messages[-1]["content"]
                yield "乙" * 1000

    provider = Provider()
    text, segments = await writing_lab._call_long_writing(
        provider, [{"role": "user", "content": "写一篇长文"}], 6000
    )
    assert len(text) == 4000
    assert segments == provider.calls == 2


@pytest.mark.asyncio
async def test_long_draft_stays_unsaved_when_all_normal_stops_are_too_short():
    class Provider:
        async def stream_chat(self, messages):
            yield "短文。"

    with pytest.raises(RuntimeError, match="未达到目标篇幅"):
        await writing_lab._call_long_writing(
            Provider(), [{"role": "user", "content": "写一篇长文"}], 6000
        )
