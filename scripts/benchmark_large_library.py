"""Synthetic SQLite/FTS5 capacity benchmark; never opens the user's database."""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1)]


def run(book_count: int, chunks_per_book: int, query_count: int, output: Path | None = None) -> dict:
    random.seed(20260829)
    terms = ["拉格朗日", "行政监督", "中心极限定理", "分层抽样", "知识图谱", "公共责任", "二分查找"]
    with tempfile.TemporaryDirectory(prefix="study-capacity-") as folder:
        path = Path(folder) / "capacity.db"
        db = sqlite3.connect(path)
        db.executescript("""
        PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
        CREATE TABLE books(id INTEGER PRIMARY KEY,title TEXT,status TEXT,created_at TEXT);
        CREATE TABLE chunks(id INTEGER PRIMARY KEY,book_id INTEGER,chunk_index INTEGER,page_start INTEGER,content TEXT);
        CREATE INDEX ix_chunks_book_index ON chunks(book_id,chunk_index);
        CREATE VIRTUAL TABLE fts_books USING fts5(content,book_id UNINDEXED,chunk_id UNINDEXED,tokenize='unicode61');
        """)
        started = time.perf_counter()
        chunk_id = 0
        for first in range(1, book_count + 1, 100):
            last = min(book_count + 1, first + 100)
            db.executemany("INSERT INTO books(id,title,status,created_at) VALUES (?,?,?,?)",
                           [(bid, f"合成资料 {bid:05d}", "ready", "2026-08-29") for bid in range(first, last)])
            rows = []
            fts_rows = []
            for bid in range(first, last):
                for index in range(chunks_per_book):
                    chunk_id += 1
                    term = terms[(bid + index) % len(terms)]
                    content = f"{term} 第{bid}本文献第{index}片段，固定容量测试正文。" * 5
                    rows.append((chunk_id, bid, index, index + 1, content))
                    fts_rows.append((content, bid, chunk_id))
            db.executemany("INSERT INTO chunks VALUES (?,?,?,?,?)", rows)
            db.executemany("INSERT INTO fts_books(content,book_id,chunk_id) VALUES (?,?,?)", fts_rows)
            db.commit()
        ingest_seconds = time.perf_counter() - started
        list_latencies = []
        search_latencies = []
        for index in range(query_count):
            before = time.perf_counter()
            db.execute("SELECT id,title,status FROM books ORDER BY id DESC LIMIT 40 OFFSET ?",
                       ((index * 40) % max(book_count, 1),)).fetchall()
            list_latencies.append((time.perf_counter() - before) * 1000)
            before = time.perf_counter()
            db.execute("SELECT chunk_id FROM fts_books WHERE fts_books MATCH ? ORDER BY rank LIMIT 10",
                       (terms[index % len(terms)],)).fetchall()
            search_latencies.append((time.perf_counter() - before) * 1000)
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        db.close()
        size_mb = path.stat().st_size / 1024 / 1024
        metrics = {
            "books": book_count, "chunks": chunk_id, "database_mb": round(size_mb, 2),
            "ingest_seconds": round(ingest_seconds, 3),
            "book_list_p50_ms": round(statistics.median(list_latencies), 3),
            "book_list_p95_ms": round(percentile(list_latencies, .95), 3),
            "fts_search_p50_ms": round(statistics.median(search_latencies), 3),
            "fts_search_p95_ms": round(percentile(search_latencies, .95), 3),
        }
        thresholds = {"book_list_p95_ms": 200, "fts_search_p95_ms": 500, "database_mb": 8192}
        checks = {name: {"value": metrics[name], "maximum": maximum, "passed": metrics[name] <= maximum}
                  for name, maximum in thresholds.items()}
        report = {"profile": "sqlite-personal-library-v1", "metrics": metrics,
                  "thresholds": {"passed": all(item["passed"] for item in checks.values()), "checks": checks},
                  "note": "合成结果用于版本间比较，不等同于真实 PDF 解析、向量索引或并发写入性能。"}
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
        print(rendered)
        return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--books", type=int, default=1000)
    parser.add_argument("--chunks-per-book", type=int, default=100)
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(max(1, args.books), max(1, args.chunks_per_book), max(5, args.queries), args.output)
    return 0 if report["thresholds"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
