# Priority Refinement v3.0.0

## Overview

This document describes the refined P0 priority criteria effective from v3.0.0 of the bug_scrub triage skills.

## What Changed

### Previous Logic (v2.x)
- P0: Any crash/segfault, OR >5% perf regression, OR custom/production model impact
- P1: UT >6 failures, regression, or hang
- P2: 1-6 failures, functional errors, ≤5% perf regression
- P3: Enhancement, alignment, or cosmetic issues

### New Logic (v3.0+)
- **P0 is MUCH MORE RESTRICTIVE**: Only crashes on Core APIs OR >7% perf regression (was >5%)
- **Core API List**: Explicitly defined (tensor lifecycle, device transfer, arithmetic, autograd, nn.Linear/.Conv2d, serialization, indexing)
- **Non-Core-API Crashes**: Now classified as P2 (not P0)
- **Performance Threshold**: Raised from >5% to >7%

## P0 Criteria (v3.0)

### Criterion 1: Crash/Segfault on Core API

**Must have BOTH:**
1. Crash/segfault evidence (SIGSEGV, access violation, stack trace)
2. Failure occurs on one of these Core APIs:

#### Core API List

**Module Loading:**
- `import torch`
- `import torch.nn`

**Tensor Lifecycle:**
- `torch.tensor()`, `torch.zeros()`, `torch.ones()`, `torch.empty()`, `torch.rand()`
- `.clone()`, `.contiguous()`

**Device Transfer:**
- `.to(device)`, `.cuda()`, `.cpu()`, `.xpu()`

**Basic Arithmetic:**
- `+`, `-`, `*`, `/`, `@` (matmul) on standard dtypes (float32/float64/int64)

**Autograd Core:**
- `.backward()`
- `torch.autograd.grad()` on a ≤3-op graph

**Key Module Forward/Backward:**
- `nn.Linear` forward/backward
- `nn.Conv2d` forward/backward

**Serialization:**
- `torch.save()`
- `torch.load()`

**Basic Indexing:**
- `tensor[idx]`
- `.view()`
- `.reshape()`

### Criterion 2: Performance Regression >7%

**Must have ALL:**
1. Measured performance drop (actual numbers cited, not "performance dropped")
2. Drop is **strictly >7%** (not ≤7%)
3. Regression confirmed between specific releases (e.g., "15% slower on 2.12 vs 2.11")

## Examples

### ✅ P0 Cases

| Issue | Core API | Perf Drop | Status |
|-------|----------|-----------|--------|
| SIGSEGV in `torch.tensor()` | Tensor Lifecycle | — | **P0** |
| Crash in `.backward()` on simple graph | Autograd | — | **P0** |
| 12% perf regression v2.12 → v2.13 | (any) | >7% | **P0** |
| Segfault in `nn.Linear.forward()` | nn.Linear | — | **P0** |

### ❌ Not P0 Cases

| Issue | Reason |
|-------|--------|
| Crash in custom CUDA kernel | Not on Core API list |
| Crash in advanced feature (sparse, inductor) | Not on Core API list |
| 5% perf regression | Performance drop ≤7% → P2 |
| 7% perf regression | Exactly 7% is NOT >7% → P2 |
| Crash in `torch.sparse.tensor()` | Sparse APIs not on Core list |
| Crash in `torch.compile()` | Inductor/PT2 not on Core list |
| Crash in custom op | Not on Core API list |

## Impact on Triage Workflow

### Phase 3 (Analyze Issues)

When using `triage_skills/SKILL_Priority_Analysis.md`:

1. **Read the issue fully** (title, body, stack trace, comments)
2. **Apply decision tree**:
   - Is it a crash on Core API? → P0
   - Is it >7% perf regression? → P0
   - Is there a hang/deadlock? → P1
   - Is it a regression with failures? → P1
   - How many test failures? (>6 → P1, 1-6 → P2, 0 → P3)
   - Is it an enhancement? → P3

3. **Validate against Core API list** before assigning P0 for crashes

### Phase 1.1 (Generate Excel)

No changes — Priority column in Phase 1 is populated from GitHub Projects `PyTorchXPU Priority` field. Phase 1.1 does not compute priority; it imports from the issue's labeled priority.

### Phase 4 (Action Reason Collection)

When computing Action Reason, use the refined P0 threshold:
- >7% (not ≤7%) for performance regressions
- Only crashes on Core APIs qualify as P0 crashes

## Backward Compatibility

### Existing Excel Data

Issues already triaged under v2.x with P0 priority remain P0 in the workbook (Phase 3.3 preserves non-blank `Priority` cells). However:

- Future retriage of these issues should re-evaluate against v3.0 criteria
- If a "P0 crash" is on a non-Core-API, it should be reclassified to P2 on next triage
- If a "P0 perf regression" is ≤7%, it should be reclassified to P2 on next triage

### Migration Path

To migrate existing workbooks to v3.0 logic:

1. Identify all P0 issues in `result/torch_xpu_ops_issues.xlsx`
2. For each P0 issue:
   - Check if crash is on Core API (from list above)
   - Check if perf regression is >7% (not ≤7%)
   - If neither, change Priority to P2
3. Save and re-run Phase 4 to regenerate reports

## Files Modified

- `SKILL_Priority_Analysis.md` (v2.0 → v3.0): Updated P0 definitions, Core API list, decision tree
- `SKILL.md` (line ~102): Updated Priority Taxonomy summary
- `PRIORITY_REFINEMENT_v3.md` (NEW): This document

## Related Skills

- `SKILL_Triage_Logic.md`: Core triage workflow (unchanged)
- `SKILL_Category_Analysis.md`: Category assignment (unchanged)
- `SKILL_Domain_Patterns.md`: Reference guide (unchanged)
- `SKILL.md`: Main skill documentation (priority taxonomy updated)

## Questions & FAQs

### Q: My crash issue is not on the Core API list. Is it P0?
**A:** No. Only crashes on the listed Core APIs are P0. Other crashes are P2 (or lower depending on context).

### Q: Is a 7% perf regression P0?
**A:** No. The threshold is **strictly >7%**, not ≥7%. Use P2 for ≤7% regressions.

### Q: What about crashes in opaque frameworks built on PyTorch (PyG, detectron2)?
**A:** If the crash occurs within torch-xpu-ops code (SYCL kernel, device transfer, etc.), it may be P0 if on Core API. If it's in third-party code, it's likely P3 (out of scope).

### Q: Does the Core API list ever change?
**A:** Only via explicit skill version update. Current list is final for v3.0. Requests for additions go through formal review.

## Version History

- **v3.0.0** (2026-06-01): Initial release with refined P0 criteria
  - P0 limited to Core API crashes + >7% perf regression
  - Explicit Core API list added
  - Performance threshold raised from >5% to >7%
