---
name: xpu-classify-and-draft
description: Decide the XPU portability verdict (Portable / Known Issue / Feature gap / Not Applicable) of PyTorch test cases from real pytest evidence, and draft intel/torch-xpu-ops issues for them. Covers the API/wontfix precheck, force-enabling skipped or not-instantiated tests and re-measuring with skips removed, OpInfo resolutions (keep skip / expectedFailure / toleranceOverride / remove gate), searching and verifying existing issues with gh, the issue draft template, and the evidence rules enforced by verify_evidence.py. Use inside the enable-xpu-tests workflow, or when asked "is this skipped XPU test portable?", "why does this fail on XPU?", "draft an issue for these failures".
---

# Classify XPU cases and draft issues

`T=enable-xpu-tests/tools`. Results land in the report tree via the tools; record
verdicts with `python3 $T/store.py set-case <slug> <case_id> field=value ...`
(fields: `portability reason issue_number issue_url draft_id cuda_only cited_existing`).

## The one rule

**A skip is not a verdict.** SKIPPED, XFAIL or NOT_RUN (not collected on XPU) tells
you nothing about XPU. Every case is decided from a run in which it actually
executed on XPU:

- PASSED at the final patch -> Portable.
- FAILED/ERROR with its skips removed -> Known Issue / Feature gap / Not Applicable,
  each with an issue (existing or drafted).

Only exception: the case already cites an issue that `gh` confirms OPEN or labeled
`wontfix`. Cite it (`cited_existing=true`, reason "cited existing #N"); no
gate-removed run is needed.

## Step A: precheck (does not decide anything)

```
python3 $T/precheck.py --file <test_file> --csv BATCH.csv
```

Per case it reports:

- the `torch.cuda.*` APIs with their catalog category and rewrite;
- `cuda_only_apis` (category C, or B without xpu), which sets `cuda_only=true`;
- the gates to remove and the instantiation fix;
- `wontfix_candidates`, from `references/wontfix_features.md` plus
  `gh search issues --label wontfix`.

Use it as the porting plan and as a hint for classification. Keep
`references/wontfix_features.md` growing: add a row whenever you find or file a
wontfix tracker.

## Step B: force-enable and measure

1. Port the test (`xpu-port-device-agnostic`). Remove every XPU gate on the case,
   and make sure an XPU variant is instantiated. This applies even when the test
   is not selected on XPU today (CUDA-only class, `onlyCUDA`, `requires_cuda`,
   `only_for="cuda"`).
2. Leave category C calls in place. Their XPU error is the evidence.
3. Run:
   ```
   python3 $T/measure.py --file <f> --run gate_removed --cases <ids>
   ```
   It runs `pytest -v -rA --junitxml` and stores per case the status, the skip
   reason, the `E ` lines of the failure, the log path, the patch hash and a
   source snapshot.
4. If a case is still SKIPPED or NOT_RUN, another gate is active (a body
   `skipTest`, a class-level gate, a test-level `@skipXPU` such as `test_out`, a
   missing dtype in `dtypesIfXPU`, an OpInfo `supports_*` flag, a capability flag
   like `PLATFORM_SUPPORTS_FLASH_ATTENTION` evaluated at import time). Find it,
   remove it, and re-run. If a gate cannot be removed for good (a hardware
   capability flag), override it for the run only and mark the line
   `# MEASURE-ONLY`; it must be gone from the final code (verify_evidence R7
   ignores marked lines).
5. Check whether the old reason is still true: a stale CSV snapshot, a test that
   was renamed in the base (`*_cuda` -> `*_gpu`), or a class the base already
   made device-agnostic. If the case passes on the plain base, it is Portable with
   no code change. Do not credit it to the patch in the PR description.

## Step C: classify from the gate-removed result

| result | verdict | final code |
|---|---|---|
| PASSED | Portable **only if the final code runs it** | keep the gate removed and confirm with `--run rework` at the final patch. If the gate must stay in the shipped code (host compiler, CPU-only, CUDA-only), the case is SKIPPED there, so it is **not** Portable: Not Applicable / Known Issue with an issue |
| FAILED on tolerance only, within 10x the CUDA tolerance | Portable | add an XPU `toleranceOverride` / `precisionOverride`, then re-run until PASSED |
| FAILED from a bug (wrong result, crash, unexpected exception) | Known Issue | `expectedFailureXPU` / xfail `DecorateInfo` with msg = issue URL |
| FAILED because XPU lacks the op/backend/feature (`NotImplementedError`, `could not run 'aten::x' with ... XPU`, missing library) | Feature gap | skip with msg = issue URL |
| FAILED from a CUDA-only API, a CPU/x86-only test, or a feature with a wontfix tracker | Not Applicable | category C: restore `onlyCUDA` (issue recorded in cases.json only). Otherwise skip with msg = issue URL |

**OpInfo / op_db tests** (`test_ops.py`, `test_ops_gradients.py`,
`test_torchinductor_opinfo.py`, ...). Gates live in
`torch/testing/_internal/common_methods_invocations.py` and `opinfo/definitions/`.
Remove the xpu `DecorateInfo` / `skipXPU` entry, measure, then resolve the case
one of four ways:

1. **Keep the skip**: the op is unimplemented on XPU (Feature gap / Not Applicable).
2. **`DecorateInfo(unittest.expectedFailure, ..., device_type="xpu")`**: the op runs
   and the case fails from a bug (Known Issue).
3. **`DecorateInfo(toleranceOverride({dtype: tol(atol=, rtol=)}), ..., device_type="xpu")`**:
   the case fails only on tolerance (Portable).
4. **Delete the gate**: the case passes (Portable).

Say which of the four in `reason`.

## Step D: find or draft the issue

Group the non-Portable cases by root cause, using the first `E ` line of each
failure. For each group:

1. Search intel/torch-xpu-ops for the op, API or error text:
   `gh search issues --repo intel/torch-xpu-ops "<op or error>"` (omit `--state` to get open and closed; `gh search` has no `--state all`).
   Check each candidate's state:
   `gh issue view N --repo intel/torch-xpu-ops --json state,labels,title`.
2. If it is **open**, cite it. If it is labeled **wontfix** (open or closed), cite
   it as Not Applicable; never draft a replacement for it.
3. If it is **closed and not wontfix**, it is a stale tracker: draft a new issue.
   On Submit, the new number replaces the stale one on those cases.
4. If there is no match, draft an issue. The draft is a JSON file added with
   `python3 $T/store.py add-draft <slug> draft.json`:

```json
{"placeholder": "XXXX_SHORT_UNIQUE_NAME", "verdict": "Feature gap",
 "title": "[upstream_ut] <op/feature>: <short error> on XPU",
 "labels": ["test: ut"], "cases": ["<case_id>", "..."],
 "body": "<template below>"}
```

Body rules:

- Use the intel/torch-xpu-ops template: `### 🐛 Describe the bug` for a bug, or
  `### 🚀 The feature, motivation and pitch` for a feature gap.
- Include `### Error log / message`, fenced, containing the verbatim `E ` lines
  from the gate-removed run (verify_evidence R9 checks this).
- Include `### Reproduce` with the exact pytest command, taken from the first
  line of the log.
- List the affected tests.
- Include `### Versions` with the nightly version from `setup_ws.py --status`.
- End with the line `_Drafted with AI assistance (Claude via OpenCode); reviewed and submitted by a human._`

Labels:

- Always use `test: ut`.
- Add `wontfix` for a Not Applicable tracker.
- Never use `module: xpu`; that label does not exist in intel/torch-xpu-ops.
- Title a deprecated or permanently unsupported API as `[wontfix] ...`.

In the source, the re-added gate uses
`msg="https://github.com/intel/torch-xpu-ops/issues/XXXX_SHORT_UNIQUE_NAME"`.
When the reviewer clicks Submit, the server files the issue and replaces the
token everywhere: the agent-branch source (amend or fixup commit), the patch,
`cases.json` and `drafts.json`. The reviewer's **Reject** button reverts exactly
the lines carrying that token. Never file issues yourself.

Record each case:

```
python3 $T/store.py set-case <slug> <cid> portability="Feature gap" \
    reason="aten::foo unimplemented on XPU (kept OpInfo skip)"            # with a draft
python3 $T/store.py set-case <slug> <cid> portability="Known Issue" \
    issue_number=1234 issue_url=https://github.com/intel/torch-xpu-ops/issues/1234 \
    reason="expectedFailure on XPU, bug #1234"
```

## Step E: verify

```
python3 $T/verify_evidence.py --file <test_file>      # nonzero exit on any ERROR
```

| rule | checks |
|---|---|
| R1 | Portable: the latest run of the final code (initial/rework at the current patch, never a gate_removed run) is PASSED. SKIPPED is never Portable |
| R2 | an issue or draft exists |
| R3 | the cited issue is not stale |
| R4 | Not Applicable cites an open/wontfix issue |
| R5 | a gate-removed FAILED result exists |
| R6 | the log contains the case and its error |
| R7 | only gate lines changed after measuring |
| R8 | the final gate msg is the issue URL / placeholder, or `onlyCUDA` is kept for cuda_only |
| R9 | the draft contains the error and the repro |
| R10 | reason is non-empty |

Fix every ERROR by re-measuring or reclassifying. Never by editing logs or
results.
