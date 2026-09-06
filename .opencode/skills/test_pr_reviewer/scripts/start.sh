#!/usr/bin/env bash
# Start the PyTorch test-refactor review server.
#
#   ./start.sh                 # bind all interfaces on :8765
#   ./start.sh 189250          # ...and print the URL for that PR
#   PORT=8899 ./start.sh       # different port
#   HOST=127.0.0.1 ./start.sh  # loopback only (use with an SSH tunnel)
#
# Idempotent: if a server is already serving the current code on $PORT it is
# left alone.  Use ./restart.sh after editing server.py / matcher.py / prdata.py.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8765}"
HOST="${HOST:-0.0.0.0}"
PR="${1:-}"

# A local clone makes full file contents come from git instead of the GitHub API.
if [ -z "${REFACTOR_REVIEW_CLONE:-}" ]; then
  for cand in "$HOME/pytorch" "$HOME/src/pytorch" "$HOME/git/pytorch"; do
    if [ -d "$cand/.git" ]; then export REFACTOR_REVIEW_CLONE="$cand"; break; fi
  done
fi

banner() {
  local ip
  ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  local host_fqdn
  host_fqdn=$(hostname -f 2>/dev/null || hostname)
  local q=""
  [ -n "$PR" ] && q="?pr=$PR"
  echo
  echo "review server ready:"
  [ "$HOST" = "0.0.0.0" ] && [ -n "$ip" ] && echo "  http://$ip:$PORT/$q"
  [ "$HOST" = "0.0.0.0" ] && [ -n "$host_fqdn" ] && echo "  http://$host_fqdn:$PORT/$q   (survives a DHCP change)"
  echo "  http://127.0.0.1:$PORT/$q   (this machine / SSH tunnel)"
  echo
  echo "clone : ${REFACTOR_REVIEW_CLONE:-<none: file contents come from the GitHub API>}"
  echo "log   : /tmp/refactor-review.log"
  echo "stop  : $HERE/stop.sh"
}

# already up and healthy?
if curl -s --noproxy '*' -m 5 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  echo "a server is already answering on :$PORT (use ./restart.sh to reload code)"
  banner
  exit 0
fi

exec "$HERE/restart.sh" ${PR:+"$PR"}
