#!/bin/bash
set -euo pipefail
. /usr/local/bin/wait-for.sh
wait_for_file /run/dbus/session_bus_socket "the D-Bus session bus"

args=(daemon
    --name "${SENDSPIN_NAME}"
    --settings-dir /config/sendspin
    --audio-device "${SENDSPIN_AUDIO_DEVICE}"
    --hardware-volume "${SENDSPIN_HARDWARE_VOLUME:-false}"
    --log-level "$(printf '%s' "${LOG_LEVEL:-info}" | tr '[:lower:]' '[:upper:]')"
)
if [ -n "${SENDSPIN_URL:-}" ]; then args+=(--url "${SENDSPIN_URL}"); fi
if [ -n "${SENDSPIN_INTERFACE:-}" ]; then args+=(--interface "${SENDSPIN_INTERFACE}"); fi
if [ -n "${SENDSPIN_AUDIO_FORMAT:-}" ]; then args+=(--audio-format "${SENDSPIN_AUDIO_FORMAT}"); fi

# shellcheck disable=SC2206  # word splitting is intended for user-supplied args
extra=(${EXTRA_SENDSPIN_ARGS:-})
exec /opt/venv/bin/sendspin "${args[@]}" "${extra[@]}"
