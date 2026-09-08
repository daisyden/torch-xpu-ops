---
name: xpu-test-decorator-parity
description: "Checklist of the parity rules for porting PyTorch CUDA tests to XPU (Intel GPU). Use this whenever reviewing or writing an XPU port of a CUDA test, checking that CUDA-specific decorators were correctly mirrored for XPU, or auditing a test file for missing dtypesIfXPU / skipXPUIf / largeTensorTest xpu / expectedFailureXPU / onlyXPU counterparts. Trigger on requests like 'port this cuda test to xpu', 'check xpu decorator parity', 'did develop-xpu-test mirror the decorators correctly', 'review this xpu test port', or any mention of CUDA-to-XPU test decorator mirroring, even if the word parity is not used."
---

# CUDA -> XPU Test Decorator Parity

When a CUDA test is ported to XPU (Intel GPU), device-conditional decorators
that were applied for CUDA usually need an XPU counterpart, otherwise the ported
test runs with the wrong dtype set, tolerance, or memory/skip gating on XPU. The
tool `develop-xpu-test` mirrors many of these automatically; this skill is the
checklist to confirm the mirror is complete and scoped correctly.

Decorators live in `torch/testing/_internal/common_device_type.py`. The core
idea for every rule below is the same: a decorator whose behavior is keyed to
`device_type="cuda"` only affects the CUDA instantiation of the test, so XPU
needs its own decorator keyed to `"xpu"` (or an intentional, documented decision
not to mirror it).

## How to review a ported file

For each test method (and the test class), scan for CUDA-conditional decorators
and confirm the XPU counterpart exists with the **same scope** (same dtypes,
same size, same version/condition). "Same scope" matters as much as "present" —
a mirrored decorator with a narrower or wider argument set is still a bug.

Report each finding as:

- **Rule** — which parity rule below applies.
- **Location** — `file:line` + test name.
- **CUDA decorator found** — what the CUDA test uses.
- **XPU counterpart expected** — what the port should have.
- **Status** — present / missing / mismatched.
- **Severity** — see the scale below.

### Severity scale

| Severity | Meaning | Typical action |
| --- | --- | --- |
| **Blocker** | XPU gets wrong or zero test execution (test never instantiates, silently runs the wrong dtype/skip gating, or risks an unguarded OOM/crash). | Must fix before merge. |
| **Major** | Coverage or scope is silently wrong (mirrored but mismatched dtypes/size/condition, or a needed skip/xfail is missing). | Fix before merge. |
| **Minor** | Tuning-level issue (tolerance slightly off, over-loose inherited override). | Fix or justify. |
| **Info** | No direct action required; guidance or a decorator with no XPU equivalent. | Note and move on. |

## The parity rules

### 1. Large-tensor memory gating — Blocker
- CUDA: `@largeTensorTest("20GB", "cuda")` (or `device="cuda"`, or bare on a
  device-generic test where the primary device is CUDA).
- XPU: must have `@largeTensorTest("20GB", "xpu")` with the **same size string**.
- Why: `largeTensorTest` checks free memory on the named device and skips if
  insufficient. Without the XPU copy, the XPU run either skips-checks the wrong
  device or runs an OOM-prone test unguarded. Confirm size scope matches exactly
  ("20GB" -> "20GB", not "16GB").

### 2. dtype overrides — Major
- CUDA: `@dtypesIfCUDA(...)` (a.k.a. the old spelling `@dtypeIfCuda`).
- XPU: must have `@dtypesIfXPU(...)` with the **same dtype set**.
- Why: `dtypesIfCUDA` overrides the base `@dtypes(...)` only for CUDA. If XPU is
  not given its own override, the XPU test silently runs the base `@dtypes` set,
  which may be narrower or wider than intended. If XPU genuinely does not support
  a dtype in the CUDA set (e.g. certain complex/bfloat16 paths), narrow the XPU
  set **on purpose** and note why — don't blindly copy.

### 3. Device-only restriction — Blocker
- CUDA: `@onlyCUDA`.
- XPU: decide intent. `@onlyCUDA` means "this test only makes sense on CUDA."
  When porting, either add a parallel `@onlyXPU` test or broaden to run on both.
  Do NOT leave `@onlyCUDA` on a test that is supposed to also cover XPU — it will
  never instantiate for XPU.

### 4. Conditional skips — Major
- CUDA: `@skipCUDAIf(cond, msg)` and the whole family
  (`skipCUDAIfRocm`, `skipCUDAIfNoMagma`, `skipCUDAIfNoCusolver`,
  `skipCUDAIfNoMagmaAndNoCusolver`, `skipCUDAIfCudnnVersionLessThan`,
  `skipCUDAIfNoCudnn`, `skipCUDAIfVersionLessThan`, etc.).
- XPU: map to `@skipXPUIf(cond, msg)` (or `@skipXPU` for an unconditional skip)
  **only if the underlying limitation also applies to XPU**. Feature-gated CUDA
  skips (Magma, cuSOLVER, cuDNN, MIOpen, ROCm/HIP versions) are CUDA-stack
  specific — they usually have no XPU meaning, so the correct action is often to
  drop them for XPU or replace them with the matching XPU capability check.
  The parity question is: "does the XPU port need a skip for the same underlying
  reason?" If yes, mirror it; if the reason can't occur on XPU, don't.

### 5. Expected failures — Major
- CUDA: `@expectedFailure("cuda")` / device-scoped expected failure.
- XPU: if the same test is known-broken on XPU, add `@expectedFailureXPU`. This
  is not automatic — it depends on whether the failure reproduces on XPU. Verify
  before mirroring; a spurious `expectedFailureXPU` hides real regressions.

### 6. Precision / tolerance overrides — Minor
- CUDA: `@precisionOverride({...})` or `@toleranceOverride({...})` applied in a
  CUDA context, or `@dtypesIfCUDA` paired with a tolerance tuned for CUDA.
- XPU: if CUDA needed a looser tolerance for numerical reasons, XPU commonly
  needs its own tuned tolerance too. Confirm the XPU run isn't inheriting a
  CPU-strict tolerance that its hardware can't meet (or an over-loose CUDA one).
  `precisionOverride`/`toleranceOverride` are not device-keyed, so this is a
  judgment check, not a mechanical mirror.

### 7. TF32 / numeric-mode decorators — Info
- CUDA: `@tf32_on_and_off`, `@with_tf32_off`, and similar TF32 controls.
- XPU: TF32 is a CUDA (Ampere+) concept. There is no direct XPU equivalent, so
  do not mechanically mirror these. Instead confirm the XPU port runs in a
  well-defined numeric mode and, if TF32-driven tolerance loosening was the point
  of the decorator, that XPU has appropriate tolerance handling (see rule 6).

### 8. Stacked decorators — Info
- When a method carries several CUDA-conditional decorators at once (e.g.
  `@dtypesIfCUDA` + `@precisionOverride` + `@skipCUDAIf`), each one is an
  independent parity obligation. Check every decorator in the stack, not just the
  first — a common miss is mirroring the dtype override but forgetting the skip.

### 9. Test-class instantiation for XPU — Blocker
- The mirrored decorators only run if the test class is actually instantiated for
  XPU. Confirm `instantiate_device_type_tests(...)` includes XPU — via
  `allow_xpu=True`, or `only_for`/`except_for` settings that don't exclude
  `"xpu"`. If XPU isn't in the instantiated device list, none of rules 1-8 take
  effect no matter how carefully the decorators were mirrored.

## Quick reference: CUDA -> XPU decorator map

| CUDA decorator | XPU counterpart | Mirror? | Severity |
| --- | --- | --- | --- |
| `largeTensorTest(size, "cuda")` | `largeTensorTest(size, "xpu")` | Yes, same size | Blocker |
| `dtypesIfCUDA(...)` | `dtypesIfXPU(...)` | Yes, same dtypes (narrow only if unsupported) | Major |
| `onlyCUDA` | `onlyXPU` or broaden | Intentional decision | Blocker |
| `skipCUDAIf(...)` | `skipXPUIf(...)` / `skipXPU` | Only if reason applies to XPU | Major |
| `skipCUDAIfNoMagma`/`NoCusolver`/`NoCudnn`/ROCm/MIOpen | (usually none) | CUDA-stack specific; drop or replace | Major |
| `expectedFailure("cuda")` | `expectedFailureXPU` | Only if it also fails on XPU | Major |
| `precisionOverride`/`toleranceOverride` | same, re-tuned | Judgment, not mechanical | Minor |
| `tf32_on_and_off`/`with_tf32_off` | (no direct equiv) | Don't mirror; check tolerances | Info |
| `instantiate_device_type_tests(...)` | `allow_xpu=True` | Required for any of the above to run | Blocker |

## Guiding principle

Parity is not "copy every CUDA decorator to XPU." It's "for each CUDA-conditional
behavior, decide whether the same behavior is needed on XPU, and if so express it
with the same scope." Mechanical mirrors (rules 1, 2, 9) should match exactly;
judgment mirrors (rules 3-8) require confirming the underlying reason reproduces
on XPU before copying, and documenting when it deliberately doesn't.
