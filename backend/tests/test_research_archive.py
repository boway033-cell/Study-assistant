"""Iteration A: frozen scope, verifiable evidence, and append-only revision history."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.assistant import delete_project
from backend.app.api.shelves import delete_shelf
from backend.app.core.database import Base, get_db
from backend.app.main import app
from backend.app.models import (AssistantProject, Book, Chunk, ResearchEvidence,
                                ResearchItem, ResearchRevision, ResearchScopeSnapshot, Shelf)
from backend.app.services import research_archive as archive


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def _source(db, title="文献甲"):
    book = Book(title=title, file_path=f"{title}.pdf", file_type="pdf", status="ready", file_hash="file-v1")
    db.add(book)
    db.flush()
    chunk = Chunk(book_id=book.id, chunk_index=0, content="透明度提高了排班选择，但也可能限制任务裁量。",
                  page_start=7, page_end=7)
    db.add(chunk)
    db.flush()
    return book, chunk


def test_snapshot_is_immutable_and_diff_tracks_membership_and_versions(db):
    first, chunk = _source(db)
    shelf = Shelf(name="劳动研究", books=[first])
    db.add(shelf)
    db.commit()
    frozen = archive.create_snapshot(db, "shelf", shelf.id)
    assert archive.create_snapshot(db, "shelf", shelf.id)["id"] == frozen["id"]
    second, _ = _source(db, "文献乙")
    shelf.books.append(second)
    db.commit()
    difference = archive.snapshot_diff(db, db.get(ResearchScopeSnapshot, frozen["id"]))
    assert [row["book_id"] for row in difference["added"]] == [second.id]
    assert [row["book_id"] for row in frozen["books"]] == [first.id]
    chunk.content = "重新 OCR 后的正文"
    db.commit()
    difference = archive.snapshot_diff(db, db.get(ResearchScopeSnapshot, frozen["id"]))
    assert difference["changed"][0]["before"]["text_digest"] == frozen["books"][0]["text_digest"]


def test_evidence_checks_snapshot_quote_and_source_status(db):
    first, chunk = _source(db)
    other, other_chunk = _source(db, "范围外")
    shelf = Shelf(name="范围", books=[first])
    db.add(shelf)
    db.commit()
    snapshot = db.get(ResearchScopeSnapshot, archive.create_snapshot(db, "shelf", shelf.id)["id"])
    item = db.get(ResearchItem, archive.create_item(db, snapshot, "judgment", "排班选择与任务裁量可能不同")["id"])
    with pytest.raises(Exception) as outside:
        archive.add_evidence(db, item, snapshot, other.id, other_chunk.id, "透明度提高了排班选择", "supports")
    assert outside.value.status_code == 422
    with pytest.raises(Exception) as invented:
        archive.add_evidence(db, item, snapshot, first.id, chunk.id, "原文没有这句话", "supports")
    assert invented.value.status_code == 422
    saved = archive.add_evidence(db, item, snapshot, first.id, chunk.id, "透明度提高了排班选择", "supports")
    assert saved["source_status"] == "current"
    chunk.content = "重新 OCR 后的正文"
    db.commit()
    assert archive.item_row(db, item, snapshot)["evidence"][0]["source_status"] == "source_changed"
    with pytest.raises(Exception) as stale:
        archive.add_evidence(db, item, snapshot, first.id, chunk.id, "重新 OCR 后的正文", "supports")
    assert stale.value.status_code == 409
    db.delete(first)
    db.commit()
    assert archive.item_row(db, item, snapshot)["evidence"][0]["source_status"] == "missing"


def test_http_revision_and_scope_binding(db):
    book, chunk = _source(db)
    shelf = Shelf(name="书架", books=[book])
    other_shelf = Shelf(name="另一个范围")
    db.add_all([shelf, other_shelf])
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            frozen = client.post("/api/assistant/research/snapshots", json={"scope_type": "shelf", "scope_id": shelf.id})
            assert frozen.status_code == 201
            snapshot_id = frozen.json()["id"]
            item = client.post("/api/assistant/research/items", json={
                "scope_type": "shelf", "scope_id": shelf.id, "snapshot_id": snapshot_id,
                "kind": "question", "statement": "透明度怎样改变自主性？"})
            assert item.status_code == 201
            item_id = item.json()["id"]
            assert client.get(f"/api/assistant/research/items/{item_id}", params={
                "scope_type": "shelf", "scope_id": other_shelf.id}).status_code == 404
            assert client.post("/api/assistant/research/items", json={
                "scope_type": "shelf", "scope_id": other_shelf.id, "snapshot_id": snapshot_id,
                "kind": "judgment", "statement": "不能越界"}).status_code == 404
            evidence = client.post(f"/api/assistant/research/items/{item_id}/evidence", json={
                "scope_type": "shelf", "scope_id": shelf.id, "book_id": book.id, "chunk_id": chunk.id,
                "quote": "透明度提高了排班选择", "relation": "supports"})
            assert evidence.status_code == 201
            patched = client.patch(f"/api/assistant/research/items/{item_id}", json={
                "scope_type": "shelf", "scope_id": shelf.id,
                "statement": "透明度可能同时影响两类自主性", "detail": {"unresolved": "仍需访谈"},
                "trigger_evidence_id": evidence.json()["id"]})
            assert patched.status_code == 200
            revisions = client.get(f"/api/assistant/research/items/{item_id}/revisions", params={
                "scope_type": "shelf", "scope_id": shelf.id}).json()
            assert len(revisions) == 2
            assert revisions[0]["before"]["statement"] == "透明度怎样改变自主性？"
            assert revisions[0]["after"]["detail"]["unresolved"] == "仍需访谈"
    finally:
        app.dependency_overrides.clear()


def test_delete_owned_archive_only(db):
    book, _ = _source(db)
    shelf = Shelf(name="书架", books=[book])
    db.add(shelf)
    db.commit()
    project = AssistantProject(name="项目")
    project.shelves.append(shelf)
    db.add(project)
    db.commit()
    for scope_type, scope_id in [("shelf", shelf.id), ("project", project.id)]:
        snapshot = db.get(ResearchScopeSnapshot, archive.create_snapshot(db, scope_type, scope_id)["id"])
        archive.create_item(db, snapshot, "question", "这是哪种自主性？")
    delete_project(project.id, db)
    assert db.scalar(select(ResearchScopeSnapshot).where(ResearchScopeSnapshot.scope_type == "project")) is None
    assert db.scalar(select(ResearchScopeSnapshot).where(ResearchScopeSnapshot.scope_type == "shelf")) is not None
    assert db.get(Book, book.id) is not None
    delete_shelf(shelf.id, db)
    assert db.scalar(select(ResearchScopeSnapshot)) is None
    assert db.scalar(select(ResearchItem)) is None
    assert db.scalar(select(ResearchEvidence)) is None
    assert db.scalar(select(ResearchRevision)) is None


def test_deleting_parent_shelf_cleans_child_archive(db):
    parent = Shelf(name="父书架")
    child = Shelf(name="子书架")
    parent.children.append(child)
    db.add_all([parent, child])
    db.commit()
    snapshot = db.get(ResearchScopeSnapshot, archive.create_snapshot(db, "shelf", child.id)["id"])
    archive.create_item(db, snapshot, "question", "子书架中的问题？")
    delete_shelf(parent.id, db)
    assert db.scalar(select(ResearchScopeSnapshot)) is None
    assert db.scalar(select(ResearchItem)) is None


def test_ai_proposal_requires_explicit_user_confirmation(db):
    shelf = Shelf(name="AI 提议范围")
    db.add(shelf)
    db.commit()
    snapshot = db.get(ResearchScopeSnapshot, archive.create_snapshot(db, "shelf", shelf.id)["id"])
    proposal = archive.create_item(db, snapshot, "judgment", "两种自主性可能不同", origin="ai")
    assert proposal["origin"] == "ai"
    assert proposal["review_status"] == "unreviewed"
    item = db.get(ResearchItem, proposal["id"])
    archive.update_item(db, item, snapshot, {"review_status": "confirmed"})
    revisions = archive.list_revisions(db, item)
    assert revisions[0]["before"]["review_status"] == "unreviewed"
    assert revisions[0]["after"]["review_status"] == "confirmed"
