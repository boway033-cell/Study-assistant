"""语义切块单元测试：页码映射、段落边界"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # noqa: E402


class TestSemanticChunker:
    def test_simple_chunking(self):
        from backend.app.services.rag.semantic_chunker import split_semantic_chunks

        pages = [
            "第一章 函数。\n函数是描述关系的概念。\n\n极限是趋近的值。",
            "第二章 导数。\n导数是变化率。\n\n微分是无穷小增量。",
        ]
        chunks = split_semantic_chunks(pages, [(1, 1, 2)])
        # 短文本合并为 1 块，页码映射正确
        assert len(chunks) >= 1
        assert all(1 <= c["page_start"] <= c["page_end"] <= 2 for c in chunks)

    def test_page_mapping(self):
        from backend.app.services.rag.semantic_chunker import split_semantic_chunks

        pages = ["页一内容" * 200, "页二内容" * 200]
        chunks = split_semantic_chunks(pages, [(1, 1, 2)])
        # 每块 page_start 在 1-2 之间
        for c in chunks:
            assert c["page_start"] in (1, 2)

    def test_chapter_boundary(self):
        from backend.app.services.rag.semantic_chunker import split_semantic_chunks

        pages = ["第一章内容" * 100, "第二章内容" * 100]
        # 两章
        chunks = split_semantic_chunks(pages, [(1, 1, 1), (2, 2, 2)])
        chapter_ids = {c["chapter_id"] for c in chunks}
        assert chapter_ids == {1, 2}

    def test_long_paragraph_split(self):
        from backend.app.services.rag.semantic_chunker import _split_paragraphs

        # 100 个句子约 1300 字 > MAX_SIZE 900 → 应切分为多段
        long_text = "。".join(["这是第%d个句子，内容足够长，包含足够的文字信息" % i for i in range(100)])
        paras = _split_paragraphs(long_text)
        assert len(paras) >= 2  # 长段落被切分
        assert all(len(p) <= 900 for p in paras)

    def test_llm_refine_fallback(self):
        """LLM 切分失败应回退原文。"""
        import asyncio
        from backend.app.services.rag.semantic_chunker import refine_boundaries_with_llm

        class FakeProvider:
            async def stream_chat(self, messages):
                yield "不是JSON"

        async def run():
            result = await refine_boundaries_with_llm(FakeProvider(), "测试文本" * 100)
            return result

        result = asyncio.run(run())
        assert len(result) == 1  # 回退原块


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
