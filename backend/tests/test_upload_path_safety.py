"""路径穿越与安全响应头回归测试。

背景：历史/脏数据库里的 books.file_path 可能是绝对路径或带 ../ 的路径，早期实现直接
用 uploads_dir / file_path 拼接，导致读取原文件、原版 PDF、按页文本、结构化文档的接口
可被用来读数据目录外的任意文件，删除接口可删任意文件。这里用虚构文件和隔离数据目录
验证所有入口都限制在 uploads 目录内。
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.core.config import resolve_under, settings
from backend.app.core.database import SessionLocal
from backend.app.main import app
from backend.app.models import Book


CLIENT = TestClient(app, base_url="http://127.0.0.1:8000")


def _make_book(db, *, file_path: str, file_type: str = "pdf", title: str = "虚构越界书") -> Book:
    book = Book(title=title, file_path=file_path, file_type=file_type, status="ready")
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


def test_resolve_under_blocks_escape(tmp_path: Path):
    root = tmp_path / "uploads"
    root.mkdir()
    inside = root / "ok.pdf"
    inside.write_bytes(b"%PDF-1.7 fictional")
    outside = tmp_path / "secret.txt"
    outside.write_text("fictional-secret", encoding="utf-8")

    assert resolve_under(root, "ok.pdf") == inside.resolve()
    assert resolve_under(root, str(inside)) == inside.resolve()
    assert resolve_under(root, "../secret.txt") is None
    assert resolve_under(root, str(outside)) is None
    assert resolve_under(root, "nested/../../secret.txt") is None


def test_dirty_file_path_cannot_read_outside_uploads(tmp_path: Path):
    secret = tmp_path / "fictional-secret.txt"
    secret.write_text("fictional-secret-value", encoding="utf-8")
    db = SessionLocal()
    book = _make_book(db, file_path=str(secret))
    try:
        for url in (
            f"/api/books/{book.id}/file",
            f"/api/books/{book.id}/rendered-file",
            f"/api/books/{book.id}/page/1",
        ):
            resp = CLIENT.get(url)
            assert resp.status_code == 404, (url, resp.status_code)
            assert "fictional-secret-value" not in resp.text
    finally:
        from backend.app.services.book_lifecycle import delete_book_records
        delete_book_records(db, book.id)
        db.commit()
        db.close()


def test_dirty_file_path_document_endpoint_is_blocked(tmp_path: Path):
    secret = tmp_path / "fictional.docx"
    secret.write_text("fictional-secret-value", encoding="utf-8")
    db = SessionLocal()
    book = _make_book(db, file_path=str(secret), file_type="docx", title="虚构越界文档")
    try:
        resp = CLIENT.get(f"/api/books/{book.id}/document")
        assert resp.status_code == 404
        assert "fictional-secret-value" not in resp.text
    finally:
        from backend.app.services.book_lifecycle import delete_book_records
        delete_book_records(db, book.id)
        db.commit()
        db.close()


def test_dirty_file_path_delete_keeps_outside_file(tmp_path: Path):
    secret = tmp_path / "must-survive.txt"
    secret.write_text("fictional-survivor", encoding="utf-8")
    db = SessionLocal()
    book = _make_book(db, file_path=str(secret))
    book_id = book.id
    try:
        resp = CLIENT.delete(f"/api/books/{book_id}")
        assert resp.status_code == 204
        assert secret.exists(), "越界路径绝不能导致数据目录外的文件被删除"
    finally:
        db.expire_all()
        assert db.get(Book, book_id) is None
        db.close()


def test_legitimate_upload_file_still_served():
    name = "fictional-path-safety-ok.pdf"
    path = settings.uploads_dir / name
    path.write_bytes(b"%PDF-1.7\nfictional legal file")
    db = SessionLocal()
    book = _make_book(db, file_path=name, title="虚构合法文件")
    try:
        resp = CLIENT.get(f"/api/books/{book.id}/file")
        assert resp.status_code == 200
        assert resp.content == path.read_bytes()
        # 文件名只暴露 basename，不把目录结构写进响应头
        assert name in resp.headers.get("content-disposition", "")
    finally:
        from backend.app.services.book_lifecycle import delete_book_records
        delete_book_records(db, book.id)
        db.commit()
        db.close()
        path.unlink(missing_ok=True)


def test_security_headers_present():
    resp = CLIENT.get("/api/health")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    assert resp.headers["referrer-policy"] == "same-origin"
