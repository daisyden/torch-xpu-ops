# Phase 4b LLM-Based Example: Content-Match Verification

> Generated: May 23, 2026
> This example demonstrates when and how LLM/Agent is used in Phase 4b verification.

---

## Overview: When LLM is Needed

LLM/Agent (Explore Agent) is used when:
1. **Path 0** (GitHub-linked): PR not in `closedByPullRequestsReferences` ✗
2. **Path 1** (Explicit reference): PR body has no `#NNNN`, no `Fixes #NNNN`, no issue URL ✗
3. **Path 2** (Content-match): Must analyze if PR fixes the issue based on:
   - File overlap (PR files vs issue stack trace/mentioned files)
   - Symptom overlap (PR description vs issue error message)
   - Timing plausibility (PR created around issue time)

---

## Real Issue: #3723 (OPEN)

```
Title: "XPU AOTInductor package fails on complex RoPE-style computation 
        while equivalent real cos/sin implementation passes"
State: OPEN
Labels: []

Error in issue:
- "AssertionError: Torch not compiled with CUDA enabled"
- Operations: torch.polar, torch.view_as_complex, complex multiply, torch.view_as_real
- Context: AOTInductor package execution on XPU

Stack trace mentions:
- OSSProxyExecutor
- CUDA device hardcoded
```

---

## Scenario A: PR WITH Explicit Reference (Path 1 Succeeds)

### Upstream PR: pytorch/pytorch#184741

```
Title: "Fix hardcoded CUDA device in OSSProxyExecutor"
State: OPEN
Body: "Replace old aoti OSSProxyExecutor logic, that checked if 
       device_str == \"cpu\" and based on that used either CPU or 
       hardcoded \"CUDA:-1\", with just passing device_str to 
       OSSProxyExecutor instead of is_cpu...
       
       Fixes: https://github.com/intel/torch-xpu-ops/issues/3723"
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
              Explicit reference found!

Files changed:
- torch/csrc/inductor/aoti_runner/model_container_runner.cpp
- torch/csrc/inductor/aoti_torch/oss_proxy_executor.cpp
- torch/csrc/inductor/aoti_torch/oss_proxy_executor.h
```

**Verification Result**: VERIFIED via Path 1 (explicit_reference)
- Method: **Script** (string matching found `/issues/3723`)
- No LLM needed

---

## Scenario B: Hypothetical PR WITHOUT Explicit Reference (Path 2 Needed)

### Hypothetical PR: pytorch/pytorch#184800

Imagine a different PR that fixes the same issue but was authored by Copilot or 
a developer who didn't link it:

```
Title: "Make OSSProxyExecutor device-agnostic"
State: OPEN
Body: "This change removes the hardcoded CUDA assumption in the proxy executor
       and allows any device string to be passed through. This enables XPU and
       other non-CUDA accelerators to use AOTInductor packaging without hitting
       the 'Torch not compiled with CUDA enabled' assertion.
       
       Changes:
       - Remove is_cpu boolean check
       - Pass device_str directly instead of hardcoding CUDA:-1"
       
       (NO issue reference!)

Files changed:
- torch/csrc/inductor/aoti_torch/oss_proxy_executor.cpp
- torch/csrc/inductor/aoti_torch/oss_proxy_executor.h
```

---

### Step 2: Verification with LLM (Path 2)

Since Path 0 and Path 1 fail, invoke **Explore Agent** for content-match analysis:

```python
# Path 1 check (Script):
search_for("#3723") in pr_body  → NOT FOUND
search_for("/issues/3723") in pr_body  → NOT FOUND
search_for("Fixes #3723") in pr_body  → NOT FOUND
# Result: Path 1 FAILED → proceed to Path 2

# Path 2: LLM/Agent Content-Match
task(
    description="content_match_verification",
    subagent_type="explore",
    prompt="""
    CONTENT-MATCH VERIFICATION

    Issue #3723:
    - Title: "XPU AOTInductor package fails on complex RoPE-style computation"
    - Error: "AssertionError: Torch not compiled with CUDA enabled"
    - Context: OSSProxyExecutor, hardcoded CUDA device, XPU tensors
    - Symptom: AOTInductor fails on XPU with complex tensor ops

    PR #184800:
    - Title: "Make OSSProxyExecutor device-agnostic"
    - Body: "removes hardcoded CUDA assumption in proxy executor...
             enables XPU and other non-CUDA accelerators...
             without hitting 'Torch not compiled with CUDA enabled'"
    - Files: oss_proxy_executor.cpp, oss_proxy_executor.h

    ANALYZE:
    1. FILE OVERLAP: Do PR files match issue's mentioned components?
    2. SYMPTOM OVERLAP: Does PR address the same error/problem?
    3. TIMING: Is PR creation plausible relative to issue date?

    Return verdict: VERIFIED (content_match) or REJECTED
    """
)
```

---

### Explore Agent Analysis Output

```
CONTENT-MATCH ANALYSIS RESULT

1. FILE OVERLAP: YES ✓
   - Issue mentions: OSSProxyExecutor, hardcoded CUDA
   - PR modifies: oss_proxy_executor.cpp, oss_proxy_executor.h
   - Match: Same component (OSSProxyExecutor)

2. SYMPTOM OVERLAP: YES ✓
   - Issue error: "Torch not compiled with CUDA enabled"
   - PR addresses: "without hitting 'Torch not compiled with CUDA enabled'"
   - Match: Exact same error message

3. TIMING: PLAUSIBLE ✓
   - Issue created: 2026-05-18
   - PR created: 2026-05-20 (2 days after)
   - Window: Reasonable fix timeline

4. SEMANTIC MATCH: YES ✓
   - Issue: XPU tensors fail because code assumes CUDA
   - PR: Removes CUDA assumption, enables XPU
   - Root cause addressed

VERDICT: VERIFIED (content_match)
CONFIDENCE: HIGH

match_reasoning: "PR modifies the exact component mentioned in issue 
                  (OSSProxyExecutor), addresses the identical error message 
                  ('Torch not compiled with CUDA enabled'), and explicitly 
                  mentions enabling XPU - the device that's failing in the issue."
```

---

### Verification Output Schema

```json
{
  "pr_number": 184800,
  "repo": "pytorch/pytorch",
  "verdict": "VERIFIED",
  "verdict_source": "content_match",
  "verification_details": {
    "github_linked": false,
    "explicit_reference": false,
    "content_match": true,
    "match_reasoning": "PR modifies OSSProxyExecutor (component in issue), addresses identical error message, enables XPU support",
    "files_overlap": ["oss_proxy_executor.cpp", "oss_proxy_executor.h"],
    "symptom_match": "Torch not compiled with CUDA enabled",
    "timing_delta_days": 2
  }
}
```

---

## Complete Flow Diagram: LLM-Based Verification

```
Issue #3723: "XPU AOTInductor fails with CUDA assertion"
State: OPEN

Step 1: Discovery
├── Vector A (Timeline): []                           ← Tool (gh api)
├── Vector C (Keywords): [PR #184800]                 ← Tool (gh pr list --search)
│   └── Search: "OSSProxyExecutor device XPU"
│   └── Found: PR #184800 "Make OSSProxyExecutor device-agnostic"
└── Candidates: [#184800]

Step 2: Verification
└── PR #184800:
    ├── Path 0 (GitHub-linked):                       ← Tool (gh api graphql)
    │   └── Not in closedByPullRequestsReferences
    │   └── FAILED
    │
    ├── Path 1 (Explicit reference):                  ← Script (string match)
    │   └── Search for "#3723" in body: NOT FOUND
    │   └── Search for "/issues/3723" in body: NOT FOUND
    │   └── FAILED
    │
    └── Path 2 (Content-match):                       ← LLM/Agent (Explore Agent)
        ├── Input to Agent:
        │   ├── Issue: title, error message, components
        │   └── PR: title, body, files
        │
        ├── Agent Analysis:
        │   ├── File overlap: YES (oss_proxy_executor.cpp)
        │   ├── Symptom overlap: YES ("CUDA enabled" error)
        │   ├── Timing: PLAUSIBLE (2 days after issue)
        │   └── Semantic match: YES (enables XPU)
        │
        └── Verdict: VERIFIED (content_match)

Step 2.5: Live Re-Check
└── PR #184800: state=OPEN                            ← Tool (gh pr view)
    └── Verdict: "Track PR pytorch/pytorch#184800 to merge"

Step 3: Deep Analysis (Gates)
├── Gate 1 (Resolving): ...                           ← Tool + LLM
├── Gate 2 (Review): ...                              ← Tool
├── Gate 3 (CI): ...                                  ← Tool
└── Gate 4 (Merge): ...                               ← Rule-Based

Step 4: Output
├── action_TBD: "Track upstream PR pytorch/pytorch#184800"
├── action_reason: "PR fixes hardcoded CUDA assumption causing XPU AOTInductor 
│                   failure; verified via content-match (same component, 
│                   same error, enables XPU)"
└── owner_transferred: @pr_author
```

---

## Methods Summary: Scenario B (LLM Used)

| Step | What Happened | Method |
|------|---------------|--------|
| **1. Discovery** | Keyword search found related PR | **Tool** (`gh pr list --search`) |
| **2. Path 0** | Check GitHub links | **Tool** (`gh api graphql`) |
| **2. Path 1** | Search for `#3723` in PR body | **Script** (string match) → FAILED |
| **2. Path 2** | Analyze file/symptom/timing overlap | **LLM/Agent** (Explore Agent) → VERIFIED |
| **2.5. State** | Check PR is OPEN | **Tool** (`gh pr view`) |
| **3. Gates** | Check review/CI status | **Tool** + **LLM** |
| **4. Output** | Generate AR | **Script** (template) |

---

## Key Differences: Path 1 vs Path 2

| Aspect | Path 1 (Script) | Path 2 (LLM/Agent) |
|--------|-----------------|---------------------|
| **Input** | PR body text | PR + Issue full context |
| **Method** | Regex/string search | Semantic reasoning |
| **Speed** | Fast (milliseconds) | Slow (seconds) |
| **Cost** | Free | LLM API cost |
| **Accuracy** | 100% when found | ~90% (can miss/false-positive) |
| **When Used** | PR has `#NNNN` or `Fixes #NNNN` | PR has no explicit reference |

---

## When Path 2 (LLM) is Essential

1. **Copilot-authored PRs**: Often fix issues without mentioning them
2. **Drive-by fixes**: Developer fixes bug while working on related feature
3. **Upstream PRs**: PyTorch core PRs that fix torch-xpu-ops issues
4. **Refactoring PRs**: Changes that accidentally fix issues
5. **Duplicate fixes**: PR addresses same root cause as another issue

---

## Anti-Patterns (LLM Should REJECT)

| Scenario | Why REJECT |
|----------|------------|
| Keyword coincidence | PR mentions "XPU" but fixes unrelated XPU issue |
| Same file, different bug | PR modifies same file but different code path |
| Similar title, different scope | "Fix AOTInductor" but for different operation |
| Wrong timing | PR created long before issue (regression source, not fix) |

Example REJECTED analysis:

```
CONTENT-MATCH ANALYSIS RESULT

1. FILE OVERLAP: PARTIAL
   - Issue mentions: OSSProxyExecutor
   - PR modifies: model_container_runner.cpp (different component)

2. SYMPTOM OVERLAP: NO
   - Issue error: "CUDA enabled" assertion
   - PR addresses: Memory leak (unrelated)

3. SEMANTIC MATCH: NO
   - Issue: Device-agnostic execution
   - PR: Memory management

VERDICT: REJECTED
REASON: "PR modifies different component and addresses unrelated symptom. 
         Keyword 'AOTInductor' appears in both but root cause is different."
```
