"""Deterministic retrieval evaluation metrics for versioned local datasets."""
from __future__ import annotations

from collections.abc import Callable
from statistics import mean


def evaluate_cases(cases: list[dict], search: Callable[[str, int], list[str]], *, ks=(1, 3, 5)) -> dict:
    """Evaluate ranked stable document IDs without calling an LLM or user database."""
    rows = []
    answerable_rows = []
    rejection_rows = []
    for case in cases:
        limit = max(ks)
        ranked = list(dict.fromkeys(search(case["query"], limit)))[:limit]
        relevant = set(case.get("relevant_ids", []))
        answerable = bool(case.get("answerable", True))
        strata = case.get("strata") or case.get("stratum") or []
        if isinstance(strata, str):
            strata = [strata]
        row = {"id": case["id"], "query": case["query"], "answerable": answerable,
               "strata": list(dict.fromkeys(str(value) for value in strata if value)),
               "retrieved_ids": ranked}
        if answerable:
            recalls = {f"recall_at_{k}": len(set(ranked[:k]) & relevant) / max(len(relevant), 1) for k in ks}
            first_rank = next((index for index, item in enumerate(ranked, 1) if item in relevant), None)
            cited = ranked[:1]  # deterministic extractive answer cites its leading source
            citation_correct = bool(cited and cited[0] in relevant)
            row.update(recalls)
            row.update({"reciprocal_rank": 1 / first_rank if first_rank else 0.0,
                        "citation_correct": citation_correct})
            answerable_rows.append(row)
        else:
            row["refused"] = not ranked
            rejection_rows.append(row)
        rows.append(row)

    def summarize(selected: list[dict]) -> dict:
        answerable_selected = [row for row in selected if row["answerable"]]
        rejection_selected = [row for row in selected if not row["answerable"]]
        values = {}
        for k in ks:
            values[f"recall_at_{k}"] = round(mean(r[f"recall_at_{k}"] for r in answerable_selected), 4) if answerable_selected else None
        values["mrr"] = round(mean(r["reciprocal_rank"] for r in answerable_selected), 4) if answerable_selected else None
        values["citation_correct_rate"] = round(mean(float(r["citation_correct"]) for r in answerable_selected), 4) if answerable_selected else None
        values["no_answer_rejection_rate"] = round(mean(float(r["refused"]) for r in rejection_selected), 4) if rejection_selected else None
        values.update({"case_count": len(selected), "answerable_count": len(answerable_selected),
                       "unanswerable_count": len(rejection_selected)})
        return values

    metrics = summarize(rows)
    # 顶层指标保持旧版数值契约：没有对应案例时返回 0；分层报告用 null 表示不适用。
    metrics = {key: (0.0 if value is None else value) for key, value in metrics.items()}
    stratum_names = sorted({name for row in rows for name in row["strata"]})
    by_stratum = {name: summarize([row for row in rows if name in row["strata"]]) for name in stratum_names}
    return {"metrics": metrics, "by_stratum": by_stratum, "cases": rows}


def compare_thresholds(metrics: dict, thresholds: dict) -> dict:
    checks = {name: {"value": metrics.get(name, 0), "minimum": minimum,
                     "passed": metrics.get(name, 0) >= minimum}
              for name, minimum in thresholds.items()}
    return {"passed": all(item["passed"] for item in checks.values()), "checks": checks}
