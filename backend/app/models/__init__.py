"""ORM 模型：books / chapters / chunks / notes / chat_logs / quizzes / attempts / knowledge_nodes / settings
对应 docs/02-database.md。卡片学习已取消，无 cards / review_logs 表。"""
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
    category: Mapped[str | None] = mapped_column(String(50))  # AI 自动分类（数学/管理学/…）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    chapters: Mapped[list["Chapter"]] = relationship(back_populates="book", cascade="all, delete-orphan")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="book", cascade="all, delete-orphan")
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
    mode: Mapped[str] = mapped_column(String(20), nullable=False)  # deepseek
    model_name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


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


class KnowledgeNode(Base):
    """知识树节点：用户自主搭建的知识结构，可关联书籍章节以便右侧展示原文。"""

    __tablename__ = "knowledge_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_nodes.id"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id"))
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id"))
    note: Mapped[str | None] = mapped_column(Text)  # 用户笔记/总结（Markdown）
    ref_node_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_nodes.id"))  # 跨树引用
    node_type: Mapped[str] = mapped_column(String(20), default="concept")  # concept/theorem/point/example/question
    mastery: Mapped[str] = mapped_column(String(10), default="unknown")  # unknown/known/fuzzy/unknown 掌握度
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    parent: Mapped["KnowledgeNode | None"] = relationship(
        foreign_keys="KnowledgeNode.parent_id",
        remote_side="KnowledgeNode.id",
        back_populates="children",
    )
    children: Mapped[list["KnowledgeNode"]] = relationship(
        foreign_keys="KnowledgeNode.parent_id",
        back_populates="parent",
    )


class Annotation(Base):
    """PDF 阅读器标注：高亮选区 + 笔记，可关联知识树节点。"""

    __tablename__ = "annotations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    rect_json: Mapped[str] = mapped_column(Text, nullable=False)  # [{x,y,w,h} 归一化坐标]
    text: Mapped[str | None] = mapped_column(Text)                # 选中的原文
    color: Mapped[str] = mapped_column(String(20), default="#f9e572")
    note: Mapped[str | None] = mapped_column(Text)                # 用户笔记
    knowledge_node_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_nodes.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    book: Mapped["Book"] = relationship()
    knowledge_node: Mapped["KnowledgeNode | None"] = relationship()


class BookDeep(Base):
    """深度分析产物：三级标题目录 + AI 逐章总结 + Markdown 转换。"""

    __tablename__ = "book_deep"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, unique=True, index=True)
    toc_json: Mapped[str | None] = mapped_column(Text)        # 完整三级标题目录 [{title,level,page}]
    summaries_json: Mapped[str | None] = mapped_column(Text)  # [{title, summary}]
    markdown: Mapped[str | None] = mapped_column(Text)        # Markdown 版本
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/done/failed
    error_msg: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class StudyReport(Base):
    """AI 综合阅读报告。"""

    __tablename__ = "study_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_ids_json: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


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
