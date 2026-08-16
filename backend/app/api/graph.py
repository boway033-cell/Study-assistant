"""知识图谱 / 双向链接：自动抽概念 → 全局图谱 → 点概念反查所有出处。"""
from __future__ import annotations

import json
from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import BookAnalysis
from backend.app.services.rag.fts import search

router = APIRouter(prefix="/api/graph", tags=["graph"])

_MAX_NODES = 200  # 图谱节点上限，避免概念过多导致渲染卡顿

# 泛词/虚词停用表：过滤掉不是真正概念的词
_STOPWORDS = {
    "一个", "不同", "影响", "进行", "通过", "可以", "以及", "需要", "问题", "之间",
    "根据", "结果", "方法", "分析", "研究", "理论", "概念", "定义", "变量", "指标",
    "关系", "过程", "发展", "就是", "成为", "作为", "对于", "关于", "其中", "这里",
    "那里", "这些", "那些", "这个", "那个", "主要", "重要", "基本", "一般", "相关",
    "有关", "方面", "内容", "包括", "具有", "没有", "不是", "以及", "或者", "但是",
    "因为", "所以", "如果", "那么", "然后", "我们", "他们", "自己", "什么", "怎么",
    "的", "了", "是", "在", "有", "和", "与", "及", "或", "等", "中", "上", "下",
    "第一", "第二", "第三", "之一", "某种", "每个", "按照", "属于", "对应", "唯一",
    "法则", "常数", "是非", "数集", "趋近", "无限", "某种",
}


def _valid(t: str) -> bool:
    t = t.strip()
    if len(t) <= 1 or t in _STOPWORDS or t.isdigit():
        return False
    return True


def _collect_concepts(db: Session) -> dict[str, set[int]]:
    """从所有书籍分析结果提取概念：关键词 + 定理名（定义句是句子片段，质量差，弃用）。"""
    concepts: dict[str, set[int]] = {}
    for a in db.scalars(select(BookAnalysis)).all():
        terms: list[str] = []
        try:
            for kw in json.loads(a.keywords_json or "[]"):
                if isinstance(kw, str) and _valid(kw):
                    terms.append(kw.strip())
        except (ValueError, TypeError):
            pass
        try:
            for t in json.loads(a.theorems_json or "[]"):
                if isinstance(t, dict) and t.get("type"):
                    tv = str(t["type"]).strip()
                    if _valid(tv):
                        terms.append(tv)
        except (ValueError, TypeError):
            pass
        for t in terms:
            concepts.setdefault(t, set()).add(a.book_id)
    return concepts


@router.get("")
def get_graph(db: Session = Depends(get_db)):
    """全局图谱：概念节点 + 同书关键词共现边。"""
    concepts = _collect_concepts(db)
    # 节点按出现书籍数降序，取前 N
    sorted_concepts = sorted(concepts.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    top = sorted_concepts[:_MAX_NODES]
    top_names = {name for name, _ in top}
    nodes = [{"name": name, "count": len(books), "books": sorted(books)} for name, books in top]

    # 边：同书内关键词两两共现（仅限 top 概念）
    edge_counter: Counter = Counter()
    for a in db.scalars(select(BookAnalysis)).all():
        try:
            kws = [k.strip() for k in json.loads(a.keywords_json or "[]") if isinstance(k, str) and k.strip()]
        except (ValueError, TypeError):
            kws = []
        kws = [k for k in kws if k in top_names]
        for i in range(len(kws)):
            for j in range(i + 1, len(kws)):
                edge_counter[(kws[i], kws[j])] += 1
    edges = [{"source": a, "target": b, "weight": w} for (a, b), w in edge_counter.items()]
    return {"nodes": nodes, "edges": edges, "total": len(nodes)}


@router.get("/concept/{name}/sources")
def concept_sources(name: str, limit: int = 30, db: Session = Depends(get_db)):
    """点概念反查所有出处：全文检索该概念出现的章节/页码。"""
    return search(name, top_k=limit)
