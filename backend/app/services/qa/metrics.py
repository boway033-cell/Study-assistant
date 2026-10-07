"""问答质量三维度指标：准确性、相关性、响应时延。

指标定义（与 docs/AI_QA_QUALITY_METRICS.md 一一对应）
--------------------------------------------------
**准确性 accuracy**
- ``citation_reference_valid_rate``：回答里 [资料N] 编号都落在真实提供的来源范围内。
- ``citation_support_rate``：被引片段与相邻句子实词重合度 ≥ 阈值（默认 0.34）的比例，
  仅作词面筛查，不判断语义、否定关系或事实正确性。
- ``abstention_precision``：无人工相关性标签时为 None；无来源比例单独统计。
- ``clarification_precision`` / ``clarification_recall``：该澄清时澄清、不该时不多问。

**相关性 relevance**
- ``retrieval_hit_rate``：可回答问题的检索命中率。
- ``history_use_rate``：追问轮里答案确实用到了历史锚点的比例。
- ``source_redundancy``：同一本书在来源列表中的占比（过高说明来源不分散）。

**响应时延 latency**
- ``ttft_p50/p95``：请求发出到首个 token 的毫秒数（用户感知快慢的主因）。
- ``e2e_p50/p95``：整轮端到端毫秒数。
- ``retrieval_p95``：检索阶段耗时。

存储是进程内滑动窗口（沿用 reranker 的 ``_eval_stats`` 约定），只保留最近 N 条，
不落用户内容。分位数用最近邻法，避免样本少时被插值伪造精度。
"""
from __future__ import annotations

import re
import math
import threading
import time
from collections import deque

from backend.app.services.qa.context import content_terms

_WINDOW = 200

# 仅检测弃答措辞，不判断弃答是否正确。
_ABSTENTION_MARKERS = (
    "资料中未找到", "未找到相关", "资料未涉及", "没有提到", "未提及",
    "无法回答", "不能回答", "超出资料范围", "资料范围外", "无法从资料中",
)

_lock = threading.Lock()
_samples: deque[dict] = deque(maxlen=_WINDOW)


# ---------- 记录 ----------


def record_turn(metrics: dict) -> None:
    """记录一轮问答的时延与判定结果。字段缺失会被忽略，不抛错。"""
    row = {
        "ts": time.time(),
        "intent": metrics.get("intent", "new_question"),
        "status": metrics.get("status", "completed"),
        "model_called": metrics.get("model_called", metrics.get("intent") != "clarify"),
        "ttft_ms": _num(metrics.get("ttft_ms")),
        "e2e_ms": _num(metrics.get("e2e_ms")),
        "retrieval_ms": _num(metrics.get("retrieval_ms")),
        "rewrite_ms": _num(metrics.get("rewrite_ms")),
        "prompt_chars": _num(metrics.get("prompt_chars")),
        "source_count": _num(metrics.get("source_count")),
        "cited_count": _num(metrics.get("cited_count")),
        "citation_valid": metrics.get("citation_valid"),
        "citation_support_rate": _num(metrics.get("citation_support_rate")),
        "abstained": metrics.get("abstained"),
        "history_used": metrics.get("history_used"),
        "redundant_source_ratio": _num(metrics.get("redundant_source_ratio")),
    }
    with _lock:
        _samples.append(row)


def reset() -> None:
    with _lock:
        _samples.clear()


def snapshot() -> list[dict]:
    with _lock:
        return list(_samples)


# ---------- 计算 ----------


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def percentile(values: list[float], fraction: float) -> float | None:
    """最近邻分位数；样本不足 5 条时返回 None，避免伪造稳定性。"""
    ordered = sorted(values)
    if len(ordered) < 5:
        return None
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 1)


def is_abstention(answer: str) -> bool:
    return any(marker in (answer or "") for marker in _ABSTENTION_MARKERS)


def citation_support_rate(answer: str, sources: list[dict],
                          *, min_overlap: float = 0.34) -> tuple[float | None, list[dict]]:
    """逐条核对「[资料N] 前后的句子」是否与被引片段有足够实词重合。

    返回 (词面筛查通过率, 明细)。不是语义支持率；supported 为兼容字段。
    """
    text = answer or ""
    pattern = re.compile(r"\[(?:资料([0-9]+)|ref([0-9]+))\]", re.IGNORECASE)
    matches = list(pattern.finditer(text))
    if not matches:
        return None, []
    contexts = [str(item.get("context") or item.get("snippet") or "") for item in sources]
    details = []
    for index, match in enumerate(matches):
        number = int(match.group(1) or match.group(2))
        left = text[max(0, match.start() - 90):match.start()]
        right = text[match.end():match.end() + 90]
        sentence = (left.split("。")[-1] if index else left) + right.split("。")[0]
        if not 1 <= number <= len(contexts):
            details.append({"ref": number, "supported": False, "lexical_match": False, "reason": "out_of_range",
                            "overlap": 0.0})
            continue
        passage_terms = set(content_terms(contexts[number - 1]))
        claim_terms = set(content_terms(sentence))
        overlap = (len(passage_terms & claim_terms) / len(claim_terms)) if claim_terms else 0.0
        details.append({"ref": number, "supported": overlap >= min_overlap, "lexical_match": overlap >= min_overlap,
                        "overlap": round(overlap, 3),
                        "reason": "lexical_overlap" if overlap >= min_overlap else "weak_overlap"})
    supported = sum(1 for item in details if item["supported"])
    return round(supported / len(details), 4), details


def redundant_source_ratio(sources: list[dict]) -> float | None:
    """同一本书来源占比；≥1.0 说明只有一本书，无法交叉印证。"""
    if not sources:
        return None
    titles = [str(item.get("book_id") or item.get("book_title") or "").strip() for item in sources]
    counts: dict[str, int] = {}
    for title in titles:
        counts[title] = counts.get(title, 0) + 1
    return round(max(counts.values()) / len(titles), 3)


def summarize(rows: list[dict] | None = None) -> dict:
    """把窗口内的样本聚合成三维度指标。"""
    rows = rows if rows is not None else snapshot()
    model_rows = [row for row in rows if row.get("model_called", True)]
    completed = [row for row in model_rows if row.get("status", "completed") == "completed"]
    local_rows = [row for row in rows if not row.get("model_called", True)]
    ttft = [row["ttft_ms"] for row in model_rows if row["ttft_ms"] is not None]
    e2e = [row["e2e_ms"] for row in model_rows if row["e2e_ms"] is not None]
    retrieval = [row["retrieval_ms"] for row in rows if row["retrieval_ms"] is not None]
    support = [row["citation_support_rate"] for row in completed if row["citation_support_rate"] is not None]
    citation_valid = [bool(row["citation_valid"]) for row in completed if row["citation_valid"] is not None]
    abstained = [row for row in completed if row["abstained"]]
    followups = [row for row in completed if row["intent"] == "followup"]
    history_used = [bool(row["history_used"]) for row in followups if row["history_used"] is not None]
    clarified = [row for row in rows if row["intent"] == "clarify"]
    redundancy = [row["redundant_source_ratio"] for row in rows if row["redundant_source_ratio"] is not None]
    prompt_chars = [row["prompt_chars"] for row in rows if row["prompt_chars"] is not None]

    def _ratio(values: list[bool]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    def _mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    return {
        "sample_count": len(rows),
        "accuracy": {
            "citation_reference_valid_rate": _ratio(citation_valid),
            "citation_support_rate": _mean(support),
            "abstention_count": len(abstained),
            "abstention_precision": None,
            "abstention_empty_source_rate": _ratio([bool(row["source_count"] == 0) for row in abstained])
            if abstained else None,
            "failed_count": sum(row.get("status") == "failed" for row in rows),
            "model_completed_count": len(completed),
            "clarification_count": len(clarified),
            "clarification_rate": round(len(clarified) / len(rows), 4) if rows else None,
        },
        "relevance": {
            "retrieval_hit_rate": _ratio([bool((row["source_count"] or 0) > 0) for row in completed]),
            "followup_count": len(followups),
            "history_use_rate": _ratio(history_used),
            "source_redundancy_mean": _mean(redundancy),
            "sources_per_answer": _mean([row["source_count"] for row in rows
                                         if row["source_count"] is not None]),
        },
        "latency": {
            "ttft_p50": percentile(ttft, 0.50),
            "ttft_p95": percentile(ttft, 0.95),
            "e2e_p50": percentile(e2e, 0.50),
            "e2e_p95": percentile(e2e, 0.95),
            "retrieval_p95": percentile(retrieval, 0.95),
            "prompt_chars_p50": percentile(prompt_chars, 0.50),
            "model_sample_count": len(model_rows),
            "local_sample_count": len(local_rows),
            "local_e2e_p95": percentile([row["e2e_ms"] for row in local_rows if row["e2e_ms"] is not None], 0.95),
        },
    }


def gate_report(summary: dict, thresholds: dict) -> dict:
    """按阈值给出通过/未通过。

    样本不足的指标标记为 ``insufficient``，并且**不算通过**——否则门禁会在
    「刚重启、还没积累样本」时给出虚假绿灯。
    """
    checks = {}
    for name, minimum in thresholds.items():
        value = _dig(summary, name)
        if value is None or summary.get("sample_count", 5) < 5:
            checks[name] = {"value": None, "minimum": minimum, "status": "insufficient"}
        else:
            checks[name] = {"value": value, "minimum": minimum,
                            "status": "passed" if value >= minimum else "failed"}
    failed = sorted(name for name, item in checks.items() if item["status"] == "failed")
    insufficient = sorted(name for name, item in checks.items()
                          if item["status"] == "insufficient")
    return {"passed": not failed and not insufficient, "failed": failed, "checks": checks,
            "insufficient": insufficient}


def _dig(payload: dict, dotted: str):
    node = payload
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, (int, float)) else None


def format_duration(ms: float | None) -> str:
    if ms is None:
        return "-"
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{int(ms)}ms"


__all__ = ["record_turn", "summarize", "gate_report", "citation_support_rate",
           "is_abstention", "redundant_source_ratio", "percentile", "snapshot", "reset",
           "format_duration"]
