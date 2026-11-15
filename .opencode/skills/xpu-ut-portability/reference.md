# XPU UT Portability — reference

## CSV / xlsx schema

`ut_regression_pvc_cuda_notrun.csv` = the "Not Run" subset of
`ut_regression_pvc_cuda.csv` (PVC / Intel Max 1550), with 16 original columns
plus three appended:

| Column | Meaning |
|---|---|
| `Reason` | `Portable` \| `Feature gap` \| `Not Applicable` |
| `Detailed reason` | Specific, source-grounded justification + how to port (no vague wording) |
| `Action` | `rerun` (Portable, runs as-is) \| `TBD` (Portable, needs a test edit) \| blank (Not Applicable / Feature gap) |

Key original columns used by the scripts: `CUDA Test File`, `CUDA Test Class`,
`CUDA Test Name`.

The `.xlsx` mirrors the CSV on a sheet named `Not Run` with a frozen header row.
Regenerate it after every CSV change.

## Decision flow (per case)

1. Is it RPC / wontfix / sheet not-target/Blocked? -> **Not Applicable**.
2. NCCL backend (class/file name has NCCL, or `backend="nccl"`)? -> **Not Applicable** (rule 1).
3. cuDNN / FA2 attention (`backend_fa2`, `SDPBackend.CUDNN_ATTENTION`)? -> **Not Applicable** (rule 2).
4. ROCm CK/AOTriton SDPA (`sdpa_backend` in aotriton/ck, `preferred_rocm_fa_library`, `PLATFORM_SUPPORTS_CK_SDPA`)? -> **Not Applicable** (rule 3).
5. CUDA-only legacy API (typed tensors, magma, `torch.cuda.*` with no `torch.xpu.*`)? -> **Not Applicable**.
6. Unsupported dtype / FP8-sparse feature the XPU backend lacks? -> **Feature gap**.
7. Flash-attention gated (Flash supported on XPU Xe2+)? -> **Portable**, Action TBD.
8. Plain `TestCase`? -> **Portable** (runs as-is; do not convert), Action TBD.
9. Device-generic class already instantiated for XPU (`allow_xpu=True` present)?
   Only method-level blocks (`onlyCUDA`, `dtypesIfCUDA`, `onlyNativeDeviceTypes`)
   remain -> **Portable** with the specific method-level fix. Never re-suggest
   `allow_xpu=True`.
10. Device-generic class NOT yet instantiated for XPU -> **Portable**, "add
    `allow_xpu=True`" / "add xpu to `only_for`".

## Deep analysis checklist (when the name is ambiguous)

Grep the test file (use the authoritative checkout for diverged files) for:

- the `instantiate_device_type_tests(..., allow_xpu=..., only_for=...)` call;
- the method's decorators (`@onlyCUDA`, `@skipXPU`, `@dtypesIfCUDA`,
  `@skipIfXpu`, `@parametrize("sdpa_backend"/"backend", ...)`);
- feature gates referencing `PLATFORM_SUPPORTS_*`;
- the method body for `torch.cuda.*`, `torch.backends.*`,
  `preferred_rocm_fa_library`, hardcoded `backend="nccl"`.

Only assign the label after confirming the gate in source.

## Known PLATFORM_SUPPORTS_* on PVC Max 1550

False on XPU: FLASH (box arch Xe; backend still supports it — see SKILL rule 4),
CK_SDPA, CUDNN_ATTENTION, FUSED_SDPA, BF16_ATOMICS, GREEN_CONTEXT,
WORKQUEUE_CONFIG, FP8_SPARSE, FP8_GROUPED_GEMM, MXFP8_GROUPED_GEMM.

True on XPU: MEM_EFF, BF16, FP8, MX_GEMM, HALF_ATOMICS, FUSED_ATTENTION.

Re-verify with `scripts/eval_flags.py` on the actual box before relying on these.
