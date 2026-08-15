"""后端单元测试：解析、分词、FSRS、FTS 检索

运行：.venv\\Scripts\\python.exe -m pytest backend/tests/test_unit.py -q
"""
import os
import sys
from pathlib import Path

# 确保可以 import backend 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # noqa: E402


# ---------- 解析 ----------
class TestParser:
    def test_build_chapters_with_toc(self):
        from backend.app.services.parser import TocItem
        from backend.app.services.rag.chunker import build_chapters

        toc = [
            TocItem("第一章", 1, 1),
            TocItem("1.1 节", 2, 2),
            TocItem("1.2 节", 2, 5),
            TocItem("第二章", 1, 10),
        ]
        chapters = build_chapters(toc, total_pages=20)
        assert len(chapters) == 4
        # 第二章 parent_id 为 None，1.1 的 parent 是第一章
        ch1 = chapters[0]
        ch11 = chapters[1]
        assert ch1["parent_id"] is None
        assert ch11["parent_id"] == ch1["order_index"]
        # end_page：第一章到第二章前
        assert chapters[0]["end_page"] == 9
        assert chapters[-1]["end_page"] == 20

    def test_build_chapters_empty_toc(self):
        from backend.app.services.rag.chunker import build_chapters

        chapters = build_chapters([], total_pages=100)
        assert len(chapters) == 1
        assert chapters[0]["start_page"] == 1
        assert chapters[0]["end_page"] == 100

    def test_split_pages_into_chunks(self):
        from backend.app.services.rag.chunker import split_pages_into_chunks

        # 两页 300 字 + 分隔符 = 602 字 → 主块 600 字 + 尾部碎片(82字,与主块重叠80字)被跳过 → 1 块
        pages = ["a" * 300, "b" * 300]
        chunks = split_pages_into_chunks(pages, [(1, 1, 2)])
        assert len(chunks) == 1
        assert chunks[0]["page_start"] == 1
        assert chunks[0]["chapter_id"] == 1
        assert len(chunks[0]["content"]) == 600

    def test_split_long_text_multiple_chunks(self):
        from backend.app.services.rag.chunker import split_pages_into_chunks

        # 单页 2000 字 → 600 字主块 + 后续块（每步 520 前进）
        pages = ["a" * 2000]
        chunks = split_pages_into_chunks(pages, [(1, 1, 1)])
        # 2000 = 0..600, 520..1120, 1040..1640, 1560..2000(440字, ≥260 保留) → 4 块
        assert len(chunks) == 4
        assert all(c["word_count"] <= 600 for c in chunks)

    def test_split_single_chunk_exact_size(self):
        from backend.app.services.rag.chunker import split_pages_into_chunks

        # 单页恰好 600 字 → 1 个 chunk
        pages = ["a" * 600]
        chunks = split_pages_into_chunks(pages, [(1, 1, 1)])
        assert len(chunks) == 1
        assert len(chunks[0]["content"]) == 600


# ---------- 分词 ----------
class TestTokenizer:
    def test_tokenize_chinese(self):
        from backend.app.services.rag.chunker import tokenize

        result = tokenize("拉格朗日中值定理")
        # 分词后应包含空格（多词）
        assert " " in result

    def test_tokenize_query_or_join(self):
        from backend.app.services.rag.chunker import tokenize_query

        expr = tokenize_query("拉格朗日")
        assert '"' in expr
        assert " OR " in expr

    def test_tokenize_query_empty(self):
        from backend.app.services.rag.chunker import tokenize_query

        assert tokenize_query("!!!") == ""


# ---------- FSRS ----------
class TestFSRS:
    def _make_card(self):
        from datetime import datetime
        from backend.app.models import Card

        return Card(
            id=1, book_id=1, front="q", back="a", state="New",
            due=datetime.now(), stability=0, difficulty=0,
        )

    def test_review_good_increases_stability(self):
        from backend.app.services.srs.fsrs_service import review_card

        card = self._make_card()
        s1 = review_card(card, "good")
        assert s1["state"] in ("Learning", "Review")
        assert card.stability > 0
        assert card.reps == 1

    def test_review_again_increases_lapses(self):
        from backend.app.services.srs.fsrs_service import review_card

        card = self._make_card()
        review_card(card, "again")
        assert card.lapses == 1
        assert card.reps == 1

    def test_invalid_rating(self):
        from backend.app.services.srs.fsrs_service import review_card

        card = self._make_card()
        with pytest.raises(ValueError):
            review_card(card, "unknown")


# ---------- FTS 检索 ----------
class TestFTS:
    def test_tokenize_query_safe_chars(self):
        from backend.app.services.rag.chunker import tokenize_query

        # 特殊字符应被过滤，不产生非法 MATCH 表达式
        expr = tokenize_query("概率与统计 2024!")
        assert not any(c in expr for c in ("!", ",", "."))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
