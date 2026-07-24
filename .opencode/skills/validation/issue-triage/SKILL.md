---
name: issue-triage
description: "End-to-end orchestrator for triaging one GitHub issue (pytorch or torch-xpu-ops) from a bare issue link. Takes an issue URL/number plus conda_env and pytorch_folder, and sequences extract-issue-information (fetch + classify), reproduce-issue (local repro), then triage-issue (duplication/not-target-scope/target-component/priority/category). Early-terminates with Need_action=N/A when the test passes locally (bug no longer reproduces), skipping full triage. Persists logs/JSON under a per-issue results folder in agent_space/, then upserts a single readable `[agent-issue-triage]:` summary comment \u2014 applies `agent:fix_feasible` when verdict is NEED_FIX, and hard-stops on any critical failure. Use for a full triage pipeline in one pass with a durable on-disk record and one GitHub comment that stays current across reruns."
---

# Issue Triage Orchestrator

Sequences: fetch -> reproduce -> triage -> summarize -> notify for ONE issue.

**You orchestrate, log, and notify; you never fix.** Only GitHub mutations
allowed (Step 5 only): upsert one `[agent-issue-triage]:` comment + apply
`agent:fix_feasible` when verdict is `NEED_FIX`.

## Inputs

| Input | Required | Notes |
|---|---|---|
| `issue_link` | yes | URL or bare number (defaults to `intel/torch-xpu-ops`). |
| `conda_env` | yes | Conda env with XPU-enabled `torch`. |
| `pytorch_folder` | yes | Local pytorch/torch-xpu-ops checkout. |

Missing any -> **hard-stop**; never guess.

## Results Folder

```python
issue_dir = f"agent_space/issue_triage_orchestrator/{repo.replace('/', '_')}_issue_{issue_id}"
```

Files: `step1_extract.json`, `step2_reproduce.json`, `step2_5_combined_issue.json`,
`step3_triage.json`, `summary.md`, `final_output.json`.

Log format: `[YYYY-MM-DD HH:MM:SS] <step> | subagent: <skill> | task_id: <id> | result: <ok|hard-stop|skipped>`.
Write each step's JSON **immediately** — never batch.

## Workflow

**Step 0** — Validate inputs. All three non-empty, else HARD STOP.

**Step 1** — Extract issue information.
```
task(load_skills=["validation/issue-triage/extract-issue-information"], ...)
```
Exit 0 -> Step 2. Exit 1/2 -> HARD STOP.

**Step 2** — Reproduce locally. Skip when `test_cases` is empty.
```
task(load_skills=["validation/issue-triage/reproduce-issue"],
     prompt="...If a case comes back SKIPPED with needs_skip_removal=true,
     run the skip-removal retry loop (remove-xpu-skips, then --rerun)
     for a confirmed verdict before returning.")
```
Exit 0 -> early-termination check. Exit 1/2 -> HARD STOP.

**⚠️ SKIPPED handling (MANDATORY):** When ANY case has `result=="SKIPPED"`:
1. It is INCONCLUSIVE — does NOT qualify for early termination.
2. You MUST invoke `remove-xpu-skips` + re-run with `--rerun`.
3. **No exceptions. Do NOT rationalize that "the skip is expected behavior"
   or "this is a programmatic skip, not a decorator." ALL skips get the
   retry loop. The remove-xpu-skips skill decides, not you.**
4. Only after retry: PASSED/FAILED/`skip_maintained` is the final verdict.
5. If skip stays (`reason="skip_maintained"`), it does NOT satisfy early termination.

**Early termination** — fires ONLY when ALL cases have `reproduced==false`
AND `result=="PASSED"`. No other result qualifies:

| `result` | Qualifies? |
|---|---|
| `PASSED` | ✅ Yes (the ONLY one) |
| `SKIPPED` | ❌ No — never ran |
| `NO_TEST_FOUND` | ❌ No |
| `CANNOT_VERIFY` | ❌ No |
| `FAILED`/`ERROR` | ❌ No |

If all PASSED, check platform:
- **Platform-specific** (issue platform != PVC and != ""): Do NOT early-terminate.
  Proceed to Step 2.5/3. Note `platform_specific = true`.
- **Not platform-specific**: Early-terminate. Set `need_action = "N/A"`,
  skip Step 3, proceed to Step 5 with all triage fields as `"N/A"`.

**Step 2.5** — Build combined issue JSON (merge `issue_info` + `reproduce_result.results[]`).

**Step 3** — Full triage.
```
task(load_skills=["validation/issue-triage/triage-issue"], ...)
```

**Step 4** — Build `final_output.json`.

**Step 5** — Summarize and notify (upsert comment + label).

*5a.* Find existing comment:
```bash
existing_comment_id=$(gh api "repos/<repo>/issues/<id>/comments" --paginate \
  --jq '.[] | select(.body | startswith("[agent-issue-triage]: Automated triage result")) | .id' | tail -n 1)
```

*5b.* Build `summary.md` — a 4-column GFM table: `Field | Value | Reason | Evidence`.
First line MUST be exactly: `[agent-issue-triage]: Automated triage result`

Rows: Priority, Category, Need action, Target component, Duplicate, Not target,
Third-party dependency, OS, Platform, [Platform specific if true], [Reproduction if Step 2 ran],
Overall confidence. Add `### Duplicates` sub-table if `has_duplicate`.

`Need action` derivation:

| Condition | Value |
|---|---|
| Early termination (all passed) | `"N/A"` |
| `source == "skipped-duplicate-triaged"` | `"Inherited from duplicate"` |
| `verdict == "NEED_FIX"` | `"Fix required (product code)"` |
| `verdict == "NEED_FIX_CASE"` | `"Fix required (test case)"` |
| `verdict == "NEED_FIX_3RDPARTY"` | `"Blocked — third-party dependency"` |
| `verdict == "NEED_HUMAN"` | `"Needs human review"` |

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
`NEED_HUMAN`. Platform-specific passes trigger full triage, not early termination.

## Output

```python
{
    "issue": {"issue_id": int, "repo": str, "title": str, "url": str},
    "status": "completed" | "failed-hard-stop",
    "hard_stop": {"step": str, "reason": str} | None,
    "issue_dir": str,
    "extract_result": {...},
    "reproduce_result": {...} | None,
    "triage_result": {...} | None,
    "notification": {
        "summary_path": str, "commented": bool, "comment_url": str | None,
        "comment_error": str | None, "comment_action": "created"|"updated"|None,
        "existing_comment_id": int | None, "need_action": str,
        "labeled": bool, "label_error": str | None, "apply_label_reason": str,
    } | None,
    "logs": [...]
}
```

## Constraints

1. Steps run strictly 1→2→(early-term?)→2.5→3→4→5→6. Never reorder.
2. Step 5 is the ONLY GitHub mutation. `agent:fix_feasible` only for `NEED_FIX`.
3. Comment first line = `[agent-issue-triage]: Automated triage result` (marker).
4. Comment follows 5b template exactly: 4-column table, no `<details>`.

## See Also

`extract-issue-information`, `reproduce-issue`, `triage-issue`.
