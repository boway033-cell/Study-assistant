"""后台任务管理器（docs/01-architecture.md §6）

设计：独立后台线程运行事件循环（与 FastAPI 主 loop 解耦）。
- submit 可从任意线程（sync 端点线程池）安全提交
- OCR/解析等重型本地任务串行执行；AI 研读使用独立串行队列，避免被数百页 OCR 阻塞
- 任务状态持久化到 import_tasks 表，重启后自动恢复 pending 任务
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from backend.app.core.database import SessionLocal, engine
from backend.app.models import ImportTask

_task_registry: dict[str, "TaskRecord"] = {}
_queue: asyncio.Queue | None = None
_interactive_queue: asyncio.Queue | None = None
_backend_loop: asyncio.AbstractEventLoop | None = None
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
    _coro: "Callable[[TaskRecord], Awaitable[Any]] | None" = field(default=None, repr=False)


def _release_completed_tasks() -> None:
    """释放已结束任务的闭包，仅在内存保留最近记录；完整历史仍在 SQLite。"""
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
        pass


def _ensure_backend() -> asyncio.AbstractEventLoop:
    """确保后台线程 + 其事件循环已启动并运行。"""
    global _backend_loop, _queue, _interactive_queue, _worker_started
    with _lock:
        if _backend_loop is not None and not _backend_loop.is_closed():
            return _backend_loop

        _backend_loop = asyncio.new_event_loop()
        _queue = asyncio.Queue()
        _interactive_queue = asyncio.Queue()
        _ready = threading.Event()

        def _run():
            asyncio.set_event_loop(_backend_loop)
            _backend_loop.create_task(_worker(_queue))
            _backend_loop.create_task(_worker(_interactive_queue))
            _ready.set()
            _backend_loop.run_forever()

        t = threading.Thread(target=_run, name="task-backend", daemon=True)
        t.start()
        _ready.wait(timeout=5)  # 等待 loop 启动
        _worker_started = True
        return _backend_loop


async def _worker(queue: asyncio.Queue) -> None:
    """单队列 FIFO：每条资源通道一次只执行一个任务。
    
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
                record.result = await record._coro(record)
            if record.cancel_requested:
                raise TaskCancelled("任务已由用户取消")
            record.status = "done"
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
            if record.status in ("done", "failed", "cancelled"):
                record._coro = None
                _release_completed_tasks()
            queue.task_done()


_RESOURCE_HEAVY_TASKS = {"import", "reimport", "toc-rebuild", "deck_render"}


def _queue_for(name: str) -> asyncio.Queue:
    """隔离本地重型解析与交互式 AI，避免超长 OCR 阻塞研究和写作。"""
    queue = _queue if name in _RESOURCE_HEAVY_TASKS else _interactive_queue
    if queue is None:  # pragma: no cover - _ensure_backend 总会先初始化
        raise RuntimeError("任务队列尚未初始化")
    return queue


def _should_retry(exc: Exception) -> bool:
    """仅重试短暂的远程调用故障；解析错误和 OCR 看门狗不能在后台盲目重放。"""
    if exc.__class__.__name__ in {"OCRPageTimeout", "TaskCancelled", "ParseError", "ValueError"}:
        return False
    text = str(exc).lower()
    return any(token in text for token in (
        "timeout", "temporarily", "connection", "429", "rate limit", "503", "502",
    ))


def submit(name: str, coro_factory: Callable[[TaskRecord], Awaitable[Any]],
           book_id: int = 0) -> TaskRecord:
    """提交任务（线程安全）；同一资源通道内串行，不同通道可并行。"""
    loop = _ensure_backend()
    record = TaskRecord(
        id=f"{name}-{uuid.uuid4().hex[:8]}", name=name,
        book_id=book_id, _coro=coro_factory,
    )
    _task_registry[record.id] = record
    _persist(record)
    # 入队后由 _worker 串行 await，避免多任务并发解析/并发 AI 请求
    asyncio.run_coroutine_threadsafe(_queue_for(name).put(record), loop)
    return record


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

_last_persist_time: float = 0.0
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
        raise TaskCancelled("任务已取消；已完成的页面缓存会在下次解析时复用")
    record.progress = progress
    if stage:
        record.stage = stage
    if message:
        record.message = message
    # 定期持久化进度到 DB（避免中断后丢失进度信息）
    global _last_persist_time
    now = _time.time()
    if force or now - _last_persist_time >= _PERSIST_INTERVAL:
        _last_persist_time = now
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
                    row.message = "书籍已就绪或已删除，跳过恢复"
                    continue
                # 重新入队
                from backend.app.worker.import_task import run_import
                record = TaskRecord(
                    id=row.id, name=row.name or "import", book_id=row.book_id, status="pending",
                    _coro=lambda rec, bid=row.book_id: run_import(rec, bid),
                )
                _task_registry[row.id] = record
                loop = _ensure_backend()
                asyncio.run_coroutine_threadsafe(_queue_for(record.name).put(record), loop)
                recovered.append(row.id)
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass
    return recovered
