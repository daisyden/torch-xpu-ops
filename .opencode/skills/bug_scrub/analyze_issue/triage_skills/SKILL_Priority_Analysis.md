# Priority Analysis Skill

## Overview
This skill provides **deep semantic priority assessment** of torch-xpu-ops issues. Rather than relying on keyword pattern matching, the agent must read the full issue context, understand the failure mode, assess impact scope, and apply the priority definitions with human-level judgment.

---

## Priority Definitions

### P0 - Critical

**P0 is assigned ONLY when BOTH conditions are met:**

1. **Performance regression >7%** (measured, quantified, between releases), OR
2. **Crash/Segfault on Core API** (from the list below)

#### Core API List (Crashes Here = P0)

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
- `.backward()`, `torch.autograd.grad()` on a ≤3-op graph

**Key Module Forward/Backward:**
- `nn.Linear` forward/backward
- `nn.Conv2d` forward/backward

**Serialization:**
- `torch.save()`, `torch.load()`

**Basic Indexing:**
- `tensor[idx]`, `.view()`, `.reshape()`

| Condition | How to Verify | Examples |
|-----------|---------------|----------|
| Performance regression >7% | Issue body or comments cite measured % drop AND regression confirmed between releases. Must be >7%, not ≤7% | 15% slower on 2.12 vs 2.11 ✅, 5% slower ❌ |
| Crash on Core API | Stack trace shows SIGSEGV/access violation on one of the listed Core APIs; OR segfault during import torch, tensor creation, .backward(), .to(device), matmul, nn.Linear/.Conv2d forward, torch.save/load, or indexing | SIGSEGV in torch.tensor() ✅, crash in custom op ❌ |
| (Legacy) Torch build failure | CI log shows compilation/linker errors preventing a build | Compilation failures, linker errors |

### P1 - High

| Condition | How to Verify | Examples |
|-----------|---------------|----------|
| UT >6 failed cases | Count distinct test case names in issue body/title; meta-tracking issues listing many cases | Large test class failures |
| Regression (was passing, now failing) | Issue cites specific version where it passed AND current version where it fails | "Passed on 2.10, fails on 2.11" |
| Hang / timeout (process alive but stuck) | Issue describes 300s timeout, infinite wait, deadlock | Distributed test hangs |
| Accuracy regression on benchmark | Benchmark accuracy was passing in prior release, now fails (but not crash) | fail_accuracy on E2E model |

### P2 - Medium

| Condition | How to Verify | Examples |
|-----------|---------------|----------|
| Benchmark performance <5% | Measured perf drop cited but ≤5% | Minor throughput decrease |
| UT 1-6 failures | Small number of related test failures | A few op tests failing |
| Functional errors (not crash) | RuntimeError, AssertionError, wrong result but process continues | Wrong output, type errors |
| Feature gap blocking tests | Tests fail because API is not implemented, but no crash | "not implemented" errors |

### P3 - Low

| Condition | How to Verify | Examples |
|-----------|---------------|----------|
| Enhancement / feature request | Title contains "implement", "enable", "support", "RFC", "consider", "investigate", or body describes desired new functionality | Feature requests |
| Validation / error message difference | XPU raises different error message than CPU/CUDA, or doesn't raise where it should, but no incorrect computation | Error message mismatch |
| Minor / cosmetic / warning | Warning mismatch, deprecated API usage, documentation gaps | Warning mismatches |
| Alignment with CUDA (no functional break) | XPU behavior differs from CUDA but isn't incorrect per se | dtype support alignment |

---

## Deep Analysis Protocol

### Step 1: Read the Full Issue

Do NOT rely on title keywords alone. Read:
- Issue title
- Issue body (full error log, reproducer, description)
- Root cause analysis (if available in our triage data)
- Labels
- Comments (for maintainer diagnosis, version info)

### Step 2: Determine Failure Mode & Apply Priority Decision Tree

**Check P0 Conditions (in order):**

1. **Is there a CRASH/SEGFAULT on Core API?**
   - Verify stack trace shows SIGSEGV or access violation
   - Verify failure is on one of the Core APIs (listed above: tensor creation, .backward(), .to(), matmul, nn.Linear/.Conv2d, torch.save/load, indexing, import torch)
   - YES → **P0**
   - NO → continue

2. **Is there a measured PERFORMANCE REGRESSION >7%?**
   - Read the actual numbers. Must be >7%, not ≤7%.
   - "performance dropped" without numbers is NOT sufficient.
   - Verify it's a regression (compare between specific releases).
   - YES with >7% confirmed → **P0**
   - NO or ≤7% → continue

3. **Does the process hang indefinitely?** (timeout, deadlock, no response)
   - YES → **P1** candidate
   - NO → continue

4. **Is it a regression?** (confirmed working in version X, broken in version Y)
   - YES + accuracy/correctness issue → **P1**
   - YES + UT failures >6 → **P1**
   - YES + UT failures 1-6 → **P1** (regression boost from P2)
   - NO → continue

5. **How many test cases fail?**
   - >6 distinct cases → **P1**
   - 1-6 cases → **P2**
   - 0 (enhancement/feature) → **P3**

6. **Is it an enhancement, feature request, or alignment issue?**
   - YES → **P3**

### Step 3: Apply Edge Case Rules

- **Crash on non-Core-API**: If crash occurs on custom ops, advanced kernels, or non-listed APIs → NOT P0 by this criterion. Check if >7% perf regression applies.
- **Performance <7%**: Even if it's a regression, ≤7% drop → P2 (not P0).
- **ai_generated issues**: Judge by actual failure mode, not by the `ai_generated` label. If it crashes on Core API → P0. If wrong result → P2. If validation/error message difference → P3.
- **"Bug Skip" tracking issues**: Count the actual failing cases inside. Don't assume P1 just because title says "new failures".
- **Performance without regression**: If perf is slow but was ALWAYS slow (no prior version was faster), it's P2 (improvement request), not P0/P1.
- **Benchmark vs custom model**: Benchmark issues follow the same Core API rule. Custom model impact follows the same rules (no special boost).
- **Distributed feature gaps**: "not implemented" / "does not support" without crash = P2, not P1.

### Step 4: Generate Priority Reason

Write a concise (5-15 word) reason explaining the priority assignment. Examples:
- "Segfault on GPU page fault during SDPA with large seq_len"
- "15% perf regression on release/2.12 vs 2.11"
- "Regression: 245 conv tests pass on oneDNN 3.10, fail on 3.11"
- "3 UT accuracy failures in index_add bf16"
- "Enhancement: implement linalg.svd XPU backend"
- "Error message differs from CPU, no functional impact"

---

## Priority Assignment Workflow

If the input row already has a non-blank `Priority`, preserve that value. Phase 1 initializes `Priority` from the GitHub Projects `PyTorchXPU Priority` field, and that labeled priority is authoritative over computed priority. Only run computed priority assignment when the input row's `Priority` is blank.

## Anti-Patterns (DO NOT)

1. **DO NOT** classify as P0 just because title contains "regression" or "crash" — verify the actual failure is on Core API (listed above) or performance drop is >7% (not ≤7%).
2. **DO NOT** classify as P0 for crashes on non-Core APIs (custom ops, advanced kernels, experimental features). Those are P2 at best.
3. **DO NOT** classify as P1 just because there are "failures" — count them. 1-6 failures = P2.
4. **DO NOT** classify performance issues as P0 without confirmed >7% measured drop (not ≤7%) AND regression between releases.
5. **DO NOT** give P1 to feature gaps / "not implemented" issues that don't crash.
6. **DO NOT** use keyword pattern matching as the primary classification method. Keywords are hints; the agent must understand the issue semantically.
7. **DO NOT** classify ai_generated validation/error-message issues as P1 just because they mention "crash" in a hypothetical sense — verify actual crash evidence in the reproduction.
8. **DO NOT** grant P0 for performance issues ≤7% — use P2 instead.

---

## Skill Metadata

- **Version**: 3.0.0 (Refined P0 criteria)
- **Created**: 2026-04-20
- **Updated**: 2026-06-01
- **Requires**: Issue text, error log, root cause analysis, comments
- **Related Skills**: SKILL_Category_Analysis.md, SKILL_Triage_Logic.md

### Change History (v3.0.0)
- **P0 Redefinition**: Now ONLY crash on Core API OR performance regression >7%
- **Core API List**: Added explicit list of Core APIs (tensor lifecycle, device transfer, arithmetic, autograd, nn.Linear/.Conv2d, serialization, indexing)
- **Performance Threshold**: Increased from >5% to >7% for P0
- **Non-Core-API Crashes**: Crashes on non-Core APIs no longer qualify for P0
