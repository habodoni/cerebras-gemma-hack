#!/usr/bin/env bash
# One-time: make 1-bit Bonsai 27B the Jetson's local model (replacing Liquid as
# the default; Liquid stays pulled in Ollama as an instant fallback).
#
# SSH into the Jetson, then:
#   cd ~/cerebras-gemma-hack && ./scripts/jetson_bonsai_setup.sh
#
# Why it works this way: Bonsai's 1-bit Q1_0 kernels exist only in PrismML's
# llama.cpp fork — stock Ollama cannot load this GGUF. So Bonsai runs as its own
# systemd service (llama-server on 127.0.0.1:11435) and Ferry's .env is
# repointed at it. Ollama keeps running on 11434 for the extra picker models.
#
# Footprint on the 8 GB Orin Nano: weights ~3.8 GB + KV/compute ~1.5 GB.
# Expect roughly 5-12 tok/s on the GPU — a 27B on an 8 GB board is a capability
# demo, not a speed demo. Thinking is disabled (--reasoning-budget 0) to keep
# answers arriving in seconds; to re-enable it, remove that flag from
# /etc/systemd/system/bonsai.service, then:
#   sudo systemctl daemon-reload && sudo systemctl restart bonsai
#
# Safe to re-run: every step is idempotent and the service picks up unit changes.
set -uo pipefail

FORK_DIR="$HOME/llama.cpp-prism"
MODEL_DIR="$HOME/models/bonsai"
MODEL_FILE="Bonsai-27B-Q1_0.gguf"          # 3.80 GB, verified on HF 2026-07-14
HF_REPO="prism-ml/Bonsai-27B-gguf"
PORT=11435
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_KIND="unknown"

step() { echo; echo "== $* =="; }
die()  { echo "ERROR: $*" >&2; exit 1; }

step "Preflight"
FREE_GB=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc '0-9')
[ "${FREE_GB:-0}" -ge 8 ] || die "need >=8 GB free in \$HOME (have ${FREE_GB:-?} GB)"
FREE_MEM_MB=$(free -m | awk '/^Mem:/{print $7}')
if [ "${FREE_MEM_MB:-0}" -lt 5500 ]; then
    echo "WARN: only ${FREE_MEM_MB} MB memory available — Bonsai needs ~5.3 GB resident."
    echo "      Consider closing other workloads; continuing anyway."
fi
if ! command -v cmake >/dev/null || ! command -v g++ >/dev/null; then
    sudo apt-get update && sudo apt-get install -y cmake build-essential || die "apt install failed"
fi
# JetPack installs CUDA at /usr/local/cuda but does NOT put nvcc on PATH.
if [ -x /usr/local/cuda/bin/nvcc ]; then
    export PATH="/usr/local/cuda/bin:$PATH"
fi
command -v nvcc >/dev/null && echo "nvcc: $(command -v nvcc)" \
    || echo "WARN: nvcc not found — the build will fall back to CPU (slow). Install/repair CUDA (JetPack) for GPU speed."

step "Clone/update the PrismML llama.cpp fork (has the 1-bit kernels)"
if [ -d "$FORK_DIR/.git" ]; then
    git -C "$FORK_DIR" pull --ff-only || true
else
    git clone --depth 1 https://github.com/PrismML-Eng/llama.cpp "$FORK_DIR" || die "clone failed"
fi

step "Build llama-server (CUDA sm_87 for the Orin; CPU fallback if that fails)"
cd "$FORK_DIR"
# -j2: nvcc on the CUDA sources easily eats >1.5 GB per job; higher parallelism
# OOMs the 8 GB board and would silently discard the GPU build.
if command -v nvcc >/dev/null && \
   { cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=87 && cmake --build build -j2; } > /tmp/bonsai_build.log 2>&1; then
    BUILD_KIND="CUDA (GPU)"
else
    echo "CUDA build unavailable/failed — tail of /tmp/bonsai_build.log:"
    tail -5 /tmp/bonsai_build.log 2>/dev/null || true
    echo "retrying CPU-only build (works, but expect ~1-3 tok/s)"
    rm -rf build
    { cmake -B build && cmake --build build -j"$(nproc)"; } > /tmp/bonsai_build.log 2>&1 \
        || die "CPU build failed too — see /tmp/bonsai_build.log"
    BUILD_KIND="CPU only (slow — fix CUDA and re-run for GPU speed)"
fi
[ -x "$FORK_DIR/build/bin/llama-server" ] || die "llama-server binary missing after build"
echo "build kind: $BUILD_KIND"

step "Download $MODEL_FILE (~3.8 GB — grab a coffee)"
mkdir -p "$MODEL_DIR"
if [ ! -f "$MODEL_DIR/$MODEL_FILE" ]; then
    "$REPO_DIR/.venv/bin/pip" install -q -U "huggingface_hub[cli]"
    "$REPO_DIR/.venv/bin/hf" download "$HF_REPO" "$MODEL_FILE" --local-dir "$MODEL_DIR" || die "download failed"
fi
[ -f "$MODEL_DIR/$MODEL_FILE" ] || die "model file missing after download"

step "Tame Ollama's memory (so picker extras can't OOM Bonsai)"
# nemotron loads on demand next to the resident Bonsai; keep it short-lived and
# never allow two Ollama models resident at once.
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/ferry-memory.conf > /dev/null <<'CONF'
[Service]
Environment=OLLAMA_KEEP_ALIVE=1m
Environment=OLLAMA_MAX_LOADED_MODELS=1
CONF

step "Install + (re)start bonsai.service (llama-server on 127.0.0.1:$PORT)"
# --parallel 2 keeps Ferry's quick router probes from queueing behind a long
# streaming answer (the hybrid-attention KV is small, so 2 slots are cheap).
sudo tee /etc/systemd/system/bonsai.service > /dev/null <<UNIT
[Unit]
Description=Bonsai 27B (1-bit) local model - llama.cpp server (PrismML fork)
After=network.target

[Service]
User=$USER
Environment=GGML_CUDA_ENABLE_UNIFIED_MEMORY=1
ExecStart=$FORK_DIR/build/bin/llama-server -m $MODEL_DIR/$MODEL_FILE --host 127.0.0.1 --port $PORT -c 8192 --parallel 2 --reasoning-budget 0 --temp 0.7 --top-p 0.95 --top-k 20
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable bonsai
sudo systemctl restart bonsai ollama

step "Waiting for the model to load (up to 5 min on first start)"
health=""
for _ in $(seq 1 60); do
    health=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" || true)
    [ "$health" = "200" ] && break
    sleep 5
done
[ "$health" = "200" ] || die "bonsai.service not healthy — check: journalctl -u bonsai -n 50"

step "Pull the extra picker model into Ollama (nemotron-3-nano:4b, ~2.8 GB)"
if command -v ollama >/dev/null && ! ollama list 2>/dev/null | grep -q '^nemotron-3-nano:4b'; then
    ollama pull nemotron-3-nano:4b || echo "WARN: nemotron pull failed; retry later with: ollama pull nemotron-3-nano:4b"
fi

step "Repoint Ferry at Bonsai (Liquid stays in Ollama as fallback)"
cd "$REPO_DIR"
[ -s .env ] && [ -n "$(tail -c1 .env)" ] && echo >> .env
set_env() { grep -q "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }
set_env OLLAMA_BASE_URL "http://127.0.0.1:$PORT/v1"
set_env LOCAL_MODEL "1-bit-Bonsai-27B"
set_env LOCAL_MAX_TOKENS 400
set_env LOCAL_TIMEOUT_SECONDS 180
set_env EXTRA_LOCAL_MODELS "nemotron-3-nano:4b"
set_env EXTRA_LOCAL_BASE_URL "http://127.0.0.1:11434/v1"
sudo systemctl restart ferry
sleep 3

step "Smoke test"
curl -s --max-time 5 http://localhost:8080/api/status; echo
echo "(short chat through Ferry -> Bonsai; first answer includes model warm-up)"
curl -sN --max-time 180 http://localhost:8080/v1/chat/completions \
    -H 'content-type: application/json' \
    -d '{"model":"ferry","stream":true,"messages":[{"role":"user","content":"Say hello in five words."}]}' \
    | grep -o '"content": "[^"]*"' | head -5

echo
echo "== Done. Bonsai 27B (1-bit) is the local model — build: $BUILD_KIND =="
cat <<'EOF'

Revert to Liquid at any time (bcluzel/... is the tag actually pulled on this Jetson):
  cd ~/cerebras-gemma-hack
  set_env() { grep -q "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }
  set_env OLLAMA_BASE_URL http://localhost:11434/v1
  set_env LOCAL_MODEL bcluzel/LFM2.5-1.2B-Instruct:Q4_K_M
  set_env LOCAL_MAX_TOKENS 64
  set_env LOCAL_TIMEOUT_SECONDS 45
  sed -i '/^EXTRA_LOCAL_BASE_URL=/d' .env      # extras follow OLLAMA_BASE_URL again
  sudo systemctl restart ferry
  sudo systemctl disable --now bonsai          # frees ~5 GB of memory
EOF
