#!/usr/bin/env bash
# Run the tool's own test suites.
#
#   ./selftest.sh              # matcher + UI tests (offline where possible)
#   ./selftest.sh --quick      # matcher accuracy on 3 PRs only
#   ./selftest.sh --live       # also test the RUNNING server over HTTP
#
# Requires: python3, gh (authenticated).  The UI tests additionally need node
# and jsdom; they are skipped with a notice if jsdom is unavailable.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
PORT="${PORT:-8765}"
MODE="${1:-}"

if [ -z "${REFACTOR_REVIEW_CLONE:-}" ] && [ -d "$HOME/pytorch/.git" ]; then
  export REFACTOR_REVIEW_CLONE="$HOME/pytorch"
fi
export PYTHONPATH="$HERE:${PYTHONPATH:-}"

fails=0
step() { echo; echo "=== $* ==="; }
note() { echo "  $*"; }

PRS_QUICK="189250 195155 195840"
PRS_FULL="189250 195155 195840 195730 195722 195452 195717 195016 195002 193956"
PRS="$PRS_QUICK"
[ "$MODE" = "--full" ] && PRS="$PRS_FULL"

step "python modules import"
python3 -c "import server, matcher, prdata; print('  ok')" || fails=$((fails+1))

step "matcher: method pairing accuracy (name-agnostic ground truth)"
if python3 -W ignore dev/_validate2.py $PRS 2>&1 | tee /tmp/st_v2.log | grep -E "strict top-1|pairs"; then
  grep -q "100.000%" /tmp/st_v2.log || { echo "  NOT 100% top-1"; fails=$((fails+1)); }
else
  fails=$((fails+1))
fi

step "matcher: whole-file line map consistency"
if python3 -W ignore dev/_validate_linemap.py $PRS 2>&1 | tail -3 | tee /tmp/st_lm.log; then
  grep -q "^OK" /tmp/st_lm.log || fails=$((fails+1))
else
  fails=$((fails+1))
fi

# ---- UI tests ---------------------------------------------------------- #
JSDOM_DIR=""
for d in "$HERE/node_modules" /tmp/opencode/node_modules "$HOME/node_modules"; do
  [ -d "$d/jsdom" ] && JSDOM_DIR="$d" && break
done

step "UI: javascript syntax"
if command -v node >/dev/null; then
  node --check static/app.js && note "app.js ok" || fails=$((fails+1))
else
  note "SKIP: node not installed"
fi

step "UI: DOM tests"
if [ -z "$JSDOM_DIR" ]; then
  note "SKIP: jsdom not found. Install with:  npm install jsdom"
elif ! command -v node >/dev/null; then
  note "SKIP: node not installed"
else
  export NODE_PATH="$JSDOM_DIR"
  node dev/test_overlay.js | tail -1 || fails=$((fails+1))
  FIX=$(mktemp -d)
  note "building fixtures in $FIX"
  python3 -W ignore - "$FIX" <<'PY' || fails=$((fails+1))
import json, sys
sys.path.insert(0, '.')
import server
D = sys.argv[1].rstrip('/') + '/'
PR, P = '189250', 'test/test_dataloader.py'
f = server.api_file({'ref': [PR], 'path': [P]})
tgt = next((str(l.get('target_uid') or '') for l in f['file']['lines']
            if l['kind'] == 'del' and l.get('base_no') == 3308), '')
json.dump(server.api_pr({'ref': [PR]}), open(D + 'pr.json', 'w'))
json.dump(f, open(D + 'file.json', 'w'))
json.dump(server.api_linemap({'ref': [PR], 'path': [P]}), open(D + 'linemap.json', 'w'))
json.dump(server.api_resolve({'ref': [PR], 'path': [P], 'side': ['base'],
                              'line': ['3308'], 'target': [tgt]}),
          open(D + 'resolve.json', 'w'))
print('  fixtures ok')
PY
  for t in test_ui_e2e test_nav test_reverse test_pane_nav; do
    printf '  %-16s ' "$t"
    node "dev/$t.js" "$FIX" 2>&1 | tail -1 || fails=$((fails+1))
  done
  rm -rf "$FIX"
fi

# ---- live server ------------------------------------------------------- #
if [ "$MODE" = "--live" ]; then
  step "live server: API contract on :$PORT"
  python3 dev/test_api_contract.py --port "$PORT" 2>&1 | tail -4 || fails=$((fails+1))
  step "live server: multi-user concurrency on :$PORT"
  python3 dev/test_concurrency.py --port "$PORT" 2>&1 | tail -4 || fails=$((fails+1))
fi

echo
if [ "$fails" -eq 0 ]; then
  echo "SELFTEST PASSED"
else
  echo "SELFTEST: $fails step(s) failed"
fi
exit "$fails"
