"""Burst drainer — when a window is open, fan the backlog out to Cerebras.

FIFO order, but all in-flight at once (bounded by a semaphore sized to the key
pool). Each answer is streamed back into its held-open chat bubble if one is
waiting, and always persisted to SQLite for the dashboard / next refresh.
"""
from __future__ import annotations

import asyncio
import json
import logging

from . import db, registry
from .config import settings
from .watcher import ConnectivityWatcher

log = logging.getLogger("ferry.drainer")


class BurstDrainer:
    def __init__(self, clients, watcher: ConnectivityWatcher) -> None:
        self.clients = clients
        self.watcher = watcher
        self._sem = asyncio.Semaphore(settings.drain_concurrency)
        self._warned_no_key = False

    async def run(self) -> None:
        while True:
            if self.watcher.can_burst():
                await self.drain_once()
            await asyncio.sleep(settings.drain_poll_interval)

    async def drain_once(self) -> int:
        if not await self.watcher.can_burst_now():
            return 0
        if not self.clients.has_cerebras_key:
            if not self._warned_no_key:
                log.warning("Window is open but no CEREBRAS_API_KEYS set; tasks stay queued.")
                self._warned_no_key = True
            return 0

        tasks = await db.get_queued(limit=1000)
        if not tasks:
            return 0
        log.info("Window open — draining %d task(s) in parallel", len(tasks))
        await asyncio.gather(
            *(self._handle(t) for t in tasks), return_exceptions=True
        )
        return len(tasks)

    async def _handle(self, task: dict) -> None:
        # Respect a window that closed mid-drain: leave it queued for next time.
        if not self.watcher.can_burst():
            return
        async with self._sem:
            if not self.watcher.can_burst():
                return
            await self._process(task)

    async def _process(self, task: dict) -> None:
        tid = task["id"]
        messages = json.loads(task["messages"])
        await db.mark_sending(tid)
        try:
            full = []
            runner = (
                self.clients.cerebras_multiverse
                if _cloud_mode(task) == "multi_agent"
                else self.clients.cerebras_agent
            )
            async for kind, text in runner(messages):
                if kind == "status":
                    await registry.push(tid, {"type": "status", "text": text})
                else:
                    full.append(text)
                    await registry.push(tid, {"type": "token", "text": text})
            answer = "".join(full)
            await db.mark_done(tid, answer)
            await registry.push(tid, {"type": "done"})
            log.info("Delivered task %s (%d chars)", tid[:8], len(answer))
        except Exception as exc:  # noqa: BLE001 - drainer must never crash the loop
            outcome = await db.requeue_or_fail(tid, str(exc))
            log.warning("Task %s failed (%s) -> %s", tid[:8], exc, outcome)
            if outcome == "error":
                await registry.push(tid, {"type": "error", "text": str(exc)})


def _cloud_mode(task: dict) -> str:
    route = str(task.get("route") or "")
    if route.startswith("multi_agent"):
        return "multi_agent"
    return "single_agent"
