---
name: develop-xpu-test
description: Enable Intel XPU coverage for an already device-generic PyTorch test file. Use after a test has been refactored to be accelerator-agnostic and you now need to add XPU to its device instantiation and register XPU xfails/skips in op_db. Gates on review-test-refactoring first (hard stop if the refactor is not clean), then extends instantiate_device_type_tests to include "xpu"/adds a HAS_GPU guard, mirrors largeTensorTest and dtype decorators for XPU, and updates DecorateInfo device_type entries in common_methods_invocations.py to include xpu. Does NOT add new XPU skip decorators or touch existing XPU skips/decorators. After making the edits, hand off to the verify-xpu-test skill for local XPU verification.
---

# Develop XPU Test

Enable Intel XPU backend coverage on a PyTorch test that has *already* been made
device-generic (accelerator-agnostic). This skill does three, and only three,
kinds of edits:

1. **Instantiation enablement** — add `"xpu"` to the device instantiation of the
   test class (or add a `HAS_GPU` guard for surfaces that don't use
   `instantiate_device_type_tests`).
2. **Decorator parity enablement** — when CUDA-specific large-tensor or dtype
   decorators exist, add the matching XPU decorator with the same scope.
3. **op_db xfail/skip enablement** — extend `DecorateInfo(device_type=...)`
   entries in `torch/testing/_internal/common_methods_invocations.py` so the
   CUDA-registered expected-failures/skips also apply on XPU.

It is intentionally narrow. It **never** adds new XPU skip decorators and
**never** touches existing XPU skips/decorators (see Constraints).

Local test and verification are **out of scope** for this skill — after the
edits are made, run the `verify-xpu-test` skill.

## When to Use

- A test class is already accelerator-agnostic (uses the `device` parameter, is
  instantiated via `instantiate_device_type_tests`, uses `@onlyAccelerator`
  rather than `@onlyCUDA`, etc.) and you want to turn on XPU for it.
- You are following up the `test_ops.py` / `op_db` XPU enablement pattern
  (extend `device_type='cuda'` entries to `device_type=('cuda', 'xpu')`).

Do **not** use this skill to generalize a CUDA-only test (rename classes,
replace `torch.cuda.*` with `device_mod`, swap `@onlyCUDA` → `@onlyAccelerator`).
That generalization must already be done and must pass review — this skill
assumes it and gates on it in Step 1.

## Tools Used

- **Read / grep / glob**: inspect the test file and `common_methods_invocations.py`.
- **edit**: make the instantiation and op_db changes.
- **task (subagent)**: run the `review-test-refactoring` gate.

## Workflow

### Step 1: Review Gate (HARD STOP)

Before making any edit, verify the target test file passes
`review-test-refactoring`. Dispatch it as a subagent against the test file (or
the diff/branch, if that is what the user provided):

```python
task(
    subagent_type="explore",
    load_skills=["review-test-refactoring"],
    description="Review gate for XPU enablement of <test_file>",
    prompt=(
        "Review <ABSOLUTE_PATH_TO_TEST_FILE> against the decoupling standards. "
        "Report Blockers / Major / Minor findings. Return whether the file is "
        "clean enough to enable XPU on (i.e. zero Blockers)."
    ),
)
```

**Decision:**

- **PASS** = the review returns **zero Blockers**. (Majors/Minors are surfaced
  to the user but do not by themselves block XPU enablement — call them out in
  the final report.)
- **FAIL** = the review returns one or more **Blockers**.

**On FAIL — HARD STOP:**

1. Make **zero** edits to the test file or to `op_db` /
   `common_methods_invocations.py`.
2. Report the review's Blocker (and Major) findings verbatim to the user.
3. State clearly that XPU enablement is halted until the refactoring blockers
   are fixed (that fixing is out of scope for this skill).
4. End. Do not proceed to Step 2.

Only when the gate PASSES do you continue.

### Step 2: Enable the Test Instantiation

Pick the pattern that matches how the class exposes its device axis.

#### 2.1 Pattern A — `instantiate_device_type_tests` (preferred)

Extend the instantiation to include `"xpu"` and set `allow_xpu=True`:

```python
instantiate_device_type_tests(
    TestSDPAGpuOnly, globals(), only_for=("cuda", "xpu"), allow_xpu=True
)
```

Notes:
- If the call currently reads `only_for=("cuda",)`, change it to
  `only_for=("cuda", "xpu")` and add `allow_xpu=True`.
- If the call has no `only_for=` (runs on all device types) but is missing
  `allow_xpu=True`, add `allow_xpu=True`.
- Keep any existing `except_for=` / `allow_mps=` arguments intact.

#### 2.2 Pattern B — `HAS_GPU` guard (when `torch.accelerator` isn't suitable)

For surfaces that gate on raw availability rather than parametrized device
types, define a `HAS_GPU` flag that is true for either CUDA or XPU, and use it
in the skip guard:

```python
HAS_GPU = torch.cuda.is_available() or torch.xpu.is_available()

@unittest.skipIf(not HAS_GPU, "CUDA or XPU is unavailable")
```

Replace the existing CUDA-only availability guard (e.g.
`@unittest.skipIf(not torch.cuda.is_available(), "CUDA is unavailable")`) with
the `HAS_GPU` form above.

Use exactly one of Pattern A or Pattern B, matching the file's existing style.

### Step 3: Add XPU Decorator Parity in the Test File

Apply the following mechanical decorator parity rules in the enabled test file.

#### 3.1 `largeTensorTest` parity

If the file has a CUDA large-tensor decorator:

```python
@largeTensorTest("20GB", "cuda")
```

add the matching XPU decorator with the same size:

```python
@largeTensorTest("20GB", "xpu")
```

General rule: preserve the size string exactly and mirror the CUDA form for XPU.

#### 3.2 dtype decorator parity

If there is a CUDA dtype decorator (for example `@dtypeIfCuda(...)` or
`@dtypesIfCUDA(...)`), add the XPU counterpart with the same dtype scope:

```python
# BEFORE
@dtypeIfCuda(torch.float32, torch.float64)

# AFTER
@dtypeIfCuda(torch.float32, torch.float64)
@dtypesIfXpu(torch.float32, torch.float64)
```

Keep argument scope identical (same dtype list/expressions).

Import requirement for this rule:

```python
from torch.testing._internal.common_device_type import dtypeIfXpu
```

Use the existing file style for ordering/placement; only add the missing XPU
decorator/import needed for parity.

### Step 4: Update `op_db` DecorateInfo Entries

In `torch/testing/_internal/common_methods_invocations.py`, extend the CUDA
`DecorateInfo` xfail/skip/tolerance entries so they also apply on XPU. There are
two mechanical transforms, both taken from the `test_ops.py` XPU-enablement
pattern:

**Transform 1 — widen a single `'cuda'` entry to the tuple form:**

```python
# BEFORE
DecorateInfo(unittest.expectedFailure, 'TestCommon', 'test_out', device_type='cuda'),

# AFTER
DecorateInfo(unittest.expectedFailure, 'TestCommon', 'test_out', device_type=('cuda', 'xpu')),
```

This applies to every `DecorateInfo` kind — `unittest.skip("...")`,
`unittest.expectedFailure`, and `toleranceOverride({...})` — and preserves all
other arguments (`active_if=`, `dtypes=`, the test class/name strings).

```python
# tolerance example
DecorateInfo(
    toleranceOverride({torch.float32: tol(atol=1e-05, rtol=1.2e-03)}),
    'TestCommon', 'test_variant_consistency_eager', device_type=('cuda', 'xpu')),

# active_if example (preserve active_if)
DecorateInfo(unittest.skip("Skipped!"), 'TestCommon', 'test_dtypes',
             device_type=('cuda', 'xpu'), active_if=not SM53OrLater),
```

**Transform 2 — collapse a duplicated cuda + xpu pair into one tuple entry:**

If the op already has a separate `device_type='cuda'` line and a
`device_type='xpu'` line for the *same* `(test_class, test_name, dtypes,
active_if)`, merge them into a single `device_type=('cuda', 'xpu')` entry and
delete the redundant one:

```python
# BEFORE (two lines)
DecorateInfo(unittest.expectedFailure, 'TestCommon', 'test_out', device_type='cuda'),
DecorateInfo(unittest.expectedFailure, 'TestCommon', 'test_out', device_type='xpu'),

# AFTER (one line)
DecorateInfo(unittest.expectedFailure, 'TestCommon', 'test_out', device_type=('cuda', 'xpu')),
```

**Leave untouched:**
- `device_type='mps'` and `device_type='cpu'` entries — never fold these into
  the cuda/xpu tuple.
- Entries that already read `device_type=('cuda', 'xpu')`.

Formatting: when adding `('cuda', 'xpu')` pushes a line past the linter width,
wrap the `DecorateInfo(...)` arguments onto a continuation line (indent the
continuation to align with the first argument), matching the surrounding style.

## Constraints

1. **Review gate is a hard stop.** If `review-test-refactoring` returns any
   Blocker, make zero edits and report. Never enable XPU on a file that fails
   the refactoring review.
2. **Do NOT add new XPU skip decorators.** This skill does not create
   `@skipIfXpu`, `@skipXPU`, `subtest(..., decorators=[skipIfXpu(...)])`, or
   in-body `self.skipTest("xpu", ...)`. Its job is to *enable*, not to skip.
3. **Do NOT touch existing XPU skips or decorators.** Any `@skipIfXpu`,
   `skipXPU`, `@skipXPUIf`, existing `device_type='xpu'` entries that are NOT
   part of a cuda+xpu merge (Transform 2), or any other pre-existing XPU-related
   decorator/skip must be left exactly as-is.
4. **Only enable, mechanically.** Apply Transform 1 / Transform 2 to the op_db
   entries; do not editorialize about which CUDA entries "should" apply to XPU
   beyond the mechanical widen/merge. (No per-entry cherry-picking heuristic.)
5. **Never touch `mps` / `cpu` DecorateInfo entries.**
6. **Local verification is out of scope.** Do not run pytest here — verify via
   the `verify-xpu-test` skill.
7. **No commits unless the user asks.** Present a summary of edits and the
   `verify-xpu-test` result; commit only on explicit request.
8. **ASCII only** in any new code/comments; match existing file style.
9. **Decorator parity is mechanical.** For `largeTensorTest` and
   `dtypeIfCuda`/`dtypesIfCUDA`, mirror to XPU with the same scope; do not alter
   dtype sets or tensor-size thresholds while adding parity.

## Summary of Edits Produced

- `test/<file>.py`: instantiation extended to include XPU (Pattern A) or a
  `HAS_GPU` guard added (Pattern B), plus decorator parity updates for
  `largeTensorTest` and CUDA dtype decorators mirrored to XPU.
- `torch/testing/_internal/common_methods_invocations.py`: `DecorateInfo`
  `device_type='cuda'` entries widened to `('cuda', 'xpu')` and duplicate
  cuda+xpu pairs collapsed — with no new XPU skip decorators and no changes to
  existing XPU skips/decorators.

## See Also

- `verify-xpu-test` — local XPU verification of the enablement edits made here.
- `submit-xpu-test-pr` — packages the verified edits into a confirm-gated draft PR.
- `review-test-refactoring` — the Step 1 gate that must pass before enablement.
