"""ORM 模型：books / chapters / chunks / notes / chat_logs / cards / review_logs / quizzes / attempts / settings
对应 docs/02-database.md。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)  # pdf/docx/pptx
    file_size: Mapped[int | None] = mapped_column(Integer)
    total_pages: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending/parsing/ready/failed
    error_msg: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    chapters: Mapped[list["Chapter"]] = relationship(back_populates="book", cascade="all, delete-orphan")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="book", cascade="all, delete-orphan")
    cards: Mapped[list["Card"]] = relationship(back_populates="book", cascade="all, delete-orphan")
    quizzes: Mapped[list["Quiz"]] = relationship(back_populates="book", cascade="all, delete-orphan")


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_page: Mapped[int | None] = mapped_column(Integer)
    end_page: Mapped[int | None] = mapped_column(Integer)

    book: Mapped["Book"] = relationship(back_populates="chapters")
    children: Mapped[list["Chapter"]] = relationship()
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="chapter")
    cards: Mapped[list["Card"]] = relationship(back_populates="chapter")
    quizzes: Mapped[list["Quiz"]] = relationship(back_populates="chapter")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    word_count: Mapped[int | None] = mapped_column(Integer)

    book: Mapped["Book"] = relationship(back_populates="chunks")
    chapter: Mapped["Chapter | None"] = relationship(back_populates="chunks")


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id"))
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    highlight_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ChatLog(Base):
    __tablename__ = "chat_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id"), index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    sources_json: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)  # local/cloud
    model_name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id"))
    front: Mapped[str] = mapped_column(Text, nullable=False)
    back: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual/auto
    # FSRS 状态
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="New")  # New/Learning/Review/Relearning
    stability: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    difficulty: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    due: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_review: Mapped[datetime | None] = mapped_column(DateTime)
    elapsed_days: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_days: Mapped[int] = mapped_column(Integer, default=0)
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    book: Mapped["Book"] = relationship(back_populates="cards")
    chapter: Mapped["Chapter | None"] = relationship(back_populates="cards")
    review_logs: Mapped[list["ReviewLog"]] = relationship(back_populates="card", cascade="all, delete-orphan")


class ReviewLog(Base):
    __tablename__ = "review_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), nullable=False, index=True)
    rating: Mapped[str] = mapped_column(String(10), nullable=False)  # again/hard/good/easy
    state_before: Mapped[str | None] = mapped_column(String(20))
    state_after: Mapped[str | None] = mapped_column(String(20))
    elapsed_days: Mapped[int | None] = mapped_column(Integer)
    scheduled_days: Mapped[int | None] = mapped_column(Integer)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    card: Mapped["Card"] = relationship(back_populates="review_logs")


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id"))
    q_type: Mapped[str] = mapped_column(String(10), nullable=False)  # choice/blank/short
    question: Mapped[str] = mapped_column(Text, nullable=False)
    options_json: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    difficulty: Mapped[str] = mapped_column(String(10), default="normal")
    source: Mapped[str] = mapped_column(String(20), default="auto")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    book: Mapped["Book"] = relationship(back_populates="quizzes")
    chapter: Mapped["Chapter | None"] = relationship(back_populates="quizzes")
    attempts: Mapped[list["Attempt"]] = relationship(back_populates="quiz", cascade="all, delete-orphan")


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id"), nullable=False, index=True)
    user_answer: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_self_graded: Mapped[int] = mapped_column(Integer, default=0)
    answered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    quiz: Mapped["Quiz"] = relationship(back_populates="attempts")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class BookAnalysis(Base):
    """书籍智能分析结果（关键信息提取 + 版面统计），一对一。"""

    __tablename__ = "book_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, unique=True, index=True)
    definitions_json: Mapped[str | None] = mapped_column(Text)  # [{term, definition}]
    theorems_json: Mapped[str | None] = mapped_column(Text)     # [{type, number, statement}]
    keywords_json: Mapped[str | None] = mapped_column(Text)     # [str]
    body_size: Mapped[float | None] = mapped_column(Float)       # 正文字号
    header_count: Mapped[int] = mapped_column(Integer, default=0)
    footer_count: Mapped[int] = mapped_column(Integer, default=0)
    table_pages: Mapped[str | None] = mapped_column(Text)        # JSON 页码列表
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
