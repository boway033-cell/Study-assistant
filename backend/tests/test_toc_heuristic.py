"""启发式章节提取测试"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # noqa: E402


class TestTocHeuristic:
    def test_extract_chapters(self):
        from backend.app.services.rag.toc_heuristic import extract_toc_heuristic

        pages = [
            "封面内容",
            "第1章公共管理导论\n正文内容…",
            "第2章公共管理者的角色\n正文…",
            "第3章公共部门\n正文…",
            "第4章绩效管理\n正文…",
        ]
        toc = extract_toc_heuristic(pages)
        assert len(toc) >= 4
        assert toc[0]["title"].startswith("第1章")
        assert toc[0]["level"] == 1
        assert toc[0]["page"] == 2

    def test_insufficient_chapters(self):
        from backend.app.services.rag.toc_heuristic import extract_toc_heuristic

        # 只有 1 章 → 视为噪声
        pages = ["第1章导论\n正文", "正文没有标题"]
        assert extract_toc_heuristic(pages) == []

    def test_sections(self):
        from backend.app.services.rag.toc_heuristic import extract_toc_heuristic

        pages = [
            "第1章 总论\n正文",
            "第1节 概念\n正文",
            "第2节 分类\n正文",
            "第2章 方法\n正文",
        ]
        toc = extract_toc_heuristic(pages)
        levels = {t["level"] for t in toc}
        assert 1 in levels and 2 in levels


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
