"""HTTP clients for the two brains: Ollama (local) and Cerebras (cloud).

Both speak the OpenAI Chat Completions API, so one shape of code talks to both.
The Cerebras client keeps a warm, persistent HTTP/2 connection (so the TLS
handshake doesn't eat the window) and round-robins across a pool of API keys so
we can fan out wider than a single key's 100 RPM.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .config import settings


class Clients:
    def __init__(self) -> None:
        # Warm, reusable connections. Created once at startup.
        self.cerebras = httpx.AsyncClient(
            base_url=settings.cerebras_base_url,
            http2=True,
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=64, max_connections=128),
        )
        self.ollama = httpx.AsyncClient(
            base_url=settings.ollama_base_url,
            timeout=httpx.Timeout(120.0, connect=5.0),
        )
        self._key_idx = 0

    async def aclose(self) -> None:
        await self.cerebras.aclose()
        await self.ollama.aclose()

    @property
    def has_cerebras_key(self) -> bool:
        return bool(settings.cerebras_api_keys)

    def _next_key(self) -> str:
        keys = settings.cerebras_api_keys
        key = keys[self._key_idx % len(keys)]
        self._key_idx += 1
        return key

    # ----- Local (Ollama) -------------------------------------------------
    async def ollama_stream(
        self, messages: list[dict], model: str
    ) -> AsyncIterator[str]:
        payload = {"model": model, "messages": messages, "stream": True}
        async with self.ollama.stream(
            "POST", "/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            async for delta in _iter_sse_content(resp):
                yield delta

    async def ollama_complete(self, messages: list[dict], model: str) -> str:
        payload = {"model": model, "messages": messages, "stream": False}
        resp = await self.ollama.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"].get("content", "")

    # ----- Cloud (Cerebras) ----------------------------------------------
    async def cerebras_stream(self, messages: list[dict]) -> AsyncIterator[str]:
        if not self.has_cerebras_key:
            raise RuntimeError("No CEREBRAS_API_KEYS configured")
        payload = {
            "model": settings.cerebras_model,
            "messages": messages,
            "stream": True,
            "max_tokens": settings.cerebras_max_tokens,
        }
        # Gemma 4 accepts reasoning_effort="none", but some models (gpt-oss-120b)
        # reject it. Omitting the param keeps reasoning off by default on every
        # model, so only send it when a real effort (low/medium/high) is asked.
        effort = (settings.cerebras_reasoning_effort or "").lower()
        if effort and effort not in ("none", "off"):
            payload["reasoning_effort"] = effort
        last_exc: Exception | None = None
        # Try each key once on rate-limit / transient connection errors.
        for _ in range(max(1, len(settings.cerebras_api_keys))):
            key = self._next_key()
            headers = {"Authorization": f"Bearer {key}"}
            try:
                async with self.cerebras.stream(
                    "POST", "/chat/completions", json=payload, headers=headers
                ) as resp:
                    if resp.status_code == 429:
                        await resp.aread()
                        continue  # next key
                    resp.raise_for_status()
                    async for delta in _iter_sse_content(resp):
                        yield delta
                return
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                continue
        if last_exc is not None:
            raise last_exc


async def _iter_sse_content(resp: httpx.Response) -> AsyncIterator[str]:
    """Yield the `delta.content` string from each OpenAI SSE line."""
    async for line in resp.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            return
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if content:
            yield content
