# Jetson Guide: Access, What's Running, How It Works, and Porting

**Last updated:** 2026-07-11
**Hardware:** Jetson Orin Nano (`ethan@ethan-desktop`)
**Current project on it:** Ferry

## What this doc is

The Jetson is a general-purpose edge hub. It can run any project. Right now it's
running **Ferry**, a local-first AI gateway built for the Cerebras × Gemma 4
hackathon (June 27 to 28, 2026). Everything in this guide about SSH access,
services, and networking applies to the hub itself, no matter what's deployed on
it. Ferry is just the project sitting on it today.

This guide covers four things:

1. **Tapping in** (§1) - how to reach the Jetson, over SSH and over the web.
2. **What's running** (§2) - the live stack, ports, models, and repo state.
3. **How it works** (§3) - the architecture, request flow, services, and boot behavior.
4. **Porting to another repo** (§5) - how to move Ferry to a new repo, or replace it with a different project.

## Timeline

- **2026-06-27** - project started.
- **2026-06-28** - hackathon build day. The Jetson was deployed and verified working end to end.
- **2026-07-11** - this guide was written.

## Current state

The Jetson is running commit `77e0f40`, from 2026-06-28. The code on `main` has
moved on since then and is a few commits ahead. To bring it current, follow
`JETSON_SSH_UPDATE.md` — SSH in and run the self-service scripts in `scripts/`.

Related docs: `JETSON_SSH_UPDATE.md` (**start here to update it yourself** —
SSH in + run the self-service scripts in `scripts/`), `JETSON_DEPLOY.md` (build
from scratch), `JETSON_STARTUP.md` (day-to-day ops), `JETSON_TODO.md` (older
catch-up list, superseded by the scripts), `HOW_IT_WORKS.md` (deep
architecture), `CLAUDE.md` (project overview).

---

## 0. Quick reference

| Thing | Value |
|---|---|
| Host / user | `ethan@ethan-desktop` |
| LAN IP | `192.168.1.62` |
| Tailscale | `ethan-desktop.taile8145e.ts.net` · `100.72.28.10` |
| Chat UI (Open WebUI) | `http://192.168.1.62:3000` |
| Operator dashboard | `http://192.168.1.62:8080/dashboard` |
| Demo page / diagram | `http://192.168.1.62:8080/demo` · `/how` |
| Repo on device | `/home/ethan/cerebras-gemma-hack` |
| Local model | `1-bit-Bonsai-27B` (llama-server :11435) after the Bonsai swap; Liquid `LiquidAI/lfm2.5-1.2b-instruct` (Ollama) before/fallback |
| Cloud model | `gemma-4-31b` (Cerebras) |
| Ports | Ferry `8080` · Open WebUI `3000` · Ollama `11434` · Tailscale serve `8443` |

Health check in one line (run on the Jetson):
```bash
curl -s http://localhost:8080/api/status; echo
```

---

## 1. Tapping in

### 1a. SSH (terminal access)

You reach the Jetson over SSH. Any of these hosts work depending on where you are:

```bash
ssh ethan@192.168.1.62                       # same Wi-Fi / LAN
ssh ethan@ethan-desktop.taile8145e.ts.net    # anywhere, via Tailscale
ssh ethan@100.72.28.10                        # Tailscale IP (same thing)
```

- **From a phone:** use an SSH client app (Termius, Blink, JuiceSSH). Add a host
  with the address above, user `ethan`, and your password or key. This is the
  primary way the hub is managed when there's no laptop on the same network.
- **From a laptop:** must be on the **same LAN** (use `192.168.1.62`) **or on the
  tailnet** (use the `.ts.net` name). A laptop that is on neither will get
  `NXDOMAIN`/timeout — that is expected, it just means you're off both networks.
- **Paste tip:** phone SSH keyboards sometimes flatten newlines in multi-line
  pastes. Join commands with `;` or `&&` on one line (all command blocks in
  `JETSON_TODO.md` are already written this way).

### 1b. Web access (no SSH needed)

Anyone on the same network as the Jetson can open these in a browser:

- **Chat:** `http://192.168.1.62:3000` — Open WebUI, the actual product surface.
  Pick the **`ferry`** model. On a phone you can "Add to Home Screen" to make it
  feel like a native app (the server stays on the Jetson).
- **Operator dashboard:** `http://192.168.1.62:8080/dashboard` — backlog board,
  speed scoreboard, and the demo controls (Seed / Open window / Drain / Clear).
- **Demo + diagram:** `http://192.168.1.62:8080/demo` and `/how`.

Over Tailscale, Open WebUI is also fronted at
`https://ethan-desktop.taile8145e.ts.net:8443` (tailnet devices only). Tailscale is
**dev convenience** — it is not part of the offline path; a real client always
reaches the hub over the local network/hotspot.

---

## 2. What's running right now

*Snapshot as of 2026-07-11.* Four things run on the Jetson, **all auto-start on boot**:

| Service | What it does | Managed by | Port |
|---|---|---|---|
| **Ollama** | serves the local model (Liquid LFM2.5) | systemd `ollama.service` | 11434 |
| **Ferry** | the gateway: router + SQLite backlog + Cerebras burst | systemd `ferry.service` (enabled) | 8080 |
| **Open WebUI** | the chat UI clients talk to | Docker container `open-webui` (`--restart unless-stopped`) | 3000 |
| **Tailscale** | remote access | systemd | serve `8443` |

Key facts:

- **Repo:** `/home/ethan/cerebras-gemma-hack`, virtualenv at `.venv`, config at
  `.env` (gitignored). Git remote is HTTPS with a Personal Access Token embedded
  in `.git/config` (so `git pull` just works — **rotate that token after the
  hackathon**).
- **Local model:** after the Bonsai swap (`scripts/jetson_bonsai_setup.sh`),
  `LOCAL_MODEL=1-bit-Bonsai-27B` served by llama-server on :11435. The official
  Liquid tag `LiquidAI/lfm2.5-1.2b-instruct` stays pulled in Ollama as the
  fallback and as a picker entry. (Historical note: the original hackathon
  deploy used the community `bcluzel` Q4 quant; it has been replaced by the
  official tag.)
- **Cloud model:** `gemma-4-31b` on Cerebras, drained over the Jetson's uplink.
- **Open WebUI → Ferry wiring** lives inside Open WebUI's own DB (set once in the
  UI, persists): **Admin → Connections → OpenAI API** = `http://172.17.0.1:8080/v1`,
  key `ferry`, with the **Ollama API connection toggled OFF**. `172.17.0.1` is the
  Docker bridge to the host — `localhost` from *inside* the container would not
  reach Ferry.

Confirm it's all healthy:
```bash
systemctl status ferry  --no-pager | head -5
systemctl status ollama --no-pager | head -5
docker ps --filter name=open-webui --format "{{.Names}} {{.Status}}"
curl -s http://localhost:8080/api/status; echo
```
A healthy `/api/status` shows `online`/`can_burst` booleans, `local_model` = the
configured local model (the Liquid id, or `1-bit-Bonsai-27B` after the Bonsai
swap in `JETSON_SSH_UPDATE.md`), and `cloud_model` = `gemma-4-31b`.

---

## 3. How it works on the Jetson

### 3a. The picture

```
client (phone/laptop browser, on the Jetson's network or hotspot)
   │  http://<jetson-ip>:3000
   ▼
Open WebUI  ──►  Ferry  ──►  Ollama · Liquid LFM2.5      (local: instant, offline)
(Docker :3000)  (:8080)      (:11434)
                   │
                   └── hard prompt ──► SQLite backlog ──► Cerebras · Gemma 4
                                       (holds on-device)   (burst when the uplink is up)
```

One codebase, one exposed model (`ferry`). Ferry decides per message; the user
never picks a route.

### 3b. Request flow

1. A client opens Open WebUI and sends a message to the `ferry` model.
2. Open WebUI calls Ferry's OpenAI-compatible endpoint (`/v1/chat/completions`)
   at `172.17.0.1:8080` (host bridge).
3. Ferry's **router** decides **local** vs **cloud**:
   - Easy/short → answered **on-device** by Liquid via Ollama. Works with no
     internet at all.
   - Hard (tools, current info, long/multi-step) → **cloud**.
4. Cloud path:
   - If a **connection window is open** (uplink reachable) → burst to **Gemma 4 on
     Cerebras** immediately, streaming back into the chat bubble.
   - If **offline** → the prompt is written to the **SQLite backlog** and the chat
     bubble is held open with a queued receipt. A background **drainer** bursts it
     the moment the window opens, streaming the answer back into that same bubble.
5. Cloud work can call **tools** (Exa `web_search`, E2B `run_code` for code +
   file artifacts) and, for research/compare/plan prompts, fan out to a
   **multi-agent orchestrator** (planner → parallel workers → synthesis).

The held-open SSE stream is the trick that makes the answer land in the *original*
message with no Open WebUI plugin. Deep detail in `HOW_IT_WORKS.md`.

### 3c. The Ferry service (systemd)

Ferry runs as `ferry.service`. The unit (installed at
`/etc/systemd/system/ferry.service`) is:

```ini
[Unit]
Description=Ferry gateway
After=network-online.target ollama.service
Wants=network-online.target

[Service]
User=ethan
WorkingDirectory=/home/ethan/cerebras-gemma-hack
EnvironmentFile=/home/ethan/cerebras-gemma-hack/.env
ExecStart=/home/ethan/cerebras-gemma-hack/.venv/bin/uvicorn ferry.main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

- Listens on `0.0.0.0:8080` so the Open WebUI container (and LAN clients) can reach it.
- Reads all config from `.env` (`EnvironmentFile`).
- `After=ollama.service` so the local model server is up first.
- `Restart=always` — it comes back on crash and on boot.

### 3d. Open WebUI (Docker)

Runs as a container, not native (the Jetson has Python 3.10; Open WebUI needs
3.11, so Docker sidesteps that):

```bash
docker run -d -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data \
  --name open-webui --restart unless-stopped \
  ghcr.io/open-webui/open-webui:main
```

`--restart unless-stopped` brings it back on boot — no separate systemd unit needed.

### 3e. After a reboot

Nothing to do. `ferry.service` is enabled, Ollama is a service, and the Open WebUI
container auto-restarts. Just run the health block in §2 to confirm.

---

## 4. Day-to-day operations

### Start / stop / restart
```bash
sudo systemctl restart ferry     # after a code change or .env edit
sudo systemctl stop ferry
sudo systemctl start ferry
docker restart open-webui        # restart the chat UI
sudo systemctl restart ollama    # restart the local model server
```

### Logs
```bash
journalctl -u ferry -f           # Ferry: router decisions (route=…), drains, Cerebras errors
docker logs -f open-webui        # Open WebUI
```

### Update the code (pull the Mac side's changes)
```bash
cd ~/cerebras-gemma-hack && git pull && sudo systemctl restart ferry
```
Better: run `./scripts/jetson_update.sh` instead — it pulls, installs deps only
when they changed, fills in required `.env` values, and health-checks the restart.
Full instructions: `JETSON_SSH_UPDATE.md`.

### Demo controls (or use the dashboard buttons)
```bash
curl -X POST http://localhost:8080/demo/online/false     # offline → hard prompts queue & hold
curl -X POST http://localhost:8080/demo/online/true      # window opens → Cerebras burst
curl -X POST http://localhost:8080/demo/online/auto      # follow the real uplink
curl -X POST "http://localhost:8080/demo/seed?count=100" # preload a backlog
curl -X POST http://localhost:8080/demo/clear            # empty the backlog
curl -X POST http://localhost:8080/demo/drain            # force a drain now
```

### Editing config safely (set-or-append helper)
```bash
cd ~/cerebras-gemma-hack; set_env() { grep -q "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }
set_env CEREBRAS_MODEL gemma-4-31b
sudo systemctl restart ferry
```
> Never re-run `cp .env.jetson.example .env` on the live box — it wipes your keys
> and resets `LOCAL_MODEL` to an id that may not be pulled. Use `set_env`.

### Troubleshooting
| Symptom | Likely cause / fix |
|---|---|
| Open WebUI shows no models | Connection not set / wrong URL. Admin → Connections → `http://172.17.0.1:8080/v1`, key `ferry`; Ollama API OFF. |
| Local answers empty | `LOCAL_MODEL` in `.env` ≠ what's pulled in Ollama (`ollama list`) — exception: `1-bit-Bonsai-27B` is served by llama-server on :11435, not Ollama, so it never appears in `ollama list`. Or (with reasoning on) `CEREBRAS_MAX_TOKENS` too low. |
| Cloud answers empty / `finish=length` | Reasoning on with a tiny `CEREBRAS_MAX_TOKENS`. Raise it (≥ 8192). |
| Cloud `429` | Per-minute token quota. Add keys to the pool (`CEREBRAS_API_KEYS=k1,k2`) or wait a minute. |
| "Window open" but nothing drains | Uplink actually unreachable (`can_burst=false`). `curl https://api.cerebras.ai/v1/models`. |
| Ferry won't start | `journalctl -u ferry -n 50` — usually a `.env` typo or a bad venv. |

---

## 5. Bringing Ferry to another repo

The hub is reusable and Ferry is only its **current tenant**, so moving Ferry to a
new repo — or replacing it with a different project entirely — is straightforward.
Ferry itself is a **plain FastAPI app**: nothing about the *code* is Jetson-specific.
Only the **`.env`, the host, and the pulled model** differ per deployment, so
"moving to another repo" is mostly git plumbing plus re-pointing the deployment.

### 5a. Portable vs host-specific

| Portable (belongs in the repo) | Host-specific (do NOT commit) |
|---|---|
| `ferry/` (all the service code) | `.env` (keys + per-host config) — **gitignored** |
| `static/` (`demo`, `dashboard`, `how`) | `data/` (the SQLite DB + generated files) — **gitignored** |
| `seeds/tasks.json` | `.venv/` — **gitignored** |
| `requirements.txt` | `/etc/systemd/system/ferry.service` (host paths) |
| `.env.example`, `.env.jetson.example` (templates) | Open WebUI's own DB (the Docker volume) |
| `tests/`, docs (`*.md`) | the pulled Ollama model |

The `.gitignore` already excludes `.env`, `data/`, `.venv`, `__pycache__`. Confirm
before any push: `git status` should never list `.env`.

### 5b. Option A — mirror the repo (keeps history)

Fastest way to get an identical repo under a new remote:

```bash
# on any machine with git access to the current repo
git clone --bare https://github.com/habodoni/cerebras-gemma-hack.git
cd cerebras-gemma-hack.git
git push --mirror https://github.com/<you>/<new-repo>.git
```

Or from an existing working checkout, just add a second remote and push:
```bash
git remote add neworigin https://github.com/<you>/<new-repo>.git
git push neworigin main
```

### 5c. Option B — fresh repo (drop history)

Start clean (e.g. to strip the commit history before sharing):

```bash
# from a CLEAN checkout of main (no local .env staged)
rm -rf .git
git init -b main
git add .                       # .gitignore keeps .env/.venv/data out
git commit -m "Ferry: local-first AI gateway (initial import)"
git remote add origin https://github.com/<you>/<new-repo>.git
git push -u origin main
```
Double-check `git ls-files | grep -E '\.env$|^data/'` returns nothing before pushing.

### 5d. Re-point the Jetson (or any host) at the new repo

Keep the existing deployment, just change where it pulls from:
```bash
cd ~/cerebras-gemma-hack
git remote set-url origin https://<token>@github.com/<you>/<new-repo>.git
git fetch origin && git reset --hard origin/main
sudo systemctl restart ferry
```
`.env` is untouched by this (gitignored), so the box keeps its keys and config.

If you'd rather clone somewhere new, clone the new repo to a new dir, copy your
existing `.env` into it, and update the systemd unit's `WorkingDirectory`,
`ExecStart`, and `EnvironmentFile` paths, then `daemon-reload` + restart.

### 5e. Stand up Ferry on a brand-new host (condensed)

Full runbook is `JETSON_DEPLOY.md`; the essence:

```bash
# 1. Local model
#    (native Ollama; pull whatever LOCAL_MODEL will point at)
ollama pull LiquidAI/lfm2.5-1.2b-instruct

# 2. Ferry
git clone <new-repo-url> ~/ferry && cd ~/ferry
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.jetson.example .env        # then fill CEREBRAS_API_KEYS, keys, PUBLIC_BASE_URL, model
uvicorn ferry.main:app --host 0.0.0.0 --port 8080   # smoke test, then Ctrl-C

# 3. systemd (edit User/paths to match the new host), then:
sudo systemctl daemon-reload && sudo systemctl enable --now ferry

# 4. Open WebUI (Docker) → Admin → Connections → http://172.17.0.1:8080/v1 (key: ferry), Ollama OFF
# 5. verify
curl -s http://localhost:8080/api/status; echo
```

Requirements on the new host: Ollama, Docker (for Open WebUI) or Python 3.11 (for
native Open WebUI), Python 3.10–3.12 for Ferry, `git`, `sudo`, and a Cerebras key
with Gemma 4 access.

### 5f. Porting gotchas

- **Python version:** Ferry needs 3.10–3.12 (avoid 3.13/3.14 — some deps lack
  wheels). Native Open WebUI needs 3.11; if you can't get 3.11, use the Docker
  container.
- **Docker bridge IP:** Open WebUI-in-Docker reaches host Ferry at
  `172.17.0.1:8080`, not `localhost`. On non-default Docker networks the host IP
  may differ (`ip addr show docker0`).
- **Model name must match what's pulled:** `LOCAL_MODEL` in `.env` has to equal an
  `ollama list` entry exactly, or local answers come back empty.
- **`PUBLIC_BASE_URL`:** set it to the new host's reachable address (e.g.
  `http://<host-ip>:8080`) so generated-file download links resolve for clients.
- **Keys & quota:** the Cerebras key needs Gemma 4 preview access; pool multiple
  keys (`CEREBRAS_API_KEYS=k1,k2`) to widen the per-minute token budget.
- **Secrets:** never commit `.env`; rotate any token embedded in a remote URL.

---

## 6. Configuration reference (`.env`)

Ferry reads everything from `.env`. Canonical template: **`.env.jetson.example`**.
The ones you actually touch per host:

| Var | What | Typical |
|---|---|---|
| `LOCAL_MODEL` | local model id | `1-bit-Bonsai-27B` (llama-server) or `LiquidAI/lfm2.5-1.2b-instruct` (Ollama) |
| `OLLAMA_BASE_URL` | local model server | `http://localhost:11434/v1` |
| `CEREBRAS_API_KEYS` | comma-separated key pool | `csk-…,csk-…` |
| `CEREBRAS_MODEL` | cloud model | `gemma-4-31b` |
| `CEREBRAS_MAX_TOKENS` | max output tokens (leave room for reasoning) | `8192` |
| `CEREBRAS_REASONING_EFFORT` | global effort (Hands/Voice) | `none` |
| `CEREBRAS_THINK_EFFORT` | Brain steps (planner/synthesis) effort | `medium` |
| `EXA_API_KEY` | web_search tool | `…` |
| `E2B_API_KEY` | run_code / artifacts tool | `…` |
| `PUBLIC_BASE_URL` | host for artifact download links | `http://192.168.1.62:8080` |
| `NOTIFY_MODE` | `none` \| `macos` \| `ntfy` | `ntfy` |
| `NTFY_TOPIC` | phone push topic (headless hub) | `ferry-…` |
| `ROUTER_MODE` | `heuristic` \| `llm` \| `always_cloud` \| `always_local` | `heuristic` |
| `DB_PATH` | SQLite backlog path | `./data/ferry.db` |
| `GENERATED_FILES_DIR` | artifact output dir | `./data/generated` |

Others (`LOCAL_*` sizing, `E2B_*` limits, `MULTIVERSE_AGENTS`, `AGENT_MAX_STEPS`,
`WATCHER_*`, `DRAIN_*`, `MAX_ATTEMPTS`, `HEARTBEAT_INTERVAL`, `PLACEHOLDER_TEXT`)
have sensible defaults in `ferry/config.py` — only set them to override.

---

## 7. Appendix — one-liners

```bash
# What commit is the Jetson on?
git -C ~/cerebras-gemma-hack rev-parse --short HEAD

# Which Gemma models does the key see?
curl -s https://api.cerebras.ai/v1/models \
  -H "Authorization: Bearer $(grep -m1 CEREBRAS_API_KEYS ~/cerebras-gemma-hack/.env | cut -d= -f2 | cut -d, -f1)" \
  | tr ',' '\n' | grep '"id"'

# Watch a burst live
watch -n1 'curl -s http://localhost:8080/api/status | python3 -m json.tool'

# Full health snapshot
for u in ferry ollama; do systemctl is-active $u; done
docker ps --filter name=open-webui --format "{{.Status}}"
curl -s http://localhost:8080/api/status; echo
```

To bring the Jetson fully up to date with the latest code, follow
**`JETSON_SSH_UPDATE.md`** (SSH in, run the scripts in `scripts/`).
