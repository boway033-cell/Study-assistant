"""AI 问答 API（docs/03-api.md §2）— SSE 流式"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, ChatLog
from backend.app.schemas import ChatHistoryItem, ChatHistoryResp, ChatReq, ChatSource
from backend.app.services.llm import LLMRouter
from backend.app.services.rag import fts, retriever

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat")
async def chat(req: ChatReq, db: Session = Depends(get_db)):
    if req.book_id is not None and not db.get(Book, req.book_id):
        raise HTTPException(404, "书籍不存在")

    # 检索
    sources = retriever.retrieve(req.question, book_id=req.book_id)
    messages = retriever.build_prompt(req.question, sources)

    provider = LLMRouter.get(req.mode)
    sources_payload = [
        {"chunk_id": s["chunk_id"], "page": s.get("page"), "snippet": s.get("snippet", "")}
        for s in sources
    ]

    async def event_stream():
        yield _sse("meta", {"mode": provider.name, "model": getattr(provider, "model", ""),
                            "book_ids": [req.book_id] if req.book_id else []})
        answer_parts: list[str] = []
        try:
            async for delta in provider.stream_chat(messages):
                answer_parts.append(delta)
                yield _sse("token", {"text": delta})
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"message": str(e)})
            return

        answer = "".join(answer_parts)
        # 存历史
        log = ChatLog(
            book_id=req.book_id, question=req.question, answer=answer,
            sources_json=json.dumps(sources_payload, ensure_ascii=False),
            mode=provider.name, model_name=getattr(provider, "model", None),
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        yield _sse("done", {"chat_id": log.id, "sources": sources_payload})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/chat/history", response_model=ChatHistoryResp)
def chat_history(
    book_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    q = select(ChatLog)
    if book_id:
        q = q.where(ChatLog.book_id == book_id)
    total = len(db.scalars(q).all())
    logs = db.scalars(q.order_by(ChatLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    items = []
    for log in logs:
        try:
            sources = json.loads(log.sources_json) if log.sources_json else []
        except json.JSONDecodeError:
            sources = []
        items.append(ChatHistoryItem(
            id=log.id, question=log.question, answer=log.answer, mode=log.mode,
            sources=[ChatSource(**s) for s in sources], created_at=log.created_at,
        ))
    return ChatHistoryResp(total=total, items=items)


@router.delete("/chat/{chat_id}", status_code=204)
def delete_chat(chat_id: int, db: Session = Depends(get_db)):
    log = db.get(ChatLog, chat_id)
    if not log:
        raise HTTPException(404, "记录不存在")
    db.delete(log)
    db.commit()
