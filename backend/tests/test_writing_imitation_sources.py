"""独立仿写取材系统：DNA 与内容解耦、来源分区、联网元数据检索的聚焦测试。

所有网络访问都被 mock；测试不依赖真实外网。真实 provider 的官方文档、身份标识、
速率与 attribution 假设见 backend/app/services/literature_search.py 文件头注释。
"""
import json
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from backend.app.api.literature import SearchReq, search_metadata
from backend.app.api.writing import (ImitateReq, _source_freshness, has_usable_content_source,
                                     imitate_with_profile, list_writing_sources)
from backend.app.core.database import SessionLocal
from backend.app.models import (Book, Chunk, EvidenceCard, KnowledgeNote, LiteratureAccessAttempt, PaperProfile,
                                StudyReport, WritingDnaProfile, WritingDnaRevision, WritingOutput)
from backend.app.services import literature_search as search_service
from backend.app.services import writing_lab
from backend.app.services.literature_search import LiteratureSearchError, search_crossref
from backend.app.services.writing_lab import collect_writing_knowledge


# ---------------------------------------------------------------------------
# 素材构造
# ---------------------------------------------------------------------------


def _ready_book(db, *, title="语料", chunks=6, page_size=1, file_hash=None, status="ready"):
    book = Book(title=f"{title}-{uuid4().hex[:8]}", file_path=f"{uuid4().hex}.pdf", file_type="pdf",
                status=status, total_pages=chunks * page_size, file_hash=file_hash)
    db.add(book); db.flush()
    for index in range(chunks):
        db.add(Chunk(book_id=book.id, chunk_index=index, page_start=index * page_size + 1,
                     page_end=(index + 1) * page_size,
                     content=f"{title}第{index}段：关于地方治理与参与机制的证据材料。" * 12))
    db.commit()
    return book


def _ready_profile(db, corpus_book_ids):
    profile = WritingDnaProfile(name=f"DNA-{uuid4().hex[:6]}", book_ids_json=json.dumps(corpus_book_ids),
                                status="ready", current_version=1, rights_acknowledged=1)
    db.add(profile); db.flush()
    db.add(WritingDnaRevision(profile_id=profile.id, version=1, language_dna="# 语言DNA",
                              structure_patterns="# 结构模板", logic_dna="# 逻辑结构DNA",
                              cognitive_framework="# 认知框架", visual_style_guide="# 视觉指南",
                              writing_dna="# 整合DNA", quality_json="{}"))
    db.commit()
    return profile


def _patch_llm(monkeypatch, payload_text):
    captured: dict = {}

    class _Provider:
        async def stream_chat(self, messages):
            captured["prompt"] = messages[-1]["content"]
            captured["system"] = messages[0]["content"]
            yield payload_text

    monkeypatch.setattr(writing_lab, "load_llm_config", lambda db, task: {})
    monkeypatch.setattr(writing_lab.LLMRouter, "get", staticmethod(lambda mode, cfg: _Provider()))
    return captured


WEB_SOURCE = {
    "provider": "crossref", "provider_id": "10.1000/xyz123",
    "title": "Participatory governance under fiscal stress",
    "authors": "Zhang, Li", "year": 2021, "doi": "10.1000/xyz123",
    "container_title": "Journal of Public Administration",
    "url": "https://doi.org/10.1000/xyz123",
    "abstract": ("这项研究比较了三种参与机制在财政紧缩条件下的稳定性，样本仅覆盖东部两省，"
                 "因此结论不能外推到全国范围，作者也提醒测量方式可能低估了基层动员成本。"),
    "evidence_level": "abstract", "retrieved_at": "2026-09-14T02:00:00+00:00",
}


# ---------------------------------------------------------------------------
# B. DNA 与内容解耦
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imitation_accepts_content_outside_dna_corpus_and_keeps_dna_as_calibration(monkeypatch):
    """选择 DNA-A 后仍可使用与 DNA-A 语料无关的笔记并成功生成。"""
    db = SessionLocal()
    try:
        corpus = _ready_book(db, title="DNA语料", chunks=3)
        other = _ready_book(db, title="DNA外语料", chunks=2)
        note = KnowledgeNote(book_id=other.id, title="DNA 之外的笔记", content="这条事实来自 DNA 语料之外。",
                             origin="user")
        db.add(note); db.commit(); db.refresh(note)
        profile = _ready_profile(db, [corpus.id])

        captured = _patch_llm(monkeypatch, f"# 新作\n\n核心判断成立 [NOTE:{note.id}]\n\n## 结论\n边界清晰。")
        output = await writing_lab.imitate(db, profile.id, "地方治理中的参与机制", "评论", 800, "",
                                           knowledge_note_ids=[note.id])
        audit = json.loads(output.audit_json)

        assert audit["calibration_policy"] == "dna_corpus_style_only"
        assert audit["dna_profile_id"] == profile.id and audit["dna_version"] == 1
        assert audit["content_source_counts"] == {"note": 1}
        assert [item["id"] for item in audit["content_manifest"] if item["type"] == "note"] == [note.id]
        # DNA 语料原文只出现在 calibration，绝不进入内容 manifest
        assert all(item["type"] != "local_literature" for item in audit["content_manifest"])
        assert corpus.title in audit["calibration_books"]
        # prompt 分区：风格约束与内容证据分开，且用户要求优先
        assert "【风格约束：Writing DNA" in captured["prompt"]
        assert "【内容证据：本次已选内容来源" in captured["prompt"]
        assert "【论证安排】" in captured["prompt"]
        assert "每段只推进一个主要论点" in captured["prompt"]
        assert "用户本次明确要求高于 Writing DNA 风格约束" in captured["prompt"]
        assert "[NOTE:" in output.output_text or "来源索引" in output.output_text
    finally:
        db.close()


@pytest.mark.asyncio
async def test_dna_corpus_anchor_is_rejected_as_citation_source(monkeypatch):
    """DNA calibration 原文的锚点不是合法来源；未知锚点被剥离并记录。"""
    db = SessionLocal()
    try:
        corpus = _ready_book(db, title="DNA语料", chunks=2)
        other = _ready_book(db, title="外部笔记来源", chunks=1)
        note = KnowledgeNote(book_id=other.id, title="笔记", content="可核验事实。", origin="user")
        db.add(note); db.commit(); db.refresh(note)
        profile = _ready_profile(db, [corpus.id])
        chunk = db.query(Chunk).filter(Chunk.book_id == corpus.id).first()

        rogue = f"[B{corpus.id}:C{chunk.id}:P1-1]"
        _patch_llm(monkeypatch, f"# 新作\n\n越界引用 {rogue} 以及合法引用 [NOTE:{note.id}]\n")
        output = await writing_lab.imitate(db, profile.id, "议题", "评论", 600, "",
                                           knowledge_note_ids=[note.id])
        audit = json.loads(output.audit_json)

        assert audit["invalid_anchors"] == [rogue]
        assert rogue not in output.output_text
        assert "[B" not in output.output_text
        assert "## 来源索引" in output.output_text
        assert audit["citation_count"] == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_local_literature_excerpts_keep_book_and_page_anchors(monkeypatch):
    """DNA corpus 之外的已解析本地文献可选作取材，并保留书目与页码/分块锚点。"""
    db = SessionLocal()
    try:
        corpus = _ready_book(db, title="DNA语料", chunks=2)
        local = _ready_book(db, title="本地文献", chunks=20, file_hash="hash-local-1")
        db.add(PaperProfile(book_id=local.id, authors="王五", published_year=2019, journal="治理研究"))
        db.commit()
        profile = _ready_profile(db, [corpus.id])

        captured = {}
        anchor_holder = {}

        class _Provider:
            async def stream_chat(self, messages):
                captured["prompt"] = messages[-1]["content"]
                yield f"# 新作\n\n本地证据支持该判断 {anchor_holder['anchor']}\n"

        monkeypatch.setattr(writing_lab, "load_llm_config", lambda db, task: {})
        monkeypatch.setattr(writing_lab.LLMRouter, "get", staticmethod(lambda mode, cfg: _Provider()))

        # 先单独取出本次会被选中的锚点，供假模型引用
        _, manifest, _ = collect_writing_knowledge(db, local_book_ids=[local.id], question="地方治理")
        local_entry = next(item for item in manifest if item["type"] == "local_literature")
        assert local_entry["selected_chunks"] <= 6
        assert local_entry["book_file_hash"] == "hash-local-1"
        assert all(value.startswith(f"[B{local.id}:C") for value in local_entry["anchors"])
        anchor_holder["anchor"] = local_entry["anchors"][0]

        output = await writing_lab.imitate(db, profile.id, "地方治理", "评论", 900, "",
                                           local_book_ids=[local.id])
        audit = json.loads(output.audit_json)
        recorded = next(item for item in audit["content_manifest"] if item["type"] == "local_literature")
        assert recorded["book_id"] == local.id
        assert recorded["anchors"][0].startswith(f"[B{local.id}:C")
        assert audit["invalid_anchors"] == []
        assert audit["citation_count"] >= 1
        assert "[B" not in output.output_text
        assert "王五" in captured["prompt"] or "2019" in captured["prompt"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_mixed_sources_keep_provider_doi_retrieved_at_and_evidence_level(monkeypatch):
    """混合来源：manifest 分类型计数，联网摘要保留 provider/DOI/retrieved_at/evidence_level。"""
    db = SessionLocal()
    try:
        local = _ready_book(db, title="本地文献", chunks=4)
        note = KnowledgeNote(book_id=local.id, title="笔记A", content="笔记事实。", origin="user")
        card = EvidenceCard(book_id=local.id, title="证据卡A", evidence_text="证据事实。",
                            claim_text="主张A", verification_status="verified")
        report = StudyReport(book_ids_json=json.dumps([local.id]), focus="审查报告A", content="报告结论。",
                             selection_json=json.dumps({"hypotheses": [{"text": "h"}], "evidence_summary": {"total": 1}}))
        db.add_all([note, card, report]); db.commit()
        for item in (note, card, report):
            db.refresh(item)
        profile = _ready_profile(db, [local.id])
        _, manifest, _ = collect_writing_knowledge(
            db, [note.id], [card.id], [report.id], [local.id], [WEB_SOURCE], question="地方治理")
        web_entry = next(item for item in manifest if item["type"] == "web")
        anchors = {"note": f"[NOTE:{note.id}]", "evidence": f"[EVIDENCE:{card.id}]",
                   "report": f"[REPORT:{report.id}]", "web": web_entry["anchors"][0],
                   "local": next(item for item in manifest if item["type"] == "local_literature")["anchors"][0]}
        body = (f"# 新作\n\n{anchors['note']} 主张一。{anchors['evidence']} 主张二。"
                f"{anchors['report']} 主张三。{anchors['local']} 主张四。{anchors['web']} 主张五。\n")
        _patch_llm(monkeypatch, body)
        output = await writing_lab.imitate(db, profile.id, "地方治理", "评论", 900, "",
                                           knowledge_note_ids=[note.id], evidence_card_ids=[card.id],
                                           report_ids=[report.id], local_book_ids=[local.id],
                                           external_sources=[WEB_SOURCE])
        audit = json.loads(output.audit_json)
        assert audit["content_source_counts"] == {"note": 1, "evidence": 1, "report": 1,
                                                 "local_literature": 1, "web": 1}
        snapshot = audit["external_sources"][0]
        assert snapshot["provider"] == "crossref" and snapshot["doi"] == "10.1000/xyz123"
        assert snapshot["retrieved_at"] == WEB_SOURCE["retrieved_at"]
        assert snapshot["evidence_level"] == "abstract"
        assert audit["external_retrieved_at"] == WEB_SOURCE["retrieved_at"]
        assert audit["invalid_anchors"] == []
        assert audit["unreferenced_sources"] == []
        assert audit["citation_count"] == 5
        labels = {item["anchor"]: item["label"] for item in audit["citation_notes"]}
        web_label = next(label for anchor, label in labels.items() if anchor.startswith("WEB:"))
        assert "crossref" in web_label and "10.1000/xyz123" in web_label and "摘要级依据" in web_label
    finally:
        db.close()


def test_external_metadata_only_source_cannot_be_used_as_fact():
    db = SessionLocal()
    try:
        metadata_only = {**WEB_SOURCE, "evidence_level": "metadata", "abstract": ""}
        with pytest.raises(ValueError, match="不能作为事实依据"):
            collect_writing_knowledge(db, external_sources=[metadata_only])
        with pytest.raises(ValueError, match="至少选择一个内容来源"):
            collect_writing_knowledge(db, external_sources=[])
    finally:
        db.close()


def test_mixed_maximum_selection_stays_within_global_context_budget():
    """所有来源同时达到请求上限时，总上下文仍不得突破模型预算。"""
    db = SessionLocal()
    try:
        base = _ready_book(db, title="混合预算来源", chunks=1)
        notes = [KnowledgeNote(book_id=base.id, title=f"长笔记 {index}", content="笔记事实。" * 300,
                               origin="user") for index in range(40)]
        cards = [EvidenceCard(book_id=base.id, title=f"长证据 {index}", claim_text="待核验主张",
                              evidence_text="证据材料。" * 300, verification_status="needs_review")
                 for index in range(40)]
        reports = [StudyReport(book_ids_json=json.dumps([base.id]), focus=f"长报告 {index}",
                               content="批判性审查结论。" * 300, selection_json="{}")
                   for index in range(10)]
        db.add_all([*notes, *cards, *reports]); db.commit()
        local_books = [_ready_book(db, title=f"本地预算文献 {index}", chunks=1) for index in range(20)]
        externals = [
            {**WEB_SOURCE, "provider_id": f"10.1000/budget-{index}",
             "doi": f"10.1000/budget-{index}",
             "url": f"https://doi.org/10.1000/budget-{index}",
             "abstract": "联网摘要材料。" * 300}
            for index in range(20)
        ]
        max_chars = 30000
        context, manifest, anchors = collect_writing_knowledge(
            db,
            knowledge_note_ids=[item.id for item in notes],
            evidence_card_ids=[item.id for item in cards],
            report_ids=[item.id for item in reports],
            local_book_ids=[item.id for item in local_books],
            external_sources=externals,
            question="地方治理参与机制",
            max_chars=max_chars,
        )
        assert len(manifest) == 130
        assert len(context) <= max_chars
        assert context.endswith(writing_lab.TRUNCATION_NOTICE)
        assert anchors
        assert all(anchor in context for anchor in anchors)
        assert all(anchor in anchors for item in manifest for anchor in item.get("anchors", []))
        assert all(item["excerpt_chars"] <= 230 for item in manifest
                   if item["type"] == "local_literature")
    finally:
        db.close()


@pytest.mark.parametrize("url", [
    "http://example.com/paper",                       # 非 HTTPS
    "https://user:secret@example.com/paper",          # 含凭据
    "https://127.0.0.1/paper",                        # 私网字面地址
    "https://localhost/paper",                        # 本地主机
])
def test_external_source_url_must_be_public_https(url):
    db = SessionLocal()
    try:
        with pytest.raises(ValueError, match="不合法"):
            collect_writing_knowledge(db, external_sources=[{**WEB_SOURCE, "url": url}])
    finally:
        db.close()


def test_oversized_and_unknown_selections_are_rejected():
    db = SessionLocal()
    try:
        with pytest.raises(ValueError, match="最多选择 20 篇本地文献"):
            collect_writing_knowledge(db, local_book_ids=list(range(1, 22)))
        with pytest.raises(ValueError, match="最多选择 40 条知识笔记"):
            collect_writing_knowledge(db, knowledge_note_ids=list(range(1, 42)))
        with pytest.raises(ValueError, match="最多选择 20 条联网摘要"):
            collect_writing_knowledge(db, external_sources=[{**WEB_SOURCE, "provider_id": f"10.1/{index}"}
                                                            for index in range(21)])
        with pytest.raises(ValueError, match="不存在或已被删除"):
            collect_writing_knowledge(db, knowledge_note_ids=[999999])
        with pytest.raises(ValueError, match="不存在或已被删除"):
            collect_writing_knowledge(db, local_book_ids=[999999])
    finally:
        db.close()


def test_local_book_must_be_parsed_before_use():
    db = SessionLocal()
    try:
        pending = Book(title=f"未解析-{uuid4().hex[:6]}", file_path="x.pdf", file_type="pdf", status="parsing")
        db.add(pending); db.commit(); db.refresh(pending)
        with pytest.raises(ValueError, match="尚未完成解析"):
            collect_writing_knowledge(db, local_book_ids=[pending.id])
    finally:
        db.close()


def _web(level="abstract", abstract=None, provider_id="10.1000/xyz123"):
    return {**WEB_SOURCE, "evidence_level": level, "provider_id": provider_id,
            "doi": provider_id, "abstract": WEB_SOURCE["abstract"] if abstract is None else abstract}


@pytest.mark.parametrize("knowledge,evidence,report,local,web,expected", [
    (None, None, None, None, None, False),
    ([1], None, None, None, None, True),
    (None, None, None, None, [_web("metadata", "")], False),
    (None, None, None, None, [_web("abstract", "过短")], False),
    (None, None, None, None, [_web("abstract", "足" * 60)], True),
])
def test_gate_requires_at_least_one_usable_content_source(knowledge, evidence, report, local, web, expected):
    req = ImitateReq(topic="议题标题", knowledge_note_ids=knowledge or [],
                     evidence_card_ids=evidence or [], report_ids=report or [],
                     local_book_ids=local or [], external_sources=web or [])
    assert has_usable_content_source(req) is expected


def test_imitate_endpoint_blocks_metadata_only_selection():
    db = SessionLocal()
    try:
        profile = _ready_profile(db, [])
        with pytest.raises(HTTPException) as excinfo:
            imitate_with_profile(profile.id, ImitateReq(topic="仅线索不能生成",
                                                         external_sources=[_web("metadata", "")]), db)
        assert excinfo.value.status_code == 400
        assert "不能作为事实依据" in excinfo.value.detail
    finally:
        db.close()


def test_freshness_uses_content_manifest_and_keeps_web_snapshot_immutable():
    db = SessionLocal()
    try:
        local = _ready_book(db, title="快照来源", chunks=3, file_hash="hash-fresh-1")
        note = KnowledgeNote(book_id=local.id, title="来源笔记", content="原始判断", source_refs_json="[]")
        db.add(note); db.commit(); db.refresh(note)
        _, manifest, _ = collect_writing_knowledge(db, [note.id], local_book_ids=[local.id],
                                                   external_sources=[WEB_SOURCE])
        output = WritingOutput(kind="imitation", title="快照", input_type="text", output_text="正文",
                               audit_json=json.dumps({"content_manifest": manifest}, ensure_ascii=False))
        db.add(output); db.commit(); db.refresh(output)
        try:
            fresh = _source_freshness(db, output)
            assert fresh["status"] == "fresh"
            assert fresh["immutable_snapshots"] == 1
            note.content = "修订后的判断"; db.commit()
            assert _source_freshness(db, output)["status"] == "stale"
            db.delete(note); db.commit()
            assert _source_freshness(db, output)["status"] == "missing"
        finally:
            db.delete(output); db.commit()
    finally:
        db.close()


def test_sources_endpoint_partitions_counts_and_reports_missing_ids():
    db = SessionLocal()
    try:
        book = _ready_book(db, title="分区来源", chunks=2)
        note = KnowledgeNote(book_id=book.id, title="可搜索笔记", content="内容", origin="user")
        card = EvidenceCard(book_id=book.id, title="可搜索证据", evidence_text="证据",
                            claim_text="主张", verification_status="needs_review")
        report = StudyReport(book_ids_json=json.dumps([book.id]), focus="可搜索报告", content="报告",
                             selection_json="{}")
        db.add_all([note, card, report]); db.commit()
        for item in (note, card, report):
            db.refresh(item)

        notes = list_writing_sources(category="note", q=None, page=1, page_size=5, ids=None, db=db)
        assert notes["counts"]["note"] >= 1 and notes["counts"]["evidence"] >= 1
        assert notes["counts"]["report"] >= 1 and notes["counts"]["local_literature"] >= 1
        assert any(item["book_title"] for item in notes["items"])

        filtered = list_writing_sources(category="evidence", q="可搜索", page=1, page_size=5, ids=None, db=db)
        assert any(item["id"] == card.id and item["verification_status"] == "needs_review"
                   for item in filtered["items"])

        reports = list_writing_sources(category="report", q=None, page=1, page_size=5, ids=None, db=db)
        assert any(item["id"] == report.id and item["book_ids"] == [book.id] for item in reports["items"])

        books = list_writing_sources(category="local_literature", q=None, page=1, page_size=5, ids=None, db=db)
        assert any(item["id"] == book.id and item["chunk_count"] == 2 for item in books["items"])

        stale = list_writing_sources(category="note", q=None, page=1, page_size=5,
                                     ids=[note.id, 999999], db=db)
        assert stale["missing_ids"] == [999999]
        assert [item["id"] for item in stale["items"]] == [note.id]

        with pytest.raises(HTTPException) as excinfo:
            list_writing_sources(category="unknown", q=None, page=1, page_size=5, ids=None, db=db)
        assert excinfo.value.status_code == 400
    finally:
        db.close()


# ---------------------------------------------------------------------------
# C. 联网元数据检索 provider（全部 mock，不触网）
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, body: bytes = b"{}", headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    async def aiter_bytes(self, size):
        yield self._body


class _FakeStream:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    """按序返回预设结果；异常项直接抛出，用来模拟超时/网络错误。"""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def stream(self, method, url, params=None):
        outcome = self._outcomes[min(self.calls, len(self._outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeStream(outcome)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _use_fake_client(monkeypatch, outcomes):
    client = _FakeClient(outcomes)
    monkeypatch.setattr(search_service, "ensure_public_https", lambda url: url)
    monkeypatch.setattr(search_service.httpx, "AsyncClient", lambda **kwargs: client)
    return client


CROSSREF_BODY = json.dumps({"message": {"items": [
    {"DOI": "10.1000/abs", "title": ["摘要级题名"], "author": [{"given": "Li", "family": "Zhang"}],
     "issued": {"date-parts": [[2021, 5]]}, "URL": "https://doi.org/10.1000/abs",
     "abstract": "<jats:p>这是一段可用的摘要文本，说明了样本范围与主要限制条件。</jats:p>",
     "container-title": ["治理研究"], "type": "journal-article"},
    {"DOI": "10.1000/meta", "title": ["仅元数据题名"], "author": [],
     "issued": {"date-parts": [[2018]]}, "URL": "http://insecure.example.com/x",
     "container-title": ["另一期刊"], "type": "journal-article"},
]}}, ensure_ascii=False).encode()


@pytest.mark.asyncio
async def test_search_normalizes_results_and_labels_evidence_level(monkeypatch):
    _use_fake_client(monkeypatch, [_FakeResponse(200, CROSSREF_BODY)])
    results = await search_crossref("地方治理 参与机制", rows=5, mailto="owner@example.com")
    assert [item["evidence_level"] for item in results] == ["abstract", "metadata"]
    abstract_item, meta_item = results
    assert abstract_item["provider"] == "crossref" and abstract_item["provider_id"] == "10.1000/abs"
    assert abstract_item["authors"] == "Zhang Li" and abstract_item["year"] == 2021
    assert abstract_item["url"].startswith("https://")
    assert "<jats" not in abstract_item["abstract"] and "摘要文本" in abstract_item["abstract"]
    assert abstract_item["retrieved_at"]
    # 非 HTTPS 的原始 URL 会被规范化为 DOI 解析地址
    assert meta_item["url"] == "https://doi.org/10.1000/meta"
    assert meta_item["abstract"] == ""


@pytest.mark.asyncio
async def test_search_empty_result_is_not_an_error(monkeypatch):
    _use_fake_client(monkeypatch, [_FakeResponse(200, json.dumps({"message": {"items": []}}).encode())])
    assert await search_crossref("无结果的关键词") == []


@pytest.mark.asyncio
async def test_search_rate_limit_timeout_and_redirect_are_recoverable(monkeypatch):
    _use_fake_client(monkeypatch, [_FakeResponse(429)])
    with pytest.raises(LiteratureSearchError, match="限流"):
        await search_crossref("主题检索")

    _use_fake_client(monkeypatch, [httpx.TimeoutException("t")])
    with pytest.raises(LiteratureSearchError, match="超时"):
        await search_crossref("主题检索")

    _use_fake_client(monkeypatch, [_FakeResponse(302, headers={"location": "https://evil.example.com"})])
    with pytest.raises(LiteratureSearchError, match="跳转"):
        await search_crossref("主题检索")


@pytest.mark.asyncio
async def test_search_retries_server_error_then_succeeds(monkeypatch):
    client = _use_fake_client(monkeypatch, [_FakeResponse(503), _FakeResponse(200, CROSSREF_BODY)])
    results = await search_crossref("主题检索")
    assert client.calls == 2 and len(results) == 2


@pytest.mark.asyncio
async def test_search_rejects_oversized_and_unparsable_payloads(monkeypatch):
    _use_fake_client(monkeypatch, [_FakeResponse(200, b"x" * (search_service.MAX_RESPONSE_BYTES + 10))])
    with pytest.raises(LiteratureSearchError, match="过大"):
        await search_crossref("主题检索")

    _use_fake_client(monkeypatch, [_FakeResponse(200, b"<html>not json</html>")])
    with pytest.raises(LiteratureSearchError, match="无法解析"):
        await search_crossref("主题检索")


@pytest.mark.asyncio
async def test_search_endpoint_records_attempt_and_hides_internals(monkeypatch):
    db = SessionLocal()
    try:
        async def _fake_search(query, *, provider="crossref", rows=10, mailto=""):
            return [{**WEB_SOURCE, "provider_id": f"10.1000/{query[:4]}", "doi": f"10.1000/{query[:4]}"}]

        monkeypatch.setattr("backend.app.api.literature.search_literature", _fake_search)
        payload = await search_metadata(SearchReq(query="地方治理参与机制"), db)
        assert payload["provider"] == "crossref" and payload["count"] == 1
        assert payload["results"][0]["evidence_level"] == "abstract"
        assert payload["evidence_levels"]["metadata"].startswith("元数据线索")
        attempt = db.get(LiteratureAccessAttempt, payload["attempt_id"])
        assert attempt.status == "searched" and attempt.route == "crossref_metadata"
        manifest = json.loads(attempt.manifest_json)
        assert manifest["secrets_included"] is False and manifest["full_text_downloaded"] is False
    finally:
        db.close()


@pytest.mark.asyncio
async def test_search_endpoint_maps_provider_failure_without_leaking(monkeypatch):
    db = SessionLocal()
    try:
        async def _boom(query, *, provider="crossref", rows=10, mailto=""):
            raise LiteratureSearchError("文献检索服务暂时限流，请稍后重试")

        monkeypatch.setattr("backend.app.api.literature.search_literature", _boom)
        with pytest.raises(HTTPException) as excinfo:
            await search_metadata(SearchReq(query="地方治理参与机制"), db)
        assert excinfo.value.status_code == 502
        assert excinfo.value.detail == "文献检索服务暂时限流，请稍后重试"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_search_rejects_unknown_provider():
    db = SessionLocal()
    try:
        with pytest.raises(HTTPException) as excinfo:
            await search_metadata(SearchReq(query="地方治理参与机制", provider="scraped-web"), db)
        assert excinfo.value.status_code == 400
    finally:
        db.close()
