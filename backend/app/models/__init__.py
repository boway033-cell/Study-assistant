"""ORM 模型：books / chapters / chunks / notes / chat_logs / quizzes / attempts / knowledge_nodes /
settings / book_analysis / book_deep / study_reports / tags / import_tasks
对应 docs/02-database.md。卡片学习已取消，无 cards / review_logs 表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base

# 书籍-标签 多对多关联表（需在 Book / Tag 之前定义）
book_tags = Table(
    "book_tags", Base.metadata,
    Column("book_id", ForeignKey("books.id"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id"), primary_key=True),
)

# 书架是虚拟集合：同一文献可加入多个书架，不复制或移动原文件。
shelf_books = Table(
    "shelf_books", Base.metadata,
    Column("shelf_id", ForeignKey("shelves.id", ondelete="CASCADE"), primary_key=True),
    Column("book_id", ForeignKey("books.id", ondelete="CASCADE"), primary_key=True),
    Column("order_index", Integer, nullable=False, default=0),
)


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)  # pdf/docx/pptx
    file_size: Mapped[int | None] = mapped_column(Integer)
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)  # SHA-256，用于去重
    total_pages: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending/parsing/ready/failed/needs_ocr
    error_msg: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(50))  # AI 自动分类（数学/管理学/…）
    duplicate_of: Mapped[int | None] = mapped_column(Integer)  # 疑似重复的 book_id（0=无）
    library_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 全库自定义顺序
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    chapters: Mapped[list["Chapter"]] = relationship(back_populates="book", cascade="all, delete-orphan")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="book", cascade="all, delete-orphan")
    quizzes: Mapped[list["Quiz"]] = relationship(back_populates="book", cascade="all, delete-orphan")
    tags: Mapped[list["Tag"]] = relationship(secondary=book_tags, back_populates="books")
    shelves: Mapped[list["Shelf"]] = relationship(secondary=shelf_books, back_populates="books")


class PaperProfile(Base):
    """文献归档档案：书目信息、来源可追溯性与阅读状态。"""

    __tablename__ = "paper_profiles"

    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), primary_key=True)
    authors: Mapped[str | None] = mapped_column(Text)
    journal: Mapped[str | None] = mapped_column(String(255))
    published_year: Mapped[int | None] = mapped_column(Integer)
    doi: Mapped[str | None] = mapped_column(String(255), index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(100), index=True)
    language: Mapped[str | None] = mapped_column(String(20))
    abstract: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    access_route: Mapped[str] = mapped_column(String(50), default="local_upload")
    publication_status: Mapped[str] = mapped_column(String(24), default="unknown")
    visibility: Mapped[str] = mapped_column(String(20), default="private")
    demo_allowed: Mapped[int] = mapped_column(Integer, default=0)
    metadata_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    provenance_json: Mapped[str | None] = mapped_column(Text)
    source_map_json: Mapped[str | None] = mapped_column(Text)
    reading_status: Mapped[str] = mapped_column(String(20), default="unread")  # unread/reading/read
    favorite: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[float | None] = mapped_column(Float)
    progress_page: Mapped[int] = mapped_column(Integer, default=1)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


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


class TocRevision(Base):
    """目录人工/自动修订快照，用于追溯和撤销。"""

    __tablename__ = "toc_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    note: Mapped[str | None] = mapped_column(String(255))
    before_json: Mapped[str] = mapped_column(Text, nullable=False)
    after_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


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
    mark_type: Mapped[str] = mapped_column(String(20), default="highlight", nullable=False)
    origin: Mapped[str] = mapped_column(String(20), default="user", nullable=False)
    note: Mapped[str | None] = mapped_column(Text)                # 用户笔记
    knowledge_node_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_nodes.id"))
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    anchor_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    book: Mapped["Book"] = relationship()
    knowledge_node: Mapped["KnowledgeNode | None"] = relationship()


class EvidenceCard(Base):
    """独立证据卡片；与 PDF 标注、知识树节点分表保存，只在知识沉淀首页聚合。"""

    __tablename__ = "evidence_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"), index=True)
    page: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_text: Mapped[str | None] = mapped_column(Text)
    source_ref_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    source_report_id: Mapped[int | None] = mapped_column(Integer, index=True)
    source_scope_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    origin: Mapped[str] = mapped_column(String(20), default="user", nullable=False)
    verification_status: Mapped[str] = mapped_column(String(24), default="needs_review", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class KnowledgeNote(Base):
    """普通知识笔记；不以知识树节点或 PDF 标注冒充笔记。"""

    __tablename__ = "knowledge_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"), index=True)
    page: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_annotation_id: Mapped[int | None] = mapped_column(ForeignKey("annotations.id", ondelete="SET NULL"), unique=True)
    source_legacy_node_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    source_report_id: Mapped[int | None] = mapped_column(Integer, index=True)
    source_scope_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    source_refs_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    origin: Mapped[str] = mapped_column(String(20), default="user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class BookDeep(Base):
    """深度分析产物：三级标题目录 + AI 逐章总结 + Markdown 转换。"""

    __tablename__ = "book_deep"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, unique=True, index=True)
    toc_json: Mapped[str | None] = mapped_column(Text)        # 完整三级标题目录 [{title,level,page}]
    summaries_json: Mapped[str | None] = mapped_column(Text)  # [{title, summary}]
    markdown: Mapped[str | None] = mapped_column(Text)        # Markdown 版本
    paper_card: Mapped[str | None] = mapped_column(Text)      # 01-16 节证据型阅读卡
    card_audit_json: Mapped[str | None] = mapped_column(Text) # 阅读卡结构/来源审计
    chapter_hashes_json: Mapped[str | None] = mapped_column(Text)  # 各章内容哈希（增量缓存用）
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/done/failed
    error_msg: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class StudyReport(Base):
    """AI 综合阅读报告。"""

    __tablename__ = "study_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_ids_json: Mapped[str | None] = mapped_column(Text)
    selection_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    focus: Mapped[str | None] = mapped_column(Text)
    framework: Mapped[str | None] = mapped_column(Text)
    claims_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
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


class Tag(Base):
    """资料标签：多对多关联书籍，支持自动/手动标签。"""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    color: Mapped[str] = mapped_column(String(20), default="#8B5A2B")
    auto_generated: Mapped[int] = mapped_column(Integer, default=0)  # 1=自动生成
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    books: Mapped[list["Book"]] = relationship(secondary=book_tags, back_populates="tags")


class Shelf(Base):
    """层级虚拟书架；删除书架只删除归属关系，不删除文献。"""

    __tablename__ = "shelves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("shelves.id", ondelete="CASCADE"), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(20), default="#8B5A2B")
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    books: Mapped[list["Book"]] = relationship(secondary=shelf_books, back_populates="shelves")
    children: Mapped[list["Shelf"]] = relationship(cascade="all, delete-orphan")


class LiteratureResource(Base):
    """正文、SI、图表或数据集的来源与权利元数据。"""

    __tablename__ = "literature_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    resource_book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"), index=True)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="main")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    license_expression: Mapped[str | None] = mapped_column(String(120))
    rights_statement_uri: Mapped[str | None] = mapped_column(Text)
    rights_status: Mapped[str] = mapped_column(String(30), default="not_evaluated")
    rights_holder: Mapped[str | None] = mapped_column(String(255))
    attribution: Mapped[str | None] = mapped_column(Text)
    permission_note: Mapped[str | None] = mapped_column(Text)
    allow_reuse: Mapped[int] = mapped_column(Integer, default=0)
    provenance_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ImportTask(Base):
    """导入任务持久化记录（断点恢复用）。"""

    __tablename__ = "import_tasks"

    id: Mapped[str] = mapped_column(String(60), primary_key=True)  # 同 TaskRecord.id
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)  # import / reimport
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending/running/done/failed
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str] = mapped_column(String(30), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class PresentationDeck(Base):
    """由指定章节/选段生成的可编辑文献汇报。"""

    __tablename__ = "presentation_decks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    paper_type: Mapped[str | None] = mapped_column(String(30))
    selection_json: Mapped[str | None] = mapped_column(Text)
    options_json: Mapped[str | None] = mapped_column(Text)
    outline_json: Mapped[str | None] = mapped_column(Text)
    manifest_json: Mapped[str | None] = mapped_column(Text)
    qa_json: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(String(500))
    error_msg: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class LiteratureAccessAttempt(Base):
    """合法全文获取记录；清单只保存来源与校验信息，不保存凭据。"""

    __tablename__ = "literature_access_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    include_si: Mapped[int | None] = mapped_column(Integer)
    route: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    manifest_json: Mapped[str | None] = mapped_column(Text)
    book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id"), index=True)
    error_msg: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class WritingDnaProfile(Base):
    """可持续完善的作者/账号写作 DNA。原文仍由 Book 管理，此表只保存范围与版本。"""

    __tablename__ = "writing_dna_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    target_author: Mapped[str | None] = mapped_column(String(120))
    book_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    corpus_manifest_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rights_acknowledged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feedback: Mapped[str | None] = mapped_column(Text)
    error_msg: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class WritingDnaRevision(Base):
    """每次蒸馏保留不可变版本，便于比较、完善和回退。"""

    __tablename__ = "writing_dna_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("writing_dna_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    language_dna: Mapped[str] = mapped_column(Text, nullable=False, default="")
    structure_patterns: Mapped[str] = mapped_column(Text, nullable=False, default="")
    logic_dna: Mapped[str | None] = mapped_column(Text)  # v2 新增：逻辑结构考察层；旧版本为 NULL
    cognitive_framework: Mapped[str] = mapped_column(Text, nullable=False, default="")
    visual_style_guide: Mapped[str] = mapped_column(Text, nullable=False, default="")
    writing_dna: Mapped[str] = mapped_column(Text, nullable=False, default="")
    quality_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    feedback: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class WritingOutput(Base):
    """DNA 仿写或白名单去 AI 味输出。"""

    __tablename__ = "writing_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("writing_dna_profiles.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # imitation / ai_tone
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    input_type: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    source_text: Mapped[str | None] = mapped_column(Text)
    output_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_file_path: Mapped[str | None] = mapped_column(String(500))
    output_file_path: Mapped[str | None] = mapped_column(String(500))
    audit_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
