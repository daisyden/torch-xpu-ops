---
name: update-label
description: "Determine which label actions are needed for a triaged GitHub issue based on the final output of issue-triage. Evaluates six label conditions (closed, duplicated, dependency, not_target, priority, type) against current issue state and returns a JSON file with action requests (ARs). Analysis-only — never applies labels directly. Use after issue-triage completes, when you need to know which labels are missing or stale on an issue."
---

# Update Label

Evaluates the final triage output for a single GitHub issue and returns a JSON
file listing label actions (ARs) that should be applied. **Analysis-only** —
this skill never mutates GitHub state (no label additions, no comment edits).

## Inputs

| Input | Required | Default | Notes |
|---|---|---|---|
| `issue_link` | yes | — | URL or bare issue number. |
| `repo` | no | `intel/torch-xpu-ops` | Repository owner/name for bare numbers. |
| `triage_output` | no | — | Path to `final_output.json` from issue-triage, OR the JSON object directly. If not provided, the skill checks the conventional on-disk path first (see Workflow step 2). |
| `conda_env` | conditional | — | Required only when no `triage_output` is provided AND no on-disk `final_output.json` exists at the conventional path (needed to run issue-triage). |
| `pytorch_folder` | conditional | — | Required only when no `triage_output` is provided AND no on-disk `final_output.json` exists at the conventional path (needed to run issue-triage). |

Missing `issue_link` -> **hard-stop**.
Missing `triage_output` AND no on-disk `final_output.json` AND missing `conda_env`/`pytorch_folder` -> **hard-stop**.

## Prerequisites

- Authenticated `gh` CLI on PATH.
- If `triage_output` is not provided AND no on-disk `final_output.json` exists:
  `conda_env` and `pytorch_folder` must be available so issue-triage can run.

## Decision Rules

Evaluate ALL rules. Multiple ARs can fire for the same issue. Each rule is
independent — do not short-circuit after the first match.

### Rule 1 — Label Closed (local reproduce all passed)

**Condition:** ALL of:
- Issue is NOT platform-specific (platform field is empty or matches local platform "PVC")
- Issue is NOT a PyTorch CI failure (i.e., current labels do NOT contain `pytorch-ci-failure` AND issue title does NOT start with `[PyTorch CI]`)
- `reproduce_result` exists and ALL test cases have `result == "PASSED"` (i.e., `reproduced == false` for all)

**Rationale:** PyTorch CI failures may be environment-specific (different driver
versions, OS packages, container images, or transient infra issues). Passing
locally does not prove the CI bug is resolved — only a green CI run does.

**AR:** `label_closed`
**AR_REASON:** Summary of reproduce results — which cases passed, on which environment.

### Rule 2 — Label Duplicated

**Condition:** ALL of:
- `triage_result.has_duplicate == true` AND at least one entry in `triage_result.duplicates[]` exists
- Current issue labels do NOT contain `duplicated`

**AR:** `label_duplicated`
**AR_REASON:** The duplicated issue(s) — format: `"Duplicate of #<issue_id> (<repo>)"`

### Rule 3 — Label Dependency

**Condition:** ALL of:
- `triage_result.verdict == "NEED_FIX_3RDPARTY"` (or `triage_result.target_component.verdict == "NEED_FIX_3RDPARTY"`)
- `triage_result.dependency` (or `triage_result.target_component.dependency`) identifies a third-party component
- Current issue labels do NOT contain a matching `dependency component: <component>` label for that component

Supported dependency components and their label mappings:

| Component | Label |
|---|---|
| MSVC | `dependency component: MSVC` |
| driver | `dependency component: driver` |
| oneAPI | `dependency component: oneAPI` |
| oneDNN | `dependency component: oneDNN` |
| oneMKL | `dependency component: oneMKL` |
| Triton | `dependency component: Triton` |
| community | `dependency component: community` |
| third_party packages | `dependency component: third_party packages` |

**AR:** `label_dependency`
**AR_REASON:** The identified third-party component — format: `"Depends on <component>: <evidence>"`

### Rule 4 — Label Not Target

**Condition:** ALL of:
- `triage_result.not_target.is_not_target == true` (or `triage_result.verdict == "NO_NEED_FIX"` with not-target evidence)
- Current issue labels do NOT contain `not_target` AND do NOT contain `wontfix`

**AR:** `label_not_target`
**AR_REASON:** The not-target evidence — format: `"<reason from not_target verdict>"`

### Rule 5 — Label Priority

**Condition:** ALL of:
- `triage_result.priority` is set (P0/P1/P2/P3)
- Issue's PyTorchXPU project Priority field is blank (not set)

To check the project priority field:
```bash
gh issue view <issue_id> --repo <repo> --json projectItems \
  --jq '.projectItems[] | select(.project.title == "PyTorchXPU") | .priority.name'
```
If output is empty or null, the condition is met.

**AR:** `label_priority`
**AR_REASON:** The priority and evidence — format: `"Priority <P0-P3>: <reason from priority verdict>"`

### Rule 6 — Label Type

**Condition:** ALL of:
- `extract_result.issue_type` is set (Bug/Task/Feature/Epic)
- Issue's native GitHub type field is blank (not set)

To check the native issue type:
```bash
gh api graphql -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){issue(number:$number){issueType{name}}}}' \
  -f owner=<owner> -f name=<repo_name> -F number=<issue_id> \
  --jq '.data.repository.issue.issueType.name'
```
If output is empty or null, the condition is met.

**AR:** `label_type`
**AR_REASON:** The inferred type — format: `"Type <Bug|Task|Feature|Epic>: <source>"` where source is one of "github_type", "label", or "inferred from description".

## Workflow

1. Parse `issue_link` to extract `issue_id` and `repo`.
2. **Resolve triage output** (checked in priority order):
   a. If `triage_output` is provided explicitly (file path or inline JSON), load it directly.
   b. If NOT provided, check the conventional on-disk path:
      ```
      agent_space/issue_triage_orchestrator/<repo_with_underscores>_issue_<id>/final_output.json
      ```
      Where `<repo_with_underscores>` replaces `/` with `_` (e.g. `intel/torch-xpu-ops` → `intel_torch-xpu-ops`).
      If the file exists, load it directly — no issue-triage run needed.
   c. If the file does NOT exist AND `conda_env`/`pytorch_folder` are provided,
      run issue-triage:
      ```
      task(load_skills=["validation/issue-triage"],
           run_in_background=true,
           prompt="issue_link=<issue_link>, conda_env=<conda_env>,
           pytorch_folder=<pytorch_folder>. Run full issue-triage pipeline.")
      ```
      Wait for completion. Read the resulting `final_output.json` from the path in (b).
   d. If the file does NOT exist AND `conda_env`/`pytorch_folder` are NOT provided → **hard-stop**.
3. Fetch current issue labels via `gh issue view`.
4. Fetch current PyTorchXPU project priority field and native issue type field.
5. Evaluate all 6 rules against current state + triage output.
6. Build output JSON with all triggered ARs.
7. Write output to `{issue_dir}/update_label_result.json` (or stdout if no issue_dir).

## Output

```json
{
  "issue_id": 1234,
  "repo": "intel/torch-xpu-ops",
  "url": "https://github.com/intel/torch-xpu-ops/issues/1234",
  "current_labels": ["module: ut", "hw: PVC"],
  "project_priority": null,
  "action_requests": [
    {
      "AR": "label_closed",
      "AR_REASON": "All test cases passed locally: test_foo (PASSED), test_bar (PASSED). Environment: PVC, conda env xpu_test."
    },
    {
      "AR": "label_priority",
      "AR_REASON": "Priority P2: 2 UT failures, non-crash functional error in torch.add."
    }
  ],
  "total_ars": 2
}
```

When no rules fire:

```json
{
  "issue_id": 1234,
  "repo": "intel/torch-xpu-ops",
  "url": "https://github.com/intel/torch-xpu-ops/issues/1234",
  "current_labels": ["module: ut", "duplicated", "dependency component: oneDNN"],
  "project_priority": "P1",
  "action_requests": [],
  "total_ars": 0
}
```

### AR Codes Reference

| AR Code | Meaning | Label to Apply |
|---|---|---|
| `label_closed` | Bug no longer reproduces locally | Close the issue |
| `label_duplicated` | Issue is a duplicate but not labeled | `duplicated` |
| `label_dependency` | Third-party dependency identified but not labeled | `dependency component: <component>` |
| `label_not_target` | Issue is out of scope but not labeled | `not_target` |
| `label_priority` | Priority determined but project field is blank | Set PyTorchXPU Priority field |
| `label_type` | Type inferred but native issue type is blank | Set native GitHub issue type |

## Constraints

1. **Analysis-only.** Never apply labels, close issues, or mutate GitHub state.
2. All 6 rules are evaluated independently. Multiple ARs can coexist.
3. Uses `gh` CLI exclusively for GitHub API access.
4. Output MUST be valid JSON.
5. If `triage_output` is missing required fields for a rule, skip that rule
   silently (do not error). Only hard-stop on missing top-level inputs.

## Hard Stops

- Missing `issue_link` input.
- Missing `triage_output` AND no on-disk `final_output.json` AND missing `conda_env` or `pytorch_folder`.
- `gh` CLI not authenticated or not on PATH.
- Issue fetch returns 404.
- issue-triage run fails with `status == "failed-hard-stop"` (when auto-triggered).

## See Also

`issue-triage`, `collect-opens`, `triage-issue`.
