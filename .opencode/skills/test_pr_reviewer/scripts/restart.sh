#!/usr/bin/env bash
# Restart the review server cleanly.
#
# Why this exists: server.py is loaded into memory at startup, so edits to it do
# NOT take effect until the process is replaced (unlike static/*, which is read
# from disk per request).  A stale process silently serves old API responses,
# which is very confusing to debug.  This script verifies the old process is
# really gone and that the new one is answering before returning.
set -u

PORT="${PORT:-8765}"
HOST="${HOST:-0.0.0.0}"
HERE="$(cd "$(dirname "$0")" && pwd)"
export REFACTOR_REVIEW_CLONE="${REFACTOR_REVIEW_CLONE:-$HOME/pytorch}"

echo "stopping any existing server on :$PORT"
# NB: the server is started as `python3 -W ignore server.py --host ...` from its
# own directory, so its command line does NOT contain "refactor-review/".
# Match on the script name plus its flags, and additionally kill whatever holds
# the port, so a differently-invoked instance cannot survive.
for _ in 1 2 3 4 5; do
  pids=$(pgrep -f "server\.py --host .* --port $PORT" || true)
  # anything actually listening on the port (needs no root for own processes)
  portpid=$( (command -v fuser >/dev/null && fuser -n tcp "$PORT" 2>/dev/null) || true)
  all=$(printf '%s %s' "$pids" "$portpid" | tr -s ' ' '\n' | sort -u | tr '\n' ' ')
  [ -z "${all// /}" ] && break
  kill -9 $all 2>/dev/null || true
  sleep 1
done

# verify the port is really free before trying to bind
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -q ":$PORT "; then
    sleep 1
  else
    break
  fi
done
if command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  echo "ERROR: port $PORT is still in use:" >&2
  ss -ltnp 2>/dev/null | grep ":$PORT " >&2
  exit 1
fi

cd "$HERE"
rm -rf __pycache__ dev/__pycache__

# fail fast on a syntax error instead of leaving nothing running
python3 -c "import sys; sys.path.insert(0,'.'); import server, matcher, prdata" || {
  echo "ERROR: python modules do not import; not starting" >&2
  exit 1
}

setsid nohup python3 -W ignore server.py --host "$HOST" --port "$PORT" --no-browser \
  > /tmp/refactor-review.log 2>&1 < /dev/null &

for i in $(seq 1 20); do
  sleep 1
  code=$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null || true)
  [ "$code" = "200" ] && break
done
if [ "${code:-}" != "200" ]; then
  echo "ERROR: server did not come up; see /tmp/refactor-review.log" >&2
  tail -20 /tmp/refactor-review.log >&2
  exit 1
fi

pid=$(pgrep -f "server\.py --host .* --port $PORT" | head -1)
if [ -z "$pid" ]; then
  echo "ERROR: nothing is running for port $PORT" >&2
  tail -20 /tmp/refactor-review.log >&2
  exit 1
fi

# Prove the running process actually has the current code, not a stale build.
# The verdict field is what the pane-1 nav depends on; if it is missing the
# process is old and every "real change" would look like a plain change.
probe=$(curl -s --noproxy '*' -m 300 \
  "http://127.0.0.1:$PORT/api/file?ref=189250&path=test/test_dataloader.py" 2>/dev/null \
  | head -c 400000 | grep -c '"verdict"' || true)
if [ "${probe:-0}" -eq 0 ]; then
  echo "WARNING: the running server returned no line verdicts." >&2
  echo "         It may be stale, or PR 189250 is not reachable right now." >&2
fi

echo "server up: pid $pid  started $(ps -o lstart= -p "$pid" 2>/dev/null | tr -d ' \n')"
echo "  code check: verdict field present ($probe occurrences)"
echo "  local   http://127.0.0.1:$PORT/"
ip=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "$ip" ] && echo "  network http://$ip:$PORT/"
echo "log: /tmp/refactor-review.log"
