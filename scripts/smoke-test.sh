#!/usr/bin/env bash
# Boot the image without any audio hardware and check the basics:
# every binary is present and runnable, and the web UI answers.
#
# Usage: scripts/smoke-test.sh <image>
set -euo pipefail

IMAGE="${1:?usage: smoke-test.sh <image>}"
NAME="sendspin-shareplay-smoke-$$"
PORT=18080

cleanup() {
    docker logs "$NAME" 2>&1 | tail -50 || true
    docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "--- installed versions"
docker run --rm --entrypoint /bin/bash "$IMAGE" -c '
    set -e
    shairport-sync -V
    /usr/local/bin/nqptp -V || true
    /opt/venv/bin/sendspin --version
    /opt/venv/bin/python -c "import nowplaying, dbus_fast, aiohttp; print(\"nowplaying\", nowplaying.__version__)"
'

echo "--- booting with audio disabled"
docker run -d --name "$NAME" \
    -e ENABLE_AIRPLAY=0 \
    -e ENABLE_SENDSPIN=0 \
    -e AUDIO_SHARING=none \
    -e WEB_PORT=8080 \
    -p "${PORT}:8080" \
    "$IMAGE" >/dev/null

echo "--- waiting for the web UI"
for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

curl -fsS "http://127.0.0.1:${PORT}/healthz"
echo
curl -fsS "http://127.0.0.1:${PORT}/api/state"
echo
curl -fsS "http://127.0.0.1:${PORT}/" | grep -q "Now Playing"

echo "--- checking D-Bus and generated config"
docker exec "$NAME" test -S /run/dbus/session_bus_socket
docker exec "$NAME" test -f /etc/shairport-sync.conf

echo "smoke test passed"
