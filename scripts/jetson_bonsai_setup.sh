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
if systemctl is-active --quiet bonsai 2>/dev/null; then
    echo "bonsai.service already running — memory is largely in use by it, that's fine."
elif [ "${FREE_MEM_MB:-0}" -lt 5500 ]; then
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

step "Platform check (L4T / nvmap allocation policy)"
L4T=$(head -1 /etc/nv_tegra_release 2>/dev/null || echo "unknown")
echo "L4T: $L4T"
if echo "$L4T" | grep -q 'R36.*REVISION: 4\.'; then
    echo "NOTE: L4T r36.4.x ships a stricter nvmap policy (late-2025 security updates):"
    echo "      multi-GB CUDA allocations fail with 'NvMap... error 12' whenever the page"
    echo "      cache is full, even though that memory is reclaimable. This script works"
    echo "      around it (--no-mmap --direct-io + a cache drop at service start), and"
    echo "      falls back to partial GPU offload if a full load still fails."
fi

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
# Progress percentages stream to the terminal (full log: /tmp/bonsai_build.log).
# The `|| true` keeps a no-match grep from failing the pipeline under pipefail —
# only cmake's own exit code decides success. Expect 60-90 min on first build;
# re-runs resume from the compiled objects.
show_progress() { grep --line-buffered -E '\[ *[0-9]+%\]|[Ee]rror' || true; }
build_cuda() {
    cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=87 > /tmp/bonsai_build.log 2>&1 || return 1
    cmake --build build -j2 2>&1 | tee -a /tmp/bonsai_build.log | show_progress
}
build_cpu() {
    rm -rf build
    cmake -B build > /tmp/bonsai_build.log 2>&1 || return 1
    cmake --build build -j"$(nproc)" 2>&1 | tee -a /tmp/bonsai_build.log | show_progress
}
if command -v nvcc >/dev/null && build_cuda; then
    BUILD_KIND="CUDA (GPU)"
else
    echo "CUDA build unavailable/failed — tail of /tmp/bonsai_build.log:"
    tail -5 /tmp/bonsai_build.log 2>/dev/null || true
    echo "retrying CPU-only build (works, but expect ~1-3 tok/s)"
    build_cpu || die "CPU build failed too — see /tmp/bonsai_build.log"
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

step "Tame Ollama's memory (so the picker fallback can't OOM Bonsai)"
# Liquid loads on demand next to the resident Bonsai; keep it short-lived and
# never allow two Ollama models resident at once.
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/ferry-memory.conf > /dev/null <<'CONF'
[Service]
Environment=OLLAMA_KEEP_ALIVE=1m
Environment=OLLAMA_MAX_LOADED_MODELS=1
CONF

step "Optional: go headless (frees a few hundred MB for the model)"
if [ "${BONSAI_HEADLESS:-0}" = "1" ]; then
    sudo systemctl set-default multi-user.target
    sudo systemctl stop gdm 2>/dev/null || sudo systemctl stop gdm3 2>/dev/null || true
    echo "desktop UI disabled (revert: sudo systemctl set-default graphical.target && sudo systemctl start gdm)"
else
    echo "skipped — re-run with BONSAI_HEADLESS=1 to disable the desktop UI on this hub"
fi

step "Install bonsai.service (llama-server on 127.0.0.1:$PORT)"
UNIT_FILE=/etc/systemd/system/bonsai.service
# Why these flags:
#   --no-mmap --direct-io  load ORDER is what matters on Jetson: the default
#       mmap load faults the whole 3.8 GB GGUF into page cache BEFORE the
#       ~3.4 GiB CUDA weight-buffer alloc, and L4T r36.4.x nvmap refuses any
#       allocation that would need cache reclaim (NvMap error 12) — so the
#       ExecStartPre cache drop alone gets undone during load. These two keep
#       the file out of the cache so the buffer is allocated while memory is
#       genuinely free.
#   --fit-target 2048      auto-fit sizes offload from MemAvailable, which
#       overstates what nvmap will actually grant; a 2 GiB margin makes it
#       degrade offload gracefully instead of overcommitting.
#   -fa on --min-p 0       PrismML's own serving settings for Bonsai.
#   --parallel 2           Ferry's quick router probes don't queue behind a
#       long streaming answer (hybrid-attention KV is small; 2 slots are cheap).
#   No GGML_CUDA_ENABLE_UNIFIED_MEMORY: on Tegra, cudaMallocManaged allocates
#       through nvmap all the same — it dodges nothing and is slower.
render_unit() {  # $1 = extra llama-server flags (used by the fallback ladder)
    EXTRA_FLAGS="$1"
    cat <<UNIT
[Unit]
Description=Bonsai 27B (1-bit) local model - llama.cpp server (PrismML fork)
After=network.target
StartLimitIntervalSec=0

[Service]
User=$USER
ExecStartPre=+/bin/sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
ExecStart=$FORK_DIR/build/bin/llama-server -m $MODEL_DIR/$MODEL_FILE --host 127.0.0.1 --port $PORT -c 4096 --parallel 2 --no-mmap --direct-io -fa on --fit-target 2048 --reasoning-budget 0 --temp 0.7 --top-p 0.95 --top-k 20 --min-p 0${EXTRA_FLAGS:+ $EXTRA_FLAGS}
Restart=always
RestartSec=20

[Install]
WantedBy=multi-user.target
UNIT
}
install_unit() { render_unit "$1" | sudo tee "$UNIT_FILE" > /dev/null; sudo systemctl daemon-reload; }
wait_health() {  # $1 = max seconds
    local h=""
    for _ in $(seq 1 $(( $1 / 5 ))); do
        h=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" || true)
        [ "$h" = "200" ] && return 0
        sleep 5
    done
    return 1
}

cur_health=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" || true)
if [ "$cur_health" = "200" ] && render_unit "" | cmp -s - "$UNIT_FILE" 2>/dev/null; then
    echo "bonsai.service already healthy with the current flags — leaving the running model alone"
    sudo systemctl enable bonsai 2>/dev/null || true
else
    # Unload any resident Ollama model right before Bonsai allocates.
    sudo systemctl restart ollama 2>/dev/null || true
    ok=""
    used_flags=""
    for extra in "" "-fit off -ngl 24 -nkvo" "-fit off -ngl 12 -nkvo"; do
        if [ -n "$extra" ]; then
            echo "load failed — journal tail:"
            journalctl -u bonsai -n 6 --no-pager 2>/dev/null | tail -4 || true
            echo "retrying with reduced GPU offload: $extra"
        fi
        install_unit "$extra"
        sudo systemctl enable bonsai 2>/dev/null || true
        sudo systemctl restart bonsai
        echo "waiting for the model to load (up to 5 min) ..."
        if wait_health 300; then ok=1; used_flags="$extra"; break; fi
    done
    [ -n "$ok" ] || die "bonsai.service never became healthy — check: journalctl -u bonsai -n 50 (revert to Liquid: JETSON_SSH_UPDATE.md section 4)"
    if [ -n "$used_flags" ]; then
        echo "WARN: DEGRADED MODE — running with '$used_flags' (part of the model on CPU, slower)."
        echo "      Re-run this script later (e.g. after a reboot or L4T upgrade) to retry full GPU offload."
    fi
fi

step "Pull the Liquid fallback into Ollama (official tag, ~700 MB)"
# NOTE: nemotron-3-nano:4b is deliberately NOT in the picker beside Bonsai —
# it needs ~3.4 GB to load and the 8 GB board cannot hold it while Bonsai
# (~5 GB) is resident: picking it just returns out-of-memory. Liquid (~1.2 GB
# loaded) is the one extra that fits.
if command -v ollama >/dev/null; then
    ollama list 2>/dev/null | grep -qi '^LiquidAI/lfm2.5-1.2b-instruct' \
        || ollama pull LiquidAI/lfm2.5-1.2b-instruct \
        || echo "WARN: Liquid pull failed; retry later with: ollama pull LiquidAI/lfm2.5-1.2b-instruct"
fi

step "Repoint Ferry at Bonsai (default; Liquid stays in the picker as fallback)"
cd "$REPO_DIR"
[ -s .env ] && [ -n "$(tail -c1 .env)" ] && echo >> .env
set_env() { grep -q "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }
set_env OLLAMA_BASE_URL "http://127.0.0.1:$PORT/v1"
set_env LOCAL_MODEL "1-bit-Bonsai-27B"
set_env LOCAL_MAX_TOKENS 400
set_env LOCAL_TIMEOUT_SECONDS 180
set_env EXTRA_LOCAL_MODELS "LiquidAI/lfm2.5-1.2b-instruct"
set_env EXTRA_LOCAL_BASE_URL "http://127.0.0.1:11434/v1"
set_env EXPOSE_ROUTER_MODEL false
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

Revert to Liquid as the default at any time:
  cd ~/cerebras-gemma-hack
  set_env() { grep -q "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }
  set_env OLLAMA_BASE_URL http://localhost:11434/v1
  set_env LOCAL_MODEL LiquidAI/lfm2.5-1.2b-instruct
  set_env LOCAL_MAX_TOKENS 64
  set_env LOCAL_TIMEOUT_SECONDS 45
  set_env EXTRA_LOCAL_MODELS "nemotron-3-nano:4b,LiquidAI/lfm2.5-1.2b-instruct"
  ollama pull nemotron-3-nano:4b               # fits again once Bonsai is gone
  sed -i '/^EXTRA_LOCAL_BASE_URL=/d' .env      # extras follow OLLAMA_BASE_URL again
  sudo systemctl restart ferry
  sudo systemctl disable --now bonsai          # frees ~5 GB of memory
EOF
