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
from backend.app.services.rag.reranker import rerank, record_retrieval_eval

MIN_PRIMARY_HITS = 3
RRF_K = 60  # Reciprocal Rank Fusion 常数


def retrieve(question: str, book_id: int | None = None, book_ids: list[int] | None = None,
             top_k: int | None = None) -> list[dict]:
    """宽定位 + 混合检索（向量 + FTS + LIKE），RRF 融合排序。

    book_ids: 多书搜索（优先于 book_id）。
    返回 items（含 page_start/page_end/context/score 等）。
    """
    k = top_k or settings.rag_top_k
    search_book_ids = book_ids if book_ids else ([book_id] if book_id else None)

    ranked_lists: list[list[dict]] = []

    # 1. 向量检索
    if vector.is_enabled():
        vec_items = []
        for it in vector.vector_search(question, book_id=book_id, top_k=k * 2):
            vec_items.append(_enrich_vector_item(it, book_id))
        ranked_lists.append(vec_items)

    # 2. FTS5 关键词
    fts_items = fts.search(question, book_id=book_id, book_ids=search_book_ids, top_k=k * 2)["items"]
    ranked_lists.append(fts_items)

    # 3. LIKE 兜底
    total_primary = sum(len(lst) for lst in ranked_lists)
    if total_primary < MIN_PRIMARY_HITS:
        fb_items = fallback_search(question, book_id=book_id, book_ids=search_book_ids, limit=k)
        ranked_lists.append(fb_items)

    # 4. RRF 融合排序
    items = _rrf_fuse(ranked_lists, k)

    # 4b. 二次重排（关键词覆盖率 + 位置加权 + 长度归一化）
    if items:
        items = rerank(question, items, top_k=k)

    # 5. 章节级上下文
    enriched = []
    for it in items[:k]:
        it = dict(it)
        it["context"] = get_chapter_neighbors(it["chunk_id"], radius=1)
        enriched.append(it)

    # 6. 目录兜底
    if not enriched:
        enriched = [get_book_outline(book_id)]

    # 7. 记录检索效果
    record_retrieval_eval(len(enriched), question)

    return enriched


def _rrf_fuse(ranked_lists: list[list[dict]], top_k: int) -> list[dict]:
    """Reciprocal Rank Fusion：多路检索结果按 1/(k+rank) 加权合并。"""
    scores: dict[int, float] = {}
    item_map: dict[int, dict] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            cid = item.get("chunk_id", 0)
            if cid == 0:
                continue
            score = 1.0 / (RRF_K + rank + 1)
            scores[cid] = scores.get(cid, 0.0) + score
            if cid not in item_map:
                item_map[cid] = dict(item)
            existing = item_map[cid]
            for key in ("page_start", "page_end", "book_title", "chapter_title"):
                if key in item and key not in existing:
                    existing[key] = item[key]
            existing["score"] = scores[cid]
    sorted_ids = sorted(scores, key=lambda c: scores[c], reverse=True)
    return [item_map[cid] for cid in sorted_ids[:top_k * 2]]


def _enrich_vector_item(it: dict, book_id: int | None) -> dict:
    """向量结果补充书籍/章节标题 + 页码区间。"""
    from backend.app.core.database import engine
    from sqlalchemy import text as sql_text

    chunk_id = it["chunk_id"]
    with engine.connect() as conn:
        row = conn.execute(sql_text(
            "SELECT c.book_id, b.title AS book_title, ch.title AS chapter_title, "
            "c.page_start, c.page_end "
            "FROM chunks c LEFT JOIN books b ON b.id=c.book_id "
            "LEFT JOIN chapters ch ON ch.id=c.chapter_id WHERE c.id=:id"
        ), {"id": chunk_id}).mappings().first()
    if row:
        it["book_id"] = row["book_id"]
        it["book_title"] = row["book_title"] or ""
        it["chapter_title"] = row["chapter_title"] or None
        it["page"] = row["page_start"]
        it["page_start"] = row["page_start"]
        it["page_end"] = row["page_end"] if row["page_end"] else row["page_start"]
    else:
        it["book_id"] = book_id
        it["book_title"] = ""
        it["chapter_title"] = None
        it["page"] = it.get("page_start")
        it["page_start"] = it.get("page_start")
        it["page_end"] = it.get("page_end") or it.get("page_start")
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
        page_start = s.get("page_start") or page
        page_end = s.get("page_end") or page
        loc = f"《{book}》" if book else ""
        if chap:
            loc += f" {chap}"
        if page_start:
            if page_end and page_end != page_start:
                loc += f" 第{page_start}-{page_end}页"
            else:
                loc += f" 第{page_start}页"
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