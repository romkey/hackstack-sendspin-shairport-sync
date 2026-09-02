#!/bin/bash
set -euo pipefail
. /usr/local/bin/wait-for.sh
wait_for_file /run/sendspin-shareplay/session_bus_socket "the D-Bus session bus"

# Spotify killed username/password logins, so this relies entirely on Spotify
# Connect discovery: the speaker advertises itself and the phone app claims it.
args=(
    --no-daemon
    --device-name "${SPOTIFY_NAME}"
    --backend alsa
    --device "${SPOTIFY_AUDIO_DEVICE:-default}"
    --cache-path /config/spotifyd
    --use-mpris=true
    --dbus-type session
    --device-type speaker
)
if [ -n "${SPOTIFY_BITRATE:-}" ]; then args+=(--audio-format "${SPOTIFY_BITRATE}"); fi
if [ -n "${SPOTIFY_INITIAL_VOLUME:-}" ]; then args+=(--initial-volume "${SPOTIFY_INITIAL_VOLUME}"); fi
if [ -n "${SPOTIFY_ZEROCONF_PORT:-}" ]; then args+=(--zeroconf-port "${SPOTIFY_ZEROCONF_PORT}"); fi

# shellcheck disable=SC2206  # word splitting is intended for user-supplied args
extra=(${EXTRA_SPOTIFYD_ARGS:-})

# spotifyd's zeroconf library cannot parse some perfectly ordinary mDNS records
# other devices broadcast -- NSEC (type 47) in particular -- and logs a warning
# for each one. On a busy network that is several lines a second, which buries
# everything else and churns through the log rotation. It says nothing about our
# own advertisement, so drop those lines by default. spotifyd fixes its log level
# internally, so RUST_LOG cannot do this for us.
if [ "${SPOTIFY_QUIET_MDNS:-1}" = "1" ]; then
    exec > >(grep --line-buffered -vE "parse packet from .*:5353") 2>&1
fi

exec /usr/local/bin/spotifyd "${args[@]}" "${extra[@]}"
