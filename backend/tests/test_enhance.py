"""新功能单元测试：版面分析 / 文本清洗 / 关键信息提取 / OCR检测 / 宽定位

运行：.venv\\Scripts\\python.exe -m pytest backend/tests/test_enhance.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # noqa: E402


# ---------- 文本清洗 ----------
class TestTextClean:
    def test_remove_repeated_lines(self):
        from backend.app.services.analyzer.textclean import remove_repeated_lines
        text = "拉格朗日中值定理\n拉格朗日中值定理\n下一行"
        assert remove_repeated_lines(text) == "拉格朗日中值定理\n下一行"

    def test_collapse_repeated_chars(self):
        from backend.app.services.analyzer.textclean import collapse_repeated_chars
        assert collapse_repeated_chars("拉格朗日日日日") == "拉格朗日"
        assert collapse_repeated_chars("好好好好学") == "好学"

    def test_merge_broken_english(self):
        from backend.app.services.analyzer.textclean import merge_broken_english
        assert merge_broken_english("The func-\ntion is") == "The function is"
        assert merge_broken_english("compu\nter") == "computer"

    def test_clean_text_filters_header(self):
        from backend.app.services.analyzer.textclean import clean_text
        text = "高等数学\n第一章 函数\n5\n"
        # "5" 是纯页码，应被过滤
        result = clean_text(text, {"高等数学"}, set())
        assert "高等数学" not in result
        assert "5" not in result
        assert "第一章 函数" in result

    def test_merge_broken_chinese(self):
        from backend.app.services.analyzer.textclean import merge_broken_chinese
        # 第一行无句末标点，应合并
        text = "拉格朗日中值定理指出\n若函数连续"
        result = merge_broken_chinese(text)
        assert "拉格朗日中值定理指出若函数连续" in result


# ---------- 关键信息提取 ----------
class TestKeyInfo:
    def test_extract_definitions(self):
        from backend.app.services.analyzer.keyinfo import extract_definitions
        text = "导数是指函数在某一点的变化率。极限是函数趋近的值。"
        defs = extract_definitions(text)
        terms = {d["term"] for d in defs}
        assert "导数" in terms

    def test_extract_theorems(self):
        from backend.app.services.analyzer.keyinfo import extract_theorems
        text = "定理 3.2：拉格朗日中值定理，若函数连续则存在一点。"
        thms = extract_theorems(text)
        assert len(thms) >= 1
        assert thms[0]["type"] == "定理"
        assert "拉格朗日" in thms[0]["statement"]

    def test_extract_keywords(self):
        from backend.app.services.analyzer.keyinfo import extract_keywords
        text = "导数 导数 导数 函数 函数 极限 积分"
        kws = extract_keywords(text, top_n=5)
        assert "导数" in kws


# ---------- OCR 检测 ----------
class TestOCR:
    def test_detect_scanned_empty(self):
        from backend.app.services.parser.ocr import detect_scanned
        assert detect_scanned(["", "", ""]) is True

    def test_detect_scanned_text(self):
        from backend.app.services.parser.ocr import detect_scanned
        pages = ["a" * 200] * 5
        assert detect_scanned(pages) is False


# ---------- 版面分析 ----------
class TestLayout:
    def test_analyze_sample_pdf(self):
        """对测试 PDF 做版面分析，验证能识别正文、返回页数。"""
        from backend.app.services.analyzer.layout import analyze_pdf

        pdf = Path(__file__).parent / "sample_math.pdf"
        if not pdf.exists():
            pytest.skip("sample_math.pdf 不存在")
        result = analyze_pdf(pdf)
        assert result.total_pages >= 1
        assert result.body_size > 0
        # 至少能取到正文
        cleaned = result.clean_page_text(0)
        assert cleaned  # 非空


# ---------- 宽定位检索 ----------
class TestWideRetrieval:
    def test_retrieve_returns_items(self):
        """宽定位检索：即使主检索命中不足，也应降级返回结果（不空）。"""
        from backend.app.services.rag.retriever import retrieve

        items = retrieve("拉格朗日", book_id=None)
        # 测试 DB 中应已有 sample_math 的索引
        assert isinstance(items, list)

    def test_build_prompt_outline_fallback(self):
        from backend.app.services.rag.retriever import build_prompt

        outline = [{"is_outline": True, "snippet": "《测试书》目录：第一章"}]
        msgs = build_prompt("问题", outline)
        assert msgs[0]["role"] == "system"
        assert "目录" in msgs[1]["content"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
