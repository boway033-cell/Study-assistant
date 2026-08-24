"""轻量文档解析链回归测试：结构证据、按页 OCR 与目录证据约束。"""
from pathlib import Path


def test_weak_page_detection_is_selective():
    from backend.app.services.parser.ocr import pages_requiring_ocr

    pages = ["这是正常文本页" * 8, "", "短", "这是另一张正常文本页" * 8]
    assert pages_requiring_ocr(pages, threshold=20) == [2, 3]


def test_rapidocr_engine_can_be_released(monkeypatch):
    from backend.app.services.parser import ocr

    sentinel = object()
    monkeypatch.setattr(ocr, "_rapid_engine", sentinel)
    ocr.release_ocr_engine()
    assert ocr._rapid_engine is None


def test_cached_ocr_pages_are_removed_before_render(tmp_path: Path):
    from backend.app.services.parser.ocr import _initial_pages, _prepare_cached_targets

    (tmp_path / "page_0002.txt").write_text("缓存识别结果", encoding="utf-8")
    pages = _initial_pages(3, ["文本层第一页", "", "文本层第三页"])
    progress: list[tuple[int, bool]] = []
    missing = _prepare_cached_targets(
        3,
        pages,
        tmp_path,
        {2, 3},
        lambda page, total, cached: progress.append((page, cached)),
    )

    assert missing == {3}
    assert pages == ["文本层第一页", "缓存识别结果", "文本层第三页"]
    assert progress == [(2, True)]


def test_pdf_renderer_is_lazy_and_page_selective(tmp_path: Path):
    import fitz
    from backend.app.services.parser.ocr import _iter_pdf_page_images

    pdf = tmp_path / "three-pages.pdf"
    doc = fitz.open()
    for number in range(3):
        page = doc.new_page(width=120, height=120)
        page.insert_text((12, 30), f"page {number + 1}")
    doc.save(pdf)
    doc.close()

    rendered = _iter_pdf_page_images(pdf, {2}, dpi=72)
    assert not isinstance(rendered, list)
    page_no, total, image = next(rendered)
    assert (page_no, total) == (2, 3)
    assert image.size == (120, 120)
    rendered.close()


def test_structured_markdown_restores_paragraphs_and_headings():
    from backend.app.services.parser.structured import (
        DocumentBlock,
        StructuredDocument,
        StructuredPage,
        structured_to_markdown,
    )

    document = StructuredDocument(pages=[
        StructuredPage(1, 600, 800, [
            DocumentBlock(1, "第一章 行政管理概述", role="title"),
            DocumentBlock(1, "行政管理是国家治理体系的", lines=["行政管理是国家治理体系的"]),
        ]),
        StructuredPage(2, 600, 800, [
            DocumentBlock(2, "重要组成部分。", lines=["重要组成部分。"]),
            DocumentBlock(2, "（一）基本概念", role="title"),
        ]),
    ])

    markdown = structured_to_markdown(document)
    assert "# 第一章 行政管理概述" in markdown
    assert "行政管理是国家治理体系的重要组成部分。" in markdown
    assert "#### （一）基本概念" in markdown
    assert "体系的\n\n重要" not in markdown


def test_toc_evidence_rejects_invented_ai_title():
    from backend.app.services.rag.toc_evidence import (
        build_candidate_tree,
        score_toc_rows,
        validate_ai_review,
    )

    rows = [
        {"title": "第一章 绪论", "page": 1, "level": 1, "source_priority": 0},
        {"title": "（一）研究背景", "page": 2, "level": 3, "source_priority": 1},
    ]
    assert len(score_toc_rows(rows)) == 2
    tree = build_candidate_tree(rows)
    assert tree[0]["children"][0]["level"] == 2

    reviewed = [
        {"candidate_id": 1, "title": "（一）模型凭空新增标题", "level": 2},
        {"candidate_id": 1, "title": "（一）研究背景", "level": 2},
    ]
    assert validate_ai_review(rows, reviewed) == [{
        "title": "（一）研究背景", "page": 2, "level": 2, "candidate_id": 1,
    }]


def test_pp_doclayout_only_changes_roles_not_text():
    from backend.app.services.analyzer.pp_doclayout import apply_layout_roles
    from backend.app.services.parser.structured import (
        DocumentBlock,
        StructuredDocument,
        StructuredPage,
    )

    document = StructuredDocument(pages=[StructuredPage(1, 600, 800, [
        DocumentBlock(1, "第一章 原文标题", bbox=(40, 50, 300, 90)),
    ])])
    payload = [{"res": {
        "page_index": 0,
        "boxes": [{
            "label": "paragraph_title",
            "coordinate": [35, 45, 310, 95],
            "block_content": "模型改写的标题不应采用",
        }],
    }}]

    apply_layout_roles(document, payload)
    block = document.pages[0].blocks[0]
    assert block.role == "title"
    assert block.text == "第一章 原文标题"
    assert block.source.endswith("+pp-doclayout-m")


def test_pp_doclayout_command_uses_single_module_worker(tmp_path, monkeypatch):
    import sys
    from backend.app.services.analyzer.pp_doclayout import build_command

    monkeypatch.setenv("PP_DOCLAYOUT_PYTHON", sys.executable)
    command = build_command(tmp_path / "sample.pdf", tmp_path / "output")
    joined = " ".join(command)
    assert "pp_doclayout_worker.py" in joined
    assert "PP-DocLayout-M" in joined
    assert "pp_structurev3" not in joined.lower()


def test_deep_analysis_ai_must_reference_original_candidate():
    import asyncio
    from backend.app.services.deep_analysis import complete_with_ai

    class FakeProvider:
        async def stream_chat(self, _prompt):
            yield (
                '[{"candidate_id":0,"title":"第一章 模型编造标题","level":1},'
                '{"candidate_id":1,"title":"（一）研究背景","level":2}]'
            )

    pages = ["第一章 绪论\n正文。\n（一）研究背景\n具体内容。"]
    result = asyncio.run(complete_with_ai(
        FakeProvider(),
        [{"title": "第一章 绪论", "level": 1, "page": 1}],
        [{"type": "flat_structure", "ref": "缺少分标题", "level": 2}],
        pages,
    ))
    titles = [item["title"] for item in result]
    assert "第一章 模型编造标题" not in titles
    assert "（一）研究背景" in titles
