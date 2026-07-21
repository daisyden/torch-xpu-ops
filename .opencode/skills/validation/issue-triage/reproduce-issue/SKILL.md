---
name: reproduce-issue
description: Locally verify whether an intel/torch-xpu-ops (or pytorch) issue's failing test case(s) still reproduce, by running pytest (unit tests) or the issue's reproduce steps (e2e/other) inside a given conda env against a local pytorch checkout, and comparing the observed failure against the issue's traceback signature. Use after extract-basic-info to confirm a bug reproduces on this machine. Outputs JSON.
---

# Reproduce Issue Locally

A LOCAL verifier: it runs a test case that is already installed and asks "does
this still fail the way the issue says?" It does not build, does not install a
wheel, and does not touch the network. Contrast with `fix/reproduce`, which
stages nightly-wheel -> source-build -> CI-environment to hunt down a
reproduction; this skill assumes the environment is already in place and only
observes the current outcome.

## When to use

Use this right after `extract-basic-info` has produced a `test_cases[]` array
for one issue, when you want to confirm the bug reproduces on this machine
before spending effort on a fix. It handles a single case or several cases from
one issue per invocation.

Requirements you must already have: a conda env with an XPU-enabled `torch`
installed, and a local `pytorch` checkout (or a standalone `torch-xpu-ops`
checkout) whose test tree supplies the test files and support modules. If the
env is missing torch, has no XPU device, or the checkout is absent, the run
reports `CANNOT_VERIFY` rather than guessing.

## Inputs

CLI:

```
python3 scripts/reproduce_issue.py --input <JSON> --conda-env <env> \
    --pytorch-folder <dir> [--timeout 600] [--output PATH] [--rerun]
```

| Flag | Required | Meaning |
|------|----------|---------|
| `--input` | Yes (or stdin) | JSON: a single case dict, an extract-basic-info object with `test_cases:[...]`, or a bare array of case dicts. If omitted, JSON is read from stdin. |
| `--conda-env` | Yes | Conda env name with an XPU `torch` installed. |
| `--pytorch-folder` | Yes | Path to the local pytorch checkout (or a standalone torch-xpu-ops checkout). |
| `--timeout` | No | Per-case timeout in seconds (default 600). |
| `--output` | No | Also write the JSON report to this path (still printed to stdout). |
| `--rerun` | No | Re-run a single case after skip removal; suppresses further skip-removal handoff. See the retry loop below. |

Per-case fields read from each element:

- `test_file`, `test_class`, `test_case`: the test id. `test_file` is
  polymorphic (bare basename, `test/xpu/...`, `torch-xpu-ops/test/...`, or a
  Windows path) and is normalized.
- `test_type`: a UT type (`ut`, `op_ut`, `op_extend`, `op_extended`,
  `test_xpu`) routes to the pytest path; anything else is treated as non-UT.
- `source`: `torch-xpu-ops` or `pytorch` when known; otherwise the repo is
  inferred from the file (an `_xpu` stem or a `torch-xpu-ops` path segment maps
  to torch-xpu-ops, else pytorch).
- `traceback`: the reference failure signature for this case.
- `reproduce_steps`: shell lines. Env-setup lines feed the UT command prefix,
  and the whole block is the command for non-UT cases.
- `op_db_override_plugin_dir` / `op_db_override_plugin_module`: only used on
  a `--rerun` pass after `remove-xpu-skips`' P7 non-editable-install
  fallback. `plugin_dir` is exported onto `PYTHONPATH` and
  `plugin_module` is passed as `-p <module>`, so the in-memory op_db skip
  removal loads before pytest collection. See the retry loop below.

Top-level inheritance rule: when a case lacks its own `traceback` or
`reproduce_steps`, it inherits the top-level `traceback` / `reproduce_steps`
ONLY when it is unambiguous, i.e. there is a single case, OR the case's
`test_case` name appears verbatim in the top-level text. The caller's input
dict is never mutated; inherited steps are applied on a copy.

## How it runs

Every command runs through `conda run --no-capture-output -n <env>`. The torch
probe runs first from a neutral cwd (`/tmp`), never inside a pytorch checkout,
so `import torch` resolves to the installed wheel and not an in-tree
`torch/_C.so`.

Unit-test (UT) cases:

- torch-xpu-ops repo: cwd is `<pf>/third_party/torch-xpu-ops/test/xpu`. If that
  directory is absent but `<pf>/test/xpu` exists (i.e. `--pytorch-folder` IS a
  standalone torch-xpu-ops checkout), the standalone `<pf>/test/xpu` is used.
- pytorch repo: cwd is `<pf>/test`.
- When the test class is known, an exact pytest nodeid
  (`rel::Class::method`) is built; otherwise the file is run with `-k
  <method>`. Always `-v`.
- Env-setup lines from `reproduce_steps` (`source ...`, `export ...`,
  leading `VAR=value` tokens, `ZE_*` masks) are applied via a `bash -lc`
  prefix. Build/install lines (`pip install`, `python setup.py`, `cmake`,
  `ninja`, `conda install`) are ignored and recorded in `skipped_build_lines`.
- If the file cannot be resolved on disk, the case is `NO_TEST_FOUND`.

Non-UT cases:

- The `reproduce_steps` block runs verbatim via `bash -lc` with cwd set to
  `--pytorch-folder`. Non-zero exit is `FAILED`, zero is `PASSED`. With no
  `reproduce_steps`, the case is `CANNOT_VERIFY`.

All runs are bounded by `--timeout`; on timeout the whole process group is
SIGKILLed (start_new_session isolates the child group), which maps to
`CANNOT_VERIFY` with reason `timeout`.

## Output schema

Top-level object:

| Field | Meaning |
|-------|---------|
| `torch_version` | `torch.__version__` in the env. |
| `torch_commit` | `torch.version.git_version`, or "". |
| `xpu_available` | Bool from `torch.xpu.is_available()`. |
| `conda_env` | Echoed input. |
| `pytorch_folder` | Echoed input. |
| `summary.total` | Number of cases. |
| `summary.reproduced` | Count with `reproduced=true`. |
| `summary.not_reproduced` | `total - reproduced - cannot_verify`. |
| `summary.cannot_verify` | Count with `result=CANNOT_VERIFY`. |
| `summary.needs_skip_removal` | Count with `needs_skip_removal=true`. |
| `results` | Array of per-case objects (below). |

Per-case object:

| Field | Meaning |
|-------|---------|
| `test_file` | Original input file string. |
| `test_case` | Test method name. |
| `test_class` | Test class name. |
| `test_repo` | `torch-xpu-ops` or `pytorch` (resolved). |
| `resolved_test_path` | Path relative to the test dir, or "". |
| `result` | `PASSED` \| `FAILED` \| `ERROR` \| `SKIPPED` \| `NO_TEST_FOUND` \| `CANNOT_VERIFY`. |
| `reproduced` | Bool: does the observed failure match the issue? |
| `matched_error` | Bool: the failure signature matched the reference traceback. |
| `reason` | Human-readable verdict explanation. |
| `command` | Exact command that ran. |
| `exit_code` | Process exit code, or null. |
| `actual_error` | Last `ExceptionType: message` line observed. |
| `skipped` | Bool: `result == "SKIPPED"`. |
| `duration_s` | Wall time of the run. |
| `raw_tail` | Last ~4000 chars of combined stdout/stderr. |
| `skipped_build_lines` | Build/install lines ignored from `reproduce_steps`. |
| `needs_skip_removal` | Bool: UT case came back `SKIPPED` (handoff below). |
| `skip_removal_request` | Params for `remove-xpu-skips`, or null. |
| `skip_removal_attempted` | Bool: set true on the `--rerun` pass. |
| `skip_removal_result` | Copy of the `remove-xpu-skips` summary, or "". |

`result` is the observed pytest/command outcome. `reproduced` is the judgment
of whether that outcome matches the issue (see below); a case can be `FAILED`
yet `reproduced=false` if the failure is a different error.

## Reproduced determination

`judge()` compares the observed outcome against the issue's reference
traceback:

| Observed | Reference traceback | Verdict |
|----------|---------------------|---------|
| `FAILED` / `ERROR` | present, same exception type + overlapping message | `reproduced=true`, `matched_error=true` |
| `FAILED` / `ERROR` | present, different type/message | `reproduced=false`, reason contains "different failure" |
| `FAILED` / `ERROR` | absent | `reproduced=true`, `matched_error=false` (matched on failure state only) |
| `PASSED` | any | `reproduced=false` (issue not reproduced) |
| `SKIPPED` | any | `reproduced=false` (needs skip removal to confirm) |
| `NO_TEST_FOUND` | any | `reproduced=false` (no matching test) |

Exception types compare case-insensitively; messages compare after stripping
hex addresses, path-like tokens, and `line N` references.

## Skip-removal retry loop (SKIPPED UT cases)

A UT case that comes back `SKIPPED` cannot confirm the bug: the test never ran.
The script flags it with `needs_skip_removal=true` and emits a
`skip_removal_request` carrying `test_file`, `test_class`, `target_method`,
`conda_env`, and `pytorch_folder`. The script itself does NOT edit any test,
call git, or call gh.

The AGENT drives a bounded retry, at most once per case:

1. Invoke the `remove-xpu-skips` skill
   (`.claude/skills/validation/enable-xpu-test/remove-xpu-skips/`, frontmatter
   name `remove-xpu-skips`) with the request params. It probes the skip: for
   CLOSED-issue skips it removes the decorator (or widens a P5/P6 device
   guard), runs the test on XPU, and keeps the change on pass or reverts on
   fail. It writes artifacts under `agent_space/` and needs `gh` authenticated.

   For an op_db-level (P7) skip on a **non-editable** torch install (a built
   wheel, or an editable install of a different checkout),
   `remove-xpu-skips` cannot edit `common_methods_invocations.py` and reach
   the running interpreter. Instead it generates a throwaway pytest plugin
   via `generate_op_db_override.py` that removes the skip in-memory, and
   returns `plugin_path`/`pytest_plugin_module`/`plugin_dir` alongside its
   verdict (`override_verified_would_pass` / `override_verified_still_fails`)
   instead of `kept`/`reverted`.

2. Re-run reproduce_issue on that single case with `--rerun`, setting
   `skip_removal_attempted=true` on the input case and copying the
   `remove-xpu-skips` summary into `skip_removal_result`. When
   `remove-xpu-skips` took the override path, also set
   `op_db_override_plugin_dir` and `op_db_override_plugin_module` from its
   response on the input case -- `run_ut_case` puts the plugin's directory
   on `PYTHONPATH` and adds `-p <module>` so the in-memory removal is loaded
   before pytest collects the test. The fresh outcome is the final verdict:
   - now `FAILED` with a matching signature -> `reproduced=true`;
   - now `PASSED` -> not reproduced (the skip was stale);
   - still `SKIPPED` -> `result=SKIPPED`, `reason=skip_maintained` (open issue,
     or reverted by remove-xpu-skips). The loop does not repeat.

Copy-paste two-step example (single SKIPPED case, editable install / normal
file-edit path):

```bash
# Step 1: agent invokes remove-xpu-skips with the skip_removal_request fields
#   test_file=<resolved>  test_class=<Class>  target_method=<method>
#   conda_env=nightly  pytorch_folder=/path/to/pytorch
# (this is a skill invocation, not a shell command)

# Step 2: re-run this single case with --rerun, feeding the skip-removal outcome
python3 scripts/reproduce_issue.py \
  --input '{"test_file":"test_meta_xpu.py","test_class":"TestMetaXPU","test_case":"test_dispatch_symbolic_meta_outplace_torch_ops_aten__flash_attention_forward_xpu_float16","test_type":"ut","skip_removal_attempted":true,"skip_removal_result":"kept: PASSED"}' \
  --conda-env nightly \
  --pytorch-folder /home/daisyden/opencode/skills_refactor \
  --rerun
```

Same example on a **non-editable install** (op_db override fallback): after
`remove-xpu-skips` generates the plugin and reports its
`pytest_plugin_module="op_db_override__torch_ops_aten__flash_attention_forward"`
and `plugin_dir="agent_space/remove_xpu_skips/overrides"`, feed both into the
`--rerun` input case:

```bash
python3 scripts/reproduce_issue.py \
  --input '{"test_file":"test_meta_xpu.py","test_class":"TestMetaXPU","test_case":"test_dispatch_symbolic_meta_outplace_torch_ops_aten__flash_attention_forward_xpu_float16","test_type":"ut","skip_removal_attempted":true,"skip_removal_result":"override_verified_still_fails","op_db_override_plugin_dir":"agent_space/remove_xpu_skips/overrides","op_db_override_plugin_module":"op_db_override__torch_ops_aten__flash_attention_forward"}' \
  --conda-env pytorch_xpu \
  --pytorch-folder /home/daisyden/opencode/skills_refactor \
  --rerun
```

This re-run's `command` field will show `-p
op_db_override__torch_ops_aten__flash_attention_forward` and an exported
`PYTHONPATH` prefix; the case comes back `FAILED`/`reproduced=true` instead
of the plain `SKIPPED` it would report without the plugin.

## Exit codes

| Code | When |
|------|------|
| 0 | Run completed (any per-case result, including not-reproduced / SKIPPED / CANNOT_VERIFY). |
| 1 | Setup failure: missing conda env, missing/not-a-directory `--pytorch-folder`, torch import failure, or no XPU device. Emits a top-level `CANNOT_VERIFY` JSON. |
| 2 | Bad input JSON. |

A per-case not-reproduced, SKIPPED, or CANNOT_VERIFY never makes the process
exit non-zero; only run-level setup problems do.

## Prerequisites

- No network access is needed by the script.
- `conda` on `PATH` with the named env, and that env must have an XPU-enabled
  `torch` (`torch.xpu.is_available()` true, else the run is `CANNOT_VERIFY`).
- A local `pytorch` checkout, or a standalone `torch-xpu-ops` checkout, at
  `--pytorch-folder`.
- Skip-removal loop only: `gh` authenticated (needed by `remove-xpu-skips` to
  check issue state and by its own XPU test runs; it also writes `agent_space/`
  artifacts).
- If `python3` or its deps are missing, check for a `.venv` in the project root
  or a parent directory and activate it, then retry (per AGENTS.md). Do NOT
  install tools yourself.

## Usage

Pipe an extract-basic-info result straight in:

```bash
python3 .claude/skills/validation/issue-triage/extract-issue-information/scripts/extract_basic_info.py 4344 \
  | python3 .claude/skills/validation/issue-triage/reproduce-issue/scripts/reproduce_issue.py \
      --conda-env nightly --pytorch-folder ~/pytorch
```

Or capture and pass via `--input`:

```bash
python3 .claude/skills/validation/issue-triage/reproduce-issue/scripts/reproduce_issue.py \
  --input "$(cat basic_info.json)" --conda-env nightly --pytorch-folder ~/pytorch
```

Single-case JSON with a custom timeout, also written to a file:

```bash
python3 .claude/skills/validation/issue-triage/reproduce-issue/scripts/reproduce_issue.py \
  --input '{"test_file":"test_ops_xpu.py","test_class":"TestCommonXPU","test_case":"test_foo_xpu","test_type":"ut"}' \
  --conda-env nightly --pytorch-folder ~/pytorch --timeout 900 --output repro.json
```

Live example (requires an XPU host), using the `nightly` env and this repo as a
standalone torch-xpu-ops checkout:

```bash
python3 .claude/skills/validation/issue-triage/extract-issue-information/scripts/extract_basic_info.py \
  https://github.com/CuiYifeng/torch-xpu-ops-sandbox/issues/6 \
  | python3 .claude/skills/validation/issue-triage/reproduce-issue/scripts/reproduce_issue.py \
      --conda-env nightly --pytorch-folder /home/daisyden/opencode/skills_refactor
```

For a SKIPPED UT case, follow the two-step skip-removal loop above (invoke
`remove-xpu-skips`, then re-run the single case with `--rerun`).

## Scope

This skill runs tests that are already installed and reports what happens. It
does NOT build or install torch, does NOT fetch wheels, clone, or use the
network. The SCRIPT does NOT edit tests, call git, or call gh. Skip removal is
delegated to the `remove-xpu-skips` skill, which the agent invokes separately.
One issue's cases are handled per invocation.
