"""AI 增强 API：选文解释/翻译、章节总结、页面视觉解读（Qwen-VL）"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, Chapter, Chunk
from backend.app.schemas import AiExplainReq, AiResp, AiSummaryReq, AiVisionReq
from backend.app.services.llm import LLMRouter, load_llm_config
from backend.app.services.vision import VisionProvider, load_vision_config

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/explain", response_model=AiResp)
async def ai_explain(req: AiExplainReq, db: Session = Depends(get_db)):
    """选中文字 → AI 解释 / 翻译（仅发送选中文本，不发送整页）。"""
    cfg = load_llm_config(db)
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
    return AiResp(ok=True, result=answer)


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
    cfg = load_llm_config(db)
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
    return AiResp(ok=True, result=answer)


@router.post("/vision", response_model=AiResp)
async def ai_vision(req: AiVisionReq, db: Session = Depends(get_db)):
    """页面截图 → Qwen-VL 多模态解读（图表/公式/示意图）。"""
    book = db.get(Book, req.book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    cfg = load_vision_config(db)
    provider = VisionProvider(
        api_key=cfg["vision_api_key"], base_url=cfg["vision_base_url"],
        model=cfg["vision_model"],
    )
    prompt = req.prompt or (
        f"这是《{book.title}》第 {req.page} 页的截图。请：1) 解读页面上的图表/公式/示意图；"
        "2) 总结页面要点；3) 若有公式请用文字描述。用中文回答。"
    )
    try:
        result = await provider.analyze_image(req.image, prompt)
        return AiResp(ok=True, result=result)
    except Exception as e:  # noqa: BLE001
        return AiResp(ok=False, error=str(e))
