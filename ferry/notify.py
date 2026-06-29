"""Best-effort notifications for the two moments that matter in Ferry's loop:
a prompt gets **queued** (no window now) and its answer **returns** (bursted back).

Pluggable per deployment via NOTIFY_MODE:
  - none  : disabled (default)
  - macos : native macOS notification on the machine running Ferry — the laptop flow
  - ntfy  : HTTP push to an ntfy topic — reaches your phone from the headless Jetson hub

Notifications are best-effort: a failure here must never affect the chat path, so
every call is wrapped and swallowed.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .config import settings

log = logging.getLogger("ferry.notify")


async def queued(backlog: int = 0) -> None:
    tail = f" ({backlog} in backlog)" if backlog and backlog > 1 else ""
    await _notify(
        "Ferry",
        f"⏳ Queued — I'll answer the moment a connection window opens{tail}.",
        tag="hourglass_flowing_sand",
    )


async def returned(preview: str = "") -> None:
    preview = " ".join(preview.split())[:80]
    body = "✅ Answer ready" + (f": {preview}…" if preview else " — bursted back from Cerebras.")
    await _notify("Ferry", body, tag="white_check_mark")


async def _notify(title: str, body: str, tag: str | None = None) -> None:
    mode = (settings.notify_mode or "none").lower()
    try:
        if mode == "macos":
            await _macos(title, body)
        elif mode == "ntfy":
            await _ntfy(title, body, tag)
    except Exception as exc:  # noqa: BLE001 - notifications are best-effort
        log.warning("notify (%s) failed: %s", mode, exc)


async def _macos(title: str, body: str) -> None:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{esc(body)}" with title "{esc(title)}"'
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def _ntfy(title: str, body: str, tag: str | None) -> None:
    if not settings.ntfy_topic:
        return
    url = settings.ntfy_server.rstrip("/") + "/" + settings.ntfy_topic
    # HTTP headers must be latin-1 safe, so keep the (ASCII) title there and let the
    # UTF-8 body carry any emoji; ntfy renders `Tags` as a leading icon.
    headers = {"Title": title}
    if tag:
        headers["Tags"] = tag
    async with httpx.AsyncClient(timeout=5.0) as h:
        await h.post(url, content=body.encode("utf-8"), headers=headers)
