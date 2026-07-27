#!/usr/bin/env bash
# Re-runnable: give the Jetson's chat UI the same look as the Base Drive
# product — "Offline Base" name (no " (Open WebUI)" suffix), Offline Base
# favicon/logo/splash artwork, the navy/teal brand palette — running with NO
# login (appliance mode) and the Ferry connection pre-seeded.
#
# SSH into the Jetson, then:
#   cd ~/cerebras-gemma-hack && ./scripts/jetson_branding.sh
#
# First time switching to no-login mode (or if you forgot the old password):
#   RESET_OPENWEBUI=1 ./scripts/jetson_branding.sh
# That DELETES the open-webui volume (accounts + chat history). It is required
# once because Open WebUI refuses WEBUI_AUTH=False while any account exists.
#
# ---------------------------------------------------------------------------
# LICENSE NOTE (mirrors Offline-Base/base-drive's branding.env decision)
# ---------------------------------------------------------------------------
# The Open WebUI backend license (Clause 4) forbids altering or removing
# "Open WebUI" branding EXCEPT in deployments of 50 or fewer end users in any
# rolling 30-day period (or with written permission / an enterprise license).
# This script de-brands ONE private hub used by its owner and guests — inside
# exception 4(i). DO NOT sell this hub or open it to an unbounded audience
# while branded this way; that would be a material breach. The Settings→About
# copyright notice is retained (Clause 1). See base-drive's branding.env,
# LICENSE-MATRIX.md and docs/demo-branding.md for the full reasoning.
# ---------------------------------------------------------------------------
#
# How it works: the Offline Base assets live in this repo (brand/webui/, taken
# from base-drive's brand/generated/webui/). Each run re-extracts env.py and
# index.html from the CURRENT image, patches them (exact-match, loud fallback),
# stages everything under ~/.offlinebase-brand/, and bind-mounts the staged
# files over the container's own. An image update just needs a re-run.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE=ghcr.io/open-webui/open-webui:main
BRAND_SRC="$REPO_DIR/brand/webui"
STAGE="$HOME/.offlinebase-brand"
PRODUCT="Offline Base"

[ -d "$BRAND_SRC" ] || { echo "ERROR: $BRAND_SRC missing — git pull first." >&2; exit 1; }

if [ "${RESET_OPENWEBUI:-0}" = "1" ]; then
    echo "== RESET_OPENWEBUI=1: wiping Open WebUI data (accounts + chats) =="
    docker rm -f open-webui 2>/dev/null || true
    docker volume rm open-webui 2>/dev/null || true
fi

echo "== Staging Offline Base branding in $STAGE =="
# Make sure the image is available BEFORE removing the running container, so a
# dead uplink (this hub's normal condition) can't leave us with no UI at all.
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker pull "$IMAGE" \
        || { echo "ERROR: image not present locally and pull failed — existing UI left untouched." >&2; exit 1; }
fi

rm -rf "$STAGE" && mkdir -p "$STAGE/static"
cp "$BRAND_SRC"/* "$STAGE/static/"

# Brand palette — Open WebUI ships custom.css as an empty hook loaded from
# index.html; unlayered rules beat its @layer-scoped Tailwind variables.
# Sampled from base-drive's offline-base-mark.png (navy #101820 / teal #3888A0).
cat > "$STAGE/static/custom.css" <<'CSS'
/* Offline Base brand palette - written by scripts/jetson_branding.sh */
:root {
  --color-blue-300: #8fc0ce;
  --color-blue-400: #6caabc;
  --color-blue-500: #3888a0;
  --color-blue-600: #2f7387;
  --color-blue-700: #265d6d;
  --color-blue-800: #1d4754;
  --color-blue-900: #101820;
}
CSS

# Pull env.py + index.html out of the current image and patch them.
docker rm -f owui-extract 2>/dev/null || true
docker create --name owui-extract "$IMAGE" >/dev/null \
    || { echo "ERROR: could not create extraction container." >&2; exit 1; }
docker cp owui-extract:/app/backend/open_webui/env.py "$STAGE/env.py"
docker cp owui-extract:/app/build/index.html "$STAGE/index.html"
docker rm owui-extract >/dev/null

ENV_PATCHED=""
if python3 - "$STAGE/env.py" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); t = p.read_text()
if "BASE-JETSON-BRANDING" in t:
    sys.exit(0)
blocks = [
    "if WEBUI_NAME != 'Open WebUI':\n    WEBUI_NAME += ' (Open WebUI)'\n",
    'if WEBUI_NAME != "Open WebUI":\n    WEBUI_NAME += " (Open WebUI)"\n',
]
m = [b for b in blocks if b in t]
if len(m) != 1:
    sys.exit("env.py suffix block not found (%d matches) — upstream changed; keeping the suffix." % len(m))
line = m[0].split("\n")[1]
rep = ("# --- BASE-JETSON-BRANDING: ' (Open WebUI)' suffix disabled on this private\n"
       "# hub only, under Open WebUI License Clause 4(i) (<=50 end users). ---\n"
       "if False:\n" + line + "\n"
       "# --- end BASE-JETSON-BRANDING ---\n")
p.write_text(t.replace(m[0], rep, 1))
PY
then ENV_PATCHED=1; echo "env.py: suffix append disabled"
else echo "WARN: env.py patch skipped — the UI will read '$PRODUCT (Open WebUI)'"; fi

if grep -q '<title>Open WebUI</title>' "$STAGE/index.html"; then
    sed -i "s|<title>Open WebUI</title>|<title>$PRODUCT</title>|" "$STAGE/index.html"
    echo "index.html: tab title = $PRODUCT"
else
    grep -q "<title>$PRODUCT</title>" "$STAGE/index.html" \
        || echo "WARN: index.html title not found — pre-load tab title stays 'Open WebUI'"
fi

echo "== Recreating open-webui ($PRODUCT, no login, Ferry pre-wired) =="
docker rm -f open-webui 2>/dev/null || true

# Mount every staged asset over BOTH copies the app serves: the frontend build
# (/app/build/static) and the backend (/app/backend/open_webui/static — its
# favicon.png is also the assistant avatar next to every reply).
MOUNTS=(-v "$STAGE/index.html:/app/build/index.html")
[ -n "$ENV_PATCHED" ] && MOUNTS+=(-v "$STAGE/env.py:/app/backend/open_webui/env.py")
for f in "$STAGE/static/"*; do
    base=$(basename "$f")
    MOUNTS+=(-v "$f:/app/build/static/$base" -v "$f:/app/backend/open_webui/static/$base")
done
MOUNTS+=(-v "$STAGE/static/favicon.png:/app/build/favicon.png")

# On a FRESH volume these envs fully configure the UI at first boot:
#   OPENAI_API_BASE_URL  -> Ferry on the host (key is a dummy; Ferry ignores auth)
#   ENABLE_OLLAMA_API=false -> picker shows ONLY Ferry's models: 1-bit-Bonsai-27B
#                              and LiquidAI/lfm2.5-1.2b-instruct, nothing else
#   DEFAULT_MODELS       -> new chats preselect Bonsai
# (On a kept volume, DB values from the old setup win over these seeds.)
docker run -d -p 3000:8080 \
    --add-host=host.docker.internal:host-gateway \
    -v open-webui:/app/backend/data \
    "${MOUNTS[@]}" \
    -e WEBUI_NAME="$PRODUCT" \
    -e WEBUI_AUTH=False \
    -e DEFAULT_MODELS="1-bit-Bonsai-27B" \
    -e OPENAI_API_BASE_URL="http://host.docker.internal:8080/v1" \
    -e OPENAI_API_KEY="ferry-local" \
    -e ENABLE_OLLAMA_API=false \
    -e ENABLE_EVALUATION_ARENA_MODELS=false \
    --name open-webui --restart unless-stopped \
    "$IMAGE" \
    || { echo "ERROR: docker run failed — chat UI is DOWN. Check: docker logs open-webui" >&2; exit 1; }

echo
echo "Waiting for the UI to come up (cold start can take a couple of minutes)..."
code=""
for _ in $(seq 1 60); do
    code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3000 || true)
    [ "$code" = "200" ] && break
    sleep 3
done
ip=$(hostname -I | awk '{print $1}')
if [ "$code" = "200" ]; then
    echo "Done — open http://${ip}:3000 ; no login, the UI reads \"$PRODUCT\" with the"
    echo "Offline Base artwork. Hard-refresh (or clear site data) — browsers cache the"
    echo "old favicon and splash aggressively."
else
    echo "WARN: UI not serving after 3 min. Check: docker logs open-webui" >&2
    echo "      If the log says auth/users can't be disabled: old accounts are blocking" >&2
    echo "      no-login mode — re-run as: RESET_OPENWEBUI=1 ./scripts/jetson_branding.sh" >&2
    exit 1
fi
