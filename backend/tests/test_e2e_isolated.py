"""P1-1/P2-1 隔离端到端回归：导入 → 阅读/来源校验 → 问答范围 → 重解析取消/成功 → 备份恢复。

约束：只使用测试隔离 DATA_DIR（conftest 已设置）与虚构文件；不调用真实模型
（问答用确定性 fake provider）；不触碰 backend/data。
"""
from __future__ import annotations

import io
import json
import sqlite3
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.database import SessionLocal
from backend.app.models import Book, Chunk, SourceVersion
from backend.app.services.source_identity import active_version, content_hash

OLD_PHRASE = "zqxunique original phrase about algorithms"
NEW_PHRASE = "zqxunique revised phrase after reparse"
CHAT_QUESTION = "zqxunique 的含义是什么"


def _docx_bytes(phrase: str) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading("第一章 虚构章节", level=1)
    doc.add_paragraph(f"{phrase}。这是完全虚构的端到端测试正文，仅用于隔离验证。")
    doc.add_paragraph("第二段：算法会重复检查内容是否保持一致，避免旧引用错指新正文。")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _wait(timeout: float, predicate, message: str):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.2)
    raise AssertionError(f"{message}（最后状态: {last!r}）")


def _wait_book_ready(client: TestClient, book_id: int, timeout: float = 120) -> dict:
    def check():
        resp = client.get(f"/api/books/{book_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        return data if data["status"] == "ready" else None

    return _wait(timeout, check, "导入/重解析未在限定时间内完成")


def _task_status(client: TestClient, task_id: str) -> str:
    resp = client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()["status"]


def _upload(client: TestClient) -> int:
    payload = _docx_bytes(OLD_PHRASE)
    resp = client.post(
        "/api/books/upload",
        files={"file": ("e2e_fictional.docx", payload,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if data is not None:
            payload = json.loads(data)
            payload["event"] = name
            events.append(payload)
    return events


class _FakeProvider:
    name = "fake"
    model = "fake-model"
    selected_provider_id = "fake"
    selected_model = "fake-model"
    last_style_audit = None

    async def stream_chat(self, messages):
        for token in ("这是", "虚构答案"):
            yield token


def test_e2e_import_identity_scope_chat_and_reparse(monkeypatch):
    from backend.app.main import app
    from backend.app.api import chat as chat_api
    from backend.app.worker import import_task

    provider = _FakeProvider()
    monkeypatch.setattr(chat_api, "load_llm_config", lambda *_: {"model": "fake-model"})
    monkeypatch.setattr(chat_api.LLMRouter, "get", lambda *args, **kwargs: provider)

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        book_id = _upload(client)
        detail = _wait_book_ready(client, book_id)
        assert detail["chapters"], "导入后应有章节"

        with SessionLocal() as db:
            chunk = db.scalars(select(Chunk).where(Chunk.book_id == book_id)
                               .order_by(Chunk.chunk_index)).first()
            assert chunk is not None and OLD_PHRASE in chunk.content
            old_chunk_id = chunk.id
            old_hash = chunk.content_hash or content_hash(chunk.content)
            old_version_uid = active_version(db, book_id).version_uid
            assert old_hash and old_version_uid

        # 阅读/来源回跳：无指纹的旧式请求会明确标未验证；带指纹则校验为原文。
        resp = client.get(f"/api/books/{book_id}/chunk/{old_chunk_id}")
        assert resp.status_code == 200
        assert resp.json()["identity_status"] == "legacy_unverified"
        resp = client.get(f"/api/books/{book_id}/chunk/{old_chunk_id}",
                          params={"content_hash": old_hash, "version_uid": old_version_uid})
        body = resp.json()
        assert body["identity_status"] == "ok" and body["identity_verified"] is True
        assert OLD_PHRASE in body["content"]

        # 搜索返回可复核身份，供新引用持久化。
        hits = client.get("/api/search", params={"q": "zqxunique", "book_id": book_id}).json()["items"]
        assert hits and all(hit.get("content_hash") and hit.get("source_version_uid") for hit in hits)

        # 问答范围：只带 book_id，不带书架/项目范围；meta 必须精确反映该范围。
        resp = client.post("/api/chat", json={"book_id": book_id, "question": CHAT_QUESTION})
        assert resp.status_code == 200, resp.text
        events = _parse_sse(resp.text)
        meta = next(event for event in events if event.get("event") == "meta")
        assert meta["book_ids"] == [book_id]
        assert meta["scope_type"] is None and meta["scope_id"] is None
        done = next(event for event in events if event.get("event") == "done")
        chat_id = done["chat_id"]
        payload_sources = done["sources"]
        assert payload_sources, "回答应带来源"
        assert any(source.get("content_hash") and source.get("source_version_uid")
                   for source in payload_sources)
        assert all(source.get("book_id") == book_id for source in payload_sources)

        history = client.get(f"/api/chat/{chat_id}").json()
        assert history["sources"][0]["identity_status"] == "ok"
        assert history["sources"][0]["content"] and OLD_PHRASE in history["sources"][0]["content"]

        # ---- 运行中取消重解析：旧正文/FTS/历史引用保持可用 ----
        real_parse = import_task.parse_document

        def slow_parse(path):
            time.sleep(1.5)
            return real_parse(path)

        monkeypatch.setattr(import_task, "parse_document", slow_parse)
        resp = client.post(f"/api/books/{book_id}/reparse")
        assert resp.status_code == 200, resp.text
        task_id = resp.json()["task_id"]
        _wait(20, lambda: _task_status(client, task_id) == "running",
              "重解析任务没有进入运行中")
        resp = client.post(f"/api/tasks/{task_id}/cancel")
        assert resp.status_code == 200, resp.text
        _wait(60, lambda: _task_status(client, task_id) == "cancelled",
              "取消后任务状态不是 cancelled")
        assert client.get(f"/api/books/{book_id}").json()["status"] == "ready"
        with SessionLocal() as db:
            assert active_version(db, book_id).version_uid == old_version_uid
            assert db.query(SourceVersion).filter_by(book_id=book_id, status="abandoned").count() == 1
            old_content = db.get(Chunk, old_chunk_id).content
            assert OLD_PHRASE in old_content and NEW_PHRASE not in old_content
        assert client.get(f"/api/search", params={"q": "zqxunique"}).json()["items"]
        monkeypatch.setattr(import_task, "parse_document", real_parse)

        # ---- 成功重解析：覆盖上传文件后重解析，旧引用必须返回归档原文或明确失效 ----
        from backend.app.core.config import settings

        with SessionLocal() as db:
            book = db.get(Book, book_id)
            upload_path = settings.uploads_dir / book.file_path
        upload_path.write_bytes(_docx_bytes(NEW_PHRASE))
        resp = client.post(f"/api/books/{book_id}/reparse")
        assert resp.status_code == 200, resp.text
        task_id = resp.json()["task_id"]
        _wait_book_ready(client, book_id)
        _wait(60, lambda: _task_status(client, task_id) in {"done", "failed"},
              "重解析任务没有结束")
        assert _task_status(client, task_id) == "done"

        with SessionLocal() as db:
            current_version = active_version(db, book_id)
            assert current_version is not None and current_version.version_uid != old_version_uid
            assert db.query(SourceVersion).filter_by(book_id=book_id, status="active").count() == 1
            chunks = db.scalars(select(Chunk).where(Chunk.book_id == book_id)).all()
            assert any(NEW_PHRASE in item.content for item in chunks)
            assert all(OLD_PHRASE not in item.content for item in chunks)

        old_ref = client.get(f"/api/books/{book_id}/chunk/{old_chunk_id}",
                             params={"content_hash": old_hash, "version_uid": old_version_uid})
        old_ref = old_ref.json()
        assert old_ref["identity_status"] == "stale", old_ref
        assert old_ref["identity_verified"] is True
        assert OLD_PHRASE in old_ref["content"]

        # 历史问答引用同样返回原文（stale），绝不显示新正文。
        history = client.get(f"/api/chat/{chat_id}").json()
        source = history["sources"][0]
        assert source["identity_status"] == "stale", source
        assert source["content"] and OLD_PHRASE in source["content"]
        assert NEW_PHRASE not in source["content"]

        # FTS 只索引当前版本：新短语可搜到，旧短语不再命中。
        assert client.get("/api/search", params={"q": "revised", "book_id": book_id}).json()["items"]
        assert not client.get("/api/search", params={"q": "original", "book_id": book_id}).json()["items"]


def test_e2e_backup_and_restore_isolated(tmp_path, capsys):
    import shutil

    from scripts.maintenance import backup as backup_script

    source = tmp_path / "data"
    (source / "uploads").mkdir(parents=True)
    database = source / "study.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO books (title) VALUES ('虚构备份书')")
        conn.commit()
    (source / "uploads" / "fictional.pdf").write_bytes(b"%PDF-1.4 fictional attachment")
    archive = tmp_path / "backup.zip"
    work = tmp_path / "work"
    work.mkdir()

    code = backup_script.main([
        "--data-dir", str(source), "--output", str(archive), "--work-dir", str(work),
        "--allow-online", "--port", "9999", "--quiet",
    ])
    assert code == 0, capsys.readouterr().out
    assert archive.is_file()

    restore = tmp_path / "restore"
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        bundle.extractall(restore)
    assert any(name.endswith("study.db") for name in names)
    assert any(name.endswith("fictional.pdf") for name in names)
    assert not any(".env" in name for name in names)
    restored_db = next(restore.rglob("study.db"))
    with sqlite3.connect(restored_db) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT title FROM books").fetchall() == [("虚构备份书",)]
    restored_attachment = next(restore.rglob("fictional.pdf"))
    assert restored_attachment.read_bytes().startswith(b"%PDF-1.4 fictional")
    shutil.rmtree(work, ignore_errors=True)