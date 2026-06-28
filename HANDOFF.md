# Handoff — Mac Side

Welcome aboard. This is everything you need to take the **Mac side** of **Ferry** while
Ethan runs the **Jetson side**. Read `CLAUDE.md` first for the project overview; this doc
is your onboarding + scope.

## What Ferry is (60 seconds)
Open WebUI talks to **Ferry** (an OpenAI-compatible FastAPI service), not to a model
directly. Ferry **routes** each message: easy → a **local model** (instant, offline);
hard → an on-device **SQLite backlog** that **bursts to Gemma 4 on Cerebras** the moment a
connection window opens, streaming the answer back into the **same chat bubble** (a
held-open SSE stream — no Open WebUI plugin needed). Full detail: `HOW_IT_WORKS.md`.

## The split — your lane
One codebase, two deployments (see the table in `CLAUDE.md`). You own:
- **The Mac deployment** — the "wifi flicker on a laptop" demo, local model `gemma4:e2b`.
- **The shared Ferry app code** — this is the important part. The `ferry/` service, the
  `/demo` & `/how` pages, and **new features** all live in the shared repo and run on
  **both** deployments. You're the primary dev on the codebase.

Ethan owns the Jetson hub (Liquid model, systemd, Open WebUI Docker, hotspot networking).

> ⚠️ **Shared code = coordinate.** Editing `ferry/*.py`, `static/*.html`, or `seeds/`
> changes what runs on Ethan's Jetson too. Branch for anything non-trivial; the Jetson
> picks up changes via `git pull` + restart. Only `.env` differs per machine.

## Prerequisites (Mac)
- Homebrew + **Ollama**.
- **Python 3.10–3.12** for Ferry (avoid 3.13/3.14 — some deps lack wheels) **and**
  **Python 3.11** for Open WebUI.
- A **Cerebras key** (from Ethan); an **Exa key** too if you'll do Track 1.

## Get running on your Mac (~10 min)
```bash
git clone https://github.com/habodoni/cerebras-gemma-hack.git
cd cerebras-gemma-hack

# 1. Local model
ollama serve &                 # wait until it's listening
ollama pull gemma4:e2b         # if unavailable, pull any small model (e.g. gemma3:4b)
                               # and set LOCAL_MODEL in .env to match

# 2. Config (NEVER commit .env — it's gitignored)
cp .env.example .env           # paste CEREBRAS_API_KEYS (+ EXA_API_KEY for Track 1)

# 3. Ferry  (use a 3.10–3.12 interpreter)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn ferry.main:app --reload --port 8080
#   dashboard → http://localhost:8080/dashboard · demo → /demo · diagram → /how
#   sanity: curl localhost:8080/v1/models → lists ferry / ferry-local / ferry-cloud

# 4. Open WebUI — needs Python 3.11, in its OWN venv
deactivate
python3.11 -m venv .venv-webui && source .venv-webui/bin/activate
pip install open-webui
OPENAI_API_BASE_URL=http://localhost:8080/v1 OPENAI_API_KEY=ferry \
ENABLE_OLLAMA_API=false WEBUI_AUTH=false open-webui serve --port 3000
#   chat → http://localhost:3000 → pick the "ferry" model
#   no models? Admin → Connections → add http://localhost:8080/v1, key "ferry"
```
Verify: short prompt answers instantly (local `gemma4:e2b`); a long/"analyze…" prompt
shows `⏳ Queued` then bursts to Cerebras. Toggle the window with the dashboard buttons or
`curl -X POST localhost:8080/demo/online/false|true|auto`.

## Already verified (so you know the baseline works)
- Single Cerebras call: full answer in **~0.22s**.
- Parallel burst: **8 queued tasks drained in ~0.9s**, 0 errors.
- Held-open bubble: placeholder → Cerebras answer in one stream.
- Local `gemma4:e2b` generates on-device.
- The Jetson side is **live** end-to-end (Liquid local + Cerebras burst through Open WebUI).

## Cloud model status (important for the prize)
The shared key currently sees only `zai-glm-4.7` and `gpt-oss-120b` — **`gemma-4-31b` isn't
live yet** (Gemma 4 preview opens during the hackathon). So Ferry bursts to `gpt-oss-120b`
for now. The hackathon **requires Gemma 4** as the central model: the moment the key sees
`gemma-4-31b`, set `CEREBRAS_MODEL=gemma-4-31b` in `.env` and restart. Keep checking.

## Open work (good places to start)
- **Track 1 (the $2K prize): Exa web-search + multi-agent burst.** Greenfield — `config.py`
  reads `EXA_API_KEY` (add yours to `.env`), but the `web_search` tool, the Exa HTTP call,
  and the Cerebras tool-definition aren't built yet (`httpx` is already a dep). Give the
  burst a `web_search` tool (Gemma 4 tool-calling) and let a hard task fan into parallel
  sub-agents that search + synthesize.
- **Multimodal:** an image queued task → Gemma 4 vision on Cerebras (and `gemma4:e2b` is
  multimodal locally).
- **Polish** the `/demo` and `/how` pages (black-and-white shadcn style — keep it minimal).
- **The ≤60s demo video** showing Cerebras speed (side-by-side vs a GPU provider is a plus).

## Coordination & security
- **Repo:** `github.com/habodoni/cerebras-gemma-hack`, default branch `main`. Branch for
  non-trivial work; the Jetson pulls from `main`.
- **Secrets:** `.env` is gitignored — never commit keys. The Cerebras key was shared in
  chat during setup; rotate it after the hackathon to be safe.
- **Don't break the contract:** Open WebUI relies on Ferry's OpenAI-compatible surface
  (`/v1/chat/completions`, `/v1/models`) and the held-open SSE behavior. Test both routes
  after changes.

Questions on the Jetson side → Ethan / `JETSON_STARTUP.md`. Architecture → `HOW_IT_WORKS.md`.
