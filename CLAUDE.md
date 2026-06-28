# Ferry — Project Guide

Ferry is a **local-first AI gateway for intermittent connectivity**. Open WebUI talks
to Ferry (an OpenAI-compatible FastAPI service). Ferry answers easy prompts with a
**local model** and queues hard ones in an on-device SQLite backlog, **bursting them to
Gemma 4 on Cerebras** the moment a connection window opens — streaming the answer back
into the same chat bubble. Cerebras × Gemma 4 hackathon.

---

## ⚠️ TWO DEPLOYMENTS — read this first

There is **one Ferry codebase** (`ferry/`) but **two deployments**. They share all code
and differ only in **config (`.env`) + host + the local model**. Always know which side
you're working on.

| | 🖥️ **MAC FLOW** | 🤖 **JETSON FLOW** |
|---|---|---|
| **Owner** | Mac-side teammate | Ethan |
| **Purpose** | wifi-flicker laptop demo **+ Ferry app development** | the real product: edge hub, weak client connects over a hotspot |
| **Runs on** | a MacBook | Jetson Orin Nano (`ethan@ethan-desktop`, Ubuntu 22.04, JetPack 6, Python 3.10) |
| **Local model** | `gemma4:e2b` (Ollama) | `bcluzel/LFM2.5-1.2B-Instruct:Q4_K_M` — "Liquid" (Ollama) |
| **Ferry** | `uvicorn` native | **systemd** `ferry.service` |
| **Open WebUI** | native (pip), `--port 3000` | **Docker** container `open-webui`, `:3000` |
| **Cloud model** | `gemma-4-31b` on Cerebras (`gpt-oss-120b` fallback) | same |
| **"Window" control** | toggle Mac wifi, or `/demo/online` | `/demo/online` toggle (ethernet uplink later) |
| **Config template** | `.env.example` | `.env.jetson.example` |
| **Status** | built + verified on Mac | **deployed + verified live on the Jetson** |

**The Ferry code is SHARED.** A change to `ferry/*.py`, `static/*.html`, or `seeds/`
affects **both** deployments. Only `.env` (gitignored) and host setup differ. Coordinate
before touching shared code.

## Who owns what
- **Mac side (teammate):** the Mac deployment **and** the shared Ferry app code — router,
  the `/demo` & `/how` pages, drainer, and new features (Track 1 Exa web-search +
  multi-agent, multimodal). The Mac is the dev environment for the codebase.
- **Jetson side (Ethan):** the Jetson hub deployment — Ollama+Liquid, Ferry systemd
  service, Open WebUI (Docker), networking/hotspot, on-device model.

---

## Repo map
- `ferry/` — the service: `config.py`, `db.py`, `clients.py`, `router.py`, `watcher.py`,
  `drainer.py`, `registry.py`, `sse.py`, `main.py`
- `static/` — `demo.html` (`/demo`), `how.html` (`/how`), `dashboard.html` (`/dashboard`)
- `seeds/tasks.json` — demo backlog tasks
- `.env.example` (Mac) / `.env.jetson.example` (Jetson) — config templates; **`.env` is gitignored**
- Docs: `HOW_IT_WORKS.md`, `JETSON_DEPLOY.md`, `JETSON_STARTUP.md`, `HANDOFF.md`, `README.md`

## Models & ports
- **Ports:** Ferry `8080` · Open WebUI `3000` · Ollama `11434`
- **Ferry exposes 4 models:** `ferry` (auto-route), `ferry-local` (force local),
  `ferry-cloud` (force queue→burst), `ferry-agent` (Cerebras tool-calling with
  `web_search` via Exa + `run_code`/file-creation via E2B — see `ferry/tools.py`)
- **Cloud:** `gemma-4-31b` on Cerebras — the prize model, live and verified (~0.2s answers,
  clean tool-calling). Needs Gemma 4 preview access on the key; `gpt-oss-120b` is the fallback.

## Run it (Mac)
```bash
ollama serve &
ollama pull gemma4:e2b          # if unavailable, use a small model + set LOCAL_MODEL
cp .env.example .env            # add CEREBRAS_API_KEYS (+ EXA_API_KEY for Track 1)
python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
uvicorn ferry.main:app --port 8080
# Open WebUI (native, Python 3.11, own venv): open-webui serve --port 3000 → http://localhost:8080/v1
```
Full Mac steps in `HANDOFF.md`. Jetson runs as services — see `JETSON_STARTUP.md`.

## Gotchas
- Open WebUI **native** needs Python **3.11**; the Jetson only has 3.10, so it runs Open
  WebUI via **Docker** there.
- **Never commit `.env` / API keys** (gitignored). Rotate keys if exposed in chat/screens.
- `reasoning_effort=none` is rejected by `gpt-oss-120b`; Ferry omits the param unless a
  real effort is set (`clients.py`).
- Cloud is a **key pool** (currently one key) — add teammates' keys (comma-separated in
  `CEREBRAS_API_KEYS`) to widen the parallel burst beyond one key's 100 RPM.

## More docs
- `HOW_IT_WORKS.md` — deep architecture & the held-open-SSE trick
- `JETSON_DEPLOY.md` — full Jetson deploy runbook
- `JETSON_STARTUP.md` — Jetson start/stop/restart/check ops
- `HANDOFF.md` — Mac-side onboarding for the teammate
