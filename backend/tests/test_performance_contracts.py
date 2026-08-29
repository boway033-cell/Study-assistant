from __future__ import annotations

from backend.app.api.annotations import _anchor_payload
from backend.app.api.presentations import _resp
from backend.app.models import Book, PresentationDeck
from backend.app.schemas import AnnotationCreateReq


def test_office_annotation_anchor_preserves_structural_locator():
    request = AnnotationCreateReq(
        page=0,
        text="结构化原文",
        anchor={
            "schema_version": 3,
            "kind": "office",
            "quote": {"exact": "结构化原文", "prefix": "前文", "suffix": "后文"},
            "segments": [],
            "office": {"chapter_id": 8, "section_index": 2, "start_offset": 12, "end_offset": 18},
        },
    )
    page, rects, anchor_json, version = _anchor_payload(
        request, Book(title="Word", file_path="a.docx", file_type="docx", status="ready", file_hash="abc")
    )
    assert page == 0
    assert rects == "[]"
    assert '"chapter_id":8' in anchor_json
    assert version == 3


def test_presentation_list_summary_omits_heavy_fields():
    row = PresentationDeck(
        id=7, book_id=1, title="汇报", status="outline_ready",
        selection_json='{"books":[1,2],"sources":[{"text":"very long"}],"coverage":{"ratio":0.8}}',
        options_json='{"purpose":"test"}', outline_json='[{"title":"A"}]',
        manifest_json='{"large":"payload"}', qa_json='{"stage":"outline"}',
    )
    summary = _resp(row, detail=False)
    assert summary["source_book_count"] == 2
    assert summary["slide_count"] == 1
    assert all(key not in summary for key in ("selection", "options", "outline", "manifest", "qa"))
