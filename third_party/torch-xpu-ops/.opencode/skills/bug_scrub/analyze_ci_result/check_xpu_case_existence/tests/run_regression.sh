#!/usr/bin/env bash
# Regression test for the check_xpu_case_existence skill.
#
# What this exercises:
#   - Real `opencode run` invocation against a single Test Cases row.
#   - The agent must load the check_xpu_case_existence skill, fire its
#     mandatory explore sub-agent, inspect torch-xpu-ops sources, and return
#     a True/False verdict plus an explanation containing diagnostic tokens
#     that prove the deep-analysis path executed.
#
# Pass criteria (per fixture):
#   1. The agent emits a final block that contains the exact line
#        XPU_CASE_EXIST: <True|False>
#      matching fixtures.json -> expected_exist.
#   2. The agent's explanation contains the required `must_contain_token`
#      (case-insensitive) AND at least one of the `must_contain_any` tokens.
#
# Cost: each fixture spawns ~1 explore sub-agent. Plan ~1-3 minutes total.
# Not safe for CI-on-every-commit; intended for manual pre-release runs.
#
# Usage:
#   bash run_regression.sh                # run all fixtures
#   bash run_regression.sh true_dtypes... # run a single named fixture
#
# Env overrides:
#   PYTORCH_SRC         - default $HOME/upstream/pytorch
#   OPENCODE_MODEL      - e.g. github-copilot/claude-opus-4.7
#   OPENCODE_BIN        - default `opencode`
#   SKILL_DIR           - directory that exposes the skill (defaults to the
#                         parent dir of this tests/ folder).

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${SKILL_DIR:-$(cd "$HERE/.." && pwd)}"
FIXTURES_JSON="$HERE/fixtures.json"
OPENCODE_BIN="${OPENCODE_BIN:-opencode}"
PYTORCH_SRC="${PYTORCH_SRC:-$HOME/upstream/pytorch}"
MODEL_FLAG=""
if [[ -n "${OPENCODE_MODEL:-}" ]]; then
  MODEL_FLAG="--model ${OPENCODE_MODEL}"
fi

if ! command -v "$OPENCODE_BIN" >/dev/null 2>&1; then
  echo "FATAL: opencode binary not found ($OPENCODE_BIN)" >&2
  exit 2
fi
if [[ ! -f "$FIXTURES_JSON" ]]; then
  echo "FATAL: fixtures.json not found at $FIXTURES_JSON" >&2
  exit 2
fi

WANT_NAME="${1:-}"
LOG_DIR="$HERE/_runs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"

PASS=0
FAIL=0
FAILED_NAMES=()

# Iterate fixtures via python (fixtures.json is structured).
PY="${BUG_SCRUB_PY:-/home/daisyden/miniforge3/envs/pytorch_opencode_env/bin/python}"
[[ -x "$PY" ]] || PY=python3

# Dump fixture rows as TSV: name<TAB>json_payload
mapfile -t ROWS < <("$PY" - <<PY
import json, sys
d = json.load(open("$FIXTURES_JSON"))
for f in d["fixtures"]:
    print(f["name"] + "\t" + json.dumps(f))
PY
)

for line in "${ROWS[@]}"; do
  name="${line%%	*}"
  payload="${line#*	}"
  if [[ -n "$WANT_NAME" && "$WANT_NAME" != "$name" ]]; then
    continue
  fi

  echo "================================================================"
  echo "FIXTURE: $name"
  echo "================================================================"

  # Build prompt that pins the skill and forces a parseable answer line.
  expected=$("$PY" -c "import json,sys;f=json.loads(sys.argv[1]);print(f['expected_exist'])" "$payload")
  prompt=$("$PY" - <<PY
import json, os, sys
f = json.loads(sys.argv[1])
pytorch_src = os.environ.get("PYTORCH_SRC", "")
print(f"""You are running the bug_scrub check_xpu_case_existence skill at .opencode/skills/bug_scrub/analyze_ci_result/check_xpu_case_existence/SKILL.md. Follow that skill exactly: launch the mandatory explore sub-agent, then verify by reading sources under PYTORCH_SRC={pytorch_src}. Do not use scripts, filename matches, or regex-only checks.

Classify this single Test Cases row:
  issue_id={f['issue_id']}
  test_file={f['test_file']}
  origin_file={f['origin_file']}
  test_class={f['test_class']}
  test_case={f['test_case']}

When you are done, end your reply with exactly these two lines (no markdown, no code fence), each on its own line:

XPU_CASE_EXIST: <True|False>
EXPLANATION: <one-line explanation citing concrete source paths, decorators, or skip lists>

Do not print anything after EXPLANATION.""")
PY
"$payload")

  raw_out="$LOG_DIR/${name}.out"
  raw_err="$LOG_DIR/${name}.err"

  PYTORCH_SRC="$PYTORCH_SRC" \
  "$OPENCODE_BIN" run $MODEL_FLAG --dir "$SKILL_DIR" \
    --dangerously-skip-permissions \
    "$prompt" \
    >"$raw_out" 2>"$raw_err"
  rc=$?

  echo "  exit_code=$rc"
  echo "  stdout: $raw_out"

  # Extract verdict line.
  verdict_line=$(grep -E "^XPU_CASE_EXIST:" "$raw_out" | tail -n1 || true)
  explain_line=$(grep -E "^EXPLANATION:" "$raw_out" | tail -n1 || true)

  if [[ -z "$verdict_line" ]]; then
    echo "  FAIL: no XPU_CASE_EXIST line in output"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name:no-verdict")
    continue
  fi

  got=$(echo "$verdict_line" | sed -E 's/^XPU_CASE_EXIST:[[:space:]]*//; s/[[:space:]]+$//')
  # Normalize expected/got to "True"/"False"
  case "$got" in true|True|TRUE) got_norm=True ;; false|False|FALSE) got_norm=False ;; *) got_norm="$got" ;; esac
  case "$expected" in True) exp_norm=True ;; False) exp_norm=False ;; *) exp_norm="$expected" ;; esac

  ok=1
  if [[ "$got_norm" != "$exp_norm" ]]; then
    echo "  FAIL: verdict mismatch (expected=$exp_norm got=$got_norm)"
    ok=0
  fi

  # Token checks against full output (verdict + explanation + agent narration).
  must_token=$("$PY" -c "import json,sys;f=json.loads(sys.argv[1]);print(f['must_contain_token'])" "$payload")
  if ! grep -qi -- "$must_token" "$raw_out"; then
    echo "  FAIL: required token '$must_token' not found in agent output"
    ok=0
  fi

  any_ok=0
  while IFS= read -r tok; do
    if grep -qi -- "$tok" "$raw_out"; then any_ok=1; break; fi
  done < <("$PY" -c "import json,sys;f=json.loads(sys.argv[1])
for t in f['must_contain_any']: print(t)" "$payload")
  if (( any_ok == 0 )); then
    echo "  FAIL: none of must_contain_any tokens found in agent output"
    ok=0
  fi

  if (( ok == 1 )); then
    echo "  PASS: verdict=$got_norm explanation tokens OK"
    PASS=$((PASS+1))
  else
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
done

echo
echo "================================================================"
echo "SUMMARY: PASS=$PASS FAIL=$FAIL"
if (( FAIL > 0 )); then
  echo "Failed: ${FAILED_NAMES[*]}"
  echo "Logs:   $LOG_DIR"
  exit 1
fi
echo "Logs:   $LOG_DIR"
exit 0
