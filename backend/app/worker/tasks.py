"""后台任务管理器（docs/01-architecture.md §6）

设计：独立后台线程运行事件循环（与 FastAPI 主 loop 解耦）。
- submit 可从任意线程（sync 端点线程池）安全提交
- OCR/解析等重型本地任务串行执行；交互与后台 AI 各有有限并发队列
- 任务状态持久化到 import_tasks 表，重启后自动恢复 pending 任务
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy import or_, select, update

from backend.app.core.database import SessionLocal, engine
from backend.app.models import ImportTask
from backend.app.core.config import settings as app_settings

logger = logging.getLogger(__name__)

_task_registry: dict[str, "TaskRecord"] = {}
# registry 由后台 loop 线程与 FastAPI 请求线程共同访问：遍历期间被插入会抛
# "dictionary changed size during iteration"，而该异常发生在 _worker 的 finally 中，
# 会直接杀死 worker 协程并让队列永久停摆。所有访问必须持此锁。
_TASK_REGISTRY_LOCK = threading.RLock()
_queue: asyncio.Queue | None = None
_interactive_queue: asyncio.Queue | None = None
_background_ai_queue: asyncio.Queue | None = None
_backend_loop: asyncio.AbstractEventLoop | None = None
_interactive_loop: asyncio.AbstractEventLoop | None = None
_background_ai_loop: asyncio.AbstractEventLoop | None = None
_worker_started = False
_lock = threading.Lock()
_MAX_COMPLETED_IN_MEMORY = 128


@dataclass
class TaskRecord:
    id: str
    name: str = ""
    book_id: int = 0
    status: str = "pending"  # pending/running/done/failed
    progress: float = 0.0
    stage: str = ""
    message: str = ""
    result: dict | None = None
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 2  # 失败自动重试次数（网络抖动/限流场景）
    cancel_requested: bool = False
    budget_max_tokens: int = 0
    budget_max_calls: int = 0
    _last_persist_at: float = field(default=0.0, repr=False)
    _coro: "Callable[[TaskRecord], Awaitable[Any]] | None" = field(default=None, repr=False)


def _release_completed_tasks() -> None:
    """释放已结束任务的闭包，仅在内存保留最近记录；完整历史仍在 SQLite。"""
    with _TASK_REGISTRY_LOCK:
        completed = [
            task_id for task_id, task in _task_registry.items()
            if task.status in ("done", "failed", "cancelled")
        ]
        for task_id in completed[:-_MAX_COMPLETED_IN_MEMORY]:
            _task_registry.pop(task_id, None)


def _persist(record: TaskRecord) -> None:
    """把 TaskRecord 状态写入 import_tasks 表（best-effort，失败不阻塞）。"""
    try:
        db = SessionLocal()
        try:
            row = db.get(ImportTask, record.id)
            if row is None:
                row = ImportTask(
                    id=record.id, book_id=record.book_id,
                    name=record.name or record.id.split("-")[0],
                    status=record.status, progress=record.progress, stage=record.stage,
                    message=record.message, error=record.error,
                    result_json=json.dumps(record.result, ensure_ascii=False) if record.result else None,
                )
                db.add(row)
            else:
                row.status = record.status
                row.progress = record.progress
                row.stage = record.stage
                row.message = record.message
                row.error = record.error
                row.result_json = json.dumps(record.result, ensure_ascii=False) if record.result else None
                row.retry_count = record.retry_count
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        # 状态落库失败只降级为日志：进度/取消状态丢失必须留痕，否则重启恢复
        # 会静默对不上账（写入仍保持 best-effort，不阻塞任务执行）。
        logger.warning(
            "任务状态持久化失败 task_id=%s status=%s", record.id, record.status, exc_info=True
        )


def _ensure_backend() -> asyncio.AbstractEventLoop:
    """为本地解析与交互 AI 各启动一个事件循环和线程。"""
    global _backend_loop, _interactive_loop, _background_ai_loop
    global _queue, _interactive_queue, _background_ai_queue, _worker_started
    with _lock:
        if (_backend_loop is not None and not _backend_loop.is_closed()
                and _interactive_loop is not None and not _interactive_loop.is_closed()
                and _background_ai_loop is not None and not _background_ai_loop.is_closed()):
            return _backend_loop

        _backend_loop = asyncio.new_event_loop()
        _interactive_loop = asyncio.new_event_loop()
        _background_ai_loop = asyncio.new_event_loop()
        _queue = asyncio.Queue()
        _interactive_queue = asyncio.Queue()
        _background_ai_queue = asyncio.Queue()
        ready = (threading.Event(), threading.Event(), threading.Event())

        def _run(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, signal: threading.Event):
            asyncio.set_event_loop(loop)
            worker_count = (1 if queue is _queue else
                            app_settings.ai_background_workers if queue is _background_ai_queue else
                            app_settings.ai_interactive_workers)
            for _ in range(worker_count):
                loop.create_task(_worker(queue))
            signal.set()
            loop.run_forever()

        threading.Thread(target=_run, args=(_backend_loop, _queue, ready[0]),
                         name="task-parse", daemon=True).start()
        threading.Thread(target=_run, args=(_interactive_loop, _interactive_queue, ready[1]),
                         name="task-ai", daemon=True).start()
        threading.Thread(target=_run, args=(_background_ai_loop, _background_ai_queue, ready[2]),
                         name="task-ai-background", daemon=True).start()
        if not all(signal.wait(timeout=5) for signal in ready):
            raise RuntimeError("后台任务线程未能启动")
        _worker_started = True
        return _backend_loop


async def _worker(queue: asyncio.Queue) -> None:
    """队列 FIFO 分发；AI 通道允许多个任务在网络等待期间并行。
    
    失败自动重试：网络抖动/限流场景下重试 max_retries 次，每次间隔递增。
    """
    while True:
        record: TaskRecord = await queue.get()
        try:
            if record.cancel_requested or record.status == "cancelled":
                raise TaskCancelled("任务已由用户取消")
            record.status = "running"
            _persist(record)
            if record._coro is not None:
                if record.name in _RESOURCE_HEAVY_TASKS:
                    record.result = await record._coro(record)
                else:
                    from backend.app.services.llm.budget import current_budget, load_default_budget
                    budget = load_default_budget()
                    if record.budget_max_tokens:
                        budget.max_tokens = record.budget_max_tokens
                    if record.budget_max_calls:
                        budget.max_calls = record.budget_max_calls
                    token = current_budget.set(budget)
                    try:
                        record.result = await record._coro(record)
                    finally:
                        current_budget.reset(token)
            if record.cancel_requested:
                raise TaskCancelled("任务已由用户取消")
            record.status = "done"
            record.progress = 1.0
            record.stage = "done"
            record.message = "已完成"
            _persist(record)
        except TaskCancelled as e:
            record.status = "cancelled"
            record.message = str(e)
            record.error = None
            _persist(record)
        except Exception as e:  # noqa: BLE001
            if record.retry_count < record.max_retries and _should_retry(e):
                # 自动重试：间隔递增（3s, 6s, 9s...）
                record.retry_count += 1
                record.status = "pending"
                record.error = f"重试中({record.retry_count}/{record.max_retries}): {e}"
                _persist(record)
                import asyncio as _aio
                await _aio.sleep(3 * record.retry_count)
                # 重新入队
                await queue.put(record)
            else:
                record.status = "failed"
                record.error = str(e)
                _persist(record)
        finally:
            try:
                if record.status in ("done", "failed", "cancelled"):
                    record._coro = None
                    _release_completed_tasks()
            except Exception:  # noqa: BLE001
                # 释放内存失败绝不能传播：异常从这里逃逸会终止 worker 协程，
                # 之后提交的任务永远停在 pending 且无自愈。
                pass
            queue.task_done()


_RESOURCE_HEAVY_TASKS = {"import", "reimport", "toc-rebuild", "deck_render"}
_BACKGROUND_AI_TASKS = {"assistant_reading", "research_concept", "research_alignment", "research_counter", "research_reading_plan", "research_coach_question", "research_coach_feedback"}


def _queue_for(name: str) -> asyncio.Queue:
    """隔离本地重型解析与交互式 AI，避免超长 OCR 阻塞研究和写作。"""
    queue = (_queue if name in _RESOURCE_HEAVY_TASKS else
             _background_ai_queue if name in _BACKGROUND_AI_TASKS else _interactive_queue)
    if queue is None:  # pragma: no cover - _ensure_backend 总会先初始化
        raise RuntimeError("任务队列尚未初始化")
    return queue


def _loop_for(name: str) -> asyncio.AbstractEventLoop:
    loop = (_backend_loop if name in _RESOURCE_HEAVY_TASKS else
            _background_ai_loop if name in _BACKGROUND_AI_TASKS else _interactive_loop)
    if loop is None or loop.is_closed():
        raise RuntimeError("任务事件循环尚未初始化")
    return loop


def _should_retry(exc: Exception) -> bool:
    """仅重试短暂的远程调用故障；解析错误和 OCR 看门狗不能在后台盲目重放。"""
    if exc.__class__.__name__ in {"OCRPageTimeout", "TaskCancelled", "ParseError", "ValueError"}:
        return False
    text = str(exc).lower()
    if any(token in text for token in ("certificate verify failed", "sslcertverificationerror",
                                      "http 400", "http 401", "http 403", "invalid_api_key")):
        return False
    return any(token in text for token in (
        "timeout", "temporarily", "connection", "429", "rate limit", "503", "502",
    ))


def submit(name: str, coro_factory: Callable[[TaskRecord], Awaitable[Any]],
           book_id: int = 0, budget_max_tokens: int = 0,
           budget_max_calls: int = 0) -> TaskRecord:
    """提交任务（线程安全）；解析串行，AI 队列按配置有限并发。"""
    _ensure_backend()
    record = TaskRecord(
        id=f"{name}-{uuid.uuid4().hex[:8]}", name=name,
        book_id=book_id, _coro=coro_factory,
        budget_max_tokens=budget_max_tokens, budget_max_calls=budget_max_calls,
    )
    with _TASK_REGISTRY_LOCK:
        _task_registry[record.id] = record
    _persist(record)
    # 入队后由对应资源通道的 worker 消费；同一模型另有全局并发上限。
    asyncio.run_coroutine_threadsafe(_queue_for(name).put(record), _loop_for(name))
    return record


class DuplicateTaskError(RuntimeError):
    """An active task already owns this book and work type."""


def submit_unique_batch(
    name: str,
    jobs: list[tuple[int, Callable[[TaskRecord], Awaitable[Any]]]],
    *,
    conflicting_names: tuple[str, ...] = (),
    budget_max_tokens: int = 0,
    budget_max_calls: int = 0,
    initial_result: dict | None = None,
) -> list[TaskRecord]:
    """Reserve all book jobs atomically before persisting or queueing any of them."""
    _ensure_backend()
    if len({book_id for book_id, _ in jobs}) != len(jobs):
        raise DuplicateTaskError("同一批次不能重复提交同一资料")
    names = {name, *conflicting_names}
    with _TASK_REGISTRY_LOCK:
        active = {task.book_id for task in _task_registry.values()
                  if task.name in names and task.status in {"pending", "running", "cancelling"}}
        duplicate = next((book_id for book_id, _ in jobs if book_id in active), None)
        if duplicate is not None:
            raise DuplicateTaskError(f"资料 {duplicate} 已有同类任务在排队或运行")
        records = [TaskRecord(id=f"{name}-{uuid.uuid4().hex[:8]}", name=name,
                              book_id=book_id, _coro=factory,
                              budget_max_tokens=budget_max_tokens,
                              budget_max_calls=budget_max_calls,
                              result=initial_result.copy() if initial_result else None)
                   for book_id, factory in jobs]
        for record in records:
            _task_registry[record.id] = record
    for record in records:
        _persist(record)
        asyncio.run_coroutine_threadsafe(_queue_for(name).put(record), _loop_for(name))
    return records


def submit_unique(name: str, coro_factory: Callable[[TaskRecord], Awaitable[Any]],
                  book_id: int, *, conflicting_names: tuple[str, ...] = (),
                  budget_max_tokens: int = 0, budget_max_calls: int = 0,
                  initial_result: dict | None = None) -> TaskRecord:
    return submit_unique_batch(name, [(book_id, coro_factory)],
                               conflicting_names=conflicting_names,
                               budget_max_tokens=budget_max_tokens,
                               budget_max_calls=budget_max_calls,
                               initial_result=initial_result)[0]


def has_active_task(name: str, book_id: int) -> bool:
    """同一书籍是否已有同名任务在排队或运行中（已结束/已取消的不算）。

    重复任务可能并发写同一份资料，也会重复计费；提交入口必须拦截。
    这里用于在提交入口提前告知用户，而不是让他们等跑完才发现重复。
    """
    with _TASK_REGISTRY_LOCK:
        return any(task.name == name and task.book_id == book_id
                   and task.status in {"pending", "running", "cancelling"}
                   for task in _task_registry.values())


def get_task(task_id: str) -> TaskRecord | None:
    """获取任务状态。优先从内存 registry 取，无则从 DB 恢复。"""
    record = _task_registry.get(task_id)
    if record is not None:
        return record
    # 从 DB 恢复（跨进程/重启场景）
    try:
        db = SessionLocal()
        try:
            row = db.get(ImportTask, task_id)
            if row is None:
                return None
            return TaskRecord(
                id=row.id, name=row.name or row.id.split("-")[0],
                book_id=row.book_id, status=row.status,
                progress=row.progress, stage=row.stage, message=row.message,
                error=row.error, result=json.loads(row.result_json) if row.result_json else None,
            )
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return None


def list_tasks() -> list[TaskRecord]:
    with _TASK_REGISTRY_LOCK:
        return list(_task_registry.values())


class TaskCancelled(RuntimeError):
    """由用户取消的任务；不触发自动重试。"""


def cancel_task(task_id: str) -> TaskRecord | None:
    """请求取消排队中或运行中的任务，并持久化取消状态。"""
    record = _task_registry.get(task_id)
    managed_in_process = record is not None
    if record is None:
        record = get_task(task_id)
        if record is None:
            return None
        with _TASK_REGISTRY_LOCK:
            _task_registry[task_id] = record
    if record.status in ("done", "failed", "cancelled"):
        return record
    record.cancel_requested = True
    record.status = "cancelling" if managed_in_process and record.status == "running" else "cancelled"
    record.message = "正在安全停止，已完成的页面缓存会保留" if record.status == "cancelling" else "任务已取消"
    _persist(record)
    return record


def retry_task(task_id: str) -> TaskRecord:
    """为失败/取消的导入任务创建一次显式重试；不自动重放 AI 写入任务。"""
    from backend.app.models import Book
    from backend.app.worker.import_task import run_import

    db = SessionLocal()
    try:
        row = db.get(ImportTask, task_id)
        if row is None:
            raise ValueError("任务不存在")
        if row.status not in ("failed", "cancelled"):
            raise ValueError("只有失败或已取消的任务可以重试")
        if row.name not in ("import", "reimport"):
            raise ValueError("该 AI 生成任务不能自动重放，请回到原工作区重新提交")
        book = db.get(Book, row.book_id)
        if book is None:
            raise ValueError("原资料已不存在，无法重试")
        book.status = "pending"
        book.error_msg = None
        db.commit()
        book_id = book.id
    finally:
        db.close()
    return submit("reimport", lambda record: run_import(record, book_id), book_id=book_id)


import time as _time

_PERSIST_INTERVAL = 5.0  # 每 5 秒最多持久化一次进度（避免频繁 DB 写入）


def update_progress(
    record: TaskRecord,
    progress: float,
    stage: str = "",
    message: str = "",
    *,
    force: bool = False,
) -> None:
    if record.cancel_requested:
        # 取消提示要贴合任务类型。这条早期只为 PDF 解析/OCR 而写，
        # 现在研读、报告、写作 DNA、PPT 等任务共用同一函数，
        # 不能一律告诉用户"页面缓存会复用"。
        if record.name in {"import", "reimport"}:
            raise TaskCancelled("任务已取消；已完成的页面缓存会在下次解析时复用")
        raise TaskCancelled("任务已取消；已完成的部分结果会保留，可稍后继续")
    record.progress = progress
    if stage:
        record.stage = stage
    if message:
        record.message = message
    # 定期持久化进度到 DB（避免中断后丢失进度信息）
    now = _time.time()
    if force or now - record._last_persist_at >= _PERSIST_INTERVAL:
        record._last_persist_at = now
        _persist(record)


def recover_pending_tasks() -> list[str]:
    """应用启动时调用：扫描 import_tasks 表中 status=pending 的任务，重新入队。
    
    返回恢复的 task_id 列表。running 状态的任务已被 _migrate 复位为 pending。
    需要 book 仍存在且 status != ready 才有意义重新导入。
    """
    from backend.app.models import Book

    recovered: list[str] = []
    try:
        db = SessionLocal()
        try:
            rows = db.scalars(
                select(ImportTask).where(ImportTask.status == "pending").order_by(ImportTask.created_at)
            ).all()
            for row in rows:
                if row.name not in ("import", "reimport"):
                    row.status = "failed"
                    row.error = "服务重启，非导入任务需由用户重新提交"
                    continue
                book = db.get(Book, row.book_id)
                if book is None or book.status == "ready":
                    # 书已删或已完成，标记任务完成
                    row.status = "done"
                    row.progress = 1.0
                    row.stage = "done"
                    row.message = "书籍已就绪或已删除，跳过恢复"
                    continue
                # 重新入队
                from backend.app.worker.import_task import run_import
                record = TaskRecord(
                    id=row.id, name=row.name or "import", book_id=row.book_id, status="pending",
                    _coro=lambda rec, bid=row.book_id: run_import(rec, bid),
                )
                with _TASK_REGISTRY_LOCK:
                    _task_registry[row.id] = record
                _ensure_backend()
                asyncio.run_coroutine_threadsafe(_queue_for(record.name).put(record), _loop_for(record.name))
                recovered.append(row.id)
            # Legacy versions sometimes persisted terminal tasks at 94%.
            # Normalize them on startup so task history and the live API agree.
            db.execute(
                update(ImportTask)
                .where(ImportTask.status == "done")
                .where(or_(ImportTask.progress.is_(None), ImportTask.progress != 1.0,
                           ImportTask.stage.is_(None), ImportTask.stage != "done"))
                .values(progress=1.0, stage="done")
            )
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass
    return recovered
