"""重识别知识库 PDF 目录；每本文献写入可恢复 TocRevision。"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.database import SessionLocal
from backend.app.models import Book
from backend.app.services.rag.toc_rebuild import rebuild_all_tocs, rebuild_book_toc
from backend.app.worker.tasks import TaskRecord


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book-id", action="append", type=int, default=[])
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if not args.all and not args.book_id:
        parser.error("请使用 --all 或至少一个 --book-id")
    record = TaskRecord(id="toc-rebuild-cli", max_retries=0)
    if args.all:
        result = asyncio.run(rebuild_all_tocs(record))
    else:
        db = SessionLocal()
        try:
            items = []
            for book_id in args.book_id:
                book = db.get(Book, book_id)
                items.append(rebuild_book_toc(db, book, record) if book else
                             {"book_id": book_id, "status": "failed", "reason": "文献不存在"})
            result = {"total": len(items), "items": items}
        finally:
            db.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
