#!/bin/bash
set -euo pipefail
. /usr/local/bin/wait-for.sh
wait_for_file /run/dbus/system_bus_socket "the D-Bus system bus"
rm -f /run/avahi-daemon/pid
exec /usr/sbin/avahi-daemon --no-chroot -f /etc/avahi/avahi-daemon.conf
