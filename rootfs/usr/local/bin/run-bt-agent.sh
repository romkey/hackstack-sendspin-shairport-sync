#!/bin/bash
set -euo pipefail
. /usr/local/bin/wait-for.sh
wait_for_dbus_name org.bluez "bluetoothd"

args=(--adapter "${BLUETOOTH_ADAPTER:-hci0}" --name "${BLUETOOTH_NAME:-}")
if [ "${BLUETOOTH_DISCOVERABLE:-1}" = "1" ]; then
    args+=(--discoverable)
else
    args+=(--no-discoverable)
fi

exec /opt/venv/bin/python /usr/local/lib/sendspin-shareplay/bt_agent.py "${args[@]}"
