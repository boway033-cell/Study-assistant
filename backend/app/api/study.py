"""AI 研读：综合阅读报告 + 思维训练（出题批改追问 / 自由陪练）"""
from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, Chapter, Chunk, KnowledgeNote
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


_MODE_GUIDANCE = {
    "adaptive": "先识别资料类型与研究问题性质，再自主选择最有解释力的分析维度和报告结构；不要套用固定论文或期刊模板。",
    "comparative": "优先比较概念定义、核心主张、论证机制、证据类型、适用边界与相互冲突，但只保留对当前材料真正有用的维度。",
    "critical": "区分原始材料、作者解释与模型推断；检查证据强度、替代解释、反例、方法限制和因果外推。",
    "gap": "梳理已有共识与分歧，识别材料尚未回答的问题、证据缺口和可继续研究的方向，避免把未知包装成结论。",
}


async def _stream_answer(provider, messages: list[dict], error_prefix: str) -> str:
    """统一流式调用与短暂故障重试；不在内存保留模型隐性推理过程。"""
    import asyncio

    last_err = ""
    for attempt in range(3):
        answer = ""
        try:
            async for delta in provider.stream_chat(messages):
                answer += delta
            if answer.strip():
                return answer
            last_err = "AI 返回为空"
        except Exception as exc:  # noqa: BLE001
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
        claims.append({
            "claim": str(claim["claim"]).strip()[:1000],
            "claim_type": claim_type if claim_type in allowed_types else "interpretive",
            "source_refs": refs,
            "status": status,
            "confidence": confidence if confidence in allowed_confidence else "low",
            "reason": str(claim.get("reason") or "")[:1000],
            "counterpoint": str(claim.get("counterpoint") or "")[:1000],
        })
    return claims


async def run_overview(record, book_ids: list[int], focus: str = "", framework: str = "",
                       chapter_ids: list[int] | None = None, note_ids: list[int] | None = None,
                       research_mode: str = "adaptive", reasoning_depth: str = "deep") -> dict:
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
        cfg = load_llm_config(db)
        if not cfg.get("deepseek_api_key"):
            raise ValueError("未配置 DeepSeek API Key")
        provider = LLMRouter.get("auto", cfg)
        plan = _normalize_plan({}, research_mode)
        if reasoning_depth == "deep":
            update_progress(record, 0.32, "research-plan", "DeepSeek 正在拆分问题并规划证据路径...")
            plan_messages = [
                {"role": "system", "content": (
                    "你是独立研究分析助手。不要套用 Nature 或任何期刊的固定写作模板，也不要机械复述目录。"
                    "请根据研究问题和材料本身，选择分析维度。区分材料事实、作者解释与模型推断。"
                    "只输出 JSON 对象："
                    '{"material_type":"材料类型判断","subquestions":["3-6个子问题"],'
                    '"analysis_axes":["2-5个分析维度"],"evidence_needs":["需核查的证据"],'
                    '"report_outline":["自适应报告章节"]}。不要输出思维过程或答案正文。'
                )},
                {"role": "user", "content": (
                    f"研读方式：{_MODE_GUIDANCE.get(research_mode, _MODE_GUIDANCE['adaptive'])}\n"
                    f"研究问题：{focus.strip()}\n用户补充维度：{framework.strip() or '无，由 AI 自主判断'}\n\n"
                    f"材料预览：\n{overview_context[:18000]}"
                )},
            ]
            from backend.app.services.llm import parse_json_response
            raw_plan = await _stream_answer(provider, plan_messages, "研究路径规划失败")
            plan = _normalize_plan(parse_json_response(raw_plan), research_mode)

        # 用户没有点选具体材料时，按研究问题和 AI 子问题在所选书目内迭代检索。
        # 已点选章节/笔记时严格遵守选择边界，不从其他章节补料。
        if selected_context:
            evidence_context = selected_context
        else:
            update_progress(record, 0.52, "evidence", "正在按研究路径检索和整理证据...")
            from backend.app.services.rag import retriever
            queries = [focus.strip()] + plan.get("subquestions", [])[:4]
            retrieved: list[dict] = []
            seen: set[int] = set()
            for query in queries:
                if not query:
                    continue
                for item in retriever.retrieve(query, book_ids=book_ids, top_k=5):
                    chunk_id = int(item.get("chunk_id") or 0)
                    if chunk_id and chunk_id not in seen:
                        seen.add(chunk_id)
                        retrieved.append(item)
            evidence_context, allowed_refs = _format_retrieved_context(retrieved)
            if not evidence_context:
                evidence_context = overview_context[:22000]

        update_progress(record, 0.7, "synthesis", "DeepSeek 正在综合证据、检验反例与形成报告...")
        prompt = [
            {"role": "system", "content": (
                "你是独立、审慎的研究分析助手。Nature 类技能在这里不决定报告结构；你必须根据文章内容和研究问题自主判断。"
                "不要机械摘要，不要逐篇流水账，不要补造来源。先区分原文信息、作者解释和你的综合推断，再综合一致、互补、冲突、"
                "替代解释与适用边界。标题应由问题和材料决定，不使用固定期刊模板。只输出 JSON 对象："
                '{"report_markdown":"中文 Markdown 报告","claims":[{"claim":"可核验主张",'
                '"claim_type":"descriptive|associational|causal|interpretive","source_refs":["来源锚点"],'
                '"status":"supported|partial|needs_review|unsupported","confidence":"high|medium|low",'
                '"reason":"支持或降级理由","counterpoint":"反例、限制或替代解释"}],'
                '"open_questions":["材料尚未回答或需要继续核查的问题"]}。\n'
                "每项关键结论必须使用材料中逐字存在的 [B…] 来源锚点；没有有效锚点的判断必须标为 needs_review。"
                "直接支持=supported；仅部分支持或含外推=partial；材料不足=needs_review；材料反驳=unsupported。"
                "因果主张必须有相称的因果证据，不能因表达流畅或多处重复就提高置信度。"
            )},
            {"role": "user", "content": (
                f"研读方式：{_MODE_GUIDANCE.get(research_mode, _MODE_GUIDANCE['adaptive'])}\n"
                f"研究问题：{focus.strip()}\n用户补充维度：{framework.strip() or '无，允许 AI 自主选择'}\n"
                f"AI 研究路径（可调整结构，不是答案）：{json.dumps(plan, ensure_ascii=False)}\n\n"
                f"可引用材料：\n{evidence_context[:48000]}"
            )},
        ]
        answer = await _stream_answer(provider, prompt, "综合研读失败")

        from backend.app.services.llm import parse_json_response
        try:
            parsed = parse_json_response(answer)
        except Exception:  # 兼容旧模型偶发返回纯 Markdown；报告可保存，但主张均留待人工整理。
            parsed = None
        if isinstance(parsed, dict):
            report_content = str(parsed.get("report_markdown") or "").strip()
            raw_claims = parsed.get("claims") if isinstance(parsed.get("claims"), list) else []
            open_questions = [str(item).strip()[:300] for item in (parsed.get("open_questions") or []) if str(item).strip()][:10]
        else:
            report_content, raw_claims = answer, []
            open_questions = []
        if not report_content:
            report_content = answer
        claims = _normalize_claims(raw_claims, allowed_refs)
        # 持久化报告及其选择范围/核验主张，不复制原文附件。
        from backend.app.models import StudyReport
        report = StudyReport(
            book_ids_json=json.dumps(book_ids or [], ensure_ascii=False),
            selection_json=json.dumps({
                "chapter_ids": chapter_ids or [], "note_ids": note_ids or [],
                "research_mode": research_mode, "reasoning_depth": reasoning_depth,
                "research_plan": plan, "open_questions": open_questions,
            }, ensure_ascii=False),
            focus=focus, framework=framework, claims_json=json.dumps(claims, ensure_ascii=False),
            content=report_content,
        )
        db.add(report)
        db.commit()
        update_progress(record, 1.0, "overview", "完成")
        return {"report_id": report.id, "chars": len(report_content), "claims": len(claims), "plan_steps": len(plan.get("subquestions", []))}
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
        req.research_mode, req.reasoning_depth,
    ))
    return {"task_id": record.id}


def _report_payload(r, include_content: bool = True) -> dict:
    selection = json.loads(r.selection_json or "{}")
    payload = {
        "id": r.id, "book_ids": json.loads(r.book_ids_json or "[]"),
        "focus": r.focus or "", "framework": r.framework or "", "selection": selection,
        "research_plan": selection.get("research_plan", {}),
        "open_questions": selection.get("open_questions", []),
        "research_mode": selection.get("research_mode", "adaptive"),
        "reasoning_depth": selection.get("reasoning_depth", "standard"),
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
    from backend.app.models import StudyReport
    report = db.get(StudyReport, report_id)
    if not report:
        raise HTTPException(404, "报告不存在")
    return _report_payload(report, True)


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(report_id: int, db: Session = Depends(get_db)):
    """删除一条综合阅读报告。"""
    from backend.app.models import StudyReport

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
    cfg = load_llm_config(db)
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
    cfg = load_llm_config(db)
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
