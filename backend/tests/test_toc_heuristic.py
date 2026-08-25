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

    def test_multilevel_headings_on_same_page(self):
        from backend.app.services.rag.toc_heuristic import extract_toc_heuristic

        pages = [
            "第一章 总论\n\n一、研究背景\n\n（一）问题提出\n\n正文内容。",
            "第二章 方法\n\n2.1 数据来源\n\n2.1.1 样本筛选\n\n正文内容。",
        ]
        toc = extract_toc_heuristic(pages)
        assert [x["level"] for x in toc] == [1, 3, 4, 1, 2, 3]
        assert toc[1]["title"] == "一、研究背景"
        assert toc[2]["title"] == "（一）问题提出"

    def test_whitespace_and_decimal_regex(self):
        from backend.app.services.rag.toc_heuristic import classify_heading

        assert classify_heading("第 2 节 研究设计")[1] == 2
        assert classify_heading("3.2 Results")[1] == 2

    def test_ocr_part_and_department_are_distinguished(self):
        from backend.app.services.rag.toc_heuristic import classify_heading

        assert classify_heading("第一部 分：基础知识") == ("第一部分 基础知识", 1)
        assert classify_heading("第三部 门是介于政府与市场之间的组织") is None
        # 纯数字前缀只在版面分析明确标成 title 时采用，不能把题库正文全升为目录。
        assert classify_heading("２文献综述") is None
        assert classify_heading("21 治理的定义") is None
        assert classify_heading("（2）118－128．") is None

    def test_plain_number_is_accepted_only_with_layout_title_evidence(self):
        from types import SimpleNamespace
        from backend.app.services.rag.toc_heuristic import extract_toc_from_layout

        def block(text, size, page=1):
            return SimpleNamespace(
                text=text, size=size, page=page, block_type="title"
            )

        layout = SimpleNamespace(
            body_size=10,
            pages=[[block("论文题名", 20), block("1问题提出", 12)],
                   [block("2文献综述", 12, 2), block("（2）118－128．", 12, 2)]],
        )
        toc = extract_toc_from_layout(layout)
        assert [(x["title"], x["level"]) for x in toc] == [
            ("论文题名", 1), ("1 问题提出", 2), ("2 文献综述", 2)
        ]

    def test_repeated_numbered_running_header_is_not_a_new_chapter(self):
        from backend.app.services.rag.toc_heuristic import extract_toc_heuristic

        pages = [
            "第一章 导论\n1.1 研究对象",
            "第一章 导论\n正文",
            "第二章 方法\n2.1 数据来源",
        ]
        toc = extract_toc_heuristic(pages, min_pages=1)
        assert [x["title"] for x in toc].count("第一章 导论") == 1

    def test_decimal_measure_is_not_a_heading(self):
        from backend.app.services.rag.toc_heuristic import classify_heading

        assert classify_heading("24.6 亿人次，分别是此前的两倍") is None

    def test_chinese_textbook_spacing_and_wrapped_titles(self):
        from backend.app.services.rag.toc_heuristic import extract_toc_heuristic

        pages = [
            "第一章　行政和行政管理学\n第一节　行政管理学的对象、\n内容和特点\n一、什么是行政",
            "第 三 节 建 设 有 中 国 特 色 社 会\n主义的行政管理学\n"
            "一、建设有中国特色社会主义行政管理学的深厚基础\n（一）行政管理的二重性",
        ]
        toc = extract_toc_heuristic(pages, min_pages=1)
        assert [(x["title"], x["level"]) for x in toc] == [
            ("第一章 行政和行政管理学", 1),
            ("第一节 行政管理学的对象、内容和特点", 2),
            ("一、什么是行政", 3),
            ("第三节 建设有中国特色社会主义的行政管理学", 2),
            ("一、建设有中国特色社会主义行政管理学的深厚基础", 3),
            ("（一）行政管理的二重性", 4),
        ]

    def test_semantic_levels_override_flat_bookmarks_and_drop_toc_root(self):
        from backend.app.services.rag.toc_heuristic import merge_toc_sources

        bookmarks = [
            {"title": "目 录", "level": 1, "page": 1},
            {"title": "第一章 行政和行政管理学", "level": 2, "page": 1},
            {"title": "第一节 对象和特点", "level": 2, "page": 1},
        ]
        merged = merge_toc_sources(bookmarks)
        assert [x["level"] for x in merged] == [1, 2]
        assert all("目录" not in x["title"].replace(" ", "") for x in merged)

    def test_verify_chinese_number_continuity_within_parent(self):
        from backend.app.services.deep_analysis import verify_toc

        toc = [
            {"title": "第一章 总论", "level": 1, "page": 1},
            {"title": "第一节 概念", "level": 2, "page": 1},
            {"title": "一、起源", "level": 3, "page": 1},
            {"title": "三、发展", "level": 3, "page": 2},
        ]
        audit = verify_toc(toc)
        assert any(i["type"] == "missing_chinese_sequence" and i["level"] == 3 for i in audit["issues"])

    def test_number_logic_infers_contextual_chinese_levels_and_gap(self):
        from backend.app.services.rag.toc_logic import analyze_toc_rows, auto_repair_toc_rows

        rows = [
            {"title": "第一章 绪论", "level": 1, "page": 1},
            {"title": "一、行政管理的含义", "level": 3, "page": 2},
            {"title": "（一）公共行政", "level": 4, "page": 2},
            {"title": "三、行政管理的发展", "level": 3, "page": 4},
            {"title": "第二章 理论", "level": 1, "page": 8},
        ]
        audit = analyze_toc_rows(rows)
        assert audit["items"][1]["inferred_level"] == 2
        assert audit["items"][2]["inferred_level"] == 3
        assert any(issue["type"] == "sequence_gap" and issue["missing"] == [2]
                   for issue in audit["issues"])
        repaired, _ = auto_repair_toc_rows(rows)
        assert len(repaired) == len(rows)  # 不凭空创建“二、”
        assert [row["level"] for row in repaired[:3]] == [1, 2, 3]

    def test_same_page_misordered_child_continues_previous_parent(self):
        from backend.app.services.rag.toc_logic import analyze_toc_rows, auto_repair_toc_rows

        rows = [
            {"title": "第一节 结构", "level": 2, "page": 10},
            {"title": "二、纵向结构", "level": 3, "page": 11},
            {"title": "（一）中央", "level": 4, "page": 11},
            {"title": "（二）地方", "level": 4, "page": 12},
            {"title": "三、编制管理", "level": 3, "page": 12},
            {"title": "（三）其他方面", "level": 4, "page": 12},
            {"title": "（一）编制含义", "level": 4, "page": 12},
        ]
        audit = analyze_toc_rows(rows)
        misplaced = audit["items"][5]
        assert misplaced["inferred_parent_index"] == 1
        assert misplaced["repairs"]["move_before_index"] == 4
        repaired, _ = auto_repair_toc_rows(rows)
        assert [row["title"] for row in repaired][3:6] == [
            "（二）地方", "（三）其他方面", "三、编制管理",
        ]

    def test_user_toc_replace_preserves_ids_and_rebuilds_mapping(self):
        from sqlalchemy import select

        from backend.app.api.books import delete_book
        from backend.app.core.database import SessionLocal
        from backend.app.models import Book, Chapter, Chunk, TocRevision
        from backend.app.services.rag.toc_editor import replace_book_toc, review_chapters

        db = SessionLocal()
        try:
            book = Book(title="TOC edit", file_path="missing.pdf", file_type="pdf",
                        status="ready", total_pages=10)
            db.add(book); db.flush()
            root = Chapter(book_id=book.id, title="第一章 绪论", level=1,
                           order_index=0, start_page=1, end_page=10)
            wrong = Chapter(book_id=book.id, title="一、概念", level=3,
                            order_index=1, start_page=2, end_page=10)
            db.add_all([root, wrong]); db.flush(); wrong.parent_id = root.id
            chunk = Chunk(book_id=book.id, chapter_id=wrong.id, content="正文",
                          chunk_index=0, page_start=2, page_end=2)
            db.add(chunk); db.commit()

            audit = review_chapters([root, wrong])
            assert audit["summary"]["safe_repairs"] == 1
            result = replace_book_toc(db, book, [
                {"client_key": f"id:{root.id}", "id": root.id, "parent_key": None,
                 "title": root.title, "level": 1, "start_page": 1},
                {"client_key": f"id:{wrong.id}", "id": wrong.id,
                 "parent_key": f"id:{root.id}", "title": wrong.title, "level": 2, "start_page": 2},
                {"client_key": "new:1", "id": None, "parent_key": f"id:{root.id}",
                 "title": "二、发展", "level": 2, "start_page": 5},
            ], "user", "test")
            assert result["audit"]["ok"] is True
            assert db.get(Chapter, wrong.id).level == 2
            assert db.get(Chunk, chunk.id).chapter_id == wrong.id
            assert db.scalar(select(TocRevision).where(TocRevision.book_id == book.id)) is not None
            delete_book(book.id, db)
        finally:
            db.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
