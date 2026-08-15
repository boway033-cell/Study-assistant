"""向量检索：fastembed（ONNX，低内存）+ ChromaDB（本地持久化）

设计要点（低内存）：
- 嵌入模型懒加载单例；不使用时可 unload 释放内存
- 默认关闭（settings.vector_search），开启后才初始化
- 向量与 FTS 混合检索：向量召回 + 关键词召回去重合并
"""
from __future__ import annotations

import json
import os

from backend.app.core.config import settings

_COLLECTION = "study_chunks"
_embedder = None
_collection_obj = None

# 模型名：bge-small-zh-v1.5（384 维，约 100MB，CPU 可跑）
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"

# 模型缓存目录（backend/data/models，已预置 fast-bge-small-zh-v1.5）
MODEL_CACHE_DIR = str(settings.data_dir / "models")


def _get_embedder():
    """懒加载 fastembed（本地缓存已就绪；设置 HF_HUB_OFFLINE 避免联网卡顿）。"""
    global _embedder
    if _embedder is None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(model_name=EMBED_MODEL, cache_dir=MODEL_CACHE_DIR)
    return _embedder


def _get_collection():
    """懒加载 ChromaDB collection。"""
    global _collection_obj
    if _collection_obj is None:
        import chromadb
        client = chromadb.PersistentClient(path=str(settings.chroma_dir))
        _collection_obj = client.get_or_create_collection(
            name=_COLLECTION, metadata={"hnsw:space": "cosine"}
        )
    return _collection_obj


def is_enabled() -> bool:
    return settings.vector_search


def ensure_model_ready() -> tuple[bool, str]:
    """预加载嵌入模型（用户开启向量检索时调用）。返回 (ok, 说明)。"""
    try:
        _get_embedder()
        return True, "嵌入模型已就绪"
    except Exception as e:  # noqa: BLE001
        return False, f"模型加载失败: {e}"


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化（转为纯 Python float，兼容 ChromaDB）。"""
    emb = _get_embedder()
    return [[float(x) for x in v] for v in emb.embed(texts)]


def upsert_chunks(book_id: int, chunks: list[dict]) -> None:
    """写入/更新一本书的向量（chunks: [{id, content, chapter_id, page_start}]）。"""
    if not is_enabled() or not chunks:
        return
    col = _get_collection()
    ids = [f"b{book_id}c{c['id']}" for c in chunks]
    texts = [c["content"] for c in chunks]
    vectors = embed_texts(texts)
    metas = [
        {
            "book_id": book_id,
            "chapter_id": c.get("chapter_id"),
            "page_start": c.get("page_start"),
            "page_end": c.get("page_end"),
            "chunk_id": c["id"],
        }
        for c in chunks
    ]
    col.upsert(ids=ids, embeddings=vectors, documents=texts, metadatas=metas)


def delete_book_vectors(book_id: int) -> None:
    if not is_enabled():
        return
    try:
        col = _get_collection()
        col.delete(where={"book_id": book_id})
    except Exception:  # noqa: BLE001
        pass


def vector_search(query: str, book_id: int | None = None, top_k: int = 5) -> list[dict]:
    """向量检索。返回 [{chunk_id, page_start, page_end, content, chapter_id}]"""
    if not is_enabled():
        return []
    col = _get_collection()
    emb = _get_embedder()
    query_vec = [float(x) for x in list(emb.embed([query]))[0]]
    where = {"book_id": book_id} if book_id is not None else None
    try:
        result = col.query(
            query_embeddings=[query_vec], n_results=top_k, where=where,
        )
    except Exception:  # noqa: BLE001
        return []
    items = []
    ids = result.get("ids", [[]])[0]
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    for i, cid in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        items.append({
            "chunk_id": int(meta.get("chunk_id", 0)),
            "chapter_id": meta.get("chapter_id"),
            "page_start": meta.get("page_start"),
            "page_end": meta.get("page_end"),
            "content": docs[i] if i < len(docs) else "",
            "score": distances[i] if i < len(distances) else 0.0,
            "is_vector": True,
        })
    return items


def unload() -> None:
    """释放嵌入模型内存（设置关闭时调用）。"""
    global _embedder, _collection_obj
    _embedder = None
    _collection_obj = None
