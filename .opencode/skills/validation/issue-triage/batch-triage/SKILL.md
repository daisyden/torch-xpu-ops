---
name: batch-issue-triage
description: Run issue-triage in parallel for multiple GitHub issues with per-issue isolated logs and a consolidated batch report. Accepts a list of issue links (or numbers) plus shared conda_env and pytorch_folder, dispatches each to a background subagent running issue-triage, collects results, and writes a batch summary. Use when triaging 2+ issues at once, when asked to "triage these issues", "batch triage", or "run triage on all of these".
---

# Batch Issue Triage Orchestrator

Dispatches `issue-triage` in parallel for a list of issues, collects
per-issue results into isolated directories, and produces a consolidated
batch report. This skill orchestrates concurrency and reporting; the
analysis logic lives in the single-issue `issue-triage` skill it calls.

**You orchestrate, fan out, collect, and summarize. You never fix.**
Same constraints as `issue-triage` apply to each spawned subagent.

## Inputs

| Input | Required | Notes |
|---|---|---|
| `issue_links` | yes | List of URLs or bare numbers (bare numbers default to `intel/torch-xpu-ops`). Minimum 2. |
| `conda_env` | yes | Conda env with an XPU-enabled `torch`. Shared across all issues. |
| `pytorch_folder` | yes | Local `pytorch`/`torch-xpu-ops` checkout. Shared across all issues. |
| `concurrency` | no | Max parallel issues (default: 5). Use lower values if reproduce steps touch shared test files. |
| `skip_reproduce` | no | If true, tells subagents to skip Step 2 (reproduce). Useful for bulk classification without local test runs. Default: false. |

Missing `issue_links`, `conda_env`, or `pytorch_folder` -> **hard-stop
before dispatch**; never guess a default.

## Results Folder & Logging

Each issue gets its own isolated directory via the single-issue skill's
existing convention. The batch orchestrator adds a top-level batch folder:

```
agent_space/
├── session_log.txt                                    # appended by both batch + per-issue
└── issue_triage_orchestrator/
    ├── batch_<timestamp>/
    │   ├── batch_config.json                          # inputs, concurrency, timestamp
    │   ├── batch_status.json                          # live status: pending/running/completed/failed per issue
    │   ├── batch_summary.md                           # final human-readable summary table
    │   └── batch_report.json                          # final structured output
    ├── intel_torch-xpu-ops_issue_1234/                # per-issue (written by issue-triage subagent)
    │   └── ...
    ├── intel_torch-xpu-ops_issue_5678/
    │   └── ...
    └── ...
```

The `batch_<timestamp>` folder uses format `batch_YYYYMMDD_HHMMSS`.

## Workflow

### Step 0 - Validate Inputs

1. Verify `issue_links` is a non-empty list with >= 2 entries.
2. Verify `conda_env` and `pytorch_folder` are non-empty.
3. Deduplicate `issue_links` (same issue appearing twice -> keep one).
4. Create batch folder, write `batch_config.json`.
5. Initialize `batch_status.json` with all issues as `"pending"`.

### Step 1 - Dispatch Issues in Parallel

Fan out issues up to `concurrency` at a time. Each issue is dispatched as
a background task:

```python
for issue_link in issue_links:
    task(
        category="deep",
        run_in_background=true,
        load_skills=["validation/issue-triage/issue-triage"],
        description=f"Triage issue {issue_link}",
        prompt=f"""issue_link={issue_link}
conda_env={conda_env}
pytorch_folder={pytorch_folder}
Follow the issue-triage skill exactly. Return the full final_output.json content."""
    )
```

Track each dispatched task's `task_id` mapped to its `issue_link`.
Update `batch_status.json` to `"running"` for dispatched issues.

**Concurrency control:** If `len(issue_links) > concurrency`, dispatch
the first `concurrency` issues, wait for any to complete, then dispatch
the next. This prevents overloading the system with too many parallel
reproduce steps.

### Step 2 - Collect Results

As each background task completes (signaled by `<system-reminder>`):

1. Collect output via `background_output(task_id=...)`.
2. Parse the subagent's final output (the `final_output.json` structure
   from issue-triage).
3. Update `batch_status.json`: mark the issue as `"completed"` or
   `"failed"` with the hard-stop reason.
4. Log to `session_log.txt`:
   ```
   [YYYY-MM-DD HH:MM:SS] batch | issue: <link> | task_id: <id> | result: <completed|failed-hard-stop> | issue_dir: <path>
   ```
5. If concurrency slots freed and pending issues remain, dispatch next.

Continue until all issues are resolved (completed or failed).

### Step 3 - Generate Batch Report

After all issues are collected, build two artifacts:

**`batch_report.json`** - structured output:

```python
{
    "batch_id": "batch_YYYYMMDD_HHMMSS",
    "total": int,
    "completed": int,
    "failed": int,
    "concurrency": int,
    "skip_reproduce": bool,
    "duration_seconds": float,
    "issues": [
        {
            "issue_link": str,
            "issue_id": int | None,
            "repo": str | None,
            "title": str | None,
            "status": "completed" | "failed-hard-stop",
            "hard_stop": {"step": str, "reason": str} | None,
            "priority": str | None,        # e.g. "P1"
            "category": str | None,        # e.g. "Torch Ops / eltwise"
            "need_action": str | None,     # e.g. "Fix required (product code)"
            "has_duplicate": bool | None,
            "labeled": bool | None,        # was agent:fix_feasible applied?
            "comment_url": str | None,
            "issue_dir": str,
        }
    ]
}
```

**`batch_summary.md`** - human-readable GFM table:

```markdown
# Batch Issue Triage Report

**Run:** batch_YYYYMMDD_HHMMSS
**Issues:** N total | M completed | K failed
**Duration:** Xm Ys
**Config:** concurrency=5, skip_reproduce=false

## Results

| # | Issue | Title | Priority | Category | Need Action | Duplicate | Labeled | Comment |
|---|---|---|---|---|---|---|---|---|
| 1 | [repo#id](url) | title | P2 | Torch Ops / eltwise | Fix required | No | Yes | [link](url) |
| 2 | [repo#id](url) | title | P1 | Backend infra | Needs human review | Yes: #999 | No | [link](url) |
| 3 | [repo#id](url) | — | FAILED | — | — | — | — | Hard stop: Step 1 fetch failure |

## Statistics

| Metric | Count |
|---|---|
| Total issues | N |
| Completed | M |
| Failed (hard-stop) | K |
| Priority P0 | X |
| Priority P1 | Y |
| Priority P2 | Z |
| Priority P3 | W |
| Fix required (product code) | A |
| Fix required (test case) | B |
| Blocked (3rd party) | C |
| Needs human review | D |
| Duplicates found | E |
| Labels applied | F |
```

### Step 4 - Report to User

Present the `batch_summary.md` content and the path to `batch_report.json`.

## Concurrency Safety

### Safe to Parallelize (No Contention)

- Step 1 (extract): read-only GitHub API calls - fully parallel.
- Step 3 (triage): read-only analysis + `gh` API calls - fully parallel.
- Step 5 (notify): each issue's comment/label is independent - fully parallel.

### Potential Contention (Step 2 - Reproduce)

The reproduce step runs pytest locally. Contention occurs when:

1. **Two issues reference the same test file** AND one triggers
   `remove-xpu-skips` (edits the file while the other reads it).
2. **Shared XPU device** - concurrent GPU tests may interfere via OOM or
   device contention.

**Mitigation strategies (applied automatically):**

- `skip_reproduce=true` eliminates all contention (classification-only mode).
- When `skip_reproduce=false`, the orchestrator does NOT serialize reproduce
  steps (that would defeat parallelism). Instead, each subagent runs in its
  own pytest process and any skip-removal edits are scoped per-issue. If
  two issues share the same test file AND both trigger skip-removal, the
  second may fail its reproduce step - this is a normal `CANNOT_VERIFY`
  outcome (not a hard-stop), and the issue still completes triage via
  Steps 3-5 without reproduction data.

### Session Log Contention

`session_log.txt` is append-only. Parallel appends from multiple subagents
may interleave lines but each line is atomic (single write). This is
acceptable for a human-readable log.

## Critical Error Handling

| Condition | Action |
|---|---|
| Missing required inputs | Hard-stop the entire batch before dispatch |
| Single issue hard-stops | Record as failed in batch report; continue others |
| All issues hard-stop | Report batch failure with per-issue reasons |
| Network/rate-limit on dispatch | Retry dispatch once after 30s; if still fails, mark as failed |
| Subagent unavailable (model quota) | Hard-stop the entire batch (systemic, not per-issue) |

A single issue failing does NOT abort the batch. Only systemic failures
(model unavailable, missing inputs) hard-stop everything.

## Output

The skill returns:

```python
{
    "status": "completed" | "partial" | "all-failed" | "hard-stop",
    "batch_id": str,
    "batch_dir": str,           # path to batch_<timestamp>/ folder
    "total": int,
    "completed": int,
    "failed": int,
    "duration_seconds": float,
    "report_path": str,         # path to batch_report.json
    "summary_path": str,        # path to batch_summary.md
    "issues": [...]             # same as batch_report.json.issues
}
```

| `status` | Meaning |
|---|---|
| `completed` | All issues triaged successfully |
| `partial` | Some completed, some failed |
| `all-failed` | Every issue hard-stopped |
| `hard-stop` | Batch-level failure (inputs, systemic error) |

## Constraints

1. Each issue runs as an independent subagent with its own `issue-triage`
   session. No shared state between issue triages except the filesystem
   (which uses isolated `issue_dir` paths).
2. Never skip Steps 3-5 of a single-issue triage because its reproduce
   step conflicted with another issue's. A `CANNOT_VERIFY` reproduce
   result is passed through to triage, which handles it gracefully.
3. The batch orchestrator never directly calls `extract-issue-information`,
   `reproduce-issue`, or `triage-issue` - it only delegates to the
   single-issue `issue-triage` skill which handles sequencing.
4. Batch-level artifacts (`batch_config.json`, `batch_status.json`,
   `batch_report.json`, `batch_summary.md`) are written by THIS skill.
   Per-issue artifacts are written by the delegated `issue-triage` subagents.
5. `batch_status.json` is updated in real-time as issues transition states,
   enabling external monitoring of an in-progress batch.

## Example Invocation

```
Triage these issues in parallel:
- https://github.com/intel/torch-xpu-ops/issues/3344
- https://github.com/intel/torch-xpu-ops/issues/3290
- https://github.com/intel/torch-xpu-ops/issues/3100
- 3050
- 2999

conda_env=xpu_triage
pytorch_folder=/home/user/pytorch
concurrency=3
```

## See Also

`validation/issue-triage/issue-triage` (single-issue orchestrator),
`validation/issue-triage/extract-issue-information`,
`validation/issue-triage/reproduce-issue`,
`validation/issue-triage/triage-issue`.
