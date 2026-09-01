#!/bin/bash
set -euo pipefail
if [ "${ENABLE_WEB:-1}" = "1" ]; then
    exec /opt/venv/bin/python - "$@" <<'PY'
import sys, urllib.request, os
url = f"http://127.0.0.1:{os.environ.get('WEB_PORT', '8080')}/healthz"
try:
    with urllib.request.urlopen(url, timeout=3) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception as exc:  # noqa: BLE001
    print(exc, file=sys.stderr)
    sys.exit(1)
PY
fi
# Without the web UI there is nothing to poll; fall back to "supervisord is alive".
pgrep -x supervisord >/dev/null
