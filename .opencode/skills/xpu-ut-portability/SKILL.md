---
name: xpu-ut-portability
description: Classify "Not Run" XPU unit-test cases (from a PVC/Intel-GPU ut_regression CSV) as Portable, Feature gap, or Not Applicable, deciding per case whether QA can enable the test on XPU by editing it and how. Use this skill whenever the user asks to analyze/triage/refresh the XPU UT portability report, judge whether a CUDA test can run or be ported to XPU, classify not-run cases, add or fix the Reason / Detailed reason / Action columns, resolve a specific test's label (e.g. NCCL, cuDNN/FA2, CK/aotriton SDPA, flash-attention, unsupported dtype, RPC, wontfix), or build ut_regression_pvc_cuda_notrun.csv/.xlsx — even if they only say something short like "is this test portable?", "mark these Not Applicable", "re-analyze these files", "why is this a feature gap?", or name a flag like PLATFORM_SUPPORTS_CK_SDPA.
---

# XPU UT Portability Classification

Classifies each "Not Run" XPU test case into one of three Reasons and emits
`Reason`, `Detailed reason`, and `Action` columns. Judgments MUST be grounded in
the actual PyTorch test source, not guessed from the test name.

## Reason enum (exactly three)

- **Portable** — QA can make it run on XPU by editing the test: add
  `allow_xpu=True`, extend `only_for`/`dtypes`, remove `@skipXPU`/`@onlyCUDA`,
  add the file to the XPU CI run set, or port a `torch.cuda.*` call that HAS a
  `torch.xpu.*` equivalent. A plain `TestCase` (not device-generic) already runs
  as-is on XPU and is Portable.
- **Feature gap** — the XPU backend genuinely lacks the dtype/op/feature; it is
  NOT fixable by editing the test (e.g. unsupported dtype, FP8-sparse).
- **Not Applicable** — cannot and should not run on XPU: RPC tests, wontfix
  issues, sheet marked not-target/Not Applicable/Blocked, or a CUDA/ROCm-only
  API with no XPU equivalent.

## Action column

- **Portable that runs as-is -> `Action = rerun`.** A plain `TestCase`, or a
  device-generic class already XPU-instantiated (`allow_xpu=True`) with no
  method-level block, needs no edit; just rerun it on XPU.
- **Portable that needs a test edit -> `Action = TBD`.** Anything requiring a
  code change to enable (add `allow_xpu=True`, extend `only_for`/`dtypes`, fix
  `onlyCUDA`/`dtypesIfCUDA`/`onlyNativeDeviceTypes`, port a `torch.cuda.*` call).
- **Not Applicable / Feature gap -> `Action` blank.**

## Source of truth

- Portability logic comes from **upstream PyTorch only**. Do NOT read
  `third_party/torch-xpu-ops` skip lists. GitHub issues in `intel/torch-xpu-ops`
  (wontfix) ARE allowed as evidence.
- When a tree has diverged, use the authoritative checkout the UT run came from
  (here `~/upstream/ut_result_analysis`) for any file whose content differs from
  the working tree; identical files need no rework.
- **Deep analysis is mandatory.** Never judge from the test name. Use the
  runtime deep-analysis gate (`scripts/audit.py` static pass + `scripts/`
  `verify_runtime.py` runtime pass, AND-gated) rather than `pytest
  --collect-only`. Grep the test file for the method, its decorators, its
  `instantiate_device_type_tests(..., allow_xpu=...)` call, feature-flag gates
  (`PLATFORM_SUPPORTS_*`), and the method body (`torch.cuda.*`,
  `torch.backends.*`, `preferred_rocm_fa_library`, hardcoded `backend="nccl"`).
  Only then assign the label.

## Hard rules (learned, in priority order)

1. **NCCL backend -> Not Applicable.** Any class/file whose name contains `NCCL`
   or that hardcodes `backend="nccl"` / `ProcessGroupNCCL`. NCCL is NVIDIA-only;
   XPU uses XCCL via a different backend. Apply uniformly (no mixed state).
2. **cuDNN / FA2 attention backend -> Not Applicable.** Variants named
   `backend_fa2` or using `SDPBackend.CUDNN_ATTENTION`. cuDNN is NVIDIA-specific
   (`PLATFORM_SUPPORTS_CUDNN_ATTENTION` False on XPU).
3. **ROCm CK / AOTriton SDPA -> Not Applicable.** Variants parametrized
   `sdpa_backend` in `["aotriton","ck"]` (set via `preferred_rocm_fa_library`),
   gated by `PLATFORM_SUPPORTS_CK_SDPA` (False on XPU). ROCm-only, no XPU
   equivalent.
4. **Flash Attention IS supported on XPU (Xe2+).** Flash-gated cases stay
   **Portable** (not a feature gap). A gate like `FLASH or CK_SDPA` is
   satisfiable on XPU via Flash -> Portable. NOTE: a PVC "Max 1550" box reports
   `get_xpu_arch()==Xe` (not Xe2), so the runtime flag reads False on that box
   even though the XPU backend supports Flash; judge by backend capability, not
   the local box flag.
5. **Unsupported dtype -> Feature gap.** CUDA-only legacy API (typed tensors,
   magma, etc.) with no XPU equivalent -> Not Applicable.
6. **Plain `TestCase` runs as-is.** Do NOT suggest converting it to
   `instantiate_device_type_tests`. Reason Portable, Action TBD.
7. **Never advise `add allow_xpu=True` for a class whose XPU variant is already
   generated** (e.g. `TestTorchDeviceType` already has `allow_xpu=True`). For
   already-instantiated classes, only suggest method-level fixes
   (`onlyCUDA`, `dtypesIfCUDA`, `onlyNativeDeviceTypes`).
8. **Verify `rerun` is actually selectable on XPU before trusting it.** A case
   labeled Portable/`rerun` can still be impossible to run on XPU. Confirm with
   the runtime deep-analysis gate (see "Verifying selectability"), NOT with
   `pytest --collect-only` (which gives false negatives for files using torch's
   internal runner, e.g. `test_ops.py`, `test_torch.py`, `opinfo_properties`).
   Outcomes: a whole CUDA-only class (no XPU variant generated at all) ->
   `Action = TBD` (needs an instantiation edit); a missing dtype/parametrization
   variant on an EXISTING XPU class -> `Reason = Feature gap`, `Action` blank.
9. **An OpInfo/`op_db` skip is NOT proof of a feature gap.** For OpInfo-driven
   tests (`test_ops.py`, `test_ops_gradients.py`, etc.) a case skipped by a
   `DecorateInfo(..., device_type="xpu")` or an `OpInfo` gated `"Only runs on
   [cuda, mps]"` / `"test doesn't work on XPU backend"` / bare `Skipped!` must be
   force-run with the gate removed before labeling it. Only label **Feature gap /
   Not Applicable** (keep the OpInfo skip) when the op is genuinely unimplemented
   on XPU. If it runs but fails from a bug -> **Known Issue** and the fix is a
   `DecorateInfo(unittest.expectedFailure, ..., device_type="xpu")`, not a skip.
   If it only misses a numeric tolerance within <10x the CUDA threshold ->
   **Portable** via an XPU `toleranceOverride`. If it runs and passes ->
   **Portable** (stale skip). Always cross-check intel/torch-xpu-ops for an
   existing issue before drafting.


## Verifying selectability (does the XPU variant exist?) — runtime deep analysis

`pytest --collect-only` is UNRELIABLE here: files using torch's internal runner
report zero collected items even when the XPU variants exist. Use the
import-based runtime check instead, AND-gated with static decorator analysis:

1. **Static analysis (`scripts/audit.py`)** — parse the authoritative source with
   `ast`: resolve the CSV class through the `instantiate_device_type_tests(...)`
   map (case-insensitively: CSV `TestFooCUDA` -> source `TestFooCuda` with
   `only_for="cuda"` is CUDA-only, NOT the plain mixin `TestFoo`), read class
   guards (`if HAS_CUDA_AND_TRITON`, `skipIf(not TEST_CUDA)`), and the method's
   decorators. Block set (keep TBD): `@onlyCUDA`, `@skipXPU`, `@skipXPUIf`,
   `@requires_cuda(_and_triton)`, `only_for` excluding xpu, class CUDA guards.
   NON-blocks (still run on XPU): `@onlyNativeDeviceTypes*` (NATIVE_DEVICES
   includes xpu), `@dtypesIfCUDA`-only (xpu falls back to `@dtypes`).
2. **Runtime generation check (`scripts/verify_runtime.py`)** — import the module
   (from `/tmp`, absolute path, with `XPU_SRC_TREE` on `sys.path`; add the
   `functorch` subdir for those tests), get the generated XPU class
   (`TestFooCUDA` -> `TestFooXPU`), and check `hasattr(klass, xpu_name)` where
   `xpu_name = name.replace('_cuda', '_xpu')`.
3. **Combine them** (this is the decision `verify_runtime.decide` makes):
   - No generated XPU class (plain/non-device test, or import failed) -> fall
     back to the static verdict; if static has NO positive evidence
     (`noclass`/`file-missing`) KEEP the current Action (never flip on absence of
     evidence — this protects inductor's dynamically-generated test classes).
   - XPU class exists but the `_xpu` method is ABSENT -> `no-xpu-variant` ->
     `Reason = Feature gap`, `Action` blank (backend/dtype gap).
   - XPU method exists AND static finds no block -> `rerun` (runs as-is).
   - XPU method exists BUT static finds a block (e.g. `@skipXPU`, which `hasattr`
     canNOT see because the method is present-but-skipped) -> stay `TBD`.

Critical: `hasattr` alone over-flips `@skipXPU` cases to rerun, and static alone
over-flips dtype-missing cases; only the AND-gate is correct. TBD->rerun requires
runtime confirmation (never add a case to the run set on static evidence alone).

PVC-box note: a "Max 1550" box reports `get_xpu_arch()==Xe` (not Xe2), so some
runtime flags read False even where the XPU backend supports the feature; judge
flash/backend capability by the backend, not the local box flag.


## Evaluating PLATFORM_SUPPORTS_* flags

Run live on the target XPU box to get ground truth (conda env with torch+xpu).
Known results on PVC Max 1550:

- **False on XPU:** FLASH (box arch Xe, see rule 4), CK_SDPA, CUDNN_ATTENTION,
  FUSED_SDPA, BF16_ATOMICS, GREEN_CONTEXT, WORKQUEUE_CONFIG, FP8_SPARSE,
  FP8_GROUPED_GEMM, MXFP8_GROUPED_GEMM.
- **True on XPU:** MEM_EFF, BF16, FP8, MX_GEMM, HALF_ATOMICS, FUSED_ATTENTION.

## Output format

`ut_regression_pvc_cuda_notrun.csv` (+ `.xlsx`, sheet "Not Run", frozen header)
= 16 original columns + `Reason`, `Detailed reason`, `Action`. Always back up
before bulk edits (`.bak`, `.bak2`, ...). Regenerate the xlsx after every CSV
change.

## Scripts

Deep-analysis pipeline (authoritative; set `XPU_SRC_TREE` to the source checkout
the UT run came from, and `XPU_UT_CSV` to the report CSV):

- `scripts/audit.py` — static `ast` analysis of the source tree: class
  enablement via `instantiate_device_type_tests` kwargs (case-insensitive class
  resolution), class guards, and per-method decorator block detection. Exposes
  `verdict(file, class, name) -> (action, mechanism)`; run directly for a
  CSV-wide static diff report.
- `scripts/verify_runtime.py` — THE authoritative gate. Imports each module and
  AND-gates the runtime generation check (`hasattr` on the generated XPU class)
  with `audit.py`'s static verdict; writes `$XPU_CHANGES_JSON` (default
  `/tmp/universal_changes.json`). Decides rerun vs TBD vs Feature-gap and keeps
  the current Action when there is no positive evidence.
- `scripts/apply_verified.py` — apply only runtime-confirmed changes from the
  changes json: TBD->rerun requires `xpu-variant-runs`; rerun->TBD takes runtime
  `no-xpu-variant` and static CUDA-only guards. Backs up before writing.
- `scripts/reclassify_featuregap.py` — scan the whole CSV and set every
  `no-xpu-variant` row (XPU class exists, dtype/param method absent) to
  `Reason = Feature gap`, `Action` blank.
- `scripts/reclassify.py` — library of rule helpers (NCCL, cuDNN/FA2,
  CK/aotriton, flash, dtype) plus CSV load/save and xlsx regeneration.
- `scripts/eval_flags.py` — evaluate `PLATFORM_SUPPORTS_*` live on the XPU box.
- `scripts/stats.py` — print Reason/Action distributions and per-file/per-class
  breakdowns; the quick way to verify a change.

Deprecated (collect-only; unreliable for internal-runner files — kept only for
inductor `*GpuTests` name confirmation): `scripts/run_collect.py`,
`scripts/check_rerun.py`, `scripts/bucket.py`.

See `reference.md` for the full column spec and the decision flow.
