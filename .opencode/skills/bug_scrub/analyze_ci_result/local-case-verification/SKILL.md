# Phase 2.5 — Local Case Verification

> **Base constraints**: This subskill is governed by [`../../base-constraints.md`](../../base-constraints.md) (C1 logs->agent_space, C2 ask-on-blocker, C3 background status, C4 no over-skip). They apply even when not restated here.

> **Path convention**: `${PYTORCH_REPO_ROOT}` (default `~/upstream/pytorch`) — see [`../../SKILL.md`](../../SKILL.md) for the full convention.

## Purpose

For every issue whose tracked test cases (Test Cases sheet, E2E Test Cases sheet)
or reproducer (Others sheet) were **not exercised by the CI artifacts** collected
in Phase 1.2, attempt the reproduction in a fresh local conda environment and
record the verdict in a single new **`Local status`** column on the `Issues`
sheet.

This skill is the *one* place in the bug-scrub pipeline that runs user / model
code locally — every other phase is read-only against CI artifacts or static
sources. It exists to plug the gap between "issue exists" and "issue was tested
this cycle".

## When to run

**Default scope (v1.1+): Others sheet only.** UT and E2E lanes are opt-in;
Phase 2.1/2.2 already exercise those test sets against CI artifacts, and
Phase 2.5 by default fills only the Others gap (issues that have no
enumerable test and therefore no CI coverage at all).

| Trigger | Source columns | Action | Default? |
|---|---|---|---|
| Non-performance issue is in **Others** sheet (no enumerable test) | Current GitHub issue body from `data/torch_xpu_ops_issues.json`; Others `reproduce step` may be blank | Deep-extract/materialize runnable reproducer, then run it (see Workflow §5) | **YES** |
| Performance issue is in **Others** sheet | Title/body/labels mention performance/latency/throughput | Skip local verification; write `skipped` with reason `performance issue` | **YES** |
| Issue is in **Test Cases** sheet, ALL its rows have `XPU Status` blank/`not found`/`not_run` (Phase 2.1 verdict) | `Test Cases.XPU Status` | Run UT path (see Workflow §3) | opt-in via `--lanes ut,...` or `--all-lanes` |
| Issue is in **E2E Test Cases** sheet, ALL its rows have `XPU Status` blank/`not found`/`not_run` (Phase 2.2 verdict) | `E2E Test Cases.XPU Status` | Run E2E path (see Workflow §4) | opt-in via `--lanes e2e,...` or `--all-lanes` |

For UT/E2E (when opted in): if at least one row of the issue's test set was
exercised in CI (regardless of PASS/FAIL), this skill **skips** the issue:
the `Local Status` column is left blank and the CI verdict on
`Test Cases.XPU Status` / `E2E Test Cases.XPU Status` remains authoritative.

## Invocation

The executable counterpart of this skill lives next to this `SKILL.md`:

```
analyze_ci_result/local-case-verification/
├── SKILL.md                       ← this spec
├── __init__.py
└── run_local_verification.py      ← runner (matches §Workflow step-for-step)
```

```bash
# Phase 1.0 (env setup) must already have run in this session.
# See prepare_data/test-environment-setup/SKILL.md.

cd .../bug_scrub/analyze_ci_result/local-case-verification/

# Default run: Others lane only (no env update — Phase 1.0 already did it)
python run_local_verification.py

# Broaden scope explicitly
python run_local_verification.py --all-lanes              # ut + e2e + others
python run_local_verification.py --lanes ut,others         # ut + others
python run_local_verification.py --lanes ut                # ut only

# Legacy single-lane flag (still supported)
python run_local_verification.py --only-lane others
python run_local_verification.py --only-lane ut

# Restrict to specific issues
python run_local_verification.py --issues 1678,3549

# Plan only — never executes pytest, never modifies xlsx
python run_local_verification.py --dry-run
```

Default paths (overridable via flags):

| Flag | Default | Meaning |
|---|---|---|
| `--xlsx` | `${REPO}/result/torch_xpu_ops_issues.xlsx` | Workbook to update |
| `--log-dir` | `${REPO}/local_logs/` | Per-issue log + `phase25.log` + `run_summary.json` |
| `--pytorch-repo` | `${PYTORCH_REPO_ROOT:-~/upstream/pytorch}` | Test execution `cwd` |
| `--ut-timeout` | `600` | Per-pytest timeout (seconds) |
| `--repro-timeout` | `1800` | Per-reproducer timeout (seconds) |

The runner derives `${REPO}` from `__file__` (`SCRIPT_DIR.parents[4]`), so
the script must remain in this directory for the defaults to resolve.

## Trust rule (downstream consumers)

Phase 3.3 (`triage_skills`) and Phase 4a (`close_or_skip`) **may treat
`Local status` as authoritative evidence iff**:

- Issue body / labels indicate the platform is **PVC (Ponte Vecchio)**, AND
- Issue body / labels indicate the OS is **Linux**.

Otherwise `Local status` is informational only: downstream phases must still
look up CI status (which will be blank for these rows) and treat the issue as
"unverified this cycle". This is because the local environment used by this
skill is a single PVC-Linux box; verdicts on other platforms (e.g., Arc,
Windows) cannot be inferred from it.

The PVC + Linux check is performed by Phase 3.3 / 4a at consumption time, not
by this skill. This skill always writes its honest local result.

## Preconditions

Phase 1.0 (test environment setup, owned by
`prepare_data/test-environment-setup/SKILL.md`) must have run in this
session. That phase activates the conda env, installs the nightly XPU
torch + triton wheels, and syncs `${PYTORCH_REPO_ROOT}` to the installed
torch's git commit. This skill assumes that state and verifies it.

### Required tools

- `bash` — pytest, reproducer execution
- `python` — run reproducer scripts

## Workflow

### Step 1 — Verify Phase 1.0 prerequisites

Phase 2.5 assumes Phase 1.0 (test environment setup) has already run in
this session. The conda env is active, the XPU nightly `torch` +
`pytorch-triton-xpu` wheels are installed, and `${PYTORCH_REPO_ROOT}` is
synced to the installed torch's git commit. See
[`prepare_data/test-environment-setup/SKILL.md`](../../prepare_data/test-environment-setup/SKILL.md)
§"Phase 1.0 — Test Environment Setup" for the full sequence.

This skill **does not perform** env updates or commit syncs itself. It first
verifies that the installed torch package exposes an available XPU backend:

```bash
python -c "import torch; assert hasattr(torch, 'xpu'); assert torch.xpu.is_available()"
```

If this check fails, stop before building the workbook worklist or running any
local pytest/reproducer command. Record the skip reason in `local_logs/phase25.log`
and `local_logs/run_summary.json`; do not modify the workbook.

### Step 2 — Build the work list

Read `result/torch_xpu_ops_issues.xlsx`. For every row of the `Issues` sheet,
determine which lane applies:

```python
def lane_for(issue_id, sheets):
    ut_rows  = [r for r in sheets["Test Cases"]    if r.issue_id == issue_id]
    e2e_rows = [r for r in sheets["E2E Test Cases"]if r.issue_id == issue_id]
    oth_rows = [r for r in sheets["Others"]        if r.issue_id == issue_id]

    if oth_rows:
        return "OTHERS"
    if ut_rows and all(_is_blank(r.xpu_status) for r in ut_rows):
        return "UT"
    if e2e_rows and all(_is_blank(r.xpu_status) for r in e2e_rows):
        return "E2E"
    return None  # skip — CI already covered it

def _is_blank(s):
    return s is None or str(s).strip().lower() in ("", "not found", "not_run", "n/a")
```

Each issue maps to **exactly one** lane (Issues sheet placement is already
exclusive by Phase 1.6 post-pass).

### Step 3 — UT lane (`Test Cases` sheet)

For each row of the issue in `Test Cases`:

```bash
cd "${PYTORCH_REPO_ROOT}"
PYTORCH_TEST_WITH_SLOW=1 \
  python -m pytest \
    "<test_file>" \
    -k "<test_case>" \
    -v --tb=short --timeout=600 \
    2>&1 | tee /tmp/local_ut_${issue_id}_${row_idx}.log
```

- `<test_file>` and `<test_case>` come straight from the `Test File` / `Test Case`
  columns written by Phase 1.1. They were already filesystem-verified during
  Phase 1.1, so no path-resolution work is needed here.
- `PYTORCH_TEST_WITH_SLOW=1` is mandatory: bug-scrub coverage includes
  `@slowTest`-decorated cases.
- Per-row verdicts: `pass` / `fail` / `error` / `skipped` / `notfound`.
- Aggregate to a single issue-level verdict (see Step 6).

### Step 4 — E2E lane (`E2E Test Cases` sheet)

E2E issues do not have a portable pytest invocation; they need the **reproducer
block** the LLM extracted into the `Others`-style reproducer fields during
Phase 1.1 (the same extraction also fires for E2E issues; the text is stored
on the `E2E Test Cases` row's `reproduce step` column when present, else
re-extracted from the issue body).

```bash
# Materialize the reproducer to a script
echo "$REPRODUCER_BLOCK" > /tmp/repro_${issue_id}.sh
chmod +x /tmp/repro_${issue_id}.sh

# Run it with a hard timeout
cd "${PYTORCH_REPO_ROOT}"
timeout 1800 bash /tmp/repro_${issue_id}.sh \
    2>&1 | tee /tmp/local_e2e_${issue_id}.log
echo "EXIT=$?"
```

Verdict mapping:

- Exit 0 + no `RuntimeError` / `AssertionError` / `Traceback` in log → `pass`
- Exit 124 (timeout) → `timeout`
- Non-zero exit OR any of the above tokens in log → `fail`
- Reproducer block absent or empty → `noreproducer`

### Step 5 — Others lane (`Others` sheet)

The runner does **not** parse issue bodies. For each Others-sheet issue it
reads a pre-materialized reproducer from disk:

```
${LOG_DIR}/reproducers/<issue_id>.sh
```

(`${LOG_DIR}` defaults to `${REPO}/local_logs/`.) If the file exists and is
non-empty, the runner executes it as bash via the same path as Step 4
(timeout `--repro-timeout`, log to `${LOG_DIR}/<issue_id>.log`, classifier
in §Verdict mapping). If the file is missing or empty, the issue's verdict
is `noreproducer` and nothing is executed.

Performance/latency/throughput issues are filtered out *before* this step
by `is_performance_issue` (title/body/labels scan); they receive
`skipped` with reason `performance issue` and no reproducer file is
consulted.

#### Deep-extract contract (upstream of this skill)

Producing `reproducers/<issue_id>.sh` is the responsibility of an upstream
deep-extraction step (model-driven). That step reads the current issue
body from `data/torch_xpu_ops_issues.json`, identifies the runnable
command(s) and any adjacent script blocks, materializes referenced
scripts via `cat > foo.py <<'PYREPRO' ... PYREPRO` heredocs, fetches any
remote URLs the reproducer depends on (e.g. gists), and writes a single
self-contained bash script. The runner is intentionally dumb about all
of this — it only `bash`-executes what it finds on disk.

Files in `reproducers/` are a **cache**: they survive across runs, can
be hand-edited to fix a misbehaving repro, and can be regenerated by
re-running the deep-extract step. The directory lives under `local_logs/`
and is gitignored.

#### Path-rewrite helper (still in the runner)

When a reproducer references `test/xpu/X.py`, `tests/xpu/X.py`, or
`benchmarks/xpu/X.py` and that path does not exist under
`${PYTORCH_REPO_ROOT}/test`, the runner's `_resolve_test_file` rewrites
the command to the absolute path under
`${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/test/xpu/`. This stays
in the runner because it depends on the live filesystem, not on the
issue body.

#### Classifier rule (still in the runner)

`can't open file '*.py': [Errno 2] No such file or directory` and
`Can't list '*.py'` in the output map to `noreproducer`, not `fail`.
This prevents repros referencing scripts living outside the PyTorch
tree (e.g., Intel-internal benchmark harnesses) from being reported
as real XPU bugs.

### Step 6 — Aggregate per-issue verdict

Multi-row issues collapse to a single `Local status` value on the Issues sheet:

```python
def aggregate(row_verdicts: list[str]) -> str:
    if not row_verdicts:
        return ""                       # blank — nothing ran
    if any(v == "fail"    for v in row_verdicts): return "fail"
    if any(v == "timeout" for v in row_verdicts): return "timeout"
    if any(v == "error"   for v in row_verdicts): return "error"
    if all(v == "pass"    for v in row_verdicts): return "pass"
    if all(v == "skipped" for v in row_verdicts): return "skipped"
    if all(v == "notfound"for v in row_verdicts): return "notfound"
    return "mixed"
```

Verdict vocabulary written to the `Issues.Local status` column (one of):

| Value | Meaning |
|---|---|
| `pass` | All row-level local runs succeeded |
| `fail` | At least one row failed with a real test assertion / runtime error |
| `error` | Collection error or import error (test couldn't start) |
| `timeout` | At least one row exceeded its timeout |
| `skipped` | All rows were skipped (e.g., XPU not available, OS gate) |
| `notfound` | Test file or test case was not collectable |
| `noreproducer` | Others/E2E reproducer block was missing or empty |
| `mixed` | Multiple row outcomes with no dominant failure |
| `` (blank) | Issue had CI coverage — local run not attempted |
| `env_unsupported` | Local environment is not PVC + Linux — skip writing trust signal but still record the value (downstream phases will mark it informational) |

### Step 7 — Persist

Write `Local status` to the Issues sheet **only**. Do not touch the per-row
`XPU Status` / `Stock Status` columns on the Test Cases / E2E Test Cases
sheets — those remain CI-authoritative.

Also write a per-issue log path to `Issues.Local status comments` (col adjacent
to Local status) of the form `local_logs/<issue_id>.log` so that Phase 3.3 root
cause analysis can cite the exact failure output. Logs themselves live in
`local_logs/` at the repo root, gitignored.

## Outputs

| Column | Sheet | Values |
|---|---|---|
| `Local status` | Issues | One of the verdict tokens above, or blank when CI covered the issue |
| `Local status comments` | Issues | `local_logs/<id>.log` (relative path) or short note (e.g., `noreproducer: empty block`) |

## Invariants

- **Phase 1.0 must have run in this session.** Conda env activation, nightly
  XPU torch + triton install, and source-repo commit sync are all owned by
  Phase 1.0 (in `prepare_data/test-environment-setup/SKILL.md`). This
  skill verifies the env is usable but never updates it.
- Runs **after** Phase 2.1–2.4 — depends on `Test Cases.XPU Status` /
  `E2E Test Cases.XPU Status` already being populated.
- Runs **before** Phase 3.1, so triage skills can read `Local status` as
  evidence.
- Never modifies per-row CI status columns. CI verdicts are immutable here.
- Issue-level verdict is the strictest of its row-level verdicts (fail wins
  over pass) — this prevents accidental "PASS" hiding a single failing test.

## Re-run cadence

Re-run **before every weekly bug scrub**, after Phase 1.0 has refreshed
the nightly torch + triton wheels for that session. Verdicts older than
~7 days should be treated as stale by Phase 3.3.

## Cross-references

- Phase 1.1 (`prepare_data/issue-basic-info-extraction`) — produced the test
  paths and reproducer blocks consumed here.
- Phase 2.1 / 2.2 (`match-ut-ci-matching`, `match-e2e-ci-matching`) — produced
  the CI `XPU Status` that gates whether this skill activates.
- Phase 2.4 (`check_xpu_case_existence`) — orthogonal: verifies the test
  *exists*; this skill verifies the test *runs and what it returns*.
- Phase 3.3 (`analyze_issue/triage_skills`) — consumes `Local status` for
  PVC-Linux issues to inform root cause / fix approach.
- Phase 4a (`collect_AR/close_or_skip`) — consumes `Local status == pass`
  (PVC-Linux only) as evidence to recommend close.

## Version

v1.7 - June 15, 2026 - Phase 1.0 (test environment setup) moved out of
`prepare_data/issue-basic-info-extraction/SKILL.md` into a new standalone
skill at `prepare_data/test-environment-setup/SKILL.md`. All four in-file
pointers and the two in `run_local_verification.py` were repointed. No
behavior change to Phase 2.5; it still only verifies the env and never
updates it.

v1.6 - May 22, 2026 - Others-lane reproducer extraction moved out of the
runner. The runner no longer parses issue bodies; it reads
`${LOG_DIR}/reproducers/<issue_id>.sh` written by an upstream model-driven
deep-extract step. `extract_reproducer_from_issue()` and its regression
test (`test_fix1_unlabelled_python_block_links_to_python_command`) are
removed. The path-rewrite helper (`test/xpu/*.py` -> `third_party/torch-xpu-ops/`)
and the "no such file" classifier rule stay in the runner; they depend on
the live filesystem / live process output, not on issue body parsing.

v1.5 - May 22, 2026 - Added five Others-lane extractor/classifier rules
documented in §5a: (1) link unlabelled python fenced blocks to `python foo.py`
commands without requiring a "save this as" prefix; (2) detect heredoc
materialization (`cat > X <<'PYREPRO'`) as `bash`; (3) recognise absolute
`/path/to/python` interpreter paths as `bash`; (4) rewrite `test/xpu/*.py`
paths to their `third_party/torch-xpu-ops/test/xpu/` location when missing
from `${PYTORCH_REPO_ROOT}/test/`; (5) classify "can't open file" /
"Can't list" output as `noreproducer` instead of `fail`. Backed by the
new `tests/extractor_classifier/` regression test.

v1.4 - May 22, 2026 - Others local verification now ignores Phase 1 `reproduce step`, deep-extracts runnable reproducers from current issue bodies, materializes adjacent script blocks, and skips performance Others issues instead of benchmarking locally.
v1.3 - May 22, 2026 - Added an XPU torch availability preflight gate: if `torch.xpu` is missing or `torch.xpu.is_available()` is false, Phase 2.5 logs a skipped environment summary and exits before building the worklist or running local tests.
v1.2 — 2026-05-21 — **Env setup moved to Phase 1.0** (in
`prepare_data/issue-basic-info-extraction/SKILL.md`). Phase 2.5 no longer
performs `git pull`, nightly install, or commit sync; it now only verifies
the env is usable and runs tests. The runner's `--skip-env-update` and
`--skip-commit-sync` flags are removed (no-op if passed).

v1.1 — 2026-05-21 — Default scope narrowed to Others lane only. UT/E2E
lanes are now opt-in via the new `--lanes` flag (e.g. `--lanes ut,others`)
or `--all-lanes`. The legacy `--only-lane {ut,e2e,others}` flag remains
supported for back-compat.

v1.0 — 2026-05-17 — Initial Phase 2.5 spec covering all three lanes
(UT / E2E / Others) by default.
