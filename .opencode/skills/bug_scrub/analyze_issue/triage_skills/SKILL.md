# PyTorch XPU-Ops Issue Triage - Complete Workflow

> **Base constraints**: This subskill is governed by [`../../base-constraints.md`](../../base-constraints.md) (C1 logs->agent_space, C2 ask-on-blocker, C3 background status, C4 no over-skip). They apply even when not restated here.

> **Path convention**: `${PYTORCH_REPO_ROOT}` (default `~/upstream/pytorch`) — see [`../../SKILL.md`](../../SKILL.md) for the full convention.

## Execution Constraints

**CRITICAL INSTRUCTION: PARALLEL EXECUTION REQUIRED.** 
To minimize execution turns and lower latency, you MUST run independent data-gathering commands concurrently using parallel tool calls. 
In your very first turn, execute `gh issue view` along with initial file reading or codebase searches (e.g. `ast_grep_search` or `bash` grep) in parallel. 
Do NOT run them sequentially.

## Overview
This skill provides comprehensive tooling for triaging GitHub issues from intel/torch-xpu-ops repository. It integrates version-aware analysis, deep root cause investigation, explore agent usage for code exploration, and expert-level fix suggestions.

## When to Use
- Triage new and existing GitHub issues from torch-xpu-ops
- Deep root cause analysis beyond simple pattern matching
- Explore and understand codebase structure
- Version compatibility checking
- Environment verification for reproducible results

---

## Authoritative Reference (read this first)

This section is the **single source of truth** for per-issue triage output when populating the tracking Excel. All wave/batch agents must conform to these taxonomies and the JSON schema below. Anything else in this skill is guidance only.

### Canonical Output JSON Schema

Each triaged issue MUST be emitted as one object in a JSON array. Required keys — no wrapping (`{"results": [...]}` is forbidden):

```json
{
  "row":           <int>,      // Excel row (2..N). REQUIRED.
  "issue_id":      <int>,      // GitHub issue number. REQUIRED.
  "category":      "<string>", // From Category Taxonomy below. REQUIRED.
  "priority":      "P0|P1|P2|P3",
  "dependency":    "<string>", // From Dependency Taxonomy below. Use "" for blank.
  "root_cause":    "<string>", // 2-4 sentences, cite file:line.
  "fix_approach":  "<string>", // Actionable next steps.
  "mini_reproducer": {         // OPTIONAL — present when STEP 3.5 ran.
    "path":       "<string>", // e.g. "agent_space/phase3_triage/repro_3530.py"
    "reproduced": <bool>,     // true if the script reproduced the same error
    "notes":      "<string>"  // 1-2 sentences: what was tried, why NOT_REPRODUCED, etc.
  }
}
```

Output the JSON array directly. No markdown fences, no prose, no trailing commentary.

### Category Taxonomy (11 buckets — authoritative)

This is the production taxonomy used in the tracking Excel column "Category". Pick **exactly one**. The four `Torch Ops - *` sub-buckets replace the legacy single `Torch Operations` value. See `SKILL_Category_Analysis.md` for the detailed rubric + examples (gemm/eltwise/reduction/others sub-bucket selection guide).

| Category | Use when |
|---|---|
| `Distributed` | ProcessGroup/XCCL/DDP/FSDP/DTensor/symm_mem/collective ops; anything tagged `[distributed]` in title |
| `Flash Attention` | SDPA / `scaled_dot_product_attention` / flash/efficient attention kernels / MultiheadAttention |
| `Inductor` | torch.compile / Dynamo / AOTAutograd / Triton codegen / FakeTensor / ExportedProgram / benchmark suites running via inductor |
| `TorchAO` | quantization paths: int4/int8 weight-only/dynamic, fp8, PT2E quant, torchao integration |
| `Sparse` | sparse tensors (COO/CSR/CSC/BSR), sparse ops, `sparse_csr_tensor` APIs |
| `Torch Ops - gemm` | matrix multiplication family: matmul/mm/bmm/addmm, conv/conv_transpose, linalg solve/cholesky/svd/qr/lu/pinv |
| `Torch Ops - eltwise` | elementwise/pointwise: add/mul/clamp/pow/activations; includes normalization ops (batch_norm/layer_norm/group_norm/instance_norm) whose primary API purpose is elementwise transform |
| `Torch Ops - reduction` | sum/mean/std/var/norm/topk/argmax/argmin/all/any; loss functions (nll_loss/cross_entropy/mse_loss/etc.) |
| `Torch Ops - others` | indexing/reshape/scatter/gather, FFT, pooling, distributions, sort, and other aten/native ops that aren't gemm/eltwise/reduction |
| `Torch Runtime` | torch.xpu.* runtime APIs, memory management/OOM, device context, profiler, RNG, streams, IPC/share_memory |
| `Others` | CI/infra/tracking issues, build/doc/test-harness bugs, upstream benchmark harness gaps, release checklists, meta-tracking — the catch-all |

**Decision order** (resolve overlaps): `Distributed` > `Flash Attention` > `Inductor` > `TorchAO` > `Sparse` > `Torch Ops - *` (use the gemm/eltwise/reduction/others sub-bucket selector) > `Torch Runtime` > `Others`. Example: a `[distributed]` SDPA issue → `Distributed` (not Flash Attention).

**Torch Ops sub-bucket selector** (only when the chosen category is `Torch Ops - *`):
1. Core computation is matrix-multiply / convolution / linalg-solve family → `gemm`
2. Core computation is elementwise (O(n), independent per element) → `eltwise`
3. Output dimensions are smaller than input (reduces along an axis) → `reduction`
4. Indexing, reshape, FFT, pooling, distributions, sort — none of the above → `others`

Notes:
- "Feature Gap" is a *sub-type* surfaced in `fix_approach` text, NOT a Category value.
- "PT2E" rolls into `Inductor` (or `TorchAO` for PT2E quantization).
- "Build/Compilation", "Documentation", "CI/CD", "Test Infrastructure", "Numerical Accuracy" are descriptive sub-types; map them to the bucket above using the domain of the failing component (e.g., Numerical Accuracy on Conv3d → `Torch Ops - gemm`; CI infra → `Others`).
- The legacy single `Torch Operations` value is **deprecated**. Any existing rows with `Torch Operations` must be reclassified to the appropriate `Torch Ops - *` sub-bucket.

### Dependency Taxonomy (authoritative)

Pick **exactly one** value. Populates Excel column "Dependency".

Run dependency classification **after** drafting `root_cause` and `fix_approach`. Dependency is a final ownership/component classification that should use the confirmed failing component, the diagnosed root cause, and the proposed fix path. Do not assign dependency from title, labels, or operator name before root-cause analysis is complete.

| Value | Use when |
|---|---|
| `driver` | ocloc / IGC / libigc / intel-igc-cm / level-zero / compute-runtime / drm_neo / SYCL runtime bug / GPU segfault at driver layer |
| `xccl` | ProcessGroupXCCL / WorkXCCL / FlightRecorderXCCL / torch.xpu.xccl / oneCCL |
| `triton` | intel-xpu-backend-for-triton codegen/compile/lowering |
| `oneDNN` | oneDNN-backed op (conv*, SDPA, linear, quantized int8, _grouped_mm, etc.) |
| `oneMKL` | oneMKL-backed op (linalg.svd/qr/pinv/cholesky, BLAS paths) |
| `oneAPI` | oneAPI compiler/runtime version mismatch or compiler regression (CMPLRLLVM-*) |
| `CPU fallback` | XPU operator missing; CPU fallback registered in torch-xpu-ops |
| `SYCL kernel: <FileName.cpp>` | Bug in a specific SYCL kernel under `torch-xpu-ops/src/ATen/native/xpu/sycl/` — cite the file name |
| `upstream-pytorch` | Bug lives in pytorch/pytorch (Dynamo/Inductor logic, AOTAutograd, `_prims_common`, test-list sync, benchmark harness); fix PR goes to pytorch repo |
| `""` (blank) | Pure torch-xpu-ops internal issue (not an external dep, not upstream) — e.g., test-list maintenance, meta-tracking, doc cleanup inside torch-xpu-ops |

**Lookup for operator-based dependency**: `${BUG_SCRUB_SKILL_ROOT}/prepare_data/pytorch_xpu_backend_analysis/xpu_supported_operators_complete_list.md`
- Part I (~L33): Implementation File Index by dependency (Native SYCL / oneMKL / oneDNN)
- Part II (~L136): Operator Registry
- Part IV (~L1143): CPU Fallback Operators

### Priority Taxonomy

**P0** = Crash/segfault on Core API (listed in SKILL_Priority_Analysis.md) OR performance regression >7% (quantified, measured between releases).
**P1** = UT >6 failures OR hang/deadlock OR regression with accuracy issues.
**P2** = 1-6 UT failures OR functional error OR perf regression ≤7%.
**P3** = Enhancement/feature request OR error message difference OR alignment issue.

See `SKILL_Priority_Analysis.md` for detailed rubric, Core API list, and examples.

If the input row already has a non-blank `Priority`, treat it as the labeled value imported from GitHub Projects `PyTorchXPU Priority` and preserve it. Still perform root-cause/fix-approach analysis, but do not replace the row's `Priority` with a computed value. Only compute priority when the input `Priority` is blank.

### Standard Investigation Pattern (per issue)

#### Blank Cell Definition (authoritative for ALL Incremental-Mode checks below)

A cell in the Issues sheet is **blank** if and only if its raw value matches any of:

- Python `None` (truly empty openpyxl cell)
- Empty string `""`
- Whitespace-only string (after `.strip()`)
- The case-insensitive literal string `"None"` — i.e., `"none"`, `"None"`, `"NONE"`, or any whitespace-padded variant

Reference Python predicate (every Phase 3.3 step MUST behave equivalently):

```python
def is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        s = v.strip()
        return s == "" or s.lower() == "none"
    return False
```

**Rationale**: Phase 1 (`prepare_data/issue-basic-info-extraction/generate_excel.py`) historically wrote the literal string `"None"` into the `Dependency` column when no dependency was detected from labels/body. Treating that string as non-blank would defeat Source A (HTML) refill in Incremental Mode and poison every downstream consumer (highlight HTML, markdown report, dependency filter dropdowns). Future Phase 1 fixes may switch the sentinel to `""`, but Phase 3.3 must remain tolerant of legacy `"None"` values that already exist in `result/torch_xpu_ops_issues.xlsx` and in any historical xlsx snapshot.

**Scope**: this definition governs EVERY occurrence of "blank" / "non-blank" in the rest of this skill, including:
- Step 0 Incremental Mode skip gate (4-column all-non-blank check)
- Step 0 Source A per-column write gate
- Step 0 Source B per-column write gate
- Step 6 / live-triage per-column write gate
- `Priority` preservation rule (line ~104)

When this definition treats a cell as blank, the runner MUST also normalize it on write — replace any pre-existing `"None"` string with a true `None` (or `""`) before writing the new value, so downstream phases don't re-inherit the sentinel.

0. **Incremental Mode gate + Details Fast Path**: Apply the Phase 3.3 Incremental Mode rule from `../../SKILL.md` line ~51 **first**:

   - If `Category`, `Priority`, `Root Cause`, **and** `Fix Approach` are **all** non-blank for this issue (per the Blank Cell Definition above) → skip this issue entirely. No fetch, no triage.
   - Otherwise continue; the rest of this step never overwrites a non-blank cell.

   Then attempt **two** published-data sources in parallel. Both are Incremental-Mode-safe: each only writes a target cell if that cell is currently blank.

   **Source A — Highlight HTML (`Category` / `Priority` / `Dependency`)**

   Fetch once per Phase 3.3 run and cache:

   ```
    https://raw.githubusercontent.com/daisyden/torch-xpu-ops/refs/heads/opencode/classify_ut/issue_triage/result/bug_scrub_highlight.html
   ```

   Each issue appears as a `<tr>` carrying authoritative attributes:

   ```html
   <tr data-issue="3394" data-priority="P0" data-category="Flash Attention" data-dependency="oneDNN" ...>
   ```

   For the current issue, find the matching `<tr data-issue="<id>">` (regex / BeautifulSoup / simple string find), then:

   - If `Category` cell is blank → write `data-category` (must be one of the 11 buckets from §"Category Taxonomy"; reject and fall through if not).
   - If `Priority` cell is blank → write `data-priority` (must be `P0`/`P1`/`P2`/`P3`; reject and fall through if not).
   - If `Dependency` cell is blank → write `data-dependency` value verbatim (allowed: any value from §"Dependency Taxonomy", including `""` blank, or `SYCL kernel: <FileName.cpp>` family).
   - If the issue ID is **not present** in the HTML, treat all three as "not provided by Source A" and fall through to live classification (Step 6) for whichever of the three is still blank.

   **Source B — Per-issue detail markdown (`Root Cause` / `Fix Approach`)**

   ```
    https://raw.githubusercontent.com/daisyden/torch-xpu-ops/refs/heads/opencode/classify_ut/issue_triage/result/details/<issue_id>.md
   ```

   Use `webfetch(url=..., format="markdown")`. If the fetch returns a 200 with a real document (HTTP 404 / "not found" / empty body → file does not exist, proceed to Step 1):

   - Parse the `## Root Cause` and `## Fix Approach` sections (everything between that heading and the next `## ` heading, trimmed).
   - **Per Incremental Mode**: write the parsed value into `Root Cause` / `Fix Approach` **only if the target cell is currently blank**. Never overwrite an existing non-blank cell, even if the detail file disagrees with it.
   - **Do NOT copy `Category`, `Priority`, or `Dependency` from the detail file.** Those columns come from Source A above; if Source A didn't cover them, Step 6 below will compute them locally.
   - If parsing fails (heading absent, body literally `_(none)_`, or empty) treat the detail file as unusable and fall through to the live path for `Root Cause` / `Fix Approach`.

   **After both sources are tried**: if every blank column got filled (Category, Priority, Dependency, Root Cause, Fix Approach), you may skip Steps 1-5 entirely. Otherwise run Steps 1-6 only for whichever columns remain blank.

1. **Fetch**: `gh issue view <id> --repo intel/torch-xpu-ops --json title,body,labels,comments,state`
   - Fallback: `webfetch(url="https://github.com/intel/torch-xpu-ops/issues/<id>", format="markdown")`
2. **Locate** test/code/error — read relevant files, grep for the failing symbol.
3. **Reproduce** (when applicable): write a minimal Python reproducer and verify it triggers the same error. See `SKILL_Mini_Reproducer.md` for the template, iteration budget, and acceptance criteria. Skip for pure tracker / doc / infra issues.
4. **Cite** file:line evidence (torch-xpu-ops source and/or pytorch source).
5. **Draft root_cause and fix_approach first** from the evidence. For new issues, existing Excel triage fields may be blank; do not wait for them or treat blanks as evidence.
6. **Assign dependency, priority, and category last** using the taxonomy docs after root cause/fix approach are known. Dependency should learn from root_cause and fix_approach; category and priority are final classifications, not early keyword passes. Exception: preserve an existing non-blank `Priority` from `PyTorchXPU Priority` instead of recomputing it.
7. Write the JSON entry.

### Pinned Reference Paths

| Purpose | Path |
|---|---|
| torch-xpu-ops source | `${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/` |
| PyTorch source | `${PYTORCH_REPO_ROOT}/` |
| Operator → dependency lookup | `${BUG_SCRUB_SKILL_ROOT}/prepare_data/pytorch_xpu_backend_analysis/xpu_supported_operators_complete_list.md` |
| CI op_ut XML logs | `${BUG_SCRUB_SKILL_ROOT}/../../ci_results/torch-xpu-ops/Inductor-XPU-UT-Data-*/op_ut/*.xml` |
| Tracking Excel | `${BUG_SCRUB_SKILL_ROOT}/../../result/torch_xpu_ops_issues.xlsx` |
| Agent workspace (scratch) | `${PYTORCH_REPO_ROOT}/agent_space/phase3_triage/` |
| Published per-issue details (Step 0 Source B: Root Cause / Fix Approach) | `https://raw.githubusercontent.com/daisyden/torch-xpu-ops/refs/heads/opencode/classify_ut/issue_triage/result/details/<issue_id>.md` |
| Published highlight HTML (Step 0 Source A: Category / Priority / Dependency) | `https://raw.githubusercontent.com/daisyden/torch-xpu-ops/refs/heads/opencode/classify_ut/issue_triage/result/bug_scrub_highlight.html` |

### For large-scale triage (many issues)

See `SKILL_Batch_Orchestration.md` for the wave-based parallel pattern (5 issues per batch × 5 parallel explore agents per wave × N waves → merge → single Excel write).

---

## Workflow Summary

```
┌─────────────────────────────────────────────────────────────────┐
│ STEP 1: Issue Acquisition & Version Detection                   │
├─────────────────────────────────────────────────────────────────┤
│ • Fetch issue (gh CLI or web fetch)                             │
│ • Extract versions: PyTorch, IGC, Triton, oneAPI               │
│ • Check private/unreleased branch status                        │
│ • Assess version compatibility                                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 2: Reproduce Command Extraction                            │
├─────────────────────────────────────────────────────────────────────────┤
│ • Identify test case pattern from issue body                    │
│ • Enable explore agent for code exploration                      │
│ • Access PyTorch test code (${PYTORCH_REPO_ROOT}/test/)                  │
│ • Access torch-xpu-ops test code (${PYTORCH_REPO_ROOT}/../torch-xpu-ops/test/)  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 3: Code Exploration & Test Analysis                        │
├─────────────────────────────────────────────────────────────────┤
│ • Use explore agent to find implementation files                 │
│ • Locate test files in PyTorch (test_linalg.py, test_ops.py)    │
│ • Locate test files in torch-xpu-ops (test/xpu/)                │
│ • Analyze test expectations and assertions                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 3.5: Mini Reproducer  (see SKILL_Mini_Reproducer.md)       │
├─────────────────────────────────────────────────────────────────┤
│ 3.5.1 Write a minimal Python reproducer (single file, ≤30 LOC,  │
│       hard-coded inputs, deterministic seed, runs against the   │
│       conda env). Save to                                       │
│       agent_space/phase3_triage/repro_<issue>.py                │
│ 3.5.2 Run it and verify it reproduces the SAME error            │
│       (exception type + message substring match).               │
│       If not, iterate up to N=3 times (adjust dtype/shape/      │
│       device/seed). Capture stdout+stderr to                    │
│       agent_space/phase3_triage/repro_<issue>.log               │
│ 3.5.3 Emit "mini_reproducer" {path, reproduced, notes} into     │
│       the triage JSON. Skip the entire step for tracker / doc / │
│       infra-only issues (Category == Others without a kernel    │
│       failure) and omit the field.                              │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 4: Runtime Verification (if compatible)                    │
├─────────────────────────────────────────────────────────────────┤
│ • Execute reproduce command in conda env                        │
│ • Capture all test execution results                             │
│ • Compare with PyTorch upstream test behavior                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 5: Deep Root Cause Analysis                                │
├─────────────────────────────────────────────────────────────────┤
│ • Multi-dimension analysis based on explore findings            │
│ • XPU implementation vs CPU fallback comparison                 │
│ • Kernel code investigation                                      │
│ • Draft root_cause and fix_approach evidence                    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 6: Final Dependency / Priority / Category Assignment        │
├─────────────────────────────────────────────────────────────────┤
│ • Assign Dependency from confirmed failing component, root_cause,│
│   and fix_approach                                               │
│ • Assign Priority from verified impact, unless the input row     │
│   already has labeled PyTorchXPU Priority                        │
│ • Assign Category from root-cause/source evidence                │
│ • New-case Excel triage fields may be blank; use issue/log/     │
│   source evidence, not empty cells, as classification input      │
└─────────────────────────────────────────────────────────────────┘
```

## Prerequisites

### Required Access & Paths
```bash
# Environment
source ~/miniforge3/bin/activate pytorch_opencode_env

# PyTorch source and test paths
${PYTORCH_REPO_ROOT}                                    # PyTorch source root
${PYTORCH_REPO_ROOT}/test/                              # PyTorch test directory
${PYTORCH_REPO_ROOT}/test/test_linalg.py                # Example: linalg tests
${PYTORCH_REPO_ROOT}/test/test_ops.py                  # Example: ops tests
${PYTORCH_REPO_ROOT}/aten/src/ATen/native/             # ATen native implementations

# torch-xpu-ops source and test paths
${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops         # XPU ops source root
${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/test/xpu/  # XPU ops test directory
${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/src/    # XPU ops implementation
```

### Source Code Locations Reference

| Component | Location Path | Purpose |
|-----------|---------------|---------|
| PyTorch ATen | `${PYTORCH_REPO_ROOT}/aten/src/ATen/native/` | Native operator implementations |
| PyTorch Tests | `${PYTORCH_REPO_ROOT}/test/test_*.py` | Upstream test cases |
| XPU Ops Native | `${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/src/ATen/native/` | XPU-specific kernels |
| XPU Ops Tests | `${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/test/xpu/test_*.py` | XPU-specific tests |
| SYCL Kernels | `${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/src/ATen/native/xpu/sycl/` | XPU kernel implementations |
| Operator Registry | `${BUG_SCRUB_SKILL_ROOT}/prepare_data/pytorch_xpu_backend_analysis/xpu_supported_operators_complete_list.md` | Dependency mapping |

### Version Detection Commands
```bash
# Core versions
python -c "import torch; print(torch.__version__)"
python -c "import torch; print(torch.xpu.get_device_properties(0).driver_version)"
python -c "import triton; print(triton.__version__)"

# Check all嘶
conda list | grep -E "intel|dpcpp|oneapi"
```

## Tool Reference

### 1. Explore Agent - Code Exploration
```python
# Use explore agent for comprehensive code search
task(description="explore_xpu_ops", 
     prompt="Find all implementations and tests related to OPERATOR_NAME in torch-xpu-ops\n\nRequirements:\n1. Search ${PYTORCH_REPO_ROOT} for native implementations\n2. Search ${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops for XPU-specific code\n3. List relevant test files in both PyTorch and torch-xpu-ops\n4. Identify implementation files, kernel files, and test files\n\nReturn:\n- List of implementation file paths\n- List of test file paths  \n- Key function signatures\n- Related kernel files",
     subagent_type="explore")

# Medium exploration level (default)
# Use "quick" for simple searches
# Use "very thorough" for comprehensive analysis
```

### 2. Issue Fetching
```python
# Method 1: GitHub CLI (requires gh auth login)
gh issue view {issue_number} --repo intel/torch-xpu-ops --json title,body,labels

# Method 2: Web fetch fallback
webfetch(url="https://github.com/intel/torch-xpu-ops/issues/{issue_number}", format="markdown")
```

### 3. Test File Access Patterns
```python
# Access PyTorch test files (upstream tests)
read(filePath="${PYTORCH_REPO_ROOT}/test/test_linalg.py", offset=1700, limit=100)
# Structure: ${PYTORCH_REPO_ROOT}/test/test_{module}.py

# Access torch-xpu-ops test files
read(filePath="${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/test/xpu/test_transformers_xpu.py", offset=1100, limit=50)
# Structure: ${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/test/xpu/test_{module}_xpu.py

# Search for specific test methods
grep(pattern="def test_.*xpu", path="${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/test/xpu", include="*.py")
```

### 4. Code Investigation
```python
# Search implementations across both repos
grep(pattern="operator_name", path="${PYTORCH_REPO_ROOT}", include="*.cpp")
grep(pattern="operator_name", path="${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/src", include="*.cpp")

# Find implementation files
glob(pattern="**/aten/**/native/**/Attention*.cpp", path="${PYTORCH_REPO_ROOT}")
glob(pattern="**/xpu/sycl/**/Attention*.cpp", path="${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops")

# Read implementation
read(filePath="${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/src/ATen/native/transformers/sycl/AttentionKernels.cpp", offset=100, limit=50)
```

### 5. Runtime Execution
```python
# Execute in conda env
bash(command="source ~/miniforge3/bin/activate pytorch_opencode_env && python -c '<code>'", timeout=180000)
```

## Explore Agent Integration

### When to Use Explore Agent
```python
EXPLORE_SCENARIOS = {
    "sdpa_kernels": {
        "trigger": "scaled_dot_product_attention，听到看到这个操作",
        "depth": "medium",
        "requirements": "Explore SDPA implementations in both default and torch-xpu-ops"
    },
    "linalg_ops": {
        "trigger": "linalg.cond, linalg.svd",
        "depth": "medium", 
        "requirements": "Find linalg operations in native ATen and XPU overrides"
    },
    "test_investigation": {
        "trigger": "Need to understand test expectations",
        "depth": "quick",
        "requirements": "Locate test file and analyze test structure"
    }
}
```

### Explore Agent Usage Templates

#### Template 1: Operator Implementation Search
```python
task(description="operator_implementation_finder",
     prompt=f"""
Find COMPLETE implementation for OPERATOR: <operator_name>

Search in order:
1. ${PYTORCH_REPO_ROOT}/aten/src/ATen/native/ - Native implementations
2. ${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/src/ATen/native/xpu/ - XPU-specific
3. ${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/src/ATen/native/transformers/sycl/ - SYCL kernels

For each file found:
- Read key sections (first 100 lines, important kernels)
- Identify kernel launch patterns
- Note any XPU fallback paths

Output:
- Implementation file paths
- Key kernel functions
- Fallback behavior if any""",
     subagent_type="explore")
```

#### Template 2: Test Case Investigation
```python
task(description="test_case_investigator",
     prompt=f"""
Investigate test case: <test_case_name> from TEST_PATH

Access test files:
- PyTorch tests: ${PYTORCH_REPO_ROOT}/test/
- torch-xpu-ops tests: ${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/test/xpu/

For the test case:
1. Read the full test method
2. Identify assertions and expectations
3. Note any setup/teardown requirements
4. Check for related helper functions

Output:
- Full test code
- Test assertions
- Expected behavior
- Related fixtures""",
     subagent_type="explore")
```

#### Template 3: Root Cause Deep Dive
```python
task(description="root_cause_explorer",
     prompt=f"""
Deep investigation for issue with ERROR_PATTERN

Areas to investigate:
1. Implementation files for failing operator
2. XPU-specific kernel implementations  
3. Test expectations
4. CPU fallback paths

Key locations:
- ${PYTORCH_REPO_ROOT}/aten/src/ATen/native/

- ${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/src/ATen/native/xpu/

- ${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/src/ATen/native/transformers/sycl/

For each investigation point:
- Read relevant code sections
- Identify potential fault locations
- Check data type handling
- Verify memory access patterns

Output:
- Potential root causes
- Code evidence
- Investigation notes""",
     subagent_type="explore")
```

## Deep Analysis Patterns

### Error Pattern -> Investigation Mapping

| Error Type | Indicators | Investigation | Common Root Causes |
|------------|-------------|----------------|-------------------|
| **SegmentationFault** | "page fault", "longjmp" | Buffer sizes, tiling | Large allocation >16K seq, uninitialized stack |
| **PrecisionError** | "nan", "inf", "accuracy" | Input dtypes, tolerance | Mixed precision, accumulator dtype mismatch |
| **APICompatibility** | "not implemented", "fallback" | XPU fallback detection | Missing XPU implementation, CPU path assumed |
| **WarningMismatch** | "Expected X got Y" | Warning generation paths | Extra internal operations, copyback issues |
| **DriverHardware** | "gpu aborted", "device error" | Driver version check | IGC bug, hardware limitation |

### Test Code Access Patterns

```python
# Pattern 1: Access PyTorch upstream test
def access_pytorch_test(test_module: str, line_offset: int, limit: int) -> str:
    """
    Access PyTorch test file.
    
    Args:
        test_module: Module name (e.g., "test_linalg.py")
        line_offset: Line to start reading from
        limit: Number of lines to read
        
    Returns:
        Test file content
    """
    test_path = f"${PYTORCH_REPO_ROOT}/test/{test_module}"
    return read(filePath=test_path, offset=line_offset, limit=limit)

# Pattern 2: Access torch-xpu-ops test
def access_xpu_ops_test(test_module: str, line_offset: int, limit: int) -> str:
    """
    Access torch-xpu-ops test file.
    
    Args:
        test_module: Module name (e.g., "test_transformers_xpu.py")
        line_offset: Line to start reading from
        limit: Number of lines to read
        
    Returns:
        Test file content
    """
    test_path = f"${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/test/xpu/{test_module}"
    return read(filePath=test_path, offset=line_offset, limit=limit)

# Pattern 3: Search test methods
def search_test_methods(test_file: str, method_pattern: str):
    """
    Search for test methods in test files.
    
    Usage:
        search_test_methods("test_linalg.py", "def test_cond")
    """
    grep(pattern=method_pattern, 
         path=f"${PYTORCH_REPO_ROOT}/test/{test_file}",
         include="*.py")
```

### Version-Aware Analysis

```python
def analyze_with_explore_and_version(issue_data: dict, env_info: dict) -> dict:
    """
    Analysis using explore agent and version context.
    
    Steps:
    1. Use explore to find relevant code
    2. Check versions for compatibility
    3. Analyze based on explore findings
    """
    
    # Step 1: Explore for relevant implementations
    explores = task(description="find_relevant_code",
                    prompt=f"""
Find implementations and tests for OPERATOR related to issue.

Check:
- ${PYTORCH_REPO_ROOT}/aten/src/ATen/native/ for native code
- ${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/ for XPU code
- Both test directories

Return paths and key findings""",
                    subagent_type="explore")
    
    # Step 2: Version compatibility on analysis
    analysis = {
        "explore_findings": explores,
        "version_compatible": True,
        "confidence": "medium",
        "test_access_info": {}
    }
    
    # Step 3: Access test code
    if "test_linalg" in issue_data.get("test_file", ""):
        analysis["test_access_info"]["pytorch_test"] = read(
            filePath="${PYTORCH_REPO_ROOT}/test/test_linalg.py",
            offset=issue_data.get("line", 1700),
            limit=100
        )
    
    return analysis
```

## Fix Suggestion Templates

### Template 1: Sequence Length Threshold
```cpp
// For memory allocation crashes
if (seq_len > MAX_SEQ_LEN_THRESHOLD) {
    TORCH_WARN("Large sequence detected, using math backend");
    return math_backend_path;
}
```

### Template 2: Dtype Promotion Fix
```cpp
// For precision issues
auto acc_dtype = at::accumulate_type<input_scalar_t, acc_scalar_t>::type;
auto query_promoted = query.to(acc_dtype);
```

### Template 3: Warning Suppression
```cpp
// For extra warning generation
if (out_tensor_provided && shapes_match(existing_out, result)) {
    // Skip unnecessary resize operation
    return existing_out;  // No copy needed
}
```

## Output Format

### Required Sections in Report

```markdown
# Triage Report - Issue #{issue}

## Issue Summary
[One sentence description]

## 1. Version Table
| Component | Issue Ver | Env Ver | Compatible |
|-----------|-----------|---------|------------|
| PyTorch | X.Y.Z | X.Y.Z | Yes/No |
| XPU Driver | X.XX | X.XX | Yes/No |
| Triton | X.X.X | X.X.X | Yes/No |

## 2. Reproduce Info
[Test case / command reference]

## 3. Code Exploration Findings
[Explore agent results for implementation and test files]

## 4. Root Cause Analysis
[Deep analysis with evidence]

## 5. Fix Suggestions
[Expert-level suggestions]

## 6. Priority & Labels
[Recommended priority and labels]
```

## Usage Example

### Complete Triage Run with Explore
```bash
# Step 1: Fetch issue
webfetch(url="https://github.com/intel/torch-xpu-ops/issues/3394", format="markdown")

# Step 2: Extract test case from issue
# Test case: test_cond_errors_and_warnings_xpu_float64

# Step 3: Explore agent for code investigation
task(description="sdpa_crash_investigation",
     prompt="Investigate sdpa crash issue:\n1. Find SDPA implementations in ${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops/\n2. Locate related test files\n3. Identify kernel launch patterns for large sequences",
     subagent_type="explore")

# Step 4: Access test code
read(filePath="${PYTORCH_REPO_ROOT}/test/test_linalg.py", offset=1735, limit=50)

# Step 5: Execute reproduce if compatible
bash(command="source ~/miniforge3/bin/activate pytorch_opencode_env && python -c '<reproduce>'")

# Step 6: Generate comprehensive report
```

## Category Analysis

Apply SKILL_Category_Analysis.md only after the issue has gone through triage_skills root-cause analysis and fix_approach drafting. Category assignment uses the root cause, failing component, and source/test evidence; it must not run as an early keyword-only pass.

```python
# Final Step: Category Analysis (after root_cause and fix_approach)
task(description="category_analysis",
     prompt="Analyze issue for category classification using the 11-bucket taxonomy in SKILL_Category_Analysis.md:\n\nDecision order (first match wins):\n1. Distributed - XCCL, NCCL, DDP, FSDP, DTensor, [distributed] tag\n2. Flash Attention - flash_attention, SDPA, scaled_dot_product_attention\n3. Inductor - torch.compile, Triton codegen, Dynamo, FakeTensor\n4. TorchAO - torchao, quantize_, int4/int8, fp8\n5. Sparse - sparse tensor, BSR, CSR, COO\n6-9. Torch Ops - {gemm|eltwise|reduction|others} - aten/native ops; pick sub-bucket per the selector in SKILL_Category_Analysis.md\n10. Torch Runtime - torch.xpu.*, OOM, memory, streams, profiler\n11. Others - CI/infra/doc/meta-tracking\n\nAnalyze issue text, stack trace, and code patterns\nto determine the single canonical category value.",
     subagent_type="explore")

# Integrate with triage report (illustrative keyword hints — NOT a substitute
# for the full decision-order rubric in SKILL_Category_Analysis.md; sub-bucket
# selection for Torch Ops - * MUST follow the gemm/eltwise/reduction/others
# selector defined there)
CATEGORIES = {
    "Distributed": ["ProcessGroup", "NCCL", "XCCL", "DDP", "FSDP", "DTensor"],
    "Flash Attention": ["scaled_dot_product", "sdpa", "flash_attention"],
    "Inductor": ["torch.compile", "Dynamo", "AOTAutograd", "Triton"],
    "TorchAO": ["torchao", "quantize_", "int4", "int8", "fp8"],
    "Sparse": ["sparse", "BSR", "CSR", "COO"],
    "Torch Ops - gemm": ["matmul", "mm", "bmm", "addmm", "conv", "linalg.solve", "cholesky", "svd"],
    "Torch Ops - eltwise": ["add", "mul", "clamp", "batch_norm", "layer_norm", "activation"],
    "Torch Ops - reduction": ["sum", "mean", "std", "var", "norm", "topk", "argmax", "all", "any"],
    "Torch Ops - others": ["index", "scatter", "gather", "reshape", "fft", "pool", "sort"],
    "Torch Runtime": ["OOM", "page fault", "drm_neo", "torch.xpu", "streams", "profiler"],
    "Others": ["CI", "docs", "test infra", "tracking"],
}
```

## Priority Analysis

Apply SKILL_Priority_Analysis.md only after root_cause and fix_approach are drafted. Priority assignment uses the verified failure mode and impact from triage; it must not run before the issue is understood. If the row already has a non-blank `Priority`, preserve it as the GitHub Projects labeled priority and skip computed priority assignment.

```python
# Final Step: Priority Analysis (after root_cause and fix_approach)
# Uses verified triage evidence across multiple dimensions:
# - Error type (40%): Fatal/Error/Warning
# - Test failures (30%): Many/few failures
# - Regression (20%): Was passing, now failing
# - Performance (20%): >5% = P0
# - Custom impact (40%): Production model

# Priority levels:
# - P0: Critical (crash, segfault, >5% perf, custom model)
# - P1: High (UT >20 failures, regression)
# - P2: Medium (E2E issues, few failures)
# - P3: Low (minor, cosmetic)

# Example: SDPA crash would be P0
# Example: Warning mismatch would be P3
```

## Related Skills

| Skill | File | Purpose |
|-------|------|---------|
| Priority Analysis | SKILL_Priority_Analysis.md | Final priority assignment after root cause/fix approach |
| Category Analysis | SKILL_Category_Analysis.md | Final category assignment after root cause/fix approach |
| Deep Analysis | SKILL_Deep_Analysis_Patterns.md | Multi-dimension analysis logic |
| Domain Patterns | SKILL_Domain_Patterns.md | Quick reference & tools |
| Mini Reproducer | SKILL_Mini_Reproducer.md | STEP 3.5: write & verify minimal Python reproducer |
| Issue Extraction | SKILL.md (in parent) | Basic issue collection |

## Scripts (in this folder)

| Script | Purpose |
|---|---|
| [`run_needs_owner_fix.py`](./run_needs_owner_fix.py) | Post-triage repair: for rows tagged `NEEDS_OWNER` that actually have an Assignee, reclassify to `ROOT_CAUSE` (or drop from IMPLEMENT combos). Uses `Path(__file__).resolve().parents[7]` to anchor on the repo root, so it runs from any CWD. |

Typical run:
```bash
python3 opencode/issue_triage/.opencode/skills/bug_scrub/analyze_issue/triage_skills/run_needs_owner_fix.py
```

---

## Skill Metadata

- **Version**: 1.4.1
- **Created**: 2026-04-20
- **Updated**: 2026-05-23 (formal Blank Cell Definition treats string `"None"` as blank — covers legacy Phase 1 sentinel that defeated Incremental-Mode refill of `Dependency`)
- **Compatibility**: PyTorch 2.12+, torch-xpu-ops 2.10+
- **Requires**: GitHub access, Conda pytorch_opencode_env, explore agent
