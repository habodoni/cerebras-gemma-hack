"""HTTP clients for the two brains: Ollama (local) and Cerebras (cloud).

Both speak the OpenAI Chat Completions API, so one shape of code talks to both.
The Cerebras client keeps a warm, persistent HTTP/2 connection (so the TLS
handshake doesn't eat the window) and round-robins across a pool of API keys so
we can fan out wider than a single key's 100 RPM.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import httpx

from .config import settings

log = logging.getLogger("ferry.clients")


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
            timeout=httpx.Timeout(settings.local_timeout_seconds, connect=5.0),
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
        payload = _local_payload(messages, model, stream=True)
        async with self.ollama.stream(
            "POST", "/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            async for delta in _iter_sse_content(resp):
                yield delta

    async def ollama_complete(self, messages: list[dict], model: str) -> str:
        payload = _local_payload(messages, model, stream=False)
        resp = await self.ollama.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"].get("content", "")

    # ----- Cloud (Cerebras) ----------------------------------------------
    async def cerebras_agent(
        self, messages: list[dict], system_prompt: str | None = None
    ) -> AsyncIterator[tuple]:
        """Agentic tool-calling loop on Cerebras.

        Yields (kind, text): kind is 'status' for a tool step (shown to the user)
        or 'token' for answer text. The model may call web_search / run_code over
        several rounds before producing a final answer.
        """
        from . import tools as toolmod

        if not self.has_cerebras_key:
            raise RuntimeError("No CEREBRAS_API_KEYS configured")
        convo = [dict(m) for m in messages]
        if system_prompt is not None:
            convo.insert(0, {"role": "system", "content": system_prompt})
        elif not any(m.get("role") == "system" for m in convo):
            convo.insert(0, {"role": "system", "content": _AGENT_SYSTEM})
        force_run_code = _requires_code_artifact(messages)
        force_web_search = force_run_code and _requests_web_search(messages)
        ran_code = False
        ran_search = False
        for _ in range(settings.agent_max_steps):
            if force_web_search and not ran_search:
                tool_choice = _WEB_SEARCH_TOOL_CHOICE
            elif force_run_code and not ran_code:
                tool_choice = _RUN_CODE_TOOL_CHOICE
            else:
                tool_choice = "auto"
            payload = self._cerebras_payload(
                convo,
                tools=toolmod.TOOLS,
                tool_choice=tool_choice,
            )
            msg = (await self._post_cerebras_completion(payload))["choices"][0]["message"]
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                content = msg.get("content") or ""
                if content:
                    yield ("token", content)
                return
            convo.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            })
            tool_tasks = []
            for tc in tool_calls:
                fn = (tc.get("function") or {}).get("name", "")
                raw_args = (tc.get("function") or {}).get("arguments", "{}")
                if fn == "run_code":
                    ran_code = True
                elif fn == "web_search":
                    ran_search = True
                yield ("status", _tool_label(fn, raw_args))
                task = asyncio.create_task(toolmod.call_tool(fn, raw_args))
                tool_tasks.append((tc, fn, task))
            results = await asyncio.gather(
                *(task for _, _, task in tool_tasks),
                return_exceptions=True,
            )
            for (tc, fn, _), result in zip(tool_tasks, results):
                if isinstance(result, Exception):
                    result = f"[tool {fn} error: {result}]"
                convo.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": str(result)[:_TOOL_RESULT_CHARS],
                })
        # Step budget exhausted — one final answer with no more tools.
        data = await self._post_cerebras_completion(self._cerebras_payload(convo))
        yield ("token", data["choices"][0]["message"].get("content") or "")

    async def cerebras_multiverse(self, messages: list[dict]) -> AsyncIterator[tuple]:
        """Parallel sub-agents with tools, followed by a synthesis answer."""
        if not self.has_cerebras_key:
            raise RuntimeError("No CEREBRAS_API_KEYS configured")

        yield ("status", "planning sub-agents")
        agents = await self._plan_multiverse_agents(messages)
        yield ("status", "fan-out: " + ", ".join(a["name"] for a in agents))

        events: asyncio.Queue = asyncio.Queue()
        results: list[dict | None] = [None] * len(agents)
        tasks = [
            asyncio.create_task(self._run_multiverse_agent(i, agent, events))
            for i, agent in enumerate(agents)
        ]
        remaining = len(tasks)
        try:
            while remaining:
                item = await events.get()
                kind = item[0]
                if kind == "status":
                    yield ("status", item[1])
                elif kind == "result":
                    _, idx, result = item
                    results[idx] = result
                    remaining -= 1
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

        completed = [r for r in results if r is not None]
        yield ("status", "synthesizing final answer")
        async for kind, text in self.cerebras_agent(
            _multiverse_synthesis_messages(messages, completed)
        ):
            yield (kind, text)

    def _cerebras_payload(self, messages: list[dict], **extra) -> dict:
        payload = {
            "model": settings.cerebras_model,
            "messages": messages,
            "max_tokens": settings.cerebras_max_tokens,
        }
        # Gemma 4 accepts reasoning_effort="none", but some models (gpt-oss-120b)
        # reject it. Omitting the param keeps reasoning off by default on every
        # model, so only send it when a real effort (low/medium/high) is asked.
        effort = (settings.cerebras_reasoning_effort or "").lower()
        if effort and effort not in ("none", "off"):
            payload["reasoning_effort"] = effort
        payload.update(extra)
        return payload

    async def _post_cerebras_completion(self, payload: dict) -> dict:
        last_exc: Exception | None = None
        # Try every key, plus a few extra rounds so a transient per-minute token
        # quota (429) can be ridden out with backoff instead of failing instantly.
        attempts = max(1, len(settings.cerebras_api_keys)) + 3
        backoff = 1.5
        for _ in range(attempts):
            key = self._next_key()
            try:
                resp = await self.cerebras.post(
                    "/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {key}"},
                )
                if resp.status_code == 429:
                    # Per-minute token/request quota. Another key may have budget;
                    # otherwise wait for the window to refill, then retry.
                    last_exc = RuntimeError(f"Cerebras rate/token limit: {resp.text[:200]}")
                    log.warning("Cerebras 429; backing off %.1fs", backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 8.0)
                    continue
                if resp.status_code >= 400:
                    body = resp.text[:1000]
                    log.error("Cerebras %s: %s", resp.status_code, body)
                    # Other 4xx are bad requests (e.g. context too long) — another
                    # key won't fix it, so fail fast with the real reason.
                    raise RuntimeError(f"Cerebras {resp.status_code}: {body}")
                return resp.json()
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                continue
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Cerebras request failed")

    async def _plan_multiverse_agents(self, messages: list[dict]) -> list[dict]:
        max_agents = max(1, min(settings.multiverse_agents, 6))
        payload = self._cerebras_payload(
            [
                {"role": "system", "content": _MULTIVERSE_PLANNER_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Create {max_agents} specialist sub-agent tasks for this "
                        "request. Return only JSON.\n\n"
                        f"Request:\n{_format_messages(messages, limit=5000)}"
                    ),
                },
            ],
            max_tokens=700,
        )
        try:
            raw = (
                await self._post_cerebras_completion(payload)
            )["choices"][0]["message"].get("content") or ""
            return _parse_agent_plan(raw, max_agents, messages)
        except Exception:
            return _fallback_agents(messages, max_agents)

    async def _run_multiverse_agent(
        self,
        idx: int,
        agent: dict,
        events: asyncio.Queue,
    ) -> None:
        name = agent["name"]
        await events.put(("status", f"{name}: started"))
        try:
            chunks = []
            async for kind, text in self.cerebras_agent(
                _subagent_messages(agent),
                system_prompt=_subagent_system(name),
            ):
                if kind == "status":
                    await events.put(("status", f"{name}: {text}"))
                else:
                    chunks.append(text)
            await events.put(("status", f"{name}: done"))
            await events.put((
                "result",
                idx,
                {"name": name, "task": agent["task"], "content": "".join(chunks)},
            ))
        except Exception as exc:  # noqa: BLE001 - one failed agent should not kill synthesis
            await events.put(("status", f"{name}: error ({exc})"))
            await events.put((
                "result",
                idx,
                {"name": name, "task": agent["task"], "content": f"[agent error: {exc}]"},
            ))


_AGENT_SYSTEM = (
    "You are Ferry's agent. Use web_search for current facts and run_code to "
    "compute or create files. You can create downloadable files including PPTX, "
    "DOCX, XLSX, CSV, PDF, HTML, JSON, TXT, GIF, PNG, SVG, and MP4 by writing "
    "Python code with run_code. For PPTX files, use python-pptx and install it "
    "inside run_code if needed. For GIF/image/video files, use matplotlib, PIL/"
    "Pillow, imageio, or moviepy and install packages inside run_code if needed. "
    "Save generated artifacts in /mnt/data or the current directory. "
    "Never claim you cannot create files when run_code is available; call run_code "
    "instead. Call tools only when needed, then give a clear, "
    "direct final answer for the user. When run_code returns file download URLs, "
    "include those URLs in the final answer. Do not narrate your tool use or think "
    "out loud."
)

_RUN_CODE_TOOL_CHOICE = {
    "type": "function",
    "function": {"name": "run_code"},
}

_WEB_SEARCH_TOOL_CHOICE = {
    "type": "function",
    "function": {"name": "web_search"},
}

_ARTIFACT_TERMS = (
    "pptx", ".pptx", "powerpoint", "presentation", "slide deck", "slides",
    "deck", "docx", ".docx", "word document", "xlsx", ".xlsx", "spreadsheet",
    "csv", ".csv", "pdf", ".pdf", "html", ".html", "json", ".json", "txt",
    ".txt", "gif", ".gif", "animated gif", "animation", "animated",
    "visualization", "png", ".png", "image", "svg", ".svg", "mp4", ".mp4",
    "video", "download link", "downloadable", "file",
)

_WEB_SEARCH_TERMS = (
    "search", "web", "look up", "lookup", "current", "latest", "today",
    "recent", "news", "online",
)

_ARTIFACT_ACTIONS = (
    "create", "make", "build", "generate", "export", "save", "write",
    "produce", "prepare",
)

_MULTIVERSE_PLANNER_SYSTEM = (
    "You are the orchestrator. Break the user's request into parallel specialist "
    "sub-agents. Each sub-agent runs in ISOLATION and cannot see the conversation, "
    "so every task must be fully self-contained: restate the context it needs, the "
    "specific question to answer, and exactly what to return. Return only JSON of "
    "the shape {\"agents\":[{\"name\":\"Researcher\",\"task\":\"...\"}]}. Use short "
    "names and concrete, non-overlapping tasks. Include search, code, or "
    "verification work only when it materially helps."
)

_MULTIVERSE_SYNTHESIS_SYSTEM = (
    "You are the orchestrator. You guided parallel sub-agents and now own the final "
    "answer. Critically review their findings: reconcile conflicts, correct errors, "
    "and fill gaps. Tools are available — call web_search or run_code yourself to "
    "verify a doubtful claim, finish a missing computation, or generate a requested "
    "file. Then write one clear, direct final answer for the user. Cite URLs when "
    "the findings include them, preserve any file download URLs exactly, and briefly "
    "flag any remaining uncertainty. Prefer a direct answer over process notes."
)


def _tool_label(name: str, raw_args) -> str:
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
    except (json.JSONDecodeError, TypeError):
        args = {}
    if name == "web_search":
        return f"🔍 searching: {str(args.get('query', ''))[:80]}"
    if name == "run_code":
        return "🐍 running code"
    return f"🔧 {name}"


def _local_payload(messages: list[dict], model: str, stream: bool) -> dict:
    payload = {
        "model": model,
        "messages": _compact_local_messages(messages),
        "stream": stream,
        "temperature": settings.local_temperature,
    }
    if settings.local_max_tokens > 0:
        payload["max_tokens"] = settings.local_max_tokens
    return payload


def _compact_local_messages(messages: list[dict]) -> list[dict]:
    """Keep local prompts bounded so Docker Ollama does not stall Open WebUI."""
    system = []
    for msg in messages:
        if msg.get("role") == "system":
            system.append({
                "role": "system",
                "content": _trim_text(_content_text(msg.get("content"))),
            })
            break

    keep_count = max(1, settings.local_context_messages)
    recent = [
        msg for msg in messages
        if msg.get("role") in {"user", "assistant"}
    ][-keep_count:]
    compacted = [
        {
            "role": msg.get("role", "user"),
            "content": _trim_text(_content_text(msg.get("content"))),
        }
        for msg in recent
    ]
    return system + compacted


def _trim_text(text: str) -> str:
    limit = max(200, settings.local_context_chars)
    if len(text) <= limit:
        return text
    return "[truncated]\n" + text[-limit:]


def _requires_code_artifact(messages: list[dict]) -> bool:
    text = _last_user_text(messages).lower()
    if not text:
        return False
    has_artifact = any(term in text for term in _ARTIFACT_TERMS)
    if not has_artifact:
        return False
    has_action = any(action in text for action in _ARTIFACT_ACTIONS)
    # Extensions and explicit download/file phrasing are already artifact requests.
    return has_action or any(
        token in text
        for token in (
            ".pptx", ".docx", ".xlsx", ".csv", ".pdf", ".html", ".json",
            ".txt", ".gif", ".png", ".svg", ".mp4", "download",
        )
    )


def _requests_web_search(messages: list[dict]) -> bool:
    text = _last_user_text(messages).lower()
    return any(term in text for term in _WEB_SEARCH_TERMS)


def _subagent_system(name: str) -> str:
    return (
        f"You are {name}, a specialist sub-agent in Ferry's multiverse fan-out. "
        "Use web_search for current facts and run_code for computation or file "
        "creation when they materially improve your assigned slice. Return concise "
        "findings only. Include any file download URLs returned by run_code. Do "
        "not write the user's final answer."
    )


def _subagent_messages(agent: dict) -> list[dict]:
    # Workers run in isolation: they receive ONLY their self-contained instruction,
    # never the chat history. The orchestrator's planner bakes all needed context
    # into the task, which keeps each worker's payload tiny.
    return [{
        "role": "user",
        "content": (
            f"{agent['task']}\n\n"
            "Complete only this assignment. Use web_search for current facts and "
            "run_code for computation or file creation when they materially help. "
            "Return concise findings: key facts, evidence, URLs, numbers, code "
            "results, and any file download URLs. Do not write the user's final answer."
        ),
    }]


def _multiverse_synthesis_messages(messages: list[dict], results: list[dict]) -> list[dict]:
    findings = "\n\n".join(
        f"## {r['name']}\nAssigned task: {r['task']}\nFindings:\n{r['content'][:_FINDINGS_CHARS]}"
        for r in results
    )
    return [
        {"role": "system", "content": _MULTIVERSE_SYNTHESIS_SYSTEM},
        {
            "role": "user",
            "content": (
                "Original conversation:\n"
                f"{_format_messages(messages, limit=7000)}\n\n"
                "Parallel sub-agent findings:\n"
                f"{findings or 'No findings returned.'}\n\n"
                "Write the final answer for the user."
            ),
        },
    ]


def _parse_agent_plan(raw: str, max_agents: int, messages: list[dict]) -> list[dict]:
    obj = _extract_json_object(raw)
    if not isinstance(obj, dict):
        return _fallback_agents(messages, max_agents)
    agents = obj.get("agents")
    if not isinstance(agents, list):
        return _fallback_agents(messages, max_agents)
    cleaned = []
    for i, item in enumerate(agents[:max_agents], 1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or f"Agent {i}").strip()[:40]
        task = str(item.get("task") or "").strip()[:1200]
        if task:
            cleaned.append({"name": name or f"Agent {i}", "task": task})
    return cleaned or _fallback_agents(messages, max_agents)


def _extract_json_object(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _fallback_agents(messages: list[dict], max_agents: int) -> list[dict]:
    request = _last_user_text(messages)[:1200] or "the user's request"
    templates = [
        (
            "Researcher",
            "Find current facts, sources, and context relevant to the request.",
        ),
        (
            "Analyst",
            "Check logic, tradeoffs, calculations, and edge cases relevant to the request.",
        ),
        (
            "Builder",
            "Prototype or compute anything useful, then identify concrete implementation details.",
        ),
        (
            "Reviewer",
            "Look for gaps, risks, missing evidence, and concise caveats.",
        ),
    ]
    return [
        {"name": name, "task": f"{task}\n\nUser request: {request}"}
        for name, task in templates[:max_agents]
    ]


# Open WebUI forwards the entire multi-turn chat (including prior long answers)
# on every request. gemma-4-31b allows 131k context but only ~100k tokens/minute,
# and a multi-agent turn fans that history out across several concurrent calls —
# so trim it once at the API boundary before it reaches Cerebras.
_CLOUD_KEEP_MESSAGES = 8
_CLOUD_MSG_CHARS = 6000
_TOOL_RESULT_CHARS = 6000
_FINDINGS_CHARS = 2500


def compact_cloud_messages(messages: list[dict]) -> list[dict]:
    """Cap chat-history length/size before bursting it to Cerebras."""
    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"][-_CLOUD_KEEP_MESSAGES:]
    out: list[dict] = []
    for m in system + rest:
        nm = dict(m)
        content = m.get("content")
        if isinstance(content, str) and len(content) > _CLOUD_MSG_CHARS:
            nm["content"] = content[:_CLOUD_MSG_CHARS] + "\n…[truncated]"
        out.append(nm)
    return out


def _format_messages(messages: list[dict], limit: int) -> str:
    text = "\n".join(
        f"{m.get('role', 'message')}: {_content_text(m.get('content'))}"
        for m in messages
    )
    if len(text) <= limit:
        return text
    return "[truncated]\n" + text[-limit:]


def _last_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return _content_text(msg.get("content"))
    return _content_text(messages[-1].get("content")) if messages else ""


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict):
                parts.append(str(part))
            elif part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif part.get("type") == "image_url":
                image = part.get("image_url") or {}
                url = image.get("url") if isinstance(image, dict) else image
                parts.append(f"[image: {str(url)[:160]}]")
        return " ".join(p for p in parts if p)
    return str(content)


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
