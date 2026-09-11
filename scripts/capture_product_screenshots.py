"""Capture release screenshots from an isolated, synthetic knowledge base.

The script never opens the user's normal ``backend/data`` directory. It seeds a
temporary SQLite database, starts the already-built app on a dedicated port,
and replaces the README screenshots only after every capture succeeds.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FORBIDDEN_TERMS = ("\u6c34\u53e3\u4e61",)
SMOKE_ROUTES = (
    "/library",
    "/literature-workbench",
    "/chat",
    "/knowledge-hub?view=notes",
    "/knowledge",
    "/graph",
    "/study",
    "/writing",
    "/knowledge-health",
    "/quiz",
    "/draw",
    "/stats",
    "/settings",
)
DEMO_TITLES = [
    "城市热岛缓解策略的跨城市比较",
    "湿地生态系统碳汇的长期观测",
    "叙事视角与记忆书写：三部虚构小说的比较",
    "基层数字服务中的组织协同",
    "蛋白质折叠动力学的成像研究",
    "可解释机器学习在生态监测中的应用",
    "公共空间活力与步行网络",
    "区域创新网络中的知识扩散",
    "气候适应政策的实施条件",
    "珊瑚礁恢复的多尺度证据",
    "小说时间结构与读者认知",
    "地方档案中的日常生活史",
    "社区协商与公共问题形成",
    "遥感数据中的物种识别",
    "河流生态修复的长期效应",
    "开放科学实践与结果复现",
    "数字人文中的文本比较方法",
    "组织韧性与跨部门协作",
    "环境暴露与健康风险沟通",
    "科学模型的解释边界",
]


def create_demo_pdf(path: Path) -> None:
    import pymupdf as fitz

    font_candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    font_path = next((item for item in font_candidates if item.exists()), None)
    document = fitz.open()
    sections = [
        ("研究问题与比较框架", "城市热岛研究如何比较不同城市、不同尺度与不同时间窗口下的缓解效果？"),
        ("材料与方法", "演示材料结合温度观测、空间形态与政策文本。三类证据回答不同问题，不能直接相互替代。"),
        ("条件性共识", "多份材料都提示绿地连续性和遮阴结构与降温相关，但效应大小取决于观测尺度。"),
        ("解释冲突", "关于主要驱动因素的排序仍不一致。建筑密度、风环境与活动模式可能构成竞争解释。"),
        ("互补证据", "长期观测提供变化方向，案例材料解释实施条件。二者共同限定结论的适用范围。"),
        ("结论与待核查问题", "现有证据支持条件性判断，而非无边界外推。下一步应统一测量口径并扩大观察窗口。"),
    ]
    for page_number, (heading, body) in enumerate(sections, start=1):
        page = document.new_page(width=595, height=842)
        font_name = "demo-cjk" if font_path else "china-s"
        if font_path:
            page.insert_font(fontname=font_name, fontfile=str(font_path))
        page.insert_text((64, 68), "PERSONAL KNOWLEDGE BASE · DEMO", fontsize=9, color=(0.45, 0.32, 0.2))
        page.insert_textbox(
            fitz.Rect(64, 105, 530, 160),
            "城市热岛缓解策略的跨城市比较",
            fontname=font_name,
            fontsize=20,
            color=(0.12, 0.16, 0.16),
        )
        page.draw_line((64, 172), (530, 172), color=(0.72, 0.58, 0.42), width=0.8)
        page.insert_textbox(
            fitz.Rect(64, 205, 530, 265),
            f"{page_number}. {heading}",
            fontname=font_name,
            fontsize=15,
            color=(0.22, 0.25, 0.23),
        )
        page.insert_textbox(
            fitz.Rect(64, 280, 530, 610),
            body + "\n\n本文件仅用于 Study Assistant v2.3.0 界面演示，不对应真实论文、作者或研究项目。",
            fontname=font_name,
            fontsize=12,
            lineheight=1.7,
            color=(0.22, 0.22, 0.2),
        )
        page.insert_text((470, 790), f"{page_number} / {len(sections)}", fontname=font_name, fontsize=9, color=(0.5, 0.5, 0.48))
    document.save(path)
    document.close()


def seed_demo(data_dir: Path) -> list[int]:
    os.environ["DATA_DIR"] = str(data_dir)
    from backend.app.core.config import settings
    from backend.app.core.database import Base, SessionLocal, engine
    from backend.app.models import (
        Book,
        Chapter,
        Chunk,
        EvidenceCard,
        KnowledgeNote,
        PaperProfile,
        Shelf,
        StudyReport,
        WritingDnaProfile,
        WritingDnaRevision,
        WritingOutput,
        shelf_books,
    )

    Base.metadata.create_all(bind=engine)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    source_pdf = settings.uploads_dir / "demo-paper.pdf"
    create_demo_pdf(source_pdf)
    db = SessionLocal()
    book_ids: list[int] = []
    chapters: list[Chapter] = []
    try:
        now = datetime.now()
        for index, title in enumerate(DEMO_TITLES):
            needs_attention = index == len(DEMO_TITLES) - 1
            book = Book(
                title=title,
                file_path="demo-paper.pdf",
                file_type="pdf",
                file_size=source_pdf.stat().st_size,
                file_hash=f"demo-release-2-{index:02d}",
                total_pages=6,
                status="needs_ocr" if needs_attention else "ready",
                error_msg="示例：第 2 页文字层质量较低，等待 OCR 复核" if needs_attention else None,
                category=("社会科学", "自然与生物", "人文文学")[index % 3],
                library_order=index,
            )
            db.add(book)
            db.flush()
            book_ids.append(book.id)
            if needs_attention:
                continue
            chapter = Chapter(
                book_id=book.id,
                title=("研究问题与理论框架", "材料、方法与证据", "比较结果与解释边界")[index % 3],
                level=1,
                order_index=0,
                start_page=1,
                end_page=6,
            )
            chapters.append(chapter)
            db.add(chapter)
            db.flush()
            db.add(Chunk(
                book_id=book.id,
                chapter_id=chapter.id,
                content=(
                    f"《{title}》的虚构演示文本用于验证个人知识库的解析、检索与来源回链。"
                    "材料分别说明研究设计、观察证据、可能机制与适用边界，不对应任何真实作者或项目。"
                ) * 8,
                page_start=1,
                page_end=2,
                chunk_index=0,
            ))
            db.add(PaperProfile(
                book_id=book.id,
                authors=("林青", "周岚", "陈澈")[index % 3],
                journal="演示研究辑刊",
                published_year=2025 - index % 6,
                publication_status="published",
                visibility="private",
                demo_allowed=1,
                metadata_confidence=0.95,
                reading_status=("reading", "read", "unread")[index % 3],
                favorite=1 if index in {0, 2, 4} else 0,
                progress_page=1 + index % 2,
                last_read_at=now - timedelta(days=index),
            ))

        methods = Shelf(name="研究方法", description="演示书架", color="#8B5A2B", order_index=0)
        themes = Shelf(name="跨学科主题", description="演示书架", color="#476C5E", order_index=1)
        db.add_all([methods, themes])
        db.flush()
        for index, book_id in enumerate(book_ids[:8]):
            db.execute(shelf_books.insert().values(
                shelf_id=methods.id if index < 4 else themes.id,
                book_id=book_id,
                order_index=index % 4,
            ))

        selected = book_ids[:3]
        for index, (book_id, chapter) in enumerate(zip(selected, chapters[:3], strict=True)):
            db.add(KnowledgeNote(
                book_id=book_id,
                chapter_id=chapter.id,
                page=1,
                title=("机制条件记录", "长期证据边界", "叙事比较备忘")[index],
                content=(
                    "不同材料在核心方向上形成部分共识，但机制解释依赖研究尺度与观察窗口。"
                    "这条演示笔记保留原页入口，后续仍需结合反例核对。"
                ),
                source_refs_json=json.dumps([f"B{book_id}:CH{chapter.id}:P1"], ensure_ascii=False),
                tags_json=json.dumps(["演示", "待综合"], ensure_ascii=False),
                origin="user",
            ))
            db.add(EvidenceCard(
                book_id=book_id,
                chapter_id=chapter.id,
                page=1,
                title=("条件性共识", "方法互补", "解释冲突")[index],
                evidence_text="虚构样例中的观察结果支持条件性判断，并明确限制在当前材料范围。",
                claim_text="研究尺度会改变机制解释的权重。",
                source_ref_json=json.dumps({"refs": [f"B{book_id}:CH{chapter.id}:P1"]}, ensure_ascii=False),
                tags_json=json.dumps(["演示证据"], ensure_ascii=False),
                origin="user",
                verification_status="verified" if index < 2 else "needs_review",
            ))

        claims = [
            {
                "claim": "三份材料都把情境条件视为解释差异的必要部分。",
                "claim_type": "interpretive",
                "source_refs": [f"B{selected[0]}:CH{chapters[0].id}:P1", f"B{selected[1]}:CH{chapters[1].id}:P1"],
                "status": "supported",
                "confidence": "high",
                "reason": "两个独立材料在不同方法下得到方向一致的条件性结论。",
                "counterpoint": "样例尚未覆盖更长时间尺度。",
                "synthesis_relation": "consensus",
                "evidence_quality": "moderate",
                "bias_flags": ["观察窗口有限"],
                "alternative_explanations": ["样本选择可能放大一致性"],
                "human_review_required": True,
            },
            {
                "claim": "定量观察与文本解释提供的是互补证据，而不是相互替代。",
                "claim_type": "interpretive",
                "source_refs": [f"B{selected[1]}:CH{chapters[1].id}:P1", f"B{selected[2]}:CH{chapters[2].id}:P1"],
                "status": "partial",
                "confidence": "medium",
                "reason": "两类材料回答的问题层次不同，可共同限定机制边界。",
                "counterpoint": "跨学科概念仍需人工对齐。",
                "synthesis_relation": "complementary",
                "evidence_quality": "moderate",
                "bias_flags": [],
                "alternative_explanations": ["术语相似不一定代表同一机制"],
                "human_review_required": True,
            },
            {
                "claim": "关于主要驱动因素，材料之间仍存在未解决的解释冲突。",
                "claim_type": "causal",
                "source_refs": [f"B{selected[0]}:CH{chapters[0].id}:P1", f"B{selected[2]}:CH{chapters[2].id}:P1"],
                "status": "needs_review",
                "confidence": "low",
                "reason": "证据设计不足以排除竞争解释。",
                "counterpoint": "需要统一尺度的追加材料。",
                "synthesis_relation": "conflict",
                "evidence_quality": "low",
                "bias_flags": ["方法异质性"],
                "alternative_explanations": ["研究对象差异", "测量口径差异"],
                "human_review_required": True,
            },
        ]
        selection = {
            "chapter_ids": [],
            "note_ids": [],
            "research_mode": "critical",
            "reasoning_depth": "deep",
            "writing_style": "analytical_essay",
            "extension_level": "exploratory",
            "target_length": 3200,
            "research_plan": {
                "material_type": "跨学科比较材料",
                "subquestions": ["哪些结论构成共识？", "冲突来自证据还是概念？"],
                "analysis_axes": ["研究尺度", "证据类型", "适用边界"],
                "evidence_needs": ["独立来源", "反例", "长期观察"],
                "mode": "critical",
            },
            "open_questions": ["扩大观察窗口后，当前共识是否仍然成立？"],
            "hypotheses": [],
            "evidence_summary": {
                "relations": {"consensus": 1, "complementary": 1, "conflict": 1, "single_source": 0, "unresolved": 0},
                "qualities": {"high": 0, "moderate": 2, "low": 1, "very_low": 0, "not_assessed": 0},
                "human_review_required": 3,
            },
        }
        db.add(StudyReport(
            book_ids_json=json.dumps(selected),
            selection_json=json.dumps(selection, ensure_ascii=False),
            focus="不同学科材料如何形成共识、冲突与互补证据？",
            framework="比较研究尺度、证据类型、竞争解释与适用边界",
            claims_json=json.dumps(claims, ensure_ascii=False),
            content=(
                "# 条件性共识与解释分歧\n\n"
                "三份材料并不提供一个可以直接合并的答案。它们首先形成一项条件性共识：研究尺度会改变机制的可见程度。"
                "定量观察说明变化的方向，文本解释补足行动者如何理解变化；两者构成互补，而非彼此替代。\n\n"
                "## 冲突从哪里产生\n\n"
                "现有分歧主要集中在驱动因素的优先级。材料采用不同观察窗口和概念口径，因此更稳妥的结论是保留竞争解释，"
                "并把进一步比较所需的数据条件写清楚。"
            ),
        ))

        manifest = [{"book_id": book_id, "title": DEMO_TITLES[index], "file_type": "pdf", "chars": 18000}
                    for index, book_id in enumerate(book_ids)]
        profile = WritingDnaProfile(
            name="跨学科研究写作",
            target_author="个人演示语料",
            book_ids_json=json.dumps(book_ids),
            corpus_manifest_json=json.dumps(manifest, ensure_ascii=False),
            status="ready",
            current_version=2,
            rights_acknowledged=1,
            feedback="减少模板化转折，优先用证据关系推动段落。",
        )
        db.add(profile)
        db.flush()
        db.add(WritingDnaRevision(
            profile_id=profile.id,
            version=2,
            language_dna="## 语言原则\n\n句子直接承担判断；修饰词只在改变证据强度时保留。",
            logic_dna="## 论证与衔接\n\n先给出主张，再说明证据及推理过程。段落围绕同一问题递进，比较不同研究的解释条件，用反例检验结论。",
            structure_patterns="## 结构原则\n\n问题—证据关系—竞争解释—边界—下一步。",
            cognitive_framework="## 推理原则\n\n区分共识、冲突与互补证据，不把相关关系写成因果。",
            visual_style_guide="## 视觉原则\n\n长文保持清楚层级，表格只用于真正的横向比较。",
            writing_dna="## 整合 Writing DNA\n\n从具体问题进入，用证据关系组织段落；允许基于材料进行解释性延伸，但明确推断与事实的边界。",
            quality_json=json.dumps({
                "article_count": 20,
                "structure_type_count": 4,
                "cognitive_claim_count": 12,
                "visual_sample_count": 6,
                "limitations": ["演示语料仅用于界面展示"],
            }, ensure_ascii=False),
        ))
        db.add(WritingOutput(
            profile_id=profile.id,
            kind="literature_review",
            title="跨学科证据如何形成对话",
            input_type="text",
            output_text=("# 跨学科证据如何形成对话\n\n> 以下为虚构演示稿，仅展示阅读与编辑界面。\n\n"
                "## 从共同问题建立比较\n\n"
                "三组材料从城市环境、生态观察与叙事研究出发，共同关注情境如何改变观察结果。"
                "比较首先需要明确研究对象、时间窗口和概念口径，让每一项判断具有可核对的范围。\n\n"
                "## 共识、冲突与互补\n\n"
                "环境观测提供变化趋势，案例材料解释行动条件，文本细读呈现经验如何被表达。"
                "这些证据可以围绕同一问题相互补充。出现结论分歧时，应继续检查测量方式、"
                "样本背景和解释层次，保留材料尚无法排除的竞争性判断。\n\n"
                "## 把阅读变成后续问题\n\n"
                "下一步可以把比较尺度与证据缺口记入知识笔记，形成新的阅读清单。"
                "每次补充资料后，再回到已有判断，核对哪些结论得到支持、哪些需要修改。"),
            audit_json=json.dumps({"book_ids": selected, "citation_count": 5, "cited_book_ids": selected,
                                   "discipline": "跨学科", "review_type": "叙事综述"}, ensure_ascii=False),
        ))
        db.add(WritingOutput(
            profile_id=profile.id,
            kind="ai_tone",
            title="表达审阅示例",
            input_type="text",
            source_text="首先，我们必须深入探讨这一重要问题。",
            output_text="先明确问题，再比较现有证据。",
            audit_json=json.dumps({"changes": [{"id": "demo-1", "old": "首先，我们必须深入探讨这一重要问题。",
                                                  "new": "先明确问题，再比较现有证据。", "rule_ids": ["R02"]}]}, ensure_ascii=False),
        ))
        db.commit()
        return book_ids
    finally:
        db.close()
        engine.dispose()


def wait_for_server(base_url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(base_url + "/api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"演示服务未在 {timeout:.0f} 秒内启动")


def capture(base_url: str, book_ids: list[int], destination: Path) -> None:
    from playwright.sync_api import sync_playwright

    destination.mkdir(parents=True, exist_ok=True)
    captures: list[tuple[str, str]] = [
        ("library.jpg", "/library"),
        ("reader.jpg", f"/reader/{book_ids[0]}"),
        ("knowledge-notes.jpg", "/knowledge-hub?view=notes"),
        ("research-workspace.jpg", "/knowledge-hub?view=study"),
        ("workbench.jpg", f"/literature-workbench?bookId={book_ids[0]}"),
        ("writing-lab.jpg", "/writing"),
        ("writing-review.jpg", "/writing"),
        ("writing-clean.jpg", "/writing"),
        ("writing-output.jpg", "/writing"),
    ]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        browser_errors: list[str] = []
        page.on("response", lambda response: browser_errors.append(f"HTTP {response.status}: {response.url}")
                if response.status >= 400 else None)
        page.on("pageerror", lambda error: browser_errors.append(f"pageerror: {error}"))
        page.on(
            "console",
            lambda message: browser_errors.append(f"console: {message.text}")
            if message.type == "error"
            else None,
        )
        scope_json = json.dumps(book_ids[:3])
        page.add_init_script(script=(
            f"localStorage.setItem('studyKnowledgeScope', JSON.stringify({scope_json}));"
            "localStorage.setItem('study-library-sort', 'custom');"
            "localStorage.setItem('aiKeyGuideSeen', 'true');"
        ))
        for filename, route in captures:
            browser_errors.clear()
            page.goto(base_url + route, wait_until="networkidle", timeout=30_000)
            page.wait_for_timeout(900)
            if filename == "library.jpg":
                page.locator(".paper-row").first.wait_for(timeout=10_000)
            elif filename == "reader.jpg":
                page.wait_for_timeout(2_500)
            elif filename == "research-workspace.jpg":
                history = page.locator(".history button")
                if history.count():
                    history.first.click()
                    page.wait_for_timeout(700)
            elif filename == "workbench.jpg":
                knowledge_select = page.locator("input[placeholder*='选择笔记']")
                if knowledge_select.count():
                    knowledge_select.click()
                    page.wait_for_timeout(250)
                    option = page.locator(".el-select-dropdown:visible .el-select-dropdown__item")
                    if option.count():
                        option.first.click()
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(350)
            elif filename == "writing-lab.jpg":
                profile = page.locator(".profile-row")
                if profile.count():
                    profile.first.click()
                    page.wait_for_timeout(700)
                    page.get_by_text("逻辑结构 DNA", exact=True).click()
                    page.wait_for_timeout(250)
            elif filename == "writing-review.jpg":
                page.get_by_role("tab", name="写作生成", exact=True).click()
                page.get_by_text("多文献综述", exact=True).first.click()
                review_select = page.locator(".generate-config .review-field .el-select").first
                review_select.click()
                page.wait_for_timeout(250)
                for index in range(3):
                    options = page.locator(".el-select-dropdown:visible .el-select-dropdown__item")
                    if options.count() > index:
                        options.nth(index).click()
                        page.wait_for_timeout(120)
                page.keyboard.press("Escape")
                page.locator(".review-fields.two input").nth(0).fill("跨学科证据如何形成对话")
                page.locator(".review-fields.two input").nth(1).fill("共识、冲突与互补证据分别成立于哪些条件？")
                page.wait_for_timeout(350)
            elif filename == "writing-clean.jpg":
                page.get_by_role("tab", name="写作输出", exact=True).click()
                page.locator('.output-history > button').filter(has_text='表达审阅示例').click()
                page.get_by_role("tab", name="去 AI 味", exact=True).click()
            elif filename == "writing-output.jpg":
                page.get_by_role("tab", name="写作输出", exact=True).click()
                page.locator('.output-history > button').first.click()
                page.wait_for_timeout(350)

            body = page.locator("body").inner_text()
            for forbidden in FORBIDDEN_TERMS:
                if forbidden in body:
                    raise RuntimeError(f"截图页面命中禁止词：{forbidden}")
            overflow = page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth + 2"
            )
            if overflow:
                raise RuntimeError(f"截图页面存在横向溢出：{route}")
            if browser_errors:
                raise RuntimeError(f"截图页面存在浏览器错误：{route}: {browser_errors}")
            page.screenshot(path=str(destination / filename), type="jpeg", quality=90, full_page=False)
        page.close()

        smoke_failures: list[dict[str, object]] = []
        for viewport in (
            {"name": "desktop", "width": 1440, "height": 900},
            {"name": "mobile", "width": 390, "height": 844},
        ):
            smoke_page = browser.new_page(
                viewport={"width": viewport["width"], "height": viewport["height"]}
            )
            smoke_errors: list[str] = []
            smoke_page.on("pageerror", lambda error: smoke_errors.append(f"pageerror: {error}"))
            smoke_page.on(
                "console",
                lambda message: smoke_errors.append(f"console: {message.text}")
                if message.type == "error"
                else None,
            )
            for route in SMOKE_ROUTES:
                smoke_errors.clear()
                response = smoke_page.goto(base_url + route, wait_until="networkidle", timeout=30_000)
                smoke_page.wait_for_timeout(300)
                body = smoke_page.locator("body").inner_text().strip()
                overflow = smoke_page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth + 2"
                )
                if response is None or response.status >= 400 or not body or overflow or smoke_errors:
                    smoke_failures.append({
                        "viewport": viewport["name"],
                        "route": route,
                        "status": response.status if response else None,
                        "empty": not bool(body),
                        "horizontal_overflow": overflow,
                        "errors": list(smoke_errors),
                    })
            smoke_page.close()
        if smoke_failures:
            raise RuntimeError(
                "关键路由 UI 冒烟失败：" + json.dumps(smoke_failures, ensure_ascii=False)
            )
        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        raise RuntimeError("请先在 frontend 目录运行 npm run build")

    with tempfile.TemporaryDirectory(prefix="study_release_demo_") as raw_temp:
        temp = Path(raw_temp)
        data_dir = temp / "data"
        captures = temp / "captures"
        book_ids = seed_demo(data_dir)
        env = os.environ.copy()
        env["DATA_DIR"] = str(data_dir)
        env["PORT"] = str(args.port)
        log_path = temp / "server.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", str(args.port)],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                base_url = f"http://127.0.0.1:{args.port}"
                wait_for_server(base_url)
                capture(base_url, book_ids, captures)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                # Windows may keep the SQLite file handle for a brief moment after
                # the server process exits; give it time to release before the
                # temporary directory context removes the isolated database.
                time.sleep(0.5)

        target = ROOT / "docs" / "assets" / "screenshots"
        target.mkdir(parents=True, exist_ok=True)
        for screenshot in captures.glob("*.jpg"):
            shutil.copy2(screenshot, target / screenshot.name)
    print("已用隔离演示库更新产品截图，并通过 26 项关键路由 UI 冒烟。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
