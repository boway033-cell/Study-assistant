"""Regressions for deep-reading summary cache invalidation."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base
from backend.app.models import Book, BookDeep, Chapter, Chunk


@pytest.mark.parametrize("renamed_node", ["root", "section"])
def test_directory_rename_rebuilds_cached_chapter_summary(monkeypatch, renamed_node):
    from backend.app.api import deep
    from backend.app.core import database
    from backend.app.services import llm
    from backend.app.worker import tasks

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        book = Book(title="缓存验证书目", file_type="pdf", file_path="sample.pdf", status="ready")
        db.add(book)
        db.flush()
        root = Chapter(book_id=book.id, title="第一章", level=1, order_index=0, start_page=1)
        db.add(root)
        db.flush()
        section = Chapter(book_id=book.id, title="第一节", level=2, parent_id=root.id,
                          order_index=1, start_page=1)
        db.add(section)
        db.flush()
        db.add(Chunk(book_id=book.id, chapter_id=section.id, chunk_index=0,
                     page_start=1, page_end=1, content="始终不变的正文"))
        db.commit()
        book_id = book.id
        target_id = root.id if renamed_node == "root" else section.id

    calls = []

    async def summarize(provider, title, toc, texts, on_progress=None, on_chapter=None):
        calls.append({"book_title": title, "headings": [item["title"] for item in toc],
                      "text": texts[1]})
        chapter = next(item for item in toc if item["level"] == 1)
        result = {"title": chapter["title"], "key": str(chapter["chapter_id"]),
                  "summary": f"本次生成的摘要 {len(calls)}"}
        on_chapter(1, result)
        return [result]

    async def card(*args, **kwargs):
        return "阅读卡"

    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(llm, "load_llm_config", lambda *args: {"configured": True})
    monkeypatch.setattr(llm.LLMRouter, "get", lambda *args: object())
    monkeypatch.setattr(tasks, "update_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(deep, "summarize_by_toc", summarize)
    monkeypatch.setattr(deep, "build_paper_card", card)
    monkeypatch.setattr(deep, "audit_paper_card", lambda *args: {})
    record = SimpleNamespace(result=None)

    try:
        asyncio.run(deep.run_deep_analysis(record, book_id))
        with factory() as db:
            first_hash = json.loads(db.scalar(select(BookDeep)).chapter_hashes_json)[0]["hash"]

        asyncio.run(deep.run_deep_analysis(record, book_id))
        assert len(calls) == 1, "相同目录与正文应命中缓存"

        with factory() as db:
            db.get(Chapter, target_id).title = "更正后的标题"
            db.scalar(select(BookDeep)).status = "stale"
            db.commit()

        asyncio.run(deep.run_deep_analysis(record, book_id))
        with factory() as db:
            result = db.scalar(select(BookDeep))
            second_hash = json.loads(result.chapter_hashes_json)[0]["hash"]
            summaries = json.loads(result.summaries_json)
            assert result.status == "done"
        assert len(calls) == 2
        assert calls[0]["text"] == calls[1]["text"]
        assert "更正后的标题" in calls[1]["headings"]
        assert second_hash != first_hash
        assert summaries[0]["summary"] == "本次生成的摘要 2"
    finally:
        engine.dispose()
