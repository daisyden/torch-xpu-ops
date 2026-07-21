---
name: remove-xpu-skips
description: Probe and remove stale XPU skip decorators and guards from PyTorch test files, one skip at a time, verifying each removal via pytest on an XPU host. Handles @skipIfXpu (with issue in msg), @skipXPU/@skipXPUIf (with issue in comment), inline self.device_type guards, unittest.skipIf(not TEST_CUDA) gates, a bare skipXPU entry in an OpInfo's decorators=[] (op_db-level, verified across every affected test file), and a DecorateInfo(unittest.skip/expectedFailure, 'Class', 'test_name', device_type='xpu', ...) entry in an OpInfo's skips=(...) (single-test-method, dtype/active_if-scoped). Only removes when the referenced issue is CLOSED (via gh issue view); op_db-level skips with no issue may be probed but only pass if every affected file/dtype combination passes. Runs the test after each removal — keeps if passed, reverts if failed. On a non-editable torch install (P7 only), falls back to an in-memory op_db override plugin. Never removes without testing first, never removes open-issue skips.
---

# Remove XPU Skips

Probe and remove stale XPU skip decorators/guards, one skip at a time. Only
removes a skip when its issue is CLOSED (if issue-gated) and the test passes
on XPU without it.

## When to Use

- A test class has XPU enabled but individual methods are still skipped
  (`@skipIfXpu`, `@skipXPU`, `@skipXPUIf`, inline device guards).
- An `OpInfo` in `common_methods_invocations.py` has a bare `skipXPU` in
  `decorators=[...]` (blanket op-wide skip, P7) or a
  `DecorateInfo(unittest.skip/expectedFailure, 'Class', 'test_name',
  device_type='xpu', ...)` in `skips=(...)` (single-method skip, P8).
- Cleaning up stale skips whose tracking issues are resolved, or following
  up after `develop-xpu-test`/`enable-xpu-test` enabled a class.

Not for unconditional batch removal — each skip is probed and verified
individually. Open-issue skips are always left untouched.

## Skip Patterns

| # | Pattern | Source | Issue location | Gate |
|---|---------|--------|-----------------|------|
| P1 | `@skipIfXpu` | `common_utils` | `msg=` kwarg / trailing `#` comment | issue-gated |
| P2 | `@skipIfXpu(msg="...")` | same | URL in `msg` | issue-gated |
| P3 | `@skipXPU` | `common_device_type` | trailing `#` comment | issue-gated |
| P4 | `@skipXPUIf(True, "reason")` | `common_device_type` | `reason` string | issue-gated |
| P5 | `if self.device_type not in ("cpu","cuda"):` | inline in test body | none | always probed |
| P6 | `@unittest.skipIf(not TEST_CUDA, ...)` | `unittest`/`common_utils` | none | always probed |
| P7 | bare `skipXPU` in `OpInfo.decorators=[...]` | `common_methods_invocations.py` (op_db) | trailing `#` comment, else none | issue-gated if found, else probed |
| P8 | `DecorateInfo(unittest.skip/expectedFailure, 'Class','test', device_type='xpu', ...)` in `OpInfo.skips=(...)` | `common_methods_invocations.py` (op_db) | skip message / trailing `#`, else none | issue-gated if found, else probed |

**P1-P4**: remove only when `gh issue view` confirms CLOSED; leave OPEN/unknown untouched.
**P5-P6**: no issue to gate on — always widen and test.

**P7 (blanket, cross-file)**: `skipXPU` is a bare function ref in one
`OpInfo.decorators=[]`. `@ops(op_db)` applies it to every test method in
every file that iterates `op_db`/`foreach_*_op_db` for that operator (e.g.
`test_meta_xpu.py`, `test_ops_xpu.py`, `test_ops_gradients_xpu.py`
simultaneously). Not a per-class decorator — P1-P6's class-body discovery
scope does not apply. Verification must cover **every** affected file; one
failing file reverts the whole edit (a shared source change can't be
scoped per file).

**P8 (single-method, scoped)**: names an exact `(Class, test_name)` pair,
often narrowed by `dtypes=[...]`/`active_if=...`. `Class` is the upstream
*generic* class (pre `instantiate_device_type_tests`); find its XPU
instantiation file via
`grep -rl "instantiate_device_type_tests(<Class>" test/xpu/*.py` — that's
what to run. Skip verification entirely (leave untouched) if `active_if`
evaluates `False` on this host (e.g. `IS_WINDOWS` on Linux). Verification
must cover every `dtype` the entry names (or all XPU dtypes if none
given); any one dtype failing reverts the whole entry. For
`device_type=('cuda','xpu')`-style tuples, narrow to drop `'xpu'` instead
of deleting the entry (it may still be valid for the other device).

## Inputs

| Field | Required | Description |
|-------|----------|-------------|
| `test_file` | Unless P7/P8-only | Path to the test file. |
| `test_class` | Unless P7/P8-only | Test class name; scans all its methods. |
| `conda_env` | Yes | Conda env with XPU PyTorch installed. |
| `pytorch_folder` | Yes | Root of the local `pytorch/pytorch` checkout. |
| `target_method` | No | Probe only this method; omit for all methods. |
| `op_info_name` | No | `OpInfo` name (e.g. `'torch.ops.aten._flash_attention_forward'`) to probe for P7 (`decorators=[]`) and/or P8 (`skips=(...)`) in `common_methods_invocations.py`. |
| `affected_files` | No, P7 | Explicit `test_file::test_class` pairs parametrizing this OpInfo. If omitted, discover via `grep -rl "'<op_info_name>'"` / `grep -rln "op_db\|foreach_.*_op_db"` across `test/xpu/*.py`. |
| `p8_target` | No, P8 | `{'test_class', 'test_name'}` to probe one specific `DecorateInfo`; omit to probe every XPU-scoped entry found. |

## Tools Used

- **grep / Read**: discover skip decorators/guards/DecorateInfo entries.
- **bash**: `gh issue view` for issue state; `pytest` to verify.
- **edit**: remove/modify a skip, revert on failure.
- **git**: revert changes on failure.
- **scripts/generate_op_db_override.py**: P7 non-editable-install fallback;
  generates a throwaway pytest plugin that removes a bare `skipXPU` token
  from an OpInfo's `decorators` in-memory (see Step 0b).

## Logging

All artifacts under `agent_space/` in the `torch-xpu-ops` checkout:

```
agent_space/
├── session_log.txt
└── remove_xpu_skips/
    ├── <slug>__discovery.json      # Step 1
    ├── <slug>.json                 # final report (Step 6)
    ├── overrides/                  # P7 non-editable-install fallback plugins
    │   └── op_db_override__<op_info_slug>.py
    └── logs/<slug>__<method>__xpu.txt
```

Two mandatory log points per skip: pre-probe (before edit+test) and
post-probe (keep/revert decision), each a `session_log.txt` line:

```bash
echo "[$(date '+%Y-%m-%d %H:%M:%S')] remove-xpu-skips | <probe-start|probe-keep|probe-revert> | <context k:v pairs> | pattern: <P1-P8>" \
  >> <torch-xpu-ops>/agent_space/session_log.txt
```

`<context k:v pairs>` = `file/class/skip` for P1-P6, `op_info/affected_files`
for P7, `op_info/test_class/test_name` for P8. Final JSON report written at
end of Step 6.

## Workflow

### Step 0: Preconditions

```bash
cd /tmp
PYTHONPATH="" conda run -n <conda_env> python -c "import torch; print('xpu:', torch.xpu.is_available())"
gh auth status
mkdir -p <torch-xpu-ops>/agent_space/remove_xpu_skips
```

Stop and report if either check fails.

**Step 0b (P7/P8 only) — editable-install check.** `common_methods_invocations.py`
lives inside the `torch` package, not under `test/`. If `<conda_env>`'s
`torch` is a built wheel, editing `pytorch_folder`'s copy never reaches the
running interpreter — the probe would silently "pass" without validating
anything.

```bash
PYTHONPATH="" conda run -n <conda_env> python -c \
  "import torch.testing._internal.common_methods_invocations as m; print(m.__file__)"
```

Compare against `<pytorch_folder>/torch/testing/_internal/common_methods_invocations.py`.

- **Same file (editable install of this checkout)** → proceed with Step 1-6
  as written (file edit + revert).
- **Different file, P7** → do NOT stop. Fall back to the in-memory override
  (below) instead of editing any file.
- **Different file, P8** → out of scope for the override; stop this probe
  with `verdict: skip_not_editable_install`.

**Any Step 0b stop is still a probe outcome, not a silent early exit — log
it exactly like a normal Step 6 completion, even though Steps 1-5 never ran:**

```bash
echo "[$(date '+%Y-%m-%d %H:%M:%S')] remove-xpu-skips | probe-stop | op_info: <op_info_name> | pattern: P8 | verdict: skip_not_editable_install" \
  >> <torch-xpu-ops>/agent_space/session_log.txt
cat > <torch-xpu-ops>/agent_space/remove_xpu_skips/op_db__<op_info_slug>.json << 'EOF'
{
  "op_info_name": "<op_info_name>",
  "pattern": "P8",
  "total_skips_found": 0,
  "results": [],
  "summary": {"kept": 0, "reverted": 0, "skipped_open_issue": 0, "skipped_no_issue": 0,
              "issue_check_failed": 0, "skipped_not_editable_install": 1,
              "skipped_active_if_false_here": 0, "skipped_class_not_xpu_enabled": 0,
              "override_verified_would_pass": 0, "override_verified_still_fails": 0},
  "note": "Step 0b found conda_env's torch resolves common_methods_invocations.py to a different file than pytorch_folder's checkout; P8 has no in-memory fallback, so this OpInfo was never probed."
}
EOF
```

This is the ONLY way `agent_space/remove_xpu_skips/` and `session_log.txt`
end up non-empty when every OpInfo probed in a run hits the non-editable
P8 case — do not treat "stop this probe" as license to skip logging.

### P7 non-editable-install fallback: in-memory op_db override

Rather than editing `common_methods_invocations.py` on disk (which the
running interpreter would never see), generate a throwaway pytest plugin
that removes the bare `skipXPU` token from the target `OpInfo.decorators`
in the process pytest actually runs in, then verify with that plugin
loaded:

```bash
mkdir -p <torch-xpu-ops>/agent_space/remove_xpu_skips/overrides
python3 <remove-xpu-skips>/scripts/generate_op_db_override.py \
  --op-info-name '<op_info_name>' \
  --output <torch-xpu-ops>/agent_space/remove_xpu_skips/overrides/op_db_override__<op_info_slug>.py
```

This prints a JSON object with `pytest_plugin_module` (the `-p` argument)
and `plugin_dir` (put on `PYTHONPATH`). Use them in Step 4 in place of the
plain pytest command, for **every** affected file/class from Step 1's P7
discovery — same all-files-must-pass rule as the normal P7 path:

```bash
for pair in <affected_files>; do  # e.g. test_meta_xpu.py::TestMetaXPU
  file="${pair%%::*}"; cls="${pair##*::}"
  PYTHONPATH="<plugin_dir>" conda run -n <conda_env> python -m pytest \
      "$file" -v -k "$cls and xpu and <op_short_name>" \
      -p <pytest_plugin_module> --tb=short --no-header 2>&1 \
      | tee ".../logs/op_db__<op_info_slug>__${file}__${cls}__xpu.txt"
done
```

The plugin's `pytest_configure` hook runs before test collection and
mutates the shared `op_db` list object in that process only; nothing is
written to `pytorch_folder` or the installed `torch` package, and the
mutation vanishes when the pytest process exits. This is diagnostic only —
it answers "would removing this skip make the test pass in this
environment/build?", not a substitute for landing the real source-code
fix (which still requires an editable install or a PR to
`common_methods_invocations.py`).

Evaluate exactly as Step 5's P7 row (every file must pass to KEEP), but
the "revert" action is a no-op (delete the generated plugin file if you
don't need it for the report) since nothing was written to
`pytorch_folder`. Record the verdict as `skip_not_editable_install` (P8) or
the override's own result — `verdict: override_verified_would_pass` /
`override_verified_still_fails` — in the final report's `action` field for
P7 entries that took this path (see Output below), never silently
reclassified as a normal `keep`/`revert` disk edit.

### Step 1: Discover

Read the target class body (P1-P6). If `op_info_name` is set, additionally
locate the `OpInfo(...)` block in `common_methods_invocations.py`
(search `'<op_info_name>',`) and read to its closing `)`.

For each skip, record: `method`/`pattern`/`line`/`code`/`issue_url`/`issue_repo`/`issue_number`.
Issue URL extraction: match `https://github.com/([^/]+)/([^/]+)/issues/(\d+)`
in `msg=`/`reason=` kwargs, trailing `#` comments, or (P8) the
`unittest.skip("...")` message string. First URL found wins; else `null`
(unconditionally probed).

**P7 sub-discovery**: confirm `decorators=[...]` has a bare `skipXPU` (not
`skipXPUIf(...)`/`dtypesIfXPU` — different mechanisms). Discover every
affected file (unless `affected_files` given): `grep -rl "'<op_info_name>'"
test/xpu/*.py`, cross-checked against `skip_list_common.py` exclusions. Log
the full discovered set — verification (Step 4B) must cover all of it.

**P8 sub-discovery**: in `skips=(...)`, find every `DecorateInfo(...)` whose
`device_type` is `'xpu'` or a tuple/list containing it. Filter to `p8_target`
if given. For each: record `test_class`/`test_name`/`dtypes`/`active_if`/
`decorator_type` (`unittest.skip` or `unittest.expectedFailure`). Resolve
the XPU instantiation file for `test_class` (see above); if none exists,
record `verdict: skip_class_not_xpu_enabled`, don't attempt removal. If
`active_if` evaluates `False` here, record `verdict: skip_active_if_false_here`,
don't attempt removal.

**Log**: write discovery list to
`<torch-xpu-ops>/agent_space/remove_xpu_skips/<slug>__discovery.json`
(`<slug>` = `<test_file>__<test_class>` with `/`→`_`, or
`op_db__<op_info_name_slug>` for P7/P8-only probes).

### Step 2: Check Issue State (P1-P4, P7/P8 if a URL was found)

```bash
gh issue view <N> --repo <owner>/<repo> --json state,stateReason,title
```

- **CLOSED** → proceed to removal (Step 3).
- **OPEN** → leave untouched, report `skip_open_issue`.
- **`gh` fails** → leave untouched (safe default), report `skip_issue_check_failed`.

No `issue_url` (P5, P6, or unresolved P1-P4/P7/P8) → skip straight to removal.

### Step 3: Remove/Modify

| Pattern | Edit |
|---|---|
| P1/P2 `@skipIfXpu(...)` | Remove the decorator line entirely (+ one blank line if present between it and `def`). |
| P3 `@skipXPU  # url` | Remove the decorator line entirely. |
| P4 `@skipXPUIf(True, "reason")` | Remove the decorator line entirely. |
| P5 `if self.device_type not in ("cpu","cuda"):` | Widen to `("cpu", "cuda", "xpu")`. |
| P6 `@unittest.skipIf(not TEST_CUDA, ...)` | Change to `not TEST_CUDA and not TEST_XPU`; import `TEST_XPU` from `common_utils` if missing. |
| P7 `decorators=[skipXPU, ...]` | Remove only the `skipXPU` token (+trailing comma). If it's the only entry, use `decorators=[]`. |
| P8, single device (`device_type='xpu'`) | Remove the entire `DecorateInfo(...)` entry (all lines) + trailing comma. |
| P8, multi-device tuple (`device_type=('cuda','xpu')`) | Narrow the tuple to drop `'xpu'` only; keep the entry for other device(s). |

**Never** remove other decorators on the same method, other entries in the
same `decorators=[]`/`skips=(...)`, or touch other `OpInfo(...)`/`class`
blocks. Confirm method signatures/other decorators are intact after edit.

Log before running tests (pre-probe line, per Logging section above).

### Step 4: Verify on XPU

**P1-P6** — target method (or whole class if unknown):
```bash
cd /tmp
PYTHONPATH="" conda run -n <conda_env> python -m pytest \
    <test_file> -v -k "<ClassName> and xpu and <method_name>" --tb=short --no-header 2>&1 \
    | tee <torch-xpu-ops>/agent_space/remove_xpu_skips/logs/<slug>__<method>__xpu.txt
```
Always run from `/tmp` with `PYTHONPATH=""` to avoid the local checkout
shadowing the installed package.

**P7** — every affected file/class from Step 1, each targeting the op:
```bash
for pair in <affected_files>; do  # e.g. test_meta_xpu.py::TestMetaXPU
  file="${pair%%::*}"; cls="${pair##*::}"
  PYTHONPATH="" conda run -n <conda_env> python -m pytest \
      "$file" -v -k "$cls and xpu and <op_short_name>" --tb=short --no-header 2>&1 \
      | tee ".../logs/op_db__<op_info_slug>__${file}__${cls}__xpu.txt"
done
```

**P8** — resolved test file, every dtype the entry names (or all XPU
dtypes if `dtypes` was `null`); confirm the actual dtype-suffix convention
from a nearby passing test ID before building the `-k` filter:
```bash
for dt in <dtypes_or_all_xpu_dtypes>; do
  short="${dt##*.}"
  PYTHONPATH="" conda run -n <conda_env> python -m pytest \
      "<resolved_test_file>" -v -k "<Class>XPU and xpu and <test_name> and ${short}" \
      --tb=short --no-header 2>&1 | tee ".../logs/op_db__<op_info_slug>__<test_name>__${short}__xpu.txt"
done
```

### Step 5: Evaluate — Keep or Revert

| Scope | KEEP when | REVERT when |
|---|---|---|
| P1-P6 (single method) | `PASSED`/`XFAIL`; other methods' pre-existing failures don't count | `FAILED`/`ERROR`; hang >5min; collection breaks |
| P7, editable install (all affected files) | **every** file/class run passes/xfails | **any** file fails, hangs, or breaks collection |
| P7, non-editable install (override fallback) | N/A — nothing was written to disk, so there is nothing to keep/revert. Record `override_verified_would_pass` if **every** affected file passes with the plugin loaded, else `override_verified_still_fails`. Delete the generated plugin file once logged (Step 6). | (not applicable — see above) |
| P8 (all named dtypes) | **every** dtype passes/xfails (an `expectedFailure` unexpectedly `PASSED` still counts as keep) | **any** dtype fails/errors/breaks; or Step 0b/`active_if` made it unverifiable (→ `skip_not_editable_install`/`skip_active_if_false_here`, not revert) |

**If KEEP**: log `probe-keep`, leave the change in the working tree, move to
the next skip.

**If REVERT**: log `probe-revert` with a failure reason, then:
```bash
cd <pytorch_folder>
git checkout -- <test_file>                                          # P1-P6
git checkout -- torch/testing/_internal/common_methods_invocations.py # P7, P8
git diff -- <file>   # confirm clean
```
Use `git checkout --`, not `git restore` or manual re-edit, unless
`git checkout` fails for that file.

### Step 6: Repeat, then Report

Continue with the next skip (Step 1 discovery → 2 → 3 → 4 → 5). Kept
changes persist for the next probe; reverted ones don't. After all skips
are processed, write the final JSON report and log completion:

```bash
cat > <torch-xpu-ops>/agent_space/remove_xpu_skips/<slug>.json << 'EOF'
<final report JSON — see schema below>
EOF
echo "[$(date '+%Y-%m-%d %H:%M:%S')] remove-xpu-skips | complete | total: <N> | kept: <N> | reverted: <N>" \
  >> <torch-xpu-ops>/agent_space/session_log.txt
```

## Output

```json
{
  "file": "test/test_foo.py",
  "class": "TestFoo",
  "total_skips_found": 4,
  "results": [
    {"method": "test_bar", "pattern": "P1", "line": 42, "issue_number": "1234",
     "issue_repo": "intel/torch-xpu-ops", "issue_state": "CLOSED",
     "action": "removed", "verdict": "keep", "test_result": "PASSED"},
    {"method": "test_qux", "pattern": "P3", "line": 120, "issue_number": "1581",
     "issue_state": "OPEN", "action": "none", "verdict": "skip_open_issue", "test_result": "not_run"},
    {"op_info_name": "torch.ops.aten._flash_attention_forward", "pattern": "P7",
     "affected_files": ["test_meta_xpu.py::TestMetaXPU"], "action": "removed",
     "verdict": "revert", "test_result": "FAILED (test_meta_xpu.py::TestMetaXPU)"},
    {"op_info_name": "torch.ops.aten.some_op", "pattern": "P8",
     "test_class": "TestUnaryUfuncs", "test_name": "test_reference_numerics_extremal",
     "dtypes": ["torch.cfloat", "torch.cdouble"], "active_if": "IS_WINDOWS",
     "action": "removed", "verdict": "keep",
     "test_result": "PASSED (complex64, complex128)"},
    {"op_info_name": "torch.ops.aten._flash_attention_forward", "pattern": "P7",
     "affected_files": ["test_meta_xpu.py::TestMetaXPU"],
     "action": "override_in_memory", "verdict": "override_verified_still_fails",
     "test_result": "FAILED (test_meta_xpu.py::TestMetaXPU) with plugin op_db_override__torch_ops_aten__flash_attention_forward loaded",
     "plugin_path": "agent_space/remove_xpu_skips/overrides/op_db_override__torch_ops_aten__flash_attention_forward.py"}
  ],
  "summary": {"kept": 2, "reverted": 1, "skipped_open_issue": 1, "skipped_no_issue": 0,
              "issue_check_failed": 0, "skipped_not_editable_install": 0,
              "skipped_active_if_false_here": 0, "skipped_class_not_xpu_enabled": 0,
              "override_verified_would_pass": 0, "override_verified_still_fails": 1}
}
```

`action`: `removed` | `narrowed_to_drop_xpu` (P8 multi-device) | `widened_to_include_xpu` (P5/P6) | `override_in_memory` (P7 non-editable-install fallback) | `none`.

`verdict`: `keep` | `revert` | `skip_open_issue` | `skip_no_issue_skip` |
`skip_issue_check_failed` | `skip_not_editable_install` (P8, or P7 when the
override fallback itself could not be attempted) |
`skip_active_if_false_here` (P8) | `skip_class_not_xpu_enabled` (P8) |
`override_verified_would_pass` (P7 fallback: every affected file passed
with the skip removed in-memory) | `override_verified_still_fails` (P7
fallback: at least one affected file still failed/errored with the skip
removed in-memory).

## Constraints

1. **One skip at a time.** Sequential, never batched, so each removal is
   independently verified.
2. **P1-P4 issue-gated.** Remove only when `gh issue view` confirms CLOSED;
   `gh` failure = safe default of no removal.
3. **P5-P6 always probed** (no issue to gate on).
4. **Revert cleanly** via `git checkout --`, never partial/manual undo.
5. **Never skip verification** — every removal is followed by a pytest run.
6. **Never touch OPEN-issue skips**, no matter how confident.
7. **Run from `/tmp`** with `PYTHONPATH=""`.
8. **No shortened timeouts** — a hang is a failure, not a skip.
9. **Touch only the exact skip.** Leave other decorators/entries/blocks
   alone; an unused import left behind is acceptable (out of scope).
10. **No test-body edits beyond P5's guard widening.** Never fix latent
    bugs, add `device=`, or rewrite assertions.
11. **P7 verification covers every affected file** — never "keep" from one
    file's pass alone; any single-file failure reverts the shared edit.
12. **P7/P8 require Step 0b's editable-install check first** — never edit
    `common_methods_invocations.py` blindly.
13. **P7 removes only the `skipXPU` token** — no other `decorators=[]`
    entries, kwargs, or `OpInfo` blocks.
14. **P8 verification covers every named dtype** — any single-dtype failure
    reverts the whole entry (no partial per-dtype removal by default).
15. **P8 respects `active_if`** — never probe an entry inert on this host.
16. **P8 narrows multi-device tuples** (drop `'xpu'` only) instead of
    deleting entries that also apply to other devices.
17. **The P7 override fallback is throwaway/diagnostic only.** It never
    writes to `pytorch_folder` or the installed `torch` package; a
    generated plugin file lives only under `agent_space/` and is never
    treated as a "keep" that persists like a real edit. It answers whether
    the skip is stale, it does not fix the skip — landing the actual
    removal in `common_methods_invocations.py` still requires an editable
    install (or a PR).
18. **The override fallback is P7-only.** P8's single-method
    `DecorateInfo` entries stay `skip_not_editable_install` on a
    non-editable install; do not attempt the in-memory approach for P8.

## See Also

- `develop-xpu-test` — enables XPU at the class level first.
- `verify-xpu-test` — broader class-level XPU verification.
- `enable-xpu-test` — end-to-end orchestrator; can integrate this as a
  post-development cleanup step.
- `issue-triage/reproduce-issue` — invokes this skill's skip-removal loop
  (including the P7 override fallback) when a UT case comes back
  `SKIPPED`.
</content>
