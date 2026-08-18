"""知识事实层：统一知识提取与缓存

问题：深度分析、研读报告、出题、知识树 AI 生成分别从 chunks 表重新读取资料、拼提示词，
导致重复消耗同一批资料、重复请求 DeepSeek。

解决：统一知识事实层（KnowledgeBase），提供标准化的知识获取接口：
1. get_chapter_summary(book_id, chapter_order) → 优先用深度分析缓存，无缓存才调 AI
2. get_book_digest(book_id) → 返回结构化摘要（目录+关键词+定义+定理），本地零 AI
3. get_context_for_question(book_ids) → 检索+摘要合并，供问答/研读/出题共用
4. 所有 AI 产物自动缓存到 DB，重复调用零云端消耗
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    Book,
    BookAnalysis,
    BookDeep,
    Chapter,
    Chunk,
)


def get_book_digest(db: Session, book_id: int) -> dict:
    """获取一本书的结构化摘要（纯本地，零 AI 消耗）。
    
    返回: {title, category, chapters: [{title, level, start_page, end_page, chunk_count}],
           keywords: [str], definitions: [{term, definition}], theorems: [{type, statement}],
           has_deep_analysis: bool}
    """
    book = db.get(Book, book_id)
    if not book:
        return {}

    # 章节结构
    chapters = db.scalars(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_index)
    ).all()
    chapter_list = []
    for ch in chapters:
        chunk_count = db.scalar(
            select(Chunk.id).where(Chunk.chapter_id == ch.id).limit(1)
        )
        chapter_list.append({
            "id": ch.id, "title": ch.title, "level": ch.level,
            "start_page": ch.start_page, "end_page": ch.end_page,
            "has_content": chunk_count is not None,
        })

    # 关键信息（本地提取，已缓存在 book_analysis 表）
    analysis = db.scalar(select(BookAnalysis).where(BookAnalysis.book_id == book_id))
    keywords: list[str] = []
    definitions: list[dict] = []
    theorems: list[dict] = []
    if analysis:
        try:
            keywords = json.loads(analysis.keywords_json or "[]")
        except (ValueError, TypeError):
            pass
        try:
            definitions = json.loads(analysis.definitions_json or "[]")
        except (ValueError, TypeError):
            pass
        try:
            theorems = json.loads(analysis.theorems_json or "[]")
        except (ValueError, TypeError):
            pass

    # 深度分析状态
    deep = db.scalar(select(BookDeep).where(BookDeep.book_id == book_id))
    has_deep = deep is not None and deep.status == "done" and bool(deep.markdown)

    return {
        "book_id": book_id,
        "title": book.title,
        "category": book.category,
        "file_type": book.file_type,
        "total_pages": book.total_pages,
        "chapters": chapter_list,
        "keywords": keywords[:30],
        "definitions": definitions[:15],
        "theorems": theorems[:15],
        "has_deep_analysis": has_deep,
    }


def get_chapter_summary_cached(db: Session, book_id: int, chapter_order: int) -> str | None:
    """获取某章的 AI 总结（优先用深度分析缓存，无缓存返回 None）。
    
    深度分析已为每章生成总结并缓存在 book_deep.summaries_json。
    此函数避免出题/研读等功能重复调用 AI 总结同一章节。
    """
    deep = db.scalar(select(BookDeep).where(BookDeep.book_id == book_id))
    if not deep or deep.status != "done" or not deep.summaries_json:
        return None
    try:
        summaries = json.loads(deep.summaries_json)
    except (ValueError, TypeError):
        return None
    # summaries 是 [{title, summary}]，按目录顺序
    chapters = db.scalars(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_index)
    ).all()
    level1 = [ch for ch in chapters if ch.level == 1]
    if chapter_order - 1 < len(level1):
        title = level1[chapter_order - 1].title
        for s in summaries:
            if s.get("title") == title:
                return s.get("summary", "")
    return None


def get_multi_book_digest(db: Session, book_ids: list[int] | None = None,
                          limit_per_book: int = 4000) -> str:
    """获取多本书的合并摘要（供研读/出题共用，优先用深度分析 Markdown 缓存）。
    
    此函数替代 study.py 中的 _book_context，统一知识获取入口：
    - 有深度分析 → 用 Markdown 精读版（已缓存，零 AI 消耗）
    - 无深度分析 → 用 book_digest（章节+关键词+定义），本地零 AI
    - 不再直接读原始 chunks 发送（避免重复消耗）
    """
    q = select(Book).where(Book.status == "ready")
    if book_ids:
        q = q.where(Book.id.in_(book_ids))
    books = db.scalars(q).all()
    parts: list[str] = []
    for b in books[:8]:
        # 优先用深度分析 Markdown
        deep = db.scalar(select(BookDeep).where(BookDeep.book_id == b.id))
        if deep and deep.markdown and deep.status == "done":
            parts.append(f"【文献《{b.title}》】\n{deep.markdown[:limit_per_book]}")
            continue
        # 无深度分析 → 用结构化摘要（本地，零 AI）
        digest = get_book_digest(db, b.id)
        ch_list = "、".join(c["title"] for c in digest.get("chapters", [])[:30])
        kws = "、".join(digest.get("keywords", [])[:20])
        defs = "; ".join(f"{d['term']}={d['definition'][:60]}" for d in digest.get("definitions", [])[:8])
        parts.append(f"【文献《{b.title}》】分类:{digest.get('category','')} 章节:{ch_list}\n关键词:{kws}\n定义:{defs}")
    return "\n\n".join(parts)[:25000]


def get_context_for_quiz(db: Session, book_id: int, chapter_id: int,
                         max_chars: int = 4000) -> str:
    """出题用的章节上下文（优先用深度分析缓存的章节总结 + 原文片段）。
    
    替代 quizzes.py 中直接读 chunks 拼提示词的做法：
    - 有章节总结缓存 → 总结 + 少量原文（精简，减少 Token 消耗）
    - 无缓存 → 原文片段（兜底）
    """
    chapter = db.get(Chapter, chapter_id)
    if not chapter:
        return ""

    # 优先用深度分析的章节总结
    cached_summary = get_chapter_summary_cached(db, book_id, chapter.order_index)
    
    chunks = db.scalars(
        select(Chunk).where(Chunk.chapter_id == chapter_id)
        .order_by(Chunk.chunk_index).limit(4)
    ).all()
    raw_text = "\n\n".join(c.content[:600] for c in chunks)[:max_chars]

    if cached_summary:
        # 总结 + 精简原文（减少 Token，同时保留出题所需细节）
        return f"【章节总结】\n{cached_summary[:2000]}\n\n【原文片段】\n{raw_text[:2000]}"
    return raw_text
