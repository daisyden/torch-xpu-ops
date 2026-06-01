#!/usr/bin/env bash
# Shared regression runner for the issue-basic-info-extraction skill.
# Driven by a fixture.json. Used by:
#   tests/test_cases_extraction/run_regression.sh
#   tests/others_repro_extraction/run_regression.sh
#
# What this exercises:
#   - Real `opencode run` against a single issue body, using the exact
#     LLM extraction prompt the skill expects (Schema + MANDATORY rules
#     from prepare_data/issue-basic-info-extraction/SKILL.md).
#   - Output is parsed as a single JSON object that matches the schema.
#   - Assertions check kind, test_cases shape (or emptiness), and that
#     the reproducer is preserved verbatim with the required tokens.
#
# Cost: ~1 LLM call per fixture. ~30-60s.
#
# Required env:
#   FIXTURE_JSON  - absolute path to the fixture.json to run.
# Optional env:
#   OPENCODE_MODEL, OPENCODE_BIN, SKILL_DIR

set -u

FIXTURE_JSON="${FIXTURE_JSON:-}"
if [[ -z "$FIXTURE_JSON" || ! -f "$FIXTURE_JSON" ]]; then
  echo "FATAL: FIXTURE_JSON env var must point to an existing fixture.json" >&2
  exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${SKILL_DIR:-$(cd "$HERE/.." && pwd)}"
OPENCODE_BIN="${OPENCODE_BIN:-opencode}"
MODEL_FLAG=""
if [[ -n "${OPENCODE_MODEL:-}" ]]; then
  MODEL_FLAG="--model ${OPENCODE_MODEL}"
fi
PY="${BUG_SCRUB_PY:-/home/daisyden/miniforge3/envs/pytorch_opencode_env/bin/python}"
[[ -x "$PY" ]] || PY=python3

if ! command -v "$OPENCODE_BIN" >/dev/null 2>&1; then
  echo "FATAL: opencode binary not found ($OPENCODE_BIN)" >&2
  exit 2
fi

LOG_DIR="$(dirname "$FIXTURE_JSON")/_runs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"

# Build the extraction prompt: same Schema + MANDATORY rules as SKILL.md.
prompt=$("$PY" - <<'PY'
import json, os, sys, textwrap
fx = json.load(open(os.environ["FIXTURE_JSON"]))
schema = (
'{\n'
'  "issue_id":      <int>,\n'
'  "body_hash":     "<16-char prefix of sha256(body)>",\n'
'  "kind":          "unittest" | "e2e" | "other",\n'
'  "test_cases": [\n'
'    {"test_file":   "<path as referenced, e.g. test/dynamo/test_x.py>",\n'
'     "test_class":  "<class name OR benchmark suite for e2e>",\n'
'     "test_method": "<method name OR model name for e2e>"}\n'
'  ],\n'
'  "reproducer":    "<verbatim - see rules below>",\n'
'  "error_message": "<first user-visible error sentence>",\n'
'  "traceback":     "<full traceback if present>",\n'
'  "notes":         "<1-sentence semantic summary>"\n'
'}\n')
rules = (
'1. `reproducer` is verbatim. Every URL is mandatory - never paraphrased, never dropped.\n'
'2. `test_cases` is empty unless the issue actually points at runnable tests.\n'
'3. For unittest issues: test_file is the path as the issue quotes it; test_class is the class; test_method is the method.\n'
'4. For e2e issues: test_class = benchmark suite; test_method = model name.\n'
'5. `kind` reflects what the issue is about, not labels alone: unittest | e2e | other.\n'
'6. No fabrication. If the issue contains no reproducer, leave the field empty.\n'
'7. ASCII output only.\n')
sys.stdout.write(
    "You are running the bug_scrub issue-basic-info-extraction skill at "
    ".opencode/skills/bug_scrub/prepare_data/issue-basic-info-extraction/SKILL.md.\n"
    "Apply the LLM Extraction step (schema + mandatory rules) to ONE issue.\n\n"
    "Schema (emit exactly this shape, ASCII-only):\n" + schema + "\n"
    "Mandatory rules:\n" + rules + "\n"
    "Issue input:\n"
    + json.dumps({
        "issue_id": fx["issue_id"],
        "title":    fx["title"],
        "labels":   fx.get("labels", []),
        "body":     fx["body"],
      }, ensure_ascii=True, indent=2)
    + "\n\nEmit exactly one JSON object, no markdown, no fence, no prose before or after. "
      "Begin your reply with '{' and end with '}'.\n"
)
PY
)

raw_out="$LOG_DIR/run.out"
raw_err="$LOG_DIR/run.err"

FIXTURE_JSON="$FIXTURE_JSON" \
"$OPENCODE_BIN" run $MODEL_FLAG --dir "$SKILL_DIR" \
  --dangerously-skip-permissions \
  "$prompt" \
  >"$raw_out" 2>"$raw_err"
rc=$?

echo "exit_code=$rc"
echo "stdout:   $raw_out"

# Validate output is JSON matching expectations.
export RAW_OUT="$raw_out"
export FIXTURE_JSON

result_json=$("$PY" - <<'PY'
import json, os, re, sys
out = open(os.environ["RAW_OUT"]).read()
m = re.search(r"\{[\s\S]*\}", out)
if not m:
    print("NOJSON", file=sys.stderr); sys.exit(3)
try:
    obj = json.loads(m.group(0))
except Exception as e:
    print("BADJSON:", e, file=sys.stderr); sys.exit(3)
fx = json.load(open(os.environ["FIXTURE_JSON"]))
exp = fx["expected"]
errs = []
if obj.get("kind") != exp["kind"]:
    errs.append(f"kind mismatch: got={obj.get('kind')} expected={exp['kind']}")
if exp.get("test_cases_empty"):
    if obj.get("test_cases"):
        errs.append("test_cases must be empty but got: " + json.dumps(obj.get("test_cases"))[:200])
else:
    tcs = obj.get("test_cases") or []
    if len(tcs) < 1:
        errs.append("expected at least 1 test_case, got 0")
    else:
        expected_tcs = exp.get("test_cases", [])
        for i, etc in enumerate(expected_tcs):
            if i >= len(tcs):
                errs.append(f"missing test_cases[{i}]")
                continue
            got = tcs[i]
            sub = etc.get("test_file_contains")
            if sub and sub.lower() not in (got.get("test_file") or "").lower():
                errs.append(f"test_cases[{i}].test_file should contain {sub}, got {got.get('test_file')}")
            for k in ("test_class", "test_method"):
                if k in etc and etc[k] != got.get(k):
                    errs.append(f"test_cases[{i}].{k} mismatch: got={got.get(k)} expected={etc[k]}")
repro = obj.get("reproducer") or ""
for tok in exp.get("reproducer_must_contain", []):
    if tok not in repro:
        errs.append(f"reproducer missing required token: {tok!r}")
if errs:
    for e in errs: print("ERR:", e)
    sys.exit(1)
print("OK")
PY
)
asserts_rc=$?

if (( asserts_rc == 0 )); then
  echo "PASS"
  exit 0
fi
echo "FAIL (assertion rc=$asserts_rc)"
echo "See $raw_out"
exit 1
