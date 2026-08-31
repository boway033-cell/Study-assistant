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
from backend.app.models import (Book, Chunk, EvidenceCard, KnowledgeNote, PaperProfile, StudyReport, WritingDnaProfile,
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

AI_TONE_GENERATION_GUARD = """生成时主动避开以下机械痕迹，但不得为规避痕迹而改变证据含义：
翻案腔；三项以上顿号堆砌；连续同款句式；揭晓式破折号；提示性冒号；
连续三个以上序数词小标题；空泛人格比喻；用概括覆盖数字、时间或限定词；
“说白了/说穿了/先说结论”；过长前置定语、路标式连接词和“这意味着”复述；
无回指的零主语评论。保持必要的学术术语、被动句、数字、引语、因果边界和不确定性。
正文先提出主张，再用最相关证据推进；删除为预防假想反对而写的免责声明、重复限定和自我辩护。
真实影响结论的范围与方法限制只在最相关位置平静说明一次，不把“可能、一定程度上、需要指出”等修饰词连续叠加。"""

LITERATURE_REVIEW_LENSES = {
    "auto": "先依据每篇材料的方法与论证类型判断适用学科镜头；混合语料并列使用相关镜头，不强行统一评价标准。",
    "social_science": """社会科学镜头：核对理论概念与操作化、样本和情境、资料生产过程、因果识别与相关性边界、
替代机制、定量不确定性或质性编码/研究者反身性，并说明外部效度。""",
    "humanities": """人文与文学镜头：以文本细读为证据，比较措辞、叙事结构、修辞、意象和历史语境；
区分文本证据与解释框架，呈现竞争性阐释和反向阅读，不以论文数量多数表决解释真伪。""",
    "natural_biomedical": """自然科学与生物医学镜头：核对研究设计、样本、对照、实验条件、效应方向与大小、
统计/测量不确定性、混杂和偏倚、可重复性；观察性关联不得改写为因果。""",
}

LITERATURE_REVIEW_TYPES = {
    "narrative": "主题型叙事综述：围绕问题组织学术对话，不按文献逐篇摘要。",
    "scoping": "范围综述草稿：绘制当前所选语料覆盖的概念、方法、对象与空白；没有完整检索筛选流程，不宣称 PRISMA 合规。",
    "evidence_map": "证据图谱：以可比较维度呈现共识、冲突、互补证据、反例和证据缺口。",
}


def content_fingerprint(*values: object) -> str:
    """Stable snapshot hash used to detect knowledge changes after generation."""
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


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
        cfg = load_llm_config(db, "writing"); cfg["deepseek_model"] = "pro"
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


def collect_writing_knowledge(db, allowed_book_ids: list[int], knowledge_note_ids: list[int] | None,
                              evidence_card_ids: list[int] | None, report_ids: list[int] | None,
                              max_chars: int = 30000) -> tuple[str, list[dict]]:
    """Resolve selected knowledge objects; raw corpus remains style calibration only."""
    note_ids = list(dict.fromkeys(knowledge_note_ids or []))
    card_ids = list(dict.fromkeys(evidence_card_ids or []))
    selected_report_ids = list(dict.fromkeys(report_ids or []))
    if not note_ids and not card_ids and not selected_report_ids:
        raise ValueError("请至少选择一个知识对象作为写作取材来源")
    allowed = set(allowed_book_ids)
    notes = list(db.scalars(select(KnowledgeNote).where(KnowledgeNote.id.in_(note_ids))).all()) if note_ids else []
    cards = list(db.scalars(select(EvidenceCard).where(EvidenceCard.id.in_(card_ids))).all()) if card_ids else []
    reports = list(db.scalars(select(StudyReport).where(StudyReport.id.in_(selected_report_ids))).all()) if selected_report_ids else []
    if {item.id for item in notes} != set(note_ids) or {item.id for item in cards} != set(card_ids) or {item.id for item in reports} != set(selected_report_ids):
        raise ValueError("所选知识对象不存在或已被删除")
    if any(item.book_id not in allowed for item in [*notes, *cards]):
        raise ValueError("所选知识对象超出当前 Writing DNA 的文献范围")
    for report in reports:
        report_books = set(json.loads(report.book_ids_json or "[]"))
        if not report_books or not report_books.issubset(allowed):
            raise ValueError("所选研究报告超出当前 Writing DNA 的文献范围")
    blocks: list[str] = []
    manifest: list[dict] = []
    for note in notes:
        blocks.append(f"[NOTE:{note.id}|B{note.book_id}] {note.title}\n{note.content}")
        manifest.append({"type": "note", "id": note.id, "book_id": note.book_id, "title": note.title,
                         "fingerprint": content_fingerprint(note.title, note.content, note.source_refs_json)})
    for card in cards:
        blocks.append(f"[EVIDENCE:{card.id}|B{card.book_id}] {card.title}\n主张：{card.claim_text or ''}\n证据：{card.evidence_text}\n核验状态：{card.verification_status}")
        manifest.append({"type": "evidence", "id": card.id, "book_id": card.book_id,
                         "title": card.title, "verification_status": card.verification_status,
                         "fingerprint": content_fingerprint(card.title, card.claim_text, card.evidence_text,
                                                            card.source_ref_json, card.verification_status)})
    for report in reports:
        blocks.append(f"[REPORT:{report.id}] {report.focus or '综合研读'}\n{report.content}")
        manifest.append({"type": "report", "id": report.id,
                         "book_ids": json.loads(report.book_ids_json or "[]"), "title": report.focus or "综合研读",
                         "fingerprint": content_fingerprint(report.focus, report.content, report.claims_json,
                                                            report.selection_json)})
    per_object = max(800, max_chars // max(len(blocks), 1))
    context = "\n\n".join(block[:per_object] for block in blocks)
    if any(len(block) > per_object for block in blocks):
        context += "\n\n[部分知识对象达到公平取材上限；未展示部分不得推断]"
    return context, manifest


def _review_query_terms(question: str) -> set[str]:
    stop = {"研究", "文献", "综述", "问题", "分析", "如何", "什么", "哪些", "以及", "之间", "影响"}
    return {term.lower() for term in re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z][A-Za-z0-9_-]{2,}", question)
            if term.lower() not in stop}


def _select_evidence_chunks(chunks: list[Chunk], question: str, limit: int = 8) -> list[Chunk]:
    """每篇文献同时保留首中尾结构证据和议题相关片段，避免只截取最相似段落。"""
    if len(chunks) <= limit:
        return chunks
    terms = _review_query_terms(question)
    structural = {0, len(chunks) // 2, len(chunks) - 1}
    ranked = sorted(
        range(len(chunks)),
        key=lambda index: (sum((chunks[index].content or "").lower().count(term) for term in terms),
                           len(chunks[index].content or "")),
        reverse=True,
    )
    selected = set(structural)
    for index in ranked:
        if len(selected) >= limit:
            break
        selected.add(index)
    return [chunks[index] for index in sorted(selected, key=lambda value: chunks[value].chunk_index)]


def _anchor_for_chunk(chunk: Chunk) -> str:
    start = int(chunk.page_start or 1)
    end = int(chunk.page_end or start)
    return f"[B{chunk.book_id}:C{chunk.id}:P{start}-{end}]"


def collect_literature_evidence(db, book_ids: list[int], question: str,
                                max_chars: int = 72000) -> tuple[str, list[dict], set[str]]:
    """从显式选择的库内文献构建公平、有页码锚点的封闭证据包。"""
    ids = list(dict.fromkeys(int(value) for value in book_ids))
    if len(ids) < 2:
        raise ValueError("多文献综述至少需要选择 2 篇已解析文献")
    if len(ids) > 50:
        raise ValueError("一次最多选择 50 篇文献")
    books = {book.id: book for book in db.scalars(select(Book).where(Book.id.in_(ids))).all()}
    profiles = {profile.book_id: profile for profile in db.scalars(
        select(PaperProfile).where(PaperProfile.book_id.in_(ids))).all()}
    per_book_budget = max(1600, min(10000, max_chars // len(ids)))
    blocks: list[str] = []
    manifest: list[dict] = []
    valid_anchors: set[str] = set()
    for book_id in ids:
        book = books.get(book_id)
        if not book or book.status != "ready":
            raise ValueError(f"文献 {book_id} 尚未完成解析")
        chunks = list(db.scalars(select(Chunk).where(Chunk.book_id == book_id).order_by(Chunk.chunk_index)).all())
        if not chunks:
            raise ValueError(f"《{book.title}》没有可用于综述的正文分块")
        selected = _select_evidence_chunks(chunks, question)
        per_chunk_budget = max(450, per_book_budget // max(1, len(selected)))
        profile = profiles.get(book_id)
        metadata = {
            "book_id": book_id, "title": book.title,
            "authors": profile.authors if profile else None,
            "year": profile.published_year if profile else None,
            "journal": profile.journal if profile else None,
        }
        header_bits = [f"B{book_id}《{book.title}》"]
        if metadata["authors"]:
            header_bits.append(str(metadata["authors"]))
        if metadata["year"]:
            header_bits.append(str(metadata["year"]))
        if metadata["journal"]:
            header_bits.append(str(metadata["journal"]))
        evidence_rows = []
        anchors = []
        source_fingerprints = []
        for chunk in selected:
            anchor = _anchor_for_chunk(chunk)
            valid_anchors.add(anchor)
            anchors.append(anchor)
            text = re.sub(r"\n{3,}", "\n\n", (chunk.content or "").strip())[:per_chunk_budget]
            evidence_rows.append(f"{anchor}\n{text}")
            source_fingerprints.append({
                "chunk_id": chunk.id,
                "fingerprint": content_fingerprint(chunk.content, chunk.page_start, chunk.page_end,
                                                   chunk.chapter_id),
            })
        blocks.append("## " + "｜".join(header_bits) + "\n" + "\n\n".join(evidence_rows))
        manifest.append({**metadata, "book_file_hash": book.file_hash,
                         "total_chunks": len(chunks), "selected_chunks": len(selected),
                         "anchors": anchors, "source_fingerprints": source_fingerprints,
                         "excerpt_chars": sum(len(row) for row in evidence_rows)})
    return "\n\n".join(blocks), manifest, valid_anchors


def build_literature_review_prompt(question: str, title: str, review_type: str, discipline: str,
                                   length: int, evidence_context: str,
                                   style_context: str = "", ai_tone_constraints: bool = True) -> str:
    if review_type not in LITERATURE_REVIEW_TYPES:
        raise ValueError("不支持的综述类型")
    if discipline not in LITERATURE_REVIEW_LENSES:
        raise ValueError("不支持的学科镜头")
    style = style_context or "未选择 Writing DNA：保持清晰、克制的学术中文；不模仿任何具体作者。"
    tone = AI_TONE_GENERATION_GUARD if ai_tone_constraints else "未启用去 AI 味生成约束，仍须保持事实与引文边界。"
    return f"""请以当前“已选知识库文献证据包”为事实基础，生成一篇有中心判断、论证连续的中文多文献综述。
题目：{title}\n核心问题：{question}\n目标长度：约 {length} 字
综述路径：{LITERATURE_REVIEW_TYPES[review_type]}
学科审查：{LITERATURE_REVIEW_LENSES[discipline]}

【证据与推演边界】
1. 文献事实、数字、样本、作者观点和书目信息只能来自证据包；不得补造缺失信息。
2. 原文事实与作者观点附近放置原样锚点，如 [B12:C81:P3-4]；锚点只是机器审计标记，系统会在成文后自动转成脚注编号。不要让锚点参与句法，也不要为了形式在每段重复堆叠。
3. 按主题综合，不按“第一篇、第二篇”逐篇摘要。明确区分共识、冲突、互补证据和最强反证。
4. 冲突先检查概念、样本、情境、方法、测量与时间差异；证据不足就写“不足以判断”。
5. 允许在多篇证据之间自由建立概念联系、机制解释、比较框架和研究问题，使文章真正推进论证。仅在容易与作者原结论混淆时简洁标明分析主体，不要每次推演都加防御性标签。
6. 尽量覆盖所有已选文献；弱相关材料可作为边界或反例。若确实无法纳入，不要硬凑，系统会在引用审计中提示用户。
7. 这只是用户已选个人知识库的封闭语料综述。没有系统检索、去重、筛选与质量评估流程，
   禁止自称“系统综述”“元分析”或“PRISMA 合规”。
8. 优先转述并整合观点；只有原文措辞本身值得分析时才使用短引语，同一段通常不超过一处。段首先说核心判断，必要局限集中说明一次；删除不增加证据、范围或逻辑的免责声明和修饰词堆叠。
9. 输出前静默完成一次反防御性修订：主张前置；每段只完成一个论证任务；把真实不确定性改写为具体来源或适用范围；删除重复的“不意味着/并非/仍需指出/一定程度上”。不要输出检查过程。

【可调整的结构】
# 题目
## 语料边界与问题
## 证据地图（用紧凑 Markdown 表格比较主题、方法/材料、主要贡献、局限）
## 主题综合
## 共识、冲突与互补证据
## 学科专属的批判性审查
## 最强反证、研究局限与尚不能回答的问题
## 结论
## 已选文献清单（只使用证据包中给出的书目信息）

【Writing DNA：只约束表达，不得作为事实来源】\n{style[:12000]}

【去 AI 味生成约束】\n{tone}

【已选文献证据包】\n{evidence_context}"""


async def generate_literature_review(db, *, question: str, title: str, book_ids: list[int],
                                     review_type: str = "narrative", discipline: str = "auto",
                                     length: int = 3500, profile_id: int | None = None,
                                     ai_tone_constraints: bool = True) -> WritingOutput:
    style_context = ""
    dna_version = None
    if profile_id is not None:
        profile = db.get(WritingDnaProfile, profile_id)
        if not profile or profile.status != "ready":
            raise ValueError("所选 Writing DNA 尚未就绪")
        revision = _latest_revision(db, profile_id)
        dna_version = revision.version
        style_context = "\n\n".join([
            revision.language_dna, revision.structure_patterns,
            revision.cognitive_framework, revision.writing_dna,
        ])
    evidence_context, manifest, valid_anchors = collect_literature_evidence(db, book_ids, question)
    prompt = build_literature_review_prompt(
        question, title, review_type, discipline, length, evidence_context,
        style_context, ai_tone_constraints,
    )
    cfg = load_llm_config(db, "writing"); cfg["deepseek_model"] = "pro"
    provider = LLMRouter.get("auto", cfg)
    output_text = await _call(provider, [
        {"role": "system", "content": "你是个人知识库内的研究作者。围绕中心问题自由综合多篇文献，写成连贯文章；忠于锚点、保留冲突，并明确标识自己的推断。"},
        {"role": "user", "content": prompt},
    ])
    found_anchors = set(re.findall(r"\[B\d+:C\d+:P\d+(?:-\d+)?\]", output_text))
    invalid_anchors = sorted(found_anchors - valid_anchors)
    valid_found = found_anchors & valid_anchors
    cited_book_ids = {int(match) for match in re.findall(r"\[B(\d+):C\d+:P\d+(?:-\d+)?\]", " ".join(valid_found))}
    from backend.app.services.writing_citations import database_source_labels, readable_citations
    citation_labels = database_source_labels(db, {anchor.strip("[]") for anchor in valid_found})
    output_text, citation_notes = readable_citations(
        output_text, valid_anchors={anchor.strip("[]") for anchor in valid_found}, labels=citation_labels,
    )
    selected_ids = list(dict.fromkeys(int(value) for value in book_ids))
    row = WritingOutput(
        profile_id=profile_id, kind="literature_review", title=title[:255], input_type="text",
        source_text=evidence_context, output_text=output_text,
        audit_json=json.dumps({
            "review_type": review_type, "discipline": discipline, "book_ids": selected_ids,
            "evidence_manifest": manifest, "valid_anchor_count": len(valid_anchors),
            "citation_count": len(valid_found), "invalid_anchors": invalid_anchors,
            "citation_notes": citation_notes,
            "citation_warning": ("部分锚点无效，需人工核对" if invalid_anchors else
                                 "跨文献覆盖不足，已保留草稿供人工补充" if len(cited_book_ids) < 2 else ""),
            "cited_book_ids": sorted(cited_book_ids),
            "unreferenced_book_ids": [book_id for book_id in selected_ids if book_id not in cited_book_ids],
            "dna_version": dna_version, "ai_tone_constraints": ai_tone_constraints,
            "content_policy": "selected_library_documents_only",
            "method_boundary": "closed_corpus_not_systematic_review",
            "human_review_required": True,
        }, ensure_ascii=False),
    )
    db.add(row); db.commit(); db.refresh(row)
    return row


async def imitate(db, profile_id: int, topic: str, genre: str, length: int, brief: str,
                  knowledge_note_ids: list[int] | None = None,
                  evidence_card_ids: list[int] | None = None,
                  report_ids: list[int] | None = None) -> WritingOutput:
    profile = db.get(WritingDnaProfile, profile_id)
    if not profile or profile.status != "ready":
        raise ValueError("Writing DNA 尚未就绪")
    revision = _latest_revision(db, profile_id)
    profile_book_ids = json.loads(profile.book_ids_json or "[]")
    knowledge_context, knowledge_manifest = collect_writing_knowledge(
        db, profile_book_ids, knowledge_note_ids, evidence_card_ids, report_ids,
    )
    ranked = []
    topic_terms = set(re.findall(r"[\u4e00-\u9fff]{2,4}|[A-Za-z]{3,}", topic.lower()))
    for book_id in profile_book_ids:
        book = db.get(Book, book_id); text = _article_text(db, book_id)
        score = sum(text.lower().count(term) for term in topic_terms)
        ranked.append((score, book.title if book else str(book_id), book_id))
    related = sorted(ranked, reverse=True)[:5]
    calibration = "\n\n".join(
        f"## {title}\n{_representative_excerpt(_article_text(db, book_id), 1800)}"
        for _, title, book_id in related
    )
    cfg = load_llm_config(db, "writing"); cfg["deepseek_model"] = "pro"
    provider = LLMRouter.get("auto", cfg)
    prompt = f"""按下列 Writing DNA 写一篇新的中文文章。复刻抽象的语言、结构和视觉排版规律，
不得复制原文独特短语、事实和观点，不得冒充原作者；文末附“本文为风格参考写作”。
用户要求优先于 DNA。题目：{topic}\n体裁：{genre}\n目标长度：约{length}字\n补充要求：{brief or '无'}
【强制取材边界】事实、观点、数字、案例和结论只能来自下列已选知识对象。保留其中的冲突、不确定性、反例、证据质量与限定词；
不得把未核验假设写成事实。用 [NOTE:…]、[EVIDENCE:…] 或 [REPORT:…] 标记关键事实的来源即可；标记只供机器审计，系统会自动转换为脚注，不要让它参与句法或逐段堆叠。材料不足时直接收缩主张。
【已选知识对象】\n{knowledge_context}
【语言DNA】\n{revision.language_dna}\n【结构模板】\n{revision.structure_patterns}
【认知框架】\n{revision.cognitive_framework}\n【视觉指南】\n{revision.visual_style_guide}
【整合DNA】\n{revision.writing_dna}\n【5篇相近原文，仅校准语感，不得取材】\n{calibration[:14000]}"""
    output_text = await _call(provider, [{"role": "system", "content": "生成独立新作；内容只能取自已选知识对象，风格语料不得充当事实来源。围绕中心主张直接、连贯地写作，避免近似复述、作者冒充、免责声明和修饰词堆叠。"},
                                         {"role": "user", "content": prompt}])
    from backend.app.services.writing_citations import readable_citations
    knowledge_labels = {
        f"{str(item['type']).upper()}:{item['id']}": (
            f"{ {'note': '知识笔记', 'evidence': '证据卡', 'report': '研究报告'}.get(item['type'], '知识对象') }“{item['title']}”"
        ) for item in knowledge_manifest
    }
    output_text, citation_notes = readable_citations(
        output_text, valid_anchors=set(knowledge_labels), labels=knowledge_labels,
    )
    row = WritingOutput(profile_id=profile_id, kind="imitation", title=topic[:255], input_type="text",
                        source_text=knowledge_context, output_text=output_text,
                        audit_json=json.dumps({"dna_version": revision.version,
                                               "calibration_books": [title for _, title, _ in related],
                                               "knowledge_objects": knowledge_manifest,
                                               "citation_notes": citation_notes,
                                               "content_policy": "selected_knowledge_objects_only",
                                               "human_review_required": True}, ensure_ascii=False))
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
    cfg = load_llm_config(db, "writing"); provider = LLMRouter.get("auto", cfg)
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
