# Jetson: SSH In and Update It Yourself

**Written:** 2026-07-14. This is the self-service guide: how to SSH into the
Jetson and run the update scripts on your own, no walkthrough needed.

What this round of updates brings to the Jetson:

1. All pending Ferry code (the Jetson has been behind since 2026-06-28).
2. **Bonsai 27B (1-bit)** as the new local model — announced by PrismML today
   (2026-07-14), a 27B-class model in 3.8 GB that fits the Orin Nano. It
   replaces Liquid as the default; Liquid stays installed as a fallback.
3. **nemotron-3-nano:4b** added to the model picker (next to `ferry`).
4. **OfflineBase branding** on the chat UI ("OfflineBase (Open WebUI)").

---

## 1. SSH in

From a laptop on the same Wi-Fi as the Jetson:

```bash
ssh ethan@192.168.1.62
```

From anywhere (laptop or phone on your Tailscale network):

```bash
ssh ethan@ethan-desktop.taile8145e.ts.net
```

From a phone: use an SSH app (Termius, Blink, JuiceSSH), host `192.168.1.62`
(or the Tailscale name), user `ethan`, your password.

Optional, one-time — stop typing the password from your laptop:

```bash
ssh-copy-id ethan@192.168.1.62      # run on the laptop, then ssh is key-based
```

Tip: phone SSH apps sometimes flatten multi-line pastes. Every command in this
doc is a single line, so paste them one at a time.

## 2. Run the scripts (in this order)

### Step 1 — always: pull code + catch up config

```bash
cd ~/cerebras-gemma-hack && git pull && ./scripts/jetson_update.sh
```

Safe to run any time, as often as you like. It pulls the latest code, installs
deps only if they changed, fills in any missing `.env` values (without touching
ones you customized), pulls `nemotron-3-nano:4b` into Ollama, restarts Ferry,
and health-checks it. **This is the one command for every future update too.**

### Step 2 — one-time: install Bonsai 27B as the local model

```bash
cd ~/cerebras-gemma-hack && ./scripts/jetson_bonsai_setup.sh
```

Takes ~15-30 min (compiles the PrismML llama.cpp fork, downloads 3.8 GB).
What it sets up and why:

- Bonsai's 1-bit weights use custom kernels that **only exist in PrismML's
  llama.cpp fork — stock Ollama cannot load this model.** So Bonsai runs as its
  own service (`bonsai.service`, llama-server on port 11435) and Ferry's `.env`
  is repointed at it. Zero Ferry code changes; it's all config.
- Ollama keeps running on 11434, serving the picker extras (nemotron) and
  holding Liquid as the fallback.
- Thinking mode is disabled (`--reasoning-budget 0`) so answers arrive in
  seconds. Bonsai supports full reasoning, but at this board's ~5-12 tok/s that
  means minutes per answer. If you ever want quality-over-speed: edit
  `/etc/systemd/system/bonsai.service`, remove that flag, then run
  `sudo systemctl daemon-reload && sudo systemctl restart bonsai` (a unit edit
  does nothing until you do).

**About the "4-bit" file on the Hugging Face page:** the Q4_1 (~1.9 GB) file in
the Bonsai repos is the **DSpark speculative-decoding drafter** — an
accelerator that must run *alongside* the main model, not a chat model itself
(HF's "hardware compatibility" widget just matched the smallest file to the
Jetson). The real choices are ternary (~7.2 GB — does not fit an 8 GB board
once the KV cache and the rest of the stack are counted) and **1-bit (3.8 GB —
the phone-class build, which is what we install)**.

### Step 3 — one-time: OfflineBase branding

```bash
cd ~/cerebras-gemma-hack && ./scripts/jetson_branding.sh
```

Recreates the Open WebUI container with `WEBUI_NAME=OfflineBase`. The UI then
shows **"OfflineBase (Open WebUI)"** in the header and tab (the suffix is added
by Open WebUI itself and can't be removed via this setting). All data — users,
chats, the Ferry connection — survives; it lives in the `open-webui` volume.

## 3. Verify

```bash
curl -s http://localhost:8080/api/status; echo
```

Expect `"local_model":"1-bit-Bonsai-27B"` and `"cloud_model":"gemma-4-31b"`.
Then from any device on the network, open `http://192.168.1.62:3000`:

- Header reads **OfflineBase (Open WebUI)**.
- The model picker shows **`ferry`** and **`nemotron-3-nano:4b`**.
- A quick prompt on `ferry` answers from Bonsai (first answer includes model
  warm-up; after that expect ~5-12 tok/s — a 27B on an 8 GB board is a
  capability statement, not a speed one).
- Picking `nemotron-3-nano:4b` bypasses Ferry's router entirely and talks to
  that model directly on Ollama.

Service checks if something looks off:

```bash
systemctl status bonsai --no-pager | head -5
journalctl -u bonsai -n 30
journalctl -u ferry -n 30
```

## 4. Revert / undo

Back to Liquid as the local model (also frees ~5 GB of memory). `bcluzel/...`
is the tag actually pulled on this Jetson's Ollama:

```bash
cd ~/cerebras-gemma-hack && sed -i 's|^OLLAMA_BASE_URL=.*|OLLAMA_BASE_URL=http://localhost:11434/v1|; s|^LOCAL_MODEL=.*|LOCAL_MODEL=bcluzel/LFM2.5-1.2B-Instruct:Q4_K_M|; s|^LOCAL_MAX_TOKENS=.*|LOCAL_MAX_TOKENS=64|; s|^LOCAL_TIMEOUT_SECONDS=.*|LOCAL_TIMEOUT_SECONDS=45|; /^EXTRA_LOCAL_BASE_URL=/d' .env && sudo systemctl restart ferry && sudo systemctl disable --now bonsai
```

Remove the branding (plain Open WebUI): first `docker rm -f open-webui`, then
run the `docker run` command from `scripts/jetson_branding.sh` without the
`-e WEBUI_NAME="OfflineBase"` line.

## 5. Memory notes (8 GB board, be aware)

Resident when Bonsai is the local model: Bonsai ~5.3 GB (weights + cache) +
Open WebUI container + OS. That leaves little headroom — nemotron (2.8 GB)
loads into Ollama **on demand** and unloads after idle, but using Bonsai and
nemotron at the same moment can push into swap. Fine for a demo; don't
hammer both at once. If the board feels tight: `sudo systemctl disable --now
bonsai` and revert (§4).
