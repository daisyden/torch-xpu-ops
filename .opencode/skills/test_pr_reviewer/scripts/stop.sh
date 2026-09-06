#!/usr/bin/env bash
# Stop the review server.
#
#   ./stop.sh              # stop the server on :8765
#   PORT=8899 ./stop.sh    # stop the one on :8899
#   ./stop.sh --all        # stop every review server on this machine
set -u

PORT="${PORT:-8765}"
ALL="${1:-}"

if [ "$ALL" = "--all" ]; then
  pids=$(pgrep -f "server\.py --host" || true)
  label="all review servers"
else
  # Match the script name plus the port.  The server is launched as
  # `python3 -W ignore server.py --host ... --port N`, so its command line does
  # not contain the directory name.
  pids=$(pgrep -f "server\.py --host .* --port $PORT" || true)
  # also catch anything else holding the port
  portpid=$( (command -v fuser >/dev/null && fuser -n tcp "$PORT" 2>/dev/null) || true)
  pids=$(printf '%s %s' "$pids" "$portpid" | tr -s ' ' '\n' | sort -u | tr '\n' ' ')
  label="review server on :$PORT"
fi

if [ -z "${pids// /}" ]; then
  echo "no $label running"
  exit 0
fi

echo "stopping $label (pids: $pids)"
kill $pids 2>/dev/null || true
sleep 1
still=$(printf '%s' "$pids" | tr ' ' '\n' | while read -r p; do
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null && echo "$p"
done)
if [ -n "$still" ]; then
  echo "  forcing: $still"
  kill -9 $still 2>/dev/null || true
  sleep 1
fi

if command -v ss >/dev/null && [ "$ALL" != "--all" ] && ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  echo "WARNING: something is still listening on :$PORT" >&2
  ss -ltnp 2>/dev/null | grep ":$PORT " >&2
  exit 1
fi
echo "stopped"
