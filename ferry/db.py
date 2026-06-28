"""SQLite backlog (WAL mode) — the on-device task queue + delivered answers.

FIFO by created_at. Nothing is lost offline: tasks persist here and a held-open
chat stream (or the dashboard) reads the answer back once the drainer fills it.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import aiosqlite

from .config import settings

_db: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    conversation  TEXT,
    prompt        TEXT,            -- last user message, for display
    messages      TEXT,            -- full OpenAI messages array (JSON)
    priority      INTEGER DEFAULT 5,
    est_tokens    INTEGER,
    status        TEXT,            -- queued | sending | done | error
    response      TEXT,
    error         TEXT,
    route         TEXT,            -- why it was queued (router decision)
    source        TEXT,            -- chat | seed
    attempts      INTEGER DEFAULT 0,
    created_at    TEXT,
    sent_at       TEXT,
    completed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def init() -> None:
    global _db
    os.makedirs(os.path.dirname(os.path.abspath(settings.db_path)), exist_ok=True)
    _db = await aiosqlite.connect(settings.db_path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL;")
    await _db.execute("PRAGMA synchronous=NORMAL;")
    await _db.executescript(SCHEMA)
    await _db.commit()
    # Recover anything left mid-flight by a previous crash/window-close.
    await _db.execute(
        "UPDATE tasks SET status='queued' WHERE status='sending'"
    )
    await _db.commit()


async def close() -> None:
    if _db is not None:
        await _db.close()


async def enqueue(
    messages: list[dict],
    *,
    route: str,
    source: str = "chat",
    conversation: str | None = None,
    priority: int = 5,
    task_id: str | None = None,
) -> str:
    """Insert a queued task and return its id."""
    assert _db is not None
    tid = task_id or uuid.uuid4().hex
    prompt = _last_user_text(messages)
    est = max(1, len(prompt) // 4)
    await _db.execute(
        """INSERT INTO tasks
           (id, conversation, prompt, messages, priority, est_tokens, status,
            route, source, created_at)
           VALUES (?,?,?,?,?,?, 'queued', ?,?,?)""",
        (
            tid,
            conversation,
            prompt,
            json.dumps(messages),
            priority,
            est,
            route,
            source,
            now_iso(),
        ),
    )
    await _db.commit()
    return tid


async def get_queued(limit: int = 1000) -> list[dict]:
    """FIFO: oldest queued tasks first."""
    assert _db is not None
    cur = await _db.execute(
        "SELECT * FROM tasks WHERE status='queued' ORDER BY created_at ASC LIMIT ?",
        (limit,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def mark_sending(task_id: str) -> None:
    assert _db is not None
    await _db.execute(
        "UPDATE tasks SET status='sending', sent_at=?, attempts=attempts+1 WHERE id=?",
        (now_iso(), task_id),
    )
    await _db.commit()


async def mark_done(task_id: str, response: str) -> None:
    assert _db is not None
    await _db.execute(
        "UPDATE tasks SET status='done', response=?, completed_at=? WHERE id=?",
        (response, now_iso(), task_id),
    )
    await _db.commit()


async def requeue_or_fail(task_id: str, error: str) -> str:
    """Retry on the next window until max_attempts, then give up."""
    assert _db is not None
    cur = await _db.execute("SELECT attempts FROM tasks WHERE id=?", (task_id,))
    row = await cur.fetchone()
    attempts = row["attempts"] if row else settings.max_attempts
    if attempts >= settings.max_attempts:
        await _db.execute(
            "UPDATE tasks SET status='error', error=? WHERE id=?", (error, task_id)
        )
        await _db.commit()
        return "error"
    await _db.execute(
        "UPDATE tasks SET status='queued', error=? WHERE id=?", (error, task_id)
    )
    await _db.commit()
    return "queued"


async def list_tasks(limit: int = 200) -> list[dict]:
    assert _db is not None
    cur = await _db.execute(
        "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def counts() -> dict:
    assert _db is not None
    cur = await _db.execute("SELECT status, COUNT(*) c FROM tasks GROUP BY status")
    rows = await cur.fetchall()
    out = {"queued": 0, "sending": 0, "done": 0, "error": 0}
    for r in rows:
        out[r["status"]] = r["c"]
    return out


async def clear() -> None:
    assert _db is not None
    await _db.execute("DELETE FROM tasks")
    await _db.commit()


def _last_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return _text_of(msg.get("content"))
    return _text_of(messages[-1].get("content")) if messages else ""


def _text_of(content) -> str:
    """Flatten OpenAI content (string or multimodal parts) to display text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    parts.append("[image]")
        return " ".join(parts)
    return str(content)
