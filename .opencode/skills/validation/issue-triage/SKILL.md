---
name: issue-triage
description: End-to-end orchestrator for triaging one GitHub issue (pytorch or torch-xpu-ops) from a bare issue link. Takes an issue URL/number plus conda_env and pytorch_folder, and sequences extract-issue-information (fetch + classify), reproduce-issue (local repro), then triage-issue (duplication/not-target-scope/target-component/priority/category). Persists logs/JSON under a per-issue results folder in agent_space/, then upserts a single readable `[agent-issue-triage]:` summary comment (updates in place on rerun, creates if absent) — one GFM table with Field/Value/Reason/Evidence columns covering Priority/Category/Need_action/Target-component/Duplicate/Not-target/Third-party-dependency/Overall-confidence, plus a compact Reproduction/Duplicates detail table when applicable — applies `agent:fix_feasible` when verdict is NEED_FIX, and hard-stops on any critical failure. Use for a full triage pipeline in one pass with a durable on-disk record and one GitHub comment that stays current across reruns.
---

# Issue Triage Orchestrator (top-level)

Runs the complete `issue-triage/*` family end-to-end for **one** GitHub
issue, from nothing but a link: fetch -> reproduce -> triage -> summarize
-> notify. This skill schedules, logs, and posts the final result; the
analysis logic lives in the subskills it calls.

**You orchestrate, log, and notify; you never fix.** Never edit files,
`git commit`, open PRs, or edit the issue body/title. The ONLY GitHub
mutations allowed, both confined to Step 5, are: upserting one
`[agent-issue-triage]:` comment (update in place if it exists, else
create), and adding `agent:fix_feasible` when the verdict is `NEED_FIX`.
Steps 1-3 are read-only/analysis-only except `reproduce-issue`'s own
local pytest run and skip-removal retry loop (may remove a stale skip on
a CLOSED-issue test, run it, keep or revert — never commits/mutates
GitHub state).

## Inputs

| Input | Required | Notes |
|---|---|---|
| `issue_link` | yes | URL or bare number (bare number defaults to `intel/torch-xpu-ops`). |
| `conda_env` | yes | Conda env with an XPU-enabled `torch`. Passed to Steps 2 & 3. |
| `pytorch_folder` | yes | Local `pytorch`/`torch-xpu-ops` checkout. Passed to Steps 2 & 3. |

Missing any -> **hard-stop before Step 1**; never guess a default.

## Subskills Used

| Step | Skill | Purpose |
|---|---|---|
| 1 | `extract-issue-information` | Fetch the issue, emit JSON + rule-based classification |
| 2 | `reproduce-issue` | Locally confirm the failure still reproduces |
| 3 | `triage-issue` | Duplication, not-target scope, target-component, priority, category |
| 5 | *(inline `gh` calls)* | Upsert one issue comment, apply `agent:fix_feasible` when warranted |

## Results Folder & Logging (MANDATORY)

Every run gets its own folder, keyed by repo + issue id, so parallel or
repeated runs never collide:

```python
issue_dir = f"agent_space/issue_triage_orchestrator/{repo.replace('/', '_')}_issue_{issue_id}"
```

`repo`/`issue_id` come from Step 1's output (fall back to a sanitized
`issue_link` if Step 1 hard-stops first).

```
agent_space/
├── session_log.txt                 # one line per step, all issues appended here
└── issue_triage_orchestrator/<repo>_issue_<id>/
    ├── step1_extract.json / step2_reproduce.json / step2_5_combined_issue.json / step3_triage.json
    ├── summary.md                  # human-readable summary, also posted as the comment
    ├── final_output.json           # this skill's Output (see below)
    └── logs/<stage>_fatal.log      # only on a HARD STOP
```

Log format: `[YYYY-MM-DD HH:MM:SS] <step> | subagent: <skill> | task_id:
<id> | result: <ok|hard-stop|skipped> | file_refs: <path>`. Write it + the
step JSON **immediately after each step** — never batch to the end.

## Workflow

**Step 0 — Validate inputs.** All three non-empty, else HARD STOP.

**Step 1 — Extract issue information.**

```
task(subagent_type="quick", run_in_background=false,
     load_skills=["validation/issue-triage/extract-issue-information"],
     prompt="Fetch <issue_link> per the extract-issue-information skill.
     Return the full JSON verbatim plus exit code/stderr, labeled.")
```

Compute `issue_dir` from the returned `issue_info.repo`/`issue_id`,
create it, write `step1_extract.json`, log. Exit 0 -> continue to Step 2
with the JSON as `issue_info`. Exit 1 -> HARD STOP (fetch failure/PR).
Exit 2 -> HARD STOP (malformed reference).

**Step 2 — Reproduce locally (conditional).** Skip (don't spawn) when
`issue_info.test_cases` is empty — log `result: skipped`, go to Step 2.5
with `issue_info` alone. Otherwise:

```
task(subagent_type="explore", run_in_background=false,
     load_skills=["validation/issue-triage/reproduce-issue"],
     prompt="issue_info=<Step 1 JSON verbatim>. conda_env=<conda_env>,
     pytorch_folder=<pytorch_folder>. Follow reproduce-issue exactly.
     Return the full JSON verbatim plus exit code/stderr, labeled. If a
     case comes back SKIPPED with needs_skip_removal=true, run
     reproduce-issue's own skip-removal retry loop (remove-xpu-skips,
     then --rerun) for a confirmed verdict before returning.")
```

Write `step2_reproduce.json`, log. Exit 0 -> continue to Step 2.5
(per-case `NOT_REPRODUCED`/`CANNOT_VERIFY`/`SKIPPED`/`NO_TEST_FOUND` are
expected, not hard-stops). Exit 1 -> HARD STOP (env/checkout/torch
import/no XPU). Exit 2 -> HARD STOP (bad input JSON, internal contract
violation).

**Step 2.5 — Build the combined issue JSON.** `triage-issue` accepts an
`extract-issue-information` object, optionally enriched with
`reproduce-issue`'s `results[]`. Never hand it a bare `reproduce-issue`
object (lacks `issue_id`/`title`):

```python
combined_issue = dict(issue_info)
if step2_ran:
    combined_issue["results"] = reproduce_result["results"]
    combined_issue["xpu_available"] = reproduce_result["xpu_available"]
    combined_issue["torch_version"] = reproduce_result["torch_version"]
```

Write `step2_5_combined_issue.json`. Local merge only, no subagent.

**Step 3 — Full triage.**

```
task(subagent_type="general", run_in_background=false,
     load_skills=["validation/issue-triage/triage-issue"],
     prompt="conda_env=<conda_env>, pytorch_folder=<pytorch_folder>.
     INPUT=<combined_issue verbatim>. Follow issue-triage/triage-issue
     exactly and return its merged JSON verbatim (source_issue,
     duplication, not_target, target_component, priority, category,
     overall_confidence). If it cannot identify issue_id/repo, say so
     explicitly instead of guessing.")
```

Write `step3_triage.json`, log. `source_issue.issue_id` populated ->
continue to Step 4; can't identify issue_id/repo -> **HARD STOP** (never
guess).

**Step 4 — Emit final output.** Build `final_output.json` (see Output
below), write, log. Step 5's outcome is recorded in the same file,
finalized only after Step 6.

**Step 5 — Summarize and notify the issue (upsert).** The only step that
mutates GitHub state: upsert one comment, and (conditionally) one label.
Run inline with `gh`, no subagent.

*5a. Find any existing triage comment:*

```bash
existing_comment_id=$(gh api "repos/<repo>/issues/<issue_id>/comments" --paginate \
  --jq '.[] | select(.body | startswith("[agent-issue-triage]: Automated triage result")) | .id' \
  | tail -n 1)
```

Non-empty -> update via `PATCH` (5c). Empty -> create. Lookup failure ->
log it, treat as empty (attempt create), record error in
`final_output.json`. Never hard-stop.

*5b. Build the summary.* Write `issue_dir/summary.md`. The `## Summary`
table is a **4-column GFM table** — `Field | Value | Reason | Evidence` —
so every row is self-contained and fully visible without needing to
expand anything. No collapsed `<details>` block. Keep `Reason` and
`Evidence` each to one concise sentence/clause (use `<br>` inside a cell
only for a short multi-item list, e.g. several traced symbols or several
duplicates — never a paragraph). Every value traces verbatim to
`step2_reproduce.json`/`step3_triage.json`; omit the `Reproduction` row
if Step 2 was skipped; omit the `Duplicates` detail table if
`has_duplicate` is false.

```markdown
[agent-issue-triage]: Automated triage result

## Summary

| Field | Value | Reason | Evidence |
|---|---|---|---|
| Priority | [priority.priority] ([confidence] confidence) | [priority_reason] | [failure_mode]; [failing_case_count] failing case(s)[; "regression N%" if present] |
| Category | [category.category][ / subcategory if non-empty] ([confidence] confidence) | [category_reason] | [root_cause_summary][; domain=...] |
| Need action | [need_action label, derived — see table below] | [need_action_reason] | [driver-specific — see the Evidence rules right after this table: platform/os/label for environment-blocked NEED_HUMAN; `call_path`<br>symbols for traced-but-inconclusive NEED_HUMAN or any NEED_FIX*; "N/A — inherited from duplicate <id>" if source == "skipped-duplicate-triaged"] |
| Target component | [verdict or "skipped"] -> [target_component or "N/A"] | [reason or "N/A"] | `[call_path]`<br>symbols: [traced_symbols joined by ", ", or "N/A"] |
| Duplicate | [has_duplicate ? "Yes ([confidence] confidence)" : "None found"] | [match_evidence of top duplicate, or "N/A"] | see Duplicates table below |
| Not target | [verdict if not None, else "N/A"] | [reasoning, or "check skipped — no test identity"] | [evidence joined by "; ", or "N/A"] |
| Third-party dependency | [depends_on_third_party ? "Yes: <components>" : "No"][ or "N/A — skipped"] | [reason, or "N/A"] | [third_party_dependency.evidence or "N/A"] |
| Overall confidence | [overall_confidence] | = min(duplication=[d], priority=[p], category=[c]) | [note if target-component was excluded] |

[if Step 2 ran, add one more row before Overall confidence:]
| Reproduction | [reproduced]/[total] reproduced | on [torch_version] (XPU: [xpu_available]) | [per case: "`<test_case>`: <result> (<reason or actual_error>)" joined by "<br>"] |

[if has_duplicate, add a second small table:]
### Duplicates

| Issue | State | Relevance | Action | Match evidence |
|---|---|---|---|---|
| [repo]#[issue_number] | [state] | [relevance] | [recommended_action] | [match_evidence] |

**Root cause:** [root_cause or "N/A"]

---
_Generated by the `issue-triage` agent skill. Verify before acting. Rerunning updates this same comment in place._
```

The **first line must be exactly** `[agent-issue-triage]: Automated
triage result` — required prefix, and the exact marker 5a searches for.
Never omit or reword it.

`Need action` is derived, never verbatim from any leaf skill — compute
from `target_component.source`/`.result.verdict`:

| Condition | `need_action` | `need_action_reason` |
|---|---|---|
| `source == "skipped-duplicate-triaged"` | `"Inherited from duplicate"` | `"Target-component analysis skipped; see duplicate <inherited_from>."` |
| `verdict == "NEED_FIX"` | `"Fix required (product code)"` | `reason`, else `"Root cause resolved to <target_component> product code."` |
| `verdict == "NEED_FIX_CASE"` | `"Fix required (test case)"` | `reason`, else `"Root cause resolved to the test case itself."` |
| `verdict == "NEED_FIX_3RDPARTY"` | `"Blocked — third-party dependency"` | `reason` |
| `verdict == "NEED_HUMAN"` | `"Needs human review"` | `reason`, else `"No fixable in-repo path identified; requires human triage."` |

Never fabricate `need_action_reason` — source verbatim per the table,
falling back to the fixed default only when the source field is empty.
`Need action`'s `Evidence` cell must cite whatever **actually drove this
verdict**, not the code trace by default:
- Platform/environment-blocked `NEED_HUMAN` (e.g. `preliminary_verdict`
  fired because the issue is Windows/non-Linux/non-simulator, or
  `target_component.result.reason` says it cannot be reproduced/verified
  from this environment) -> cite the blocking signal: `os`/`platform`
  fields and the matching label(s), e.g. `"os: Windows, platform: BMG
  (Arc B580); label 'os: Windows'; cannot verify from a Linux/XPU-CI
  environment"`.
- Traced-but-inconclusive `NEED_HUMAN` (tracing ran, found no clean
  in-repo path) -> cite `target_component.result.evidence`
  (`call_path`/`traced_symbols`).
- `NEED_FIX`/`NEED_FIX_CASE`/`NEED_FIX_3RDPARTY` (verdict IS the traced
  code location) -> cite `target_component.result.evidence`
  (`call_path`/`traced_symbols`).
- `"Inherited from duplicate"` -> `"N/A — inherited from duplicate
  <inherited_from>"`.
Never leave it as `"—"` when a real driver signal exists. The
`## Summary` table must render as a real 4-column GFM table (`Field |
Value | Reason | Evidence`) with every cell populated (never blank — use
`"N/A"` or `"—"` only when a leaf skill genuinely produced nothing, e.g.
a skipped check) — nothing here is fabricated or paraphrased, always
verbatim from `step3_triage.json`/`step2_reproduce.json`.
Priority/Category/Not-target show confidence inline in the `Value` cell;
Target-component/Need-action/Third-party-dependency need real evidence in
the `Evidence` cell whenever `target_component.result` is populated, even
for a "No"; the separate `### Duplicates` table needs
`state`/`relevance`/`recommended_action`/`match_evidence` per row; Overall
confidence's `Reason` cell must show its three inputs (`d`/`p`/`c`).

*5c. Upsert the comment:*

```bash
if [ -n "$existing_comment_id" ]; then
  gh api -X PATCH "repos/<repo>/issues/comments/${existing_comment_id}" -f body=@<issue_dir>/summary.md
  comment_action="updated"
else
  gh issue comment <issue_id> --repo <repo> --body-file <issue_dir>/summary.md
  comment_action="created"
fi
```

Never use `--edit-last` (matches by position, could edit an unrelated
comment). Never touch a comment lacking the exact marker. Capture the
comment URL (`gh issue comment` stdout, or re-fetch via `gh api
.../comments/${existing_comment_id} --jq '.html_url'` for PATCH). If 5a or
5c fails (permissions/rate-limit/network), this is **not** a hard stop —
log it (`result: notify-failed` + stderr), record `notification.commented
= false` with the error, never retry automatically.

*5d. Apply the fix-feasible label (conditional):*

```python
apply_label = (target_component is not None
                and target_component.result is not None
                and target_component.result.verdict == "NEED_FIX")
```

Only the literal `NEED_FIX` verdict triggers the label; never inferred
from a duplicate's verdict.

```bash
gh label create "agent:fix_feasible" --repo <repo> --color c5def5 \
  --description "Agent-triaged: fix belongs in pytorch/torch-xpu-ops product code (verdict=NEED_FIX)" --force
gh issue edit <issue_id> --repo <repo> --add-label "agent:fix_feasible"
```

Idempotent — safe to re-run. Never remove any existing label, never add
any other label. On failure, treat like 5c: log, record
`notification.labeled = false` + error, no hard-stop, no auto-retry.

**Step 6 — Finalize output and close out logging.** Update
`final_output.json` with Step 5's `notification` block, rewrite it,
append the closing log line (`result: ok`).

## Critical Error Handling (HARD STOP)

Stop immediately — no retry, no partial fallback — on: missing required
input (Step 0); Step 1 exit 1/2 (fetch failure, PR, malformed reference);
Step 2 exit 1/2 when it actually ran (env/torch/XPU/bad input); Step 3
can't establish issue identity; provider/subagent unavailable
(model/rate-limit/quota error from `task()` itself). Step 5 failures are
explicitly **excluded** — see 5a/5c/5d above; Steps 1-4 are the
deliverable, GitHub notification is best-effort on top.

On a hard stop: append `[FATAL] <step>: <error> — halting session` to
`session_log.txt`; write full details to `<issue_dir>/logs/<step>_fatal.log`
(or `.../issue_triage_orchestrator/logs/<step>_fatal.log` if `issue_dir`
wasn't computed yet); set `final_output.json.status = "failed-hard-stop"`
with `hard_stop: {step, reason}`, write it partial (never run Step 5); end
the run and report the reason and log paths — never retry automatically.

A per-case `NOT_REPRODUCED`/`CANNOT_VERIFY`/`SKIPPED`/`NO_TEST_FOUND`, or
a `NEED_HUMAN`/low-confidence verdict, is a **normal outcome**, not a hard
stop — completes through Step 6 and is reported as-is (no label for
`NEED_HUMAN`).

## Output

```python
{
    "issue": {"issue_id": int, "repo": str, "title": str, "url": str},
    "status": "completed" | "failed-hard-stop",
    "hard_stop": {"step": str, "reason": str} | None,
    "issue_dir": str,
    "extract_result": {...},          # verbatim Step 1 JSON, or partial on a hard stop
    "reproduce_result": {...} | None, # verbatim Step 2 JSON; None if skipped/not reached
    "triage_result": {...} | None,    # verbatim Step 3 JSON; None if not reached
    "notification": {                 # None if a hard stop prevented Step 5
        "summary_path": str, "commented": bool, "comment_url": str | None,
        "comment_error": str | None, "comment_action": "created" | "updated" | None,
        "existing_comment_id": int | None, "need_action": str,  # e.g. "Fix required (product code)"
        "labeled": bool, "label_error": str | None,
        "apply_label_reason": str,    # e.g. "verdict=NEED_FIX" / "verdict=NEED_HUMAN, no label"
    } | None,
    "logs": [...]  # only files actually written, e.g. step1_extract.json, summary.md, final_output.json
}
```

## Constraints

1. Steps run strictly in order: 1 -> (2.5 if 2 ran) -> 3 -> 4 -> 5 -> 6 —
   never reorder, never run ahead, never fabricate a result for a
   hard-stopped step, never run Step 5 after a hard stop. Log every step's
   raw JSON + a `session_log.txt` line as it completes, not deferred.
2. Step 5 is the ONLY step permitted to mutate GitHub state, only via
   5a-5d (comment upsert + `agent:fix_feasible`). `agent:fix_feasible` is
   applied iff THIS issue's own `target_component.result.verdict ==
   "NEED_FIX"` — never inferred from a duplicate, never applied for other
   verdicts or when skipped. Failures here are logged/recorded but never
   retried and never escalated to a hard stop.
3. Comment body's first line must be exactly `[agent-issue-triage]:
   Automated triage result` (also 5a's lookup marker — changing it breaks
   the upsert). One issue per invocation: a rerun of the SAME issue reuses
   the SAME `issue_dir` and updates the SAME marked comment.
4. The comment MUST follow the Step 5b template exactly: a single
   4-column `## Summary` table (`Field | Value | Reason | Evidence`) with
   Reason and Evidence inlined per row — no collapsed `<details>` block,
   never fabricated/paraphrased data, `Need action` always derived per
   the table in Step 5b, and a separate `### Duplicates` table whenever
   `has_duplicate` is true.

## See Also

`validation/issue-triage/extract-issue-information`, `reproduce-issue`, `triage-issue`
(and its subskills: `issue-duplication`, `check-not-target-feature`,
`issue-target-component`, `issue-priority`, `issue-category`).
</content>
