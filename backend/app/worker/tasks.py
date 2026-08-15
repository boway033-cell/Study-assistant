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
    _future: "asyncio.Future | None" = field(default=None, repr=False)


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
    while True:
        record: TaskRecord = await _queue.get()
        try:
            record.status = "running"
            if record._future is not None:
                # run_coroutine_threadsafe 返回 concurrent.futures.Future，需 wrap 成 asyncio Future
                await asyncio.wrap_future(record._future)
            record.status = "done"
        except Exception as e:  # noqa: BLE001
            record.status = "failed"
            record.error = str(e)
        finally:
            _queue.task_done()


def submit(name: str, coro_factory: Callable[[TaskRecord], Awaitable[Any]]) -> TaskRecord:
    """提交任务（线程安全）。coro_factory 接收 TaskRecord 用于更新进度。"""
    loop = _ensure_backend()
    record = TaskRecord(id=f"{name}-{uuid.uuid4().hex[:8]}")

    async def run():
        result = await coro_factory(record)
        record.result = result
        return result

    record._future = asyncio.run_coroutine_threadsafe(run(), loop)
    _task_registry[record.id] = record
    # 提交到后台队列（后台 loop 上执行；不阻塞等待，避免竞态超时）
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
