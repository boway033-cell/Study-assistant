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
    card_count: int = 0
    quiz_count: int = 0
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
    mode: str = "auto"  # auto/local/cloud


class ChatSource(BaseModel):
    chunk_id: int
    page: int | None = None
    snippet: str


class ChatHistoryItem(BaseModel):
    id: int
    question: str
    answer: str
    mode: str
    sources: list[ChatSource] = []
    created_at: datetime


class ChatHistoryResp(BaseModel):
    total: int
    items: list[ChatHistoryItem]


# ---------- 卡片 ----------
class CardGenReq(BaseModel):
    chapter_ids: list[int] = []
    max_per_chapter: int = Field(default=20, ge=1, le=100)


class ReviewQueueItem(BaseModel):
    id: int
    front: str
    back: str
    state: str
    due: datetime
    book_title: str
    chapter_title: str | None = None


class ReviewQueueResp(BaseModel):
    due_count: int
    new_count: int
    items: list[ReviewQueueItem]


class ReviewReq(BaseModel):
    rating: str  # again/hard/good/easy


class ReviewResp(BaseModel):
    card_id: int
    state: str
    stability: float
    difficulty: float
    due: datetime
    scheduled_days: int


class CardCreateReq(BaseModel):
    book_id: int
    chapter_id: int | None = None
    front: str
    back: str
    tags: str | None = None


class CardUpdateReq(BaseModel):
    front: str | None = None
    back: str | None = None
    tags: str | None = None


class CardResp(BaseModel):
    id: int
    book_id: int
    chapter_id: int | None = None
    front: str
    back: str
    tags: str | None = None
    state: str
    due: datetime
    reps: int
    lapses: int
    book_title: str = ""
    chapter_title: str | None = None


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
    card_count: int
    due_today: int
    reviews_done: int
    quiz_count: int
    avg_mastery: float
    streak_days: int


class ChapterMastery(BaseModel):
    chapter_id: int
    title: str
    mastery: float
    cards: int
    due: int
    wrong_rate: float


class MasteryResp(BaseModel):
    book_id: int
    chapters: list[ChapterMastery]


class DailyReview(BaseModel):
    date: str
    reviews: int
    new_cards: int
    due: int


class ReviewHistoryResp(BaseModel):
    daily: list[DailyReview]


class WeaknessItem(BaseModel):
    book_id: int
    book_title: str
    chapter_id: int
    chapter_title: str
    mastery: float
    suggest: str


class WeaknessResp(BaseModel):
    items: list[WeaknessItem]


# ---------- 设置 ----------
class SettingsResp(BaseModel):
    llm_mode: str
    deepseek_api_key: str  # 脱敏
    ollama_model: str
    daily_new_cards: str
    rag_top_k: str
    vector_search: bool
    ollama_connected: bool
    deepseek_configured: bool


class SettingsUpdateReq(BaseModel):
    llm_mode: str | None = None
    deepseek_api_key: str | None = None
    daily_new_cards: int | None = None
    rag_top_k: int | None = None
    vector_search: bool | None = None
    ollama_model: str | None = None


class ProbeItem(BaseModel):
    ok: bool
    reason: str | None = None


class ProbeResp(BaseModel):
    ollama: ProbeItem
    deepseek: ProbeItem
