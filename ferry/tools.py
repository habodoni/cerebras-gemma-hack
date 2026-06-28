"""External tools the Cerebras burst can call.

Gemma 4 (and gpt-oss-120b) on Cerebras support OpenAI-style tool-calling, so the
model can reach outside itself mid-answer:
  - web_search  → Exa (current information from the web)
  - run_code    → E2B sandbox (execute Python, create files, return output)

Web search and code execution are inherently online and slower than inference —
that's expected; Cerebras isn't in the loop for them.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from .config import settings

# ---- Tool schemas (OpenAI function-calling format) ----
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current, factual, or recent information. "
                "Returns ranked results with titles, URLs, and text highlights."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "num_results": {
                        "type": "integer",
                        "description": "How many results to return (1-10, default 5)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_code",
            "description": (
                "Execute Python 3 code in a secure cloud sandbox and return "
                "stdout, stderr, return values, and any files it creates. Use this "
                "to compute, analyze data, or generate files (write them to disk in "
                "the code and they will be listed back)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                },
                "required": ["code"],
            },
        },
    },
]


# ---- web_search (Exa) ----
async def web_search(query: str, num_results: int = 5) -> str:
    if not settings.exa_api_key:
        return "[web_search unavailable: no EXA_API_KEY configured]"
    async with httpx.AsyncClient(timeout=20.0) as h:
        resp = await h.post(
            "https://api.exa.ai/search",
            headers={
                "x-api-key": settings.exa_api_key,
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "numResults": max(1, min(int(num_results or 5), 10)),
                "type": "auto",
                "contents": {"highlights": True, "text": {"maxCharacters": 500}},
            },
        )
        resp.raise_for_status()
        data = resp.json()
    lines = []
    for i, r in enumerate(data.get("results", []), 1):
        title = r.get("title") or "(untitled)"
        url = r.get("url", "")
        highlights = r.get("highlights") or []
        snippet = " … ".join(highlights) if highlights else (r.get("text") or "")
        lines.append(f"{i}. {title}\n   {url}\n   {snippet[:600]}")
    return "\n".join(lines) if lines else "No results found."


# ---- run_code (E2B) ----
def _run_code_sync(code: str) -> str:
    from e2b_code_interpreter import Sandbox

    sbx = Sandbox.create(api_key=settings.e2b_api_key)
    try:
        execution = sbx.run_code(code)
        parts: list[str] = []
        logs = execution.logs
        if getattr(logs, "stdout", None):
            parts.append("stdout:\n" + "".join(logs.stdout).strip())
        if getattr(logs, "stderr", None):
            parts.append("stderr:\n" + "".join(logs.stderr).strip())
        if execution.error:
            parts.append(f"error: {execution.error.name}: {execution.error.value}")
        for res in execution.results:
            text = getattr(res, "text", None)
            if text:
                parts.append("result: " + text)
        # Surface any files the code created in the working dir.
        try:
            entries = sbx.files.list("/home/user")
            names = [
                e.name for e in entries
                if getattr(e, "type", "") != "dir" and not e.name.startswith(".")
            ]
            if names:
                parts.append("files created: " + ", ".join(sorted(names)))
        except Exception:  # noqa: BLE001 - file listing is best-effort
            pass
        return "\n".join(parts) if parts else "(no output)"
    finally:
        sbx.kill()


async def run_code(code: str) -> str:
    if not settings.e2b_api_key:
        return "[run_code unavailable: no E2B_API_KEY configured]"
    # The E2B SDK is sync; run it off the event loop.
    return await asyncio.to_thread(_run_code_sync, code)


# ---- dispatch ----
DISPATCH = {"web_search": web_search, "run_code": run_code}


async def call_tool(name: str, arguments) -> str:
    """Execute a tool by name with JSON-or-dict arguments; never raises."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except (json.JSONDecodeError, TypeError):
        args = {}
    fn = DISPATCH.get(name)
    if fn is None:
        return f"[unknown tool: {name}]"
    try:
        return await fn(**args)
    except Exception as exc:  # noqa: BLE001 - tool errors go back to the model, not the user
        return f"[tool {name} error: {exc}]"
