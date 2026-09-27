"""Versioned research archive for shelf and project assistants."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class ResearchScopeSnapshot(Base):
    __tablename__ = "research_scope_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    scope_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    shelf_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    book_versions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ResearchItem(Base):
    __tablename__ = "research_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("research_scope_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    concept_key: Mapped[str] = mapped_column(String(160), nullable=False, default="", index=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    origin: Mapped[str] = mapped_column(String(12), nullable=False, default="user")
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ResearchEvidence(Base):
    __tablename__ = "research_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("research_items.id", ondelete="CASCADE"), nullable=False, index=True)
    # Historical numeric anchors remain readable after a book or chunk is deleted.
    book_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    chunk_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    book_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unreviewed")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ResearchRevision(Base):
    __tablename__ = "research_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("research_items.id", ondelete="CASCADE"), nullable=False, index=True)
    before_json: Mapped[str] = mapped_column(Text, nullable=False)
    after_json: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_evidence_id: Mapped[int | None] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(12), nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
