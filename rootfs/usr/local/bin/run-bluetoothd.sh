#!/bin/bash
set -euo pipefail
. /usr/local/bin/wait-for.sh
wait_for_file "${BT_BUS_SOCKET:-/run/sendspin-shareplay/system_bus_socket}" "the D-Bus system bus"

# The SAP (SIM Access) plugin fails noisily on a machine with no modem and is
# useless for an audio sink.
exec /usr/libexec/bluetooth/bluetoothd \
    --nodetach \
    --noplugin=sap \
    -f /etc/bluetooth/main.conf
