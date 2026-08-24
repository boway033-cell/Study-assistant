from pathlib import Path

import pytest


def test_paper_type_routing():
    from backend.app.services.presentation_deck import classify_paper_type

    assert classify_paper_type("A new transformer method", "algorithm benchmark baseline architecture") == "methods"
    assert classify_paper_type("Randomized trial", "patient cohort clinical survival intervention") == "clinical"
    assert classify_paper_type("A perspective", "review perspective future directions") == "review"


def test_private_network_urls_are_rejected(monkeypatch):
    from backend.app.services.literature_access import validate_public_https_url

    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="私有|本地|保留"):
        validate_public_https_url("https://example.test/file.pdf")


def test_library_handoff_does_not_embed_credentials(monkeypatch):
    from backend.app.services.literature_access import build_library_handoff

    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("1.1.1.1", 443))])
    url = build_library_handoff("https://library.example/search", "10.1000/test")
    assert "q=10.1000%2Ftest" in url
    assert "cookie" not in url.lower()


def test_local_outline_and_editable_pptx(tmp_path):
    from pptx import Presentation
    from backend.app.models import Book
    from backend.app.services.presentation_deck import _local_outline, audit_pptx, render_pptx

    sources = [{"source_id": "chunk:1", "chunk_id": 1, "chapter_id": 1,
                "page_start": 2, "page_end": 2, "text": "该方法提升了基准性能。边界条件仍需进一步验证。"}]
    outline = _local_outline("测试论文", "methods", sources, 8)
    book = Book(id=99, title="测试论文", file_path="missing.pdf", file_type="pdf", status="ready")
    path = tmp_path / "report.pptx"
    render_pptx(path, book, None, outline, sources, "methods",
                {"include_figures": False, "audience": "课题组", "purpose": "汇报", "slide_count": 8, "duration_minutes": 10})
    prs = Presentation(path)
    assert len(prs.slides) == 8
    assert any(shape.has_text_frame for shape in prs.slides[1].shapes)
    assert audit_pptx(path, outline)["ok"] is True
