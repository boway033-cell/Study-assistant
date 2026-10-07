"""Malformed structured generation must not become a saved research report."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api import study
from backend.app.core import database
from backend.app.models import Base, Book, StudyReport
from backend.app.services.rag import retriever
from backend.app.worker import tasks


@pytest.mark.parametrize("answer", [
    '{"report_markdown":"未闭合"',
    '{"claims":[]}',
])
def test_short_research_rejects_broken_structured_output(monkeypatch, answer):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        book = Book(title="研究材料", file_path="sample.pdf", file_type="pdf")
        db.add(book)
        db.commit()
        book_id = book.id

    class Provider:
        async def stream_chat(self, messages):
            yield answer

    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "update_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(study, "_book_context", lambda *_args: "选中材料的摘要")
    monkeypatch.setattr(study, "load_llm_config", lambda *_args: {"configured": True})
    monkeypatch.setattr(study.LLMRouter, "get", lambda *_args: Provider())
    monkeypatch.setattr(retriever, "retrieve", lambda *_args, **_kwargs: [])

    try:
        with pytest.raises(RuntimeError, match="未保存格式损坏的结果"):
            asyncio.run(study.run_overview(
                SimpleNamespace(result=None, progress=0), [book_id],
                focus="如何解释材料？", reasoning_depth="standard",
            ))
        with factory() as db:
            assert db.query(StudyReport).count() == 0
    finally:
        engine.dispose()
