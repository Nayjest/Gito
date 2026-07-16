#!/usr/bin/env bash
# Start Code Doctor for LOCAL testing — no access token, bound to localhost.
#
# The API is open (no CODE_DOCTOR_TOKEN), which is fine because it binds to
# 127.0.0.1 and is unreachable from other machines. Do NOT use this to expose
# the app on a network; for that, set CODE_DOCTOR_TOKEN and bind beyond
# loopback deliberately.
#
#   scripts/run-local.sh [port]
set -euo pipefail

PORT="${1:-8787}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="$HERE/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

# Free the port if a previous instance is still holding it, so restarts never
# fail with "Address already in use".
if LEFTOVER="$(lsof -tnP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null)"; then
  if [ -n "$LEFTOVER" ]; then
    echo "Stopping previous server on port $PORT (pid $LEFTOVER)…"
    kill $LEFTOVER 2>/dev/null || true
    sleep 1
  fi
fi

# Ensure no stray token from the shell forces an auth prompt.
unset CODE_DOCTOR_TOKEN

echo "Starting Code Doctor (open, no token) on http://127.0.0.1:$PORT"
exec "$PY" -m code_doctor_app --host 127.0.0.1 --port "$PORT"
