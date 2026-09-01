#!/bin/bash
# wait_for_file <path> <description> [timeout-seconds]
wait_for_file() {
    local path="$1" what="$2" timeout="${3:-60}" waited=0
    while [ ! -e "$path" ]; do
        if [ "$waited" -ge "$timeout" ]; then
            echo "timed out after ${timeout}s waiting for ${what} (${path})" >&2
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
}

# wait_for_dbus_name <bus-name> <description> [timeout-seconds]
# Polls the system bus until some process owns the name.
wait_for_dbus_name() {
    local name="$1" what="$2" timeout="${3:-90}" waited=0
    while ! dbus-send --system --dest=org.freedesktop.DBus --type=method_call --print-reply \
        /org/freedesktop/DBus org.freedesktop.DBus.NameHasOwner "string:${name}" 2>/dev/null \
        | grep -q "boolean true"; do
        if [ "$waited" -ge "$timeout" ]; then
            echo "timed out after ${timeout}s waiting for ${what} (${name})" >&2
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
}
