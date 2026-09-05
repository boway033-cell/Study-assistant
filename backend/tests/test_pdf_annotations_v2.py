"""PDF 批注 v2：坐标约束、多页锚点和 OCR 框缓存。"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException


def test_multpage_anchor_is_versioned_and_keeps_v1_projection():
    from backend.app.api.annotations import _anchor_payload
    from backend.app.models import Book
    from backend.app.schemas import AnnotationCreateReq

    book = Book(title="paper", file_path="paper.pdf", file_type="pdf", total_pages=10, file_hash="abc")
    req = AnnotationCreateReq(
        page=2,
        text="跨页原文",
        anchor={
            "quote": {"exact": "跨页原文", "prefix": "前", "suffix": "后"},
            "segments": [
                {"page": 2, "source": "pdf-text", "rects": [{"x": .2, "y": .9, "w": .3, "h": .05}]},
                {"page": 3, "source": "ocr", "rects": [{"x": .1, "y": .1, "w": .2, "h": .04}]},
            ],
        },
    )
    page, legacy, anchor_json, version = _anchor_payload(req, book)
    assert page == 2
    assert version == 2
    assert json.loads(legacy) == [{"x": .2, "y": .9, "w": .3, "h": .05}]
    anchor = json.loads(anchor_json)
    assert anchor["document_fingerprint"] == "abc"
    assert [segment["page"] for segment in anchor["segments"]] == [2, 3]


def test_rects_reject_out_of_page_coordinates():
    from backend.app.api.annotations import _rects_from_json

    with pytest.raises(HTTPException) as exc:
        _rects_from_json('[{"x":0.2,"y":1.02,"w":0.2,"h":0.03}]')
    assert exc.value.status_code == 422


def test_ocr_box_normalization_clips_image_edges():
    from backend.app.services.parser.ocr_layer import _normal_box

    rect = _normal_box([[-5, 10], [105, 10], [105, 30], [-5, 30]], 100, 100)
    assert rect == {"x": 0.0, "y": 0.1, "w": 1.0, "h": 0.2}


def test_annotation_round_trip_with_multpage_anchor():
    from backend.app.api.annotations import create_annotation, list_annotations
    from backend.app.core.database import SessionLocal
    from backend.app.models import Book
    from backend.app.schemas import AnnotationCreateReq

    db = SessionLocal()
    try:
        book = Book(title="anchor-roundtrip", file_path="roundtrip.pdf", file_type="pdf", total_pages=4)
        db.add(book)
        db.commit()
        db.refresh(book)
        req = AnnotationCreateReq(page=1, text="原文", anchor={
            "quote": {"exact": "原文"},
            "segments": [
                {"page": 1, "source": "pdf-text", "rects": [{"x": .1, "y": .2, "w": .3, "h": .04}]},
                {"page": 2, "source": "pdf-text", "rects": [{"x": .1, "y": .1, "w": .2, "h": .04}]},
            ],
        })
        created = create_annotation(book.id, req, db)
        assert created.schema_version == 2
        assert len(json.loads(created.anchor_json)["segments"]) == 2
        assert list_annotations(book.id, None, db)[0].id == created.id
    finally:
        db.close()


def test_office_rendered_pdf_anchor_uses_rendered_page_numbers():
    from backend.app.api.annotations import _anchor_payload
    from backend.app.models import Book
    from backend.app.schemas import AnnotationCreateReq

    book = Book(title="report", file_path="report.docx", file_type="docx", total_pages=1, file_hash="abc")
    req = AnnotationCreateReq(page=4, text="第四页", anchor={
        "quote": {"exact": "第四页"},
        "segments": [{"page": 4, "source": "pdf-text", "rects": [
            {"x": .1, "y": .2, "w": .3, "h": .04},
        ]}],
    })

    page, _, _, version = _anchor_payload(req, book)
    assert (page, version) == (4, 2)


def test_office_rendered_pdf_is_used_for_ocr_text_layer(tmp_path, monkeypatch):
    from backend.app.api import annotations
    from backend.app.core.config import settings
    from backend.app.models import Book
    from backend.app.services import office_render

    source = tmp_path / "report.docx"
    source.write_bytes(b"office")
    rendered = tmp_path / "report.pdf"
    rendered.write_bytes(b"%PDF-rendered")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path)
    monkeypatch.setattr(office_render, "render_office_pdf", lambda *args: rendered)

    book = Book(title="report", file_path=source.name, file_type="docx", file_hash="a" * 64)
    assert annotations._book_pdf_path(book) == rendered
