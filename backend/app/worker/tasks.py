"""后台任务管理器（docs/01-architecture.md §6）

- asyncio.create_task + 内存任务表（单机单用户足够）
- 长任务串行执行（FIFO 队列），避免并发解析耗尽内存
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

_task_registry: dict[str, "TaskRecord"] = {}
_queue: asyncio.Queue = asyncio.Queue()
_worker_started = False


@dataclass
class TaskRecord:
    id: str
    status: str = "pending"  # pending/running/done/failed
    progress: float = 0.0
    stage: str = ""
    message: str = ""
    result: dict | None = None
    error: str | None = None
    _future: asyncio.Future = field(default=None, repr=False)


async def _worker() -> None:
    while True:
        record: TaskRecord = await _queue.get()
        try:
            record.status = "running"
            await record._future
            record.status = "done"
        except Exception as e:  # noqa: BLE001
            record.status = "failed"
            record.error = str(e)
        finally:
            _queue.task_done()


def submit(name: str, coro_factory: Callable[[TaskRecord], Awaitable[Any]]) -> TaskRecord:
    """提交任务。coro_factory 接收 TaskRecord 用于更新进度。"""
    global _worker_started
    if not _worker_started:
        asyncio.get_event_loop().create_task(_worker())
        _worker_started = True

    record = TaskRecord(id=f"{name}-{uuid.uuid4().hex[:8]}")

    async def run():
        result = await coro_factory(record)
        record.result = result
        return result

    record._future = asyncio.ensure_future(run())
    _task_registry[record.id] = record
    _queue.put_nowait(record)
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
