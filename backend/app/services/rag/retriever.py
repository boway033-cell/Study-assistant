"""RAG 服务：检索 + prompt 组装（docs/01-architecture.md §4.2）

P0 只用 FTS5 关键词检索；向量检索为 P1 可选（设置 vector_search=true 时启用）。
"""
from __future__ import annotations

from backend.app.core.config import settings
from backend.app.services.rag import fts


def retrieve(question: str, book_id: int | None = None, top_k: int | None = None) -> list[dict]:
    """检索相关片段。返回 items（含 snippet / page / book_title 等）。"""
    k = top_k or settings.rag_top_k
    result = fts.search(question, book_id=book_id, top_k=k)
    return result["items"]


def build_prompt(question: str, sources: list[dict]) -> list[dict]:
    """组装 LLM messages：系统提示 + 检索片段 + 问题。"""
    if not sources:
        system = (
            "你是一个专业课学习助手。用户正在复习专业书籍。"
            "如果资料中没有相关内容，请直接说明'资料中未找到相关内容'，不要编造。"
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]

    ctx_lines = []
    for i, s in enumerate(sources, 1):
        book = s.get("book_title", "")
        chap = s.get("chapter_title") or ""
        page = s.get("page")
        loc = f"《{book}》" if book else ""
        if chap:
            loc += f" {chap}"
        if page:
            loc += f" 第{page}页"
        ctx_lines.append(f"[资料{i}]（{loc}）\n{s.get('snippet', '')}")

    context = "\n\n".join(ctx_lines)
    system = (
        "你是专业课学习助手，基于下方提供的书籍片段回答问题。"
        "要求：1) 优先引用片段内容；2) 回答末尾用 [资料N] 标注依据来源；"
        "3) 片段不足时明确说明，不编造。"
    )
    user = f"书籍片段：\n{context}\n\n问题：{question}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
