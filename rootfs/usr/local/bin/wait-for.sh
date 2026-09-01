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
