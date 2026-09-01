#!/bin/bash
set -euo pipefail
. /usr/local/bin/wait-for.sh
wait_for_dbus_name org.bluealsa "bluealsa"

# With no addresses listed, this plays whatever any connected device sends.
exec /usr/bin/bluealsa-aplay \
    --pcm="${BLUETOOTH_AUDIO_DEVICE:-default}" \
    --profile-a2dp
