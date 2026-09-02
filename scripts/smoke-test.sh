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
    docker rm -f "$NAME" "${NAME}-host" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "--- installed versions"
docker run --rm --entrypoint /bin/bash "$IMAGE" -c '
    set -e
    shairport-sync -V
    /usr/local/bin/nqptp -V || true
    /opt/venv/bin/sendspin --version
    spotifyd --version
    gmediarender --version 2>&1 | head -1
    /opt/venv/bin/python -c "import nowplaying, dbus_fast, aiohttp; print(\"nowplaying\", nowplaying.__version__)"
'

# The entrypoint takes a different branch per Avahi mode, and a bug in the one
# not exercised here shipped once already: host mode died with exit 127 while
# container mode was fine. Boot both.
echo "--- booting in host Avahi mode"
docker run -d --name "${NAME}-host" \
    -e ENABLE_AIRPLAY=0 \
    -e ENABLE_SENDSPIN=0 \
    -e AUDIO_SHARING=none \
    -e AVAHI_MODE=host \
    -e ENABLE_WEB=0 \
    "$IMAGE" >/dev/null
sleep 8
state="$(docker inspect -f '{{.State.Status}}' "${NAME}-host")"
if [ "$state" != "running" ]; then
    echo "host-mode container is $state, expected running:" >&2
    docker logs "${NAME}-host" 2>&1 | tail -20 >&2
    exit 1
fi
docker exec "${NAME}-host" supervisorctl status >/dev/null
echo "host mode starts cleanly"

echo "--- booting with audio disabled"
docker run -d --name "$NAME" \
    -e ENABLE_AIRPLAY=0 \
    -e ENABLE_SENDSPIN=0 \
    -e AUDIO_SHARING=none \
    -e AVAHI_MODE=container \
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
docker exec "$NAME" test -S /run/sendspin-shareplay/session_bus_socket
docker exec "$NAME" test -f /etc/shairport-sync.conf

echo "smoke test passed"
