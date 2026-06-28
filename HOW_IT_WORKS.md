# How Ferry Works

Ferry is a **local-first AI gateway for intermittent connectivity**. It lets a
normal chat app (Open WebUI) stay useful when the network is flaky: easy prompts
are answered instantly by a small model running on the device, and hard prompts
are parked in an on-device backlog that **bursts to a large model on Cerebras the
instant a connection window opens** — even a few seconds is enough.

This document explains the whole system end to end: the architecture, the
request lifecycle, every component, the data model, and the one non-obvious trick
that makes the magic moment work.

---

## 1. The problem

In a subway, on a flight, in the field, or anywhere with spotty signal, AI apps
break. They either need a constant connection or fall back to a weak offline
model. And the connection you *do* get arrives in short, unpredictable bursts —
too brief for a normal cloud LLM to answer anything useful, because the TLS
handshake alone can eat the window.

Two things make Ferry's approach work:

1. **Cerebras is fast enough that a 5-second window finishes real work.** A full
   answer comes back in a fraction of a second, so a brief blip of signal is
   genuinely productive.
2. **A warm, persistent connection and a ranked backlog** mean no second of
   signal is wasted on setup or low-value work.

---

## 2. Architecture at a glance

```
                          DEVICE (Jetson / laptop — local-first)
   ┌───────────────────────────────────────────────────────────────────────┐
   │                                                                         │
   │   Open WebUI  ──►  Ferry (FastAPI, OpenAI-compatible)  ──►  Ollama      │
   │   (browser)        • router: answer local vs queue          LiquidAI    │
   │       ▲            • /v1/chat/completions (SSE)              (on-device) │
   │       │            • connectivity watcher                               │
   │       │            • burst drainer                                      │
   │       │                      │                                          │
   │       │                      ▼                                          │
   │       │            SQLite backlog (WAL, FIFO)                           │
   │       └──── answer streamed back into the same chat bubble ────┐        │
   │                                                                │        │
   └────────────────────────────────────────────────────────────────────────┘
                                  │  only during a connection window
                                  ▼
                       Cerebras  gemma-4-31b  (~0.2s full answers)
                       OpenAI-compatible API, warm HTTP/2 + key pool
```

Everything speaks the **OpenAI Chat Completions API**, so a single code path talks
to both the local model and the cloud model, and Open WebUI treats Ferry as if it
were just a normal model endpoint. No custom Open WebUI plugin is required.

It's **Liquid local + Gemma 4 cloud**: `LiquidAI/lfm2.5-1.2b-instruct` offline,
and `gemma-4-31b` in the burst.

---

## 3. The request lifecycle

### Easy prompt (answered locally, now)

```
User → Open WebUI → POST /v1/chat/completions (stream)
     → Ferry router decides "local"
     → Ferry streams tokens from Ollama (LiquidAI/lfm2.5-1.2b-instruct)
     → tokens flow straight back to the bubble.   No network needed.
```

### Hard prompt (queued, then delivered on the next window)

```
User → Open WebUI → POST /v1/chat/completions (stream)
     → Ferry router decides "cloud"
     → Ferry writes the task to SQLite (status=queued)
     → Ferry HOLDS THE SSE STREAM OPEN, emits a placeholder:
          "⏳ Queued. I'll answer the moment a connection window opens."
     ... time passes, device offline ...
     → watcher detects a window is open
     → drainer pulls the task, calls Cerebras gemma-4-31b
     → tokens are pushed into the still-open stream
     → the answer appears in the SAME chat bubble, then [DONE]
```

---

## 4. The one clever bit: held-open SSE delivery

The hard part of "answer later, in the same conversation" is usually delivering an
async result back into the chat UI. Most approaches need a plugin or a message
injection API.

Ferry avoids all of that. Open WebUI makes **one streaming request** and shows a
"generating…" bubble while the SSE stream is open. So for a hard task, Ferry
simply **does not close the stream**:

1. Emit an assistant `role` chunk, then a placeholder content chunk.
2. Register an in-memory `asyncio.Queue` keyed by the task id
   ([registry.py](ferry/registry.py)).
3. Keep the response generator alive, sending SSE heartbeats
   (`: keepalive`) so proxies don't drop the connection
   ([main.py](ferry/main.py) → `_stream_queued`).
4. When the drainer later calls Cerebras for that task, it **pushes tokens into
   that queue**, which the still-open generator forwards as OpenAI chunks — into
   the original bubble — and finally sends `[DONE]`.

Because the registered queue is keyed by task id, the drainer can deliver to a
live, waiting bubble *or* simply persist to SQLite (for seeded/background tasks
with no open connection). Both paths share one code path.

> **Why a SQLite backlog *and* an in-memory queue?** The queue is the live
> delivery channel for an open bubble; SQLite is durability and the source of
> truth for ranking, the dashboard, and recovery after a restart. If the browser
> closes, the task still completes and the answer is saved.

---

## 5. Components

All code lives in the [`ferry/`](ferry/) package.

| File | Responsibility |
|---|---|
| [main.py](ferry/main.py) | FastAPI app: `/v1/chat/completions`, `/v1/models`, demo controls, the dashboard/demo pages, and the held-open SSE generators. |
| [router.py](ferry/router.py) | Decides **local vs cloud** for each message. |
| [db.py](ferry/db.py) | SQLite (WAL) backlog — FIFO enqueue, status transitions, recovery. |
| [clients.py](ferry/clients.py) | Warm Ollama + Cerebras HTTP clients; **API-key pool**. |
| [watcher.py](ferry/watcher.py) | Connectivity watcher — is a window open right now? |
| [drainer.py](ferry/drainer.py) | Burst drainer — fans the backlog out to Cerebras in parallel. |
| [registry.py](ferry/registry.py) | In-memory `task_id → asyncio.Queue` for live delivery. |
| [sse.py](ferry/sse.py) | OpenAI-compatible SSE chunk/heartbeat/completion helpers. |
| [config.py](ferry/config.py) | All runtime config, loaded from `.env`. |

### 5.1 Router

The router runs on **every** message and is fully automatic — the user never
flips a toggle. Modes (`ROUTER_MODE`):

- **`heuristic`** (default) — deterministic length/keyword rule. Demo-safe: a long
  prompt or one containing words like *analyze, compare, write a, refactor,
  design, plan* routes to cloud; short/simple stays local.
- **`llm`** — asks the local edge model to classify `LOCAL` vs `CLOUD`, falling
  back to the heuristic if it stalls.
- **`always_cloud` / `always_local`** — force a route (handy for demos).

Open WebUI sees one model: `ferry`. Ferry decides the route internally so the
user never chooses local/cloud/agent mode. Cloud-worthy prompts always go to
Gemma 4 with tools available; Ferry chooses either one agent or parallel
sub-agents plus synthesis.

> Non-streaming requests (Open WebUI's background title/tag generation) are always
> answered locally, so utility calls never pollute the backlog.

### 5.2 Backlog (SQLite, WAL, FIFO)

A single file-based SQLite database, separate from Open WebUI's own DB. WAL mode
allows concurrent readers/writers. Tasks drain **FIFO** (in the order they
arrived) — the value-density ranking from the original design was intentionally
dropped in favor of FIFO + massive parallelism (see §6). On startup, any task
left in `sending` by a crash or a window that closed mid-drain is reset to
`queued` so nothing is lost.

### 5.3 Connectivity watcher

Polls a lightweight endpoint every ~2.5s ([watcher.py](ferry/watcher.py)). **Any**
HTTP response — even a `401` — means the network is reachable. A manual override
(`/demo/online/{true|false|auto}`) can force the window open or shut so the burst
is reliable on stage.

### 5.4 Burst drainer

Triggered whenever the watcher reports online ([drainer.py](ferry/drainer.py)):

- Pulls all `queued` tasks (FIFO) and fires them at Cerebras **concurrently**,
  bounded by a semaphore (`DRAIN_CONCURRENCY`).
- Streams each answer back (into the live bubble if one is waiting) and persists
  it (`status=done`).
- If the window closes mid-drain, remaining tasks are left `queued` for the next
  window. Failures are retried up to `MAX_ATTEMPTS`, then marked `error`.

### 5.5 Clients & the API-key pool

One warm `httpx.AsyncClient` per backend ([clients.py](ferry/clients.py)). The
Cerebras client uses **HTTP/2 with keep-alive**, so only inference happens inside
the window — never a cold TLS handshake. It **round-robins across a pool of API
keys** (`CEREBRAS_API_KEYS`, one per teammate), and retries on the next key on a
`429`. This is what lets a small window drain ~100 tasks (see §6).

A cross-model detail: `reasoning_effort: "none"` is rejected by some models
(e.g. `gpt-oss-120b`), so Ferry omits the parameter unless a real effort
(`low|medium|high`) is configured. Reasoning is off by default everywhere, so the
behaviour is identical and portable.

---

## 6. Why FIFO + a key pool (not priority ranking)

Each hackathon participant's Cerebras key is limited to **100 RPM / 100K TPM**.
Firing 100 requests into a 5-second window on a single key would hit the rate
limit. The fix is the **key pool**: round-robining across all three teammates'
keys gives ~300 RPM of headroom, enough to drain a ~100-task backlog in one short
window.

That reframes the "wow": instead of carefully ordering tasks by value, Ferry shows
**raw parallel throughput** — a full backlog answered near-simultaneously the
moment signal returns. FIFO keeps it simple and the demo legible.

---

## 7. Data model

```sql
tasks(
  id            TEXT PRIMARY KEY,   -- uuid, registered before insert (no races)
  conversation  TEXT,
  prompt        TEXT,               -- last user message, for display
  messages      TEXT,               -- full OpenAI messages array (JSON)
  priority      INTEGER,            -- reserved; unused under FIFO
  est_tokens    INTEGER,
  status        TEXT,               -- queued | sending | done | error
  response      TEXT,
  error         TEXT,
  route         TEXT,               -- the router's reason
  source        TEXT,               -- chat | seed
  attempts      INTEGER,
  created_at    TEXT,
  sent_at       TEXT,
  completed_at  TEXT
)
```

The full `messages` array is stored (not just the last prompt), so the drainer can
replay complete multi-turn context to Cerebras.

---

## 8. HTTP surface

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/chat/completions` | OpenAI-compatible chat (triage + SSE streaming) |
| GET | `/v1/models` | lists `ferry` |
| GET | `/api/status` | window state + backlog counts (dashboard/demo poll this) |
| GET | `/api/tasks` | the backlog |
| POST | `/demo/online/{true\|false\|auto}` | force / release the connection window |
| POST | `/demo/seed?count=N` | preload N hard tasks |
| POST | `/demo/drain` | force a drain now |
| POST | `/demo/clear` | empty the backlog |
| GET | `/demo` | interactive black-and-white demo UI |
| GET | `/how` | animated architecture diagram |
| GET | `/dashboard` | operator backlog view |

### The three front-ends

- **`/demo`** ([static/demo.html](static/demo.html)) — a minimal, shadcn-style
  chat for showing the whole flow: type → route on-device or queue → toggle the
  connection → watch the burst land in the same bubble.
- **`/how`** ([static/how.html](static/how.html)) — a looping animated diagram of
  the flow (easy tasks return instantly; hard tasks pile into the backlog while
  the cloud route is dashed/offline; the window opens and the backlog bursts to
  Cerebras in parallel).
- **`/dashboard`** ([static/dashboard.html](static/dashboard.html)) — the operator
  view: live counts, status badges, and window/seed/clear controls.

---

## 9. Models & configuration

| Role | Model | Where |
|---|---|---|
| On-device (offline brain) | `LiquidAI/lfm2.5-1.2b-instruct` | Ollama, OpenAI-compatible at `localhost:11434/v1` |
| Cloud (burst brain) | `gemma-4-31b` | Cerebras, `https://api.cerebras.ai/v1` |

Configuration is environment-driven ([.env.example](.env.example)). Key settings:
`CEREBRAS_API_KEYS` (comma-separated pool), `CEREBRAS_MODEL`, `LOCAL_MODEL`,
`ROUTER_MODE`, `WATCHER_INTERVAL`, `DRAIN_CONCURRENCY`, `MAX_ATTEMPTS`, `DB_PATH`.

> Keep `CEREBRAS_MODEL=gemma-4-31b`; every cloud burst uses Gemma 4 with tools
> available.

---

## 10. Verified behaviour

Measured locally against Cerebras:

- **Single call:** full answer in **~0.22s** (time-to-first-token ≈ total).
- **Parallel backlog burst:** **8 queued tasks drained in ~0.9s** of inference,
  0 errors, all delivered with real answers.
- **Held-open bubble:** a single chat bubble showed the placeholder, then the
  Cerebras answer streamed into the *same* response — the demo's signature moment.
- **Local path:** `LiquidAI/lfm2.5-1.2b-instruct` generates on-device (small enough
  for responsive local demos — warm it once before recording).

---

## 11. Failure & edge handling

- **Cold-connection risk** → warm, persistent HTTP/2 connection; only inference
  happens in the window.
- **Window closes mid-drain** → in-flight tasks finish or revert to `queued`;
  leftovers ride the next window.
- **Rate limit (429)** → retry on the next key in the pool.
- **Crash recovery** → `sending` tasks reset to `queued` on startup.
- **Browser closes while queued** → the task still completes and is saved to
  SQLite; the answer is available via the dashboard.
- **Local model down** → graceful inline error; cloud path unaffected.

---

## 12. Running it

Fully local, no Docker (see [README.md](README.md) for full setup):

```bash
ollama serve & ollama pull LiquidAI/lfm2.5-1.2b-instruct
cp .env.example .env                            # add CEREBRAS_API_KEYS
uvicorn ferry.main:app --port 8080              # Ferry
open-webui serve --port 3000                    # chat UI, pointed at :8080/v1
```

Then open `http://localhost:8080/demo` (or `/how`, `/dashboard`).

---

## 13. Roadmap

- **Track 1 stretch (multi-agent + multimodal):** give the burst a web-search tool
  (Exa) via Gemma 4 tool-calling, decompose a hard task into parallel sub-agents
  that search + synthesize, and add a multimodal queued task (photo of a sign or
  document → Gemma 4 vision).
- Per-window drain caps for a sharper demo; richer priority modes behind the
  existing `priority` column.
