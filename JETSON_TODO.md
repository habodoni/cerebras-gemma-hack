# Jetson — Catch-up TODO

**Status:** the Jetson is running commit `77e0f40`. `main` is now `2655882` —
**4 commits behind**. It has NOT been updated with any of the recent work yet.

What it's missing (all on `main`):
- `c2a800e` — cloud 400/429 fix (orchestrator-workers, history trim, 429 retry) + queue/return notifications
- `1a81769` — E2B sandbox reuse, role-based **medium reasoning** on BRAIN, routing trim + `"use cerebras"`/`"use local"` override
- `d15aa2a` — backlog highlighting: in-chat receipt + live drain dashboard
- `2655882` — speed scoreboard, live model labels, window-vs-reachable warning

No new pip dependencies were added, so **no `pip install` is required** — just pull + a small `.env` update + restart.

---

## 1. Pull the latest code  *(paste into Jetson SSH)*
```bash
cd ~/cerebras-gemma-hack && git pull
```
Expect a fast-forward `77e0f40 → 2655882`. (The remote URL already has your token embedded from last time, so auth should just work.)

## 2. Update the Jetson `.env`  *(one paste — set-or-append, phone-safe)*
```bash
cd ~/cerebras-gemma-hack; set_env() { grep -q "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }; set_env CEREBRAS_MAX_TOKENS 8192; set_env CEREBRAS_THINK_EFFORT medium; set_env NOTIFY_MODE ntfy; set_env NTFY_TOPIC ferry-ethan-7q3v9k2x; grep -E "^(LOCAL_MODEL|CEREBRAS_MODEL|CEREBRAS_MAX_TOKENS|CEREBRAS_THINK_EFFORT|NOTIFY_MODE|NTFY_TOPIC|PUBLIC_BASE_URL)=" .env
```
Why each:
- **`CEREBRAS_MAX_TOKENS=8192`** — REQUIRED. The Jetson's value is still the old low one (≈1024). With reasoning now on for BRAIN steps, a low budget gets spent on the hidden reasoning trace and the answer comes back **empty**. 8192 fixes it.
- **`CEREBRAS_THINK_EFFORT=medium`** — turns on Gemma reasoning for the planner + synthesis. (Code default is already `medium`; setting it explicitly is just clarity.)
- **`NOTIFY_MODE=ntfy` + `NTFY_TOPIC=ferry-ethan-7q3v9k2x`** — phone push on queue + return. The phone is already subscribed to this topic; the Jetson just needs to start sending. (Not set before now.)
- Confirm **`PUBLIC_BASE_URL=http://192.168.1.62:8080`** is still the Jetson's IP — artifact download links use it. Update it (`set_env PUBLIC_BASE_URL http://<new-ip>:8080`) whenever the IP/hotspot changes.

### Cerebras key — decide
The Jetson still uses the **older** key. The Mac is now on the **fresh key with the
full 100k tokens/min** quota (per the limits screenshot). For the multi-agent +
scoreboard demo you want the most quota, so either:
- swap the Jetson to the fresh key: `set_env CEREBRAS_API_KEYS <fresh-key>`, or
- **pool both** (widens the per-minute budget): `set_env CEREBRAS_API_KEYS <fresh-key>,<older-key>`

(Don't paste keys into this file — set them straight into the Jetson `.env`.)

## 3. Restart + verify
```bash
sudo systemctl restart ferry; sleep 3; curl -s http://localhost:8080/api/status; echo
```
Look for `"cloud_model":"gemma-4-31b"`, a Liquid `local_model`, and `keys` = however
many you set.

---

## 4. Smoke-test the new features (from any device on the LAN)
- [ ] **Dashboard** → `http://192.168.1.62:8080/dashboard` shows the new board. Click **Seed 100 → Open window** → watch it drain and the **scoreboard** headline "N answers in Xs".
- [ ] **Backlog receipt** — offline, send a hard prompt → bubble shows `⏳ Queued · #N in the on-device backlog`; on burst → `📡 … bursted after Xs`.
- [ ] **Notifications** — that same offline→online cycle should buzz your phone (queued + answer-ready).
- [ ] **Reasoning** — a "research/compare" prompt runs multi-agent; planner + synthesis now reason (no empty bubble thanks to step 2).
- [ ] **Sandbox reuse** — a multi-step "make a file then read it back" prompt works.
- [ ] **Router override** — "use local …" stays on Liquid; "use cerebras …" forces the cloud.

## 5. Open WebUI
- [ ] Make `ferry` the default model: **Admin Panel → Settings → Interface → Default Model → `ferry`** (it's the only model now, so new chats should pick it anyway).

## 6. Still open on the Jetson (not code — separate work)
- [ ] **WiFi hotspot** (needs the ethernet uplink) so a weak client connects to the hub directly.
- [ ] **Open WebUI PWA** — "Add to Home Screen" on the client device so it feels like an app.

## 7. After the hackathon (security)
- [ ] **Revoke the GitHub PAT** — it's embedded in the Jetson's `.git/config` in plaintext.
- [ ] **Rotate** the Cerebras / Exa / E2B keys (they were shared in chat during setup).
