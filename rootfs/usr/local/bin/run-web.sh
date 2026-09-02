#!/bin/bash
set -euo pipefail
. /usr/local/bin/wait-for.sh
wait_for_file /run/sendspin-shareplay/session_bus_socket "the D-Bus session bus"
exec /opt/venv/bin/python -m nowplaying --host 0.0.0.0 --port "${WEB_PORT:-80}"
