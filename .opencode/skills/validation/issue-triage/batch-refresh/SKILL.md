---
name: batch-refresh-issue-status
description: "Run refresh-issue-status in parallel for multiple GitHub issues with per-issue isolated subagents and a consolidated batch report. Accepts a list of issue links (or numbers) plus shared conda_env and pytorch_folder, dispatches each to a background subagent running refresh-issue-status, collects results, and writes a batch summary. Use when refreshing 2+ issues at once, when asked to \"refresh these issues\", \"batch refresh\", or \"re-test all of these\"."
---

# Batch Refresh Issue Status

Dispatches `refresh-issue-status` in parallel for a list of issues, collects
per-issue results, and produces a consolidated batch report. Each issue runs
as an independent background subagent with `run_in_background=true`.

**You orchestrate, fan out, collect, and summarize. You never fix.**
Same constraints as `refresh-issue-status` apply to each spawned subagent.

## Inputs

| Input | Required | Notes |
|---|---|---|
| `issue_links` | yes | One or more issue references. Accepts: a list of URLs, bare numbers (default to `intel/torch-xpu-ops`), a range `N~M` (e.g. `2990~2999`), or a comma/space-separated mix. Minimum 1 after expansion. |
| `conda_env` | yes | Conda env with an XPU-enabled `torch`. Shared across all issues. |
| `pytorch_folder` | yes | Local `pytorch`/`torch-xpu-ops` checkout. Shared across all issues. |
| `concurrency` | no | Max parallel issues (default: 5). |

**Range syntax:** `N~M` expands to every integer from min(N,M) to max(N,M)
inclusive. E.g. `2999~2990` expands to `[2990, 2991, ..., 2999]`.

Missing `issue_links`, `conda_env`, or `pytorch_folder` -> **hard-stop
before dispatch**; never guess a default.

## Results Folder & Logging

Results are written under the standard issue-triage orchestrator directory.
Each issue reuses its existing per-issue folder (created by prior triage):

```
agent_space/
└── issue_triage_orchestrator/
    ├── batch_refresh_<timestamp>/
    │   ├── batch_config.json
    │   ├── batch_status.json
    │   ├── batch_summary.md
    │   └── batch_report.json
    ├── intel_torch-xpu-ops_issue_1234/    # per-issue (written by refresh subagent)
    │   ├── steps.log
    │   ├── refresh_output.json
    │   └── ...
    └── ...
```

## Workflow

### Step 0 - Validate & Filter Inputs

1. Verify `issue_links` is non-empty.
2. **Expand ranges:** If any element matches `N~M` (integers separated by
   `~`), expand to `[min(N,M) .. max(N,M)]`.
3. Verify `conda_env` and `pytorch_folder` are non-empty.
4. Deduplicate `issue_links` (same issue appearing twice -> keep one).
5. **Filter out PRs:** For each link, call `gh issue view <number> --repo
   intel/torch-xpu-ops --json url 2>/dev/null`. If the resolved URL contains
   `/pull/`, mark it as `"skipped_pr"` and remove it from the dispatch list.
   Record skipped PRs in `batch_config.json` under a `"skipped_prs"` array
   with the reason `"input resolves to a pull request, not an issue"`.
6. If no issues remain after filtering, hard-stop with a clear message
   listing the skipped PRs.
7. Create batch folder `agent_space/issue_triage_orchestrator/batch_refresh_<timestamp>/`.
8. Write `batch_config.json` with inputs, expanded list, skipped PRs, and timestamp.
9. Initialize `batch_status.json` with remaining issues as `"pending"`.

### Step 1 - Dispatch All Issues in Parallel

Fan out issues up to `concurrency` at a time. Each issue is dispatched as
an **independent background subagent**:

```python
for issue_link in issue_links:
    task(
        category="deep",
        run_in_background=true,
        load_skills=["validation/issue-triage/refresh-issue-status"],
        description=f"Refresh issue {issue_link}",
        prompt=f"""issue_link={issue_link}
conda_env={conda_env}
pytorch_folder={pytorch_folder}

Follow the refresh-issue-status skill exactly, WITH ONE OVERRIDE:
DO NOT upsert, create, edit, or delete any GitHub comment. Skip the
comment step entirely. MANDATORY requirements:

1. Execute the refresh-issue-status workflow steps EXCEPT the comment upsert:
   - Step 1: Check for existing final_output.json (bootstrap via issue-triage if missing)
   - Step 2: Re-run reproduce-issue
   - Step 3: Re-run collect-opens
   - Step 4: Re-run update-label
   - Step 5: Update final_output.json with new reproduce status
   - Step 6: SKIP. Do NOT post or update any GitHub comment.

2. LOGGING: Write ALL per-step logs to the issue's directory under
   agent_space/issue_triage_orchestrator/. Append to steps.log with format:
   [YYYY-MM-DDTHH:MM:SSZ] <step> | skill: <name> | result: <ok|hard-stop|skipped> | duration_s: <N>

3. Write refresh_output.json to the issue directory with:
   - previous_reproduce_status: str
   - new_reproduce_status: str
   - collect_opens_result: object
   - update_label_result: object
   - comment_url: null   (comment step is skipped in batch mode)
   - timestamp: str

Return the full refresh_output.json content at the end."""
    )
```

Track each dispatched task's `task_id` mapped to its `issue_link`.
Update `batch_status.json` to `"running"` for dispatched issues.

**Concurrency control:** If `len(issue_links) > concurrency`, dispatch
the first `concurrency` issues, wait for any to complete, then dispatch
the next.

### Step 2 - Collect Results

As each background task completes (signaled by `<system-reminder>`):

1. Collect output via `background_output(task_id=...)`.
2. Parse the subagent's final output (`refresh_output.json` structure).
3. Update `batch_status.json`: mark as `"completed"` or `"failed"`.
4. If concurrency slots freed and pending issues remain, dispatch next.

Continue until all issues are resolved (completed or failed).

### Step 3 - Generate Batch Report

After all issues are collected, build two artifacts:

**`batch_report.json`** - structured output:

```json
{
    "batch_id": "batch_refresh_YYYYMMDD_HHMMSS",
    "total": 10,
    "completed": 9,
    "failed": 1,
    "skipped_prs": 2,
    "concurrency": 5,
    "duration_seconds": 300.0,
    "skipped_pr_details": [
        {"number": 2991, "reason": "input resolves to a pull request, not an issue"},
        {"number": 2992, "reason": "input resolves to a pull request, not an issue"}
    ],
    "issues": [
        {
            "issue_link": "https://github.com/intel/torch-xpu-ops/issues/2999",
            "issue_id": 2999,
            "repo": "intel/torch-xpu-ops",
            "status": "completed",
            "previous_reproduce_status": "reproduced",
            "new_reproduce_status": "not_reproduced",
            "collect_opens_ar": "NO_AR",
            "labels_to_add": [],
            "labels_to_remove": ["agent:fix_feasible"],
            "comment_url": null,
            "hard_stop": null
        }
    ]
}
```

**`batch_summary.md`** - human-readable GFM table:

```markdown
# Batch Refresh Issue Status Report

**Run:** batch_refresh_YYYYMMDD_HHMMSS
**Issues:** N total (after filtering) | M completed | K failed | P skipped (PRs)
**Duration:** Xm Ys
**Config:** concurrency=5

## Skipped PRs

The following inputs were PRs, not issues, and were automatically skipped:

| # | Link | Reason |
|---|---|---|
| 1 | [#2991](url) | Pull request, not an issue |

## Results

| # | Issue | Previous Status | New Status | Changed? | AR | Labels Action | Comment |
|---|---|---|---|---|---|---|---|
| 1 | [#2999](url) | reproduced | not_reproduced | YES | NO_AR | -agent:fix_feasible | n/a (skipped) |
| 2 | [#3000](url) | reproduced | reproduced | no | NEED_FIX | none | n/a (skipped) |
| 3 | [#3001](url) | — | FAILED | — | — | — | Hard stop: no conda env |

## Statistics

| Metric | Count |
|---|---|
| Input links (before filtering) | N |
| Skipped (PRs) | P |
| Total issues (dispatched) | N-P |
| Completed | M |
| Failed (hard-stop) | K |
| Status changed | X |
| Still reproducing | Y |
| No longer reproducing | Z |
```

### Step 4 - Report to User

Present the `batch_summary.md` content and the path to `batch_report.json`.

## Critical Error Handling

| Condition | Action |
|---|---|
| Missing required inputs | Hard-stop the entire batch before dispatch |
| Single issue hard-stops | Record as failed in batch report; continue others |
| All issues hard-stop | Report batch failure with per-issue reasons |
| Subagent unavailable (model quota) | Hard-stop the entire batch (systemic) |

A single issue failing does NOT abort the batch. Only systemic failures
hard-stop everything.

## Output

```json
{
    "status": "completed | partial | all-failed | hard-stop",
    "batch_id": "batch_refresh_YYYYMMDD_HHMMSS",
    "batch_dir": "agent_space/issue_triage_orchestrator/batch_refresh_YYYYMMDD_HHMMSS/",
    "total_input": 10,
    "skipped_prs": 2,
    "total_dispatched": 8,
    "completed": 7,
    "failed": 1,
    "duration_seconds": 300.0,
    "report_path": "agent_space/issue_triage_orchestrator/batch_refresh_.../batch_report.json",
    "summary_path": "agent_space/issue_triage_orchestrator/batch_refresh_.../batch_summary.md"
}
```

## Constraints

1. Each issue runs as an independent subagent with `run_in_background=true`.
   No shared state between issue refreshes except the filesystem (isolated dirs).
2. **Never skip any step** in the refresh-issue-status workflow EXCEPT the
   comment upsert. Reproduce, collect-opens, and update-label must execute for
   every issue. The GitHub comment step is intentionally disabled in batch mode:
   subagents must NOT post, edit, or delete any comment.
3. The batch orchestrator never directly calls `reproduce-issue`, `collect-opens`,
   or `update-label` — it only delegates to `refresh-issue-status`.
4. Batch-level artifacts are written by THIS skill. Per-issue artifacts are
   written by the delegated `refresh-issue-status` subagents.
5. `batch_status.json` is updated in real-time for external monitoring.

## See Also

`validation/issue-triage/refresh-issue-status` (single-issue refresh),
`validation/issue-triage/batch-triage` (batch triage, similar pattern),
`validation/issue-triage/issue-triage` (single-issue triage).
