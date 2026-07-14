import unittest

import httpx

from ferry.main import _safe_local_complete, _stream_local, _WARMING_UP


def _503_error() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://127.0.0.1:11435/v1/chat/completions")
    resp = httpx.Response(503, request=req)
    return httpx.HTTPStatusError("503", request=req, response=resp)


class Raises503Clients:
    async def ollama_complete(self, messages, model):
        raise _503_error()

    async def ollama_stream(self, messages, model):
        raise _503_error()
        yield  # pragma: no cover - makes this an async generator


class WarmingUpTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_stream_returns_friendly_warming_up(self):
        out = await _safe_local_complete(Raises503Clients(), [], "ferry")
        self.assertEqual(out, _WARMING_UP)

    async def test_stream_emits_friendly_warming_up(self):
        chunks = []
        async for chunk in _stream_local(Raises503Clients(), [], "ferry"):
            chunks.append(chunk)
        joined = "".join(chunks)
        self.assertIn("warming up", joined)
        self.assertNotIn("503", joined.replace("chatcmpl", ""))
