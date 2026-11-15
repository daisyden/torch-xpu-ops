---
name: xpu-ut-skipped
description: Build ut_regression_pvc_cuda_skipped.xlsx from the passed-to-skipped cases in ut_regression_pvc_cuda.csv and explain WHY each XPU test is skipped in a Detailed Reason column, pulling the reason from the in-source skip decorator (its msg/comment and any issue number), the row's existing Issue column enriched with the issue title/state from intel/torch-xpu-ops, or a matching open intel/torch-xpu-ops issue. Use this skill whenever the user asks to collect/triage skipped XPU UT cases, find out why a test is skipped on XPU, copy passed-to-skipped cases into an xlsx, add or fill a Detailed Reason column, attach issue numbers to skipped tests, or cross-reference skips against torch-xpu-ops issues — even if they only say something short like "why is this skipped?", "build the skipped report", "which skips have issues?", or "add skip reasons".
---

# XPU UT Skipped-Case Report

Extracts every `passed -> skipped` case from `ut_regression_pvc_cuda.csv`, copies
it to `ut_regression_pvc_cuda_skipped.xlsx`, and fills a **Detailed Reason**
column explaining why the test is skipped on XPU. All reasons are grounded in
actual source / tracked issues — never guessed.

## Input / output

- Input: `ut_regression_pvc_cuda.csv` (16 cols). The relevant rows have
  `Status == "passed -> skipped"` (Baseline `passed`, Target `skipped`).
- Output: `ut_regression_pvc_cuda_skipped.xlsx`, sheet "passed to skipped",
  frozen header = the 16 original columns + **Detailed Reason** + **Reason**.

## Reason column (portability + issue status)

Added by `scripts/add_reason.py` on top of Detailed Reason:

- **Not Applicable** -- the test is fundamentally CUDA/NVIDIA/ROCm-specific and
  cannot run on XPU: NCCL class/file, `backend_fa2` (cuDNN) or
  `sdpa_backend=aotriton/ck` (ROCm CK) variants, or a skip whose reason text says
  CUDA-only / requires CUDA / not supported on this system / cuDNN / ROCm / sm_ /
  magma. An `op_db` skip with `device_type=xpu` is a real XPU bug, so it stays
  portable (NOT NA). Also NA: any test whose name matches an intel/torch-xpu-ops
  **wontfix** issue (open OR closed) -- `load_wontfix` pulls `--label wontfix
  --state all`, tokenizes the titles for `test_*` names, and `wontfix_hit`
  matches a row by exact / prefix / base-name; the issue number is written into
  the row's Issue column.
- **Known issue** -- portable AND skipped by a tracked intel/torch-xpu-ops issue
  that is still OPEN (or whose state could not be fetched).
- **TBD** -- portable AND either no tracked issue, or the tracked issue is
  already closed/fixed (a stale skip that is a candidate to re-enable).

### Refined cat4 (reason-text names cuda but test runs on BMG)

The test machine is **BMG**, where `PLATFORM_SUPPORTS_FLASH_ATTENTION_XPU` is
True. A skip whose text mentions cuda is NOT automatically NA -- inspect the
actual decorator in the authoritative source tree:

- **TBD (portable, keep as candidate)** when the only gate is GPU-generic and
  BMG satisfies it: `@skipUnless(HAS_GPU)` + `device=GPU_TYPE`,
  `requires_cuda_and_triton` on a device-generic test, a pure
  `PLATFORM_SUPPORTS_FLASH_ATTENTION_XPU` flag gate, or an already-`_xpu`-named
  device-generic test. `tbd_gpu_generic` encodes the verified clusters
  (`test_ctx_manager::test_autocast*`, `inductor/test_indexing`
  masked_load/combo_reduction/explicit_dense/reduction_invariant,
  `test_activation_checkpointing::*_xpu`).
- **NA** when the test is genuinely CUDA-bound: `@skipIfXpu` citing a CUDA-only
  kernel (e.g. flash NestedTensor), a CUDA platform flag with no XPU equivalent
  (`PLATFORM_SUPPORTS_MEM_EFF_ATTENTION`), `torch.backends.cuda.is_built()` /
  `RUN_CUDA` / `cuda._is_compiled()`, hardcoded `cuda:0` device strings, inline
  PTX asm, or a CUDA-specific test class (`CudaReproTests`). Default long-tail
  singles to NA (conservative: never add to the run-set on static evidence
  alone).

Issue states come from the `gh` cache (`/tmp/opencode/xpu_ops_issue_cache.json`),
topped up for any issue referenced only inside Detailed Reason.

### Runtime portability probe for OpInfo TBD rows (`scripts/portability_probe.py`)

Reason text alone is not proof of portability. For OpInfo-based files
(`test_ops`, `test_binary_ufuncs`, `test_unary_ufuncs`, `test_meta`,
`test_decomp`, `functorch/test_ops`) every TBD row is confirmed by actually
running the op on XPU:

- Map the mangled test name to its `OpInfo` (longest `formatted_name` substring)
  and trailing dtype token, then execute one `op.sample_inputs("xpu", dtype)`.
- **Portable -> TBD** if the op runs. Per the user's rule *runtime wins*: a
  transparent ATen `XPU->CPU` fallback still counts as portable (the test would
  execute and pass), so ops without a native XPU kernel but with a fallback stay
  TBD. The yaml/dispatch check (`torch._C._dispatch_has_kernel_for_dispatch_key(
  "aten::X", "XPU")`) is informational only -- it is stricter than runtime.
- **Not Applicable** only on a hard unsupported signal, written verbatim into
  Detailed Reason as `[XPU portability probe: <reason>]`:
  - `jiterator` ops -> "jiterator is CUDA/ROCm-only" (issue #2918).
  - `Could not run 'aten::X' ... 'XPU' backend` with no fallback (e.g.
    `aten::_efficient_attention_forward`) -> "no XPU kernel for aten::X".
  - `"op_xpu" not implemented for '<dtype>'` (e.g. igamma, issue #2452) ->
    the dtype is genuinely unsupported on XPU for that op.
- Ambiguous/test-specific errors (bad sample build, unrelated runtime error)
  keep TBD -- never demote to NA without a clear unsupported signal.

Non-OpInfo TBD rows (transformers, convolution, inductor, dynamo, fx, ...) are
feature/infra tests that do not map to a single op; they are left at the
decorator-level verdict and still warrant per-feature runtime verification.

## How Detailed Reason is resolved (priority order)

1. **In-source skip decorator.** Locate the test method in `test/<file>.py`
   (the XPU Test File maps `test.test_ops` -> `test/test_ops.py`) using
   **longest-prefix** matching of the def name against the mangled test name
   (OpInfo/parametrized names are `<def>_<params>_<device>_<dtype>`). Read the
   decorator region above the def (bracket-aware, so multi-line decorators like
   `@unittest.skipIf(cond, "msg")` are captured) and report the decorator name
   (`@skipXPU`, `@skipXPUIf`, `@skipIfXpu`, `@unittest.skip`, ...), any quoted
   msg, any issue number it references, and, only if neither exists, a trimmed
   comment. When several skip decorators stack, the one carrying an issue ref (or
   the XPU-specific one) is chosen. A bare `@skipXPU` = "unconditional XPU skip".
   Example: `test_fake_export_..._scaled_dot_product_attention` ->
   `In-source @skipXPUIf (issue #5157)`.
2. **op_db DecorateInfo skip/xfail.** OpInfo tests (`test_ops.py`,
   `test_export_opinfo.py`, `test_meta.py`, `test_unary_ufuncs.py`, ...) are
   frequently skipped by a `DecorateInfo` attached to the op in `op_db`
   (`op.skips` / `op.decorators`) with `device_type` in `{None, "xpu"}`, not by a
   method decorator. `dump_opdb_skips.py` introspects `op_db` at runtime; the
   report matches a test to its op (longest op-name token in the mangled name)
   and the DecorateInfo's `test_name`/`cls_name`, reporting
   `op_db DecorateInfo skip|xfail for op '<op>' (device_type=...)`.
3. **Curated Issue column**, enriched with the issue title/state from
   `intel/torch-xpu-ops` via `gh` (cached):
   `Skipped per intel/torch-xpu-ops #NNNN (open): <title>. <url>`
4. **Open-issue match.** Match the full test name (whole token, len >= 12)
   against OPEN `intel/torch-xpu-ops` issue titles.
5. **Fallback.** `Skipped on XPU; no explicit reason found in source, op_db, or a
   tracked intel/torch-xpu-ops issue. Needs manual triage.`

Whichever tier fires, a curated issue number from the Issue column is always
appended if not already present: `[tracked intel/torch-xpu-ops #NNNN <url>]`.

## Key rules

- Reasons must come from real evidence: the in-source decorator, the tracked
  issue (with its real title/state from `gh`), or an open issue whose title names
  the exact test. Do NOT invent reasons.
- OpInfo tests in `test_ops.py` are overwhelmingly skipped by a method-level
  `@skipXPU` (e.g. `test_out`, `test_cow_input`) or a conditional skip
  (`test_python_ref_executor`'s "Takes too long for inductor") — the longest-
  prefix matcher finds these correctly.
- Only claim an in-source decorator when a skip decorator truly sits above the
  matched def; otherwise fall through (many XPU skips come from an external
  torch-xpu-ops skip list that is NOT in this checkout).
- Always include the issue number/URL when one is available anywhere (Issue
  column, decorator msg, or matched open issue).

## Scripts

- `scripts/fetch_open_issues.py` — dump OPEN intel/torch-xpu-ops issues to JSON
  (`gh issue list`), used for name matching. Run first.
- `scripts/dump_opdb_skips.py` — introspect `op_db` at runtime (needs the torch
  XPU env) and dump every skip/xfail `DecorateInfo` with `device_type` in
  `{None, "xpu"}` to JSON. Run once per torch build.
- `scripts/skipped_report.py` — build the xlsx. Flags: `--csv`, `--out`,
  `--repo-root` (where `test/` lives), `--open-issues <json>`,
  `--opdb-skips <json>`, `--no-gh` (skip issue-title lookups). Issue titles are
  cached in `/tmp/opencode/xpu_ops_issue_cache.json`.
- `scripts/add_reason.py` — append the **Reason** column (Not Applicable / Known
  issue / TBD) to the xlsx, checking portability and related-issue state. Run
  after `skipped_report.py`. Flags: `--xlsx`, `--no-gh`.

## Typical run

```
# 1. open issues (for name matching)
python scripts/fetch_open_issues.py --out /tmp/opencode/xpu_ops_open_issues.json
# 2. op_db skips (inside the torch XPU env)
source ~/miniforge3/etc/profile.d/conda.sh && conda activate any_gate
python scripts/dump_opdb_skips.py --out /tmp/opencode/opdb_xpu_skips.json
# 3. build the report
python scripts/skipped_report.py \
    --csv ut_regression_pvc_cuda.csv \
    --out ut_regression_pvc_cuda_skipped.xlsx \
    --repo-root . \
    --open-issues /tmp/opencode/xpu_ops_open_issues.json \
    --opdb-skips /tmp/opencode/opdb_xpu_skips.json
# 4. add the Reason column (portability + issue status)
python scripts/add_reason.py --xlsx ut_regression_pvc_cuda_skipped.xlsx
```

The script prints a breakdown of how many reasons came from each source
(`issue`, `source`, `openmatch`, `none`) — use it to gauge how many cases still
need manual triage.
