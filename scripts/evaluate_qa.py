"""多轮对话上下文的确定性评测（CI 门禁）。

与 ``scripts/evaluate_retrieval.py`` 的分工：

- ``evaluate_retrieval`` 测的是「单轮查询 → 片段」的召回能力。
- 本脚本测的是「多轮语境 → 意图 / 改写 / 澄清 / 拒答」的解析能力。

全程不调用模型、不读用户数据库：意图与改写由规则产生，检索可达性用内存
SQLite + FTS5 模拟。这样门禁在任何 CI 机器上结果一致。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services.rag.chunker import tokenize, tokenize_query  # noqa: E402
from backend.app.services.rag.reranker import rerank  # noqa: E402
from backend.app.services.rag.retriever import explicitly_out_of_scope  # noqa: E402
from backend.app.services.qa import context as qa_context  # noqa: E402
from backend.app.services.qa import prompts as qa_prompts  # noqa: E402


def build_search(corpus: list[dict]):
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE VIRTUAL TABLE docs USING fts5(stable_id UNINDEXED, content, tokenize='unicode61')")
    connection.executemany("INSERT INTO docs(stable_id,content) VALUES (?,?)",
                           [(item["id"], tokenize(item["text"])) for item in corpus])
    by_id = {item["id"]: item for item in corpus}

    def search(query: str, limit: int = 5) -> list[str]:
        if explicitly_out_of_scope(query):
            return []
        expression = tokenize_query(query)
        if not expression:
            return []
        rows = connection.execute(
            "SELECT stable_id, bm25(docs) AS score FROM docs WHERE docs MATCH ? ORDER BY score LIMIT ?",
            (expression, limit * 2),
        ).fetchall()
        candidates = [{"stable_id": stable_id, "snippet": by_id[stable_id]["text"],
                       "score": 1 / (60 + index + 1)}
                      for index, (stable_id, _score) in enumerate(rows)]
        return [item["stable_id"] for item in rerank(query, candidates, top_k=limit)]

    return search


def evaluate_cases(cases: list[dict], search) -> dict:
    rows = []
    for case in cases:
        turns = [qa_context.Turn(question=item.get("question", ""), answer=item.get("answer", ""),
                                 sources=item.get("sources", []))
                 for item in case.get("turns", [])]
        turn = qa_context.resolve_turn(case["question"], turns)
        ranked = search(turn.search_query) if turn.intent != qa_context.INTENT_SUMMARIZE else []
        expected_doc = case.get("expected_doc")
        row = {
            "id": case["id"],
            "intent": turn.intent,
            "expected_intent": case["expected_intent"],
            "intent_match": turn.intent == case["expected_intent"],
            "reason": turn.reason,
            "expected_reason": case.get("expected_reason"),
            "search_query": turn.search_query,
            "retrieved_ids": ranked,
            "expected_doc": expected_doc,
            "doc_hit": (expected_doc in ranked[:1]) if expected_doc else None,
            "history_chars": len(turn.history_block),
            "abstain_expected": bool(case.get("expected_abstain")),
            "abstain_detected": (not ranked) if case.get("expected_abstain") else None,
            "note": case.get("note", ""),
        }
        # 澄清用例的 reason 必须与声明一致，否则「该问的没问」会被意图命中掩盖。
        if case.get("expected_reason"):
            row["reason_match"] = turn.reason == case["expected_reason"]
        else:
            row["reason_match"] = None
        rows.append(row)

    def _rate(key: str, selected: list[dict]) -> float | None:
        values = [row[key] for row in selected if row[key] is not None]
        return round(sum(values) / len(values), 4) if values else None

    clarify_rows = [row for row in rows if row["expected_intent"] == qa_context.INTENT_CLARIFY]
    # 混淆矩阵：期望澄清且真的澄清 / 不该澄清却拦下 / 该澄清却放过。
    true_positive = sum(1 for row in rows
                        if row["expected_intent"] == qa_context.INTENT_CLARIFY
                        and row["intent"] == qa_context.INTENT_CLARIFY)
    false_positive = sum(1 for row in rows
                         if row["expected_intent"] != qa_context.INTENT_CLARIFY
                         and row["intent"] == qa_context.INTENT_CLARIFY)
    false_negative = len(clarify_rows) - true_positive
    precision = true_positive / max(true_positive + false_positive, 1)
    recall = true_positive / max(true_positive + false_negative, 1)

    doc_rows = [row for row in rows if row["expected_doc"]]
    abstain_rows = [row for row in rows if row["abstain_expected"]]

    metrics = {
        "intent_accuracy": _rate("intent_match", rows),
        "rewrite_hit_rate": _rate("doc_hit", doc_rows),
        "clarify_precision": round(precision, 4),
        "clarify_recall": round(recall, 4),
        "clarify_reason_match": _rate("reason_match", clarify_rows),
        "abstention_detection_rate": _rate("abstain_detected", abstain_rows),
        "context_char_max": max((row["history_chars"] for row in rows), default=0),
        "case_count": len(rows),
        "doc_case_count": len(doc_rows),
        "clarify_case_count": len(clarify_rows),
        "abstain_case_count": len(abstain_rows),
    }
    return {"metrics": metrics, "cases": rows}


def compare_thresholds(metrics: dict, thresholds: dict) -> dict:
    # 上界型阈值：预算是上限，越小越好，必须用 <= 判定。
    _UPPER_BOUND = {"context_char_max"}
    checks = {}
    for name, target in thresholds.items():
        value = metrics.get(name)
        if value is None:
            checks[name] = {"value": None, "target": target, "status": "insufficient"}
        elif name in _UPPER_BOUND:
            checks[name] = {"value": value, "target": target,
                            "status": "passed" if value <= target else "failed"}
        else:
            checks[name] = {"value": value, "target": target,
                            "status": "passed" if value >= target else "failed"}
    return {"passed": all(item["status"] == "passed" for item in checks.values()),
            "checks": checks,
            "insufficient": sorted(name for name, item in checks.items()
                                   if item["status"] == "insufficient")}


def prompt_contract(dataset: dict) -> dict:
    """提示词契约：按意图分别锁定「历史怎么处理」和「引用规则是否还在」。

    - ``followup`` / ``summarize``：历史必须进提示，且必须显式标注不是证据。
    - ``new_question``：历史**不得**进提示（独立新问题不受上文污染），但引用规则必须在。
    """
    corpus = {item["id"]: item for item in dataset["corpus"]}
    sources = [{"chunk_id": 1, "book_id": 1, "book_title": "测试文献",
                "chapter_title": "第一章", "page_start": 1, "page_end": 2,
                "context": corpus["did"]["text"], "snippet": corpus["did"]["text"]}]
    history = "- 已讨论话题：双重差分"
    report = {}
    for intent in (qa_context.INTENT_NEW, qa_context.INTENT_FOLLOWUP, qa_context.INTENT_SUMMARIZE):
        messages = qa_prompts.build_messages(intent, "它有什么前提", sources, history)
        system, user = messages[0]["content"], messages[1]["content"]
        uses_history = "历史问答" in user
        marks_non_evidence = ("不构成事实证据" in system or "不是可引证的原文" in system
                              or "不构成事实证据" in user or "仅用于理解指代" in user)
        if intent == qa_context.INTENT_NEW:
            ok = (not uses_history) and ("[资料N]" in system)
        else:
            ok = uses_history and marks_non_evidence and ("[资料N]" in system)
        report[intent] = {"uses_history": uses_history,
                          "marks_history_as_non_evidence": marks_non_evidence,
                          "keeps_citation_rule": "[资料N]" in system,
                          "passed": ok}
    report["passed"] = all(row["passed"] for intent, row in report.items() if intent != "passed")
    return report


def baseline_compare(cases: list[dict], search) -> dict:
    """与改造前的多轮策略逐案对照，量化收益。

    旧策略（本任务之前的 ``chat.py`` 行为）：

    1. 只有正则命中指代词才把**上一轮问题**拼到检索查询前面；
    2. 省略型追问（无指代词）不补任何上下文；
    3. 没有任何澄清通道——指代不明也照答。

    对照只比较两个可观测结果：Top-1 命中率、应澄清时的澄清率。
    """
    legacy_anaphora = re.compile(r"它|这个|该|上述|前者|后者|那|此|这些|they|that|\bit\b", re.I)
    doc_rows = [case for case in cases if case.get("expected_doc")]
    clarify_rows = [case for case in cases
                    if case.get("expected_intent") == qa_context.INTENT_CLARIFY]
    legacy_hits = new_hits = 0
    per_case = []
    for case in doc_rows:
        prior = [item.get("question", "") for item in case.get("turns", [])]
        legacy_query = (prior[-1][:180] + " " + case["question"]
                        if prior and legacy_anaphora.search(case["question"])
                        else case["question"])
        legacy_top = (search(legacy_query) or [None])[:1]
        turns = [qa_context.Turn(question=item.get("question", ""), answer=item.get("answer", ""))
                 for item in case.get("turns", [])]
        turn = qa_context.resolve_turn(case["question"], turns)
        new_top = (search(turn.search_query) or [None])[:1]
        expected = case["expected_doc"]
        legacy_ok = legacy_top == [expected]
        new_ok = new_top == [expected]
        legacy_hits += legacy_ok
        new_hits += new_ok
        per_case.append({"id": case["id"], "expected": expected,
                         "legacy_top1": legacy_top[0], "new_top1": new_top[0],
                         "legacy_hit": legacy_ok, "new_hit": new_ok})
    legacy_clarify = 0
    new_clarify = 0
    for case in clarify_rows:
        turns = [qa_context.Turn(question=item.get("question", ""), answer=item.get("answer", ""))
                 for item in case.get("turns", [])]
        legacy_clarify += 0  # 旧策略没有澄清通道
        new_clarify += qa_context.resolve_turn(case["question"], turns).intent == qa_context.INTENT_CLARIFY
    return {
        "top1_hit_rate": {"legacy": round(legacy_hits / max(len(doc_rows), 1), 4),
                          "current": round(new_hits / max(len(doc_rows), 1), 4),
                          "delta": round((new_hits - legacy_hits) / max(len(doc_rows), 1), 4)},
        "clarify_rate": {"legacy": round(legacy_clarify / max(len(clarify_rows), 1), 4),
                         "current": round(new_clarify / max(len(clarify_rows), 1), 4)},
        "doc_case_count": len(doc_rows),
        "clarify_case_count": len(clarify_rows),
        "per_case": per_case,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path,
                        default=ROOT / "backend" / "eval" / "qa_multiturn.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-baseline", action="store_true",
                        help="跳过与改造前策略的对照计算")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    search = build_search(dataset["corpus"])
    report = {"dataset": dataset["version"],
              **evaluate_cases(dataset["cases"], search)}
    report["prompt_contract"] = prompt_contract(dataset)
    if not args.no_baseline:
        report["baseline_comparison"] = baseline_compare(dataset["cases"], search)
    report["thresholds"] = compare_thresholds(report["metrics"], dataset["thresholds"])
    if not report["prompt_contract"]["passed"]:
        report["thresholds"]["passed"] = False
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    # CI 的 Windows 控制台可能是 cp1252；控制台写 ASCII，文件保留 UTF-8 原文。
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["thresholds"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())