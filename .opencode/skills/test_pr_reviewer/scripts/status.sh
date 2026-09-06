#!/usr/bin/env bash
# Report whether the review server is running, reachable and up to date.
#
#   ./status.sh
#   PORT=8899 ./status.sh
set -u

PORT="${PORT:-8765}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "== process =="
pids=$(pgrep -f "server\.py --host .* --port $PORT" || true)
if [ -z "$pids" ]; then
  echo "  no server process for :$PORT"
else
  for p in $pids; do
    echo "  pid $p  started $(ps -o lstart= -p "$p" 2>/dev/null | tr -s ' ')"
  done
fi

echo "== socket =="
if command -v ss >/dev/null; then
  ss -ltnp 2>/dev/null | grep ":$PORT " | sed 's/^/  /' || echo "  nothing listening on :$PORT"
fi

echo "== reachability =="
for target in "127.0.0.1" "$(hostname -I 2>/dev/null | awk '{print $1}')" "$(hostname -f 2>/dev/null)"; do
  [ -z "$target" ] && continue
  code=$(curl -s --noproxy '*' -m 8 -o /dev/null -w '%{http_code}' "http://$target:$PORT/" 2>/dev/null || echo "---")
  printf '  %-34s HTTP %s\n' "$target:$PORT" "$code"
done

echo "== code freshness =="
# The Python modules are loaded at startup, so a running process can be older
# than the files on disk.  Compare, and probe the API for the `verdict` field
# that the pane-1 "real change" navigation depends on.
newest=0
for f in server.py matcher.py prdata.py; do
  [ -f "$HERE/$f" ] || continue
  m=$(stat -c %Y "$HERE/$f")
  [ "$m" -gt "$newest" ] && newest=$m
done
if [ -n "$pids" ]; then
  first=$(echo "$pids" | awk '{print $1}')
  started=$(stat -c %Y "/proc/$first" 2>/dev/null || echo 0)
  if [ "$newest" -gt "$started" ]; then
    echo "  STALE: source is newer than the running process -> run ./restart.sh"
    echo "    newest source: $(date -d @"$newest" '+%F %T')"
    echo "    process start: $(date -d @"$started" '+%F %T')"
  else
    echo "  process is at least as new as the sources"
  fi
fi

health=$(curl -s --noproxy '*' -m 10 "http://127.0.0.1:$PORT/api/health" 2>/dev/null || true)
if [ -n "$health" ]; then
  python3 - "$health" <<'PY' 2>/dev/null || echo "  (health payload unparsable)"
import json, sys
d = json.loads(sys.argv[1])
print(f"  health: ok={d.get('ok')} repo={d.get('default_repo')} "
      f"cached_prs={len(d.get('cached_prs', []))} "
      f"matches={d.get('analysis_cache', {}).get('matches')} "
      f"threads={d.get('threads')}")
PY
else
  echo "  /api/health did not answer"
fi
