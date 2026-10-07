"""The corrected chapter outline must reach the chapter analysis model."""

import asyncio

from backend.app.services import deep_analysis


def test_chapter_summary_uses_only_its_own_corrected_subheadings(monkeypatch):
    prompts = []

    async def capture(provider, messages, error_prefix, on_progress=None):
        prompts.append(messages[-1]["content"])
        return "有来源的分析"

    monkeypatch.setattr(deep_analysis, "_bounded_stream", capture)
    toc = [
        {"title": "第一章", "level": 1, "page": 1, "chapter_id": 1},
        {"title": "更正后的第一节", "level": 2, "page": 2, "chapter_id": 2},
        {"title": "第二章", "level": 1, "page": 8, "chapter_id": 3},
        {"title": "第二章的子节", "level": 2, "page": 9, "chapter_id": 4},
    ]

    result = asyncio.run(deep_analysis.summarize_by_toc(
        object(), "样本书", toc, {1: "第一章正文", 2: "第二章正文"}
    ))

    assert len(result) == 2
    assert "更正后的第一节" in prompts[0]
    assert "第二章的子节" not in prompts[0]
    assert "第二章的子节" in prompts[1]
    assert "更正后的第一节" not in prompts[1]
    assert "仅用于组织结构，论点仍须依据原文" in prompts[0]
