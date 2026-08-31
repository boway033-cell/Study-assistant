"""Run the versioned retrieval benchmark without touching the application database."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services.rag.chunker import tokenize, tokenize_query  # noqa: E402
from backend.app.services.rag.evaluation import compare_thresholds, evaluate_cases  # noqa: E402
from backend.app.services.rag.reranker import rerank  # noqa: E402
from backend.app.services.rag.retriever import explicitly_out_of_scope  # noqa: E402


def build_search(corpus: list[dict]):
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE VIRTUAL TABLE docs USING fts5(stable_id UNINDEXED, content, tokenize='unicode61')")
    connection.executemany("INSERT INTO docs(stable_id,content) VALUES (?,?)",
                           [(item["id"], tokenize(item["text"])) for item in corpus])
    by_id = {item["id"]: item for item in corpus}

    def search(query: str, limit: int) -> list[str]:
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
                       "score": 1 / (60 + index + 1)} for index, (stable_id, _score) in enumerate(rows)]
        return [item["stable_id"] for item in rerank(query, candidates, top_k=limit)]
    return search


def expand_cases(dataset: dict) -> list[dict]:
    """展开紧凑的版本化评测声明；生成规则本身固定且不访问用户数据。"""
    if dataset.get("cases"):
        return list(dataset["cases"])
    cases = []
    for item in dataset.get("corpus", []):
        for index, query in enumerate(item.get("queries", []), 1):
            cases.append({"id": f"{item['id']}-q{index}", "query": query, "answerable": True,
                          "relevant_ids": [item["id"]], "strata": item.get("strata", [])})
    cases.extend(dataset.get("cross_cases", []))
    for index, item in enumerate(dataset.get("unanswerable_queries", []), 1):
        if isinstance(item, str):
            item = {"query": item, "strata": ["unanswerable"]}
        cases.append({"id": item.get("id") or f"none-q{index}", "query": item["query"],
                      "answerable": False, "relevant_ids": [], "strata": item.get("strata", ["unanswerable"])})
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "backend" / "eval" / "retrieval_v2.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = {"dataset": dataset["version"], **evaluate_cases(expand_cases(dataset), build_search(dataset["corpus"]))}
    report["thresholds"] = compare_thresholds(report["metrics"], dataset["thresholds"])
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if report["thresholds"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
