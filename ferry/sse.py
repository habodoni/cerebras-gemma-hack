"""Helpers for emitting OpenAI-compatible Server-Sent Events.

Both the local path and the cloud path stream through these so Open WebUI sees
one consistent chunk format regardless of which brain answered.
"""
from __future__ import annotations

import json
import time

DONE = "data: [DONE]\n\n"


def chunk(model: str, delta: dict, finish_reason: str | None = None) -> str:
    """One streaming chat.completion.chunk."""
    payload = {
        "id": "chatcmpl-ferry",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def heartbeat() -> str:
    """SSE comment line — keeps the held-open connection alive while queued."""
    return ": keepalive\n\n"


def completion(model: str, content: str) -> dict:
    """A full (non-streaming) chat.completion response body."""
    return {
        "id": "chatcmpl-ferry",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
