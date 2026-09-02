#!/bin/bash
set -euo pipefail
. /usr/local/bin/wait-for.sh
wait_for_file /run/sendspin-shareplay/session_bus_socket "the D-Bus session bus"
wait_for_file /run/avahi-daemon/socket "avahi-daemon"

args=(-c /etc/shairport-sync.conf)
case "${AIRPLAY_MODE:-airplay2}" in
    airplay2) args+=(--service-type=airplay2) ;;
    classic|airplay1) args+=(--service-type=classic) ;;
    auto) args+=(--service-type=auto) ;;
    *) echo "AIRPLAY_MODE must be airplay2, classic or auto (got '${AIRPLAY_MODE}')" >&2; exit 1 ;;
esac

# shellcheck disable=SC2206  # word splitting is intended for user-supplied args
extra=(${EXTRA_SHAIRPORT_ARGS:-})
exec /usr/local/bin/shairport-sync "${args[@]}" "${extra[@]}"
