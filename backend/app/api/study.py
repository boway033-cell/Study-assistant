"""AI 研读：综合阅读报告 + 思维训练（出题批改追问 / 自由陪练）"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, Chapter, Chunk, EvidenceCard, KnowledgeNote, StudyReport
from backend.app.services.chat_sessions import delete_session, load_session, save_session
from backend.app.services.llm import LLMRouter, load_llm_config

router = APIRouter(prefix="/api/study", tags=["study"])


# ---------- 会话（落库持久化，单用户）----------
# 多轮训练状态存在 chat_sessions 表（services/chat_sessions.py）；此前放在模块级 dict，
# 重启即报 404，用户会丢掉整段多轮历史。


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
    profile_id: int | None = None
    budget_max_tokens: int = Field(default=0, ge=0, le=2_000_000)
    budget_max_calls: int = Field(default=0, ge=0, le=200)
    route_signature: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def _overview_budget(req: StudyOverviewReq, db: Session, *, cfg: dict | None = None) -> dict:
    """提交前用数据库里的实际材料范围估算；不把估算冒充账单。"""
    from math import ceil
    from backend.app.services.long_research import scope_size
    from backend.app.services.llm.budget import estimate_tokens
    from backend.app.models import Setting

    books = db.scalars(select(Book).where(Book.id.in_(req.book_ids))).all()
    if len(books) != len(set(req.book_ids)) or any(book.status != "ready" for book in books):
        raise HTTPException(400, "所选资料不存在或尚未解析完成")
    if req.chapter_ids:
        rows = db.execute(select(Chapter.id, Chapter.book_id, Chapter.parent_id)
                          .where(Chapter.book_id.in_(req.book_ids))).all()
        selected = set(req.chapter_ids)
        if not selected.issubset({row.id for row in rows}):
            raise HTTPException(400, "所选章节不属于当前资料")
        while True:
            expanded = selected | {row.id for row in rows if row.parent_id in selected}
            if expanded == selected:
                break
            selected = expanded
        chapter_ids = list(selected)
    else:
        chapter_ids = []
    _, corpus_chars = scope_size(db, req.book_ids, chapter_ids)
    notes = db.scalars(select(KnowledgeNote).where(KnowledgeNote.id.in_(req.note_ids))).all() if req.note_ids else []
    if len(notes) != len(set(req.note_ids)) or any(note.book_id not in req.book_ids for note in notes):
        raise HTTPException(400, "所选笔记不属于当前资料")
    note_chars = sum(min(2000, len(note.content or "")) for note in notes)
    full_scope = req.reasoning_depth == "deep" and not (req.note_ids and not req.chapter_ids)
    long_reading = full_scope and corpus_chars > 24000
    batches = ceil(corpus_chars / 11000) if long_reading else 0
    sections = max(2, min(6, ceil(req.target_length / 2000))) if long_reading or req.target_length > 4500 else 1
    calls = batches + (1 if req.reasoning_depth == "deep" else 0) + sections + 1
    if long_reading:
        calls += ceil(batches / 18)  # 中间证据地图压缩；具体次数取决于模型输出
    context_chars = min(corpus_chars, 48000) if full_scope else min(corpus_chars, 42000)
    if req.note_ids and not req.chapter_ids:
        context_chars = note_chars
    if long_reading:
        context_chars = min(24000, batches * 1200)
    # Long reads can restore up to 12,000 original characters for planned
    # evidence needs. Include that material in every section and final audit.
    candidate_chars = 12000 if long_reading else 0
    input_chars = (batches * 12500 + (context_chars + candidate_chars) * sections
                   + min(context_chars, 18000) * int(req.reasoning_depth == "deep")
                   + candidate_chars * int(sections > 1))
    input_chars += note_chars * max(1, sections) + len(req.focus + req.framework) * max(1, calls) + calls * 2400
    input_chars += min(corpus_chars + note_chars, 26000) + req.target_length
    output_chars = batches * 1200 + req.target_length * (1.5 if sections > 1 else 1.8) + calls * 350
    input_tokens, output_tokens = estimate_tokens(input_chars), estimate_tokens(int(output_chars))
    cfg = cfg if cfg is not None else load_llm_config(db, "research")
    try:
        rates = json.loads((db.get(Setting, "ai_price_rates") or Setting(value="{}")).value)
    except (ValueError, TypeError):
        rates = {}
    rate = rates.get(f"{cfg.get('provider_id')}:{cfg.get('model')}", {}) if isinstance(rates, dict) else {}
    try:
        price = round((input_tokens * float(rate["input"]) + output_tokens * float(rate["output"])) / 1_000_000, 4)
    except (KeyError, TypeError, ValueError):
        price = None
    selected_document_chars = 0 if req.note_ids and not req.chapter_ids else corpus_chars
    return {"material_chars": selected_document_chars + note_chars, "document_chars": selected_document_chars,
            "note_chars": note_chars, "estimated_calls": calls, "estimated_input_tokens": input_tokens,
            "estimated_output_tokens": output_tokens, "estimated_tokens": input_tokens + output_tokens,
            "estimated_cost_cny": price, "provider_id": cfg.get("provider_id"),
            "provider_name": cfg.get("provider_name"), "model": cfg.get("model"),
            "configured": bool(cfg.get("configured")),
            "boundary": "Token 按约 2 字/Token 偏保守估算；重试、回退、模型内部推理与供应商实际分词可能增加用量。费用仅使用你填写的费率。"}


def _route_signature(cfg: dict) -> str:
    """Fingerprint the effective route without storing a decrypted API key."""
    route = [cfg, *(cfg.get("fallbacks") or [])]
    identity = [{key: item.get(key) for key in (
        "provider_id", "protocol", "base_url", "model", "deepseek_model", "api_key")}
        for item in route]
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _checked_research_config(db: Session, expected_signature: str | None) -> dict:
    cfg = load_llm_config(db, "research")
    if expected_signature is not None and _route_signature(cfg) != expected_signature:
        raise ValueError("研读模型连接在任务排队期间发生变化；资料尚未发送，请重新预估并提交")
    return cfg


@router.post("/overview/estimate")
def estimate_overview(req: StudyOverviewReq, db: Session = Depends(get_db)):
    if not req.book_ids:
        raise HTTPException(422, "请至少选择一本研读文献")
    cfg = load_llm_config(db, "research")
    return {**_overview_budget(req, db, cfg=cfg), "route_signature": _route_signature(cfg)}


_MODE_GUIDANCE = {
    "adaptive": "先识别资料类型与研究问题性质，再自主选择最有解释力的分析维度和报告结构；不要套用固定论文或期刊模板。",
    "comparative": "优先比较概念定义、核心主张、论证机制、证据类型、适用边界与相互冲突，但只保留对当前材料真正有用的维度。",
    "critical": "区分原始材料、作者解释与模型推断；检查证据强度、替代解释、反例、方法限制和因果外推。",
    "gap": "梳理已有共识与分歧，识别材料尚未回答的问题、证据缺口和可继续研究的方向，避免把未知包装成结论。",
}


# 端点静默看门狗：只用来发现「连接卡死」，不是性能预算，也不是首字预算。
# 2026-09-11 实测当前活动模型（DashScope 兼容模式 qwen3.8-max-0902）原始 SSE：
#   首帧 reasoning_content 2.2s 到达 → 流式思考到 133.5s → 首帧正文才出现，
#   单次 306s 输出 12855 字正文（另有 7355 字 reasoning 被丢弃）。
# 即「正文首字」与端点是否活着完全无关：思考型模型会先长时间只吐 reasoning 增量。
# 因此存活判据只能用 provider.last_delta_at（任何一帧，含思考帧），
# 绝不能用「answer 是否增长」——那正是长报告反复报「连续120秒未返回内容」的根因。
# 上界受外层 asyncio.timeout(900) 约束：3 次尝试 × 240s + 退避 6s = 726s < 900s。
FIRST_TOKEN_TIMEOUT = 240


def _retryable_generation_error(exc: Exception) -> bool:
    """只重试网络抖动与限流；证书、鉴权和内容错误应立即报告。"""
    if isinstance(exc, ValueError):
        return False
    detail = f"{type(exc).__name__}: {exc}".lower()
    if any(marker in detail for marker in ("certificate verify failed", "sslcertverificationerror",
                                            "http 400", "http 401", "http 403", "invalid_api_key")):
        return False
    return any(marker in detail for marker in ("timeout", "timed out", "模型连续", "connecterror",
                                               "connection", "getaddrinfo", "http 429", "http 502",
                                               "http 503", "http 504", "rate limit"))


async def _stream_answer(provider, messages: list[dict], error_prefix: str, on_progress=None, on_text=None) -> str:
    """统一流式调用与短暂故障重试；不在内存保留模型隐性推理过程。"""
    import asyncio

    last_err = ""
    for attempt in range(2):
        answer = ""
        last_notified_at = time.monotonic()
        last_notified_size = 0
        try:
            iterator = provider.stream_chat(messages).__aiter__()
            pending = None
            try:
                async with asyncio.timeout(900):
                    while True:
                        try:
                            pending = asyncio.create_task(anext(iterator))
                            started = time.monotonic()
                            while not pending.done():
                                done, _ = await asyncio.wait({pending}, timeout=2)
                                if done:
                                    break
                                if on_progress:
                                    on_progress(len(answer))
                                if time.monotonic() - started >= FIRST_TOKEN_TIMEOUT:
                                    # 端点还在推帧（思考增量也算）就只是「模型在思考」，重置窗口继续等；
                                    # 完全没有帧才算连接卡死。
                                    alive_at = getattr(provider, 'last_delta_at', 0.0)
                                    if time.monotonic() - alive_at < FIRST_TOKEN_TIMEOUT:
                                        started = alive_at
                                    else:
                                        raise TimeoutError(
                                            f'模型连续{FIRST_TOKEN_TIMEOUT}秒无任何响应（既无正文也无思考增量）')
                            delta = pending.result()
                        except StopAsyncIteration:
                            break
                        answer += delta
                        if len(answer) > 80000:
                            raise ValueError('模型输出超出单次安全长度')
                        now = time.monotonic()
                        if now - last_notified_at >= 1.5 or len(answer) - last_notified_size >= 800:
                            if on_progress:
                                on_progress(len(answer))
                            if on_text:
                                on_text(answer)
                            last_notified_at = now
                            last_notified_size = len(answer)
            finally:
                if pending and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                if hasattr(iterator, 'aclose'):
                    await iterator.aclose()
            if answer.strip():
                if on_progress:
                    on_progress(len(answer))
                if on_text:
                    on_text(answer)
                return answer
            last_err = "AI 返回为空"
        except Exception as exc:  # noqa: BLE001
            if exc.__class__.__name__ in {"TaskCancelled", "BudgetExceeded"}:
                raise
            if answer and on_text:
                on_text(answer)
                raise RuntimeError(f'{error_prefix}：连接中断，已接收的草稿保留在任务中') from exc
            last_err = str(exc)
            if not _retryable_generation_error(exc):
                break
        if attempt < 1:
            await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{error_prefix}：{last_err}")


def _writing_stage(provider, label: str, received: int) -> str:
    """思考型模型产出正文前会长时间只吐推理增量，给用户一个「端点还活着」的可见信号。

    否则界面会长时间停在「已接收 0 字」，用户无法区分「正在深度推理」与「已经卡死」。
    """
    if received:
        return label
    thinking = getattr(provider, "reasoning_chars", 0)
    return f"{label}：模型正在推理（已思考 {thinking} 字）" if thinking else label


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


def _writing_section_plan(outlines: list[str], count: int) -> list[dict]:
    """Assign distinct argument topics to bounded prose calls."""
    topics = [str(item).strip() for item in outlines if str(item).strip()] or [
        "提出中心论题", "比较证据与解释", "综合判断与结论",
    ]
    count = max(1, count)
    sections = []
    if len(topics) >= count:
        for index in range(count):
            start = index * len(topics) // count
            end = (index + 1) * len(topics) // count
            sections.append({"focus": topics[start:end], "part": 1, "parts": 1,
                             "covered": topics[:start], "upcoming": topics[end:]})
    else:
        assignments = [min(len(topics) - 1, index * len(topics) // count)
                       for index in range(count)]
        for index, topic_index in enumerate(assignments):
            sections.append({
                "focus": [topics[topic_index]],
                "part": assignments[:index].count(topic_index) + 1,
                "parts": assignments.count(topic_index),
                "covered": topics[:topic_index],
                "upcoming": topics[topic_index + 1:],
            })
    return sections


def _source_anchor(item: dict) -> str:
    if item.get("canonical_ref"):
        return str(item["canonical_ref"])
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


def _format_retrieved_context(items: list[dict], max_chars: int = 22000,
                              per_item_chars: int = 3200) -> tuple[str, set[str]]:
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
        body = str(item.get("context") or item.get("snippet") or "").strip()[:per_item_chars]
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


def _retrieve_research_candidates(retrieve, focus: str, plan: dict,
                                  book_ids: list[int], *, allowed_refs: set[str] | None = None,
                                  needs_only: bool = False, max_needs: int = 3,
                                  max_sources_per_need: int | None = None,
                                  top_k: int = 6) -> tuple[list[dict], list[dict]]:
    """Reserve a bounded search pass for planned evidence needs.

    A hit is a candidate passage, not proof that the need was met. Every
    retrieval is scoped to a book explicitly selected by the user.
    """
    needs = list(dict.fromkeys(str(value).strip() for value in plan.get("evidence_needs", [])
                               if str(value).strip()))[:max_needs]
    # Reserve room for every planned need. A fixed six-hit cap lets early
    # needs consume all candidates before later needs are checked.
    per_book_cap = max(6, len(needs) * (max_sources_per_need or 2)) if needs else 5
    base_queries = ([] if needs_only else
                    list(dict.fromkeys([focus.strip(), *plan.get("subquestions", [])[:2]])))
    # A vector hit may omit chapter/page metadata. Chunk IDs identify the exact
    # passages already read, so reuse their canonical anchors to keep selection
    # boundaries and citations intact.
    allowed_by_chunk: dict[int, str] | None = None
    if allowed_refs is not None:
        allowed_by_chunk = {}
        for ref in allowed_refs:
            chunk_id = ref.rpartition(":C")[2]
            if chunk_id.isdigit():
                allowed_by_chunk[int(chunk_id)] = ref
    accepted = {book_id: 0 for book_id in book_ids}
    seen: set[int] = set()
    items: list[dict] = []
    checks = [{"need": need, "status": "no_candidate", "source_refs": []} for need in needs]

    def add_candidates(query: str, book_id: int, *, check: dict | None = None,
                       per_query: int = 2) -> None:
        if (not query or accepted[book_id] >= per_book_cap or
                (check is not None and max_sources_per_need is not None and
                 len(check["source_refs"]) >= max_sources_per_need)):
            return
        added = 0
        for item in retrieve(query, book_ids=[book_id], top_k=top_k):
            chunk_id = int(item.get("chunk_id") or 0)
            if not chunk_id:
                continue  # A directory outline is not passage evidence.
            if allowed_by_chunk is not None:
                canonical_ref = allowed_by_chunk.get(chunk_id)
                if not canonical_ref or not canonical_ref.startswith(f"B{book_id}:"):
                    continue
                item = {**item, "canonical_ref": canonical_ref}
            if check is not None:
                ref = _source_anchor(item)
                if ref not in check["source_refs"]:
                    check["source_refs"].append(ref)
                    check["status"] = "candidate_found"
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            items.append(item)
            accepted[book_id] += 1
            added += 1
            if (added >= per_query or accepted[book_id] >= per_book_cap or
                    (check is not None and max_sources_per_need is not None and
                     len(check["source_refs"]) >= max_sources_per_need)):
                break

    for check in checks:
        for book_id in book_ids:
            add_candidates(check["need"], book_id, check=check, per_query=1)
    for book_id in book_ids:
        for query in base_queries:
            add_candidates(query, book_id, per_query=2 if needs else 5)
    return items, checks


def _prepare_research_evidence(retrieve, selected_context: str, overview_context: str,
                               allowed_refs: set[str], focus: str, plan: dict,
                               book_ids: list[int], *, full_scope: bool,
                               long_reading: bool, chapter_ids: list[int] | None = None
                               ) -> tuple[str, set[str], list[dict]]:
    """Check planned needs even after a full read, without widening its scope."""
    if not selected_context:
        retrieved, checks = _retrieve_research_candidates(retrieve, focus, plan, book_ids)
        evidence_context, refs = _format_retrieved_context(retrieved, max_chars=42000)
        for check in checks:
            check["source_refs"] = [ref for ref in check["source_refs"] if ref in refs]
            if not check["source_refs"]:
                check["status"] = "no_candidate"
        return evidence_context or overview_context[:22000], refs, checks

    evidence_context = selected_context
    if not full_scope or not plan.get("evidence_needs"):
        return evidence_context, allowed_refs, []

    retrieved, checks = _retrieve_research_candidates(
        retrieve, focus, plan, book_ids, allowed_refs=allowed_refs,
        needs_only=True, max_needs=6, max_sources_per_need=2,
        top_k=24 if chapter_ids else 6,
    )
    if long_reading:
        # The full source passed through map/reduce; restore bounded original
        # passages for evidence needs that compression may have omitted.
        raw_context, packed_refs = _format_retrieved_context(
            retrieved, max_chars=12000, per_item_chars=1400)
        if raw_context:
            evidence_context += "\n\n【证据需求的原文候选（仅来自已读范围）】\n" + raw_context
        usable_refs = packed_refs
    else:
        # Short full reads already contain the original passages.
        usable_refs = allowed_refs
    for check in checks:
        check["source_refs"] = [ref for ref in check["source_refs"] if ref in usable_refs]
        if not check["source_refs"]:
            check["status"] = "no_candidate"
    return evidence_context, allowed_refs, checks


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


async def _perform_source_audit(provider, draft: str, packet: dict, mode: str,
                                citation_notes: list[dict] | None = None, on_progress=None):
    from backend.app.services import research_audit
    if not packet.get("entries"):
        raise ValueError("没有可定位的原文片段；正文已保留，请补充来源后核查")
    answer = await _stream_answer(
        provider, research_audit.audit_messages(draft, packet, mode, citation_notes),
        "主张核查失败", on_progress=on_progress,
    )
    parsed = research_audit.parse_audit(answer)
    refs = {item["source_ref"] for item in packet["entries"]}
    claims = research_audit.verify_quotes(
        _normalize_claims(parsed["claims"], refs), parsed["claims"], packet,
    )
    return parsed, claims


async def run_overview(record, book_ids: list[int], focus: str = "", framework: str = "",
                       chapter_ids: list[int] | None = None, note_ids: list[int] | None = None,
                       research_mode: str = "adaptive", reasoning_depth: str = "deep",
                       writing_style: str = "analytical_essay", extension_level: str = "exploratory",
                       target_length: int = 3000, profile_id: int | None = None,
                       expected_route_signature: str | None = None) -> dict:
    from backend.app.core.database import SessionLocal
    from backend.app.services import research_audit
    from backend.app.worker.tasks import update_progress

    db = SessionLocal()
    try:
        # Writing DNA（可选）：只校准表达与结构习惯，不作为事实来源
        from backend.app.services.writing_lab import (AI_TONE_OUTPUT_BANS, ai_flavor_violations,
                                                      dna_style_context)
        dna_context, dna_version = dna_style_context(db, profile_id) if profile_id else ("", None)
        update_progress(record, 0.15, "overview", "正在汇总文献内容...")
        scope = set(book_ids)
        requested_chapters = list(chapter_ids or [])
        if chapter_ids:
            # A selected parent chapter includes its subsections within these books.
            parents = db.execute(select(Chapter.id, Chapter.parent_id).where(Chapter.book_id.in_(scope))).all()
            selected = set(chapter_ids)
            if not selected.issubset({cid for cid, _ in parents}):
                raise ValueError('所选章节已失效或不属于当前文献范围，请重新选择；未自动扩大到整本书')
            while True:
                expanded = selected | {cid for cid, parent in parents if parent in selected}
                if expanded == selected:
                    break
                selected = expanded
            chapter_ids = sorted(selected)
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
            chunks = db.scalars(select(Chunk).where(Chunk.chapter_id == chapter.id).order_by(Chunk.chunk_index)).yield_per(32)
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
        cfg = _checked_research_config(db, expected_route_signature)
        if not cfg.get("configured"):
            raise ValueError("研究模型尚未配置或不可用，请在设置中选择并检测一个模型")
        provider = LLMRouter.get("auto", cfg)
        from backend.app.services.long_research import scope_size, read_long_scope, iter_evidence_batches
        _, corpus_chars = scope_size(db, book_ids, chapter_ids)
        full_scope = reasoning_depth == 'deep' and not (note_ids and not chapter_ids)
        long_reading = full_scope and corpus_chars > 24000
        coverage = None
        if long_reading:
            record.result = {'kind': 'study-report', 'book_ids': book_ids, 'focus': focus[:200]}
            def reading_progress(done, total, message):
                record.result['reading_coverage'] = {'processed_chars': done, 'total_chars': total}
                update_progress(record, .16 + .14 * done / max(total, 1), 'evidence-reading', message)
            selected_context, allowed_refs, coverage = await read_long_scope(
                db, provider, book_ids, chapter_ids, focus, _source_anchor, _stream_answer, reading_progress)
            # Notes selected with chapters remain part of the authorized input.
            for note in selected_notes:
                if note.book_id in scope:
                    anchor = f'B{note.book_id}:NOTE{note.id}'
                    selected_context += f'\n[{anchor}]用户笔记：{note.content[:2000]}'
                    allowed_refs.add(anchor)
            overview_context = selected_context
        elif full_scope and corpus_chars:
            parts = []
            for body, refs, _ in iter_evidence_batches(db, book_ids, chapter_ids, _source_anchor):
                parts.append(body)
                allowed_refs.update(refs)
            # Preserve any explicitly selected notes as well as the full text.
            for note in selected_notes:
                if note.book_id in scope:
                    anchor = f'B{note.book_id}:NOTE{note.id}'
                    parts.append(f'[{anchor}]用户笔记：{note.content[:2000]}')
                    allowed_refs.add(anchor)
            selected_context = '\n\n'.join(parts)
            overview_context = selected_context
            coverage = {'processed_chars': corpus_chars, 'total_chars': corpus_chars}
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
                    _writing_stage(provider, f"正在形成研究路径（已接收 {chars} 字）", chars) + "...",
                ),
            )
            plan = _normalize_plan(parse_json_response(raw_plan), research_mode)
        record.result = {
            "kind": "study-report",
            "book_ids": book_ids,
            "focus": focus[:200],
            "research_plan": plan,
            "reading_coverage": coverage,
        }
        update_progress(record, 0.46, "research-plan", "研究路径已形成，准备检索证据", force=True)

        # 全文研读后仍核对研究计划中的证据需求；已选章节只接受本次读过的文本块。
        # 仅选笔记时不启动书目检索，避免把未经选择的正文混入材料。
        if not selected_context or (full_scope and plan.get("evidence_needs")):
            update_progress(record, 0.52, "evidence", "正在按研究路径核对证据需求...")
        from backend.app.services.rag import retriever
        evidence_context, allowed_refs, evidence_need_checks = _prepare_research_evidence(
            retriever.retrieve, selected_context, overview_context, allowed_refs,
            focus, plan, book_ids, full_scope=full_scope, long_reading=long_reading,
            chapter_ids=chapter_ids,
        )
        plan["evidence_need_checks"] = evidence_need_checks

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
                "证据需求的检索状态只表示是否找到候选片段，不代表主张已获支持；未找到候选时收缩相应判断并列入待核查问题。\n"
                f"写作形态：{'连贯分析文章' if writing_style == 'analytical_essay' else '结构化研究报告'}；"
                f"推演自由度：{'允许有标识的探索性延伸' if extension_level == 'exploratory' else '以直接证据解释为主'}；"
                f"目标长度：约 {target_length} 字。\n"
                + (f"【Writing DNA：只约束表达与结构习惯，不得作为事实来源】\n{dna_context}\n" if dna_context else "")
                + AI_TONE_OUTPUT_BANS + "\n\n"
                f"可引用材料：\n{evidence_context[:48000]}"
            )},
        ]
        def save_draft(text):
            record.result['draft_markdown'] = text
            update_progress(record, record.progress, 'synthesis',
                            f'正在写作，已保存 {len(text)} 字草稿', force=True)

        # 长短报告的分界保持 4500：这里区分的是「成文方式」（单次综合 vs 分段成文），
        # 与首字延迟无关——实测首字延迟只跟模型/端点有关，跟提示词长度无关（见 FIRST_TOKEN_TIMEOUT）。
        # 长报告分段的目的是避免把整篇正文塞进一个受输出上限约束的大 JSON，
        # 并让每段都能独立保存草稿；失败重试也因此是逐段的。
        if long_reading or target_length > 4500:
            # Long prose is not wrapped in one fragile, output-limit-sized JSON.
            # Write bounded sections and retain every completed section on disk.
            count = max(2, min(6, (target_length + 1999) // 2000))
            outlines = plan.get('report_outline') or ['提出中心论题', '展开证据与比较', '综合解释与结论']
            sections = _writing_section_plan(outlines, count)
            plan['writing_sections'] = [
                {'focus': item['focus'], 'part': item['part'], 'parts': item['parts']}
                for item in sections
            ]
            prose_prompt = [dict(item) for item in prompt]
            prose_prompt[0]['content'] = prose_prompt[0]['content'].split('只输出 JSON 对象：')[0] + (
                '只输出 Markdown 正文，不输出 JSON、主张清单或写作说明。证据来自分批阅读的笔记，'
                '保留有效来源锚点，不能把笔记中的解释当作原文直接引语。')
            pieces = []
            for index in range(count):
                preceding = '\n\n'.join(pieces)
                section = sections[index]
                section_prompt = [*prose_prompt, {'role': 'user', 'content': (
                    f'全文共{count}段写作任务，现在写第{index + 1}段，约{target_length // count}字。'
                    f'本段只承担这些论证主题：{json.dumps(section["focus"], ensure_ascii=False)}。'
                    f'当前主题内第{section["part"]}/{section["parts"]}段；'
                    f'已覆盖主题：{json.dumps(section["covered"], ensure_ascii=False)}；'
                    f'后续主题：{json.dumps(section["upcoming"], ensure_ascii=False)}。'
                    '按顺序推进整篇论证，不复述已写内容，不提前代写后续主题；只在最后一段收束全文。'
                    f'已完成正文末尾：\n{preceding[-3500:]}'
                )}]
                piece = await _stream_answer(provider, section_prompt, '分段成文失败',
                    on_progress=lambda chars: update_progress(record,
                        .70 + .24 * (index + min(chars / max(target_length // count, 1), .95)) / count,
                        'synthesis', _writing_stage(provider, f'正在写作第 {index + 1}/{count} 段', chars)),
                    on_text=lambda text: save_draft(preceding + '\n\n' + text))
                pieces.append(piece)
            report_text = '\n\n'.join(pieces)
            answer = json.dumps({'report_markdown': report_text, 'claims': []}, ensure_ascii=False)
        else:
            answer = await _stream_answer(
                provider,
                prompt,
                "综合研读失败",
                lambda chars: update_progress(
                    record,
                    min(0.94, 0.7 + 0.24 * min(chars / max(target_length * 1.35, 1600), 1)),
                    "synthesis",
                    _writing_stage(provider, f"AI 正在组织论证与写作（已接收 {chars} 字）", chars) + "...",
                ),
            )

        from backend.app.services.llm import parse_json_response
        try:
            parsed = parse_json_response(answer)
        except Exception:  # 兼容旧模型偶发返回纯 Markdown；报告可保存，但主张均留待人工整理。
            parsed = None
        if isinstance(parsed, dict):
            report_content = str(parsed.get("report_markdown") or "").strip()
            if not report_content:
                raise RuntimeError("模型未返回报告正文；未保存格式损坏的结果")
            raw_claims = parsed.get("claims") if isinstance(parsed.get("claims"), list) else []
            open_questions = research_audit.open_questions(parsed.get('open_questions'))
            raw_hypotheses = parsed.get("hypotheses") if isinstance(parsed.get("hypotheses"), list) else []
        else:
            if answer.lstrip().startswith(("{", "```json", "```JSON")):
                raise RuntimeError("模型返回的报告 JSON 无法解析；未保存格式损坏的结果")
            report_content, raw_claims = answer, []
            open_questions = []
            raw_hypotheses = []
        raw_report_content = report_content
        packet = research_audit.source_packet(db, report_content, allowed_refs, book_ids)
        db.rollback()
        source_audit = {"version": research_audit.AUDIT_VERSION, "packet": packet,
                        "report_hash": research_audit.text_hash(report_content),
                        "status": "running", "task_id": getattr(record, 'id', None),
                        "human_review_required": True}
        claims = _normalize_claims(raw_claims, allowed_refs)
        for claim in claims:
            claim.update(status='needs_review', confidence='low', human_review_required=True)
        hypotheses = _normalize_hypotheses(raw_hypotheses, allowed_refs)
        report_content, citation_notes = readable_citations(
            report_content, valid_anchors=allowed_refs, labels=citation_labels,
        )
        tone_violations = ai_flavor_violations(report_content)
        # 持久化报告及其选择范围/核验主张，不复制原文附件。
        from backend.app.models import StudyReport
        report = StudyReport(
            book_ids_json=json.dumps(book_ids or [], ensure_ascii=False),
            selection_json=json.dumps({
                "chapter_ids": requested_chapters, "note_ids": note_ids or [],
                "research_mode": research_mode, "reasoning_depth": reasoning_depth,
                "writing_style": writing_style, "extension_level": extension_level,
                "target_length": target_length,
                "dna_profile_id": profile_id, "dna_version": dna_version,
                "ai_tone_violations": tone_violations,
                "reading_coverage": coverage,
                "citation_notes": citation_notes,
                "audit_source_text": raw_report_content, "source_audit": source_audit,
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
        db.flush()
        report_id = report.id
        baseline = (report.content, report.claims_json)
        # Commit the completed prose before the final model call so cancellation,
        # process interruption and an invalid audit response cannot lose it.
        db.commit()
        record.result['report_id'] = report_id
        update_progress(record, .95, 'source-audit', '正文已保存，正在核对原文与论证', force=True)
        try:
            checked, checked_claims = await _perform_source_audit(
                provider, raw_report_content, packet, research_mode,
                on_progress=lambda _: update_progress(
                    record, .95, 'source-audit', '正文已保存，正在核对原文与论证'),
            )
            db.expire_all()
            report = db.get(StudyReport, report_id)
            if not report:
                raise ValueError('报告已删除，核查结果未写入')
            current = json.loads(report.selection_json or '{}')
            if baseline != (report.content, report.claims_json) or current.get('claims_reviewed_at'):
                raise ValueError('核查期间人工复核发生变化，已保留你的修改；请重新核查')
            research_audit.validate_packet(db, packet, book_ids)
            source_audit.update(status='complete', checked_at=datetime.now(timezone.utc).isoformat(),
                                logic_review=research_audit.logic_review(checked.get('logic_review')))
            current.update(source_audit=source_audit,
                           open_questions=research_audit.open_questions(checked.get('open_questions')),
                           evidence_summary=_evidence_summary(checked_claims),
                           hypotheses=_normalize_hypotheses(checked.get('hypotheses'), allowed_refs))
            claims = checked_claims
            hypotheses = current['hypotheses']
            report.claims_json = json.dumps(claims, ensure_ascii=False)
            report.selection_json = json.dumps(current, ensure_ascii=False)
            db.commit()
        except (Exception, asyncio.CancelledError) as exc:
            db.rollback()
            db.expire_all()
            report = db.get(StudyReport, report_id)
            if report:
                current = json.loads(report.selection_json or '{}')
                state = current.get('source_audit') or {}
                if state.get('task_id') == getattr(record, 'id', None):
                    state.update(status='failed', error=str(exc)[:500] or '核查任务已中断')
                    current['source_audit'] = state
                    current['open_questions'] = research_audit.open_questions(
                        [*research_audit.open_questions(current.get('open_questions')),
                         '原文与主张核查未完成；可单独重跑核查。'])
                    report.selection_json = json.dumps(current, ensure_ascii=False)
                    db.commit()
            if not report or isinstance(exc, asyncio.CancelledError) or exc.__class__.__name__ == 'TaskCancelled':
                raise
        update_progress(record, 1.0, "overview", "完成", force=True)
        return {"report_id": report_id, "chars": len(report_content), "claims": len(claims),
                "hypotheses": len(hypotheses), "plan_steps": len(plan.get("subquestions", []))}
    finally:
        db.close()


@router.post("/overview", status_code=202)
def study_overview(req: StudyOverviewReq, db: Session = Depends(get_db)):
    from backend.app.worker.tasks import submit
    if not req.book_ids:
        raise HTTPException(422, "请至少选择一本研读文献")
    if not req.focus.strip():
        raise HTTPException(422, "请先写明研究问题")
    cfg = load_llm_config(db, "research")
    if req.route_signature is not None and req.route_signature != _route_signature(cfg):
        raise HTTPException(409, "研究模型连接在预估后发生变化；请重新预估并确认用量")
    estimate = _overview_budget(req, db, cfg=cfg)
    if not estimate["configured"]:
        raise HTTPException(400, "研究模型尚未配置，请先在设置中添加并检测模型")
    if not req.budget_max_tokens or not req.budget_max_calls:
        raise HTTPException(409, "请先查看用量预估并确认本次 Token 与调用上限")
    if req.budget_max_tokens < estimate["estimated_tokens"] or req.budget_max_calls < estimate["estimated_calls"]:
        raise HTTPException(409, "本次上限低于估算需求，请调整范围或提高上限")
    if req.profile_id is not None:
        from backend.app.models import WritingDnaProfile
        dna_profile = db.get(WritingDnaProfile, req.profile_id)
        if not dna_profile or dna_profile.status != "ready":
            raise HTTPException(400, "所选 Writing DNA 尚未就绪")
    expected_route_signature = _route_signature(cfg)
    record = submit("study-overview", lambda rec: run_overview(
        rec, req.book_ids, req.focus, req.framework, req.chapter_ids, req.note_ids,
        req.research_mode, req.reasoning_depth, req.writing_style, req.extension_level,
        req.target_length, req.profile_id, expected_route_signature,
    ), book_id=req.book_ids[0], budget_max_tokens=req.budget_max_tokens,
       budget_max_calls=req.budget_max_calls)
    return {"task_id": record.id}


def _report_payload(r, include_content: bool = True) -> dict:
    selection = json.loads(r.selection_json or "{}")
    source_audit = {key: value for key, value in (selection.get("source_audit") or {}).items()
                    if key != "packet"}
    packet = (selection.get("source_audit") or {}).get("packet") or {}
    source_audit['included_refs'] = packet.get('included_refs', 0)
    source_audit['requested_refs'] = packet.get('requested_refs', 0)
    source_audit['cited_refs'] = packet.get('cited_refs', 0)
    source_audit['included_cited_refs'] = packet.get('included_cited_refs', 0)
    if not source_audit.get('status'):
        source_audit['status'] = 'not_checked'
    if source_audit.get('status') in {'queued', 'running'}:
        from backend.app.worker.tasks import get_task
        task = get_task(source_audit.get('task_id') or '')
        if not task or task.status in {'failed', 'cancelled'}:
            source_audit.update(status='failed', error='核查任务已中断，可单独重跑核查')
    public_selection = {key: value for key, value in selection.items()
                        if key not in {'audit_source_text', 'source_audit'}}
    public_selection['source_audit'] = source_audit
    payload = {
        "id": r.id, "book_ids": json.loads(r.book_ids_json or "[]"),
        "focus": r.focus or "", "framework": r.framework or "", "selection": public_selection,
        "source_audit": source_audit,
        "research_plan": selection.get("research_plan", {}),
        "reading_coverage": selection.get("reading_coverage"),
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


def _saved_audit_input(db, report):
    from backend.app.services import research_audit
    selection = json.loads(report.selection_json or '{}')
    books = json.loads(report.book_ids_json or '[]')
    draft = selection.get('audit_source_text') or report.content
    state = selection.get('source_audit') or {}
    packet = state.get('packet')
    needs_packet = not packet or not packet.get('entries')
    legacy = bool(state.get('legacy_source_version')) or needs_packet
    if needs_packet:
        refs = {str(item.get('anchor') or '') for item in selection.get('citation_notes', [])}
        for claim in json.loads(report.claims_json or '[]'):
            refs.update(claim.get('source_refs') or [])
        packet = research_audit.source_packet(db, draft, refs, books)
    research_audit.validate_packet(db, packet, books)
    return selection, books, draft, packet, legacy


def _audit_estimate(db, report):
    from backend.app.services.research_audit import audit_messages
    from backend.app.services.llm.budget import estimate_tokens
    from backend.app.worker.tasks import get_task
    selection, _, draft, packet, legacy = _saved_audit_input(db, report)
    state = selection.get('source_audit') or {}
    if state.get('status') in {'queued', 'running'}:
        task = get_task(state.get('task_id') or '')
        if task and task.status in {'pending', 'running', 'cancelling'}:
            raise ValueError('这份报告正在核查，请等待当前任务结束')
    messages = audit_messages(draft, packet, selection.get('research_mode', 'adaptive'),
                              selection.get('citation_notes'))
    tokens = estimate_tokens(sum(len(item['content']) for item in messages) + 4000)
    cfg = load_llm_config(db, 'research')
    return {"estimated_tokens": tokens + 3000, "estimated_calls": 1,
            "material_chars": sum(len(item['text']) for item in packet['entries']),
            "provider_name": cfg.get('provider_name'), "model": cfg.get('model'),
            "configured": bool(cfg.get('configured')), "route_signature": _route_signature(cfg),
            "boundary": ('旧报告首次核查使用当前原文，无法确认生成时的来源版本。' if legacy else
                         '核查使用生成时保留的原文片段；仅重跑核查，保留正文和人工复核结果。')}


@router.post('/reports/{report_id}/audit/estimate')
def estimate_report_audit(report_id: int, db: Session = Depends(get_db)):
    report = db.get(StudyReport, report_id)
    if not report:
        raise HTTPException(404, '报告不存在')
    try:
        return _audit_estimate(db, report)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class StudyAuditReq(BaseModel):
    budget_max_tokens: int = Field(ge=1, le=2_000_000)
    budget_max_calls: int = Field(ge=1, le=10)
    route_signature: str = Field(pattern=r'^[0-9a-f]{64}$')


async def run_report_audit(record, report_id: int, expected_route_signature: str):
    from backend.app.core.database import SessionLocal
    from backend.app.services import research_audit
    from backend.app.worker.tasks import update_progress
    with SessionLocal() as db:
        report = db.get(StudyReport, report_id)
        if not report:
            raise ValueError('报告已删除')
        selection, books, draft, packet, legacy = _saved_audit_input(db, report)
        cfg = _checked_research_config(db, expected_route_signature)
        baseline = (report.content, report.claims_json, selection.get('claims_reviewed_at'))
        state = {**(selection.get('source_audit') or {}), 'packet': packet,
                 'status': 'running', 'task_id': record.id, 'version': research_audit.AUDIT_VERSION}
        state.pop('error', None)
        selection['source_audit'] = state
        report.selection_json = json.dumps(selection, ensure_ascii=False)
        db.commit()
        mode = selection.get('research_mode', 'adaptive')
        citation_notes = selection.get('citation_notes')
        update_progress(record, .1, 'source-audit', '正在核对已保留正文与原文')
        try:
            checked, claims = await _perform_source_audit(
                LLMRouter.get('auto', cfg), draft, packet, mode, citation_notes,
                lambda chars: update_progress(record, min(.9, .1 + chars / 8000),
                                               'source-audit', '正在核对来源、推断与论证'),
            )
            db.expire_all()
            report = db.get(StudyReport, report_id)
            if not report:
                raise ValueError('报告已删除，核查结果未写入')
            current = json.loads(report.selection_json or '{}')
            if baseline != (report.content, report.claims_json, current.get('claims_reviewed_at')):
                raise ValueError('核查期间报告或人工复核发生变化，已保留你的修改；请重新核查')
            research_audit.validate_packet(db, packet, books)
            state.update(status='complete', checked_at=datetime.now(timezone.utc).isoformat(),
                         report_hash=research_audit.text_hash(draft), legacy_source_version=legacy,
                         human_review_required=True,
                         logic_review=research_audit.logic_review(checked.get('logic_review')))
            if current.get('claims_reviewed_at'):
                state['suggested_claims'] = claims
            else:
                report.claims_json = json.dumps(claims, ensure_ascii=False)
                current['evidence_summary'] = _evidence_summary(claims)
            current['source_audit'] = state
            current['open_questions'] = research_audit.open_questions(checked.get('open_questions'))
            report.selection_json = json.dumps(current, ensure_ascii=False)
            db.commit()
            return {'kind': 'study-report-audit', 'report_id': report_id, 'claims': len(claims)}
        except (Exception, asyncio.CancelledError) as exc:
            db.rollback()
            db.expire_all()
            report = db.get(StudyReport, report_id)
            if report:
                current = json.loads(report.selection_json or '{}')
                current_state = current.get('source_audit') or {}
                if current_state.get('task_id') == record.id:
                    current_state.update(status='failed', error=str(exc)[:500])
                    report.selection_json = json.dumps(current, ensure_ascii=False)
                    db.commit()
            raise


@router.post('/reports/{report_id}/audit', status_code=202)
def submit_report_audit(report_id: int, req: StudyAuditReq, db: Session = Depends(get_db)):
    from backend.app.worker.tasks import submit_unique, DuplicateTaskError
    report = db.get(StudyReport, report_id)
    if not report:
        raise HTTPException(404, '报告不存在')
    try:
        estimate = _audit_estimate(db, report)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not estimate['configured']:
        raise HTTPException(400, '研究模型尚未配置，请在设置中添加并检测模型')
    if req.route_signature != estimate['route_signature']:
        raise HTTPException(409, '核查模型在预估后发生变化，请重新预估')
    if req.budget_max_tokens < estimate['estimated_tokens']:
        raise HTTPException(409, '核查上限低于估算需求，请提高上限')
    books = json.loads(report.book_ids_json or '[]')
    db.rollback()  # Release SQLite before task registration writes to it.
    try:
        task = submit_unique('study-audit', lambda rec: run_report_audit(rec, report_id, req.route_signature),
                             book_id=books[0], budget_max_tokens=req.budget_max_tokens,
                             budget_max_calls=req.budget_max_calls,
                             initial_result={'kind': 'study-report-audit', 'report_id': report_id})
    except DuplicateTaskError as exc:
        raise HTTPException(409, '这本资料已有来源核查任务在运行') from exc
    return {'task_id': task.id}


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
    selection = json.loads(report.selection_json or "{}")
    allowed_refs = {str(ref) for claim in previous for ref in (claim.get("source_refs") or [])}
    # An audit can replace the automated claim list while a human editor still
    # holds an older draft. Keep that report's original cited passages available.
    allowed_refs.update(str(item.get('anchor') or '') for item in selection.get('citation_notes', []))
    packet = (selection.get('source_audit') or {}).get('packet') or {}
    allowed_refs.update(str(item.get('source_ref') or '') for item in packet.get('entries', []))
    claims = _normalize_claims(req.claims, allowed_refs)
    if len(claims) != len(req.claims):
        raise HTTPException(422, "主张存在空文本、无效结构或超过 30 条")
    prior = {claim['claim']: claim for claim in previous}
    for claim in claims:
        old = prior.get(claim['claim'], {})
        if claim['source_refs'] == old.get('source_refs'):
            for key in ('evidence_quotes', 'verification'):
                if key in old:
                    claim[key] = old[key]
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
    if not cfg.get("configured"):
        raise HTTPException(400, "研究模型连接尚未配置")
    context = _book_context(db, req.book_ids, limit_per_book=6000)
    if not context:
        raise HTTPException(400, "没有可用的文献")
    sid = uuid.uuid4().hex[:12]
    sess = {
        "mode": req.mode, "topic": req.topic, "book_ids": req.book_ids or [],
        "context": context, "history": [], "round": 0, "done": False,
    }
    provider = LLMRouter.get("auto", cfg)
    first = await _gen_turn(provider, sess, None)
    # 开场轮结束后落库：重启后 train_ask 仍能续上，不再报 404。
    save_session(db, sid, "train", sess, sess["book_ids"])
    return {"session_id": sid, "message": first, "round": 0, "done": False}


class TrainAskReq(BaseModel):
    session_id: str
    answer: str


@router.post("/train/ask")
async def train_ask(req: TrainAskReq, db: Session = Depends(get_db)):
    cfg = load_llm_config(db, "research")
    provider = LLMRouter.get("auto", cfg)
    sess = load_session(db, req.session_id, "train")
    if not sess:
        raise HTTPException(404, "会话不存在或已过期（超过 7 天未继续的会话会被清理）")
    if sess.get("done"):
        raise HTTPException(400, "训练已结束，请开启新会话")
    msg = await _gen_turn(provider, sess, req.answer)
    save_session(db, req.session_id, "train", sess, sess.get("book_ids") or [])
    return {"session_id": req.session_id, "message": msg, "round": sess["round"], "done": sess["done"]}


class TrainEndReq(BaseModel):
    session_id: str


@router.post("/train/end", status_code=204)
def train_end(req: TrainEndReq, db: Session = Depends(get_db)):
    """结束训练并删除会话记录。"""
    delete_session(db, req.session_id, "train")


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
