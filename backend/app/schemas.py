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
    order_index: int = 0
    children: list["KnowledgeNodeResp"] = []


class KnowledgeTreeResp(BaseModel):
    total: int
    items: list[KnowledgeNodeResp]


class KnowledgeNodeCreateReq(BaseModel):
    parent_id: int | None = None
    title: str = Field(min_length=1, max_length=255)


class KnowledgeNodeUpdateReq(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    note: str | None = None
    book_id: int | None = None
    chapter_id: int | None = None


class KnowledgeMoveReq(BaseModel):
    parent_id: int | None = None


class KnowledgeImportReq(BaseModel):
    book_id: int
    parent_node_id: int | None = None  # None = 新建一棵"《书名》章节骨架"根节点


class KnowledgeAiGenerateReq(BaseModel):
    book_id: int
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
class AnnotationCreateReq(BaseModel):
    page: int = Field(ge=1)
    rect_json: str  # [{x,y,w,h}] 归一化坐标
    text: str | None = None
    color: str = "#f9e572"
    note: str | None = None
    knowledge_node_id: int | None = None


class AnnotationUpdateReq(BaseModel):
    note: str | None = None
    color: str | None = None
    knowledge_node_id: int | None = None


class AnnotationResp(BaseModel):
    id: int
    book_id: int
    page: int
    rect_json: str
    text: str | None = None
    color: str
    note: str | None = None
    knowledge_node_id: int | None = None
    created_at: datetime


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
    vision_model: str
    rag_top_k: str
    vector_search: bool
    deepseek_configured: bool
    vision_configured: bool


class SettingsUpdateReq(BaseModel):
    deepseek_api_key: str | None = None
    deepseek_model: str | None = None  # flash / pro
    vision_api_key: str | None = None
    vision_model: str | None = None
    rag_top_k: int | None = None
    vector_search: bool | None = None


class ProbeItem(BaseModel):
    ok: bool
    reason: str | None = None


class ProbeResp(BaseModel):
    deepseek: ProbeItem
    vision: ProbeItem
