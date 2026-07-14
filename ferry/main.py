"""Ferry FastAPI service — the OpenAI-compatible front door for Open WebUI.

Endpoints:
  GET  /v1/models                 list Ferry's models
  POST /v1/chat/completions       triage -> local now, or queue + held-open SSE
  GET  /api/status                online state + backlog counts (for dashboard)
  GET  /api/tasks                 the backlog (for dashboard)
  POST /demo/online/{state}       true | false | auto  (force the window)
  POST /demo/seed                 preload N hard tasks into the backlog
  POST /demo/clear                empty the backlog
  GET  /dashboard                 the backlog viewer
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)

from . import db, notify, registry, router, sse
from .clients import Clients, compact_cloud_messages
from .config import settings
from .drainer import BurstDrainer
from .watcher import ConnectivityWatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("ferry")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
SEEDS_FILE = BASE_DIR / "seeds" / "tasks.json"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    app.state.clients = Clients()
    app.state.watcher = ConnectivityWatcher()
    app.state.drainer = BurstDrainer(app.state.clients, app.state.watcher)
    app.state.tasks = [
        asyncio.create_task(app.state.watcher.run()),
        asyncio.create_task(app.state.drainer.run()),
    ]
    log.info(
        "Ferry up. local=%s cloud=%s keys=%d router=%s",
        settings.local_model,
        settings.cerebras_model,
        len(settings.cerebras_api_keys),
        settings.router_mode,
    )
    try:
        yield
    finally:
        for t in app.state.tasks:
            t.cancel()
        await app.state.watcher.aclose()
        await app.state.clients.aclose()
        await db.close()


app = FastAPI(title="Ferry", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# OpenAI-compatible surface
# ---------------------------------------------------------------------------
@app.get("/v1/models")
async def list_models():
    data = [
        {"id": m, "object": "model", "created": 0, "owned_by": "ferry"}
        for m in settings.service_models
    ]
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    # Ferry exposes "ferry" (auto-routing) plus optional passthrough extras
    # (EXTRA_LOCAL_MODELS). Picking an extra bypasses the router entirely and
    # talks straight to that local model. Any other/stale id is treated as ferry.
    requested = str(body.get("model") or "")
    model = "ferry"
    stream = bool(body.get("stream", False))
    conversation = body.get("conversation_id") or request.headers.get("x-conversation-id")

    clients: Clients = request.app.state.clients

    if requested in settings.extra_local_models:
        if not stream:
            content = await _safe_local_complete(
                clients, messages, requested, backend_model=requested
            )
            return JSONResponse(sse.completion(requested, content))
        return StreamingResponse(
            _stream_local(clients, messages, requested, backend_model=requested),
            media_type="text/event-stream",
        )

    # Non-streaming calls are Open WebUI utilities (title/tag generation). Keep
    # them local & instant so they never pollute the backlog.
    if not stream:
        content = await _safe_local_complete(clients, messages, model)
        return JSONResponse(sse.completion(model, content))

    route, reason = await router.decide(clients, messages)
    log.info("route=%s (%s)", route, reason)

    if route == "local":
        return StreamingResponse(
            _stream_local(clients, messages, model),
            media_type="text/event-stream",
        )

    # Cloud path: trim the (possibly huge) Open WebUI history once here so every
    # downstream call — router, inline agent, queued drain, and each orchestrator
    # worker — inherits a bounded payload that fits Cerebras's token budget.
    messages = compact_cloud_messages(messages)

    cloud_mode, cloud_reason = await router.decide_cloud_mode(clients, messages)
    internal_route = f"{cloud_mode}: {cloud_reason}"
    log.info("cloud_mode=%s (%s)", cloud_mode, cloud_reason)

    watcher: ConnectivityWatcher = request.app.state.watcher
    if await watcher.can_burst_now():
        return StreamingResponse(
            _stream_agentic(clients, messages, model, cloud_mode),
            media_type="text/event-stream",
        )

    # Cloud path: register the live queue BEFORE enqueue so the drainer can't
    # race ahead of us, then hold the SSE stream open.
    tid = uuid.uuid4().hex
    queue = registry.register(tid)
    await db.enqueue(
        messages,
        route=internal_route,
        source="chat",
        conversation=conversation,
        task_id=tid,
        agentic=True,
    )
    # Fire-and-forget: ping the user that this one is parked until a window opens.
    backlog = (await db.counts()).get("queued", 0)
    asyncio.create_task(notify.queued(backlog))
    return StreamingResponse(
        _stream_queued(tid, queue, model, position=backlog),
        media_type="text/event-stream",
    )


async def _stream_local(
    clients: Clients, messages: list[dict], model: str, backend_model: str | None = None
):
    yield sse.chunk(model, {"role": "assistant"})
    try:
        async for delta in clients.ollama_stream(
            messages, backend_model or settings.local_model
        ):
            yield sse.chunk(model, {"content": delta})
    except httpx.TimeoutException:
        yield sse.chunk(
            model,
            {
                "content": (
                    "\n\n[local model timed out after "
                    f"{settings.local_timeout_seconds:g}s]"
                )
            },
        )
    except httpx.HTTPStatusError as exc:
        # llama-server answers 503 while it loads the model after a (re)boot.
        if exc.response.status_code == 503:
            yield sse.chunk(model, {"content": _WARMING_UP})
        else:
            yield sse.chunk(model, {"content": f"\n\n[local model error: {exc}]"})
    except Exception as exc:  # noqa: BLE001
        yield sse.chunk(model, {"content": f"\n\n[local model error: {exc}]"})
    yield sse.chunk(model, {}, finish_reason="stop")
    yield sse.DONE


def _queue_receipt(position: int) -> str:
    """The backlog receipt shown the instant a hard prompt is parked."""
    pos = f" · **#{position}** in the on-device backlog" if position else ""
    return (
        f"⏳ **Queued**{pos}\n\n"
        "This one needs the big model, so Ferry is holding it **on-device** until a "
        "connection window opens — then it bursts to **Gemma 4 on Cerebras** and the "
        "answer streams right back into this message. You can close the app; you'll "
        "get a notification when it lands."
    )


def _fmt_wait(seconds: float) -> str:
    s = int(seconds)
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s"


async def _stream_queued(tid: str, queue: asyncio.Queue, model: str, position: int = 0):
    """Hold the bubble open: a backlog receipt now, the Cerebras answer when it lands."""
    t0 = time.monotonic()
    yield sse.chunk(model, {"role": "assistant"})
    yield sse.chunk(model, {"content": _queue_receipt(position)})
    started = False
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=settings.heartbeat_interval)
            except asyncio.TimeoutError:
                yield sse.heartbeat()
                continue
            kind = item["type"]
            if kind == "status":
                yield sse.chunk(model, {"content": f"\n_{item['text']}_\n"})
                continue
            if kind == "token":
                if not started:
                    waited = _fmt_wait(time.monotonic() - t0)
                    yield sse.chunk(model, {"content": (
                        f"\n\n---\n📡 **Connection window open** — bursted to Gemma 4 on "
                        f"Cerebras after **{waited}** in the backlog.\n\n"
                    )})
                    started = True
                yield sse.chunk(model, {"content": item["text"]})
            elif kind == "done":
                yield sse.chunk(model, {}, finish_reason="stop")
                yield sse.DONE
                return
            elif kind == "error":
                yield sse.chunk(model, {"content": f"\n\n[error: {item['text']}]"})
                yield sse.chunk(model, {}, finish_reason="stop")
                yield sse.DONE
                return
    finally:
        registry.unregister(tid)


async def _stream_agentic(
    clients: Clients, messages: list[dict], model: str, cloud_mode: str
):
    """Run an agentic Cerebras path inline (online) and stream steps + answer."""
    yield sse.chunk(model, {"role": "assistant"})
    label = (
        "Gemma 4 multi-agent fan-out"
        if cloud_mode == "multi_agent"
        else "Gemma 4 agent with tools"
    )
    yield sse.chunk(model, {"content": f"\n_{label}_\n\n"})
    try:
        runner = (
            clients.cerebras_multiverse
            if cloud_mode == "multi_agent"
            else clients.cerebras_agent
        )
        async for kind, text in runner(messages):
            if kind == "status":
                yield sse.chunk(model, {"content": f"\n_{text}_\n\n"})
            else:
                yield sse.chunk(model, {"content": text})
    except Exception as exc:  # noqa: BLE001
        yield sse.chunk(model, {"content": f"\n\n[agent error: {exc}]"})
    yield sse.chunk(model, {}, finish_reason="stop")
    yield sse.DONE


_WARMING_UP = (
    "The local model is still warming up — it reloads for a few minutes after a "
    "restart. Try again shortly."
)


async def _safe_local_complete(
    clients: Clients, messages: list[dict], model: str, backend_model: str | None = None
) -> str:
    try:
        return await clients.ollama_complete(messages, backend_model or settings.local_model)
    except httpx.TimeoutException:
        return f"[local model timed out after {settings.local_timeout_seconds:g}s]"
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 503:
            return _WARMING_UP
        return f"[local model unavailable: {exc}]"
    except Exception as exc:  # noqa: BLE001
        return f"[local model unavailable: {exc}]"


# ---------------------------------------------------------------------------
# Dashboard + demo control
# ---------------------------------------------------------------------------
@app.get("/api/status")
async def status(request: Request):
    watcher: ConnectivityWatcher = request.app.state.watcher
    return {
        "online": watcher.is_online(),
        "can_burst": watcher.can_burst(),
        "override": watcher.override,
        "real_reachable": watcher.real_reachable,
        "counts": await db.counts(),
        "keys": len(settings.cerebras_api_keys),
        "local_model": settings.local_model,
        "cloud_model": settings.cerebras_model,
        "router_mode": settings.router_mode,
    }


@app.get("/api/tasks")
async def tasks(limit: int = 200):
    return await db.list_tasks(limit=limit)


@app.get("/api/metrics")
async def metrics(request: Request):
    """Speed scoreboard: burst stats since the window opened."""
    since = getattr(request.app.state, "window_opened_at", None)
    m = await db.metrics(since=since)
    m["window_opened_at"] = since
    m["keys"] = len(settings.cerebras_api_keys)
    m["cloud_model"] = settings.cerebras_model
    return m


@app.get("/api/files/{run_id}/{file_path:path}")
async def generated_file(run_id: str, file_path: str):
    target = _generated_file_path(run_id, file_path)
    if target is None:
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target, filename=target.name)


@app.post("/demo/online/{state}")
async def demo_online(state: str, request: Request):
    watcher: ConnectivityWatcher = request.app.state.watcher
    mapping = {"true": True, "false": False, "auto": None}
    if state not in mapping:
        return JSONResponse({"error": "state must be true|false|auto"}, status_code=400)
    watcher.set_override(mapping[state])
    # Stamp when the burst window opens so the scoreboard can measure window-to-clear.
    if mapping[state] is True:
        request.app.state.window_opened_at = db.now_iso()
    elif mapping[state] is False:
        request.app.state.window_opened_at = None
    return {"online": watcher.is_online(), "override": watcher.override}


@app.post("/demo/seed")
async def demo_seed(request: Request, count: int = 100):
    templates = json.loads(SEEDS_FILE.read_text()) if SEEDS_FILE.exists() else []
    if not templates:
        return JSONResponse({"error": "no seed templates found"}, status_code=500)
    seeded = 0
    for i in range(count):
        tpl = templates[i % len(templates)]
        prompt = f"{tpl['prompt']} (#{i + 1})" if count > len(templates) else tpl["prompt"]
        await db.enqueue(
            [{"role": "user", "content": prompt}],
            route="single_agent: seed",
            source="seed",
            priority=tpl.get("priority", 5),
            agentic=True,
        )
        seeded += 1
    return {"seeded": seeded, "counts": await db.counts()}


@app.post("/demo/clear")
async def demo_clear():
    await db.clear()
    return {"counts": await db.counts()}


@app.post("/demo/drain")
async def demo_drain(request: Request):
    if not getattr(request.app.state, "window_opened_at", None):
        request.app.state.window_opened_at = db.now_iso()
    drainer: BurstDrainer = request.app.state.drainer
    drained = await drainer.drain_once()
    return {"drained": drained}


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------
@app.get("/dashboard")
async def dashboard():
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/demo")
async def demo():
    return FileResponse(STATIC_DIR / "demo.html")


@app.get("/how")
async def how():
    return FileResponse(STATIC_DIR / "how.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return RedirectResponse("/dashboard")


def _generated_file_path(run_id: str, file_path: str) -> Path | None:
    if not (8 <= len(run_id) <= 64 and all(c in "0123456789abcdef" for c in run_id)):
        return None
    base = Path(settings.generated_files_dir).resolve()
    run_dir = (base / run_id).resolve()
    target = (run_dir / file_path).resolve()
    try:
        target.relative_to(run_dir)
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target
