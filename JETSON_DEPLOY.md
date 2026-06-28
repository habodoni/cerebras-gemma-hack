# Jetson Hub Deployment

The Jetson Orin Nano is the **hub**: it runs the local brain, the router, and the
chat UI. A weak client device joins the Jetson's WiFi hotspot and opens Open WebUI
in a browser — no app install, no internet required for the local experience. The
Jetson's *uplink* (the part that flickers) gates the Cerebras burst.

```
client (browser, on Jetson hotspot)
   │  http://<jetson-ip>:3000
   ▼
Open WebUI ──► Ferry ──► Ollama · LFM2.5-1.2B   (local, offline)
(:3000)        (:8080)   (:11434)  └─ queue ─► Cerebras gemma-4-31b  (burst, uplink)
```

Three native services on the Jetson: **Ollama → Ferry → Open WebUI**.

---

## Prerequisites (on the Jetson)
- [x] Ollama running natively, with `bcluzel/LFM2.5-1.2B-Instruct:Q4_K_M` pulled
      (`ollama list` to confirm).
- [ ] Python **3.11** (Open WebUI requires it; Ferry needs 3.10+).
- [ ] `git`, and `sudo` (for hotspot + systemd).
- [ ] Cerebras API key(s).
- [ ] WiFi adapter (for the hotspot) **and** a separate uplink for Cerebras —
      ethernet is ideal on the Orin Nano (wifi = AP, eth = internet).

---

## Step 1 — Ferry on the Jetson
```bash
git clone https://github.com/habodoni/cerebras-gemma-hack.git
cd cerebras-gemma-hack
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.jetson.example .env
# edit .env: paste CEREBRAS_API_KEYS (LOCAL_MODEL is already the Liquid id)

# smoke test (bound to all interfaces so the hotspot client can reach it)
uvicorn ferry.main:app --host 0.0.0.0 --port 8080
#   check: curl localhost:8080/api/status   → local_model should be the Liquid id
#   check: curl localhost:8080/demo  (the B&W demo page)
```

## Step 2 — Open WebUI on the Jetson
```bash
pip install open-webui      # in its own venv if it clashes with Ferry's deps
OPENAI_API_BASE_URL=http://localhost:8080/v1 \
OPENAI_API_KEY=ferry \
ENABLE_OLLAMA_API=false \
WEBUI_AUTH=false \
open-webui serve --host 0.0.0.0 --port 3000
#   it should list one model: "ferry"
```

## Step 3 — Make them services (survive reboot)
`/etc/systemd/system/ferry.service`:
```ini
[Unit]
Description=Ferry gateway
After=network.target ollama.service
[Service]
User=%USER%
WorkingDirectory=%HOME%/cerebras-gemma-hack
EnvironmentFile=%HOME%/cerebras-gemma-hack/.env
ExecStart=%HOME%/cerebras-gemma-hack/.venv/bin/uvicorn ferry.main:app --host 0.0.0.0 --port 8080
Restart=always
[Install]
WantedBy=multi-user.target
```
`/etc/systemd/system/open-webui.service`:
```ini
[Unit]
Description=Open WebUI
After=network.target ferry.service
[Service]
User=%USER%
Environment=OPENAI_API_BASE_URL=http://localhost:8080/v1
Environment=OPENAI_API_KEY=ferry
Environment=ENABLE_OLLAMA_API=false
Environment=WEBUI_AUTH=false
ExecStart=%HOME%/.local/bin/open-webui serve --host 0.0.0.0 --port 3000
Restart=always
[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ferry open-webui
```

## Step 4 — Networking (pick one)

### Option A — Jetson is the access point (purest "no wifi")
WiFi serves the hotspot; ethernet is the uplink for Cerebras.
```bash
# Create a NAT hotspot on the wifi interface (NetworkManager).
sudo nmcli device wifi hotspot ifname wlan0 ssid Ferry password ferrydemo
#   default AP address is 10.42.0.1
```
Client joins SSID **Ferry**, then opens **http://10.42.0.1:3000**.
Flicker the connection by unplugging/replugging the **ethernet** uplink (or use
the in-app demo toggle, Step 5).

### Option B — shared travel router / phone hotspot (simplest)
Jetson and client both join the same network; flicker *its* internet.
```bash
hostname -I            # find the Jetson's IP on that network, e.g. 192.168.1.42
```
Client opens **http://<that-ip>:3000**.

## Step 5 — Demo controls
- Force the connection window without touching hardware:
  `curl -X POST http://localhost:8080/demo/online/false`  (offline)
  `curl -X POST http://localhost:8080/demo/online/true`   (burst)
  `curl -X POST http://localhost:8080/demo/online/auto`   (follow real uplink)
- Preload a backlog: `curl -X POST "http://localhost:8080/demo/seed?count=12"`
- Operator view (on the Jetson or via the hotspot): `http://<jetson-ip>:8080/dashboard`

## Step 6 — Remote access (optional, dev only)
Tailscale is already on the Jetson (`*.ts.net`). Use it to SSH/monitor from your
Mac when there's internet. It is **not** part of the offline path — the client
always reaches the Jetson over the local hotspot.
```bash
# from your Mac, with internet:
open http://ethan-desktop.taile8145e.ts.net:3000     # Open WebUI, remote
```

---

## What flips between the two demo flows
Only deployment + config — the Ferry code is identical.

| | Mac flow | Jetson flow |
|---|---|---|
| `LOCAL_MODEL` | `gemma4:e2b` | `bcluzel/LFM2.5-1.2B-Instruct:Q4_K_M` |
| Runs on | the Mac | the Jetson |
| Client | same-machine browser | weak device over the hotspot |
| Window | Mac wifi | Jetson uplink (eth) |
