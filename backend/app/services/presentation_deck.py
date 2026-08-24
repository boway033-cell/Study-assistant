"""Evidence-grounded Chinese literature-report deck generation.

The pipeline adapts nature-paper2ppt's paper-type routing and nature-polishing's
claim-evidence-boundary discipline.  It never asks the model to invent missing
results: each slide must retain local chunk/page source IDs.
"""
from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.database import SessionLocal
from backend.app.models import Book, Chapter, Chunk, PaperProfile, PresentationDeck
from backend.app.services.llm import LLMRouter, load_llm_config, parse_json_response
from backend.app.worker.tasks import update_progress


PAPER_TYPE_LABELS = {
    "discovery": "发现 / 机制",
    "methods": "方法 / 算法",
    "resource": "资源 / 数据集",
    "clinical": "临床 / 人群",
    "materials": "材料 / 工程",
    "review": "综述 / 观点",
}

TYPE_KEYWORDS = {
    "clinical": "trial cohort patient clinical randomized intervention survival meta-analysis 临床 患者 队列 试验",
    "methods": "method algorithm model benchmark framework architecture dataset baseline 方法 算法 模型 基准 架构",
    "resource": "atlas resource database cohort dataset accession repository 图谱 资源 数据库 数据集",
    "materials": "material synthesis fabrication device performance xrd sem catalyst 材料 合成 器件 表征 性能",
    "review": "review perspective commentary systematic review 综述 述评 展望",
    "discovery": "mechanism pathway phenotype cell gene protein discovery 机制 通路 表型 细胞 基因",
}


def classify_paper_type(title: str, text: str) -> str:
    hay = f"{title}\n{text[:18000]}".lower()
    scores = {kind: sum(hay.count(w) for w in words.split()) for kind, words in TYPE_KEYWORDS.items()}
    return max(scores, key=scores.get) if max(scores.values(), default=0) else "discovery"


def collect_selection(db, book_id: int, chapter_ids: list[int], chunk_ids: list[int], selected_text: str) -> dict:
    book = db.get(Book, book_id)
    if not book:
        raise ValueError("文献不存在")
    query = select(Chunk).where(Chunk.book_id == book_id)
    if chunk_ids:
        query = query.where(Chunk.id.in_(chunk_ids))
    elif chapter_ids:
        query = query.where(Chunk.chapter_id.in_(chapter_ids))
    chunks = list(db.scalars(query.order_by(Chunk.chunk_index)).all())
    if not chapter_ids and not chunk_ids:
        chunks = chunks[:60]
    sources = []
    total = 0
    for c in chunks:
        content = (c.content or "").strip()
        if not content:
            continue
        # Keep model input bounded while preserving complete sentences where possible.
        content = content[:4000]
        if total + len(content) > 52000:
            break
        total += len(content)
        sources.append({
            "source_id": f"chunk:{c.id}", "chunk_id": c.id, "chapter_id": c.chapter_id,
            "page_start": c.page_start, "page_end": c.page_end, "text": content,
        })
    if selected_text.strip():
        sources.insert(0, {"source_id": "selection:user", "chunk_id": None, "chapter_id": None,
                           "page_start": None, "page_end": None, "text": selected_text.strip()[:12000]})
    if not sources:
        raise ValueError("所选范围没有可用于汇报的正文")
    chapter_names = dict(db.execute(select(Chapter.id, Chapter.title).where(Chapter.book_id == book_id)).all())
    return {
        "book": {"id": book.id, "title": book.title},
        "chapter_ids": chapter_ids, "chunk_ids": chunk_ids,
        "chapter_titles": [chapter_names[i] for i in chapter_ids if i in chapter_names],
        "sources": sources,
    }


def _local_outline(title: str, paper_type: str, sources: list[dict], slide_count: int) -> list[dict]:
    arcs = {
        "methods": ["研究背景与当前瓶颈", "核心问题", "方法总览", "关键设计", "评测设置", "主要结果", "稳健性与失败案例", "适用边界", "总结"],
        "resource": ["研究背景", "资源概览", "样本与数据设计", "生成与质控流程", "主要图谱", "关键洞见", "验证与复用", "局限性", "总结"],
        "clinical": ["临床问题", "研究问题", "研究设计", "终点与变量", "主要结果", "次要分析", "偏倚与局限", "实践意义", "总结"],
        "materials": ["技术挑战", "设计原理", "制备与装置", "关键表征", "性能证据", "构效关系", "稳定性与边界", "局限性", "总结"],
        "review": ["主题为何重要", "概念框架", "主题一", "主题二", "主题三", "争议与缺口", "作者综合观点", "未来方向", "总结"],
        "discovery": ["研究背景", "知识缺口", "核心问题与假设", "实验设计", "关键证据一", "关键证据二", "验证与稳健性", "机制模型", "局限性", "总结"],
    }
    wanted = max(6, min(slide_count, 18))
    names = arcs[paper_type]
    if wanted - 1 < len(names):
        names = names[: wanted - 2] + [names[-1]]
    while len(names) < wanted - 1:
        names.insert(-1, f"补充证据 {len(names) - 3}")
    slides = [{"title": title, "kind": "cover", "claim": f"{PAPER_TYPE_LABELS[paper_type]}论文中文汇报",
               "bullets": [], "source_ids": []}]
    for i, name in enumerate(names):
        src = sources[min(i, len(sources) - 1)]
        sentence = re.split(r"(?<=[。！？.!?])\s*|\n+", src["text"])[0].strip()[:130]
        slides.append({"title": name, "kind": "content", "claim": sentence or "请结合原文核对本页论点",
                       "bullets": ["本页由本地证据片段生成；建议在汇报前核对原图与数值。"],
                       "source_ids": [src["source_id"]]})
    return slides[:wanted]


async def _ai_outline(provider, title: str, paper_type: str, sources: list[dict], options: dict) -> list[dict] | None:
    source_text = "\n\n".join(
        f"[{s['source_id']}; p.{s['page_start'] or '?'}-{s['page_end'] or s['page_start'] or '?'}]\n{s['text']}"
        for s in sources
    )
    prompt = f"""你是严谨的中文学术汇报编辑。论文类型：{PAPER_TYPE_LABELS[paper_type]}。
受众：{options['audience']}；目的：{options['purpose']}；目标 {options['slide_count']} 页，{options['duration_minutes']} 分钟。
按“为什么重要→知识缺口→作者做了什么→关键证据→可信度→意义/复用→边界”建立证据链。
只使用下方来源。不得补造数字、因果关系、实验或结论；不确定就写“原文未说明”。
每页只表达一个结论，中文短句，术语和数值逐字忠于来源。每个事实页必须列 source_ids。
输出严格 JSON 数组，每项字段：title, kind(cover/content/evidence/limitations/summary), claim,
bullets(0-4条，每条不超过45字), source_ids(只能引用给定ID)。封面可无来源。不要 markdown。

来源：
{source_text}"""
    chunks = []
    async for delta in provider.stream_chat([{"role": "system", "content": "遵循 claim-evidence-boundary；只输出 JSON。"},
                                              {"role": "user", "content": prompt}]):
        chunks.append(delta)
    parsed = parse_json_response("".join(chunks))
    if not isinstance(parsed, list):
        return None
    allowed = {s["source_id"] for s in sources}
    result = []
    for item in parsed[:18]:
        if not isinstance(item, dict):
            continue
        ids = [x for x in item.get("source_ids", []) if x in allowed]
        result.append({"title": str(item.get("title") or "未命名页面")[:70],
                       "kind": str(item.get("kind") or "content")[:20],
                       "claim": str(item.get("claim") or "")[:220],
                       "bullets": [str(x)[:100] for x in item.get("bullets", [])[:4]],
                       "source_ids": ids})
    return result if len(result) >= 5 else None


def _add_textbox(slide, left, top, width, height, text, size, color, bold=False, font="Microsoft YaHei"):
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.dml.color import RGBColor
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.clear(); frame.word_wrap = True; frame.vertical_anchor = MSO_ANCHOR.TOP
    p = frame.paragraphs[0]; p.text = text; p.alignment = PP_ALIGN.LEFT
    p.font.name = font; p.font.size = size; p.font.bold = bold; p.font.color.rgb = RGBColor(*color)
    return box


def _extract_page_figure(pdf_path: Path, page_no: int, output: Path) -> Path | None:
    """Extract the largest embedded raster on a source page without cropping labels."""
    if not pdf_path.exists() or page_no < 1:
        return None
    try:
        import fitz
        doc = fitz.open(pdf_path)
        try:
            if page_no > len(doc): return None
            images = doc[page_no - 1].get_images(full=True)
            candidates = []
            for info in images:
                width, height = int(info[2]), int(info[3])
                if width >= 420 and height >= 260:
                    candidates.append((width * height, info[0]))
            if not candidates: return None
            pix = fitz.Pixmap(doc, max(candidates)[1])
            if pix.n - pix.alpha > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            pix.save(output)
            return output
        finally:
            doc.close()
    except Exception:
        return None


def render_pptx(path: Path, book: Book, profile: PaperProfile | None, outline: list[dict], sources: list[dict], paper_type: str, options: dict) -> None:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt

    prs = Presentation(); prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    source_map = {s["source_id"]: s for s in sources}
    pdf_path = settings.uploads_dir / book.file_path
    with tempfile.TemporaryDirectory(prefix="deck_fig_") as tmp:
      tmp_dir = Path(tmp)
      for idx, item in enumerate(outline):
        slide = prs.slides.add_slide(blank)
        bg = slide.background.fill; bg.solid(); bg.fore_color.rgb = RGBColor(247, 244, 237)
        if idx == 0:
            shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(4.3), prs.slide_height)
            shape.fill.solid(); shape.fill.fore_color.rgb = RGBColor(24, 54, 56); shape.line.fill.background()
            _add_textbox(slide, Inches(.72), Inches(.75), Inches(2.8), Inches(.35), "LITERATURE REPORT", Pt(15), (210,181,143), True)
            _add_textbox(slide, Inches(4.85), Inches(1.25), Inches(7.45), Inches(2.2), item["title"], Pt(36), (35,47,45), True)
            _add_textbox(slide, Inches(4.9), Inches(3.8), Inches(6.8), Inches(.8), item.get("claim", ""), Pt(20), (92,87,76))
            meta = " · ".join(x for x in [profile.authors if profile else None, profile.journal if profile else None,
                                            str(profile.published_year) if profile and profile.published_year else None] if x)
            _add_textbox(slide, Inches(4.9), Inches(5.75), Inches(6.8), Inches(.45), meta or "本地知识库文献", Pt(13), (115,108,95))
        else:
            _add_textbox(slide, Inches(.72), Inches(.42), Inches(11.6), Inches(.65), item["title"], Pt(28), (28,54,54), True)
            accent = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(.72), Inches(1.22), Inches(.8), Inches(.06))
            accent.fill.solid(); accent.fill.fore_color.rgb = RGBColor(182,128,76); accent.line.fill.background()
            hero = None
            if options.get("include_figures") and book.file_type == "pdf":
                for sid in item.get("source_ids", []):
                    src = source_map.get(sid, {}); page_no = src.get("page_start")
                    if page_no:
                        hero = _extract_page_figure(pdf_path, int(page_no), tmp_dir / f"{idx}-{page_no}.png")
                        if hero: break
            text_width = Inches(5.65) if hero else Inches(11.45)
            _add_textbox(slide, Inches(.86), Inches(1.62), text_width, Inches(1.3), item.get("claim", ""), Pt(24), (39,42,39), True)
            bullet_text = "\n".join(f"• {x}" for x in item.get("bullets", []))
            _add_textbox(slide, Inches(1.02), Inches(3.15), Inches(5.35) if hero else Inches(10.9), Inches(2.55), bullet_text, Pt(18), (69,70,64))
            if hero:
                from PIL import Image
                with Image.open(hero) as im: ratio = im.width / max(im.height, 1)
                max_w, max_h = Inches(5.5), Inches(4.7)
                pic_w = min(max_w, int(max_h * ratio)); pic_h = int(pic_w / ratio)
                slide.shapes.add_picture(str(hero), Inches(7.15) + (max_w - pic_w) // 2,
                                         Inches(1.48) + (max_h - pic_h) // 2, width=pic_w, height=pic_h)
            refs = []
            for sid in item.get("source_ids", []):
                src = source_map.get(sid, {})
                page = src.get("page_start")
                refs.append(f"{sid}" + (f" · p.{page}" if page else ""))
            _add_textbox(slide, Inches(.76), Inches(6.78), Inches(11.8), Inches(.28), "来源：" + "；".join(refs), Pt(9), (125,119,108))
        _add_textbox(slide, Inches(12.25), Inches(6.84), Inches(.45), Inches(.25), f"{idx+1:02d}", Pt(9), (125,119,108), True)
        notes = [f"[Deck] {book.title}", f"[PaperType] {paper_type}", "[Sources]"]
        for sid in item.get("source_ids", []):
            src = source_map.get(sid, {})
            notes.append(f"- {sid}; pages={src.get('page_start')}-{src.get('page_end')}")
        try:
            slide.notes_slide.notes_text_frame.text = "\n".join(notes)
        except Exception:
            pass
    path.parent.mkdir(parents=True, exist_ok=True); prs.save(path)


def audit_pptx(path: Path, outline: list[dict]) -> dict:
    from pptx import Presentation
    prs = Presentation(path)
    issues = []
    if len(prs.slides) != len(outline): issues.append("幻灯片数量与提纲不一致")
    for i, (slide, spec) in enumerate(zip(prs.slides, outline), 1):
        text = " ".join(shape.text for shape in slide.shapes if hasattr(shape, "text_frame"))
        if i > 1 and not spec.get("source_ids"): issues.append(f"第{i}页缺少来源定位")
        if len(text) > 520: issues.append(f"第{i}页文字偏多")
        for shape in slide.shapes:
            if shape.left < 0 or shape.top < 0 or shape.left + shape.width > prs.slide_width or shape.top + shape.height > prs.slide_height:
                issues.append(f"第{i}页存在越界对象"); break
    return {"ok": not issues, "slide_count": len(prs.slides), "issues": issues}


async def generate_deck_task(record, deck_id: int) -> dict:
    db = SessionLocal()
    try:
        deck = db.get(PresentationDeck, deck_id)
        if not deck: raise ValueError("汇报任务不存在")
        deck.status = "running"; db.commit()
        selection = json.loads(deck.selection_json or "{}"); options = json.loads(deck.options_json or "{}")
        book = db.get(Book, deck.book_id); profile = db.get(PaperProfile, deck.book_id)
        sources = selection["sources"]
        paper_type = classify_paper_type(book.title, "\n".join(x["text"] for x in sources))
        deck.paper_type = paper_type
        update_progress(record, .2, "deck", f"已识别为{PAPER_TYPE_LABELS[paper_type]}论文")
        outline = None; cfg = load_llm_config(db)
        if cfg.get("deepseek_api_key"):
            update_progress(record, .42, "deck", "AI 正在按证据链组织中文提纲")
            outline = await _ai_outline(LLMRouter.get("auto", cfg), book.title, paper_type, sources, options)
        if not outline:
            outline = _local_outline(book.title, paper_type, sources, options["slide_count"])
        deck.outline_json = json.dumps(outline, ensure_ascii=False)
        update_progress(record, .72, "deck", "正在生成可编辑 PPTX")
        filename = f"paper-report-{book.id}-{deck.id}.pptx"; path = settings.presentations_dir / filename
        render_pptx(path, book, profile, outline, sources, paper_type, options)
        qa = audit_pptx(path, outline)
        manifest = {"version": 1, "created_at": datetime.now().isoformat(), "book_id": book.id,
                    "paper_type": paper_type, "selection": {k: selection.get(k) for k in ("chapter_ids", "chunk_ids", "chapter_titles")},
                    "source_ids": [s["source_id"] for s in sources], "generator": "nature-paper2ppt-adapted",
                    "language": "zh-CN", "editable": True}
        deck.file_path = filename; deck.qa_json = json.dumps(qa, ensure_ascii=False)
        deck.manifest_json = json.dumps(manifest, ensure_ascii=False); deck.status = "done"; db.commit()
        update_progress(record, 1, "deck", "中文文献汇报已生成")
        return {"deck_id": deck.id, "slide_count": len(outline), "paper_type": paper_type, "qa": qa}
    except Exception as exc:
        db.rollback(); deck = db.get(PresentationDeck, deck_id)
        if deck: deck.status = "failed"; deck.error_msg = str(exc); db.commit()
        raise
    finally:
        db.close()
