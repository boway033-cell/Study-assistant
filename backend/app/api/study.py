"""AI 研读：综合阅读报告 + 思维训练（出题批改追问 / 自由陪练）"""
from __future__ import annotations

import json
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, Chapter, Chunk, EvidenceCard, KnowledgeNote, StudyReport
from backend.app.services.llm import LLMRouter, load_llm_config

router = APIRouter(prefix="/api/study", tags=["study"])


# ---------- 会话（内存态，单用户）----------
_sessions: dict[str, dict] = {}


def _book_context(db: Session, book_ids: list[int] | None, limit_per_book: int = 4000) -> str:
    """收集选中文献的结构与内容摘要。
    
    使用统一知识事实层（KnowledgeBase），替代直接读原始 chunks：
    - 有深度分析 → 用 Markdown 精读版（已缓存，零 AI 消耗）
    - 无深度分析 → 用结构化摘要（章节+关键词+定义），本地零 AI
    """
    from backend.app.services.knowledge_base import get_multi_book_digest
    return get_multi_book_digest(db, book_ids, limit_per_book=limit_per_book)


# ---------- 综合阅读 ----------
class StudyOverviewReq(BaseModel):
    book_ids: list[int] = Field(max_length=8)
    focus: str = Field(default="", max_length=500)
    framework: str = Field(default="", max_length=400)
    chapter_ids: list[int] = Field(default_factory=list, max_length=40)
    note_ids: list[int] = Field(default_factory=list, max_length=80)
    research_mode: Literal["adaptive", "comparative", "critical", "gap"] = "adaptive"
    reasoning_depth: Literal["standard", "deep"] = "deep"
    writing_style: Literal["analytical_essay", "structured_report"] = "analytical_essay"
    extension_level: Literal["grounded", "exploratory"] = "exploratory"
    target_length: int = Field(default=3000, ge=800, le=12000)


_MODE_GUIDANCE = {
    "adaptive": "先识别资料类型与研究问题性质，再自主选择最有解释力的分析维度和报告结构；不要套用固定论文或期刊模板。",
    "comparative": "优先比较概念定义、核心主张、论证机制、证据类型、适用边界与相互冲突，但只保留对当前材料真正有用的维度。",
    "critical": "区分原始材料、作者解释与模型推断；检查证据强度、替代解释、反例、方法限制和因果外推。",
    "gap": "梳理已有共识与分歧，识别材料尚未回答的问题、证据缺口和可继续研究的方向，避免把未知包装成结论。",
}


async def _stream_answer(provider, messages: list[dict], error_prefix: str, on_progress=None) -> str:
    """统一流式调用与短暂故障重试；不在内存保留模型隐性推理过程。"""
    import asyncio

    last_err = ""
    for attempt in range(3):
        answer = ""
        last_notified_at = time.monotonic()
        last_notified_size = 0
        try:
            async for delta in provider.stream_chat(messages):
                answer += delta
                now = time.monotonic()
                if on_progress and (
                    now - last_notified_at >= 1.5 or len(answer) - last_notified_size >= 800
                ):
                    on_progress(len(answer))
                    last_notified_at = now
                    last_notified_size = len(answer)
            if answer.strip():
                if on_progress:
                    on_progress(len(answer))
                return answer
            last_err = "AI 返回为空"
        except Exception as exc:  # noqa: BLE001
            if exc.__class__.__name__ == "TaskCancelled":
                raise
            last_err = str(exc)
        await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{error_prefix}：{last_err}")


def _normalize_plan(value, mode: str) -> dict:
    """把模型规划压缩成可展示、可持久化的小型研究路径。"""
    value = value if isinstance(value, dict) else {}

    def _strings(key: str, limit: int, width: int) -> list[str]:
        raw = value.get(key) if isinstance(value.get(key), list) else []
        return [str(item).strip()[:width] for item in raw if str(item).strip()][:limit]

    return {
        "material_type": str(value.get("material_type") or "待综合判断")[:80],
        "subquestions": _strings("subquestions", 6, 180),
        "analysis_axes": _strings("analysis_axes", 5, 100),
        "evidence_needs": _strings("evidence_needs", 6, 160),
        "report_outline": _strings("report_outline", 8, 100),
        "mode": mode,
    }


def _source_anchor(item: dict) -> str:
    book_id = item.get("book_id")
    chapter_id = item.get("chapter_id")
    page_start = item.get("page_start") or item.get("page")
    page_end = item.get("page_end") or page_start
    chunk_id = item.get("chunk_id")
    parts = [f"B{book_id}"]
    if chapter_id:
        parts.append(f"CH{chapter_id}")
    if page_start:
        parts.append(f"P{page_start}" if page_end == page_start else f"P{page_start}-{page_end}")
    if chunk_id:
        parts.append(f"C{chunk_id}")
    return ":".join(parts)


def _format_retrieved_context(items: list[dict], max_chars: int = 22000) -> tuple[str, set[str]]:
    """将检索结果变成唯一来源锚点；按字符预算截断以控制任务内存。"""
    parts: list[str] = []
    anchors: set[str] = set()
    used_chunks: set[int] = set()
    size = 0
    for item in items:
        chunk_id = int(item.get("chunk_id") or 0)
        if chunk_id <= 0 or chunk_id in used_chunks:
            continue
        used_chunks.add(chunk_id)
        anchor = _source_anchor(item)
        body = str(item.get("context") or item.get("snippet") or "").strip()[:3200]
        if not body:
            continue
        title = str(item.get("book_title") or "未命名资料")
        chapter = str(item.get("chapter_title") or "未分章")
        block = f"[{anchor}]《{title}》—{chapter}\n{body}"
        if parts and size + len(block) > max_chars:
            break
        parts.append(block)
        anchors.add(anchor)
        size += len(block)
    return "\n\n".join(parts), anchors


def _normalize_claims(raw_claims, allowed_refs: set[str]) -> list[dict]:
    allowed_status = {"supported", "partial", "needs_review", "unsupported"}
    allowed_types = {"descriptive", "associational", "causal", "interpretive"}
    allowed_confidence = {"high", "medium", "low"}
    allowed_relations = {"consensus", "complementary", "conflict", "single_source", "unresolved"}
    allowed_quality = {"high", "moderate", "low", "very_low", "not_assessed"}
    claims = []
    for claim in raw_claims[:30] if isinstance(raw_claims, list) else []:
        if not isinstance(claim, dict) or not str(claim.get("claim") or "").strip():
            continue
        refs = []
        for raw_ref in claim.get("source_refs") or []:
            ref = str(raw_ref).strip().strip("[]")[:100]
            if ref in allowed_refs and ref not in refs:
                refs.append(ref)
        status = str(claim.get("status") or "needs_review")
        if status not in allowed_status or not refs:
            status = "needs_review"
        claim_type = str(claim.get("claim_type") or "interpretive")
        confidence = str(claim.get("confidence") or "low")
        relation = str(claim.get("synthesis_relation") or "unresolved")
        quality = str(claim.get("evidence_quality") or "not_assessed")
        bias_flags = [str(item).strip()[:120] for item in (claim.get("bias_flags") or [])
                      if str(item).strip()][:8]
        alternatives = [str(item).strip()[:240] for item in (claim.get("alternative_explanations") or [])
                        if str(item).strip()][:6]
        claims.append({
            "claim": str(claim["claim"]).strip()[:1000],
            "claim_type": claim_type if claim_type in allowed_types else "interpretive",
            "source_refs": refs,
            "status": status,
            "confidence": confidence if confidence in allowed_confidence else "low",
            "reason": str(claim.get("reason") or "")[:1000],
            "counterpoint": str(claim.get("counterpoint") or "")[:1000],
            "synthesis_relation": relation if relation in allowed_relations else "unresolved",
            "evidence_quality": quality if quality in allowed_quality else "not_assessed",
            "bias_flags": bias_flags,
            "alternative_explanations": alternatives,
            "human_review_required": bool(claim.get("human_review_required", True)),
        })
    return claims


def _normalize_hypotheses(raw_hypotheses, allowed_refs: set[str]) -> list[dict]:
    """Hypotheses remain candidates and must preserve evidence/rival boundaries."""
    result = []
    for item in raw_hypotheses[:12] if isinstance(raw_hypotheses, list) else []:
        if not isinstance(item, dict) or not str(item.get("statement") or "").strip():
            continue
        refs = []
        for raw_ref in item.get("source_refs") or []:
            ref = str(raw_ref).strip().strip("[]")[:100]
            if ref in allowed_refs and ref not in refs:
                refs.append(ref)
        result.append({
            "statement": str(item["statement"]).strip()[:800],
            "status": "candidate",
            "claim_type": str(item.get("claim_type") or "associational")[:40],
            "source_refs": refs,
            "rival_explanations": [str(value).strip()[:240] for value in (item.get("rival_explanations") or [])
                                   if str(value).strip()][:6],
            "falsifier": str(item.get("falsifier") or "")[:500],
            "boundary_conditions": str(item.get("boundary_conditions") or "")[:500],
            "human_review_required": True,
        })
    return result


def _evidence_summary(claims: list[dict]) -> dict:
    relations = {key: 0 for key in ("consensus", "complementary", "conflict", "single_source", "unresolved")}
    qualities = {key: 0 for key in ("high", "moderate", "low", "very_low", "not_assessed")}
    for claim in claims:
        relations[claim.get("synthesis_relation", "unresolved")] += 1
        qualities[claim.get("evidence_quality", "not_assessed")] += 1
    return {"relations": relations, "qualities": qualities,
            "human_review_required": sum(bool(item.get("human_review_required")) for item in claims)}


async def run_overview(record, book_ids: list[int], focus: str = "", framework: str = "",
                       chapter_ids: list[int] | None = None, note_ids: list[int] | None = None,
                       research_mode: str = "adaptive", reasoning_depth: str = "deep",
                       writing_style: str = "analytical_essay", extension_level: str = "exploratory",
                       target_length: int = 3000) -> dict:
    from backend.app.core.database import SessionLocal
    from backend.app.worker.tasks import update_progress

    db = SessionLocal()
    try:
        update_progress(record, 0.15, "overview", "正在汇总文献内容...")
        scope = set(book_ids)
        context_parts: list[str] = []
        allowed_refs: set[str] = set()
        context_chars = 0
        selected_chapters = db.scalars(select(Chapter).where(Chapter.id.in_(chapter_ids or [])).order_by(Chapter.book_id, Chapter.order_index)).all() if chapter_ids else []
        for chapter in selected_chapters:
            if context_chars >= 46000:
                break
            if chapter.book_id not in scope:
                continue
            book = db.get(Book, chapter.book_id)
            chunks = db.scalars(select(Chunk).where(Chunk.chapter_id == chapter.id).order_by(Chunk.chunk_index).limit(8)).all()
            for chunk in chunks:
                if context_chars >= 46000:
                    break
                item = {
                    "book_id": chapter.book_id, "book_title": book.title if book else str(chapter.book_id),
                    "chapter_id": chapter.id, "chapter_title": chapter.title, "chunk_id": chunk.id,
                    "page_start": chunk.page_start, "page_end": chunk.page_end, "context": chunk.content,
                }
                block, refs = _format_retrieved_context([item], max_chars=4600)
                if block:
                    remaining = 46000 - context_chars
                    if remaining < 200:
                        context_chars = 46000
                        break
                    block = block[:remaining]
                    context_parts.append(block)
                    context_chars += len(block) + 2
                    allowed_refs.update(refs)
        selected_notes = db.scalars(select(KnowledgeNote).where(KnowledgeNote.id.in_(note_ids or []))).all() if note_ids else []
        for note in selected_notes:
            if context_chars >= 46000:
                break
            if note.book_id in scope:
                anchor = f"B{note.book_id}:NOTE{note.id}"
                remaining = 46000 - context_chars
                if remaining < 200:
                    break
                block = f"[{anchor}]用户笔记—{note.title}\n{note.content[:2000]}"[:remaining]
                context_parts.append(block)
                context_chars += len(block) + 2
                allowed_refs.add(anchor)
        selected_context = "\n\n".join(context_parts)
        overview_context = selected_context or _book_context(db, book_ids)
        if not overview_context:
            raise ValueError("没有可用的文献")
        cfg = load_llm_config(db, "research")
        if not cfg.get("configured"):
            raise ValueError("研究模型尚未配置或不可用，请在设置中选择并检测一个模型")
        provider = LLMRouter.get("auto", cfg)
        plan = _normalize_plan({}, research_mode)
        if reasoning_depth == "deep":
            update_progress(record, 0.32, "research-plan", "AI 正在拆分问题并规划论证路径...")
            plan_messages = [
                {"role": "system", "content": (
                    "你是独立研究分析助手。不要套用 Nature 或任何期刊的固定写作模板，也不要机械复述目录。"
                    "请根据研究问题和材料本身，选择分析维度。区分材料事实、作者解释与模型推断。"
                    "只输出 JSON 对象："
                    '{"material_type":"材料类型判断","subquestions":["3-6个子问题"],'
                    '"analysis_axes":["2-5个分析维度"],"evidence_needs":["需核查的证据"],'
                    '"report_outline":["自适应报告章节"]}。规划应包含中心论题与连贯的论证推进，'
                    '而不是材料清单。不要输出思维过程或答案正文。'
                )},
                {"role": "user", "content": (
                    f"研读方式：{_MODE_GUIDANCE.get(research_mode, _MODE_GUIDANCE['adaptive'])}\n"
                    f"研究问题：{focus.strip()}\n用户补充维度：{framework.strip() or '无，由 AI 自主判断'}\n\n"
                    f"材料预览：\n{overview_context[:18000]}"
                )},
            ]
            from backend.app.services.llm import parse_json_response
            raw_plan = await _stream_answer(
                provider,
                plan_messages,
                "研究路径规划失败",
                lambda chars: update_progress(
                    record,
                    min(0.45, 0.32 + 0.13 * min(chars / 1800, 1)),
                    "research-plan",
                    f"正在形成研究路径（已接收 {chars} 字）...",
                ),
            )
            plan = _normalize_plan(parse_json_response(raw_plan), research_mode)
        record.result = {
            "kind": "study-report",
            "book_ids": book_ids,
            "focus": focus[:200],
            "research_plan": plan,
        }
        update_progress(record, 0.46, "research-plan", "研究路径已形成，准备检索证据", force=True)

        # 用户没有点选具体材料时，按研究问题和 AI 子问题在所选书目内迭代检索。
        # 已点选章节/笔记时严格遵守选择边界，不从其他章节补料。
        if selected_context:
            evidence_context = selected_context
        else:
            update_progress(record, 0.52, "evidence", "正在按研究路径检索和整理证据...")
            from backend.app.services.rag import retriever
            queries = [focus.strip()] + plan.get("subquestions", [])[:3]
            retrieved: list[dict] = []
            seen: set[int] = set()
            # 每本文献都获得独立召回预算，避免相关性最高的一本文献垄断上下文。
            for book_id in book_ids:
                accepted = 0
                for query in queries:
                    if not query or accepted >= 5:
                        continue
                    for item in retriever.retrieve(query, book_ids=[book_id], top_k=5):
                        chunk_id = int(item.get("chunk_id") or 0)
                        if chunk_id and chunk_id not in seen:
                            seen.add(chunk_id); retrieved.append(item); accepted += 1
                            if accepted >= 5:
                                break
            evidence_context, allowed_refs = _format_retrieved_context(retrieved, max_chars=42000)
            if not evidence_context:
                evidence_context = overview_context[:22000]

        from backend.app.services.writing_citations import database_source_labels, readable_citations
        citation_labels = database_source_labels(db, allowed_refs)
        # 不在数分钟的模型生成期间持有 SQLite 读事务，避免阻塞导入与任务状态写入。
        db.rollback()
        update_progress(record, 0.7, "synthesis", "AI 正在跨文献综合并形成连贯文章...")
        prompt = [
            {"role": "system", "content": (
                "你是独立、审慎且有创造力的个人知识库研究作者。批判性思考、同行审查和假设生成只是辅助能力，不是写作清单，"
                "不得让方法标签切碎正文或压制正常推演。围绕一个中心论题写成逻辑连续的中文文章，段落之间必须有因果、递进、转折或回应关系。"
                "不要机械摘要，不要逐篇流水账，不要补造来源。先区分原文信息、作者解释和你的综合推断，再综合一致、互补、冲突、"
                "替代解释与适用边界。对跨文献结论明确标记 consensus（独立证据同向）、complementary（回答不同环节）、"
                "conflict（结论方向或解释矛盾）、single_source 或 unresolved；不能用文献数量代替证据质量。"
                "审查研究设计、分析单位、混杂、选择/测量偏倚、因果外推、统计不确定性和可复现性；缺少报告只能写 not_assessed，不能判定方法错误。"
                "允许在证据基础上自由提出概念联系、机制解释、比较框架、反事实问题和后续研究方向。综合判断应自然写入论证；"
                "只有当读者可能把你的推演误认成作者原结论时，才简洁说明这是本文分析，不要反复使用“本文推断”等防御性标签。"
                "来源锚点用于支撑事实与推断前提，不要求每段重复堆叠。优先准确转述；只有原文措辞本身是分析对象时才短引，"
                "同一段通常不超过一处直接引语。先说主张，再给证据；必要限制集中写一次，不在段首和结论中反复自我削弱。"
                "输出前自行删去不增加证据、范围或逻辑的免责声明，合并连续的可能性修饰词，并检查每段只承担一个主要论证任务。"
                "标题与章节应服从论证，不使用固定期刊模板。只输出 JSON 对象："
                '{"report_markdown":"中文 Markdown 报告","claims":[{"claim":"可核验主张",'
                '"claim_type":"descriptive|associational|causal|interpretive","source_refs":["来源锚点"],'
                '"status":"supported|partial|needs_review|unsupported","confidence":"high|medium|low",'
                '"reason":"支持或降级理由","counterpoint":"反例或限制",'
                '"synthesis_relation":"consensus|complementary|conflict|single_source|unresolved",'
                '"evidence_quality":"high|moderate|low|very_low|not_assessed",'
                '"bias_flags":["具体且有来源依据的偏倚风险"],"alternative_explanations":["竞争性解释"],'
                '"human_review_required":true}],"open_questions":["材料尚未回答或需要继续核查的问题"],'
                '"hypotheses":[{"statement":"候选假设","claim_type":"descriptive|associational|predictive|causal|mechanistic",'
                '"source_refs":["来源锚点"],"rival_explanations":["竞争性解释"],"falsifier":"什么结果会挑战它",'
                '"boundary_conditions":"适用边界"}]}。\n'
                "每项关键结论必须使用材料中逐字存在的 [B…] 来源锚点；没有有效锚点的判断必须标为 needs_review。"
                "直接支持=supported；仅部分支持或含外推=partial；材料不足=needs_review；材料反驳=unsupported。"
                "因果主张必须有明确估计目标和相称的因果证据，不能因表达流畅或多处重复就提高置信度。"
                "hypotheses 始终只是 candidate；仅在 gap 模式或材料确有冲突/空白时给出，并同时给出竞争性解释和可证伪条件。"
            )},
            {"role": "user", "content": (
                f"研读方式：{_MODE_GUIDANCE.get(research_mode, _MODE_GUIDANCE['adaptive'])}\n"
                f"研究问题：{focus.strip()}\n用户补充维度：{framework.strip() or '无，允许 AI 自主选择'}\n"
                f"AI 研究路径（可调整结构，不是答案）：{json.dumps(plan, ensure_ascii=False)}\n\n"
                f"写作形态：{'连贯分析文章' if writing_style == 'analytical_essay' else '结构化研究报告'}；"
                f"推演自由度：{'允许有标识的探索性延伸' if extension_level == 'exploratory' else '以直接证据解释为主'}；"
                f"目标长度：约 {target_length} 字。\n"
                f"可引用材料：\n{evidence_context[:48000]}"
            )},
        ]
        answer = await _stream_answer(
            provider,
            prompt,
            "综合研读失败",
            lambda chars: update_progress(
                record,
                min(0.94, 0.7 + 0.24 * min(chars / max(target_length * 1.35, 1600), 1)),
                "synthesis",
                f"AI 正在组织论证与写作（已接收 {chars} 字）...",
            ),
        )

        from backend.app.services.llm import parse_json_response
        try:
            parsed = parse_json_response(answer)
        except Exception:  # 兼容旧模型偶发返回纯 Markdown；报告可保存，但主张均留待人工整理。
            parsed = None
        if isinstance(parsed, dict):
            report_content = str(parsed.get("report_markdown") or "").strip()
            raw_claims = parsed.get("claims") if isinstance(parsed.get("claims"), list) else []
            open_questions = [str(item).strip()[:300] for item in (parsed.get("open_questions") or []) if str(item).strip()][:10]
            raw_hypotheses = parsed.get("hypotheses") if isinstance(parsed.get("hypotheses"), list) else []
        else:
            report_content, raw_claims = answer, []
            open_questions = []
            raw_hypotheses = []
        if not report_content:
            report_content = answer
        claims = _normalize_claims(raw_claims, allowed_refs)
        hypotheses = _normalize_hypotheses(raw_hypotheses, allowed_refs)
        report_content, citation_notes = readable_citations(
            report_content, valid_anchors=allowed_refs, labels=citation_labels,
        )
        # 持久化报告及其选择范围/核验主张，不复制原文附件。
        from backend.app.models import StudyReport
        report = StudyReport(
            book_ids_json=json.dumps(book_ids or [], ensure_ascii=False),
            selection_json=json.dumps({
                "chapter_ids": chapter_ids or [], "note_ids": note_ids or [],
                "research_mode": research_mode, "reasoning_depth": reasoning_depth,
                "writing_style": writing_style, "extension_level": extension_level,
                "target_length": target_length,
                "citation_notes": citation_notes,
                "research_plan": plan, "open_questions": open_questions,
                "hypotheses": hypotheses, "evidence_summary": _evidence_summary(claims),
                "method_profile": {
                    "critical_thinking": "1.2", "peer_review": "2.1",
                    "hypothesis_generation": "2.1", "human_accountability": True,
                },
            }, ensure_ascii=False),
            focus=focus, framework=framework, claims_json=json.dumps(claims, ensure_ascii=False),
            content=report_content,
        )
        db.add(report)
        db.commit()
        update_progress(record, 1.0, "overview", "完成", force=True)
        return {"report_id": report.id, "chars": len(report_content), "claims": len(claims),
                "hypotheses": len(hypotheses), "plan_steps": len(plan.get("subquestions", []))}
    except Exception as e:  # noqa: BLE001
        raise
    finally:
        db.close()


@router.post("/overview", status_code=202)
def study_overview(req: StudyOverviewReq, db: Session = Depends(get_db)):
    from backend.app.worker.tasks import submit
    if not req.book_ids:
        raise HTTPException(422, "请至少选择一本研读文献")
    if not req.focus.strip():
        raise HTTPException(422, "请先写明研究问题")
    record = submit("study-overview", lambda rec: run_overview(
        rec, req.book_ids, req.focus, req.framework, req.chapter_ids, req.note_ids,
        req.research_mode, req.reasoning_depth, req.writing_style, req.extension_level,
        req.target_length,
    ), book_id=req.book_ids[0])
    return {"task_id": record.id}


def _report_payload(r, include_content: bool = True) -> dict:
    selection = json.loads(r.selection_json or "{}")
    payload = {
        "id": r.id, "book_ids": json.loads(r.book_ids_json or "[]"),
        "focus": r.focus or "", "framework": r.framework or "", "selection": selection,
        "research_plan": selection.get("research_plan", {}),
        "open_questions": selection.get("open_questions", []),
        "hypotheses": selection.get("hypotheses", []),
        "evidence_summary": selection.get("evidence_summary", {}),
        "method_profile": selection.get("method_profile", {}),
        "research_mode": selection.get("research_mode", "adaptive"),
        "reasoning_depth": selection.get("reasoning_depth", "standard"),
        "writing_style": selection.get("writing_style", "analytical_essay"),
        "extension_level": selection.get("extension_level", "exploratory"),
        "target_length": selection.get("target_length", 3000),
        "content_preview": (r.content or "")[:300], "content_length": len(r.content or ""),
        "claim_count": len(json.loads(r.claims_json or "[]")), "created_at": r.created_at.isoformat(),
    }
    if include_content:
        payload.update({"content": r.content, "claims": json.loads(r.claims_json or "[]")})
    return payload


@router.get("/reports")
def list_reports(page: int = 1, page_size: int = 20, db: Session = Depends(get_db)):
    from backend.app.models import StudyReport
    page = max(1, page); page_size = max(5, min(page_size, 50))
    total = db.scalar(select(func.count()).select_from(StudyReport)) or 0
    rows = db.scalars(select(StudyReport).order_by(StudyReport.created_at.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"total": total, "page": page, "page_size": page_size,
            "items": [_report_payload(row, False) for row in rows]}


@router.get("/reports/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db)):
    report = db.get(StudyReport, report_id)
    if not report:
        raise HTTPException(404, "报告不存在")
    return _report_payload(report, True)


class StudyClaimsUpdateReq(BaseModel):
    claims: list[dict] = Field(max_length=30)


@router.patch("/reports/{report_id}/claims")
def update_report_claims(report_id: int, req: StudyClaimsUpdateReq, db: Session = Depends(get_db)):
    """保存人工核验后的证据台账；来源锚点只能来自该报告原始检索范围。"""
    from datetime import datetime, timezone

    report = db.get(StudyReport, report_id)
    if not report:
        raise HTTPException(404, "报告不存在")
    previous = json.loads(report.claims_json or "[]")
    allowed_refs = {str(ref) for claim in previous for ref in (claim.get("source_refs") or [])}
    claims = _normalize_claims(req.claims, allowed_refs)
    if len(claims) != len(req.claims):
        raise HTTPException(422, "主张存在空文本、无效结构或超过 30 条")
    selection = json.loads(report.selection_json or "{}")
    selection["evidence_summary"] = _evidence_summary(claims)
    selection["claims_reviewed_at"] = datetime.now(timezone.utc).isoformat()
    selection["claims_reviewed_by"] = "user"
    report.claims_json = json.dumps(claims, ensure_ascii=False)
    report.selection_json = json.dumps(selection, ensure_ascii=False)
    db.commit(); db.refresh(report)
    return _report_payload(report, True)


class DepositStudyReportReq(BaseModel):
    include_report_note: bool = True
    include_claim_cards: bool = True


def _claim_book_id(claim: dict, allowed_book_ids: list[int]) -> int:
    for ref in claim.get("source_refs") or []:
        head = str(ref).split(":", 1)[0]
        if head.startswith("B") and head[1:].isdigit() and int(head[1:]) in allowed_book_ids:
            return int(head[1:])
    return allowed_book_ids[0]


@router.post("/reports/{report_id}/deposit")
def deposit_report(report_id: int, req: DepositStudyReportReq, db: Session = Depends(get_db)):
    """Idempotently deposit a critical review as a note and claim-level evidence cards."""
    report = db.get(StudyReport, report_id)
    if not report:
        raise HTTPException(404, "报告不存在")
    book_ids = [int(value) for value in json.loads(report.book_ids_json or "[]") if int(value) > 0]
    if not book_ids or db.scalar(select(func.count()).select_from(Book).where(Book.id.in_(book_ids))) != len(set(book_ids)):
        raise HTTPException(409, "报告关联的文献已不存在，无法沉淀")
    selection = json.loads(report.selection_json or "{}")
    claims = json.loads(report.claims_json or "[]")
    all_refs = list(dict.fromkeys(ref for claim in claims for ref in (claim.get("source_refs") or [])))
    scope = {
        "report_id": report.id, "book_ids": book_ids,
        "research_mode": selection.get("research_mode", "adaptive"),
        "evidence_summary": selection.get("evidence_summary", {}),
        "method_profile": selection.get("method_profile", {}),
        "human_review_required": True,
    }
    note = db.scalar(select(KnowledgeNote).where(
        KnowledgeNote.source_report_id == report.id,
        KnowledgeNote.origin == "ai",
    ))
    if req.include_report_note:
        appendix = "\n\n---\n\n## 结构化审查附录\n\n" + json.dumps({
            "evidence_summary": selection.get("evidence_summary", {}),
            "open_questions": selection.get("open_questions", []),
            "hypotheses": selection.get("hypotheses", []),
            "human_review_required": True,
        }, ensure_ascii=False, indent=2)
        if not note:
            note = KnowledgeNote(book_id=book_ids[0], source_report_id=report.id, origin="ai")
            db.add(note)
        note.title = f"批判性审查｜{(report.focus or '综合研读')[:220]}"
        note.content = (report.content or "") + appendix
        note.source_scope_json = json.dumps(scope, ensure_ascii=False)
        note.source_refs_json = json.dumps(all_refs, ensure_ascii=False)
        note.tags_json = json.dumps(["批判性审查", "跨文献综合"], ensure_ascii=False)

    existing_cards = list(db.scalars(select(EvidenceCard).where(
        EvidenceCard.source_report_id == report.id,
        EvidenceCard.origin == "ai",
    ).order_by(EvidenceCard.id)).all())
    relation_labels = {
        "consensus": "共识证据", "complementary": "互补证据", "conflict": "冲突证据",
        "single_source": "单一来源", "unresolved": "未决证据",
    }
    if req.include_claim_cards:
        for claim_index, claim in enumerate(claims):
            claim_text = str(claim.get("claim") or "").strip()
            if not claim_text:
                continue
            relation = str(claim.get("synthesis_relation") or "unresolved")
            evidence = [
                f"证据关系：{relation_labels.get(relation, '未决证据')}",
                f"证据质量：{claim.get('evidence_quality') or 'not_assessed'}",
                f"判断理由：{claim.get('reason') or '未说明'}",
            ]
            if claim.get("counterpoint"):
                evidence.append(f"反例或限制：{claim['counterpoint']}")
            if claim.get("bias_flags"):
                evidence.append("偏倚风险：" + "；".join(claim["bias_flags"]))
            if claim.get("alternative_explanations"):
                evidence.append("竞争解释：" + "；".join(claim["alternative_explanations"]))
            card_scope = {**scope, "claim_index": claim_index, "synthesis_relation": relation,
                          "evidence_quality": claim.get("evidence_quality", "not_assessed")}
            card = existing_cards[claim_index] if claim_index < len(existing_cards) else EvidenceCard(
                source_report_id=report.id, origin="ai")
            card.book_id = _claim_book_id(claim, book_ids); card.title = claim_text[:255]
            card.evidence_text = "\n".join(evidence); card.claim_text = claim_text
            card.source_ref_json = json.dumps({"refs": claim.get("source_refs") or []}, ensure_ascii=False)
            card.source_scope_json = json.dumps(card_scope, ensure_ascii=False)
            card.tags_json = json.dumps(["批判性审查", relation_labels.get(relation, "未决证据")], ensure_ascii=False)
            card.verification_status = claim.get("status") or "needs_review"
            if claim_index >= len(existing_cards):
                db.add(card)
        for stale_card in existing_cards[len(claims):]:
            stale_card.verification_status = "superseded"
    db.commit()
    note = db.scalar(select(KnowledgeNote).where(KnowledgeNote.source_report_id == report.id,
                                                  KnowledgeNote.origin == "ai"))
    cards = list(db.scalars(select(EvidenceCard).where(EvidenceCard.source_report_id == report.id,
                                                        EvidenceCard.origin == "ai")
                            .order_by(EvidenceCard.id)).all())
    return {"report_id": report.id, "note_id": note.id if note else None,
            "evidence_card_ids": [card.id for card in cards], "claim_count": len(cards),
            "human_review_required": True}


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(report_id: int, db: Session = Depends(get_db)):
    """删除一条综合阅读报告。"""
    r = db.get(StudyReport, report_id)
    if not r:
        raise HTTPException(404, "报告不存在")
    db.delete(r)
    db.commit()


# ---------- 思维训练 ----------
class TrainStartReq(BaseModel):
    book_ids: list[int] | None = None
    mode: str = "quiz"  # quiz 出题训练 / free 自由陪练
    topic: str = ""


@router.post("/train/start")
async def train_start(req: TrainStartReq, db: Session = Depends(get_db)):
    cfg = load_llm_config(db, "research")
    if not cfg.get("deepseek_api_key"):
        raise HTTPException(400, "未配置 DeepSeek API Key")
    context = _book_context(db, req.book_ids, limit_per_book=6000)
    if not context:
        raise HTTPException(400, "没有可用的文献")
    sid = uuid.uuid4().hex[:12]
    # 防御：会话内存上限，避免无限轮/反复开新会话累积内存（低内存底线）
    while len(_sessions) >= 100:
        _sessions.pop(next(iter(_sessions)), None)
    _sessions[sid] = {
        "mode": req.mode, "topic": req.topic, "book_ids": req.book_ids or [],
        "context": context, "history": [], "round": 0, "done": False,
    }
    provider = LLMRouter.get("auto", cfg)
    first = await _gen_turn(provider, _sessions[sid], None)
    return {"session_id": sid, "message": first, "round": 0, "done": False}


class TrainAskReq(BaseModel):
    session_id: str
    answer: str


@router.post("/train/ask")
async def train_ask(req: TrainAskReq, db: Session = Depends(get_db)):
    cfg = load_llm_config(db, "research")
    provider = LLMRouter.get("auto", cfg)
    sess = _sessions.get(req.session_id)
    if not sess:
        raise HTTPException(404, "会话不存在或已过期（重启后端后会话丢失）")
    if sess["done"]:
        raise HTTPException(400, "训练已结束，请开启新会话")
    msg = await _gen_turn(provider, sess, req.answer)
    return {"session_id": req.session_id, "message": msg, "round": sess["round"], "done": sess["done"]}


class TrainEndReq(BaseModel):
    session_id: str


@router.post("/train/end", status_code=204)
def train_end(req: TrainEndReq):
    """结束训练并释放会话内存。"""
    _sessions.pop(req.session_id, None)


async def _gen_turn(provider, sess: dict, user_answer: str | None):
    """生成一轮：user_answer 为 None 表示开场问题。"""
    if user_answer is not None:
        sess["history"].append({"role": "user", "content": user_answer})

    if sess["mode"] == "quiz":
        system = (
            "你是思维训练导师（苏格拉底式）。基于文献内容训练用户：\n"
            "- 每次只出 1 道题，题型按顺序递进：概念理解→应用场景→批判思考→跨文献联系→综合\n"
            "- 用户回答后：① 简短评价（对错与不足，30 字内）② 若答错/含糊，追问一次引导 ③ 答得好则出下一题\n"
            "- 不设轮数上限，可持续深入；当用户表示想结束或总结时，给出 100-150 字总结评价（掌握情况 + 建议）并标注【训练结束】\n"
            "- 输出格式：先【评价】再【提问】或【总结】，用中文。"
        )
    else:
        system = (
            "你是文献陪练导师。基于文献内容与用户自由对话：\n"
            "- 主动引导用户深入理解（提问、类比、举例、指出矛盾）\n"
            "- 用户回答后给予反馈并继续深入，像真正的老师一样\n"
            "- 不设轮数上限，可持续深入对话；当用户表示想结束或总结时，总结学习收获并标注【训练结束】\n"
            "- 用中文。"
        )
    topic_hint = f"本次训练主题：{sess['topic']}\n" if sess.get("topic") else ""
    history = sess["history"][-8:]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"文献内容：\n{sess['context'][:8000]}\n\n{topic_hint}开始训练。"},
    ]
    for h in history:
        messages.append({"role": "assistant" if h["role"] == "assistant" else "user", "content": h["content"]})
    if user_answer is None:
        messages.append({"role": "user", "content": "请出第一道题（或开始陪练）。"})

    answer = ""
    try:
        async for delta in provider.stream_chat(messages):
            answer += delta
    except Exception as e:  # noqa: BLE001
        answer = f"⚠️ AI 调用失败：{e}"

    sess["history"].append({"role": "assistant", "content": answer})
    sess["round"] += 1
    if "【训练结束】" in answer:
        sess["done"] = True
    return answer
