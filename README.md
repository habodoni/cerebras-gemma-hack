# 🛶 Ferry

**Local-first AI for intermittent connectivity.** Cerebras × Gemma 4 Hackathon.

You chat in Open WebUI as normal — but it's not talking to a model, it's talking
to **Ferry**. Ferry answers easy prompts instantly with a local edge model
(`LiquidAI/lfm2.5-1.2b-instruct` via Ollama), and queues the hard ones in an on-device backlog. The
moment a connection window opens — even a few seconds — Ferry fans the backlog
out **in parallel** to `gemma-4-31b` on Cerebras and streams each answer back
into the same chat bubble you were waiting on.

It's **Liquid local + Gemma 4 cloud**: Liquid handles offline edge replies, and
Gemma 4 handles cloud bursts with tools.

```
Open WebUI ──► Ferry (FastAPI, OpenAI-compatible) ──► Ollama  LiquidAI/lfm2.5-1.2b-instruct
                 │  router: local now vs queue
                 └─ SQLite backlog (FIFO) ──► Cerebras gemma-4-31b  (burst, parallel)
                       ▲ connectivity watcher    answers stream back into the bubble
```

## Why it works
- **Same language both ways** — local and cloud both speak the OpenAI Chat API, so
  one code path talks to either, and Open WebUI treats Ferry as a normal model.
- **Held-open SSE** — a queued chat keeps its stream open showing a placeholder;
  when the drainer gets the Cerebras answer it streams into the *same* bubble. No
  Open WebUI plugin needed.
- **Key pool** — Ferry round-robins across every teammate's Cerebras key, so we
  fan out wider than one key's 100 RPM and drain ~100 tasks in one short window.

## Quickstart (fully local — no Docker)
Three native processes: Ollama, Ferry, Open WebUI.
```bash
# 1. Local model
brew install ollama && ollama serve &
ollama pull LiquidAI/lfm2.5-1.2b-instruct

# 2. Config
cp .env.example .env      # paste your CEREBRAS_API_KEYS (comma-separated)

# 3. Ferry (port 8080)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn ferry.main:app --reload --port 8080
#    backlog dashboard → http://localhost:8080/dashboard

# 4. Open WebUI, native via pip (port 3000), pointed at Ferry
pip install open-webui
OPENAI_API_BASE_URL=http://localhost:8080/v1 \
OPENAI_API_KEY=ferry \
ENABLE_OLLAMA_API=false \
ENABLE_EVALUATION_ARENA_MODELS=false \
WEBUI_AUTH=false \
open-webui serve --port 3000
#    chat → http://localhost:3000  → pick the "ferry" model
```
Open WebUI needs Python 3.11; if it clashes with Ferry's venv, install it in its
own venv. It only talks to Ferry — Ollama stays hidden behind the router.

## Optional: Docker on the Jetson
For the Jetson deploy, `docker-compose.yml` bundles just **Ollama + Ferry** (Open
WebUI still runs natively via pip, as above):
```bash
cp .env.example .env        # add CEREBRAS_API_KEYS
docker compose up --build
docker compose exec ollama ollama pull LiquidAI/lfm2.5-1.2b-instruct
```
On a Mac, prefer the fully-local path — Docker Ollama is CPU-only and slow.

## Demo script
1. Open the **dashboard** (`/dashboard`) on a second screen.
2. **Offline:** click `⏸ Close window`. Chat something easy in Open WebUI → answered
   locally, instantly, no network.
3. Ask 3 hard things (or click `＋ Seed 100`) → they pile up as **Queued** (amber).
4. **Burst:** click `▶ Open window` (or flip real WiFi on). Watch the whole backlog
   flip **Sending → Done** in parallel, and the chat bubbles fill in. That's
   Cerebras speed.
5. Close the window mid-drain to show leftovers ride the next window.

## Models exposed to Open WebUI
Open WebUI sees one model:
- `ferry` — auto-routes internally between local Ollama and tool-enabled Gemma 4.

For cloud-worthy prompts, Ferry automatically chooses a single Gemma 4 agent or
parallel sub-agents with synthesis. The user never picks a route.

## API
| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/chat/completions` | OpenAI-compatible chat (triage + stream) |
| GET | `/v1/models` | model list |
| GET | `/api/status` | window state + backlog counts |
| GET | `/api/tasks` | the backlog |
| POST | `/demo/online/{true\|false\|auto}` | force / release the window |
| POST | `/demo/seed?count=100` | preload hard tasks |
| POST | `/demo/drain` | force a drain now |
| POST | `/demo/clear` | empty the backlog |

## Build status
- [x] OpenAI-compatible service + `/v1/models`, SSE streaming
- [x] Local passthrough to Ollama (`LiquidAI/lfm2.5-1.2b-instruct`)
- [x] SQLite (WAL) FIFO backlog + automatic router
- [x] Held-open placeholder → deliver-back into the same bubble
- [x] Cerebras key-pool client, connectivity watcher, parallel burst drainer
- [x] Demo toggles + backlog dashboard
- [x] Track 1 stretch: tool-enabled single-agent + multi-agent decomposition
- [ ] Multimodal queued task
