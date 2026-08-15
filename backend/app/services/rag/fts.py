"""FTS5 全文索引与检索（P0 主检索，零模型内存）

设计（docs/02-database.md §3）：
- fts_books 虚拟表：content(分词) + book_id/chapter_id/page/chunk_id (UNINDEXED)
- 写入：解析完成后对每个 chunk 做 jieba 分词写入
- 查询：query 同样分词后拼 MATCH 表达式
"""
from __future__ import annotations

from sqlalchemy import text

from backend.app.core.config import settings
from backend.app.core.database import engine
from backend.app.services.rag.chunker import tokenize, tokenize_query

FTS_TABLE = "fts_books"

_CREATE_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
  content,
  book_id UNINDEXED,
  chapter_id UNINDEXED,
  page UNINDEXED,
  chunk_id UNINDEXED,
  tokenize = 'unicode61'
)
"""

_INSERT_SQL = f"""
INSERT INTO {FTS_TABLE}(content, book_id, chapter_id, page, chunk_id)
VALUES (:content, :book_id, :chapter_id, :page, :chunk_id)
"""


def init_fts() -> None:
    """应用启动时调用。"""
    with engine.begin() as conn:
        conn.execute(text(_CREATE_SQL))


def index_chunk(book_id: int, chapter_id: int | None, page: int | None, chunk_id: int, content: str) -> None:
    """为单个 chunk 建索引（分词后写入）。"""
    tok = tokenize(content)
    with engine.begin() as conn:
        conn.execute(text(_INSERT_SQL), {
            "content": tok,
            "book_id": book_id,
            "chapter_id": chapter_id if chapter_id is not None else -1,
            "page": page if page is not None else 0,
            "chunk_id": chunk_id,
        })


def search(
    query: str,
    book_id: int | None = None,
    chapter_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
    top_k: int | None = None,
) -> dict:
    """FTS5 检索。

    返回: {"total": int, "items": [{chunk_id, book_id, book_title, chapter_id,
                                    chapter_title, page, snippet}]}
    """
    match_expr = tokenize_query(query)
    if not match_expr:
        return {"total": 0, "items": []}

    limit = top_k or page_size
    offset = 0 if top_k else (page - 1) * page_size

    params: dict = {"match": match_expr, "limit": limit, "offset": offset}
    where = f"{FTS_TABLE} MATCH :match"
    if book_id is not None:
        where += " AND f.book_id = :book_id"
        params["book_id"] = book_id
    if chapter_id is not None:
        where += " AND f.chapter_id = :chapter_id"
        params["chapter_id"] = chapter_id

    sql = f"""
    SELECT f.chunk_id, f.book_id, f.chapter_id, f.page,
           snippet({FTS_TABLE}, 0, '[', ']', '…', 12) AS snippet,
           b.title AS book_title,
           c.title AS chapter_title
    FROM {FTS_TABLE} f
    LEFT JOIN books b ON b.id = f.book_id
    LEFT JOIN chapters c ON c.id = f.chapter_id
    WHERE {where}
    ORDER BY rank
    LIMIT :limit OFFSET :offset
    """

    count_sql = f"""
    SELECT COUNT(*) FROM {FTS_TABLE} f
    WHERE {where}
    """

    with engine.connect() as conn:
        total = conn.execute(text(count_sql), params).scalar_one()
        rows = conn.execute(text(sql), params).mappings().all()

    items = []
    for r in rows:
        items.append({
            "chunk_id": r["chunk_id"],
            "book_id": r["book_id"],
            "book_title": r["book_title"] or "",
            "chapter_id": r["chapter_id"] if r["chapter_id"] and r["chapter_id"] != -1 else None,
            "chapter_title": r["chapter_title"] or None,
            "page": r["page"] if r["page"] else None,
            "snippet": r["snippet"] or "",
        })

    return {"total": total, "items": items}


def delete_book_index(book_id: int) -> None:
    """删除一本书的全部 FTS 行。"""
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {FTS_TABLE} WHERE book_id = :book_id"), {"book_id": book_id})


def get_chunk_text(chunk_id: int) -> str:
    """取 chunk 原文（用于 RAG prompt 组装）。"""
    from sqlalchemy import select
    from backend.app.models import Chunk

    with engine.connect() as conn:
        return conn.execute(select(Chunk.content).where(Chunk.id == chunk_id)).scalar_one_or_none() or ""


# ---------- 宽定位检索辅助 ----------
def fallback_search(query: str, book_id: int | None = None, limit: int = 5) -> list[dict]:
    """LIKE 子串匹配兜底检索：任何包含查询词片段的 chunk 都能命中。

    当 FTS5 关键词检索因分词/同义表述差异而漏检时，用子串匹配保证召回。
    """
    from backend.app.services.rag.chunker import _get_jieba

    jb = _get_jieba()
    words = [w.strip() for w in jb.cut_for_search(query) if w.strip() and len(w.strip()) >= 2]
    if not words:
        # 无有效词时用原始查询的连续片段
        words = [query.strip()[:20]] if query.strip() else []
    if not words:
        return []

    # 取最长的 3 个词做 OR LIKE
    words = sorted(set(words), key=len, reverse=True)[:3]
    like_clauses = " OR ".join(["c.content LIKE :w%d" % i for i in range(len(words))])
    params: dict = {"limit": limit}
    for i, w in enumerate(words):
        params[f"w{i}"] = f"%{w}%"

    where = f"({like_clauses})"
    if book_id is not None:
        where += " AND c.book_id = :book_id"
        params["book_id"] = book_id

    sql = f"""
    SELECT c.id AS chunk_id, c.book_id, c.chapter_id, c.page_start AS page,
           substr(c.content, 1, 400) AS snippet,
           b.title AS book_title, ch.title AS chapter_title
    FROM chunks c
    LEFT JOIN books b ON b.id = c.book_id
    LEFT JOIN chapters ch ON ch.id = c.chapter_id
    WHERE {where}
    LIMIT :limit
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()

    items = []
    for r in rows:
        items.append({
            "chunk_id": r["chunk_id"],
            "book_id": r["book_id"],
            "book_title": r["book_title"] or "",
            "chapter_id": r["chapter_id"],
            "chapter_title": r["chapter_title"] or None,
            "page": r["page"],
            "snippet": r["snippet"] or "",
        })
    return items


def get_chapter_neighbors(chunk_id: int, radius: int = 1) -> str:
    """拉取 chunk 所在章节的相邻 chunk 上下文（完整章节内容优先）。

    radius=1 表示向前后各取 1 个 chunk；若章节内 chunk 数 ≤ 3，则返回整章。
    """
    from sqlalchemy import select
    from backend.app.models import Chunk

    with engine.connect() as conn:
        cur = conn.execute(
            select(Chunk.chapter_id, Chunk.book_id, Chunk.chunk_index).where(Chunk.id == chunk_id)
        ).mappings().first()
        if cur is None:
            return ""
        chapter_id = cur["chapter_id"]

        rows = conn.execute(
            select(Chunk.content, Chunk.chunk_index)
            .where(Chunk.chapter_id == chapter_id)
            .order_by(Chunk.chunk_index)
        ).all()
        if not rows:
            return ""

        # 章节很小（≤3 块）→ 直接返回整章
        if len(rows) <= 3:
            return "\n".join(r.content for r in rows)

        # 定位当前 chunk，取前后 radius 个
        idx = next((i for i, r in enumerate(rows) if r.chunk_index == cur["chunk_index"]), 0)
        lo = max(0, idx - radius)
        hi = min(len(rows), idx + radius + 1)
        selected = rows[lo:hi]
        return "\n".join(r.content for r in selected)


def get_book_outline(book_id: int | None = None) -> dict:
    """全文兜底：返回书籍目录 + 章节结构（无正文命中时）。"""
    from sqlalchemy import select
    from backend.app.models import Book, Chapter

    with engine.connect() as conn:
        if book_id is not None:
            books = conn.execute(select(Book).where(Book.id == book_id)).all()
        else:
            books = conn.execute(select(Book).limit(3)).all()
        lines = []
        for book in books:
            chapters = conn.execute(
                select(Chapter.title).where(Chapter.book_id == book.id).order_by(Chapter.order_index)
            ).all()
            ch_list = " / ".join(c.title for c in chapters[:40])
            lines.append(f"《{book.title}》目录：{ch_list}")
        snippet = "\n".join(lines) if lines else "（暂无资料）"
        return {
            "chunk_id": 0,
            "book_id": book_id,
            "book_title": "",
            "chapter_id": None,
            "chapter_title": None,
            "page": None,
            "snippet": snippet,
            "is_outline": True,
        }
