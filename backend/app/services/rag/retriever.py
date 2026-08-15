"""RAG 服务：宽定位检索 + prompt 组装（docs/01-architecture.md §4.2）

宽定位策略（杜绝「概念无法 fetch」）：
1. 主检索：FTS5 关键词（BM25 风格）
2. 降级检索：命中不足时，用 LIKE 子串匹配兜底（任何含该词的 chunk 都能命中）
3. 章节级上下文：命中 chunk 后，拉取同章节相邻 chunk 拼接，保证完整输出文献内容
4. 全文兜底：仍无命中时，返回书籍目录 + 各章节关键词，让 LLM 基于全书结构回答
"""
from __future__ import annotations

from backend.app.core.config import settings
from backend.app.services.rag import fts
from backend.app.services.rag.fts import fallback_search, get_chapter_neighbors, get_book_outline

# 主检索命中不足时触发降级检索的阈值
MIN_PRIMARY_HITS = 3


def retrieve(question: str, book_id: int | None = None, top_k: int | None = None) -> list[dict]:
    """宽定位检索。返回 items（含 snippet / page / book_title / full_context 等）。"""
    k = top_k or settings.rag_top_k

    # 1. 主检索：FTS5
    items = fts.search(question, book_id=book_id, top_k=k)["items"]

    # 2. 降级检索：命中不足 → LIKE 子串匹配
    if len(items) < MIN_PRIMARY_HITS:
        fallback = fallback_search(question, book_id=book_id, limit=k)
        # 合并去重（fallback 在前，主检索结果补后）
        seen = {it["chunk_id"] for it in items}
        for it in fallback:
            if it["chunk_id"] not in seen:
                items.append(it)
                seen.add(it["chunk_id"])

    # 3. 章节级上下文：为每个命中 chunk 附加同章节上下文
    enriched = []
    for it in items[:k]:
        context = get_chapter_neighbors(it["chunk_id"], radius=1)
        it = dict(it)
        it["context"] = context  # 供 prompt 组装使用完整章节内容
        enriched.append(it)

    # 4. 全文兜底：完全无命中 → 返回目录结构
    if not enriched:
        enriched = [get_book_outline(book_id)]

    return enriched


def build_prompt(question: str, sources: list[dict]) -> list[dict]:
    """组装 LLM messages：系统提示 + 检索内容 + 问题。

    sources 中每个 item 可含 context（章节级完整上下文），优先用 context，
    保证 LLM 在理解全文的基础上完整输出。
    """
    if not sources:
        system = (
            "你是一个专业课学习助手。用户正在复习专业书籍。"
            "如果资料中没有相关内容，请直接说明'资料中未找到相关内容'，不要编造。"
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]

    # 目录兜底（无正文命中）
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
        # 优先使用章节级完整上下文，否则用 snippet
        content = s.get("context") or s.get("snippet", "")
        ctx_parts.append(f"[资料{i}]（{loc}）\n{content}")

    context = "\n\n".join(ctx_parts)
    # 控制总上下文长度（避免超 LLM 上下文窗口，约 12000 字符）
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
