"""Meaningful boundaries for source-bound understanding and discovery."""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.models import (Book, Chunk, SensemakingArtifact, SensemakingInterpretation,
                                SensemakingReadingCheckpoint, SensemakingRevision)
from backend.app.services import sensemaking as sm


def _source(book_id: int, chunk_id: int, text: str) -> dict:
    return {"ref": f"B{book_id}:P1:C{chunk_id}", "book_id": book_id, "chunk_id": chunk_id,
            "page": 1, "text": text, "hash": sm.source_hash(text)}


def _evidence(source: dict, quote: str) -> list[dict]:
    return [{"ref": source["ref"], "quote": quote}]


def test_quote_alignment_accepts_only_typographic_differences_and_keeps_source_text():
    source = _source(1, 1, "网络舆论对政策议程的影响，是一个重要议题。")
    aligned = sm.verified_evidence(_evidence(source, "网络舆论对政策议程的影响是一个重要议题"), [source])
    assert aligned[0]["quote"] == "网络舆论对政策议程的影响，是一个重要议题"
    assert sm.verified_evidence(_evidence(source, "网络舆论主导了政策议程"), [source]) == []


def test_evidence_recovers_unique_source_ref_and_fullwidth_quote():
    first = _source(1, 1, "研究首先介绍了问题背景与方法。")
    second = _source(1, 2, "本研究使用ＡＩ工具分析三组访谈材料。")
    found = sm.verified_evidence(_evidence(first, "本研究使用AI工具分析三组访谈材料"), [first, second])
    assert len(found) == 1
    assert found[0]["ref"] == second["ref"]
    assert found[0]["quote"] == "本研究使用ＡＩ工具分析三组访谈材料"
    assert found[0]["match_status"] == "recovered_ref"
    assert found[0]["source_hash"] == second["hash"]


def test_evidence_ref_recovery_rejects_ambiguous_or_out_of_scope_sources():
    first = _source(1, 1, "不同场景都使用相同的研究方法来观察合作过程。")
    second = _source(1, 2, first["text"])
    wrong = {"ref": "B1:P9:C999", "quote": "相同的研究方法来观察合作过程"}
    assert sm.verified_evidence([wrong], [first, second]) == []
    third = _source(2, 3, "另一篇资料提出完全不同的研究结论与解释边界。")
    assert sm.verified_evidence(_evidence(third, "另一篇资料提出完全不同的研究结论"),
                                [first, third], book_id=1) == []


def test_evidence_accepts_one_minor_copy_error_but_not_changed_negation_or_number():
    source = _source(1, 1, "研究发现，社区参与能够改善信息公开，并提高政策讨论的透明度。")
    typo = sm.verified_evidence(_evidence(source, "研究发现，社区参与能够改善信息公井，并提高政策讨论的透明度"), [source])
    assert typo[0]["quote"] == "研究发现，社区参与能够改善信息公开，并提高政策讨论的透明度"
    assert typo[0]["match_status"] == "minor_quote_error"
    negative = _source(1, 2, "研究发现社区参与不能改善信息公开，并提高政策讨论的透明度。")
    assert sm.verified_evidence(_evidence(negative, "研究发现社区参与能改善信息公开，并提高政策讨论的透明度"),
                                [negative]) == []
    number = _source(1, 3, "研究观察到三组样本的政策讨论透明度提高了百分之十。")
    assert sm.verified_evidence(_evidence(number, "研究观察到四组样本的政策讨论透明度提高了百分之十"),
                                [number]) == []


def test_minor_quote_repair_requires_unique_span():
    text = "社区参与能够改善信息公开，并提高政策讨论的透明度。"
    source = _source(1, 1, text + "其他情况。" + text)
    assert sm.verified_evidence(_evidence(source, "社区参与能够改善信息公井，并提高政策讨论的透明度"),
                                [source]) == []


def test_invalid_local_reading_has_bounded_repair_and_explicit_gap(monkeypatch):
    from backend.app.api import sensemaking as api, study
    from backend.app.services.llm import request_reasoning_effort

    source = _source(1, 1, "网络舆论对政策议程的影响，是一个重要议题。")
    window = {"id": "w1", "chapter_id": 1, "chapter_title": "问题提出", "page_start": 1,
              "page_end": 1, "sources": [source]}
    calls = []

    async def invalid_answer(_provider, messages, *_args, **_kwargs):
        calls.append((messages, request_reasoning_effort.get()))
        return json.dumps({"role": "body", "claims": [{"statement": "无依据断言",
            "evidence": [{"ref": source["ref"], "quote": "原文完全没有的文字"}]}]}, ensure_ascii=False)

    monkeypatch.setattr(study, "_stream_answer", invalid_answer)
    monkeypatch.setattr(api, "update_progress", lambda *_args, **_kwargs: None)
    messages = [{"role": "system", "content": "逐段研读"},
                {"role": "user", "content": sm.source_block([source])}]
    result = asyncio.run(api._structured(object(), messages,
        lambda raw: sm.normalize_reading_pass(raw, window), object(), "fulltext-reading",
        on_validation_failure=lambda error: sm.unresolved_reading_pass(window, error)))
    assert len(calls) == 2
    assert [effort for _, effort in calls] == ["low", "medium"]
    assert "无依据断言" not in calls[1][0][-1]["content"]
    assert result["unresolved"] is True and result["claims"] == []
    assert result["source_refs"] == [source["ref"]]


def test_reading_keeps_only_nodes_with_literal_quotes_from_selected_sources():
    source = _source(1, 11, "研究问题是如何解释城市迁移。样本来自三个城市的访谈记录。作者把差异解释为制度边界。")
    raw = {"material_type": "qualitative", "nodes": [
        {"kind": "question", "statement": "问题", "epistemic_status": "source_observation",
         "evidence": _evidence(source, "研究问题是如何解释城市迁移")},
        {"kind": "method", "statement": "访谈", "evidence": _evidence(source, "样本来自三个城市的访谈记录")},
        {"kind": "conclusion", "statement": "解释", "evidence": _evidence(source, "作者把差异解释为制度边界")},
        {"kind": "finding", "statement": "捏造", "evidence": _evidence(source, "研究证明迁移必然导致收入翻倍")},
    ], "edges": [{"from": "n1", "to": "n3", "relation": "supports", "reason": "问题导向解释",
                  "evidence": _evidence(source, "作者把差异解释为制度边界")},
                 {"from": "n1", "to": "n4", "relation": "supports", "reason": "无效节点",
                  "evidence": _evidence(source, "作者把差异解释为制度边界")}]}
    result = sm.normalize_reading(raw, [source], 10)
    assert len(result["nodes"]) == 3
    assert result["nodes"][0]["epistemic_status"] == "source_observation"
    assert result["nodes"][1]["epistemic_status"] == "ai_inference"
    assert len(result["edges"]) == 1
    assert result["edges"][0]["reason"] == "问题导向解释"
    assert result["edges"][0]["evidence"][0]["ref"] == source["ref"]
    assert result["coverage"] == {"selected_chunks": 1, "total_chunks": 10, "complete": False}
    assert result["semantic_status"] == "ai_unchecked"


def test_discovery_requires_comparable_concepts_and_both_original_sources():
    first = _source(1, 11, "本文将自主性定义为劳动者能够自行选择每周排班时段。")
    second = _source(2, 22, "本文将自主性定义为劳动者在任务执行中选择操作方式的裁量权。")
    definitions = [
        {"book_id": 1, "meaning": "排班选择", "evidence": _evidence(first, "劳动者能够自行选择每周排班时段")},
        {"book_id": 2, "meaning": "执行裁量", "evidence": _evidence(second, "劳动者在任务执行中选择操作方式")},
    ]
    card = {"title": "不同自主性", "observation": "两篇定义不同", "why_tension": "同一术语指向不同维度",
            "tension": "两篇定义不同", "already_answered": "已看见两种定义", "still_missing": "同样本测量",
            "next_step": "阅读测量部分", "rival_explanations": [
                {"statement": "阶段差异", "assumption": "阶段影响行为", "prediction": "各阶段效果不同"},
                {"statement": "测量差异", "assumption": "指标捕捉不同维度", "prediction": "同群体两指标方向不同"}],
            "discriminating_question": "同一批劳动者在两个阶段如何变化？", "evidence": [
                *_evidence(first, "劳动者能够自行选择每周排班时段"),
                *_evidence(second, "劳动者在任务执行中选择操作方式"),
            ]}
    incomparable = sm.normalize_discovery(
        {"alignment": {"status": "incomparable", "definitions": definitions}, "discoveries": [card]},
        [first, second], [1, 2], "自主性")
    assert incomparable["discoveries"] == []
    assert incomparable["alignment"]["status"] == "incomparable"

    only_one = {**card, "evidence": _evidence(first, "劳动者能够自行选择每周排班时段")}
    partial = sm.normalize_discovery(
        {"alignment": {"status": "partial", "definitions": definitions}, "discoveries": [only_one, card]},
        [first, second], [1, 2], "自主性")
    assert len(partial["discoveries"]) == 1
    assert {item["book_id"] for item in partial["discoveries"][0]["evidence"]} == {1, 2}
    assert partial["discoveries"][0]["epistemic_status"] == "research_idea"


def test_source_change_invalidates_saved_reading():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = Book(title="测试", file_path="test.pdf", file_type="pdf", status="ready")
        db.add(book); db.flush()
        chunk = Chunk(book_id=book.id, content="原始正文包含一段能够定位的论述。", chunk_index=0, page_start=1)
        db.add(chunk); db.commit()
        versions = sm.source_versions([sm._candidate(chunk)])
        assert sm.stale_source_refs(db, versions) == []
        chunk.content = "重新 OCR 后正文发生变化。"
        db.commit()
        assert sm.stale_source_refs(db, versions) == [chunk.id]
    engine.dispose()


def test_fulltext_windows_cover_every_chunk_and_invalidate_on_distant_change():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = Book(title="长篇研究", file_path="long.pdf", file_type="pdf", status="ready", file_hash="original",
                    total_pages=35)
        db.add(book); db.flush()
        chunks = [Chunk(book_id=book.id, content=f"第 {index} 段解释了不同的研究条件。" * 12,
                        chunk_index=index, page_start=index + 1) for index in range(31)]
        db.add_all(chunks); db.commit()
        snapshot = sm.reading_windows(db, book.id, max_chars=600)
        seen = [source["chunk_id"] for window in snapshot["windows"] for source in window["sources"]]
        assert set(seen) == {chunk.id for chunk in chunks}
        assert snapshot["coverage"]["nonempty_chunks"] == 31
        assert snapshot["coverage"]["window_count"] > 1
        assert snapshot["coverage"]["pages_without_extracted_text"] == 4
        assert sm.stale_source_refs(db, snapshot["version"]) == []
        chunks[-1].content = "末段 OCR 文字已经改变。"
        db.commit()
        assert sm.stale_source_refs(db, snapshot["version"]) == [book.id]
    engine.dispose()


def test_local_reading_pass_rejects_unlocated_claims():
    source = _source(1, 1, "作者的核心论点依赖于两组案例之间的制度差异。")
    window = {"id": "w1", "chapter_id": None, "chapter_title": "正文", "page_start": 1,
              "page_end": 1, "sources": [source]}
    with pytest.raises(ValueError, match="没有可定位的论点"):
        sm.normalize_reading_pass({"role": "body", "claims": [{"kind": "finding", "statement": "伪造结论",
            "evidence": _evidence(source, "所有案例都证明收入增加一倍")}]}, window)


def test_numbered_passages_restore_exact_quotes_and_reject_unknown_ids():
    source = _source(1, 1, "网络舆论对政策议程的影响，是一个重要议题。" * 12)
    window = {"id": "w1", "chapter_id": None, "chapter_title": "正文", "page_start": 1,
              "page_end": 1, "sources": [source]}
    passages = sm.reading_evidence_catalog(window)
    assert len(passages) >= 2
    result = sm.normalize_reading_pass({"role": "body", "claims": [
        {"kind": "finding", "statement": "舆论影响政策议程", "evidence_ids": ["E1"]},
        {"kind": "finding", "statement": "伪造论点", "evidence_ids": ["E999"]},
    ]}, window, passages)
    assert len(result["claims"]) == 1
    assert result["claims"][0]["evidence"][0]["quote"] == passages[0]["quote"]
    assert result["claims"][0]["evidence"][0]["ref"] == source["ref"]
    with pytest.raises(ValueError, match="有效证据编号"):
        sm.normalize_reading_pass({"role": "body", "claims": [
            {"statement": "无依据断言", "evidence_ids": ["E999"]}]}, window, passages)


def test_oversized_chunk_is_split_without_losing_extracted_text():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = Book(title="长页", file_path="long-page.pdf", file_type="pdf", status="ready")
        db.add(book); db.flush()
        content = "这一段包含很多原文，不能因模型窗口上限而截断。" * 75
        db.add(Chunk(book_id=book.id, content=content, chunk_index=0, page_start=2)); db.commit()
        snapshot = sm.reading_windows(db, book.id, max_chars=500)
        parts = [source for window in snapshot["windows"] for source in window["sources"]]
        assert len(parts) > 1
        assert "".join(part["text"] for part in parts) == content
        assert len({part["ref"] for part in parts}) == len(parts)
        assert snapshot["coverage"]["text_chars"] == len(content)
    engine.dispose()


def test_followup_reopens_relevant_original_text_beyond_initial_summary():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        first = Book(title="目标文献", file_path="first.pdf", file_type="pdf", status="ready")
        other = Book(title="其他文献", file_path="other.pdf", file_type="pdf", status="ready")
        db.add_all([first, other]); db.flush()
        db.add_all([Chunk(book_id=first.id, content=f"第 {index} 段介绍普通背景资料。",
                          chunk_index=index, page_start=index + 1) for index in range(30)])
        target = Chunk(book_id=first.id, content="边缘群体的时间自主性来自排班控制，而不是执行裁量。",
                       chunk_index=30, page_start=31)
        db.add(target)
        db.add(Chunk(book_id=other.id, content="边缘群体的时间自主性来自完全不同的理论。",
                     chunk_index=0, page_start=1))
        db.commit()
        found = sm.select_question_passages(db, first.id, "边缘群体的时间自主性如何定义？")
        assert found[0]["chunk_id"] == target.id
        assert all(item["book_id"] == first.id for item in found)
        assert "排班控制" in found[0]["text"]
    engine.dispose()


def test_hierarchical_reduction_preserves_source_bound_claims(monkeypatch):
    from backend.app.api import sensemaking as api, study

    claims = []
    for index in range(130):
        source = _source(1, index + 1, f"第{index}段详细说明案例关系与解释边界。")
        claims.append({"kind": "finding", "statement": f"案例关系与解释边界第{index}项，说明另一种可能性",
                       "reasoning": "区分观察和解释", "evidence": sm.verified_evidence(
                           _evidence(source, f"第{index}段详细说明案例关系与解释边界"), [source])})

    async def answer(_provider, messages, *_args, **_kwargs):
        batch = json.loads(messages[-1]["content"].split("：\n", 1)[1])
        return json.dumps({"role": "body", "summary": "合并保留来源", "claims": batch[:2] + batch[-1:]}, ensure_ascii=False)

    monkeypatch.setattr(study, "_stream_answer", answer)
    monkeypatch.setattr(api, "update_progress", lambda *_args, **_kwargs: None)
    reduced, levels = asyncio.run(api._reduce_claims(object(), claims, object()))
    assert levels
    assert len(reduced) < len(claims)
    assert all(claim["evidence"] and claim["evidence"][0]["ref"].startswith("B1:") for claim in reduced)


def test_fulltext_reading_resumes_completed_windows_after_failure(monkeypatch):
    from backend.app.api import sensemaking as api, study

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = Book(title="可续读研究", file_path="resume.pdf", file_type="pdf", status="ready")
        db.add(book); db.flush()
        db.add_all([Chunk(book_id=book.id, content=f"第{i}段讨论制度安排和合作机制的边界条件。" * 8,
                          chunk_index=i, page_start=i + 1) for i in range(4)])
        db.commit()
        book_id = book.id

    original_windows = sm.reading_windows
    monkeypatch.setattr(api.sm, "reading_windows", lambda db, bid: original_windows(db, bid, max_chars=500))
    with Session(engine) as db:
        snapshot = api.sm.reading_windows(db, book_id)
    assert len(snapshot["windows"]) >= 2
    sources = [source for window in snapshot["windows"] for source in window["sources"]]
    calls = {"classification": 0, "first": 0, "second": 0, "fail_second": True}

    class Provider:
        model = "test-model"

    async def answer(_provider, messages, *_args, **_kwargs):
        prompt = messages[-1]["content"]
        if "材料预览" in prompt:
            calls["classification"] += 1
            return json.dumps({"material_type": "qualitative", "reason": "案例"})
        for index, window in enumerate(snapshot["windows"]):
            if f"第 {index + 1}/{len(snapshot['windows'])} 段" in prompt:
                calls["first" if index == 0 else "second"] += 1
                if index == 1 and calls["fail_second"]:
                    raise RuntimeError("模拟后段中断")
                return json.dumps({"role": "body", "summary": "局部论点", "claims": [
                    {"kind": "finding", "statement": f"第{index}段", "evidence":
                     _evidence(source, source["text"][:20])} for source in window["sources"]]}, ensure_ascii=False)
        return json.dumps({"nodes": [
            {"kind": kind, "statement": f"论点{i}", "evidence": _evidence(source, source["text"][:20])}
            for i, (kind, source) in enumerate(zip(("question", "method", "conclusion"), sources))],
            "edges": [{"from": "n2", "to": "n3", "relation": "supports", "reason": "案例支持结论",
                       "evidence": _evidence(sources[2], sources[2]["text"][:20])}]}, ensure_ascii=False)

    monkeypatch.setattr(api, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(api, "load_llm_config", lambda *_args: {"configured": True})
    monkeypatch.setattr(api.LLMRouter, "get", lambda *_args: Provider())
    monkeypatch.setattr(api, "update_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(study, "_stream_answer", answer)
    with pytest.raises(RuntimeError, match="模拟后段中断"):
        asyncio.run(api._run_reading(object(), book_id, ""))
    with Session(engine) as db:
        checkpoint = db.scalar(select(SensemakingReadingCheckpoint))
        assert len(json.loads(checkpoint.state_json)["passes"]) == 1
    calls["fail_second"] = False
    result = asyncio.run(api._run_reading(object(), book_id, ""))
    assert result["artifact_id"] > 0
    assert calls["classification"] == 1
    assert calls["first"] == 1
    with Session(engine) as db:
        assert db.scalar(select(SensemakingReadingCheckpoint)) is None
    engine.dispose()


def test_reading_task_persists_source_bound_argument_map(monkeypatch):
    from backend.app.api import sensemaking as api, study

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = Book(title="测试研究", file_path="test.pdf", file_type="pdf", status="ready")
        db.add(book); db.flush()
        chunks = [
            Chunk(book_id=book.id, content="研究问题是何种机制影响组织合作。", chunk_index=0, page_start=1),
            Chunk(book_id=book.id, content="研究使用三个案例来比较组织之间的协作过程。", chunk_index=1, page_start=2),
            Chunk(book_id=book.id, content="结论强调制度安排可能改变合作边界。", chunk_index=2, page_start=3),
        ]
        db.add_all(chunks); db.commit()
        book_id = book.id
        sources, _ = sm.select_reading_sources(db, book_id)

    class Provider:
        model = "test-model"

    async def answer(_provider, messages, *_args, **_kwargs):
        if "材料预览" in messages[-1]["content"]:
            return json.dumps({"material_type": "qualitative", "reason": "使用案例"}, ensure_ascii=False)
        if "第 1/1 段" in messages[-1]["content"]:
            assert "[E1 |" in messages[-1]["content"]
            return json.dumps({"role": "body", "summary": "案例比较与结论", "claims": [
                {"kind": "question", "statement": "问题", "evidence_ids": ["E1"]},
                {"kind": "method", "statement": "案例比较", "evidence_ids": ["E2"]},
                {"kind": "conclusion", "statement": "边界", "evidence_ids": ["E3"]},
            ]}, ensure_ascii=False)
        return json.dumps({"material_type": "qualitative", "nodes": [
            {"kind": "question", "statement": "问题", "evidence_ids": ["E1"]},
            {"kind": "method", "statement": "案例比较", "evidence_ids": ["E2"]},
            {"kind": "conclusion", "statement": "边界", "evidence_ids": ["E3"]},
        ], "edges": [{"from": "n2", "to": "n3", "relation": "supports", "reason": "案例支持有限结论",
                      "evidence_ids": ["E3"]}],
            "teach_back_question": "机制如何连接案例和结论？"}, ensure_ascii=False)

    monkeypatch.setattr(api, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(api, "load_llm_config", lambda *_args: {"configured": True})
    monkeypatch.setattr(api.LLMRouter, "get", lambda *_args: Provider())
    monkeypatch.setattr(api, "update_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(study, "_stream_answer", answer)
    result = asyncio.run(api._run_reading(object(), book_id, ""))
    with Session(engine) as db:
        saved = db.get(SensemakingArtifact, result["artifact_id"])
        assert saved.kind == "reading"
        assert saved.prompt_version == sm.PROMPT_VERSION
        assert len(json.loads(saved.payload_json)["nodes"]) == 3
        assert json.loads(saved.payload_json)["coverage"]["complete"] is True
        assert json.loads(saved.payload_json)["coverage"]["mode"] == "all_extracted_text"
        assert len(json.loads(saved.payload_json)["reading_passes"]) == 1
        assert json.loads(saved.source_versions_json)["mode"] == "whole_document"
    engine.dispose()


def test_discovery_task_searches_only_the_two_selected_books(monkeypatch):
    from backend.app.api import sensemaking as api, study
    from backend.app.services.rag import retriever

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        books = [Book(title=f"研究{i}", file_path=f"{i}.pdf", file_type="pdf", status="ready") for i in (1, 2)]
        db.add_all(books); db.flush()
        texts = ["本文把自主性界定为可以选择自己的每周排班时段。",
                 "本文把自主性界定为执行任务时可以决定操作方式。"]
        chunks = [Chunk(book_id=book.id, content=text, chunk_index=0, page_start=1)
                  for book, text in zip(books, texts)]
        db.add_all(chunks); db.commit()
        ids = [book.id for book in books]
        sources = [sm._candidate(chunk) for chunk in chunks]
        chunk_ids = {chunk.book_id: chunk.id for chunk in chunks}

    calls = []

    def retrieve(query, *, book_ids, top_k):
        calls.append((query, book_ids, top_k))
        return [{"chunk_id": chunk_ids[book_ids[0]]}]

    class Provider:
        model = "test-model"

    async def answer(*_args, **_kwargs):
        return json.dumps({"alignment": {"status": "partial", "reason": "口径不同", "definitions": [
            {"book_id": ids[0], "meaning": "排班", "evidence": _evidence(sources[0], "可以选择自己的每周排班时段")},
            {"book_id": ids[1], "meaning": "执行", "evidence": _evidence(sources[1], "执行任务时可以决定操作方式")},
        ]}, "discoveries": [{"title": "自主性维度", "observation": "两篇关注不同选择权",
            "why_tension": "同一名称下的效果不可直接合并", "tension": "两个维度",
            "already_answered": "各给出一个定义", "still_missing": "同样本测量", "next_step": "查看方法部分",
            "rival_explanations": [
            {"statement": "阶段不同", "assumption": "阶段影响行为", "prediction": "各阶段效果不同"},
            {"statement": "测量不同", "assumption": "指标不同", "prediction": "两项指标方向不同"}],
                           "discriminating_question": "同一研究能否同时测量两个维度？",
                           "evidence": [*_evidence(sources[0], "可以选择自己的每周排班时段"),
                                        *_evidence(sources[1], "执行任务时可以决定操作方式")]}]}, ensure_ascii=False)

    monkeypatch.setattr(api, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(api, "load_llm_config", lambda *_args: {"configured": True})
    monkeypatch.setattr(api.LLMRouter, "get", lambda *_args: Provider())
    monkeypatch.setattr(api, "update_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(retriever, "retrieve", retrieve)
    monkeypatch.setattr(study, "_stream_answer", answer)

    result = asyncio.run(api._run_discovery(object(), ids, "自主性"))
    assert calls == [("自主性", [ids[0]], 6), ("自主性", [ids[1]], 6)]
    with Session(engine) as db:
        saved = db.get(SensemakingArtifact, result["artifact_id"])
        payload = json.loads(saved.payload_json)
        assert payload["alignment"]["status"] == "partial"
        assert len(payload["discoveries"]) == 1
        assert payload["discoveries"][0]["review_status"] == "unreviewed"
    engine.dispose()


def test_review_records_append_only_revisions_with_source_trigger():
    from backend.app.api import sensemaking as api
    from fastapi import HTTPException

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = Book(title="测试", file_path="test.pdf", file_type="pdf", status="ready")
        db.add(book); db.flush()
        chunk = Chunk(book_id=book.id, content="作者认为案例之间的制度差异影响合作过程。", chunk_index=0, page_start=1)
        db.add(chunk); db.flush()
        source = sm._candidate(chunk)
        artifact = SensemakingArtifact(kind="reading", book_ids_json=json.dumps([book.id]), focus="",
            payload_json=json.dumps({"nodes": [{"id": "n1", "statement": "制度影响合作", "review_status": "unreviewed",
                "review_note": "", "evidence": sm.verified_evidence(_evidence(source, "案例之间的制度差异影响合作过程"), [source])}]}),
            source_versions_json=json.dumps(sm.source_versions([source])), model_name="test", prompt_version=sm.PROMPT_VERSION)
        db.add(artifact); db.commit()
        with pytest.raises(HTTPException) as invalid:
            api.review_argument_node(artifact.id, "n1", api.NodeReviewReq(status="agree", trigger_ref="fake"), db)
        assert invalid.value.status_code == 422
        api.review_argument_node(artifact.id, "n1", api.NodeReviewReq(status="unclear", note="证据不足", trigger_ref=source["ref"]), db)
        api.review_argument_node(artifact.id, "n1", api.NodeReviewReq(status="agree", note="原页说明了范围",
            remaining_doubt="仍需核查其他案例", trigger_ref=source["ref"]), db)
        revisions = db.scalars(select(SensemakingRevision).order_by(SensemakingRevision.id)).all()
        assert [(json.loads(row.before_json)["status"], json.loads(row.after_json)["status"])
                for row in revisions] == [("unreviewed", "unclear"), ("unclear", "agree")]
        assert all(row.trigger_ref == source["ref"] for row in revisions)
        assert json.loads(revisions[-1].after_json)["remaining_doubt"] == "仍需核查其他案例"
    engine.dispose()


def test_fulltext_interpretation_keeps_only_located_paragraphs(monkeypatch):
    from backend.app.api import sensemaking as api, study

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = Book(title="案例研究", file_path="study.pdf", file_type="pdf", status="ready")
        db.add(book); db.flush()
        chunk = Chunk(book_id=book.id, content="作者比较了三个案例，发现制度安排改变了合作过程。",
                      chunk_index=0, page_start=4)
        db.add(chunk); db.flush()
        source = sm._candidate(chunk)
        ev = sm.verified_evidence(_evidence(source, "制度安排改变了合作过程"), [source])
        claim = {"kind": "finding", "statement": "制度安排改变合作", "reasoning": "案例比较得出",
                 "evidence": ev}
        payload = {"coverage": {"mode": "all_extracted_text"}, "nodes": [claim],
                   "reading_passes": [{"id": "w1", "chapter_title": "发现", "claims": [claim]}]}
        artifact = SensemakingArtifact(kind="reading", book_ids_json=json.dumps([book.id]), focus="",
            payload_json=json.dumps(payload, ensure_ascii=False),
            source_versions_json=json.dumps(sm.document_digest(db, book.id)),
            model_name="test", prompt_version=sm.PROMPT_VERSION)
        db.add(artifact); db.commit()
        artifact_id = artifact.id

    class Provider:
        model = "test-model"

    async def answer(*_args, **_kwargs):
        return json.dumps({"paragraphs": [
            {"text": "作者基于案例比较提出制度安排影响合作过程。", "status": "author_interpretation",
             "evidence": _evidence(source, "制度安排改变了合作过程")},
            {"text": "没有根据的外推。", "status": "source_observation",
             "evidence": _evidence(source, "研究证明所有案例收入翻倍")},
        ], "unanswered": ["其他地区能否成立尚未回答"]}, ensure_ascii=False)

    monkeypatch.setattr(api, "load_llm_config", lambda *_args: {"configured": True})
    monkeypatch.setattr(api.LLMRouter, "get", lambda *_args: Provider())
    monkeypatch.setattr(study, "_stream_answer", answer)
    with Session(engine) as db:
        result = asyncio.run(api.generate_interpretation(
            artifact_id, api.InterpretReq(question="作者如何解释合作变化？"), db))
        assert len(result["answer"]["paragraphs"]) == 1
        assert result["answer"]["paragraphs"][0]["evidence"][0]["page"] == 4
        assert result["answer"]["unanswered"] == ["其他地区能否成立尚未回答"]
        assert db.scalar(select(SensemakingInterpretation).where(
            SensemakingInterpretation.artifact_id == artifact_id)) is not None
    engine.dispose()
