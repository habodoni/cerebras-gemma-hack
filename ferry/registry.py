"""In-memory map of task_id -> live SSE delivery queue.

When a chat request queues a hard task, it registers a queue here and holds its
SSE stream open. The drainer pushes Cerebras tokens into that queue, which the
held-open stream forwards into the *same* chat bubble. Seeded tasks have no
registered queue — their answers just land in SQLite for the dashboard.
"""
from __future__ import annotations

import asyncio

_queues: dict[str, asyncio.Queue] = {}


def register(task_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _queues[task_id] = q
    return q


def get(task_id: str) -> asyncio.Queue | None:
    return _queues.get(task_id)


def unregister(task_id: str) -> None:
    _queues.pop(task_id, None)


async def push(task_id: str, item: dict) -> None:
    q = _queues.get(task_id)
    if q is not None:
        await q.put(item)
