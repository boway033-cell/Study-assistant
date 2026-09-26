"""P1: 本地演示、调用上限与任务通道隔离。"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest


def test_ai_queue_runs_while_parser_blocks_its_own_loop(monkeypatch):
    from backend.app.worker import tasks

    monkeypatch.setattr(tasks, "_persist", lambda _: None)
    parsing = threading.Event()

    async def slow_parse(_):
        parsing.set()
        time.sleep(0.5)  # 模拟同步 OCR 卡住解析事件循环

    async def quick_ai(_):
        return {"answer": "ready"}

    heavy = tasks.submit("import", slow_parse)
    assert parsing.wait(2)
    started = time.monotonic()
    ai = tasks.submit("study-overview", quick_ai)
    while ai.status not in {"done", "failed"} and time.monotonic() - started < 0.3:
        time.sleep(0.01)
    assert ai.status == "done"
    assert heavy.status == "running"
    while heavy.status not in {"done", "failed"} and time.monotonic() - started < 2:
        time.sleep(0.01)
    assert heavy.status == "done"


def test_budget_stops_before_extra_call_and_output():
    from backend.app.services.llm.budget import BudgetExceeded, TaskBudget

    budget = TaskBudget(max_tokens=5, max_calls=1)
    budget.start_call(4)
    budget.output(6)
    assert budget.used_tokens == 5
    with pytest.raises(BudgetExceeded, match="Token"):
        budget.output(1)
    with pytest.raises(BudgetExceeded, match="调用"):
        budget.start_call(1)


def test_router_enforces_budget_on_streamed_output():
    from backend.app.services.llm import RoutedProvider
    from backend.app.services.llm.budget import BudgetExceeded, TaskBudget, current_budget

    class FakeProvider:
        name = "fake"
        model = "example"

        async def stream_chat(self, _messages):
            yield "123456"

    async def consume():
        token = current_budget.set(TaskBudget(max_tokens=3, max_calls=1))
        try:
            return [delta async for delta in RoutedProvider([FakeProvider()], "research").stream_chat(
                [{"role": "user", "content": "1234"}])]
        finally:
            current_budget.reset(token)

    with pytest.raises(BudgetExceeded, match="Token"):
        asyncio.run(consume())


def test_demo_book_can_be_imported_without_model_key():
    from backend.app.api.books import create_demo_book
    from backend.app.core.database import SessionLocal
    from backend.app.models import Book
    from backend.app.services.parser import parse_document
    from backend.app.core.config import settings

    with SessionLocal() as db:
        result = asyncio.run(create_demo_book(db))
        assert result["demo"] is True
        book = db.get(Book, result["id"])
        parsed = parse_document(settings.uploads_dir / book.file_path)
        assert "研究问题" in "\n".join(parsed.pages)
        assert "完成率" in "\n".join(parsed.pages)


def test_study_preflight_uses_selected_text_and_manual_price():
    from backend.app.api.study import StudyOverviewReq, _overview_budget
    from backend.app.core.database import SessionLocal
    from backend.app.models import Book, Chunk, Setting
    from backend.app.services.llm import load_llm_config
    import json

    with SessionLocal() as db:
        book = Book(title="预算测试材料", file_path="budget.docx", file_type="docx", status="ready")
        db.add(book)
        db.flush()
        db.add(Chunk(book_id=book.id, content="材料" * 100, chunk_index=0))
        provider = load_llm_config(db, "research")
        provider_id = provider["provider_id"]
        rates = db.get(Setting, "ai_price_rates")
        if rates is None:
            rates = Setting(key="ai_price_rates", value="{}")
            db.add(rates)
        rates.value = json.dumps({f"{provider_id}:{provider['model']}": {"input": 2, "output": 4}})
        db.flush()
        result = _overview_budget(StudyOverviewReq(book_ids=[book.id], focus="这份材料说了什么", reasoning_depth="standard"), db)
        assert result["document_chars"] == 200
        assert result["provider_id"] == provider_id
        assert result["estimated_calls"] >= 1
        assert result["estimated_tokens"] > 0
        assert result["estimated_cost_cny"] is not None
        db.rollback()
