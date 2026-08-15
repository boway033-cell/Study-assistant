"""RAG 服务：宽定位检索 + 混合向量检索 + prompt 组装

检索策略（杜绝「概念无法 fetch」）：
1. 向量检索（开启时优先）：语义召回
2. FTS5 关键词检索：BM25 风格
3. LIKE 子串兜底：命中不足时，任何含词的 chunk 都能命中
4. 章节级上下文：命中 chunk 拉取同章节相邻 chunk
5. 全文兜底：目录结构（is_outline）
"""
from __future__ import annotations

from backend.app.core.config import settings
from backend.app.services.rag import fts, vector
from backend.app.services.rag.fts import fallback_search, get_chapter_neighbors, get_book_outline

MIN_PRIMARY_HITS = 3


def retrieve(question: str, book_id: int | None = None, top_k: int | None = None) -> list[dict]:
    """宽定位 + 混合检索。返回 items（含 page_start/page_end/context 等）。"""
    k = top_k or settings.rag_top_k
    seen: set[int] = set()
    items: list[dict] = []

    # 1. 向量检索（可选，语义精准）
    if vector.is_enabled():
        for it in vector.vector_search(question, book_id=book_id, top_k=k):
            cid = it["chunk_id"]
            if cid in seen:
                continue
            seen.add(cid)
            # 补充书籍/章节标题信息
            items.append(_enrich_vector_item(it, book_id))

    # 2. FTS5 关键词
    for it in fts.search(question, book_id=book_id, top_k=k)["items"]:
        cid = it["chunk_id"]
        if cid in seen:
            continue
        seen.add(cid)
        items.append(it)

    # 3. LIKE 兜底（命中仍不足）
    if len(items) < MIN_PRIMARY_HITS:
        for it in fallback_search(question, book_id=book_id, limit=k):
            cid = it["chunk_id"]
            if cid in seen:
                continue
            seen.add(cid)
            items.append(it)

    # 4. 章节级上下文
    enriched = []
    for it in items[:k]:
        it = dict(it)
        it["context"] = get_chapter_neighbors(it["chunk_id"], radius=1)
        enriched.append(it)

    # 5. 目录兜底
    if not enriched:
        enriched = [get_book_outline(book_id)]

    return enriched


def _enrich_vector_item(it: dict, book_id: int | None) -> dict:
    """向量结果补充书籍/章节标题。"""
    from backend.app.core.database import engine
    from sqlalchemy import text as sql_text

    chunk_id = it["chunk_id"]
    with engine.connect() as conn:
        row = conn.execute(sql_text(
            "SELECT c.book_id, b.title AS book_title, ch.title AS chapter_title, c.page_start AS page "
            "FROM chunks c LEFT JOIN books b ON b.id=c.book_id "
            "LEFT JOIN chapters ch ON ch.id=c.chapter_id WHERE c.id=:id"
        ), {"id": chunk_id}).mappings().first()
    if row:
        it["book_id"] = row["book_id"]
        it["book_title"] = row["book_title"] or ""
        it["chapter_title"] = row["chapter_title"] or None
        it["page"] = row["page"]
    else:
        it["book_id"] = book_id
        it["book_title"] = ""
        it["chapter_title"] = None
        it["page"] = it.get("page_start")
    it["snippet"] = (it.get("content") or "")[:400]
    return it


def build_prompt(question: str, sources: list[dict]) -> list[dict]:
    """组装 LLM messages：系统提示 + 检索内容 + 问题。"""
    if not sources:
        system = (
            "你是一个专业课学习助手。用户正在复习专业书籍。"
            "如果资料中没有相关内容，请直接说明'资料中未找到相关内容'，不要编造。"
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]

    if sources and sources[0].get("is_outline"):
        outline = sources[0]
        system = (
            "你是专业课学习助手。以下是书籍的目录结构与章节关键词，正文检索未直接命中。"
            "请基于目录线索，说明该主题可能位于哪一章节，并给出书中相关概念的整体框架；"
            "不要编造具体公式或原文，明确提示用户可提供更多关键词。"
        )
        user = f"书籍目录：\n{outline.get('snippet', '')}\n\n问题：{question}"
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    ctx_parts = []
    for i, s in enumerate(sources, 1):
        book = s.get("book_title", "")
        chap = s.get("chapter_title") or ""
        page = s.get("page")
        loc = f"《{book}》" if book else ""
        if chap:
            loc += f" {chap}"
        if page:
            loc += f" 第{page}页"
        content = s.get("context") or s.get("snippet", "")
        ctx_parts.append(f"[资料{i}]（{loc}）\n{content}")

    context = "\n\n".join(ctx_parts)
    if len(context) > 12000:
        context = context[:12000] + "\n…（内容过长已截断）"

    system = (
        "你是专业课学习助手，基于下方提供的书籍内容回答问题。"
        "要求：1) 优先完整引用资料内容，力求完整输出文献原文相关段落；"
        "2) 回答末尾用 [资料N] 标注依据来源；"
        "3) 若资料已覆盖问题，不要以'资料不足'推脱；仅在资料确实未涉及时才说明。"
    )
    user = f"书籍资料：\n{context}\n\n问题：{question}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
