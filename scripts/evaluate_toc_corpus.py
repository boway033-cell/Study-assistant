"""Replay local PDF TOC cases without changing books, chapters, or caches."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.config import settings
from backend.app.core.database import SessionLocal
from backend.app.models import Book, Chapter
from backend.app.services.analyzer.layout import analyze_structured
from backend.app.services.parser.structured import StructuredDocument
from backend.app.services.rag.toc_heuristic import _norm_title_key, contents_page_numbers
from backend.app.services.rag.toc_import import select_import_toc
from backend.app.services.rag.toc_rebuild import _native_toc

DEFAULT_CORPUS = ROOT / "backend/tests/fixtures/toc_corpus.json"


def _read_pages(book: Book, source: Path) -> tuple[list[str], object | None]:
    cache = settings.structured_dir / f"{book.file_hash or book.id}.json"
    if cache.exists():
        document = StructuredDocument.load_json(cache)
        layout = analyze_structured(document)
        return [layout.clean_page_text(i) for i in range(len(layout.pages))], layout
    import pymupdf

    with pymupdf.open(source) as pdf:
        return [page.get_text("text") for page in pdf], None


def _score_rows(rows: list[dict], case: dict, contents: set[int]) -> dict:
    by_key: dict[str, list[dict]] = {}
    for row in rows:
        by_key.setdefault(_norm_title_key(row["title"]), []).append(row)
    missing = []
    wrong_page = []
    wrong_level = []
    for anchor in case["anchors"]:
        found = by_key.get(_norm_title_key(anchor["title"]), [])
        if not found:
            missing.append(anchor)
        elif not any(int(row["page"]) == anchor["page"] for row in found):
            wrong_page.append({"expected": anchor, "found": sorted({int(row["page"]) for row in found})})
        elif not any(int(row["page"]) == anchor["page"] and int(row["level"]) == anchor["level"]
                     for row in found):
            wrong_level.append({"expected": anchor, "found": [int(row["level"]) for row in found
                                                            if int(row["page"]) == anchor["page"]]})
    duplicate_keys = len(rows) - len({(_norm_title_key(row["title"]), int(row["page"]))
                                       for row in rows})
    pages = [int(row["page"]) for row in rows]
    hits = len(case["anchors"]) - len(missing) - len(wrong_page) - len(wrong_level)
    return {
        "rows": len(rows),
        "anchor_hits": hits,
        "anchor_total": len(case["anchors"]),
        "missing": missing,
        "wrong_page": wrong_page,
        "wrong_level": wrong_level,
        "rows_on_toc_pages": [row["title"] for row in rows if int(row["page"]) in contents][:10],
        "duplicate_title_pages": duplicate_keys,
        "nonmonotonic_pairs": sum(right < left for left, right in zip(pages, pages[1:])),
        "row_count_ok": case["min_rows"] <= len(rows) <= case["max_rows"],
    }


def evaluate_case(db, case: dict) -> dict:
    book = db.get(Book, case["book_id"])
    if book is None:
        return {"id": case["id"], "status": "unavailable", "reason": "Local book ID not found"}
    source = settings.uploads_dir / book.file_path
    if not source.is_file():
        return {"id": case["id"], "status": "unavailable", "reason": "Local PDF not found"}
    with source.open("rb") as handle:
        actual_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual_hash != case["sha256"]:
        return {"id": case["id"], "status": "unavailable", "reason": "PDF SHA-256 mismatch"}

    cleaned, layout = _read_pages(book, source)
    detected = contents_page_numbers(cleaned)
    if layout:
        detected.update(contents_page_numbers(["\n".join(block.text for block in page)
                                               for page in layout.pages]))
    candidate = select_import_toc("pdf", _native_toc(source), cleaned, layout)
    stored = [{"title": ch.title, "page": ch.start_page, "level": ch.level}
              for ch in db.scalars(select(Chapter).where(Chapter.book_id == book.id)
                                   .order_by(Chapter.order_index)).all()]
    current = _score_rows(candidate, case, detected)
    previous = _score_rows(stored, case, detected)
    expected = set(case["toc_pages"])
    forbidden = set(case.get("forbidden_toc_pages", []))
    boundaries_ok = detected == expected and not detected.intersection(forbidden)
    passed = (
        boundaries_ok and current["anchor_hits"] == current["anchor_total"]
        and current["row_count_ok"] and not current["rows_on_toc_pages"]
        and not current["duplicate_title_pages"] and not current["nonmonotonic_pairs"]
    )
    return {
        "id": case["id"], "book_id": book.id, "kind": case["kind"],
        "status": "pass" if passed else "fail", "toc_pages_expected": sorted(expected),
        "toc_pages_detected": sorted(detected), "forbidden_detected": sorted(detected & forbidden),
        "candidate": current, "stored": previous,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--case", action="append", default=[], help="Repeat to select case IDs")
    parser.add_argument("--output", type=Path, help="Write full JSON result")
    args = parser.parse_args()
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    cases = [case for case in corpus["cases"] if not args.case or case["id"] in args.case]
    with SessionLocal() as db:
        results = [evaluate_case(db, case) for case in cases]
    report = {
        "corpus_version": corpus["version"], "cases": len(results),
        "passed": sum(item["status"] == "pass" for item in results),
        "failed": sum(item["status"] == "fail" for item in results),
        "unavailable": sum(item["status"] == "unavailable" for item in results),
        "anchor_hits": sum(item["candidate"]["anchor_hits"] for item in results
                           if item["status"] != "unavailable"),
        "anchor_total": sum(item["candidate"]["anchor_total"] for item in results
                            if item["status"] != "unavailable"),
        "results": results,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"cases={report['cases']} pass={report['passed']} fail={report['failed']} "
          f"unavailable={report['unavailable']} anchors={report['anchor_hits']}/{report['anchor_total']}")
    for item in results:
        if item["status"] == "unavailable":
            print(f"{item['id']}: unavailable ({item['reason']})")
            continue
        candidate, stored = item["candidate"], item["stored"]
        print(f"{item['id']}: {item['status']} pages={item['toc_pages_detected']} "
              f"candidate={candidate['rows']} rows, {candidate['anchor_hits']}/{candidate['anchor_total']} anchors; "
              f"stored={stored['rows']} rows, {stored['anchor_hits']}/{stored['anchor_total']} anchors")
        if item["status"] == "fail":
            print("  missing:", [entry["title"] for entry in candidate["missing"]])
            print("  wrong page:", candidate["wrong_page"])
            print("  wrong level:", candidate["wrong_level"])
    return 0 if report["passed"] == report["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
