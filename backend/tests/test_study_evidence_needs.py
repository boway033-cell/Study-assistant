"""Evidence-needs checks must survive full-scope reading and selection boundaries."""

from backend.app.api.study import _prepare_research_evidence


def _passage(chunk_id: int, chapter_id: int | None = None) -> dict:
    return {
        "book_id": 1, "chunk_id": chunk_id, "chapter_id": chapter_id,
        "page_start": 3, "page_end": 3, "book_title": "选中文献",
        "chapter_title": "第三章", "context": f"第 {chunk_id} 块的原始反例与限定条件。",
    }


def test_long_full_read_checks_needs_and_restores_raw_passage():
    calls = []

    def retrieve(query, *, book_ids, top_k):
        calls.append((query, book_ids, top_k))
        # Vector search can omit chapter metadata. The read-scope chunk anchor
        # must be retained instead of treating that hit as outside scope.
        return [_passage(7, chapter_id=None)]

    allowed = {"B1:CH4:P3:C7"}
    context, refs, checks = _prepare_research_evidence(
        retrieve, "研究主题", "概要", allowed, "研究主题",
        {"evidence_needs": ["反例"], "subquestions": ["机制"]}, [1],
        full_scope=True, long_reading=True,
    )

    assert calls == [("反例", [1], 6)]
    assert "原始反例与限定条件" in context
    assert "[B1:CH4:P3:C7]" in context
    assert refs == allowed
    assert checks == [{"need": "反例", "status": "candidate_found",
                       "source_refs": ["B1:CH4:P3:C7"]}]


def test_selected_chapter_does_not_admit_outside_passages():
    calls = []

    def retrieve(query, *, book_ids, top_k):
        calls.append((book_ids, top_k))
        return [_passage(8, chapter_id=5), _passage(7, chapter_id=4)]

    context, refs, checks = _prepare_research_evidence(
        retrieve, "研究主题", "概要", {"B1:CH4:P3:C7"}, "研究主题",
        {"evidence_needs": ["反例"]}, [1], full_scope=True,
        long_reading=True, chapter_ids=[4],
    )

    assert calls == [([1], 24)]
    assert "第 7 块" in context and "第 8 块" not in context
    assert refs == {"B1:CH4:P3:C7"}
    assert checks[0]["source_refs"] == ["B1:CH4:P3:C7"]


def test_full_read_reports_missing_candidate_without_expanding_scope():
    def retrieve(query, *, book_ids, top_k):
        return [_passage(8, chapter_id=5)]

    context, refs, checks = _prepare_research_evidence(
        retrieve, "研究主题", "概要", {"B1:CH4:P3:C7"}, "研究主题",
        {"evidence_needs": ["反例"]}, [1], full_scope=True,
        long_reading=True, chapter_ids=[4],
    )

    assert context == "研究主题"
    assert refs == {"B1:CH4:P3:C7"}
    assert checks == [{"need": "反例", "status": "no_candidate", "source_refs": []}]


def test_short_full_read_checks_needs_without_duplicating_source_text():
    def retrieve(query, *, book_ids, top_k):
        return [_passage(7, chapter_id=4)]

    original = "[B1:CH4:P3:C7]原始反例与限定条件。"
    context, _, checks = _prepare_research_evidence(
        retrieve, original, "概要", {"B1:CH4:P3:C7"}, "研究主题",
        {"evidence_needs": ["反例"]}, [1], full_scope=True,
        long_reading=False,
    )

    assert context == original
    assert checks[0]["status"] == "candidate_found"


def test_full_read_checks_all_six_planned_needs():
    needs = [f"证据需求 {index}" for index in range(1, 7)]
    calls = []

    def retrieve(query, *, book_ids, top_k):
        calls.append(query)
        return [_passage(needs.index(query) + 1, chapter_id=4)]

    allowed = {f"B1:CH4:P3:C{index}" for index in range(1, 7)}
    _, _, checks = _prepare_research_evidence(
        retrieve, "完整原文", "概要", allowed, "研究主题",
        {"evidence_needs": needs}, [1], full_scope=True,
        long_reading=False,
    )

    assert calls == needs
    assert [check["status"] for check in checks] == ["candidate_found"] * 6


def test_note_only_selection_does_not_trigger_book_search():
    def forbidden_retrieve(*_args, **_kwargs):
        raise AssertionError("Selected notes must not trigger a book-wide search")

    context, refs, checks = _prepare_research_evidence(
        forbidden_retrieve, "[B1:NOTE2]用户笔记", "概要", {"B1:NOTE2"},
        "研究主题", {"evidence_needs": ["反例"]}, [1],
        full_scope=False, long_reading=False,
    )

    assert context == "[B1:NOTE2]用户笔记"
    assert refs == {"B1:NOTE2"}
    assert checks == []
