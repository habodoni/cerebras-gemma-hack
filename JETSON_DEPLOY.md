# Jetson Hub Deployment

The Jetson Orin Nano is the **hub**: it runs the local brain (Liquid via Ollama),
the router (Ferry), and the chat UI (Open WebUI). A weak client device joins the
Jetson over the local network and opens Open WebUI in a browser — no app install,
no internet needed for the local experience. The Jetson's *uplink* (the part that
flickers) gates the Cerebras burst.

This is the from-scratch runbook. For day-to-day start/stop/check, see
`JETSON_STARTUP.md`; for the project overview, `CLAUDE.md`.

```
client (browser, on the Jetson's network)
   │  http://<jetson-ip>:3000
   ▼
Open WebUI ──► Ferry ──► Ollama · LFM2.5-1.2B        (local, offline)
(Docker :3000) (:8080)   (:11434)  └─ queue ─► Cerebras  (burst, over the uplink)
```

Stack: **Ollama (native) → Ferry (systemd) → Open WebUI (Docker)**.

---

## Prerequisites (on the Jetson)
- [x] Ollama running natively, with `bcluzel/LFM2.5-1.2B-Instruct:Q4_K_M` pulled
      (`ollama list` to confirm).
- [x] **Docker** running. Open WebUI runs as a **container** — the Jetson ships
      Python 3.10, and Open WebUI needs 3.11, so Docker avoids a Python install.
- [ ] Python **3.10+** for Ferry, plus the venv module: `sudo apt install python3.10-venv`.
- [ ] `git`, and `sudo` (for systemd + the hotspot).
- [ ] Cerebras API key(s).
- [ ] For the hotspot (optional): a WiFi adapter **and** a separate uplink (ethernet)
      for Cerebras.

---

## Step 1 — Ferry (native + systemd)
```bash
sudo apt-get update && sudo apt-get install -y python3.10-venv
git clone https://github.com/habodoni/cerebras-gemma-hack.git ~/cerebras-gemma-hack
cd ~/cerebras-gemma-hack
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.jetson.example .env
# edit .env: paste CEREBRAS_API_KEYS. LOCAL_MODEL is already the Liquid id;
# CEREBRAS_MODEL stays gpt-oss-120b until your key has Gemma 4 access.

# smoke test on all interfaces, then Ctrl-C
uvicorn ferry.main:app --host 0.0.0.0 --port 8080
#   curl localhost:8080/api/status  → local_model should be the Liquid id
```
Install Ferry as a service so it survives reboot:
```bash
printf '[Unit]\nDescription=Ferry gateway\nAfter=network-online.target ollama.service\nWants=network-online.target\n\n[Service]\nUser=ethan\nWorkingDirectory=/home/ethan/cerebras-gemma-hack\nEnvironmentFile=/home/ethan/cerebras-gemma-hack/.env\nExecStart=/home/ethan/cerebras-gemma-hack/.venv/bin/uvicorn ferry.main:app --host 0.0.0.0 --port 8080\nRestart=always\nRestartSec=3\n\n[Install]\nWantedBy=multi-user.target\n' | sudo tee /etc/systemd/system/ferry.service >/dev/null
pkill -f 'uvicorn ferry.main:app'; sleep 1
sudo systemctl daemon-reload && sudo systemctl enable --now ferry
systemctl status ferry --no-pager | head -5
```

## Step 2 — Open WebUI (Docker)
If Open WebUI isn't already running, start the container (host-gateway lets it
reach Ferry on the host):
```bash
docker run -d -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data \
  --name open-webui --restart unless-stopped \
  ghcr.io/open-webui/open-webui:main
```
Then point it at Ferry **in the UI** (Open WebUI persists this in its own DB):
1. Open `http://<jetson-ip>:3000` → **Admin → Connections**.
2. **OpenAI API** → add a connection: URL `http://172.17.0.1:8080/v1`, key `ferry`.
   `172.17.0.1` is the Docker bridge → the host where Ferry runs; `localhost` from
   *inside* the container would NOT reach Ferry.
3. **Ollama API** → toggle **off** (so models come only through Ferry).
4. New chat → pick the **`ferry`** model.

> No `open-webui.service` is needed — the container's `--restart unless-stopped`
> already brings it back on boot.

## Step 3 — Networking: the hotspot (pick one)

### Option A — Jetson is the access point (purest "no wifi")
WiFi serves the hotspot; ethernet is the uplink for Cerebras.
```bash
nmcli device                 # confirm your wifi interface name (e.g. wlP1p1s0)
sudo nmcli device wifi hotspot ifname wlP1p1s0 ssid Ferry password ferrydemo
#   default AP address is 10.42.0.1
```
Client joins SSID **Ferry**, then opens **http://10.42.0.1:3000**.
Flicker the connection by unplugging/replugging the **ethernet** uplink (or use the
in-app demo toggle, Step 4).

### Option B — shared travel router / phone hotspot (simplest)
Jetson and client both join the same network; flicker *its* internet.
```bash
hostname -I            # the Jetson's IP on that network, e.g. 192.168.1.62
```
Client opens **http://<that-ip>:3000**.

## Step 4 — Demo controls
- Force the connection window without touching hardware:
  `curl -X POST http://localhost:8080/demo/online/false`  (offline)
  `curl -X POST http://localhost:8080/demo/online/true`   (burst)
  `curl -X POST http://localhost:8080/demo/online/auto`   (follow real uplink)
- Preload a backlog: `curl -X POST "http://localhost:8080/demo/seed?count=12"` (count is arbitrary; default 100)
- Operator dashboard: `http://<jetson-ip>:8080/dashboard`

## Step 5 — Remote access (optional, dev only)
Tailscale is already on the Jetson. **Tailscale Serve** fronts Open WebUI at
`https://ethan-desktop.taile8145e.ts.net:8443` — reachable from any device **on your
tailnet** (a laptop not on the tailnet will get NXDOMAIN; use the LAN IP instead). It
is **not** part of the offline path — a local client always reaches the Jetson over
the local network/hotspot.

---

## What flips between the two demo flows
Only deployment + config — the Ferry code is identical.

| | Mac flow | Jetson flow |
|---|---|---|
| `LOCAL_MODEL` | `gemma4:e2b` | `bcluzel/LFM2.5-1.2B-Instruct:Q4_K_M` |
| Ferry | `uvicorn` native | systemd `ferry.service` |
| Open WebUI | native (pip, Python 3.11) | Docker container |
| Runs on | the Mac | the Jetson |
| Client | same-machine browser | weak device over the network/hotspot |
| Window | Mac wifi | Jetson uplink (ethernet) |
