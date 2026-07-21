---
name: issue-priority
description: Assign a P0-P3 priority to a triaged GitHub issue (pytorch or torch-xpu-ops) via deep semantic analysis of failure mode, crash location, and performance-regression magnitude, given the JSON output of issue-target-component plus extract-basic-info. Applies the Priority Decision Tree (crash on a Core API or a measured over-7-percent perf regression maps to P0; hang/regression/6-plus UT failures maps to P1; 1-6 UT failures or non-crash functional errors maps to P2; enhancement/cosmetic/CUDA-alignment maps to P3) with engineering judgment, not keyword matching. Preserves an existing GitHub Projects Priority field verbatim when already set. Use after issue-target-component has produced a root cause, when you need the Excel "Priority" column value for that issue.
---

# Issue Priority Analysis

Deep semantic priority assessment of one triaged issue (pytorch or
torch-xpu-ops) into **P0-P3**, using the JSON verdict produced by
`issue-triage/triage-issue/issue-target-component` as primary evidence.
Priority assignment requires reading the full failure context and
applying the decision tree below with human-level judgment — not
regex/keyword matching against the title.

You **analyze only**; never edit files, `git commit`, open PRs, or mutate
GitHub issue state (including the Priority project field).

## Inputs

| Input | Required | Notes |
|---|---|---|
| `root_cause_result` | required | JSON output of `issue-target-component` — see "Adapting `issue-target-component` output" below: `root_cause`, `evidence.traced_symbols`, `evidence.call_path`, `target_component`, `third_party_dependency`, `verdict`. |
| `issue_info` | optional but recommended | `extract-basic-info` output for the same issue: `title`, `labels`, `priority` (existing GitHub PyTorchXPU project field, P0-P3 or `""`), `traceback`, `test_file`/`test_class`/`test_case` (or `test_cases[]`), `reproduce_steps`. Used to corroborate failure-mode evidence and to check the priority-preservation rule below. |
| `conda_env` | optional | Not required for text-based priority reasoning. Accepted only for pipeline consistency with callers that also invoke `reproduce-issue` — a caller MAY pass it through if it wants a future extension to re-confirm a cited perf number or UT failure count live rather than trusting the issue body verbatim. This skill does not itself run any command in `conda_env`. |
| `pytorch_folder` | optional | Same rationale as `conda_env` — accepted for pipeline consistency, not required or used for analysis. |

### Adapting `issue-target-component` output

`issue-target-component`'s output has no dedicated `domain` field
directly, but carries everything needed to reconstruct the failure-mode
evidence this skill needs:

- `root_cause`, `evidence.traced_symbols`, `evidence.call_path` — used as-is.
- `target_component` — informs whether a crash/regression lands on
  torch-xpu-ops/pytorch product code (in-repo, potentially P0/P1) vs a
  test-case defect (rarely P0) vs third-party (defer to
  `third_party_dependency.evidence` for the failure description).
- `verdict` — `NEED_FIX`/`NEED_FIX_CASE` imply a concrete reproducible
  failure exists (candidate for any priority); `NEED_HUMAN` with no
  `failure_signature` means priority must fall back to `issue_info`
  (title/labels) alone — see Step 4 confidence rule.

## Priority Definitions

### P0 - Critical

**P0 is assigned ONLY when BOTH conditions are met:** no — assigned when
**EITHER** of these two conditions holds:

1. **Performance regression >7%** (measured, quantified, between releases), OR
2. **Crash/Segfault on a Core API** (from the list below)

#### Core API List (Crashes Here = P0)

**Module loading:** `import torch`, `import torch.nn`

**Tensor lifecycle:** `torch.tensor()`, `torch.zeros()`, `torch.ones()`,
`torch.empty()`, `torch.rand()`, `.clone()`, `.contiguous()`

**Device transfer:** `.to(device)`, `.cuda()`, `.cpu()`, `.xpu()`

**Basic arithmetic:** `+`, `-`, `*`, `/`, `@` (matmul) on standard dtypes
(float32/float64/int64)

**Autograd core:** `.backward()`, `torch.autograd.grad()` on a <=3-op graph

**Key module forward/backward:** `nn.Linear` forward/backward, `nn.Conv2d`
forward/backward

**Serialization:** `torch.save()`, `torch.load()`

**Basic indexing:** `tensor[idx]`, `.view()`, `.reshape()`

| Condition | How to verify | Examples |
|---|---|---|
| Performance regression >7% | Issue body/comments cite a measured % drop AND the regression is confirmed between releases. Must be >7%, not <=7% | 15% slower on 2.12 vs 2.11 -> yes; 5% slower -> no |
| Crash on Core API | Stack trace shows SIGSEGV/access violation on a listed Core API; or segfault during `import torch`, tensor creation, `.backward()`, `.to(device)`, matmul, `nn.Linear`/`.Conv2d` forward, `torch.save`/`load`, or indexing | SIGSEGV in `torch.tensor()` -> yes; crash in a custom op -> no |
| (Legacy) Torch build failure | CI log shows compilation/linker errors preventing a build | Compilation failures, linker errors |

### P1 - High

| Condition | How to verify | Examples |
|---|---|---|
| UT >6 failed cases | Count distinct test case names in issue body/title; meta-tracking issues listing many cases | Large test-class failures |
| Regression (was passing, now failing) | Issue cites a specific version where it passed AND the current version where it fails | "Passed on 2.10, fails on 2.11" |
| Hang / timeout (process alive but stuck) | Issue describes a 300s timeout, infinite wait, deadlock | Distributed test hangs |
| Accuracy regression on benchmark | Benchmark accuracy was passing in a prior release, now fails (but not crash) | fail_accuracy on an E2E model |

### P2 - Medium

| Condition | How to verify | Examples |
|---|---|---|
| Benchmark performance <=5% (through 7%) | Measured perf drop cited but <=7% | Minor throughput decrease |
| UT 1-6 failures | Small number of related test failures | A few op tests failing |
| Functional errors (not crash) | RuntimeError, AssertionError, wrong result but process continues | Wrong output, type errors |
| Feature gap blocking tests | Tests fail because an API is not implemented, but no crash | "not implemented" errors |

### P3 - Low

| Condition | How to verify | Examples |
|---|---|---|
| Enhancement / feature request | Title contains "implement", "enable", "support", "RFC", "consider", "investigate", or body describes desired new functionality | Feature requests |
| Validation / error-message difference | XPU raises a different error message than CPU/CUDA, or doesn't raise where it should, but no incorrect computation | Error message mismatch |
| Minor / cosmetic / warning | Warning mismatch, deprecated API usage, documentation gaps | Warning mismatches |
| Alignment with CUDA (no functional break) | XPU behavior differs from CUDA but isn't incorrect per se | dtype support alignment |

## Workflow

### Step 1 - Priority preservation check (authoritative existing value wins)

If `issue_info.priority` is non-blank (already set via the GitHub
PyTorchXPU project's `Priority` field), that value is authoritative:

- Set `priority = issue_info.priority` verbatim, `priority_source =
  "existing"`.
- Still run Steps 2-3 to produce `priority_reason`/`evidence` for
  transparency (so the output explains *why* that priority makes sense),
  but never override the existing value with a computed one.

Only when `issue_info.priority` is blank/absent (or `issue_info` was not
provided) does the computed decision tree in Step 2 decide `priority`
itself (`priority_source = "computed"`).

### Step 2 - Read the full context

Do NOT rely on title keywords alone. Read (in order of preference):
`root_cause_result.root_cause`, `evidence.traced_symbols`/`call_path`,
`issue_info.title`, `issue_info.labels`, `issue_info.traceback`,
`issue_info.reproduce_steps`.

### Step 3 - Apply the Priority Decision Tree (first match wins)

1. **Crash/segfault on a Core API?** Verify the stack trace shows
   SIGSEGV/access violation AND the failure is on a listed Core API. Yes ->
   **P0**. No -> continue.
2. **Measured performance regression >7%?** Read the actual numbers — a
   vague "performance dropped" without numbers is NOT sufficient; verify
   it's a regression between specific releases. Yes (confirmed >7%) ->
   **P0**. No or <=7% -> continue.
3. **Does the process hang indefinitely?** (timeout, deadlock, no
   response). Yes -> **P1** candidate. No -> continue.
4. **Is it a regression?** (confirmed working in version X, broken in
   version Y). Yes + accuracy/correctness issue -> **P1**. Yes + UT
   failures >6 -> **P1**. Yes + UT failures 1-6 -> **P1** (regression boost
   from P2). No -> continue.
5. **How many test cases fail?** >6 distinct cases -> **P1**. 1-6 cases ->
   **P2**. 0 (enhancement/feature only) -> continue to 6.
6. **Enhancement, feature request, or CUDA-alignment issue with no
   functional break?** -> **P3**.
7. Anything else that produces a non-crash functional error (wrong result,
   RuntimeError/AssertionError, feature gap blocking tests without a
   crash) -> **P2**.

### Step 3b - Edge case rules

- **Crash on non-Core-API**: custom ops, advanced kernels, or non-listed
  APIs -> NOT P0 by the crash criterion; check whether the >7% perf-
  regression criterion applies instead.
- **Performance <=7%**: even if it's a regression, a <=7% drop -> P2, not
  P0.
- **`ai_generated` issues**: judge by the actual failure mode, never by the
  `ai_generated` label itself. Crash on Core API -> P0. Wrong result -> P2.
  Validation/error-message difference -> P3.
- **"Bug Skip" tracking/meta issues**: count the actual failing cases
  inside. Don't assume P1 just because the title says "new failures".
- **Performance without regression**: if perf is slow but was ALWAYS slow
  (no prior version was faster), that's P2 (improvement request), not
  P0/P1.
- **Benchmark vs custom model**: benchmark issues follow the same Core API
  rule; custom-model impact follows the same rules (no special boost).
- **Distributed feature gaps**: "not implemented" / "does not support"
  without a crash = P2, not P1.
- **`root_cause_result.verdict == "NEED_HUMAN"` / `"NEEDS_HUMAN"` with no
  failure signature**: fall back to `issue_info.title`/`labels` alone (Step
  6/enhancement path is the common outcome); mark `confidence = "Low"`.

### Step 4 - Generate the priority reason and set confidence

Write a concise (5-15 word) `priority_reason` tying the priority to the
specific evidence, e.g.:

- "Segfault on GPU page fault during SDPA with large seq_len"
- "15% perf regression on release/2.12 vs 2.11"
- "Regression: 245 conv tests pass on oneDNN 3.10, fail on 3.11"
- "3 UT accuracy failures in index_add bf16"
- "Enhancement: implement linalg.svd XPU backend"
- "Error message differs from CPU, no functional impact"

Confidence:
- `High` — priority derived directly from `root_cause_result.evidence`
  (traced symbols/call path, or a quantified perf number/failure count) for
  a non-`NEEDS_HUMAN` issue.
- `Medium` — priority inferred from `root_cause`/title text plus
  corroborating labels, without a fully traced symbol or exact count.
- `Low` — `verdict == "NEEDS_HUMAN"`/`"NEED_HUMAN"` with no failure
  signature available, and priority is inferred from the issue
  title/labels alone; also used whenever `priority_source == "existing"`
  but the computed reasoning could not independently corroborate it.

## Output

```python
{
    "source_issue": {"issue_id": int, "repo": str, "title": str},
    "priority": "P0" | "P1" | "P2" | "P3",
    "priority_source": "existing" | "computed",  # "existing" = preserved from issue_info.priority verbatim
    "priority_reason": str,      # 5-15 words, ties to specific evidence
    "evidence": {
        "failure_mode": "crash-core-api" | "perf-regression" | "hang"
                       | "regression" | "ut-failures" | "functional-error"
                       | "enhancement" | "cosmetic" | "alignment" | "other",
        "traced_symbols": [str],       # copied from root_cause_result.evidence, if present
        "measured_regression_pct": float,  # null if not a perf issue / not quantified
        "failing_case_count": int          # null if not a UT-count-driven case
    },
    "confidence": "High" | "Medium" | "Low"
}
```

## Anti-Patterns (DO NOT)

1. **DO NOT** classify as P0 just because the title contains "regression"
   or "crash" — verify the actual failure is on a Core API (listed above)
   or the performance drop is confirmed >7% (not <=7%).
2. **DO NOT** classify as P0 for crashes on non-Core APIs (custom ops,
   advanced kernels, experimental features). Those are P2 at best.
3. **DO NOT** classify as P1 just because there are "failures" — count
   them. 1-6 failures = P2.
4. **DO NOT** classify performance issues as P0 without a confirmed >7%
   measured drop (not <=7%) AND a regression between releases.
5. **DO NOT** give P1 to feature gaps / "not implemented" issues that
   don't crash.
6. **DO NOT** use keyword pattern matching as the primary classification
   method. Keywords are hints; understand the issue semantically.
7. **DO NOT** classify `ai_generated` validation/error-message issues as
   P1 just because they mention "crash" hypothetically — verify actual
   crash evidence in the reproduction.
8. **DO NOT** grant P0 for performance issues <=7% — use P2 instead.
9. **DO NOT** overwrite an existing non-blank `issue_info.priority` with a
   computed value — preserve it (Step 1).

## Hard rules

- Read-only/analysis-only: never edits files, `git commit`s, or mutates
  GitHub issue state (labels, project fields) — including the Priority
  field itself, even when this skill's computed value disagrees with it.
- `priority` MUST be one of `P0`/`P1`/`P2`/`P3` verbatim — this is an
  Excel-column enum, not free text.
- Apply the Priority Decision Tree in Step 3 in order; first match wins.
- Never fabricate a measured percentage or failure count that isn't
  actually present in the input evidence.

## Example

```bash
python3 .claude/skills/validation/issue-triage/extract-issue-information/scripts/extract_basic_info.py 4344 \
  > issue_info.json
# -> run issue-target-component with issue_info.json
#    + conda_env + pytorch_folder to get root_cause_result.json
# -> feed root_cause_result.json (and issue_info.json) as this skill's input
```

## Scope

One issue per invocation (mirrors `issue-target-component` scope).
Read-only: no edits, no `git commit`, no `gh` mutation.
</content>
