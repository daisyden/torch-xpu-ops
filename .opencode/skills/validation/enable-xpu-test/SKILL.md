---
name: enable-xpu-test
description: End-to-end orchestrator for XPU test enablement. Takes test_file/test_class plus conda_env/pytorch_folder, gates on review-test-refactoring, runs develop-xpu-test then verify-xpu-test, analyzes local outcomes with analyze-ut-failures, and routes to submit-xpu-test-pr directly on pass or to known-issue/issue-creation follow-up before PR submission on failures. Mandatory step/subagent logging to agent_space and hard-stop on critical errors.
---

# Enable-XPU-Test Orchestrator

Run the full XPU-enable workflow for one test target:

1. Review gate
2. Develop enablement edits
3. Verify locally on XPU
4. Analyze local test outcomes
5. Submit PR directly if passing
6. If not passing, do known-issue / issue-creation follow-up, then submit PR with issue links

This skill coordinates existing subskills under `validation/enable-xpu-test` and
validation issue triage skills. It does not replace those skills.

## Inputs

- `test_file` (required): target test file path (relative to `pytorch_folder`)
- `test_class` (required): target test class
- `conda_env` (required): conda env used for local test/verification
- `pytorch_folder` (required): local pytorch checkout root
- Optional: `test_cases` list when narrowing analysis / known-issue checks

## Subskills Used

| Phase | Skill | Purpose |
|---|---|---|
| Review gate | `review-test-refactoring` | Hard-stop quality gate before enablement |
| Enable edits | `develop-xpu-test` | Apply XPU-enable source changes |
| Verify | `verify-xpu-test` | Local XPU verification of edits |
| Analyze outcomes | `analyze-ut-failures` | Run/group local failures and return verdict |
| Known issue lookup | `check-known-issue` | Determine if each failing case is already tracked |
| New issue filing | `create-xpu-issue` | Create tracking issue when no known issue exists |
| PR submission | `submit-xpu-test-pr` | Commit/push/open draft PR |

## Workflow

### Phase 0: Session Logging Bootstrap (MANDATORY)

Before any subagent call, create and append logs under `agent_space`:

- `agent_space/session_log.txt`
- `agent_space/logs/background_status.log`
- `agent_space/enable_xpu_orchestrator/phase_<n>_<name>.json`

Session log line format (must be used for every step):

```text
[YYYY-MM-DD HH:MM:SS] <step description> | subagent: <skill_name> | task: <brief description> | file_refs: <comma-sep file/issue refs>
```

Delegation line format (must be logged immediately after each task dispatch):

```text
Delegated: <phase_name> | subagent_type: <type> | load_skills: [<skills>] | task_count: <N> | batch_key: <test_class>
```

### Phase 1: Review Gate (HARD STOP)

Delegate to `review-test-refactoring` for `test_file`.

```python
task(
    subagent_type="explore",
    load_skills=["review-test-refactoring"],
    run_in_background=False,
    description=f"Review gate: {test_file}",
    prompt=(
        f"Review {test_file} for refactoring correctness before XPU enablement. "
        f"Return Blockers/Majors/Minors and pass/fail gate result for class {test_class}."
    ),
)
```

Gate rule:
- PASS: zero Blockers -> proceed.
- FAIL: one or more Blockers -> **hard-stop**. Log findings and end workflow.

### Phase 2: Develop Enablement

Delegate to `develop-xpu-test` with the provided `test_file` / `test_class`.

```python
task(
    subagent_type="explore",
    load_skills=["develop-xpu-test"],
    run_in_background=False,
    description=f"Develop XPU enablement: {test_class}",
    prompt=(
        f"Enable XPU for {test_class} in {test_file}. "
        f"Use conda_env={conda_env}, pytorch_folder={pytorch_folder}."
    ),
)
```

### Phase 3: Verify Enablement

Delegate to `verify-xpu-test` immediately after develop phase.

```python
task(
    subagent_type="explore",
    load_skills=["verify-xpu-test"],
    run_in_background=False,
    description=f"Verify XPU enablement: {test_class}",
    prompt=(
        f"Verify XPU enablement for {test_class} in {test_file}. "
        f"Use conda_env={conda_env}, pytorch_folder={pytorch_folder}."
    ),
)
```

If verification result is not `verified`, hard-stop and report.

### Phase 4: Analyze Local Outcomes

Delegate to `analyze-ut-failures`.

```python
task(
    subagent_type="explore",
    load_skills=["analyze-ut-failures"],
    run_in_background=False,
    description=f"Analyze UT outcomes: {test_class}",
    prompt=(
        f"Analyze test outcomes for test_file={test_file}, test_class={test_class}, "
        f"test_cases={test_cases if 'test_cases' in locals() else '[]'}, "
        f"conda_env={conda_env}, pytorch_root={pytorch_folder}. "
        f"Return JSON with top-level verdict and groups."
    ),
)
```

Branch by returned top-level verdict:
- `verdict == "passed"` -> go to Phase 5A.
- `verdict == "has-failures"` -> go to Phase 5B.

### Phase 5A: Submit PR Directly (passed)

Call `submit-xpu-test-pr` directly.

```python
task(
    subagent_type="explore",
    load_skills=["submit-xpu-test-pr"],
    run_in_background=False,
    description=f"Submit PR: {test_class}",
    prompt=(
        f"Prepare and submit draft PR for XPU enablement of {test_class} in {test_file}. "
        f"Use pytorch_folder={pytorch_folder}."
    ),
)
```

### Phase 5B: Failure Follow-up -> PR

For each failing case/group from `analyze-ut-failures`:

1. Delegate `check-known-issue`.
2. If known issue exists, collect issue URLs.
3. If no known issue, delegate `create-xpu-issue` to create one and collect URL.

Known-issue check template:

```python
task(
    subagent_type="explore",
    load_skills=["check-known-issue"],
    run_in_background=False,
    description=f"Known-issue check: {test_name}",
    prompt=(
        f"Check known issue for test_file={test_file}, class_name={test_class}, "
        f"test_name={test_name}, error_message={error_message}, device=xpu."
    ),
)
```

If no known issue:

```python
task(
    subagent_type="explore",
    load_skills=["create-xpu-issue"],
    run_in_background=False,
    description=f"Create issue: {test_name}",
    prompt=(
        f"Create XPU issue for failure signature={signature}, tests={tests}, "
        f"root_cause={root_cause}, pr_number_or_url=<pending PR>, "
        f"include Context for this enablement workflow."
    ),
)
```

After issue URLs are collected, call `submit-xpu-test-pr` and require issue
links be included in PR body/context.

> Note: User request says "submit-xpu-test". The existing skill is
> `submit-xpu-test-pr`; use that skill for PR submission.

## Critical Error Handling (HARD STOP)

Stop workflow immediately on any critical blocker:

- Provider/subagent unavailable (model/provider/rate-limit/quota failures)
- Broken test env (`torch` import failure, `torch.xpu.is_available()==False`,
  invalid conda env)
- Missing required inputs (`test_file`, `test_class`, `conda_env`,
  `pytorch_folder`)
- Unrecoverable script/command failure that blocks next phase

When a critical blocker occurs:

1. Append fatal line to `agent_space/session_log.txt`:

```text
[FATAL] <phase>: <error> — halting session
```

2. Save details to `agent_space/logs/<phase>_fatal.log`.
3. End workflow immediately (do not execute downstream phases).

## Required Logs and Artifacts

At minimum, persist:

- `agent_space/session_log.txt`
- `agent_space/logs/background_status.log`
- `agent_space/enable_xpu_orchestrator/phase1_review.json`
- `agent_space/enable_xpu_orchestrator/phase2_develop.json`
- `agent_space/enable_xpu_orchestrator/phase3_verify.json`
- `agent_space/enable_xpu_orchestrator/phase4_analyze.json`
- `agent_space/enable_xpu_orchestrator/phase5_followup.json` (when failures)
- `agent_space/enable_xpu_orchestrator/phase5_submit_pr.json`

## Output Contract

Return JSON summary:

```json
{
  "status": "passed|issue-follow-up|failed-hard-stop",
  "test_file": "...",
  "test_class": "...",
  "analysis_verdict": "passed|has-failures",
  "known_issue_urls": ["..."],
  "created_issue_urls": ["..."],
  "pr_url": "... or null",
  "logs": [
    "agent_space/session_log.txt",
    "agent_space/enable_xpu_orchestrator/phase1_review.json"
  ]
}
```

## Constraints

1. Review gate is mandatory hard-stop.
2. Every phase/subagent must be logged in `agent_space`.
3. Never continue past a critical error.
4. Use explicit `load_skills=[...]` and `run_in_background=False` for
   sequential dependent subagent phases.
5. Do not skip verification/analyze phases.
6. PR submission must use `submit-xpu-test-pr`.

## See Also

- `develop-xpu-test`
- `verify-xpu-test`
- `submit-xpu-test-pr`
- `analyze-ut-failures`
- `check-known-issue`
- `create-xpu-issue`
