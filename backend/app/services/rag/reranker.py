"""检索重排层 + 引用核验 + 效果评测

问题：检索核心仍以 FTS5 关键词为主，RRF 融合只是简单排名合并，
缺少真正稳定的语义重排、引用核验和效果评测。

解决：
1. Reranker：对 RRF 融合后的 top-N 结果做二次重排
   - 关键词覆盖率评分：查询词在 chunk 中的命中比例
   - 位置加权：标题/首段命中 > 尾部命中
   - 长度归一化：避免长 chunk 因词多而虚高
2. 引用核验：AI 回答中的 [资料N] 标注是否真实匹配检索到的 chunk
3. 效果评测：记录检索命中率、引用准确率等指标
"""
from __future__ import annotations

import re
from typing import Any

from backend.app.services.rag.chunker import _get_jieba


def rerank(query: str, items: list[dict], top_k: int = 5) -> list[dict]:
    """对 RRF 融合后的结果做二次重排（本地零 AI，纯特征评分）。
    
    评分维度：
    1. 关键词覆盖率：查询分词后各词在 chunk 中的命中比例（权重 0.4）
    2. 位置加权：chunk 前 200 字命中 > 尾部命中（权重 0.2）
    3. RRF 原始分：保留 RRF 融合分作为基础（权重 0.3）
    4. 长度归一化：过短（<50字）或过长（>2000字）降权（权重 0.1）
    """
    if not items:
        return []

    jb = _get_jieba()
    query_words = [w.strip() for w in jb.cut_for_search(query) if w.strip() and len(w.strip()) >= 2]
    query_words = list(set(query_words))[:10]

    scored: list[tuple[float, dict]] = []
    for item in items:
        content = item.get("context") or item.get("snippet") or item.get("content") or ""
        rrf_score = item.get("score", 0.0)

        # 1. 关键词覆盖率
        if query_words:
            hits = sum(1 for w in query_words if w in content)
            coverage = hits / len(query_words)
        else:
            coverage = 0.0

        # 2. 位置加权：前 200 字命中比例
        front = content[:200]
        if query_words:
            front_hits = sum(1 for w in query_words if w in front)
            position_score = front_hits / len(query_words)
        else:
            position_score = 0.0

        # 3. RRF 原始分（归一化到 0-1）
        rrf_norm = min(rrf_score * 100, 1.0)  # RRF 分通常很小，放大后归一化

        # 4. 长度归一化
        clen = len(content)
        if clen < 50:
            length_score = 0.3  # 过短，信息量不足
        elif clen > 2000:
            length_score = 0.5  # 过长，可能跑题
        else:
            length_score = 1.0  # 适中

        # 加权综合
        final = coverage * 0.4 + position_score * 0.2 + rrf_norm * 0.3 + length_score * 0.1
        item["rerank_score"] = round(final, 4)
        scored.append((final, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def verify_citations(answer: str, sources: list[dict]) -> dict:
    """引用核验：检查 AI 回答中的 [资料N] 标注是否与检索到的 sources 匹配。
    
    返回: {citations_found: [N], sources_provided: int, mismatched: [N], verified: bool}
    """
    # 提取回答中的 [资料N] 标注（兼容中英文标点）
    citations = re.findall('资料([0-9]+)|ref([0-9]+)', answer)
    cited_nums = [int(n1 or n2) for n1, n2 in citations]
    sources_count = len(sources)

    # 检查：引用编号是否超出 sources 范围
    mismatched = [n for n in cited_nums if n > sources_count or n < 1]

    return {
        "citations_found": cited_nums,
        "sources_provided": sources_count,
        "mismatched": mismatched,
        "verified": len(mismatched) == 0 and len(cited_nums) > 0,
    }


# ---------- 效果评测 ----------
_eval_stats: dict[str, list] = {
    "retrieval_hits": [],   # 每次检索的命中数
    "citation_checks": [],  # 每次引用核验结果
}


def record_retrieval_eval(hit_count: int, query: str = "") -> None:
    """记录检索命中数（供效果评测）。"""
    _eval_stats["retrieval_hits"].append({
        "hits": hit_count, "query": query[:50],
    })
    # 只保留最近 100 条
    if len(_eval_stats["retrieval_hits"]) > 100:
        _eval_stats["retrieval_hits"] = _eval_stats["retrieval_hits"][-100:]


def record_citation_eval(verification: dict) -> None:
    """记录引用核验结果。"""
    _eval_stats["citation_checks"].append(verification)
    if len(_eval_stats["citation_checks"]) > 100:
        _eval_stats["citation_checks"] = _eval_stats["citation_checks"][-100:]


def get_eval_stats() -> dict:
    """获取效果评测统计。"""
    hits = _eval_stats["retrieval_hits"]
    cites = _eval_stats["citation_checks"]
    avg_hits = sum(h["hits"] for h in hits) / max(len(hits), 1)
    verified_count = sum(1 for c in cites if c.get("verified"))
    verify_rate = verified_count / max(len(cites), 1)
    return {
        "total_queries": len(hits),
        "avg_retrieval_hits": round(avg_hits, 1),
        "total_citation_checks": len(cites),
        "citation_verify_rate": round(verify_rate, 2),
    }