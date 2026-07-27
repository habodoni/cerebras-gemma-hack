#!/usr/bin/env bash
# Switch the Jetson's Bonsai from the PrismML llama-server sidecar to plain
# Ollama — the same runtime Base Drive uses. Same weights either way:
# MobiusDevelopment/Bonsai-27B-Q1_0-gguf is a checksum-verified republish of
# prism-ml's Bonsai-27B-Q1_0.gguf (see base-drive THIRD-PARTY-NOTICES.md).
#
# SSH into the Jetson, then:
#   cd ~/cerebras-gemma-hack && ./scripts/jetson_bonsai_via_ollama.sh
#
# What it does (idempotent):
#   1. upgrades Ollama if it predates 1-bit (Q1_0) support (needs >=0.30)
#   2. retunes Ollama for this 8 GB board (last-used model stays resident,
#      one model at a time, 8192 ctx, no parallel ctx split)
#   3. stops the old bonsai.service (kept installed, disabled — instant revert)
#   4. pulls the Bonsai tag (~4.2 GB) and PROVES it generates before touching
#      Ferry — if generation fails, everything is left as it was
#   5. repoints Ferry's .env and restarts it
#
# Revert to the llama-server sidecar at any time:
#   sudo systemctl enable --now bonsai && cd ~/cerebras-gemma-hack && ./scripts/jetson_bonsai_setup.sh
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TAG="MobiusDevelopment/Bonsai-27B-Q1_0-gguf"
LIQUID="LiquidAI/lfm2.5-1.2b-instruct"

step() { echo; echo "== $* =="; }
die()  { echo "ERROR: $*" >&2; exit 1; }

step "Preflight"
command -v ollama >/dev/null || die "ollama not installed"
FREE_GB=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc '0-9')
[ "${FREE_GB:-0}" -ge 6 ] || die "need >=6 GB free in \$HOME for the pull (have ${FREE_GB:-?} GB)"

step "Ollama version (Q1_0 needs >=0.30; Base Drive pins v0.30.7)"
VER=$(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)
echo "installed: ${VER:-unknown}"
MAJOR=${VER%%.*}; MINOR=$(echo "$VER" | cut -d. -f2)
if [ -z "$VER" ] || [ "${MAJOR:-0}" -eq 0 ] && [ "${MINOR:-0}" -lt 30 ]; then
    echo "upgrading Ollama in place (official installer, keeps models + service) ..."
    curl -fsSL https://ollama.com/install.sh | sh || die "Ollama upgrade failed"
    echo "now: $(ollama --version 2>/dev/null | head -1)"
fi

step "Retune Ollama for the 8 GB board"
# KEEP_ALIVE=-1: the last-used model stays resident (no cold reload per chat).
# MAX_LOADED_MODELS=1: picking Liquid swaps Bonsai out and vice versa — the
#   board cannot hold both; a swap back costs a ~1-2 min load, that's expected.
# NUM_PARALLEL=1 + CONTEXT_LENGTH=8192: Ollama splits the context across
#   parallel slots exactly like llama-server; one slot keeps the full 8192.
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/ferry-memory.conf > /dev/null <<'CONF'
[Service]
Environment=OLLAMA_KEEP_ALIVE=-1
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_CONTEXT_LENGTH=8192
CONF
sudo systemctl daemon-reload

step "Stop the llama-server sidecar (frees ~5 GB; stays installed for revert)"
sudo systemctl disable --now bonsai 2>/dev/null || true
sudo systemctl restart ollama
sleep 3

step "Pull $TAG (~4.2 GB if not present)"
ollama pull "$TAG" || die "pull failed — check the uplink and retry"
ollama list 2>/dev/null | grep -i bonsai || true

step "Prove Bonsai generates on THIS board's Ollama (first load takes minutes)"
# The Jetson kernel refuses big GPU allocations while the file cache is full
# (L4T r36.4.x) — drop caches right before the load, same trick the old
# service used at start.
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
OUT=$(curl -s --max-time 420 http://127.0.0.1:11434/v1/chat/completions \
    -H 'content-type: application/json' \
    -d "{\"model\":\"$TAG\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in five words.\"}],\"max_tokens\":30}")
echo "$OUT" | head -c 400; echo
echo "$OUT" | grep -q '"content"' \
    || die "Bonsai did not generate through Ollama on this board — Ferry left UNTOUCHED.
       Likely causes: Ollama still too old for Q1_0, or a GPU alloc failure
       (journalctl -u ollama -n 40). Instant revert to the working sidecar:
       sudo systemctl enable --now bonsai   (then re-run jetson_bonsai_setup.sh)"

step "Repoint Ferry at Ollama-served Bonsai"
cd "$REPO_DIR"
[ -s .env ] && [ -n "$(tail -c1 .env)" ] && echo >> .env
set_env() { grep -q "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }
set_env OLLAMA_BASE_URL "http://127.0.0.1:11434/v1"
set_env LOCAL_MODEL "$TAG"
set_env LOCAL_MAX_TOKENS 400
set_env LOCAL_TIMEOUT_SECONDS 300
set_env LOCAL_TEMPERATURE 0.7
set_env LOCAL_CONTEXT_CHARS 1600
set_env EXTRA_LOCAL_MODELS "$LIQUID"
set_env EXPOSE_ROUTER_MODEL false
# Everything is on one Ollama again — no separate extras backend.
sed -i '/^EXTRA_LOCAL_BASE_URL=/d' .env
sudo systemctl restart ferry
sleep 3

step "Smoke test through Ferry"
curl -s --max-time 5 http://localhost:8080/api/status; echo
curl -sN --max-time 300 http://localhost:8080/v1/chat/completions \
    -H 'content-type: application/json' \
    -d '{"model":"ferry","stream":true,"messages":[{"role":"user","content":"Say hello in five words."}]}' \
    | grep -o '"content": "[^"]*"' | head -5

echo
echo "== Done. Bonsai now runs on Ollama as $TAG =="
echo "Picker: $TAG (default) + $LIQUID."
echo "NOTE: switching between the two models swaps them in memory (~1-2 min on"
echo "the swap back to Bonsai). The last-used model stays warm indefinitely."
echo "Re-seed the UI default to the new model id:"
echo "  RESET_OPENWEBUI=1 ./scripts/jetson_branding.sh"
