#!/usr/bin/env bash
# Ferry self-service updater for the Jetson hub.
#
# SSH into the Jetson, then:
#   cd ~/cerebras-gemma-hack && git pull && ./scripts/jetson_update.sh
#
# What it does, every run (all steps are idempotent):
#   1. git pull (fast-forward only; harmless no-op if you already pulled)
#   2. pip install when requirements.txt differs from the last installed set
#      (tracked by a content hash, so it works no matter who ran git pull)
#   3. one-time .env catch-up values (never stomps values you customized;
#      one exception: a legacy-default CEREBRAS_MAX_TOKENS is raised once,
#      because small budgets return empty answers when reasoning is on)
#   4. pulls the extra picker model (nemotron) into Ollama if missing
#   5. restarts ferry + health check
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

echo "== Ferry updater ($(date '+%Y-%m-%d %H:%M')) =="

BEFORE=$(git rev-parse --short HEAD)
if ! git pull --ff-only; then
    echo "ERROR: git pull failed (diverged history or no network)." >&2
    exit 1
fi
AFTER=$(git rev-parse --short HEAD)
echo "code: $BEFORE -> $AFTER"

# --- deps: install when requirements.txt content differs from last install ---
REQ_SHA=$(sha256sum requirements.txt | cut -d' ' -f1)
STAMP=.venv/.requirements.sha256
if [ "$REQ_SHA" != "$(cat "$STAMP" 2>/dev/null)" ]; then
    echo "requirements changed -> installing deps"
    if .venv/bin/pip install -r requirements.txt; then
        echo "$REQ_SHA" > "$STAMP"
    else
        echo "ERROR: pip install failed — NOT restarting ferry (old code keeps running)." >&2
        exit 1
    fi
fi

# --- .env catch-up -----------------------------------------------------------
# Guard: make sure the file ends in a newline so appends can't merge lines.
[ -s .env ] && [ -n "$(tail -c1 .env)" ] && echo >> .env
# ensure_env appends only when the key is absent — customized values are kept.
ensure_env() { grep -q "^$1=" .env || { echo "$1=$2" >> .env; echo "  .env + $1=$2"; }; }

ensure_env CEREBRAS_THINK_EFFORT medium
ensure_env NOTIFY_MODE ntfy
ensure_env NTFY_TOPIC ferry-ethan-7q3v9k2x
ensure_env PUBLIC_BASE_URL http://192.168.1.62:8080
ensure_env EXTRA_LOCAL_MODELS nemotron-3-nano:4b

# Raise CEREBRAS_MAX_TOKENS ONLY off the known legacy defaults — a value you
# chose yourself (anything else) is left alone.
cur=$(grep -m1 '^CEREBRAS_MAX_TOKENS=' .env | cut -d= -f2 | tr -dc '0-9' || true)
if [ -z "${cur:-}" ]; then
    ensure_env CEREBRAS_MAX_TOKENS 8192
elif [ "$cur" = "1024" ] || [ "$cur" = "500" ]; then
    sed -i 's|^CEREBRAS_MAX_TOKENS=.*|CEREBRAS_MAX_TOKENS=8192|' .env
    echo "  .env CEREBRAS_MAX_TOKENS $cur -> 8192 (legacy default was too small for reasoning)"
fi

# --- extra picker model ------------------------------------------------------
if command -v ollama >/dev/null && ! ollama list 2>/dev/null | grep -q '^nemotron-3-nano:4b'; then
    echo "pulling nemotron-3-nano:4b (~2.8 GB) ..."
    ollama pull nemotron-3-nano:4b || echo "WARN: nemotron pull failed; the picker entry will error until it is pulled"
fi

# --- restart + verify --------------------------------------------------------
sudo systemctl restart ferry
ok=""
for _ in $(seq 1 15); do
    if curl -s --max-time 3 http://localhost:8080/api/status >/tmp/ferry_status.json 2>/dev/null; then
        ok=1; break
    fi
    sleep 2
done
if [ -n "$ok" ]; then
    cat /tmp/ferry_status.json; echo
    echo "== done: ferry is healthy =="
else
    echo "ERROR: ferry not responding after 30s — check: journalctl -u ferry -n 50" >&2
    exit 1
fi
