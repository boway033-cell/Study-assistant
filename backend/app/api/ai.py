"""AI 增强 API：选文解释、章节总结和页面图像解读。"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, Chapter, Chunk
from backend.app.schemas import AiExplainReq, AiResp, AiSummaryReq, AiPageImageReq
from backend.app.services.llm import LLMRouter, load_llm_config, load_provider_config, request_reasoning_effort
from backend.app.services.llm.budget import current_budget, load_default_budget
from backend.app.services.chinese_style_audit import audit_chinese_style, normalize_chinese_format

router = APIRouter(prefix="/api/ai", tags=["ai"])


class BatchGenerateItem(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    prompt: str = Field(min_length=1, max_length=16000)
    system: str = Field(default="", max_length=4000)
    provider_ids: list[str] | None = Field(default=None, min_length=1, max_length=4)


class BatchGenerateReq(BaseModel):
    task: Literal["chat", "research", "writing", "presentation", "utility"] = "utility"
    items: list[BatchGenerateItem] = Field(min_length=1, max_length=12)
    provider_ids: list[str] | None = Field(default=None, min_length=1, max_length=4)
    max_concurrency: int = Field(default=3, ge=1, le=4)
    reasoning_effort: Literal["none", "low", "medium", "high", "max"] | None = None


@router.post("/batch")
async def ai_batch(req: BatchGenerateReq, db: Session = Depends(get_db)):
    """多任务 × 多模型有限并行；逐项返回结果，失败不覆盖成功项。"""
    if len({item.id for item in req.items}) != len(req.items):
        raise HTTPException(400, "任务 ID 不能重复")
    default_cfg = load_llm_config(db, req.task)
    jobs = []
    for item in req.items:
        ids = item.provider_ids or req.provider_ids
        if ids:
            for provider_id in dict.fromkeys(ids):
                try:
                    cfg = load_provider_config(db, provider_id, req.task)
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
                if not cfg.get("configured"):
                    raise HTTPException(400, f"模型连接未配置：{provider_id}")
                jobs.append((item, cfg))
        else:
            if not default_cfg.get("configured"):
                raise HTTPException(400, "默认模型尚未配置")
            jobs.append((item, default_cfg))
    if len(jobs) > 12:
        raise HTTPException(400, "单次最多执行 12 个模型调用，请减少任务或模型数量")

    gate = asyncio.Semaphore(req.max_concurrency)
    budget_token = current_budget.set(load_default_budget())
    try:
        async def run_one(item: BatchGenerateItem, cfg: dict):
            async with gate:
                provider = LLMRouter.get("auto", cfg)
                messages = [{"role": "system", "content": item.system or "你是个人知识库助手。请准确、简洁地回答；不确定时说明依据不足。"},
                            {"role": "user", "content": item.prompt}]
                effort_token = request_reasoning_effort.set(req.reasoning_effort)
                try:
                    async with asyncio.timeout(180):
                        result = "".join([delta async for delta in provider.stream_chat(messages)])
                    if not result.strip():
                        raise ValueError("模型没有返回正文")
                    result = normalize_chinese_format(result)
                    return {"id": item.id, "provider_id": provider.selected_provider_id,
                            "model": provider.selected_model, "ok": True, "result": result,
                            "style_audit": audit_chinese_style(result)}
                except Exception as exc:  # 单项故障不能抹掉其他模型的结果
                    return {"id": item.id, "provider_id": cfg["provider_id"], "model": provider.model,
                            "ok": False, "result": "", "error": str(exc)[:500]}
                finally:
                    request_reasoning_effort.reset(effort_token)

        results = await asyncio.gather(*(run_one(item, cfg) for item, cfg in jobs))
    finally:
        current_budget.reset(budget_token)
    return {"results": results, "successes": sum(item["ok"] for item in results),
            "failures": sum(not item["ok"] for item in results)}


@router.post("/explain", response_model=AiResp)
async def ai_explain(req: AiExplainReq, db: Session = Depends(get_db)):
    """选中文字 → AI 解释 / 翻译（仅发送选中文本，不发送整页）。"""
    cfg = load_llm_config(db, "utility")
    provider = LLMRouter.get("auto", cfg)
    if req.action == "translate":
        system = (
            "你是专业翻译助手。把用户选中的教材片段翻译成中文（若原文已是中文则翻译成英文）。"
            "只输出译文，不要解释。"
        )
    else:
        system = (
            "你是专业课学习助手。用户选中了教材中的一段内容，请用通俗语言解释其含义、"
            "关键概念和背景（结合书名与章节名）。用中文回答，条理清晰，不超过 400 字。"
        )
    context = ""
    if req.book_title:
        context += f"（来自《{req.book_title}》"
        if req.chapter_title:
            context += f" · {req.chapter_title}"
        context += "）"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"选中内容：\n{req.text}{context}"},
    ]
    answer = ""
    try:
        async for delta in provider.stream_chat(messages):
            answer += delta
    except Exception as e:  # noqa: BLE001
        return AiResp(ok=False, error=str(e))
    answer = normalize_chinese_format(answer)
    return AiResp(ok=True, result=answer, style_audit=audit_chinese_style(answer))


@router.post("/summarize", response_model=AiResp)
async def ai_summarize(req: AiSummaryReq, db: Session = Depends(get_db)):
    """章节 AI 总结（使用本地章节文本，仅发送该章内容）。"""
    chapter = db.get(Chapter, req.chapter_id)
    if not chapter or chapter.book_id != req.book_id:
        raise HTTPException(404, "章节不存在")
    chunks = db.scalars(
        select(Chunk).where(Chunk.chapter_id == chapter.id)
        .order_by(Chunk.chunk_index)
    ).all()
    if not chunks:
        raise HTTPException(400, "该章节没有内容")
    material = "\n".join(c.content for c in chunks)[:12000]
    book = db.get(Book, req.book_id)
    cfg = load_llm_config(db, "utility")
    provider = LLMRouter.get("auto", cfg)
    messages = [
        {"role": "system", "content": (
            "你是复习助手。根据教材章节原文，生成复习用总结：1) 核心要点（3-6 条）；"
            "2) 重要概念/公式/定义；3) 可能的考点。用中文，Markdown 格式，不超过 600 字。"
        )},
        {"role": "user", "content": f"《{book.title if book else ''}》章节：{chapter.title}\n\n{material}"},
    ]
    answer = ""
    try:
        async for delta in provider.stream_chat(messages):
            answer += delta
    except Exception as e:  # noqa: BLE001
        return AiResp(ok=False, error=str(e))
    answer = normalize_chinese_format(answer)
    return AiResp(ok=True, result=answer, style_audit=audit_chinese_style(answer))


@router.post("/page-image", response_model=AiResp)
async def ai_page_image(req: AiPageImageReq, db: Session = Depends(get_db)):
    """页面截图 → 当前通用模型的图文能力。"""
    book = db.get(Book, req.book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    cfg = load_llm_config(db, "utility")
    cfg["writing_style_profile"] = "vision"
    provider = LLMRouter.get("auto", cfg)
    prompt = req.prompt or (
        f"这是《{book.title}》第 {req.page} 页的截图。请：1) 解读页面上的图表/公式/示意图；"
        "2) 总结页面要点；3) 若有公式请用文字描述。用中文回答。"
    )
    try:
        if not req.image.startswith(("data:image/jpeg;base64,", "data:image/png;base64,")):
            raise ValueError("页面图像必须是 JPEG 或 PNG")
        messages = [{"role": "system", "content": "你是专业阅读助手。只根据页面内容解释，不臆测看不清的文字或公式。"},
                    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": req.image}},
                                                   {"type": "text", "text": prompt}]}]
        result = ""
        async for delta in provider.stream_chat(messages):
            result += delta
        if not result.strip():
            raise RuntimeError("模型未返回解读内容；请确认当前模型支持图像输入")
        result = normalize_chinese_format(result)
        return AiResp(ok=True, result=result, style_audit=audit_chinese_style(result))
    except Exception as e:  # noqa: BLE001
        return AiResp(ok=False, error=f"{e}。如当前模型不支持图像，请在设置中为通用生成选择多模态模型。")
