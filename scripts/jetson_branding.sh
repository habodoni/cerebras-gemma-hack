#!/usr/bin/env bash
# Re-runnable: brand the chat UI as "OfflineBase (Open WebUI)", run it with NO
# login (appliance mode), and pre-seed the Ferry connection + Bonsai default.
#
# SSH into the Jetson, then:
#   cd ~/cerebras-gemma-hack && ./scripts/jetson_branding.sh
#
# First time switching to no-login mode (or if you forgot the old password):
#   RESET_OPENWEBUI=1 ./scripts/jetson_branding.sh
# That DELETES the open-webui volume (accounts + chat history). It is required
# once because Open WebUI refuses WEBUI_AUTH=False while any account exists.
#
# WEBUI_AUTH=False means anyone on the hub's network can chat — that is the
# point of this appliance. To go multi-user later: set it back to True here
# and run once more with RESET_OPENWEBUI=1.
set -uo pipefail

if [ "${RESET_OPENWEBUI:-0}" = "1" ]; then
    echo "== RESET_OPENWEBUI=1: wiping Open WebUI data (accounts + chats) =="
    docker rm -f open-webui 2>/dev/null || true
    docker volume rm open-webui 2>/dev/null || true
fi

echo "== Recreating open-webui (OfflineBase, no login, Ferry pre-wired) =="
# Make sure the image is available BEFORE removing the running container, so a
# dead uplink (this hub's normal condition) can't leave us with no UI at all.
if ! docker image inspect ghcr.io/open-webui/open-webui:main >/dev/null 2>&1; then
    docker pull ghcr.io/open-webui/open-webui:main \
        || { echo "ERROR: image not present locally and pull failed — existing UI left untouched." >&2; exit 1; }
fi

docker rm -f open-webui 2>/dev/null || true
# On a FRESH volume these envs fully configure the UI at first boot:
#   OPENAI_API_BASE_URL  -> Ferry on the host (key is a dummy; Ferry ignores auth)
#   ENABLE_OLLAMA_API=false -> picker shows only Ferry's models, no raw-Ollama dupes
#   DEFAULT_MODELS       -> new chats preselect Bonsai
# (On a kept volume, DB values from the old setup win over these seeds.)
docker run -d -p 3000:8080 \
    --add-host=host.docker.internal:host-gateway \
    -v open-webui:/app/backend/data \
    -e WEBUI_NAME="OfflineBase" \
    -e WEBUI_AUTH=False \
    -e DEFAULT_MODELS="1-bit-Bonsai-27B" \
    -e OPENAI_API_BASE_URL="http://host.docker.internal:8080/v1" \
    -e OPENAI_API_KEY="ferry-local" \
    -e ENABLE_OLLAMA_API=false \
    -e ENABLE_EVALUATION_ARENA_MODELS=false \
    --name open-webui --restart unless-stopped \
    ghcr.io/open-webui/open-webui:main \
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
    echo "Done — open http://${ip}:3000 ; no login, header reads \"OfflineBase (Open WebUI)\"."
else
    echo "WARN: UI not serving after 3 min. Check: docker logs open-webui" >&2
    echo "      If the log says auth/users can't be disabled: old accounts are blocking" >&2
    echo "      no-login mode — re-run as: RESET_OPENWEBUI=1 ./scripts/jetson_branding.sh" >&2
    exit 1
fi
