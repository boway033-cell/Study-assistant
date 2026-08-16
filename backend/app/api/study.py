"""AI 研读：综合阅读报告 + 思维训练（出题批改追问 / 自由陪练）"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, BookDeep, Chapter, Chunk
from backend.app.services.llm import LLMRouter, load_llm_config

router = APIRouter(prefix="/api/study", tags=["study"])


# ---------- 会话（内存态，单用户）----------
_sessions: dict[str, dict] = {}


def _book_context(db: Session, book_ids: list[int] | None, limit_per_book: int = 8000) -> str:
    """收集选中文献的结构与内容摘要（优先 Markdown 精读版，否则章节+chunk）。"""
    q = select(Book).where(Book.status == "ready")
    if book_ids:
        q = q.where(Book.id.in_(book_ids))
    books = db.scalars(q).all()
    parts: list[str] = []
    for b in books[:8]:
        deep = db.scalar(select(BookDeep).where(BookDeep.book_id == b.id))
        if deep and deep.markdown and deep.status == "done":
            md = deep.markdown[:limit_per_book]
            parts.append(f"【文献《{b.title}》】\n{md}")
            continue
        chapters = db.scalars(
            select(Chapter).where(Chapter.book_id == b.id).order_by(Chapter.order_index)
        ).all()
        titles = "、".join(ch.title for ch in chapters[:40])
        chunks = db.scalars(
            select(Chunk).where(Chunk.book_id == b.id).order_by(Chunk.chunk_index).limit(12)
        ).all()
        body = "\n".join(c.content[:400] for c in chunks)[:limit_per_book]
        parts.append(f"【文献《{b.title}》】章节：{titles}\n内容片段：{body[:limit_per_book]}")
    return "\n\n".join(parts)[:40000]


# ---------- 综合阅读 ----------
class StudyOverviewReq(BaseModel):
    book_ids: list[int] | None = None  # None = 全部


async def run_overview(record, book_ids: list[int] | None) -> dict:
    from backend.app.core.database import SessionLocal
    from backend.app.worker.tasks import update_progress

    db = SessionLocal()
    try:
        update_progress(record, 0.15, "overview", "正在汇总文献内容...")
        context = _book_context(db, book_ids)
        if not context:
            raise ValueError("没有可用的文献")
        cfg = load_llm_config(db)
        if not cfg.get("deepseek_api_key"):
            raise ValueError("未配置 DeepSeek API Key")
        provider = LLMRouter.get("auto", cfg)
        update_progress(record, 0.4, "overview", "AI 进行综合性阅读...")
        prompt = [
            {"role": "system", "content": (
                "你是深度研读导师。用户上传了若干专业文献（教材/讲义/论文），"
                "请进行综合性阅读并输出结构化报告（中文 Markdown）：\n"
                "1. **整体主题脉络**：这些文献共同构成什么知识领域，主线是什么\n"
                "2. **各文献定位**：逐一说明每篇文献的主题、特点、与其他文献的关系\n"
                "3. **交叉知识点**：哪些概念/方法在多篇文献中反复出现（跨文献联系）\n"
                "4. **思维训练题**：出 3-5 道有深度的思考题（应用/批判/跨文献比较）\n"
                "5. **学习路径建议**：建议的精读顺序与重点\n"
                "要求：忠实于文献内容，不编造；引用时注明文献名。"
            )},
            {"role": "user", "content": context},
        ]
        answer = ""
        last_err = ""
        for attempt in range(3):  # 限流/网络抖动自动重试
            try:
                answer = ""
                async for delta in provider.stream_chat(prompt):
                    answer += delta
                if answer.strip():
                    break
                last_err = "AI 返回为空"
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
            import asyncio
            await asyncio.sleep(2 * (attempt + 1))
        if not answer.strip():
            raise RuntimeError("综合阅读失败：" + last_err)

        # 持久化报告
        from backend.app.models import StudyReport
        report = StudyReport(
            book_ids_json=json.dumps(book_ids or [], ensure_ascii=False),
            content=answer,
        )
        db.add(report)
        db.commit()
        update_progress(record, 1.0, "overview", "完成")
        return {"report_id": report.id, "chars": len(answer)}
    except Exception as e:  # noqa: BLE001
        raise
    finally:
        db.close()


@router.post("/overview", status_code=202)
def study_overview(req: StudyOverviewReq, db: Session = Depends(get_db)):
    from backend.app.worker.tasks import submit

    record = submit("study-overview", lambda rec: run_overview(rec, req.book_ids))
    return {"task_id": record.id}


@router.get("/reports")
def list_reports(limit: int = 5, db: Session = Depends(get_db)):
    from backend.app.models import StudyReport

    rows = db.scalars(select(StudyReport).order_by(StudyReport.created_at.desc()).limit(limit)).all()
    return [{
        "id": r.id, "book_ids": json.loads(r.book_ids_json or "[]"),
        "content": r.content, "created_at": r.created_at.isoformat(),
    } for r in rows]


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
    _sessions[sid] = {
        "mode": req.mode, "topic": req.topic, "book_ids": req.book_ids or [],
        "context": context, "history": [], "round": 0, "max_round": 6, "done": False,
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


async def _gen_turn(provider, sess: dict, user_answer: str | None):
    """生成一轮：user_answer 为 None 表示开场问题。"""
    if user_answer is not None:
        sess["history"].append({"role": "user", "content": user_answer})

    if sess["mode"] == "quiz":
        system = (
            "你是思维训练导师（苏格拉底式）。基于文献内容训练用户：\n"
            "- 每次只出 1 道题，题型按顺序递进：概念理解→应用场景→批判思考→跨文献联系→综合\n"
            "- 用户回答后：① 简短评价（对错与不足，30 字内）② 若答错/含糊，追问一次引导 ③ 答得好则出下一题\n"
            "- 第 6 轮后给出 100-150 字总结评价（掌握情况 + 建议）并标注【训练结束】\n"
            "- 输出格式：先【评价】再【提问】或【总结】，用中文。"
        )
    else:
        system = (
            "你是文献陪练导师。基于文献内容与用户自由对话：\n"
            "- 主动引导用户深入理解（提问、类比、举例、指出矛盾）\n"
            "- 用户回答后给予反馈并继续深入，像真正的老师一样\n"
            "- 第 6 轮后总结学习收获并标注【训练结束】\n"
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
    if sess["round"] >= sess["max_round"] or "【训练结束】" in answer:
        sess["done"] = True
    return answer
