"""写作 DNA 蒸馏、风格参考写作与白名单去 AI 味。

实现边界来自 writing-dna-skill：完整语料至少 20 篇；DNA 六层分开保存；
仿写前加载全部产物与 5 篇相近原文；去 AI 味只返回精确替换操作。
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.database import SessionLocal
from backend.app.models import (Book, Chunk, PaperProfile, WritingDnaProfile,
                                WritingDnaRevision, WritingOutput)
from backend.app.services.llm import LLMRouter, load_llm_config, parse_json_response
from backend.app.worker.tasks import TaskRecord, update_progress

AI_TONE_RULES = """仅按以下白名单最小改写，未命中内容逐字保留：
1 翻案腔；2 分句内顿号串联三项以上；3 相邻句同款骨架；4 揭晓式破折号；
5 提示性冒号；6 连续三个以上序数词小标题；7 理想化职业人格比喻；
8 用概括盖住已有数字/时间，或“完成了对/实现了提升/进行了优化”空泛名词化；
9 “说白了/说穿了/先说结论”；10 只处理五种翻译腔：过长前置定语、
“当…时”前置从句、前置话题壳、句首连接词路标、同义的“这意味着/表明”复述；
11 非首段零主语评论且缺少回指。
硬约束：不改变标题层级、段落/列表/表格/引用/代码位置；不新增或删除事实、数字、
姓名、机构、日期、引语、链接、因果和限定词；不因句长、被动句、普通名词化、问句、
句内排比或一般比喻而改写。每项改动必须给出 rule_ids。"""


async def _call(provider, messages: list[dict]) -> str:
    answer = ""
    async for delta in provider.stream_chat(messages):
        answer += delta
    if not answer.strip():
        raise RuntimeError("AI 返回为空")
    return answer.strip()


async def _call_json(provider, messages: list[dict]) -> dict:
    raw = await _call(provider, messages)
    value = parse_json_response(raw)
    if not isinstance(value, dict):
        raise RuntimeError("AI 没有返回可验证的 JSON 结果")
    return value


def _article_text(db, book_id: int) -> str:
    chunks = db.scalars(select(Chunk).where(Chunk.book_id == book_id).order_by(Chunk.chunk_index)).all()
    seen: set[str] = set()
    parts: list[str] = []
    for chunk in chunks:
        text = (chunk.content or "").strip()
        digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
        if text and digest not in seen:
            seen.add(digest); parts.append(text)
    return "\n\n".join(parts)


def validate_corpus(db, book_ids: list[int]) -> list[dict]:
    ids = list(dict.fromkeys(int(value) for value in book_ids))
    if len(ids) < 20:
        raise ValueError(f"Writing DNA 至少需要 20 篇完整文章，当前只有 {len(ids)} 篇")
    books = {book.id: book for book in db.scalars(select(Book).where(Book.id.in_(ids))).all()}
    manifest = []
    for book_id in ids:
        book = books.get(book_id)
        if not book or book.status != "ready":
            raise ValueError(f"书目 {book_id} 尚未完成解析")
        chars = db.scalar(select(Chunk.word_count).where(Chunk.book_id == book_id).limit(1))
        text = _article_text(db, book_id)
        if len(text) < 500:
            raise ValueError(f"《{book.title}》正文不足 500 字，不能视为完整文章")
        profile = db.get(PaperProfile, book_id)
        image_count = 0
        if profile and profile.source_map_json:
            image_count = profile.source_map_json.count('"image')
        manifest.append({"book_id": book_id, "title": book.title, "file_type": book.file_type,
                         "chars": len(text), "word_count": chars or len(text), "image_count": image_count})
    return manifest


def _local_language_dna(db, manifest: list[dict]) -> tuple[str, dict]:
    """逐篇累计全文统计，避免把 20 篇以上全文同时留在内存。"""
    sentence_count = paragraph_count = total_chars = dash_count = parenthesis_count = 0
    short_count = long_count = sentence_chars = 0
    heading_chars: list[int] = []
    token_counts: Counter = Counter()
    stop = {"我们", "这个", "一种", "可以", "进行", "以及", "通过", "其中", "对于", "因此"}
    for item in manifest:
        text = _article_text(db, item["book_id"])
        total_chars += len(text)
        sentences = [part.strip() for part in re.split(r"[。！？!?]", text) if part.strip()]
        lengths = [len(sentence) for sentence in sentences]
        sentence_count += len(lengths); sentence_chars += sum(lengths)
        short_count += sum(length <= 15 for length in lengths)
        long_count += sum(length >= 50 for length in lengths)
        paragraph_count += len([part for part in re.split(r"\n\s*\n", text) if part.strip()])
        dash_count += text.count("——") + text.count("—")
        parenthesis_count += text.count("（") + text.count("(")
        token_counts.update(re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z][A-Za-z0-9_-]{2,}", text))
        heading_chars.extend(len(line.lstrip("# ").strip()) for line in text.splitlines()
                             if line.startswith("#") or (0 < len(line.strip()) <= 30 and re.match(
                                 r"^(第.{1,8}[章节]|[一二三四五六七八九十]+、|\d+(?:\.\d+)+)", line.strip())))
    common = [(word, count) for word, count in token_counts.most_common(160) if word not in stop][:100]
    stats = {
        "article_count": len(manifest), "total_chars": total_chars,
        "average_sentence_chars": round(sentence_chars / max(1, sentence_count), 1),
        "short_sentence_ratio": round(short_count / max(1, sentence_count), 3),
        "long_sentence_ratio": round(long_count / max(1, sentence_count), 3),
        "average_sentences_per_paragraph": round(sentence_count / max(1, paragraph_count), 2),
        "dash_count": dash_count, "parenthesis_count": parenthesis_count,
        "heading_average_chars": round(sum(heading_chars) / max(1, len(heading_chars)), 1),
        "frequent_terms": common,
    }
    md = "# 语言DNA\n\n## 量化基线\n\n" + "\n".join([
        f"- 语料：{stats['article_count']} 篇，{stats['total_chars']} 字符",
        f"- 平均句长：{stats['average_sentence_chars']} 字；短句占比 {stats['short_sentence_ratio']:.1%}；长句占比 {stats['long_sentence_ratio']:.1%}",
        f"- 每段平均句数：{stats['average_sentences_per_paragraph']}",
        f"- 破折号/括号计数：{stats['dash_count']} / {stats['parenthesis_count']}",
        f"- 小标题平均长度：{stats['heading_average_chars']} 字",
        "- 高频词：" + "、".join(word for word, _ in common[:30]),
    ])
    return md, stats


def _representative_excerpt(text: str, budget: int = 2800) -> str:
    if len(text) <= budget:
        return text
    third = budget // 3
    middle = max(0, len(text) // 2 - third // 2)
    return text[:third] + "\n[…中段…]\n" + text[middle:middle + third] + "\n[…结尾…]\n" + text[-third:]


async def distill_profile_task(record: TaskRecord, profile_id: int) -> dict:
    db = SessionLocal()
    try:
        profile = db.get(WritingDnaProfile, profile_id)
        if not profile:
            raise RuntimeError("Writing DNA 项目不存在")
        profile.status = "running"; profile.error_msg = None; db.commit()
        ids = json.loads(profile.book_ids_json or "[]")
        manifest = validate_corpus(db, ids)
        profile.corpus_manifest_json = json.dumps(manifest, ensure_ascii=False)
        update_progress(record, 0.08, "corpus", f"正在逐篇读取 {len(ids)} 篇完整文章")
        language_md, stats = _local_language_dna(db, manifest)
        samples = []
        for index, item in enumerate(manifest):
            text = _article_text(db, item["book_id"])
            samples.append(f"## {item['title']}｜{len(text)}字｜图片线索{item['image_count']}\n{_representative_excerpt(text)}")
            update_progress(record, .08 + .28 * (index + 1) / len(manifest), "corpus",
                            f"已分析 {index + 1}/{len(manifest)} 篇；仅保留有限代表片段")
        corpus = "\n\n".join(samples)
        update_progress(record, .43, "distill", "正在综合结构、选题、素材、认知与视觉规律")
        cfg = load_llm_config(db); cfg["deepseek_model"] = "pro"
        provider = LLMRouter.get("auto", cfg)
        prompt = f"""你在执行 Writing DNA 蒸馏。语料由 {len(manifest)} 篇完整文章构成；量化统计基于全文，
下方每篇提供首中尾代表片段用于跨文章归纳。不要摘要具体观点，不复制独特句子，只提取可操作规律。
必须输出 JSON 对象，键为 structure_patterns、cognitive_framework、visual_style_guide、writing_dna、quality。
前四项是中文 Markdown 字符串；writing_dna 不超过4000字并含语言、结构、选题、素材、认知、视觉六节。
structure_patterns 至少给出3种内容类型；cognitive_framework 至少3条非显而易见命题。
若图片内容样本不足5篇，视觉指南必须明确“图片语义样本不足，待补充”，不得臆测。
quality 包含 structure_type_count、cognitive_claim_count、visual_sample_count、limitations 数组。
用户完善反馈：{profile.feedback or '无'}
全文量化统计：{json.dumps(stats, ensure_ascii=False)}
语料代表片段：\n{corpus[:72000]}"""
        result = await _call_json(provider, [{"role": "system", "content": "只输出严格 JSON，不冒充原作者。"},
                                             {"role": "user", "content": prompt}])
        quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
        quality.update({"article_count": len(manifest), "metadata_coverage": 1.0,
                        "local_stats": stats, "rights_acknowledged": bool(profile.rights_acknowledged)})
        version = profile.current_version + 1
        revision = WritingDnaRevision(
            profile_id=profile.id, version=version, language_dna=language_md + "\n\n" + str(result.get("language_notes") or ""),
            structure_patterns=str(result.get("structure_patterns") or "# 文章结构模板\n\n待人工复核。"),
            cognitive_framework=str(result.get("cognitive_framework") or "# 写作视角与认知框架\n\n待人工复核。"),
            visual_style_guide=str(result.get("visual_style_guide") or "# 视觉风格指南\n\n图片语义样本不足，待补充。"),
            writing_dna=str(result.get("writing_dna") or "# Writing-DNA\n\n待人工复核。")[:16000],
            quality_json=json.dumps(quality, ensure_ascii=False), feedback=profile.feedback,
        )
        db.add(revision); profile.current_version = version; profile.status = "ready"; db.commit(); db.refresh(revision)
        update_progress(record, 1.0, "done", f"Writing DNA v{version} 已生成，可继续补充语料或反馈")
        return {"profile_id": profile.id, "revision_id": revision.id, "version": version}
    except Exception as exc:
        profile = db.get(WritingDnaProfile, profile_id)
        if profile:
            profile.status = "failed"; profile.error_msg = str(exc); db.commit()
        raise
    finally:
        db.close()


def _latest_revision(db, profile_id: int) -> WritingDnaRevision:
    revision = db.scalar(select(WritingDnaRevision).where(WritingDnaRevision.profile_id == profile_id)
                         .order_by(WritingDnaRevision.version.desc()).limit(1))
    if not revision:
        raise ValueError("请先完成 Writing DNA 蒸馏")
    return revision


async def imitate(db, profile_id: int, topic: str, genre: str, length: int, brief: str) -> WritingOutput:
    profile = db.get(WritingDnaProfile, profile_id)
    if not profile or profile.status != "ready":
        raise ValueError("Writing DNA 尚未就绪")
    revision = _latest_revision(db, profile_id)
    ranked = []
    topic_terms = set(re.findall(r"[\u4e00-\u9fff]{2,4}|[A-Za-z]{3,}", topic.lower()))
    for book_id in json.loads(profile.book_ids_json or "[]"):
        book = db.get(Book, book_id); text = _article_text(db, book_id)
        score = sum(text.lower().count(term) for term in topic_terms)
        ranked.append((score, book.title if book else str(book_id), book_id))
    related = sorted(ranked, reverse=True)[:5]
    calibration = "\n\n".join(
        f"## {title}\n{_representative_excerpt(_article_text(db, book_id), 1800)}"
        for _, title, book_id in related
    )
    cfg = load_llm_config(db); cfg["deepseek_model"] = "pro"
    provider = LLMRouter.get("auto", cfg)
    prompt = f"""按下列 Writing DNA 写一篇新的中文文章。复刻抽象的语言、结构和视觉排版规律，
不得复制原文独特短语、事实和观点，不得冒充原作者；文末附“本文为风格参考写作”。
用户要求优先于 DNA。题目：{topic}\n体裁：{genre}\n目标长度：约{length}字\n补充要求：{brief or '无'}
【语言DNA】\n{revision.language_dna}\n【结构模板】\n{revision.structure_patterns}
【认知框架】\n{revision.cognitive_framework}\n【视觉指南】\n{revision.visual_style_guide}
【整合DNA】\n{revision.writing_dna}\n【5篇相近原文，仅校准语感，不得取材】\n{calibration[:14000]}"""
    output_text = await _call(provider, [{"role": "system", "content": "生成独立新作，避免近似复述和作者冒充。"},
                                         {"role": "user", "content": prompt}])
    row = WritingOutput(profile_id=profile_id, kind="imitation", title=topic[:255], input_type="text",
                        source_text=brief, output_text=output_text,
                        audit_json=json.dumps({"dna_version": revision.version, "calibration_books": [title for _, title, _ in related]}, ensure_ascii=False))
    db.add(row); db.commit(); db.refresh(row)
    return row


def _validate_replacement(old: str, new: str) -> None:
    protected_patterns = [r"https?://\S+", r"\d+(?:\.\d+)?%?", r"[“‘][^”’]+[”’]"]
    for pattern in protected_patterns:
        for value in re.findall(pattern, old):
            if value not in new:
                raise ValueError(f"去 AI 味结果改动了受保护信息：{value[:30]}")
    for qualifier in ("可能", "通常", "据说", "某些情况下", "未必"):
        if qualifier in old and qualifier not in new:
            raise ValueError(f"去 AI 味结果删除了限定词：{qualifier}")


async def clean_blocks(db, blocks: list[dict], profile_id: int | None = None) -> tuple[list[dict], dict]:
    language = ""
    if profile_id:
        language = _latest_revision(db, profile_id).language_dna
    cfg = load_llm_config(db); provider = LLMRouter.get("auto", cfg)
    accepted: list[dict] = []
    rejected: list[dict] = []
    batches: list[list[dict]] = []
    batch: list[dict] = []; batch_chars = 0
    for block in blocks:
        chars = len(block.get("text") or "")
        if batch and (len(batch) >= 80 or batch_chars + chars > 30000):
            batches.append(batch); batch = []; batch_chars = 0
        batch.append(block); batch_chars += chars
    if batch:
        batches.append(batch)
    for batch in batches:
        payload = json.dumps(batch, ensure_ascii=False)
        prompt = f"""{AI_TONE_RULES}
目标语体语言DNA（如为空则按原体裁）：{language[:5000]}
输入是带稳定 id 的段落/表格文本。只输出 JSON：{{"changes":[{{"id":"...","old":"原文中的精确连续子串","new":"替换文本","rule_ids":[1]}}]}}。
没有命中的块不要返回。old 必须是该块原文的精确子串；一次改动尽量只覆盖一个问题。
输入：{payload}"""
        result = await _call_json(provider, [{"role": "system", "content": "执行白名单最小改写与信息守恒，不做普通润色。"},
                                             {"role": "user", "content": prompt}])
        by_id = {str(block["id"]): block["text"] for block in batch}
        for change in result.get("changes", []) if isinstance(result.get("changes"), list) else []:
            block_id = str(change.get("id") or "")
            old, new = str(change.get("old") or ""), str(change.get("new") or "")
            rules = [int(rule) for rule in change.get("rule_ids", []) if str(rule).isdigit()]
            try:
                if not old or old not in by_id.get(block_id, "") or not rules or any(rule < 1 or rule > 11 for rule in rules):
                    raise ValueError("替换操作缺少精确原文或有效规则编号")
                _validate_replacement(old, new)
                accepted.append({"id": block_id, "old": old, "new": new, "rule_ids": rules})
            except ValueError as exc:
                rejected.append({"id": block_id, "reason": str(exc)})
    return accepted, {"accepted": len(accepted), "rejected": rejected, "rules": sorted({r for item in accepted for r in item["rule_ids"]})}


def apply_text_changes(text: str, changes: list[dict]) -> str:
    lines = text.splitlines(keepends=True)
    for change in changes:
        index = int(change["id"].split(":")[-1])
        if 0 <= index < len(lines):
            lines[index] = lines[index].replace(change["old"], change["new"], 1)
    return "".join(lines)


def text_blocks(text: str) -> list[dict]:
    return [{"id": f"line:{index}", "text": line.rstrip("\r\n")} for index, line in enumerate(text.splitlines(keepends=True)) if line.strip()]


def docx_blocks(path: Path):
    import docx
    document = docx.Document(path)
    refs: dict[str, object] = {}
    blocks = []
    for index, paragraph in enumerate(document.paragraphs):
        if paragraph.text.strip():
            key = f"p:{index}"; refs[key] = paragraph; blocks.append({"id": key, "text": paragraph.text})
    for table_index, table in enumerate(document.tables):
        for row_index, row in enumerate(table.rows):
            for cell_index, cell in enumerate(row.cells):
                for paragraph_index, paragraph in enumerate(cell.paragraphs):
                    if paragraph.text.strip():
                        key = f"t:{table_index}:{row_index}:{cell_index}:{paragraph_index}"
                        refs[key] = paragraph; blocks.append({"id": key, "text": paragraph.text})
    return document, blocks, refs


def _replace_across_runs(paragraph, old: str, new: str) -> bool:
    runs = list(paragraph.runs)
    full = "".join(run.text for run in runs)
    start = full.find(old)
    if start < 0:
        return False
    end = start + len(old)
    positions = []
    cursor = 0
    for index, run in enumerate(runs):
        positions.append((index, cursor, cursor + len(run.text))); cursor += len(run.text)
    touched = [(i, left, right) for i, left, right in positions if right > start and left < end]
    if not touched:
        return False
    first_i, first_left, _ = touched[0]; last_i, last_left, _ = touched[-1]
    prefix = runs[first_i].text[:start - first_left]
    suffix = runs[last_i].text[end - last_left:]
    runs[first_i].text = prefix + new + (suffix if first_i == last_i else "")
    for index, _, _ in touched[1:-1]: runs[index].text = ""
    if last_i != first_i: runs[last_i].text = suffix
    return True


def apply_docx_changes(document, refs: dict[str, object], changes: list[dict]) -> int:
    applied = 0
    for change in changes:
        paragraph = refs.get(change["id"])
        if paragraph is not None and _replace_across_runs(paragraph, change["old"], change["new"]):
            applied += 1
    return applied


def create_word_output(title: str, text: str, destination: Path) -> None:
    import docx
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor
    document = docx.Document()
    normal = document.styles["Normal"]
    normal.font.name = "STZhongsong"; normal._element.rPr.rFonts.set(qn("w:eastAsia"), "华文中宋")
    normal.font.size = Pt(12); normal.font.color.rgb = RGBColor(51, 51, 51)
    for style_name, size in (("Title", 20), ("Heading 1", 18), ("Heading 2", 16), ("Heading 3", 14)):
        style = document.styles[style_name]
        style.font.name = "STZhongsong"; style._element.rPr.rFonts.set(qn("w:eastAsia"), "华文中宋")
        style.font.size = Pt(size); style.font.color.rgb = RGBColor(51, 51, 51)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    first_heading = re.sub(r"^#{1,3}\s+", "", first_line).strip()
    if title.strip() and first_heading != title.strip():
        document.add_heading(title.strip(), level=0)
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            document.add_paragraph(); continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        paragraph = document.add_heading(heading.group(2), level=len(heading.group(1))) if heading else document.add_paragraph(stripped)
        if not heading:
            paragraph.paragraph_format.line_spacing = 1.5
            paragraph.paragraph_format.space_after = Pt(6)
        for run in paragraph.runs:
            run.font.name = "STZhongsong"; run._element.rPr.rFonts.set(qn("w:eastAsia"), "华文中宋")
    destination.parent.mkdir(parents=True, exist_ok=True)
    document.save(destination)


def output_path(suffix: str = ".docx") -> Path:
    folder = settings.writing_dir / "outputs"; folder.mkdir(parents=True, exist_ok=True)
    return folder / f"writing-{uuid.uuid4().hex[:16]}{suffix}"
