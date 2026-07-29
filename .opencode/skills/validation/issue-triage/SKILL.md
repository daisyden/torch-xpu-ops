---
name: issue-triage
description: "End-to-end orchestrator for triaging one GitHub issue (pytorch or torch-xpu-ops) from a bare issue link. Takes an issue URL/number plus conda_env and pytorch_folder, and sequences extract-issue-information (fetch + classify), reproduce-issue (local repro), then triage-issue (duplication/not-target-scope/target-component/priority/category). Always runs full triage regardless of reproduce outcome. Persists logs/JSON under a per-issue results folder in agent_space/, then upserts a single readable `[agent-issue-triage]:` summary comment \u2014 applies `agent:fix_feasible` when verdict is NEED_FIX, and hard-stops on any critical failure. Use for a full triage pipeline in one pass with a durable on-disk record and one GitHub comment that stays current across reruns."
---

# Issue Triage Orchestrator

Sequences: fetch -> reproduce -> triage -> summarize -> notify for ONE issue.

**You orchestrate, log, and notify; you never fix.** Only GitHub mutations
allowed (Step 5 only): upsert one `[agent-issue-triage]:` comment + apply
`agent:fix_feasible` when verdict is `NEED_FIX`.

## Inputs

| Input | Required | Default | Notes |
|---|---|---|---|
| `issue_link` | yes | — | URL or bare number (defaults to `intel/torch-xpu-ops`). |
| `conda_env` | yes | — | Conda env with XPU-enabled `torch`. |
| `pytorch_folder` | yes | — | Local pytorch/torch-xpu-ops checkout. |
| `upload` | no | `false` | When `false`, skip GitHub comment upsert and label application in Step 5 (still writes `summary.md` locally). When `true`, upsert comment and apply labels as before. |

Missing any required input -> **hard-stop**; never guess.

## Results Folder

```python
issue_dir = f"agent_space/issue_triage_orchestrator/{repo.replace('/', '_')}_issue_{issue_id}"
```

All per-issue artifacts AND logs live under `issue_dir/`:

```
{issue_dir}/
├── steps.log                       # append-only per-step log (one line per step)
├── step1_extract.json
├── step2_input.json
├── step2_reproduce.json
├── step2_5_combined_issue.json
├── step3_triage_input.json
├── step3_triage.json
├── triage/                         # triage-issue subskill's working files
│   ├── step1_duplication.json
│   ├── step2_not_target.json
│   ├── step3_target_component.json
│   ├── step4_priority.json
│   └── step5_category.json
├── summary.md
└── final_output.json
```

### Per-Issue Log (`steps.log`)

Each step appends ONE line to `{issue_dir}/steps.log` IMMEDIATELY on
completion. Format:

```
[YYYY-MM-DDTHH:MM:SSZ] <step> | skill: <skill-name> | result: <ok|hard-stop|skipped|short-circuited> | duration_s: <N> | file: <filename>
```

Example:
```
[2026-07-24T13:00:01Z] step1 | skill: extract-issue-information | result: ok | duration_s: 3 | file: step1_extract.json
[2026-07-24T13:00:04Z] step2 | skill: reproduce-issue | result: ok | duration_s: 12 | file: step2_reproduce.json
[2026-07-24T13:00:16Z] step3 | skill: triage-issue | result: ok | duration_s: 45 | file: step3_triage.json
[2026-07-24T13:01:01Z] step5 | skill: notify | result: ok | duration_s: 3 | file: summary.md
[2026-07-24T13:01:04Z] done | skill: — | result: completed | duration_s: 63 | file: final_output.json
```

**Do NOT write to the shared `session_log.txt`.** All logging is local to
`{issue_dir}/steps.log`. The batch orchestrator reads this file if it
needs per-step timing. This eliminates interleaving from parallel issues.

Write each step's JSON **immediately** — never batch.

## Context Budget Management (CRITICAL)

This orchestrator's session context fills up quickly due to multi-step
sequential subagent calls. Apply these mandatory token-saving rules:

### Rule 1: File-based input passing

Never pass full JSON verbatim in `prompt=` strings. Instead:
1. Write the JSON to `{issue_dir}/<stepN_input>.json`
2. In the prompt, reference the file path: "Read input from <path>"

This avoids the JSON appearing in BOTH the prompt AND the result.

### Rule 2: Slim payloads per step

Each step receives ONLY the fields it needs (see per-step field lists below).
Strip `raw_tail`, long `traceback` (keep last 20 lines max), `reproduce_steps`,
and `reproduce_result.results[].raw_tail` before passing to Step 3.

### Rule 3: Background isolation for heavy steps

Step 3 (triage-issue) runs as `run_in_background=true`. Collect ONLY
`final_output.json` from it — its internal multi-step reasoning stays in
its own session, not yours.

## Workflow

**Step 0** — Validate inputs. All three non-empty, else HARD STOP.

**Step 1** — Extract issue information.
```
task(load_skills=["validation/issue-triage/extract-issue-information"], ...)
```
Exit 0 -> write result to `{issue_dir}/step1_extract.json`, proceed.
Exit 1/2 -> HARD STOP.

**IMPORTANT**: Store the FULL JSON output from the script as `extract_result`
in `final_output.json`. Do NOT drop fields — especially `assignee`, `reporter`,
`created_time`, `updated_time`, `milestone`, `priority`, and all `pytorchxpu_*`
fields must be preserved verbatim.

**Step 2** — Reproduce locally. Skip when:
- `test_cases` is empty, OR
- `extract_result.pr_context.has_pr_context == true` (tied to a PR/branch), OR
- issue is a task/feature (extract_result labels contain `task`/`[Task]`/`[Feature]`,
  or `issue_type` is "Task"/"Feature").

When skipped due to task/feature, log with `result: skipped-task-feature` and
set `reproduce_result = null`. Proceed directly to Step 2.5/3.

When skipped due to PR/branch context, log with
`result: skipped-pr-context` and set `reproduce_result = null`. Proceed
directly to Step 2.5/3.

Build a slim input for the reproduce subagent containing ONLY:
`issue_id`, `repo`, `test_cases`, `traceback` (last 20 lines), `conda_env`,
`pytorch_folder`. Write to `{issue_dir}/step2_input.json`.

```
task(load_skills=["validation/issue-triage/reproduce-issue"],
     prompt="Read issue input from {issue_dir}/step2_input.json.
     conda_env=<env>, pytorch_folder=<path>.
     If a case comes back SKIPPED with needs_skip_removal=true,
     run the skip-removal retry loop (remove-xpu-skips, then --rerun)
     for a confirmed verdict before returning.")
```
Exit 0 -> write result to `{issue_dir}/step2_reproduce.json`, proceed to
Step 2.5. Exit 1/2 -> HARD STOP.

**⚠️ SKIPPED handling (MANDATORY):** When ANY case has `result=="SKIPPED"`:
1. You MUST invoke `remove-xpu-skips` + re-run with `--rerun`.
2. **No exceptions. Do NOT rationalize that "the skip is expected behavior"
   or "this is a programmatic skip, not a decorator." ALL skips get the
   retry loop. The remove-xpu-skips skill decides, not you.**
3. Only after retry: PASSED/FAILED/`skip_maintained` is the final verdict.

**No early termination.** Regardless of reproduce outcome (PASSED, FAILED,
SKIPPED, NO_TEST_FOUND, CANNOT_VERIFY), ALWAYS proceed to Step 2.5 and
Step 3 (full triage). The reproduce result is recorded in `final_output.json`
for downstream consumers but never short-circuits the triage pipeline.

**Step 2.5** — Build combined issue JSON and write slim triage input.

Merge `issue_info` + `reproduce_result.results[]` into combined JSON, then
build a **slim triage input** containing ONLY these fields:
- `issue_id`, `repo`, `title`, `status`, `labels`, `type`, `module`,
  `test_module`, `dependency`, `os`, `platform`
- `test_file`, `test_class`, `test_case`, `test_cases` (keep `test_type`,
  `test_file`, `test_class`, `test_case`, `source` per entry only)
- `traceback`: last 20 lines only (truncate from the top)
- `reproduce_summary`: per case — `result`, `reproduced`, `matched_error`,
  `actual_error` (first 5 lines only), `reason`. Do NOT include `raw_tail`.
- `conda_env`, `pytorch_folder`

Write to `{issue_dir}/step3_triage_input.json`.

**Step 3** — Full triage (BACKGROUND — context isolation).

Skip when issue is a task/feature (same condition as Step 2 skip). When
skipped, log with `result: skipped-task-feature` and set `triage_result`
to `{"verdict": "NEED_HUMAN", "short_circuit_reason": "task/feature issue",
"priority": null, "category": null}`. Proceed to Step 4.

Otherwise, fire as a background subagent to keep its internal multi-step
reasoning (5 sub-steps with their own subagents) out of THIS session's context:

```
task(load_skills=["validation/issue-triage/triage-issue"],
     run_in_background=true,
     prompt="Read triage input from {issue_dir}/step3_triage_input.json.
     conda_env=<env>, pytorch_folder=<path>.
     Follow triage-issue workflow. Write final merged JSON to
     {issue_dir}/step3_triage.json when done.")
```

Wait for completion notification, then read ONLY `{issue_dir}/step3_triage.json`
for the merged verdict. Do NOT call `background_output` to pull the full
conversation — only the file-based result matters.

**Step 4** — Build `final_output.json`.

Populate `triage_result.root_cause` as follows:
- If `triage_result.target_component.result` exists and has a `root_cause` field:
  copy it verbatim (a 2-3 sentence string).
- If triage short-circuited (not_target/wontfix label): set to `null`.
- If `triage_result` itself is `null`: the field is absent (outer object is null).

**Step 5** — Summarize and notify (upsert comment + label).

**If `upload == false` (default):** Build `summary.md` (step 5b below), write it
to `{issue_dir}/summary.md`, then skip steps 5a/5c/5d entirely. Set
`notification.commented = false`, `notification.comment_action = null`,
`notification.labeled = false`, `notification.apply_label_reason = "upload=false; skipped"`.
Proceed to Step 6.

**If `upload == true`:** Execute all sub-steps (5a–5d) as described below.

*5a.* Find existing comment:
```bash
existing_comment_id=$(gh api "repos/<repo>/issues/<id>/comments" --paginate \
  --jq '.[] | select(.body | startswith("[agent-issue-triage]: Automated triage result")) | .id' | tail -n 1)
```

*5b.* Build `summary.md` — a **4-column GFM table**: `| Field | Value | Reason | Evidence |`.
First line MUST be exactly: `[agent-issue-triage]: Automated triage result`

**CRITICAL FORMAT RULES (violations = broken output):**
- The table MUST have exactly 4 columns: Field, Value, Reason, Evidence.
- Every row MUST populate all 4 cells. Use `—` for truly empty cells, never omit.
- Do NOT use 2-column tables (`| Field | Value |`). This is a hard requirement.
- Do NOT replace the table with free-form markdown sections like `## Root Cause`,
  `## Evidence`, `## Verdict`. All information goes IN the table rows.
- The only allowed content outside the table is `### Duplicates` sub-table
  (if `has_duplicate`) AFTER the main table.

Rows: Type, Priority, Category, Need action, Target component, Root cause,
Duplicate, Not target, Third-party dependency, OS, Platform,
[Platform specific if true], [Reproduction if Step 2 ran],
Overall confidence. Add `### Duplicates` sub-table if `has_duplicate`.

**Type row derivation:**
- Value = `extract_result.issue_type` (one of: Bug, Task, Feature, Epic).
- Reason = source of the classification (e.g. "github_type", "label", "inferred from description").
- Evidence = `extract_result.github_type` if non-empty, else `extract_result.type`.

**Root cause row derivation:**
- If `triage_result.target_component.result.root_cause` exists: use it verbatim
  as Value; Evidence = `triage_result.target_component.result.evidence.call_path`
  (truncated to first 80 chars) or traced symbols.
- If triage short-circuited (not_target/wontfix): Value = `"N/A (short-circuited)"`,
  Reason = short-circuit reason.
- If `triage_result` is null: Value = `"—"`, Reason = `"—"`.

`Need action` derivation:

| Condition | Value |
|---|---|
| `source == "skipped-duplicate-triaged"` | `"Inherited from duplicate"` |
| `verdict == "NEED_FIX"` | `"Fix required (product code)"` |
| `verdict == "NEED_FIX_CASE"` | `"Fix required (test case)"` |
| `verdict == "NEED_FIX_3RDPARTY"` | `"Blocked — third-party dependency"` |
| `verdict == "NEED_HUMAN"` | `"Needs human review"` |
| All cases PASSED locally (no longer reproduces) | `"N/A — not reproduced"` |

Evidence cell: cite platform/os for environment-blocked NEED_HUMAN;
cite `call_path`/`traced_symbols` for traced verdicts; cite `"N/A — inherited"`
for duplicate-inherited. Never leave blank when signal exists.

*5c.* Upsert: PATCH if existing, else `gh issue comment`. Never `--edit-last`.
Failures are logged, not hard-stops.

*5d.* Apply `agent:fix_feasible` ONLY when `verdict == "NEED_FIX"` (never
inferred from duplicates). Failures logged, not hard-stops.

**Step 6** — Finalize: update `final_output.json` with notification block.

## Hard Stops

Missing input; Step 1 exit 1/2; Step 2 exit 1/2; Step 3 can't identify issue.
Step 5 failures are NOT hard stops.

On hard stop: write `logs/<step>_fatal.log`, set `status="failed-hard-stop"`,
never run Step 5.

Normal outcomes (not hard stops): `CANNOT_VERIFY`, `SKIPPED`, `NO_TEST_FOUND`,
`NEED_HUMAN`. All reproduce outcomes proceed to full triage.

## Output

```python
{
    "issue": {"issue_id": int, "repo": str, "title": str, "url": str},
    "status": "completed" | "failed-hard-stop",
    "hard_stop": {"step": str, "reason": str} | None,
    "issue_dir": str,
    "extract_result": {
        ...,
        "issue_type": str,  # Bug | Task | Feature | Epic (canonical type from extract-issue-information)
        "assignee": str,    # First assignee login, or "" — MUST preserve from script output
        "reporter": str,    # Issue author login — MUST preserve from script output
    },
    "reproduce_result": {...} | None,
    "triage_result": {
        ...,
        "root_cause": str | None,  # from target_component.result.root_cause; None when triage skipped/short-circuited
    } | None,
    "notification": {
        "summary_path": str, "commented": bool, "comment_url": str | None,
        "comment_error": str | None, "comment_action": "created"|"updated"|None,
        "existing_comment_id": int | None, "need_action": str,
        "labeled": bool, "label_error": str | None, "apply_label_reason": str,
    } | None,
    "step_durations": {
        "step1_extract_seconds": float,
        "step2_reproduce_seconds": float | None,
        "step3_triage_seconds": float | None,
        "step5_notify_seconds": float,
        "total_seconds": float,
    },
    "logs": [...]
}
```

### Step Timing

Each step MUST record its start time (via `date -u +%s`) before starting
and compute duration after completion. The `step_durations` field is
derived from `{issue_dir}/steps.log` timestamps.

Compute each step's duration from the `duration_s` field in its log line.
Write `step_durations` into `final_output.json` alongside `logs`.

The `logs` field in `final_output.json` is the FULL CONTENT of
`{issue_dir}/steps.log` as an array of strings (one per line).

## Constraints

1. Steps run strictly 1→2→2.5→3→4→5→6. Never reorder. No early termination.
2. Step 5 is the ONLY GitHub mutation. `agent:fix_feasible` only for `NEED_FIX`.
3. Comment first line = `[agent-issue-triage]: Automated triage result` (marker).
4. Comment follows 5b template exactly: 4-column table, no `<details>`.

## See Also

`extract-issue-information`, `reproduce-issue`, `triage-issue`.
