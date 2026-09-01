#!/bin/bash
set -euo pipefail
. /usr/local/bin/wait-for.sh
wait_for_dbus_name org.bluez "bluetoothd"

# a2dp-sink means "we are the speaker": phones connect to us and send audio.
exec /usr/bin/bluealsa -p a2dp-sink
