"""目录候选的可解释评分、树构建与 AI 输出约束。

评分只决定“是否值得复核”和层级可信度，不凭空补标题。AI 返回的每个标题必须能映射
到候选或原文页证据；否则拒绝采用。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re


@dataclass
class TocEvidence:
    title: str
    page: int
    level: int
    source: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    parent_index: int | None = None


_SEMANTIC_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百千万0-9]+(?:章|节|篇|编|部分)|"
    r"[一二三四五六七八九十百千万]+、|（[一二三四五六七八九十百千万0-9]+）|"
    r"\d{1,3}(?:\.\d{1,3})+)"
)
_SENTENCE_RE = re.compile(r"[。！？!?；;]|(?:因此|但是|由于|如果|为了)")


def score_toc_candidate(row: dict) -> TocEvidence:
    title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
    priority = int(row.get("source_priority", 1))
    source = {0: "bookmark", 1: "text", 2: "layout"}.get(priority, "unknown")
    score = 0.12
    reasons: list[str] = []
    if source == "bookmark":
        score += 0.38
        reasons.append("PDF书签")
    elif source == "layout":
        score += 0.30
        reasons.append("版面标题块")
    else:
        score += 0.16
        reasons.append("全文行候选")
    if _SEMANTIC_RE.match(title):
        score += 0.42
        reasons.append("编号语义")
    if 2 <= len(title) <= 45:
        score += 0.08
        reasons.append("标题长度合理")
    if len(title) > 80:
        score -= 0.28
        reasons.append("过长疑似正文")
    if _SENTENCE_RE.search(title):
        score -= 0.45
        reasons.append("完整句式负证据")
    if re.fullmatch(r"[\d\W_]+", title):
        score = 0.0
        reasons.append("纯页码/符号")
    return TocEvidence(
        title=title,
        page=max(1, int(row.get("page") or 1)),
        level=max(1, min(4, int(row.get("level") or 1))),
        source=source,
        confidence=round(max(0.0, min(1.0, score)), 3),
        reasons=reasons,
    )


def score_toc_rows(rows: list[dict], minimum: float = 0.24) -> list[dict]:
    """为候选附加评分，并仅删除确定性噪声；低置信度仍可进入人工/AI复核。"""
    output: list[dict] = []
    for row in rows:
        evidence = score_toc_candidate(row)
        if evidence.confidence < minimum:
            continue
        enriched = dict(row)
        enriched["confidence"] = evidence.confidence
        enriched["evidence"] = evidence.reasons
        output.append(enriched)
    return output


def build_candidate_tree(rows: list[dict]) -> list[dict]:
    """依据已评分层级构建候选树；缺失中间层时压实，不生成虚构节点。"""
    roots: list[dict] = []
    stack: list[tuple[int, dict]] = []
    for index, row in enumerate(rows):
        evidence = score_toc_candidate(row)
        level = evidence.level
        while stack and stack[-1][0] >= level:
            stack.pop()
        if not stack:
            level = 1
        else:
            level = min(level, stack[-1][0] + 1)
        node = {
            **asdict(evidence),
            "level": level,
            "index": index,
            "children": [],
        }
        if stack:
            node["parent_index"] = stack[-1][1]["index"]
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((level, node))
    return roots


def build_review_packet(rows: list[dict], issues: list[dict], page_excerpt: dict[int, str]) -> dict:
    """给 AI 的最小证据包：候选、问题和对应页摘录，不发送整本书。"""
    candidates = []
    for index, row in enumerate(rows):
        evidence = score_toc_candidate(row)
        candidates.append({
            "candidate_id": index,
            "title": evidence.title,
            "page": evidence.page,
            "proposed_level": evidence.level,
            "confidence": evidence.confidence,
            "reasons": evidence.reasons,
            "excerpt": page_excerpt.get(evidence.page, "")[:600],
        })
    return {"candidates": candidates, "issues": issues[:30]}


def validate_ai_review(rows: list[dict], reviewed: list[dict]) -> list[dict]:
    """只接受引用 candidate_id 且标题不变的 AI 层级调整。"""
    accepted: list[dict] = []
    for item in reviewed:
        candidate_id = item.get("candidate_id")
        if not isinstance(candidate_id, int) or not (0 <= candidate_id < len(rows)):
            continue
        original = rows[candidate_id]
        if re.sub(r"\s+", "", str(item.get("title") or "")) != re.sub(
            r"\s+", "", str(original.get("title") or "")
        ):
            continue
        accepted.append({
            "title": original["title"],
            "page": int(original.get("page") or 1),
            "level": max(1, min(4, int(item.get("level") or original.get("level") or 1))),
            "candidate_id": candidate_id,
        })
    return accepted
