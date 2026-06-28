# Jetson Startup & Operations

Operational runbook for the **Jetson hub** (`ethan@ethan-desktop`). This is the live,
deployed side. For first-time setup see `JETSON_DEPLOY.md`; for the project overview see
`CLAUDE.md`.

## The stack (all auto-start on boot)
| Service | What | Managed by | Port |
|---|---|---|---|
| **Ollama** | serves Liquid `LiquidAI/lfm2.5-1.2b-instruct` | systemd `ollama.service` | 11434 |
| **Ferry** | router + backlog + Cerebras burst | systemd `ferry.service` (enabled) | 8080 |
| **Open WebUI** | the chat UI | Docker container `open-webui` (`--restart unless-stopped`) | 3000 |
| **Tailscale** | remote access (`ethan-desktop.taile8145e.ts.net`, `100.72.28.10`) | systemd | serve :8443 |

Repo on the Jetson: `/home/ethan/cerebras-gemma-hack` (venv at `.venv`, config at `.env`).

## Access URLs
- **Chat (Open WebUI):** `http://192.168.1.62:3000` (same-wifi) · `https://ethan-desktop.taile8145e.ts.net:8443` (Tailscale)
- **Operator dashboard:** `http://192.168.1.62:8080/dashboard`
- **Demo page / animated diagram:** `http://192.168.1.62:8080/demo` · `/how`
- Open WebUI → Ferry is wired in **Admin → Connections** as
  `http://172.17.0.1:8080/v1` (Bearer `ferry`), with the **Ollama API connection toggled OFF**.

## Check everything is up
```bash
systemctl status ferry --no-pager | head -5
systemctl status ollama --no-pager | head -5
docker ps --filter name=open-webui --format "{{.Names}} {{.Status}}"
curl -s http://localhost:8080/api/status
```
Healthy `api/status` shows an `online` boolean (true/false), `local_model` = the Liquid id, and a `cloud_model`.

## Start / stop / restart
```bash
sudo systemctl restart ferry        # restart Ferry (after a code change or .env edit)
sudo systemctl stop ferry           # stop / start
sudo systemctl start ferry
docker restart open-webui           # restart the chat UI
sudo systemctl restart ollama       # restart the local model server
```

## Logs
```bash
journalctl -u ferry -f              # Ferry logs (router decisions, drains)
docker logs -f open-webui           # Open WebUI logs
```

## Common operations
**Update Ferry code** (after the Mac side pushes changes):
```bash
cd ~/cerebras-gemma-hack && git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart ferry
```
> Ferry exposes one model, `ferry`; add `EXA_API_KEY` and `E2B_API_KEY` to
> `.env` so the internal Gemma 4 agent routes can use tools.
**Confirm Gemma 4 access** if needed:
```bash
curl -s https://api.cerebras.ai/v1/models -H "Authorization: Bearer $(grep -m1 CEREBRAS_API_KEYS ~/cerebras-gemma-hack/.env | cut -d= -f2 | cut -d, -f1)" | tr ',' '\n' | grep '"id"'
cd ~/cerebras-gemma-hack && sed -i 's/^CEREBRAS_MODEL=.*/CEREBRAS_MODEL=gemma-4-31b/' .env && sudo systemctl restart ferry
```
**Demo the connection window** (or use the dashboard buttons):
```bash
curl -X POST http://localhost:8080/demo/online/false   # offline → hard prompts queue & hold
curl -X POST http://localhost:8080/demo/online/true    # window opens → Cerebras burst lands
curl -X POST http://localhost:8080/demo/online/auto    # follow the real uplink
curl -X POST "http://localhost:8080/demo/seed?count=12" # preload a backlog
curl -X POST http://localhost:8080/demo/clear          # empty the backlog
```

## After a reboot
Nothing to do — `ferry.service` is enabled, Ollama is a service, and the Open WebUI
container restarts automatically. Verify with the "Check everything is up" block.

## Pending (optional)
- **WiFi hotspot** ("use the Jetson with no infrastructure wifi") — needs an **ethernet
  uplink** so the wifi can become the access point. Steps are in `JETSON_DEPLOY.md` →
  Step 3 (Networking) → Option A. Until then, the client + Jetson share the wifi and the `/demo/online`
  toggle simulates the window.
