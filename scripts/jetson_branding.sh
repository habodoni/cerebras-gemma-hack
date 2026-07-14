#!/usr/bin/env bash
# One-time: brand the chat UI as "OfflineBase (Open WebUI)".
#
# SSH into the Jetson, then:
#   cd ~/cerebras-gemma-hack && ./scripts/jetson_branding.sh
#
# WEBUI_NAME is a boot-time env var, so the container must be recreated with it.
# All data (users, chats, the Ferry connection) lives in the named volume
# `open-webui`, which survives the recreate — this is Open WebUI's own
# documented update flow.
set -uo pipefail

echo "== Recreating open-webui with OfflineBase branding =="
# Make sure the image is available BEFORE removing the running container, so a
# dead uplink (this hub's normal condition) can't leave us with no UI at all.
if ! docker image inspect ghcr.io/open-webui/open-webui:main >/dev/null 2>&1; then
    docker pull ghcr.io/open-webui/open-webui:main \
        || { echo "ERROR: image not present locally and pull failed — existing UI left untouched." >&2; exit 1; }
fi

docker rm -f open-webui 2>/dev/null || true
# DEFAULT_MODELS seeds Bonsai as the default for new chats. Note: Open WebUI
# persists this in its DB after first boot, so if a default was already saved,
# change it in the UI instead: Admin Panel -> Settings -> Interface -> Default Model.
docker run -d -p 3000:8080 \
    --add-host=host.docker.internal:host-gateway \
    -v open-webui:/app/backend/data \
    -e WEBUI_NAME="OfflineBase" \
    -e DEFAULT_MODELS="1-bit-Bonsai-27B" \
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
    echo "Done — open http://${ip}:3000 ; the header/tab now reads \"OfflineBase (Open WebUI)\"."
else
    echo "WARN: UI not serving yet after 3 min — it may still be starting. Check: docker logs open-webui" >&2
    exit 1
fi
