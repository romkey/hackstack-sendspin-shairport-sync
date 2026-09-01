#!/bin/bash
set -euo pipefail

# A stable UUID keeps controllers from treating every restart as a new device.
uuid_file=/config/dlna-uuid
if [ ! -f "$uuid_file" ]; then
    mkdir -p /config
    cat /proc/sys/kernel/random/uuid > "$uuid_file"
fi

args=(
    --port "${DLNA_PORT:-49494}"
    --friendly-name "${DLNA_NAME}"
    --uuid "$(cat "$uuid_file")"
    --gstout-audiosink alsasink
    --gstout-audiodevice "${DLNA_AUDIO_DEVICE:-default}"
    --logfile stdout
)
# Audio only: this is a speaker, and advertising video makes controllers offer
# it as a display target.
if [ "${DLNA_AUDIO_ONLY:-1}" = "1" ]; then args+=(--mime-filter audio); fi
if [ -n "${DLNA_INTERFACE:-}" ]; then args+=(--interface-name "${DLNA_INTERFACE}"); fi

# shellcheck disable=SC2206  # word splitting is intended for user-supplied args
extra=(${EXTRA_GMEDIARENDER_ARGS:-})
exec /usr/bin/gmediarender "${args[@]}" "${extra[@]}"
