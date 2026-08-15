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
