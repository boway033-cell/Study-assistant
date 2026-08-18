"""后台任务管理器（docs/01-architecture.md §6）

设计：独立后台线程运行事件循环（与 FastAPI 主 loop 解耦）。
- submit 可从任意线程（sync 端点线程池）安全提交
- 长任务串行执行（FIFO 队列），避免并发解析耗尽内存
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
_backend_loop: asyncio.AbstractEventLoop | None = None
_worker_started = False
_lock = threading.Lock()


@dataclass
class TaskRecord:
    id: str
    book_id: int = 0
    status: str = "pending"  # pending/running/done/failed
    progress: float = 0.0
    stage: str = ""
    message: str = ""
    result: dict | None = None
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 2  # 失败自动重试次数（网络抖动/限流场景）
    _coro: "Callable[[TaskRecord], Awaitable[Any]] | None" = field(default=None, repr=False)


def _persist(record: TaskRecord) -> None:
    """把 TaskRecord 状态写入 import_tasks 表（best-effort，失败不阻塞）。"""
    try:
        db = SessionLocal()
        try:
            row = db.get(ImportTask, record.id)
            if row is None:
                row = ImportTask(
                    id=record.id, book_id=record.book_id, name=record.id.split("-")[0],
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
    global _backend_loop, _queue, _worker_started
    with _lock:
        if _backend_loop is not None and not _backend_loop.is_closed():
            return _backend_loop

        _backend_loop = asyncio.new_event_loop()
        _queue = asyncio.Queue()
        _ready = threading.Event()

        def _run():
            asyncio.set_event_loop(_backend_loop)
            _backend_loop.create_task(_worker())
            _ready.set()
            _backend_loop.run_forever()

        t = threading.Thread(target=_run, name="task-backend", daemon=True)
        t.start()
        _ready.wait(timeout=5)  # 等待 loop 启动
        _worker_started = True
        return _backend_loop


async def _worker() -> None:
    """单协程 FIFO：一次只执行一个任务，await 完成后再取下一个（真正串行）。
    
    失败自动重试：网络抖动/限流场景下重试 max_retries 次，每次间隔递增。
    """
    while True:
        record: TaskRecord = await _queue.get()
        try:
            record.status = "running"
            _persist(record)
            if record._coro is not None:
                record.result = await record._coro(record)
            record.status = "done"
            _persist(record)
        except Exception as e:  # noqa: BLE001
            if record.retry_count < record.max_retries:
                # 自动重试：间隔递增（3s, 6s, 9s...）
                record.retry_count += 1
                record.status = "pending"
                record.error = f"重试中({record.retry_count}/{record.max_retries}): {e}"
                _persist(record)
                import asyncio as _aio
                await _aio.sleep(3 * record.retry_count)
                # 重新入队
                await _queue.put(record)
            else:
                record.status = "failed"
                record.error = str(e)
                _persist(record)
        finally:
            _queue.task_done()


def submit(name: str, coro_factory: Callable[[TaskRecord], Awaitable[Any]],
           book_id: int = 0) -> TaskRecord:
    """提交任务（线程安全）。任务入队后由后台 worker 串行执行（FIFO），不并发。"""
    loop = _ensure_backend()
    record = TaskRecord(id=f"{name}-{uuid.uuid4().hex[:8]}", book_id=book_id, _coro=coro_factory)
    _task_registry[record.id] = record
    _persist(record)
    # 入队后由 _worker 串行 await，避免多任务并发解析/并发 AI 请求
    asyncio.run_coroutine_threadsafe(_queue.put(record), loop)
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
                id=row.id, book_id=row.book_id, status=row.status,
                progress=row.progress, stage=row.stage, message=row.message,
                error=row.error, result=json.loads(row.result_json) if row.result_json else None,
            )
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return None


def list_tasks() -> list[TaskRecord]:
    return list(_task_registry.values())


def update_progress(record: TaskRecord, progress: float, stage: str = "", message: str = "") -> None:
    record.progress = progress
    if stage:
        record.stage = stage
    if message:
        record.message = message


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
                book = db.get(Book, row.book_id)
                if book is None or book.status == "ready":
                    # 书已删或已完成，标记任务完成
                    row.status = "done"
                    row.message = "书籍已就绪或已删除，跳过恢复"
                    continue
                # 重新入队
                from backend.app.worker.import_task import run_import
                record = TaskRecord(
                    id=row.id, book_id=row.book_id, status="pending",
                    _coro=lambda rec, bid=row.book_id: run_import(rec, bid),
                )
                _task_registry[row.id] = record
                loop = _ensure_backend()
                asyncio.run_coroutine_threadsafe(_queue.put(record), loop)
                recovered.append(row.id)
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass
    return recovered