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
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import quote

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
                "to compute, analyze data, or generate files. Write generated files "
                "to the current directory or /mnt/data; Ferry will return download "
                "URLs for them."
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
def _execute_on_sandbox(sbx, code: str) -> str:
    """Run one code cell on an existing sandbox and report output + new files.

    Diffs the file tree before/after so we only return files this cell created,
    even on a long-lived sandbox that already holds earlier steps' artifacts.
    """
    before = _sandbox_file_entries(sbx)
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
    files = _created_files(before, _sandbox_file_entries(sbx))
    file_lines = _save_sandbox_files(sbx, files[:settings.e2b_max_files])
    if file_lines:
        parts.append("files created:\n" + "\n".join(file_lines))
    if len(files) > settings.e2b_max_files:
        parts.append(
            f"files skipped: {len(files) - settings.e2b_max_files} "
            f"extra file(s) beyond E2B_MAX_FILES={settings.e2b_max_files}"
        )
    return "\n".join(parts) if parts else "(no output)"


def _run_code_sync(code: str) -> str:
    """One-shot: a fresh sandbox per call (fallback when there is no CodeSession)."""
    from e2b_code_interpreter import Sandbox

    sbx = Sandbox.create(api_key=settings.e2b_api_key)
    try:
        _ensure_file_roots(sbx)
        return _execute_on_sandbox(sbx, code)
    finally:
        sbx.kill()


async def run_code(code: str) -> str:
    if not settings.e2b_api_key:
        return "[run_code unavailable: no E2B_API_KEY configured]"
    # The E2B SDK is sync; run it off the event loop.
    return await asyncio.to_thread(_run_code_sync, code)


class CodeSession:
    """One reusable E2B sandbox for a single agent run.

    Created lazily on the first run_code call and reused across the loop, so
    files and pip-installed packages persist between steps (build a dataset in
    one step, chart it in the next). Killed once when the run ends —
    Clients.cerebras_agent calls close() in its finally.
    """

    def __init__(self) -> None:
        self._sbx = None

    async def run_code(self, code: str) -> str:
        if not settings.e2b_api_key:
            return "[run_code unavailable: no E2B_API_KEY configured]"
        return await asyncio.to_thread(self._run, code)

    def _run(self, code: str) -> str:
        if self._sbx is None:
            from e2b_code_interpreter import Sandbox
            self._sbx = Sandbox.create(api_key=settings.e2b_api_key)
            _ensure_file_roots(self._sbx)
        return _execute_on_sandbox(self._sbx, code)

    async def close(self) -> None:
        sbx, self._sbx = self._sbx, None
        if sbx is not None:
            try:
                await asyncio.to_thread(sbx.kill)
            except Exception:  # noqa: BLE001 - cleanup must never raise
                pass


def _ensure_file_roots(sbx) -> None:
    for root in settings.e2b_file_roots:
        root = root.strip()
        if not root or root == "/home/user":
            continue
        try:
            sbx.files.make_dir(root)
        except Exception:  # noqa: BLE001 - roots are best-effort convenience
            pass


def _sandbox_file_entries(sbx) -> dict[str, object]:
    files = {}
    for root in settings.e2b_file_roots:
        root = root.strip()
        if not root:
            continue
        try:
            entries = sbx.files.list(root, depth=settings.e2b_file_list_depth)
        except Exception:  # noqa: BLE001 - one missing root should not hide other roots
            continue
        for entry in entries:
            if not _is_file_entry(entry):
                continue
            path = str(getattr(entry, "path", "") or "")
            rel = _artifact_relpath(path)
            if path and rel is not None:
                files[path] = entry
    return files


def _created_files(before: dict[str, object], after: dict[str, object]) -> list[object]:
    created = []
    for path, entry in after.items():
        previous = before.get(path)
        if previous is None or _file_changed(previous, entry):
            created.append(entry)
    return sorted(created, key=lambda item: str(getattr(item, "path", "")))


def _file_changed(before, after) -> bool:
    before_size = getattr(before, "size", None)
    after_size = getattr(after, "size", None)
    before_modified = getattr(before, "modified_time", None)
    after_modified = getattr(after, "modified_time", None)
    return before_size != after_size or before_modified != after_modified


def _is_file_entry(entry) -> bool:
    kind = getattr(entry, "type", "")
    value = str(getattr(kind, "value", kind)).lower()
    name = str(getattr(entry, "name", "") or "")
    return value == "file" and bool(name) and not name.startswith(".")


def _artifact_relpath(path: str) -> str | None:
    posix_path = PurePosixPath("/" + path.lstrip("/"))
    for root in sorted(settings.e2b_file_roots, key=len, reverse=True):
        root_path = PurePosixPath("/" + root.strip().lstrip("/"))
        try:
            rel = posix_path.relative_to(root_path)
        except ValueError:
            continue
        parts = rel.parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            return None
        if any(part.startswith(".") for part in parts):
            return None
        return rel.as_posix()
    return None


def _save_sandbox_files(sbx, entries: list[object]) -> list[str]:
    if not entries:
        return []
    run_id = uuid.uuid4().hex
    run_dir = Path(settings.generated_files_dir).resolve() / run_id
    lines = []
    for entry in entries:
        rel = _artifact_relpath(str(getattr(entry, "path", "") or ""))
        if rel is None:
            continue
        size = int(getattr(entry, "size", 0) or 0)
        if settings.e2b_max_file_bytes > 0 and size > settings.e2b_max_file_bytes:
            lines.append(
                f"- {rel}: skipped ({size} bytes exceeds "
                f"E2B_MAX_FILE_BYTES={settings.e2b_max_file_bytes})"
            )
            continue
        target = _artifact_target(run_dir, rel)
        if target is None:
            continue
        try:
            raw = sbx.files.read(getattr(entry, "path"), format="bytes")
            if hasattr(raw, "read"):
                raw = raw.read()
            data = raw.encode() if isinstance(raw, str) else bytes(raw)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except Exception as exc:  # noqa: BLE001 - report per-file export failures
            lines.append(f"- {rel}: export failed ({exc})")
            continue
        url = _artifact_url(run_id, rel)
        lines.append(f"- {rel} ({len(data)} bytes): {url}")
    return lines


def _artifact_target(run_dir: Path, rel: str) -> Path | None:
    target = (run_dir / Path(*PurePosixPath(rel).parts)).resolve()
    try:
        target.relative_to(run_dir.resolve())
    except ValueError:
        return None
    return target


def _artifact_url(run_id: str, rel: str) -> str:
    return f"{settings.public_base_url}/api/files/{run_id}/{quote(rel, safe='/')}"


# ---- dispatch ----
DISPATCH = {"web_search": web_search, "run_code": run_code}


async def call_tool(name: str, arguments, session: "CodeSession | None" = None) -> str:
    """Execute a tool by name with JSON-or-dict arguments; never raises.

    When a CodeSession is supplied, run_code reuses its long-lived sandbox so
    state persists across the agent run; otherwise it falls back to one-shot.
    """
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except (json.JSONDecodeError, TypeError):
        args = {}
    try:
        if name == "run_code" and session is not None:
            return await session.run_code(str(args.get("code", "")))
        fn = DISPATCH.get(name)
        if fn is None:
            return f"[unknown tool: {name}]"
        return await fn(**args)
    except Exception as exc:  # noqa: BLE001 - tool errors go back to the model, not the user
        return f"[tool {name} error: {exc}]"
