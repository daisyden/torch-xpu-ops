# Phase 4b Real Example: Open Issue #3727 with Open PR #3729

> Generated: May 23, 2026
> This is a real example from the intel/torch-xpu-ops repository demonstrating the Phase 4b workflow.

---

## Issue #3727 (OPEN)

```
Title: "[Bug Skip]: RuntimeError: No viable backend for scaled_dot_product_attention was found."
State: OPEN
Labels: ["module: ut", "skipped"]

Failing test case:
- test_op_has_batch_rule_count_nonzero_xpu_float32
```

---

## Step 1: Discovery — REAL DATA

| Vector | Method | Command | Result |
|--------|--------|---------|--------|
| **Vector A** (Timeline) | Tool | `gh api .../issues/3727/timeline` | **[PR #3729]** |

```bash
# Actual command:
gh api repos/intel/torch-xpu-ops/issues/3727/timeline --jq \
  '.[] | select(.event == "cross-referenced") | 
   select(.source.issue.pull_request != null) | 
   {number: .source.issue.number, state: .source.issue.state}'

# Result:
{"number": 3729, "state": "open", "title": "Fix stale count_nonzero xfail..."}
```

**Candidates**: `[PR #3729]`

---

## Step 2: Verification — REAL DATA

### PR #3729: **VERIFIED** via Path 1 (Explicit Reference)

```
Method: Script (string matching)

Actual PR #3729 body:
"This patch fixes #3727 by aligning XPU test expectations..."
                  ^^^^^
                  Explicit reference!

Verification:
├── Strip excluded sections (### Versions, code blocks): N/A (none present)
├── Search for "#3727" in body
├── Found: "fixes #3727" ← MATCH
└── Verdict: VERIFIED (explicit_reference)
```

**Why VERIFIED by Script**: PR body contains `fixes #3727` — simple string match, no LLM needed.

---

## Step 2.5: Live PR-State Re-Check — REAL DATA

```bash
# Actual command:
gh pr view 3729 --repo intel/torch-xpu-ops --json state,mergedAt

# Result:
{
  "state": "OPEN",      ← Still open, not merged
  "mergedAt": null
}
```

| PR | Live State | Method | Verdict |
|----|------------|--------|---------|
| #3729 | **OPEN** | Tool (`gh pr view`) | "Land PR intel/torch-xpu-ops#3729" |

**State-Precedence Rule** (Rule-Based): OPEN → emit `"Land PR <ref>"` + analyze gates

---

## Step 3: Deep PR Analysis (Gate Checks) — REAL DATA

Since PR is **OPEN**, we need to analyze all 4 gates:

### Gate 1: Waiting for Resolving (Unresolved Comments)

```bash
# Check for unresolved review threads
gh pr view 3729 --json comments,reviewThreads

# Result:
{"comments": [], "reviewThreads": []}
```

| Gate 1 | Method | Status | Evidence |
|--------|--------|--------|----------|
| Unresolved Comments | Tool (`gh pr view`) | **PASS** ✓ | No unresolved threads |

---

### Gate 2: Waiting for Review

```bash
# Check review status
gh pr view 3729 --json reviewDecision,reviews

# Result:
{
  "reviewDecision": "REVIEW_REQUIRED",   ← Blocking!
  "reviews": []                          ← No reviews yet
}
```

| Gate 2 | Method | Status | Evidence |
|--------|--------|--------|----------|
| Review Required | Tool (`gh pr view`) | **FAIL** ✗ | `reviewDecision: REVIEW_REQUIRED`, 0 approvals |

**Action Required**: PR needs review/approval

---

### Gate 3: Waiting for CI

```bash
# Check CI status
gh pr view 3729 --json statusCheckRollup | jq 'group_by(.conclusion)'

# Result:
[
  {"conclusion": "FAILURE", "count": 3},   ← 3 failures!
  {"conclusion": "SKIPPED", "count": 11},
  {"conclusion": "SUCCESS", "count": 9}
]

# Failing checks:
- "linux-ut (op_ut) / summary"
- "linux-ut (basic) / test-in-container"
- "linux-ut (basic) / summary"
```

| Gate 3 | Method | Status | Evidence |
|--------|--------|--------|----------|
| CI Checks | Tool (`gh pr view`) | **FAIL** ✗ | 3 failures: `linux-ut (op_ut)`, `linux-ut (basic)` |

**Action Required**: Fix CI failures

---

### Gate 4: Waiting for Merge

| Gate 4 | Method | Status | Evidence |
|--------|--------|--------|----------|
| Ready to Merge | Rule-Based | **BLOCKED** | Gate 2 (review) and Gate 3 (CI) both failing |

---

## Complete Flow Diagram

```
Issue #3727: "[Bug Skip]: RuntimeError: No viable backend for SDPA..."
State: OPEN

Step 1: Discovery
├── Vector A (Timeline):                              ← Tool (gh api)
│   └── Cross-refs found: [PR #3729]
└── Candidates: [#3729]

Step 2: Verification
└── PR #3729:                                         ← Script (string match)
    ├── Body: "This patch fixes #3727..."
    ├── Found "fixes #3727" → VERIFIED (explicit_reference)
    └── Verdict: VERIFIED

Step 2.5: Live Re-Check
└── PR #3729: state=OPEN, mergedAt=null               ← Tool (gh pr view)
    └── State-Precedence: OPEN → analyze gates        ← Rule-Based

Step 3: Deep Analysis (4 Gates)
├── Gate 1 (Resolving):                               ← Tool (gh pr view)
│   └── comments: [], reviewThreads: []
│   └── Status: PASS ✓
│
├── Gate 2 (Review):                                  ← Tool (gh pr view)
│   └── reviewDecision: "REVIEW_REQUIRED"
│   └── reviews: [] (0 approvals)
│   └── Status: FAIL ✗ → ACTION: "Need review approval"
│
├── Gate 3 (CI):                                      ← Tool (gh pr view)
│   └── 3 FAILURE, 11 SKIPPED, 9 SUCCESS
│   └── Failing: linux-ut (op_ut), linux-ut (basic)
│   └── Status: FAIL ✗ → ACTION: "Fix CI failures"
│
└── Gate 4 (Merge):                                   ← Rule-Based
    └── Blocked by Gate 2 + Gate 3
    └── Status: BLOCKED

Step 4: Output
├── action_TBD: "Land PR intel/torch-xpu-ops#3729; 
│                Address CI failures; 
│                Waiting for review"
├── action_reason: "PR #3729 fixes #3727 but has 3 CI failures 
│                   (linux-ut) and needs review approval"
└── owner_transferred: @pponikox (PR author)
```

---

## Summary: Methods Used in Each Step

| Step | What Happened | Method |
|------|---------------|--------|
| **1. Discovery** | Timeline API found PR #3729 references #3727 | **Tool** (`gh api`) |
| **2. Verification** | PR body contains "fixes #3727" | **Script** (string match) |
| **2.5. State Check** | PR is OPEN, not merged | **Tool** (`gh pr view`) |
| **3. Gate 1** | No unresolved comments | **Tool** (`gh pr view`) |
| **3. Gate 2** | Review required, 0 approvals | **Tool** (`gh pr view`) |
| **3. Gate 3** | 3 CI checks failing | **Tool** (`gh pr view`) |
| **3. Gate 4** | Blocked by Gate 2 + 3 | **Rule-Based** (logic) |
| **4. Output** | Generate structured AR | **Script** (template) |

---

## Method Type Legend

| Method Type | When Used | Example |
|-------------|-----------|---------|
| **Tool** (`gh api`, `gh pr view`) | GitHub API queries | Fetch timeline, PR state, CI status |
| **Script** (string match, regex) | Deterministic pattern matching | Find `#3727` in PR body |
| **Rule-Based** | Conditional logic | State-precedence (MERGED > OPEN > CLOSED) |
| **LLM/Agent** | Semantic understanding | Content-match verification (not needed here) |

---

## What Would Require LLM/Agent?

In this real example, **no LLM was needed** because:
1. PR #3729 has explicit `fixes #3727` → Path 1 verification succeeded
2. All gate checks use GitHub API data → deterministic

**LLM would be needed if:**
- PR had no `#3727` reference but fixed the same issue (content-match via Path 2)
- Issue comments needed semantic analysis (Part 2: AR from Comments)
- Not-target decision needed reasoning (Part 0: Not-Target Check)

---

## Raw Data Captured

### Issue #3727
```json
{
  "number": 3727,
  "title": "[Bug Skip]: RuntimeError: No viable backend for scaled_dot_product_attention was found.",
  "state": "OPEN",
  "labels": ["module: ut", "skipped"]
}
```

### PR #3729
```json
{
  "number": 3729,
  "title": "Fix stale count_nonzero xfail in XPU vmap OpInfo test",
  "state": "OPEN",
  "author": "pponikox",
  "body": "This patch fixes #3727 by aligning XPU test expectations with upstream behavior...",
  "reviewDecision": "REVIEW_REQUIRED",
  "reviews": [],
  "ci_summary": {
    "FAILURE": 3,
    "SKIPPED": 11,
    "SUCCESS": 9
  },
  "failing_checks": [
    "linux-ut (op_ut) / summary",
    "linux-ut (basic) / test-in-container",
    "linux-ut (basic) / summary"
  ]
}
```
