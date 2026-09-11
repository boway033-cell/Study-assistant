"""AI drawing API: natural language -> draw.io compatible XML"""
from __future__ import annotations
import json
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.app.core.database import get_db
from backend.app.services.chat_sessions import (delete_session, list_sessions, load_session,
                                                row_state, save_session)
from backend.app.services.llm import LLMRouter, load_llm_config

router = APIRouter(prefix="/api/draw", tags=["draw"])
# 绘图的改图会话状态存在 chat_sessions 表（kind='draw'）；此前是模块级 dict，重启即 404。

SYSTEM_PROMPT = (
    "You are a professional diagram generation assistant. "
    "The user describes needs in natural language. You generate draw.io mxGraph XML.\n\n"
    "Format: <mxGraphModel dx=800 dy=600 grid=1 pageWidth=850 pageHeight=600>"
    " with <mxCell> nodes (vertex=1) and edges (edge=1).\n"
    "Styles: rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;\n"
    "Rules: output ONLY XML, no explanation. Node ids 2,3,4... id=0,1 are layers.\n"
    "Chinese OK directly. Layout top-to-bottom, spacing 40-80px. Size within 850x600."
)

class DrawGenerateReq(BaseModel):
    description: str
    book_ids: list[int] = []
    model: str | None = None

class DrawModifyReq(BaseModel):
    session_id: str
    request: str

class DrawSessionResp(BaseModel):
    session_id: str
    xml: str
    message: str = ""


@router.post("/generate", response_model=DrawSessionResp)
async def generate_diagram(req: DrawGenerateReq, db: Session = Depends(get_db)):
    """Generate draw.io XML from natural language."""
    cfg = load_llm_config(db, "utility")
    if not cfg.get("deepseek_api_key"):
        raise HTTPException(400, "DeepSeek API Key not configured")
    if req.model:
        cfg = {**cfg, "deepseek_model": req.model}
    provider = LLMRouter.get("auto", cfg)
    context = ''
    scope = sorted({int(x) for x in req.book_ids if int(x) > 0})
    if scope:
        from backend.app.services.knowledge_base import get_book_digest
        source_blocks = []
        for book_id in scope[:8]:
            try:
                digest = get_book_digest(db, book_id)
                kws = ", ".join(digest.get("keywords", [])[:12])
                chs = ", ".join(c["title"] for c in digest.get("chapters", [])[:12])
                source_blocks.append(
                    f"[SOURCE BOOK {book_id}] {digest.get('title','')}\nkeywords: {kws}\nchapters: {chs}"
                )
            except Exception:
                continue
        context = (
            "\nUse only the following explicitly selected books as factual source material. "
            "Keep each book's claims distinguishable; do not import knowledge from unselected books.\n"
            + "\n\n".join(source_blocks)
        )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Please draw: {req.description}{context}"},
    ]
    answer = ''
    try:
        async for delta in provider.stream_chat(messages):
            answer += delta
    except Exception as e:
        raise HTTPException(500, f"AI generation failed: {e}")
    xml = _extract_xml(answer)
    sid = uuid.uuid4().hex[:12]
    state = {
        "xml": xml,
        "history": [{"role": "user", "content": req.description}],
        "book_ids": scope,
        "created_at": datetime.now().isoformat(),
    }
    save_session(db, sid, "draw", state, scope)
    return DrawSessionResp(session_id=sid, xml=xml, message="Diagram generated")


@router.post("/modify", response_model=DrawSessionResp)
async def modify_diagram(req: DrawModifyReq, db: Session = Depends(get_db)):
    """Modify diagram via multi-turn dialogue."""
    sess = load_session(db, req.session_id, "draw")
    if not sess:
        raise HTTPException(404, "Drawing session not found")
    cfg = load_llm_config(db, "utility")
    if not cfg.get("deepseek_api_key"):
        raise HTTPException(400, "DeepSeek API Key not configured")
    provider = LLMRouter.get("auto", cfg)
    modify_prompt = f"Modify request: {req.request}\n\nCurrent XML:\n{sess['xml']}\n\nOutput complete modified XML only."
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": modify_prompt},
    ]
    answer = ''
    try:
        async for delta in provider.stream_chat(messages):
            answer += delta
    except Exception as e:
        raise HTTPException(500, f"AI modification failed: {e}")
    xml = _extract_xml(answer)
    sess["xml"] = xml
    sess["history"].append({"role": "user", "content": req.request})
    save_session(db, req.session_id, "draw", sess, sess.get("book_ids") or [])
    return DrawSessionResp(session_id=req.session_id, xml=xml, message="Diagram updated")


@router.get("/sessions")
def list_draw_sessions(db: Session = Depends(get_db)):
    """List current drawing sessions."""
    return {
        "sessions": [
            {"id": row.id,
             "created_at": row.created_at.isoformat() if row.created_at else None,
             "preview": str(row_state(row).get("xml") or "")[:200],
             "book_ids": json.loads(row.book_ids_json or "[]")}
            for row in list_sessions(db, "draw")
        ]
    }


@router.get("/sessions/{session_id}")
def get_draw_session(session_id: str, db: Session = Depends(get_db)):
    """Get a drawing session XML."""
    sess = load_session(db, session_id, "draw")
    if not sess:
        raise HTTPException(404, "Session not found")
    return {"session_id": session_id, "xml": sess.get("xml", ""), "history": sess.get("history", []),
            "book_ids": sess.get("book_ids", [])}


@router.delete("/sessions/{session_id}", status_code=204)
def delete_draw_session(session_id: str, db: Session = Depends(get_db)):
    """Delete a drawing session."""
    delete_session(db, session_id, "draw")


def _extract_xml(text: str) -> str:
    """Extract mxGraphModel XML from AI response."""
    text = text.strip()
    if text.startswith('```'):
        lines = text.split('\n')
        if lines[0].strip().startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        text = '\n'.join(lines)
    start = text.find('<mxGraphModel')
    end = text.rfind('</mxGraphModel>')
    if start >= 0 and end > start:
        return text[start:end + len('</mxGraphModel>')]
    if start >= 0:
        return text[start:]
    return text
