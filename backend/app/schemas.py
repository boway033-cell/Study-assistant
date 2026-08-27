"""Pydantic 请求/响应模型（对应 docs/03-api.md）"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ---------- 书籍 ----------
class BookListItem(BaseModel):
    id: int
    title: str
    file_type: str
    status: str
    total_pages: int | None = None
    chapter_count: int = 0
    quiz_count: int = 0
    category: str | None = None
    deep_status: str = "none"
    task_message: str | None = None
    authors: str | None = None
    journal: str | None = None
    published_year: int | None = None
    doi: str | None = None
    publication_status: str = "unknown"
    visibility: str = "private"
    demo_allowed: bool = False
    metadata_confidence: float = 0.0
    reading_status: str = "unread"
    favorite: bool = False
    progress_page: int = 1
    shelf_ids: list[int] = []
    created_at: datetime


class BookListResp(BaseModel):
    total: int
    items: list[BookListItem]


class ChapterNode(BaseModel):
    id: int
    title: str
    level: int
    order_index: int
    start_page: int | None = None
    end_page: int | None = None
    children: list["ChapterNode"] = []


class BookDetailResp(BaseModel):
    id: int
    title: str
    file_type: str
    status: str
    total_pages: int | None = None
    error_msg: str | None = None
    chapters: list[ChapterNode] = []
    analysis: "BookAnalysisResp | None" = None
    archive: "PaperProfileResp | None" = None


class PaperProfileResp(BaseModel):
    book_id: int
    authors: str | None = None
    journal: str | None = None
    published_year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    language: str | None = None
    abstract: str | None = None
    source_url: str | None = None
    access_route: str = "local_upload"
    publication_status: str = "unknown"
    visibility: str = "private"
    demo_allowed: bool = False
    metadata_confidence: float = 0.0
    reading_status: str = "unread"
    favorite: bool = False
    rating: float | None = None
    progress_page: int = 1
    last_read_at: datetime | None = None


class PaperProfileUpdateReq(BaseModel):
    authors: str | None = None
    journal: str | None = None
    published_year: int | None = Field(default=None, ge=1000, le=datetime.now().year + 1)
    doi: str | None = None
    arxiv_id: str | None = None
    language: str | None = None
    abstract: str | None = None
    source_url: str | None = None
    access_route: str | None = None
    publication_status: str | None = Field(default=None, pattern="^(unknown|published|preprint|submitted|unpublished)$")
    visibility: str | None = Field(default=None, pattern="^(private|shareable|public)$")
    demo_allowed: bool | None = None
    metadata_confidence: float | None = Field(default=None, ge=0, le=1)
    reading_status: str | None = None
    favorite: bool | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    progress_page: int | None = Field(default=None, ge=1)


class BookAnalysisResp(BaseModel):
    """智能分析结果。"""
    definitions: list[dict] = []
    theorems: list[dict] = []
    keywords: list[str] = []
    body_size: float | None = None
    header_count: int = 0
    footer_count: int = 0
    table_pages: list[int] = []


class BookRenameReq(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class SearchResultItem(BaseModel):
    chunk_id: int
    book_id: int
    book_title: str
    chapter_id: int | None = None
    chapter_title: str | None = None
    page: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    snippet: str


class SearchResp(BaseModel):
    total: int
    items: list[SearchResultItem]


class TaskResp(BaseModel):
    task_id: str
    status: str  # running/done/failed
    progress: float | None = None
    stage: str | None = None
    message: str | None = None
    error: str | None = None
    result: dict | None = None


# ---------- 笔记 ----------
class NoteCreateReq(BaseModel):
    page: int
    content: str
    highlight_json: str | None = None


class NoteUpdateReq(BaseModel):
    content: str | None = None
    highlight_json: str | None = None


class NoteResp(BaseModel):
    id: int
    book_id: int
    chapter_id: int | None = None
    page: int
    content: str
    created_at: datetime


# ---------- 问答 ----------
class ChatReq(BaseModel):
    book_id: int | None = None  # None = 全部书籍
    question: str = Field(min_length=1)
    model: str | None = None  # flash / pro；None = 用设置页默认


class ChatSource(BaseModel):
    chunk_id: int
    book_id: int | None = None
    page: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    book_title: str = ""
    chapter_title: str | None = None
    snippet: str


class ChatHistoryItem(BaseModel):
    id: int
    question: str
    answer: str
    model: str = ""
    sources: list[ChatSource] = []
    created_at: datetime


class ChatHistoryResp(BaseModel):
    total: int
    items: list[ChatHistoryItem]


# ---------- 题目 ----------
class QuizGenReq(BaseModel):
    chapter_ids: list[int] = []
    types: list[str] = ["choice", "blank", "short"]
    count_per_type: int = Field(default=5, ge=1, le=20)


class QuizImportItem(BaseModel):
    chapter_id: int | None = None
    q_type: str
    question: str
    options_json: str | None = None
    answer: str
    explanation: str | None = None


class QuizImportReq(BaseModel):
    quizzes: list[QuizImportItem]


class QuizListItem(BaseModel):
    id: int
    q_type: str
    question: str
    options: list[str] | None = None
    difficulty: str
    book_title: str = ""
    chapter_title: str | None = None


class QuizListResp(BaseModel):
    total: int
    items: list[QuizListItem]


class AttemptReq(BaseModel):
    user_answer: str


class AttemptResp(BaseModel):
    is_correct: bool
    answer: str
    explanation: str | None = None
    correct_rate: float | None = None


class SelfGradeReq(BaseModel):
    is_correct: bool


# ---------- 统计 ----------
class OverviewResp(BaseModel):
    book_count: int
    quiz_count: int
    attempts_total: int
    avg_mastery: float
    streak_days: int


class ChapterMastery(BaseModel):
    chapter_id: int
    title: str
    mastery: float
    quizzes: int
    wrong_rate: float


class MasteryResp(BaseModel):
    book_id: int
    chapters: list[ChapterMastery]


class DailyActivity(BaseModel):
    date: str
    attempts: int


class ActivityResp(BaseModel):
    daily: list[DailyActivity]


class WeaknessItem(BaseModel):
    book_id: int
    book_title: str
    chapter_id: int
    chapter_title: str
    mastery: float
    suggest: str


class WeaknessResp(BaseModel):
    items: list[WeaknessItem]


# ---------- 知识树 ----------
class KnowledgeNodeResp(BaseModel):
    id: int
    parent_id: int | None = None
    title: str
    book_id: int | None = None
    chapter_id: int | None = None
    note: str | None = None
    node_type: str = "concept"
    mastery: str = "unknown"
    ref_node_id: int | None = None
    order_index: int = 0
    children: list["KnowledgeNodeResp"] = []


class KnowledgeTreeResp(BaseModel):
    total: int
    items: list[KnowledgeNodeResp]


class KnowledgeNodeCreateReq(BaseModel):
    parent_id: int | None = None
    title: str = Field(min_length=1, max_length=255)
    book_id: int | None = None
    chapter_id: int | None = None
    note: str | None = None
    node_type: str = "concept"


class KnowledgeNodeUpdateReq(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    note: str | None = None
    book_id: int | None = None
    chapter_id: int | None = None
    node_type: str | None = None
    mastery: str | None = None
    ref_node_id: int | None = None


class KnowledgeBatchDeleteReq(BaseModel):
    node_ids: list[int]


class KnowledgeNodeExpandReq(BaseModel):
    """AI 展开节点：基于节点标题+关联章节生成子节点。"""
    node_id: int


class KnowledgeMoveReq(BaseModel):
    parent_id: int | None = None


class KnowledgeImportReq(BaseModel):
    book_ids: list[int] = Field(min_length=1, max_length=20)
    parent_node_id: int | None = None


class KnowledgeAiGenerateReq(BaseModel):
    book_ids: list[int] = Field(min_length=1, max_length=8)
    parent_node_id: int | None = None


class KnowledgeSourceResp(BaseModel):
    node_id: int
    node_title: str
    book_id: int | None = None
    book_title: str | None = None
    chapter_id: int | None = None
    chapter_title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    text: str = ""


# ---------- PDF 标注 ----------
class AnnotationRect(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class AnnotationSegment(BaseModel):
    page: int = Field(ge=1)
    source: str = Field(default="pdf-text", pattern="^(pdf-text|ocr)$")
    rects: list[AnnotationRect] = Field(min_length=1)


class AnnotationQuote(BaseModel):
    exact: str = ""
    prefix: str = ""
    suffix: str = ""


class AnnotationAnchor(BaseModel):
    schema_version: int = 2
    document_fingerprint: str | None = None
    quote: AnnotationQuote = Field(default_factory=AnnotationQuote)
    segments: list[AnnotationSegment] = Field(min_length=1)


class AnnotationCreateReq(BaseModel):
    page: int = Field(ge=0)  # 0 = 文本标注（docx/pptx 无页码）
    rect_json: str = "[]"  # v1 兼容字段
    anchor: AnnotationAnchor | None = None
    text: str | None = None
    color: str = "#f9e572"
    mark_type: str = Field(default="highlight", pattern="^(highlight|underline)$")
    origin: str = Field(default="user", pattern="^(user|ai)$")
    note: str | None = None
    knowledge_node_id: int | None = None


class AnnotationUpdateReq(BaseModel):
    note: str | None = None
    color: str | None = None
    mark_type: str | None = Field(default=None, pattern="^(highlight|underline)$")
    knowledge_node_id: int | None = None
    page: int | None = Field(default=None, ge=0)
    rect_json: str | None = None
    text: str | None = None
    anchor: AnnotationAnchor | None = None
    status: str | None = Field(default=None, pattern="^(active|needs_reanchor)$")


class AnnotationResp(BaseModel):
    id: int
    book_id: int
    page: int
    rect_json: str
    text: str | None = None
    color: str
    mark_type: str = "highlight"
    origin: str = "user"
    note: str | None = None
    knowledge_node_id: int | None = None
    schema_version: int = 1
    anchor_json: str | None = None
    status: str = "active"
    created_at: datetime


class EvidenceCardCreateReq(BaseModel):
    book_id: int
    chapter_id: int | None = None
    page: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=255)
    evidence_text: str = Field(min_length=1, max_length=12000)
    claim_text: str | None = Field(default=None, max_length=6000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    origin: str = Field(default="user", pattern="^(user|ai)$")
    verification_status: str = Field(default="needs_review", pattern="^(supported|partial|needs_review|unsupported)$")


class EvidenceCardUpdateReq(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    evidence_text: str | None = Field(default=None, min_length=1, max_length=12000)
    claim_text: str | None = Field(default=None, max_length=6000)
    tags: list[str] | None = Field(default=None, max_length=20)
    verification_status: str | None = Field(default=None, pattern="^(supported|partial|needs_review|unsupported)$")


class KnowledgeNoteCreateReq(BaseModel):
    book_id: int
    chapter_id: int | None = None
    page: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(default="", max_length=30000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    origin: str = Field(default="user", pattern="^(user|ai)$")


class KnowledgeNoteUpdateReq(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, max_length=30000)
    tags: list[str] | None = Field(default=None, max_length=20)


# ---------- AI 增强 ----------
class AiExplainReq(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    action: str = "explain"  # explain / translate
    book_title: str = ""
    chapter_title: str = ""


class AiSummaryReq(BaseModel):
    book_id: int
    chapter_id: int


class AiVisionReq(BaseModel):
    book_id: int
    page: int
    image: str  # dataURL (jpeg/png base64)
    prompt: str | None = None


class AiResp(BaseModel):
    ok: bool
    result: str = ""
    error: str = ""


# ---------- 设置 ----------
class SettingsResp(BaseModel):
    deepseek_api_key: str  # 脱敏
    deepseek_model: str    # flash / pro
    vision_api_key: str    # 脱敏（Qwen-VL 视觉分析）
    vision_base_url: str
    vision_model: str
    rag_top_k: str
    vector_search: bool
    deepseek_configured: bool
    vision_configured: bool


class SettingsUpdateReq(BaseModel):
    deepseek_api_key: str | None = None
    deepseek_model: str | None = None  # flash / pro
    vision_api_key: str | None = None
    vision_base_url: str | None = None
    vision_model: str | None = None
    rag_top_k: int | None = None
    vector_search: bool | None = None


class ProbeItem(BaseModel):
    ok: bool
    reason: str | None = None


class ProbeResp(BaseModel):
    deepseek: ProbeItem
    vision: ProbeItem
