"""P1-1 读路径回归：聊天/知识引用携带身份并在读取时校验。

覆盖点：
- 新聊天记录写入 sources_json 时带 content_hash / source_version_uid；
- 同书重解析后，带指纹的历史引用返回 stale（引用时归档原文），
  无指纹旧引用返回 needs_review（content 必须为 None），未重解析旧书返回
  legacy_unverified 且仍可读正文；
- summarize 复用历史引用时绝不按整数 id 回退读新正文；
- knowledge 的 source_refs 校验只给身份结论，不把当前正文冒充验证通过。

测试只用虚构书籍/分块，不读取 backend/data，不调用真实模型。
"""
from __future__ import annotations

import asyncio
import json

from backend.app.api import chat as chat_api
from backend.app.api import knowledge as knowledge_api
from backend.app.core.database import SessionLocal
from backend.app.models import Book, Chapter, ChatLog, Chunk
from backend.app.schemas import ChatReq, ChatSource
from backend.app.services import book_lifecycle, source_identity
from backend.app.services.qa import context as qa_context, metrics as qa_metrics


def _book(db, title: str, content: str, *, with_hash: bool = True):
    """建立一本 ready 状态的虚构书：Book + Chapter + 单个 Chunk。"""
    book = Book(title=title, file_path="virtual.pdf", file_type="pdf", status="ready")
    db.add(book)
    db.flush()
    chapter = Chapter(book_id=book.id, title="第一章", level=1, order_index=0,
                      start_page=1, end_page=1)
    db.add(chapter)
    db.flush()
    chunk = Chunk(book_id=book.id, chapter_id=chapter.id, content=content, chunk_index=0,
                  page_start=1, page_end=1,
                  content_hash=source_identity.content_hash(content) if with_hash else None)
    db.add(chunk)
    db.commit()
    return book, chapter, chunk


def _reparse(db, book_id: int, new_content: str) -> dict:
    """走真实生命周期把书重解析为新正文（旧分块归档到 RetiredChunk）。"""
    book_lifecycle.backfill_legacy_source_versions(db)
    version = book_lifecycle.start_staging_version(db, book_id)
    staged = book_lifecycle.stage_chapters(db, version, [{
        "title": "第一章", "level": 1, "parent_id": None, "order_index": 0,
        "start_page": 1, "end_page": 1,
    }])
    book_lifecycle.stage_chunks(db, version, [{
        "chapter_id": staged[0].id, "content": new_content, "page_start": 1,
        "page_end": 1, "chunk_index": 0, "word_count": 2,
    }])
    db.commit()
    return book_lifecycle.activate_staged_version(db, book_id)


def _ref(book, chapter, chunk, *, identity: dict | None = None, snippet: str = "") -> dict:
    """构造一条 sources_json 引用；identity 非空时附带引用时指纹。"""
    ref = {"chunk_id": chunk.id, "book_id": book.id, "page": chunk.page_start,
           "page_start": chunk.page_start, "page_end": chunk.page_end,
           "book_title": book.title, "chapter_title": chapter.title,
           "snippet": snippet or chunk.content[:40]}
    if identity:
        ref["content_hash"] = identity["content_hash"]
        ref["source_version_uid"] = identity["source_version_uid"]
    return ref


def _drain_chat(req: ChatReq, db) -> dict[str, dict]:
    """直接调用 chat 端点并解析 SSE 事件（不经过真实网络与模型）。"""
    async def _run():
        response = await chat_api.chat(req, db)
        parts = []
        async for part in response.body_iterator:
            parts.append(part if isinstance(part, str) else part.decode("utf-8"))
        return "".join(parts)

    events: dict[str, dict] = {}
    for block in asyncio.run(_run()).split("\n\n"):
        lines = [line for line in block.splitlines() if line]
        event = data = None
        for line in lines:
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        if event and data:
            events[event] = json.loads(data)
    return events


def test_new_chat_log_persists_content_identity(monkeypatch):
    """新聊天记录落库时 sources_json 必须带 content_hash / source_version_uid。"""
    db = SessionLocal()
    try:
        book, chapter, chunk = _book(db, "写入身份：问答", "二分查找要求数据有序。")
        book_lifecycle.backfill_legacy_source_versions(db)
        identity = source_identity.reference_identity(db, book.id, chunk.id)
        assert identity["content_hash"]
        assert identity["source_version_uid"]

        class _Provider:
            name = "fake"
            model = "fake-model"
            selected_provider_id = "fake-route"
            selected_model = "fake-model"
            last_style_audit = None

            async def stream_chat(self, messages):
                yield "二分查找要求数据有序[资料1]。"

        monkeypatch.setattr(chat_api, "load_llm_config", lambda *a, **k: {"model": "fake"})
        monkeypatch.setattr(chat_api.LLMRouter, "get", lambda *a, **k: _Provider())
        monkeypatch.setattr(chat_api.retriever, "retrieve", lambda *a, **k: [{
            "chunk_id": chunk.id, "book_id": book.id, "page": 1,
            "book_title": book.title, "chapter_title": chapter.title,
            "snippet": "二分查找要求数据有序。",
        }])

        events = _drain_chat(ChatReq(question="二分查找的前提是什么？", book_id=book.id), db)
        done = events["done"]
        assert done["sources"][0]["content_hash"] == identity["content_hash"]
        assert done["sources"][0]["source_version_uid"] == identity["source_version_uid"]

        log = db.get(ChatLog, done["chat_id"])
        stored = json.loads(log.sources_json)
        assert stored[0]["chunk_id"] == chunk.id
        assert stored[0]["content_hash"] == identity["content_hash"]
        assert stored[0]["source_version_uid"] == identity["source_version_uid"]
    finally:
        qa_metrics.reset()
        db.close()


def test_history_detail_verifies_each_source_without_silent_wrong_citation():
    """历史详情逐条校验：ok/stale/legacy 可展示正文，needs_review 必须留空。"""
    db = SessionLocal()
    try:
        # 1) ok：书未重解析，引用带指纹。
        ok_book, ok_chapter, ok_chunk = _book(db, "身份：OK", "当前正文：二分查找。")
        book_lifecycle.backfill_legacy_source_versions(db)
        ok_identity = source_identity.reference_identity(db, ok_book.id, ok_chunk.id)

        # 2) stale：先记下引用时指纹，再重解析为完全不同的正文。
        stale_book, stale_chapter, stale_chunk = _book(db, "身份：STALE", "重解析前原文：区间减半。")
        book_lifecycle.backfill_legacy_source_versions(db)
        stale_identity = source_identity.reference_identity(db, stale_book.id, stale_chunk.id)
        stale_old_chunk_id = stale_chunk.id
        stale_ref = _ref(stale_book, stale_chapter, stale_chunk,
                         identity=stale_identity, snippet="区间减半")
        _reparse(db, stale_book.id, "重解析后的新正文：完全不同的内容。")

        # 3) needs_review：旧引用无指纹，但书已重解析，禁止打开当前正文。
        review_book, review_chapter, review_chunk = _book(
            db, "身份：REVIEW", "重解析前的旧正文。", with_hash=False)
        review_old_chunk_id = review_chunk.id
        review_ref = _ref(review_book, review_chapter, review_chunk, snippet="旧正文")
        _reparse(db, review_book.id, "重解析后的新正文：不应被打开。")

        # 4) legacy：旧引用无指纹，且书从未重解析，仍可读当前正文（明确标注未验证）。
        legacy_book, legacy_chapter, legacy_chunk = _book(
            db, "身份：LEGACY", "未重解析旧书正文。", with_hash=False)

        log = ChatLog(
            book_id=ok_book.id, question="这些引用还指向原文吗？",
            answer="请核对来源身份[资料1]。",
            sources_json=json.dumps([
                _ref(ok_book, ok_chapter, ok_chunk, identity=ok_identity, snippet="二分查找"),
                stale_ref,
                _ref(legacy_book, legacy_chapter, legacy_chunk, snippet="未重解析"),
                review_ref,
            ], ensure_ascii=False),
            mode="deepseek", model_name="test-model",
        )
        db.add(log)
        db.commit()
        db.refresh(log)

        detail = chat_api.get_chat(log.id, db)
        by_chunk = {source.chunk_id: source for source in detail.sources}
        assert len(by_chunk) == 4

        ok_source = by_chunk[ok_chunk.id]
        assert ok_source.identity_status == "ok"
        assert ok_source.identity_verified is True
        assert ok_source.content == "当前正文：二分查找。"
        assert ok_source.content_hash == ok_identity["content_hash"]
        assert ok_source.snippet == "二分查找"
        assert ok_source.page_start == 1

        stale_source = by_chunk[stale_old_chunk_id]
        assert stale_source.identity_status == "stale"
        assert stale_source.identity_verified is True
        assert stale_source.content == "重解析前原文：区间减半。"
        assert "完全不同的内容" not in stale_source.content
        assert stale_source.content_hash == stale_identity["content_hash"]
        assert stale_source.source_version_uid == stale_identity["source_version_uid"]
        assert stale_source.message

        legacy_source = by_chunk[legacy_chunk.id]
        assert legacy_source.identity_status == "legacy_unverified"
        assert legacy_source.identity_verified is False
        assert legacy_source.content == "未重解析旧书正文。"
        assert legacy_source.snippet == "未重解析"

        review_source = by_chunk[review_old_chunk_id]
        assert review_source.identity_status == "needs_review"
        assert review_source.identity_verified is False
        assert review_source.content is None
        assert review_source.message
    finally:
        db.close()


def test_summary_uses_archived_original_and_skips_unverifiable_citation():
    """summarize 复用历史引用：stale 用归档原文；无指纹已重解析引用直接跳过。"""
    db = SessionLocal()
    try:
        book, chapter, chunk = _book(db, "总结：已重解析", "引用时原文：每轮将区间减半。")
        book_lifecycle.backfill_legacy_source_versions(db)
        identity = source_identity.reference_identity(db, book.id, chunk.id)
        old_chunk_id = chunk.id
        turn_ref = _ref(book, chapter, chunk, identity=identity, snippet="每轮将区间减半")
        _reparse(db, book.id, "重解析后的新正文：改为线性扫描。")

        # 带指纹的旧引用：应取归档原文，绝不采用当前新正文。
        turn = qa_context.Turn(question="总结刚才讨论的内容", sources=[turn_ref])
        historical, skipped = chat_api._historical_summary_sources(db, [turn], [book.id])
        assert skipped == 0
        assert [item["chunk_id"] for item in historical] == [old_chunk_id]
        assert "每轮将区间减半" in historical[0]["context"]
        assert "线性扫描" not in historical[0]["context"]
        assert historical[0]["content_hash"] == identity["content_hash"]
        assert historical[0]["source_version_uid"] == identity["source_version_uid"]

        # 无指纹旧引用：书已重解析，无法确认编号含义 → 跳过并计数，不回退读新正文。
        bare_turn = qa_context.Turn(question="总结", sources=[
            {"chunk_id": old_chunk_id, "book_id": book.id},
        ])
        historical, skipped = chat_api._historical_summary_sources(db, [bare_turn], [book.id])
        assert historical == []
        assert skipped == 1
    finally:
        db.close()


def test_summary_keeps_unreparsed_legacy_book_readable():
    """未重解析旧书：无指纹历史引用仍可读当前正文（legacy_unverified）。"""
    db = SessionLocal()
    try:
        book, chapter, chunk = _book(db, "总结：旧书", "旧书正文：二分查找。", with_hash=False)
        turn = qa_context.Turn(question="总结", sources=[
            _ref(book, chapter, chunk, snippet="旧书正文"),
        ])
        historical, skipped = chat_api._historical_summary_sources(db, [turn], [book.id])
        assert skipped == 0
        assert len(historical) == 1
        assert historical[0]["context"] == "旧书正文：二分查找。"
        assert historical[0]["snippet"] == "旧书正文：二分查找。"
    finally:
        db.close()


def test_knowledge_source_refs_report_identity_without_content():
    """knowledge 校验来源时只给身份结论，不返回正文，避免冒充已验证原文。"""
    db = SessionLocal()
    try:
        review_book, review_chapter, review_chunk = _book(
            db, "知识引用：已重解析", "重解析前的讨论。", with_hash=False)
        old_chapter_id, old_chunk_id = review_chapter.id, review_chunk.id
        _reparse(db, review_book.id, "重解析后的新结论。")

        legacy_book, legacy_chapter, legacy_chunk = _book(
            db, "知识引用：旧书", "旧书正文。", with_hash=False)

        checks = knowledge_api._source_ref_checks(db, [
            {"ref": f"B{review_book.id}:CH{old_chapter_id}:C{old_chunk_id}"},
            {"ref": f"B{legacy_book.id}:C{legacy_chunk.id}"},
            {"ref": "B999999:CH1:C1"},
        ])
        assert len(checks) == 3
        review, legacy, missing_book = checks
        assert review["identity_status"] == "needs_review"
        assert review["identity_verified"] is False
        assert "content" not in review
        assert legacy["identity_status"] == "legacy_unverified"
        assert legacy["identity_verified"] is False
        assert "content" not in legacy
        assert missing_book["identity_status"] == "invalid"
        assert missing_book["message"]
    finally:
        db.close()


def test_chat_source_schema_accepts_legacy_payload():
    """旧 sources_json 没有身份字段时，响应模型必须保持兼容。"""
    source = ChatSource(chunk_id=1, book_id=2, snippet="旧引用")
    assert source.identity_status is None
    assert source.identity_verified is None
    assert source.content is None
    assert source.message is None
