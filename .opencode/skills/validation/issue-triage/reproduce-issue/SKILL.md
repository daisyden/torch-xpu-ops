---
name: reproduce-issue
description: Locally verify whether an intel/torch-xpu-ops (or pytorch) issue's failing test case(s) still reproduce, by running pytest (unit tests) or the issue's reproduce steps (e2e/other) inside a given conda env against a local pytorch checkout, and comparing the observed failure against the issue's traceback signature. Use after extract-basic-info to confirm a bug reproduces on this machine. Outputs JSON.
---

# Reproduce Issue Locally

Runs test cases from one issue and reports whether they still fail the way the
issue describes. Does NOT build, install, or touch the network. Assumes the
environment is already in place.

## Inputs

```
python3 scripts/reproduce_issue.py --input <JSON> --conda-env <env> \
    --pytorch-folder <dir> [--timeout 600] [--output PATH] [--rerun]
```

| Flag | Required | Meaning |
|------|----------|---------|
| `--input` | Yes (or stdin) | JSON: single case, extract-basic-info object with `test_cases`, or array. |
| `--conda-env` | Yes | Conda env with XPU `torch`. |
| `--pytorch-folder` | Yes | Local pytorch or torch-xpu-ops checkout. |
| `--timeout` | No | Per-case timeout seconds (default 600). |
| `--output` | No | Also write JSON report to this path. |
| `--rerun` | No | Re-run after skip removal; suppresses further skip-removal handoff. |

Per-case fields: `test_file`, `test_class`, `test_case`, `test_type`, `source`,
`traceback`, `reproduce_steps`, `op_db_override_plugin_dir`/`_module` (rerun only).

## How it runs

Commands run via `conda run --no-capture-output -n <env>`. Torch probe from `/tmp`.

**UT cases:**
- torch-xpu-ops: cwd = `<pf>/third_party/torch-xpu-ops/test/xpu` (or `<pf>/test/xpu`).
- pytorch: cwd = `<pf>/test`.
- Exact nodeid (`file::Class::method`) when class known, else `-k <method>`.
- Env-setup lines from `reproduce_steps` applied; build lines ignored.
- File not on disk -> `NO_TEST_FOUND`.

**Non-UT cases:** `reproduce_steps` runs verbatim via `bash -lc`. No steps -> `CANNOT_VERIFY`.

Timeout -> SIGKILL -> `CANNOT_VERIFY` with reason `timeout`.

## Output schema

Top-level: `torch_version`, `torch_commit`, `xpu_available`, `conda_env`,
`pytorch_folder`, `test_time`, `summary.{total, reproduced, not_reproduced, cannot_verify, needs_skip_removal}`,
`results[]`.

**Mandatory top-level fields** (agent MUST populate these):

| Field | Source | Example |
|---|---|---|
| `torch_version` | `torch.__version__` via conda env | `"2.8.0a0+gitabc1234"` |
| `torch_commit` | `torch.version.git_version` via conda env | `"abc1234def5678..."` |
| `xpu_available` | `torch.xpu.is_available()` via conda env | `true` |
| `test_time` | ISO 8601 UTC timestamp when the reproduce run started | `"2026-07-25T09:00:00Z"` |

These fields are collected via a probe command before running tests:
```bash
conda run --no-capture-output -n <env> python -c \
  "import torch, json, datetime; print(json.dumps({'torch_version': torch.__version__, 'torch_commit': torch.version.git_version, 'xpu_available': torch.xpu.is_available(), 'test_time': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}))"
```

Per-case: `test_file`, `test_case`, `test_class`, `test_repo`, `resolved_test_path`,
`result`, `reproduced`, `matched_error`, `reason`, `command`, `exit_code`,
`actual_error`, `skipped`, `duration_s`, `raw_tail`, `skipped_build_lines`,
`needs_skip_removal`, `skip_removal_request`, `skip_removal_attempted`, `skip_removal_result`.

## Reproduced determination

| Observed | Reference traceback | Verdict |
|----------|---------------------|---------|
| `FAILED`/`ERROR` | present, same type+message | `reproduced=true, matched_error=true` |
| `FAILED`/`ERROR` | present, different | `reproduced=false` ("different failure") |
| `FAILED`/`ERROR` | absent | `reproduced=true, matched_error=false` |
| `PASSED` | any | `reproduced=false` |
| `SKIPPED` | any | `reproduced=false` — **NOT a final verdict; skip-removal MUST be attempted** |
| `NO_TEST_FOUND` | any | `reproduced=false` |

## Skip-removal retry loop — MANDATORY

**⚠️ NON-NEGOTIABLE: You MUST NOT return `result="SKIPPED"` without first
attempting skip removal.** A SKIPPED test provides ZERO information about
whether the bug exists. Treating SKIPPED as "not reproduced" or "passed" is
ALWAYS WRONG.

**This applies regardless of HOW the skip occurs:**
- `@skipXPU` / `@skipIfXpu` decorator -> remove-xpu-skips handles it
- `self.skipTest()` inside test body -> remove-xpu-skips handles it
- op_db `DecorateInfo` skip entry -> remove-xpu-skips handles it
- `unittest.skipIf(not TEST_CUDA)` guard -> remove-xpu-skips handles it

**You MUST NOT reason that "this skip is intentional" or "this is the expected
behavior" and bypass the retry loop.** The remove-xpu-skips skill will
determine whether the skip can be removed. If it cannot (open issue, reverted),
the result is `skip_maintained` — that is the ONLY valid way to conclude a
SKIPPED case.

The script sets `needs_skip_removal=true` and emits `skip_removal_request`
(`test_file`, `test_class`, `target_method`, `conda_env`, `pytorch_folder`).
The script does NOT edit tests or call gh.

**The AGENT drives a bounded retry (once per case):**

1. Invoke `remove-xpu-skips` skill with the request params. It probes the skip:
   CLOSED-issue -> removes decorator -> runs test -> keeps/reverts.
   Non-editable install (P7) -> generates in-memory override plugin.

2. Re-run this single case with `--rerun`, setting `skip_removal_attempted=true`
   and copying `skip_removal_result`. For override path, also set
   `op_db_override_plugin_dir` and `op_db_override_plugin_module`.

   Final verdict from re-run:
   - `FAILED` + matching signature -> `reproduced=true`
   - `PASSED` -> not reproduced (skip was stale)
   - still `SKIPPED` -> `reason=skip_maintained` (open issue / reverted). Loop stops.

Example (editable install):
```bash
python3 scripts/reproduce_issue.py \
  --input '{"test_file":"test_meta_xpu.py","test_class":"TestMetaXPU","test_case":"test_foo_xpu_float16","test_type":"ut","skip_removal_attempted":true,"skip_removal_result":"kept: PASSED"}' \
  --conda-env nightly --pytorch-folder ~/pytorch --rerun
```

Example (non-editable, override plugin):
```bash
python3 scripts/reproduce_issue.py \
  --input '{"test_file":"test_meta_xpu.py","test_class":"TestMetaXPU","test_case":"test_foo_xpu_float16","test_type":"ut","skip_removal_attempted":true,"skip_removal_result":"override_verified_still_fails","op_db_override_plugin_dir":"agent_space/remove_xpu_skips/overrides","op_db_override_plugin_module":"op_db_override__torch_ops_aten_foo"}' \
  --conda-env pytorch_xpu --pytorch-folder ~/pytorch --rerun
```

## Exit codes

| Code | When |
|------|------|
| 0 | Completed (any per-case result). |
| 1 | Setup failure (missing env/checkout/torch/XPU). |
| 2 | Bad input JSON. |

Per-case SKIPPED/CANNOT_VERIFY never causes non-zero exit.

## Prerequisites

- `conda` on PATH with the named env (XPU torch installed).
- Local pytorch/torch-xpu-ops checkout at `--pytorch-folder`.
- Skip-removal loop: `gh` authenticated.
- No network needed by the script itself.

## Scope

Runs tests and reports outcomes. Does NOT build, install, fetch, or clone.
The SCRIPT does NOT edit tests/git/gh. Skip removal is delegated to
`remove-xpu-skips`, invoked separately by the agent.
