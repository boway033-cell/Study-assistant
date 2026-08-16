"""后台任务管理器（docs/01-architecture.md §6）

设计：独立后台线程运行事件循环（与 FastAPI 主 loop 解耦）。
- submit 可从任意线程（sync 端点线程池）安全提交
- 长任务串行执行（FIFO 队列），避免并发解析耗尽内存
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

_task_registry: dict[str, "TaskRecord"] = {}
_queue: asyncio.Queue | None = None
_backend_loop: asyncio.AbstractEventLoop | None = None
_worker_started = False
_lock = threading.Lock()


@dataclass
class TaskRecord:
    id: str
    status: str = "pending"  # pending/running/done/failed
    progress: float = 0.0
    stage: str = ""
    message: str = ""
    result: dict | None = None
    error: str | None = None
    _coro: "Callable[[TaskRecord], Awaitable[Any]] | None" = field(default=None, repr=False)


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
    """单协程 FIFO：一次只执行一个任务，await 完成后再取下一个（真正串行）。"""
    while True:
        record: TaskRecord = await _queue.get()
        try:
            record.status = "running"
            if record._coro is not None:
                record.result = await record._coro(record)
            record.status = "done"
        except Exception as e:  # noqa: BLE001
            record.status = "failed"
            record.error = str(e)
        finally:
            _queue.task_done()


def submit(name: str, coro_factory: Callable[[TaskRecord], Awaitable[Any]]) -> TaskRecord:
    """提交任务（线程安全）。任务入队后由后台 worker 串行执行（FIFO），不并发。"""
    loop = _ensure_backend()
    record = TaskRecord(id=f"{name}-{uuid.uuid4().hex[:8]}", _coro=coro_factory)
    _task_registry[record.id] = record
    # 入队后由 _worker 串行 await，避免多任务并发解析/并发 AI 请求
    asyncio.run_coroutine_threadsafe(_queue.put(record), loop)
    return record


def get_task(task_id: str) -> TaskRecord | None:
    return _task_registry.get(task_id)


def list_tasks() -> list[TaskRecord]:
    return list(_task_registry.values())


def update_progress(record: TaskRecord, progress: float, stage: str = "", message: str = "") -> None:
    record.progress = progress
    if stage:
        record.stage = stage
    if message:
        record.message = message
