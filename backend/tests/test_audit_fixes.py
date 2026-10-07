"""Regression coverage for data, scope, and task boundaries found in the audit."""
from __future__ import annotations

import asyncio
import importlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from backend.app.api.books import search
from backend.app.models import Base, Book, BookDeep, Chapter, ChatLog, Chunk, ImportTask, TocRevision
from backend.app.schemas import ChatReq
from backend.app.services.rag import fts
from backend.app.services.rag.toc_editor import replace_book_toc
from backend.app.worker import tasks


def _toc_sample():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(fts._CREATE_SQL))
    db = Session(engine)
    book = Book(title="目录修订样本", file_path="sample.pdf", file_type="pdf",
                status="ready", total_pages=2)
    db.add(book)
    db.flush()
    chapter = Chapter(book_id=book.id, title="原标题", level=1,
                      order_index=0, start_page=1, end_page=2)
    db.add(chapter)
    db.flush()
    chunk = Chunk(book_id=book.id, chapter_id=chapter.id, content="目录事务验证正文",
                  chunk_index=0, page_start=1, page_end=1)
    db.add_all([chunk, BookDeep(book_id=book.id, status="done",
                                markdown="已付费生成的总结", paper_card="已生成的阅读卡")])
    db.flush()
    fts.replace_book_index_in_session(db, book.id, [chunk])
    db.commit()
    item = {"client_key": "chapter", "id": chapter.id, "parent_key": None,
            "title": "新标题", "level": 1, "start_page": 1}
    return engine, db, book, chapter.id, chunk.id, item


def test_toc_edit_preserves_generated_work_and_updates_index():
    engine, db, book, chapter_id, chunk_id, item = _toc_sample()
    try:
        replace_book_toc(db, book, [item], "user")
        deep = db.scalar(select(BookDeep).where(BookDeep.book_id == book.id))
        assert deep.status == "stale"
        assert deep.markdown == "已付费生成的总结"
        assert deep.paper_card == "已生成的阅读卡"
        assert db.get(Chapter, chapter_id).title == "新标题"
        with engine.connect() as conn:
            indexed = conn.execute(text("SELECT chapter_id FROM fts_books WHERE chunk_id=:id"),
                                   {"id": chunk_id}).scalar_one()
        assert int(indexed) == chapter_id
    finally:
        db.close()
        engine.dispose()


def test_toc_index_failure_rolls_back_everything(monkeypatch):
    engine, db, book, chapter_id, chunk_id, item = _toc_sample()
    try:
        def fail_after_delete(session, book_id, chunks):
            session.execute(text("DELETE FROM fts_books WHERE book_id=:id"), {"id": book_id})
            raise RuntimeError("simulated index failure")

        monkeypatch.setattr(fts, "replace_book_index_in_session", fail_after_delete)
        with pytest.raises(RuntimeError, match="simulated index failure"):
            replace_book_toc(db, book, [item], "user")
        assert db.get(Chapter, chapter_id).title == "原标题"
        assert db.scalar(select(BookDeep).where(BookDeep.book_id == book.id)).status == "done"
        assert db.scalar(select(TocRevision).where(TocRevision.book_id == book.id)) is None
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM fts_books WHERE chunk_id=:id"),
                                {"id": chunk_id}).scalar_one() == 1
    finally:
        db.close()
        engine.dispose()


def test_unique_task_reservation_rejects_simultaneous_submission(monkeypatch):
    class Queue:
        async def put(self, record):
            return None

    def close_coro(coro, loop):
        coro.close()

    monkeypatch.setattr(tasks, "_ensure_backend", lambda: None)
    monkeypatch.setattr(tasks, "_persist", lambda record: None)
    monkeypatch.setattr(tasks, "_queue_for", lambda name: Queue())
    monkeypatch.setattr(tasks, "_loop_for", lambda name: None)
    monkeypatch.setattr(tasks.asyncio, "run_coroutine_threadsafe", close_coro)
    barrier = Barrier(2)

    def reserve():
        barrier.wait()
        try:
            return tasks.submit_unique("audit-exclusive", lambda record: asyncio.sleep(0), 987654)
        except tasks.DuplicateTaskError:
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: reserve(), range(2)))
        assert sum(record is not None for record in results) == 1
    finally:
        with tasks._TASK_REGISTRY_LOCK:
            for task_id, record in list(tasks._task_registry.items()):
                if record.name == "audit-exclusive":
                    del tasks._task_registry[task_id]


def test_completed_task_has_full_progress(monkeypatch):
    monkeypatch.setattr(tasks, "_persist", lambda record: None)

    async def run():
        queue = asyncio.Queue()
        record = tasks.TaskRecord(id="audit-progress", name="import", progress=.94,
                                  _coro=lambda rec: asyncio.sleep(0, result={"ok": True}))
        worker = asyncio.create_task(tasks._worker(queue))
        await queue.put(record)
        await queue.join()
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        return record

    record = asyncio.run(run())
    assert record.status == "done"
    assert record.progress == 1.0
    assert record.stage == "done"
    assert record.message == "已完成"


def test_startup_normalizes_completed_task_progress(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = Book(title="已解析资料", file_path="sample.pdf", file_type="pdf", status="ready")
        db.add(book)
        db.flush()
        db.add_all([
            ImportTask(id="old-done", book_id=book.id, name="import", status="done",
                       progress=.94, stage="parsing"),
            ImportTask(id="old-stage", book_id=book.id, name="import", status="done",
                       progress=1.0, stage="saving"),
            ImportTask(id="skip-ready", book_id=book.id, name="import", status="pending",
                       progress=.25, stage="ocr"),
        ])
        db.commit()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: Session(engine))
    try:
        assert tasks.recover_pending_tasks() == []
        with Session(engine) as db:
            for task_id in ("old-done", "old-stage", "skip-ready"):
                row = db.get(ImportTask, task_id)
                assert row.status == "done"
                assert row.progress == 1.0
                assert row.stage == "done"
    finally:
        engine.dispose()


def test_invalid_book_ids_never_expand_search_scope():
    with pytest.raises(HTTPException) as error:
        search(q="治理", book_ids="12,abc", db=None)
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as conflicting:
        search(q="治理", book_id=1, book_ids="2", db=None)
    assert conflicting.value.status_code == 422


def test_chapter_scope_is_applied_before_retrieval(monkeypatch):
    from backend.app.services.rag import retriever

    engine, db, book, chapter_id, _, _ = _toc_sample()
    received = {}
    def candidates(question, **kwargs):
        received.update(kwargs)
        return [{"chunk_id": 1, "book_id": book.id, "book_title": book.title,
                 "chapter_id": chapter_id, "snippet": "正文"}]

    monkeypatch.setattr(retriever, "retrieve", candidates)
    try:
        response = search(q="正文", book_id=None, book_ids=None, category=None, tag_id=None,
                          chapter_id=chapter_id, page=1, page_size=20, db=db)
        assert response.total == 1
        assert received["book_ids"] == [book.id]
        assert received["chapter_id"] == chapter_id
        assert received["include_context"] is False
        received.clear()
        empty = search(q="正文", book_id=None, book_ids=str(book.id + 1), category=None,
                       tag_id=None, chapter_id=chapter_id, page=1, page_size=20, db=db)
        assert empty.total == 0
        assert received == {}
    finally:
        db.close()
        engine.dispose()


def test_full_text_index_respects_chapter_scope(monkeypatch):
    engine, db, book, chapter_id, chunk_id, _ = _toc_sample()
    monkeypatch.setattr(fts, "engine", engine)
    try:
        own = fts.search("目录事务验证正文", book_ids=[book.id], chapter_id=chapter_id, top_k=10)
        other = fts.search("目录事务验证正文", book_ids=[book.id], chapter_id=chapter_id + 1, top_k=10)
        assert [item["chunk_id"] for item in own["items"]] == [chunk_id]
        assert other["items"] == []
        assert fts.fallback_search("目录事务验证正文", book_ids=[book.id],
                                   chapter_id=chapter_id + 1, limit=10) == []
    finally:
        db.close()
        engine.dispose()


def test_search_can_reach_results_after_two_hundred(monkeypatch):
    from backend.app.services.rag import retriever

    def candidates(question, *, book_ids, chapter_id, top_k, include_context):
        return [{"chunk_id": number, "book_id": 1, "book_title": "样本",
                 "snippet": str(number)} for number in range(1, top_k + 1)]

    monkeypatch.setattr(retriever, "retrieve", candidates)
    response = search(q="治理", book_id=None, book_ids=None, category=None, tag_id=None,
                      chapter_id=None, page=11, page_size=20, db=object())
    assert len(response.items) == 20
    assert response.items[0].chunk_id == 201
    assert response.has_more is True
    assert response.total == 221  # lower bound, not an invented exact total
    last = search(q="治理", book_id=None, book_ids=None, category=None, tag_id=None,
                  chapter_id=None, page=50, page_size=20, db=object())
    assert last.has_more is False
    assert last.truncated is True
    assert last.window_limit == 1000


def test_chat_model_override_reaches_custom_provider(monkeypatch):
    chat_api = importlib.import_module("backend.app.api.chat")
    cfg = {"provider_id": "custom", "provider_name": "自定义连接", "protocol": "openai_chat",
           "api_key": "test", "base_url": "https://example.invalid/v1",
           "model": "configured-model", "deepseek_model": "configured-model",
           "fallbacks": [{"provider_id": "backup", "model": "other-model"}]}
    received = {}
    monkeypatch.setattr(chat_api, "load_llm_config", lambda db, task: cfg)
    monkeypatch.setattr(chat_api.retriever, "retrieve", lambda *args, **kwargs: [])
    def capture_provider(mode, config):
        received["config"] = config
        received["actual_model"] = chat_api.LLMRouter._single(config).model
        return SimpleNamespace(name="custom", model="requested-model")

    monkeypatch.setattr(chat_api.LLMRouter, "get", capture_provider)
    asyncio.run(chat_api.chat(ChatReq(question="这是什么？", model="requested-model"), db=object()))
    assert received["config"]["model"] == "requested-model"
    assert received["actual_model"] == "requested-model"
    assert received["config"]["fallbacks"] == []
    with pytest.raises(ValueError):
        ChatReq(question="测试", model=" ")


def test_chat_history_records_the_provider_that_actually_answered(monkeypatch):
    chat_api = importlib.import_module("backend.app.api.chat")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    class Provider:
        name = "primary"
        model = "first-model"
        selected_provider_id = "backup"
        selected_model = "second-model"

        async def stream_chat(self, messages):
            yield "已核对回答"

    monkeypatch.setattr(chat_api, "load_llm_config", lambda db, task: {})
    monkeypatch.setattr(chat_api.retriever, "retrieve", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat_api.LLMRouter, "get", lambda *args: Provider())
    monkeypatch.setattr(chat_api, "record_citation_eval", lambda verification: None)

    async def consume(db):
        response = await chat_api.chat(ChatReq(question="这是什么？"), db=db)
        return [part async for part in response.body_iterator]

    try:
        with Session(engine) as db:
            events = asyncio.run(consume(db))
            saved = db.scalar(select(ChatLog))
            assert saved.mode == "backup"
            assert saved.model_name == "second-model"
            assert any('"model": "second-model"' in event for event in events)
    finally:
        engine.dispose()
