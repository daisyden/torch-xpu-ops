---
name: issue-category
description: Assign the canonical Category (one of 11 buckets, with Torch Ops subcategory when applicable) for a triaged GitHub issue (pytorch or torch-xpu-ops), given the JSON output of issue-target-component (the traced root-cause + routing verdict). Applies the Category Analysis decision-priority order and Torch Operations subcategory rules (gemm/eltwise/reduction/others) with engineering judgment, not keyword matching. Use after issue-target-component has produced a root cause/evidence/verdict, when you need the Excel "Category" column value for that issue. Outputs a JSON verdict with category, subcategory, and a concise reason.
---

# Issue Category Analysis

Deep semantic categorization of one triaged issue (pytorch or torch-xpu-ops)
into the **11 canonical Category buckets**, using the JSON verdict produced
by `issue-triage/triage-issue/issue-target-component` (synthesized into the
`root_cause_result` shape below — see `triage-issue/SKILL.md`'s "Adapting
`issue-target-component` output" section) as primary evidence. Category
assignment requires understanding the issue's technical domain — tracing the
failure to its actual source component — not regex/keyword matching against
the title.

You **analyze only**; never edit files, `git commit`, open PRs, or mutate
GitHub issue state.

## Inputs

- **`root_cause_result`** (required) — a `root_cause_result`-shaped object
  built from `issue-triage/triage-issue/issue-target-component`'s output
  (its internal `issue-dependency` classification included): `root_cause`,
  `evidence.traced_symbols`, `evidence.call_path`, `domain` (`"xpu-kernel"`
  | `"upstream-pytorch"` | `"N/A"`), `third_party_dependency`,
  `fix_strategy`, `target_repo`, `verdict`.
- **`issue_info`** (optional but recommended) — `extract-basic-info` output
  for the same issue: `labels`, `title`, `test_file`/`test_class`/`test_case`
  (or `test_cases[]`), `module`. Used to corroborate/disambiguate, never to
  override direct evidence from `root_cause_result`.

If `root_cause_result.verdict == "NEEDS_HUMAN"` because there was no
failure signature to analyze (or the issue was out of scope), you may
still categorize from the issue body/labels alone — note this by setting
`"confidence": "Low"` in the output (see below).

## Canonical Category Taxonomy (11 buckets)

**Authoritative enum** for the Excel "Category" column — no other values
permitted:

`Distributed`, `Flash Attention`, `Inductor`, `TorchAO`, `Sparse`,
`Torch Ops - gemm`, `Torch Ops - eltwise`, `Torch Ops - reduction`,
`Torch Ops - others`, `Torch Runtime`, `Others`.

### Decision Priority Order

When an issue matches multiple categories, apply in this order (first match
wins):

1. `Distributed` — anything tagged `[distributed]` or involving
   XCCL/ProcessGroup/DDP/FSDP/DTensor/symm_mem/collective ops
2. `Flash Attention` — SDPA / flash / efficient attention kernels (unless
   already claimed by Distributed)
3. `Inductor` — torch.compile / Dynamo / AOTAutograd / Triton codegen /
   benchmark failures via the inductor path
4. `TorchAO` — quantization (int4/int8/fp8/PT2E quant/torchao)
5. `Sparse` — sparse tensor formats/ops
6. `Torch Ops - gemm` — matrix multiplication family (see subcategories)
7. `Torch Ops - eltwise` — elementwise/pointwise operations
8. `Torch Ops - reduction` — reduction operations
9. `Torch Ops - others` — other ATen/native ops not fitting gemm/eltwise/reduction
10. `Torch Runtime` — torch.xpu.* runtime, memory/OOM, profiler, RNG, streams,
    IPC, device management
11. `Others` — CI/infra/tracking/build/doc/test-harness/meta — the catch-all

## Torch Operations Subcategories

Only populate `subcategory` when `category` is one of the four `Torch Ops -
*` buckets; otherwise `subcategory = "N/A"`.

### `Torch Ops - gemm`

Operations whose computational core is a GEMM (General Matrix Multiply) or
convolution:

- **Matmul family**: `torch.mm`, `torch.bmm`, `torch.matmul`, `torch.addmm`,
  `torch.addmv`, `torch.baddbmm`, `torch.addbmm`, `torch.dot`, `torch.mv`,
  `torch.einsum` (when it lowers to matmul)
- **Linear**: `torch.nn.Linear`, `F.linear`
- **Convolution**: `torch.nn.Conv1d/2d/3d`, `torch.nn.ConvTranspose1d/2d/3d`,
  `F.conv1d/2d/3d`, depthwise conv, grouped conv
- **BLAS/LAPACK with GEMM core**: `torch.linalg.solve`, `torch.linalg.inv`,
  `torch.linalg.cholesky`, `torch.linalg.lu`, `torch.linalg.svd`,
  `torch.linalg.qr`, `torch.linalg.pinv`, `torch.linalg.ldl_solve`
- **Quantized GEMM**: `_weight_int4pack_mm`, qlinear, qconv (if NOT routed
  through TorchAO/Inductor)

**How to identify**: the root cause or traceback points to oneDNN
matmul/conv primitives, GEMM kernels, or linalg decomposition routines. The
operation is fundamentally O(n^3) or O(n^2*k).

### `Torch Ops - eltwise`

Operations that apply independently to each element (or small local
neighborhood for normalization):

- **Unary**: `abs`, `neg`, `exp`, `log`, `sqrt`, `rsqrt`, `sin`, `cos`,
  `tanh`, `sigmoid`, `relu`, `gelu`, `hardswish`, `silu`, `ceil`, `floor`,
  `round`, `sign`, `bitwise_not`
- **Binary**: `add`, `sub`, `mul`, `div`, `pow`, `fmod`, `remainder`,
  `bitwise_and/or/xor`, `maximum`, `minimum`, `where`
- **Activation functions**: all `torch.nn.functional` activations
- **Normalization** (elementwise with learned params): `batch_norm`,
  `layer_norm`, `group_norm`, `instance_norm` — these have reduction
  internally but are primarily elementwise transforms
- **Type conversion / copy**: `to()`, `copy_`, dtype casting, `clone`
- **Comparison**: `eq`, `ne`, `lt`, `gt`, `le`, `ge`, `isnan`, `isinf`
- **Channel shuffle**: `native_channel_shuffle`
- **Clamp/clip**: `torch.clamp`, `torch.clip`

**How to identify**: the operation processes each element (or small fixed
window) independently. Computational complexity is O(n) in the number of
elements.

### `Torch Ops - reduction`

Operations that reduce one or more dimensions, producing output smaller than
input:

- **Statistical**: `sum`, `mean`, `std`, `var`, `norm`, `nansum`, `nanmean`
- **Min/Max**: `min`, `max`, `amin`, `amax`, `argmin`, `argmax`
- **Cumulative**: `cumsum`, `cumprod`, `cummax`, `cummin`
- **Sorting/selection**: `sort`, `topk`, `kthvalue`, `median`, `mode`
- **Histogram**: `histc`, `histogram`, `bincount`
- **Loss functions** (contain reduction): `nll_loss`, `cross_entropy`,
  `ctc_loss`, `mse_loss`, `l1_loss`, `smooth_l1_loss`
- **Logical reduction**: `all`, `any`
- **Prod**: `prod`
- **Unique**: `unique`, `unique_consecutive`
- **Batch norm statistics** (when the issue is specifically about the
  mean/var computation, not the elementwise transform)

**How to identify**: the output has fewer elements than the input along at
least one dimension. The operation aggregates information across elements.

### `Torch Ops - others`

ATen/native operations that don't fit the above three subcategories:

- **Indexing**: `index_select`, `index_add`, `index_put`, `gather`,
  `scatter`, `embedding`, `index_copy`
- **FFT/spectral**: `fft`, `ifft`, `rfft`, `irfft`, `stft`
- **Pooling**: `max_pool2d/3d`, `avg_pool2d/3d`, `adaptive_avg_pool2d`
- **Reshape/view**: `view`, `reshape`, `permute`, `transpose`, `contiguous`,
  `expand`, `unsqueeze`, `squeeze`
- **Memory/tensor creation**: `empty`, `zeros`, `ones`, `rand`, `randn`,
  `arange`, `linspace`, `logspace`
- **NestedTensor**: nested tensor operations
- **DLPack**: `to_dlpack`, `from_dlpack`
- **Autograd mechanics**: `backward`, `grad`, `autograd.Function` issues
  specific to op correctness
- **Custom ops**: `torch.library`, custom operator registration
- **Padding**: `F.pad`, `ReflectionPad`, `ReplicationPad`
- **Upsampling**: `F.interpolate`, `Upsample`

**How to identify**: the operation transforms tensor structure (indexing,
reshaping) or belongs to a specialized domain (FFT, pooling) that isn't pure
GEMM, elementwise, or reduction.

## Workflow

### Step 1 — Identify the failing component from `root_cause_result`

Read `root_cause`, `evidence.traced_symbols`, `evidence.call_path`, `domain`,
and `fix_strategy` together with `issue_info.labels`/`title` (if given).
Answer: **what component is actually broken?**

- `domain == "xpu-kernel"` and traced symbols land in a distributed/XCCL/
  ProcessGroup path -> `Distributed`
- Traced symbols/root cause point at SDPA/flash/efficient-attention kernels
  (and not already claimed by Distributed) -> `Flash Attention`
- The error occurs inside `torch.compile`/Dynamo/AOTAutograd/Triton codegen,
  or `fix_strategy`/`call_path` names Inductor internals, even if the
  underlying op is a conv or matmul -> `Inductor`
- Root cause is in quantization (int4/int8/fp8/PT2E quant/torchao) ->
  `TorchAO`
- Root cause is in a sparse tensor format/op -> `Sparse`
- `domain == "xpu-kernel"` or `"upstream-pytorch"` and the traced symbol is
  an ATen op kernel (eager mode or its SYCL implementation) -> one of
  `Torch Ops - *` (go to Step 2)
- Root cause is in `torch.xpu.*` runtime, memory/OOM, profiler, RNG,
  streams, IPC, or device management (and `third_party_dependency` may point
  to driver/IGC/Level Zero) -> `Torch Runtime`
- CI/infra/tracking/build/doc/test-harness/meta, or an umbrella/task issue,
  or nothing above applies -> `Others`

Cross-check `issue_info.test_file`/`labels` only to corroborate — a
`test/distributed/` path or `module: distributed` label supports
`Distributed`; `benchmarks/dynamo/` supports `Inductor`; etc. Never let the
test path override direct evidence in `root_cause_result.evidence` when they
conflict (root cause wins — e.g. `test_ops.py` can still exercise an inductor
path; `test_inductor` can still reveal a pure op bug).

### Step 2 — Subcategorize `Torch Ops`

Only when Step 1 lands on `Torch Ops - *`:

1. Is the core computation a matrix multiply or convolution? -> `gemm`
2. Is the core computation elementwise (O(n), independent per element)? ->
   `eltwise`
3. Does the operation reduce dimensions (output smaller than input)? ->
   `reduction`
4. None of the above? -> `others`

When an op has both elementwise and reduction components (e.g. `batch_norm`
= mean/var reduction + normalize elementwise):
- Bug in the reduction statistics computation -> `reduction`
- Bug in the elementwise normalization transform -> `eltwise`
- Unclear -> default to `eltwise` (primary API purpose is the elementwise
  transform)

### Step 3 — Generate the category reason

Write a concise (5-15 word) reason tying the category to the specific traced
symbol/root cause, e.g.:

- "Convolution backward kernel accuracy (oneDNN)"
- "torch.compile Triton codegen failure on XPU"
- "ProcessGroupXCCL missing split_group API"
- "SDPA flash attention kernel crash at large seq_len"
- "index_select kernel performance (indexing op)"
- "torch.std overflow on large input (statistical reduction)"
- "channel_shuffle validation error (elementwise transform)"
- "FFT spectral op oneMKL backend failure"

### Step 4 — Set confidence

- `High` — category derived directly from `root_cause_result.evidence`
  (`traced_symbols`/`call_path`) for a non-`NEEDS_HUMAN` issue, regardless
  of whether reproduction was live-verified (`issue-target-component` traces
  from the issue's own traceback when no reproduction exists).
- `Medium` — category inferred from `root_cause`/`fix_strategy` text plus
  corroborating labels, without a fully traced symbol.
- `Low` — `verdict == "NEEDS_HUMAN"` with no failure signature available,
  and category is inferred from the issue title/labels alone.

## Output

```python
{
    "source_issue": {"issue_id": int, "repo": str, "title": str},
    "category": "Distributed" | "Flash Attention" | "Inductor" | "TorchAO"
               | "Sparse" | "Torch Ops - gemm" | "Torch Ops - eltwise"
               | "Torch Ops - reduction" | "Torch Ops - others"
               | "Torch Runtime" | "Others",
    "subcategory": "gemm" | "eltwise" | "reduction" | "others" | "N/A",
    "category_reason": str,        # 5-15 words, ties to traced symbol/root cause
    "evidence": {
        "traced_symbols": [str],       # copied from root_cause_result.evidence
        "root_cause_summary": str,     # 1 sentence, from root_cause_result.root_cause
        "domain": "xpu-kernel" | "upstream-pytorch" | "N/A",
        "priority_rule_applied": str   # which of the 11 decision-priority rules fired first
    },
    "confidence": "High" | "Medium" | "Low"
}
```

## Anti-Patterns (DO NOT)

1. **DO NOT** categorize based on test file name alone. `test_ops.py` tests
   can exercise inductor paths; `test_inductor` can reveal op bugs.
2. **DO NOT** assign `Inductor` just because `torch.compile` appears in the
   reproducer. If the root cause is in the eager kernel and compile just
   exposes it, use the op category.
3. **DO NOT** assign `Torch Ops - gemm` to every linalg issue. Only linalg
   ops with GEMM as their computational core (solve, cholesky, svd, etc.)
   belong there. `linalg.norm` is a reduction.
4. **DO NOT** use keyword scripts to categorize. The same word ("attention")
   appears in Flash Attention issues AND in TransformerEncoder issues that
   are actually Inductor bugs. Semantic understanding is required.
5. **DO NOT** assign subcategories based on words in the title alone.
   "accuracy" in the title doesn't tell you whether it's gemm/eltwise/
   reduction — you must identify which op actually failed.
6. **DO NOT** invent a category outside the 11-bucket enum.
7. **DO NOT** override `root_cause_result.evidence` with a weaker signal
   (label/title) when they disagree — root cause wins.

## Hard rules

- Read-only/analysis-only: never edits files, `git commit`s, or mutates
  GitHub issue state (labels, project fields).
- `category` MUST be one of the 11 canonical values verbatim — this is an
  Excel-column enum, not free text.
- `subcategory` MUST be `"N/A"` unless `category` starts with `"Torch Ops -"`.
- Apply the Decision Priority Order exactly — first match wins; do not skip
  ahead to a "more interesting" later bucket.
- Never derive `category` purely from `issue_info` when `root_cause_result`
  gives conflicting direct evidence.

## Example

```bash
# Chain from issue-target-component output
python3 .claude/skills/validation/issue-triage/extract-issue-information/scripts/extract_basic_info.py 4344 \
  > issue_info.json
# -> run issue-target-component with issue_info.json + conda_env + pytorch_folder
#    (this single invocation traces, classifies dependency, routes, and
#    returns the final verdict) to get root_cause_result.json
# -> feed root_cause_result.json (and optionally issue_info.json) as this skill's input
```

## Scope

One issue per invocation (mirrors `issue-target-component` scope; if it
reported per-case results in `evidence` for divergent root causes,
categorize each case separately). Read-only: no edits, no `git commit`, no
`gh` mutation.
</content>
