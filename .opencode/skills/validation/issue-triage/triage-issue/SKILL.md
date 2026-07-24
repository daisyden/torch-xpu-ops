---
name: triage-issue
description: End-to-end triage orchestrator for one GitHub issue (pytorch or torch-xpu-ops), given the JSON output of extract-basic-info plus conda_env and pytorch_folder. Step 0 classifies task/feature-gap/NotImplemented/Windows-platform/perf-issue/e2e-benchmark-accuracy issues as a preliminary NEED_HUMAN verdict without skipping the rest of the pipeline; an issue already labeled not_target or wontfix short-circuits immediately with NO_NEED_FIX instead. Then sequences issue-duplication, check-not-target-feature (short-circuits with NO_NEED_FIX on a dynamic "Not applicable" verdict), issue-target-component (skipped when a duplicate carries agent:triaged, or when not-target short-circuited), issue-priority, and issue-category as subagents, merging all verdicts into one JSON. Use when you need the full Priority/Category/Target-Component/Duplicate/Not-Target columns for an issue in one pass instead of calling each leaf triage skill separately.
---

# Issue Triage Orchestrator

Coordinates the `issue-triage/triage-issue/*` leaf skills to fully triage
one GitHub issue in a single invocation: preliminary classification,
duplication check, not-target (scope) check, target-component routing,
priority, and category — merged into one JSON. This skill orchestrates;
it does not reimplement any leaf skill's analysis logic.

**Short-circuit rules — read carefully, these are NOT the same:**

1. **Step 0's preliminary `NEED_HUMAN` classification** (task/feature-gap/
   NotImplemented/Windows-or-simulator platform/perf-with-no-test/
   e2e-benchmark-accuracy) sets a *preliminary* verdict but does **NOT**
   skip anything — Steps 1-5 still run in full, so the caller gets the
   complete duplication/not-target/target-component/priority/category
   picture even for a `NEED_HUMAN` issue. This mirrors
   `issue-target-component`'s own quick-classification gate, but at the
   orchestrator level it is informational, not a skip signal.
2. **Already labeled `not_target`/`wontfix`** (also checked in Step 0) —
   this is the one Step 0 condition that DOES short-circuit, with
   `verdict = "NO_NEED_FIX"`, skipping Steps 1-5 entirely. An existing
   label means a human already made that call; re-analyzing would be
   redundant.
3. **Step 2's dynamically-determined `Not applicable`** (not-target check
   returns `is_not_target == true`) also short-circuits with `verdict =
   "NO_NEED_FIX"`, skipping Steps 3-5.

Only conditions 2 and 3 skip remaining steps. Condition 1 (Step 0's
`NEED_HUMAN` rows) never skips anything by itself.

**You orchestrate and merge; you never fix.** Never edit files,
`git commit`, open PRs, or mutate GitHub issue state (labels, project
fields) — every leaf skill here is read-only/analysis-only, and so is
this orchestrator. `NEED_HUMAN`/`NO_NEED_FIX` are reported in the output
only, never acted on by this skill.

## Context Budget Management (CRITICAL)

This skill runs 5 sequential sub-steps, each spawning subagents. Without
discipline, the accumulated prompts and results fill context quickly.

### Rule 1: File-based input/output

- Read your input from the file path given in the prompt (e.g.
  `{issue_dir}/step3_triage_input.json`). Never ask the caller to repeat
  the JSON inline.
- Write each step's result to `{issue_dir}/triage/stepN_*.json`.
- Pass file paths (not verbatim JSON) to sub-step prompts when the JSON
  exceeds 50 lines.

### Rule 2: Slim payloads per sub-step

Each sub-step receives ONLY the fields it needs:

| Sub-step | Required fields |
|---|---|
| issue-duplication | `issue_id`, `repo`, `title`, `test_file`, `test_class`, `test_case`, `test_cases[]` (identity fields only), `traceback` (last 15 lines), `reproduce_summary` |
| check-not-target-feature | `test_file`, `test_class`, `test_case`, `traceback` (last 15 lines), `pytorch_folder` |
| issue-target-component | `issue_id`, `repo`, `title`, `labels`, `traceback` (last 20 lines), `test_file`, `test_class`, `test_case`, `reproduce_summary`, `conda_env`, `pytorch_folder` |
| issue-priority | `issue_id`, `repo`, `title`, `labels`, `priority` (existing if any), `root_cause_result` (synthesized) |
| issue-category | `issue_id`, `repo`, `title`, `labels`, `module`, `root_cause_result` (synthesized) |

Never pass `raw_tail`, full `reproduce_steps`, or unneeded `test_cases[]`
detail to steps that don't use them.

### Rule 3: Background isolation for ALL subagent steps

ALL subagent invocations (Steps 1-5) run as `run_in_background=true` for
**context isolation** — their internal reasoning (tool calls, multi-turn
conversations with their own sub-subagents) stays in their own session.
This skill only collects the file-based result.

- Steps 1→2→3 are sequential (each depends on the previous result), but
  still fire as background for isolation — wait for each before proceeding.
- Steps 4+5 are independent — fire both in parallel, then collect both.

## Inputs

| Input | Required | Notes |
|---|---|---|
| Issue JSON | yes | Output of `extract-basic-info`, optionally enriched with `results[]` from `reproduce-issue`. Needs `issue_id`, `repo`, `title`, `labels`, plus `traceback`/`test_file`/`test_class`/`test_case` (or `test_cases[]`) for a real analysis — else accepted as-is for an early `NEED_HUMAN` stop. Read from the file path provided by the caller. |
| `conda_env` | yes | Passed to Step 3's optional installed-`torch` probing. |
| `pytorch_folder` | yes | Local `pytorch`/`torch-xpu-ops` checkout; passed to Step 2 (as `PYTORCH_SRC`) and Step 3 for read-only code tracing. |

Missing `issue_id`/`repo` -> stop and report; never guess issue identity.

## Subskills Used

| Step | Skill | Purpose |
|---|---|---|
| 0 | *(inline, no subagent)* | Preliminary classification (task/feature-gap/NotImplemented/Windows/perf/e2e -> `NEED_HUMAN`, pipeline continues) plus the one real short-circuit: already labeled `not_target`/`wontfix` -> `NO_NEED_FIX`, pipeline stops |
| 1 (skipped only on the not_target/wontfix short-circuit) | `issue-duplication` | Is this issue already tracked elsewhere? |
| 2 (skipped only on the not_target/wontfix short-circuit) | `check-not-target-feature` | Out-of-scope/CUDA-only for XPU ("Not applicable" -> short-circuit `NO_NEED_FIX`) or genuine enablement gap? |
| 3 (skipped on any short-circuit or duplicate-triaged) | `issue-target-component` | Which component owns the fix? |
| 4 (skipped on any short-circuit) | `issue-priority` | P0-P3 assignment |
| 5 (skipped on any short-circuit) | `issue-category` | 11-bucket category + Torch Ops subcategory |

## Workflow

**Step 0 — Preliminary classification (mandatory, before Step 1).**
Evaluate directly against the Issue JSON's `labels`/`title`/`body` (or
`summary`) — no subagent needed, no test identity required. First match
wins:

| Condition | Action |
|---|---|
| `labels` already contains `not_target` or `wontfix` | **Short-circuit.** `verdict = "NO_NEED_FIX"` — reason "Already labeled not_target/wontfix". Skip Steps 1-5 entirely; go straight to Step 6. |
| Labeled `task`/`[Task]`/`[Feature]`, or a broad alignment/tracking issue (not a single fixable bug) | Set `preliminary_verdict = "NEED_HUMAN"` — reason "Umbrella/task issue, not a single fixable bug". **Continue to Step 1** — do not skip. |
| Title/body says "feature gap"/"blocked by missing feature"/`NotImplementedError`, with no concrete failing test | Set `preliminary_verdict = "NEED_HUMAN"` — reason "Feature gap, not a bug to fix". **Continue to Step 1.** |
| Platform is Windows (or a simulator), not a real Linux/XPU run | Set `preliminary_verdict = "NEED_HUMAN"` — reason "Non-Linux/simulator platform, not an XPU triage target". **Continue to Step 1.** |
| Performance issue with no specific failing test (regression/latency/throughput report, no reproducer) | Set `preliminary_verdict = "NEED_HUMAN"` — reason "Perf issue with no reproducible test case". **Continue to Step 1.** |
| E2E benchmark accuracy issue (model-level accuracy/perf regression, not a unit-test failure) | Set `preliminary_verdict = "NEED_HUMAN"` — reason "E2E/benchmark issue, not a unit-test bug". **Continue to Step 1.** |
| None of the above | Continue to Step 1 with no `preliminary_verdict`. |

**Only the `not_target`/`wontfix`-label row actually short-circuits.**
Every `NEED_HUMAN` row instead records `preliminary_verdict` +
`preliminary_verdict_reason` and falls through to Step 1 — Steps 1-5 run
in full regardless, so the caller still gets duplication/not-target/
target-component/priority/category context for a `NEED_HUMAN` issue
(e.g. "this Windows-only issue is ALSO a duplicate of #1234"). Carry
`preliminary_verdict` through unchanged to Step 6's merge; it never
overrides a leaf skill's own verdict — Step 6 decides the final `verdict`
per its own merge rule (see Step 6 below).

On the `not_target`/`wontfix`-label short-circuit: log it explicitly
(`result: short-circuited`, reason "Already labeled not_target/wontfix")
and go straight to Step 6 — `duplication`/`not_target`/`target_component`/
`priority`/`category` are all `None` in the merged output; only
`source_issue` and `verdict` are populated.

**Step 1 — Duplication check.** Always runs unless the not_target/wontfix
short-circuit fired above (a `preliminary_verdict` of `NEED_HUMAN` does
NOT skip this).

Build a slim duplication input with ONLY: `issue_id`, `repo`, `title`,
`test_file`, `test_class`, `test_case`, `test_cases[]` (identity fields
per entry: `test_file`, `test_class`, `test_case`, `source`), `traceback`
(last 15 lines), and `reproduce_summary` (if available). Write to
`{issue_dir}/triage/step1_input.json`.

```
task(subagent_type="general", run_in_background=true,
     load_skills=["validation/issue-triage/triage-issue/issue-duplication"],
     prompt="Issue <issue_id> (<repo>): <title>. Read input from
     {issue_dir}/triage/step1_input.json. Follow issue-duplication
     exactly; return its JSON verbatim (source_issue, has_duplicate,
     duplicates[], confidence). Write result to
     {issue_dir}/triage/step1_duplication.json.")
```

Wait for completion notification, then read
`{issue_dir}/triage/step1_duplication.json`. Log.

**Step 2 — Not-target (scope) check (conditional skip).** Skip when the
Issue JSON has no usable test identity (`test_file` blank/absent AND
`test_class`/`test_case` both blank/absent, and `test_cases[]`
empty/absent) — `check-not-target-feature` needs a concrete
`test_file`/`class_name`/`test_name`. Set `not_target_result = null`,
`not_target_source = "skipped-no-test-case"`. Otherwise run it, using the
primary `test_file`/`test_class`/`test_case` from the top-level fields
(fall back to `test_cases[0]`); `device = "xpu"` always.

```
task(subagent_type="explore", run_in_background=true,
     load_skills=["validation/check_not_target_feature"],
     prompt="Issue <issue_id> (<repo>): <title>. Is <test_class>::
     <test_case> in <test_file> out-of-scope for XPU (CUDA-only, no XPU
     equivalent -> 'Not applicable') or a genuine enablement gap (-> 'Not
     not-target'/'CPU Case')? INPUT: test_file=<test_file>,
     class_name=<test_class>, test_name=<test_case>, device=xpu,
     error_message=<traceback_last_15_lines>,
     PYTORCH_SRC=<pytorch_folder>. Follow check-not-target-feature exactly
     (export PYTORCH_SRC first, run its Step 1.5 existence pre-check before
     anything else); return its JSON verbatim (is_not_target, verdict,
     evidence, reasoning). Write result to
     {issue_dir}/triage/step2_not_target.json.")
```

Wait for completion notification, then read
`{issue_dir}/triage/step2_not_target.json`.

Set `not_target_result` = its output, `not_target_source = "computed"`.
Write `{"skipped": true, "reason": "skipped-no-test-case"}` to
`step2_not_target.json` if Step 2 was skipped. Log.

**Short-circuit on `Not applicable` (mandatory).** Immediately after Step
2 actually runs (never on a skip), check its verdict:

| Condition | Action |
|---|---|
| `is_not_target == true` AND `verdict == "Not applicable"` | **Short-circuit.** Set `merged_verdict = "NO_NEED_FIX"`. Never spawn Steps 3-5. Go straight to Step 6. |
| Otherwise (`is_not_target == false` — `"Not not-target"`/`"CPU Case"` — or Step 2 was skipped) | Continue to Step 3. |

On short-circuit, `target_component`/`priority`/`category` in the merged
output are all `None` — never synthesize placeholder values for a step
that never ran. Log it explicitly (`result: short-circuited`, reason
`"not-target verdict is 'Not applicable'"`). This mirrors
`check-not-target-feature`'s guidance that `Not applicable` "may warrant
closing as out-of-scope" — the orchestrator acts on that signal by
returning `NO_NEED_FIX` instead of spending Steps 3-5 on a fix that isn't
needed. Still read-only: reported for the caller/human, never used to
close or label the issue here.

**Step 3 — Target-component routing (conditional skip).**

| Condition | Action |
|---|---|
| Step 2 short-circuited | **Skip** — already handled above. |
| `has_duplicate == true` AND any `duplicates[]` entry's `labels` contains `agent:triaged` | **Skip.** Set `target_component_result = null`, `target_component_source = "skipped-duplicate-triaged"`, `inherited_from` = that duplicate's `issue_url`. Proceed to Step 4 with a best-effort `root_cause_result` (see below). |
| Otherwise | **Run Step 3.** |

```
task(subagent_type="deep", run_in_background=true,
     load_skills=["validation/issue-triage/triage-issue/issue-target-component"],
     prompt="Issue <issue_id> (<repo>): <title>. conda_env=<conda_env>,
     pytorch_folder=<pytorch_folder>. Read input from
     {issue_dir}/triage/step3_input.json (contains issue_id, repo,
     title, labels, traceback (truncated), test_file, test_class, test_case,
     reproduce_summary). Follow issue-target-component exactly; return its
     JSON verbatim (source_issue, verified, failure_signature, codegraph_used,
     root_cause, evidence, target_component, third_party_dependency,
     verdict, reason). Write result to
     {issue_dir}/triage/step3_target_component.json.")
```

Wait for completion notification, then read
`{issue_dir}/triage/step3_target_component.json`.

Before invoking, write `{issue_dir}/triage/step3_input.json` with
ONLY: `issue_id`, `repo`, `title`, `labels`, `traceback` (last 20 lines),
`test_file`, `test_class`, `test_case`, `module`, and `reproduce_summary`
(per case: `result`, `reproduced`, `matched_error`, `actual_error` first
5 lines). Do NOT include `raw_tail` or full `reproduce_steps`.

Wait for completion notification, then read
`{issue_dir}/triage/step3_target_component.json`.

Set `target_component_result` = its output, `target_component_source =
"computed"`. Write `{"skipped": true, "reason": "skipped-duplicate-triaged",
"inherited_from": <url>}` to `step3_target_component.json` if skipped. Log.

*Building `root_cause_result` when Step 3 is skipped.* Steps 4/5 expect a
`root_cause_result`-shaped object. Synthesize one from what's actually
available — never fabricate untraced evidence:

```python
root_cause_result = {
    "root_cause": issue_json.get("summary", issue_json.get("title", "")),
    "evidence": {"traced_symbols": [], "call_path": ""},
    "domain": "N/A", "third_party_dependency": None,
    "target_component": "N/A", "verdict": "INHERITED_FROM_DUPLICATE",
}
```

Mark `confidence = "Low"` in the Step 4/5 prompts, instructing the
subagents to fall back to `issue_info` (title/labels) alone — mirrors
`issue-category`'s existing rule for `verdict == "NEEDS_HUMAN"` with no
failure signature.

*Adapting `issue-target-component` output for Steps 4/5.* When Step 3 did
run, its output uses `target_component` (not `target_repo`) and has no
`domain`/`fix_strategy`. Build:

```python
root_cause_result = {
    "root_cause": target_component_result["root_cause"],
    "evidence": target_component_result["evidence"],
    "domain": "xpu-kernel" if target_component_result["target_component"] == "torch-xpu-ops"
              else "upstream-pytorch" if target_component_result["target_component"] == "pytorch"
              else "N/A",
    "third_party_dependency": target_component_result["third_party_dependency"],
    "target_repo": target_component_result["target_component"] if target_component_result["target_component"] in ("pytorch", "torch-xpu-ops") else "N/A",
    "fix_strategy": target_component_result["root_cause"],  # best-effort; no dedicated field
    "verdict": target_component_result["verdict"],
}
```

Pass this synthesized object (never the raw `issue-target-component`
output) as `root_cause_result` to Steps 4 and 5.

**Step 4 — Priority assignment.** Runs unless Step 2 short-circuited.

Build a slim priority input with ONLY: `issue_id`, `repo`, `title`,
`labels`, existing `priority` field (if present in issue_info), and the
synthesized `root_cause_result`. Write to
`{issue_dir}/triage/step4_input.json`.

**Step 5 — Category assignment.** Runs unless Step 2 short-circuited.

Build a slim category input with ONLY: `issue_id`, `repo`, `title`,
`labels`, `module`, and the same synthesized `root_cause_result`. Write to
`{issue_dir}/triage/step5_input.json`.

**Steps 4+5 fire in PARALLEL as background tasks** (they are independent —
both read the same `root_cause_result`, neither mutates shared state):

```
task_4 = task(subagent_type="quick", run_in_background=true,
     load_skills=["validation/issue-triage/triage-issue/issue-priority"],
     prompt="Issue <issue_id> (<repo>): <title>. Read input from
     {issue_dir}/triage/step4_input.json. Follow issue-priority
     exactly; return its JSON verbatim (source_issue, priority,
     priority_source, priority_reason, evidence, confidence). Write result
     to {issue_dir}/triage/step4_priority.json.")

task_5 = task(subagent_type="quick", run_in_background=true,
     load_skills=["validation/issue-triage/triage-issue/issue-category"],
     prompt="Issue <issue_id> (<repo>): <title>. Read input from
     {issue_dir}/triage/step5_input.json. Follow issue-category
     exactly; return its JSON verbatim (source_issue, category, subcategory,
     category_reason, evidence, confidence). Write result to
     {issue_dir}/triage/step5_category.json.")
```

Wait for both completion notifications, then read
`{issue_dir}/triage/step4_priority.json` and
`{issue_dir}/triage/step5_category.json`. Log both.

**Step 6 — Merge.** Combine Steps 0-5 into one JSON (see Output below).
Never re-derive or second-guess a leaf skill's verdict — merge verbatim,
adding only orchestrator bookkeeping (`preliminary_verdict`,
`not_target_source`, `target_component_source`, `inherited_from`,
`verdict`). Decide the final `verdict` in this order:

| Condition | `verdict` |
|---|---|
| Step 0's not_target/wontfix-label short-circuit fired | `"NO_NEED_FIX"` — `duplication`/`not_target`/`target_component`/`priority`/`category` all `None`, never fabricated. |
| Step 2 dynamically short-circuited (`is_not_target == true`, `verdict == "Not applicable"`) | `"NO_NEED_FIX"` — `target_component`/`priority`/`category` all `None`. |
| Otherwise, a `preliminary_verdict` was set in Step 0 (task/feature-gap/NotImplemented/Windows/perf/e2e) | Carry it through as `verdict = preliminary_verdict` (i.e. `"NEED_HUMAN"`) — but `duplication`/`not_target`/`target_component`/`priority`/`category` are all POPULATED from Steps 1-5's real results, since none of them were skipped. |
| No short-circuit and no preliminary verdict | `verdict = None` — Steps 1-5's own leaf verdicts (e.g. `target_component.result.verdict`) speak for themselves in the merged output. |

Write `{issue_dir}/triage/output.json`, append the closing log line to
`{issue_dir}/steps.log`.

## Logging (MANDATORY, under `{issue_dir}/triage/`)

The caller (issue-triage orchestrator) passes the `issue_dir` path. This
skill writes ALL its working files under `{issue_dir}/triage/` — NOT a
shared `agent_space/triage_issue/` directory. This ensures parallel issues
never clobber each other's intermediate files.

```
{issue_dir}/triage/
├── step1_input.json / step1_duplication.json
├── step2_not_target.json
├── step3_input.json / step3_target_component.json
├── step4_input.json / step4_priority.json
├── step5_input.json / step5_category.json
└── output.json                 # this skill's merged Output
```

Log line format (append to `{issue_dir}/steps.log`):
`[YYYY-MM-DD HH:MM:SS] triage:<sub-step> | skill: <leaf-skill> |
result: <ok|skipped|short-circuited> | duration_s: <N> | file: triage/<filename>`.

Write each sub-step's JSON + log line immediately after it completes —
never batch to the end. A Step 0 `preliminary_verdict` (without a
short-circuit) logs as `result: ok` with the reason noted in the log line
text, NOT as `short-circuited` — the pipeline did not actually stop.

## Output

```python
{
    "source_issue": {"issue_id": int, "repo": str, "title": str},
    "preliminary_verdict": "NEED_HUMAN" | None,  # Step 0's task/feature-gap/NotImplemented/Windows/perf/e2e classification; does NOT imply anything was skipped
    "preliminary_verdict_reason": str | None,    # e.g. "Non-Linux/simulator platform, not an XPU triage target"
    "verdict": "NEED_HUMAN" | "NO_NEED_FIX" | None,   # see Step 6's decision table
    "short_circuit_reason": str | None,  # set only when Steps 1-5 (or 3-5) were actually skipped
    "duplication": {"has_duplicate": bool, "duplicates": [...], "confidence": "High"|"Medium"|"Low"} | None,
    "not_target": {
        "result": {...} | None,        # None if source == "skipped-no-test-case"
        "source": "computed" | "skipped-no-test-case",
    } | None,
    "target_component": {
        "result": {...} | None,        # None if skipped-duplicate-triaged
        "source": "computed" | "skipped-duplicate-triaged",
        "inherited_from": str,         # only set when skipped-duplicate-triaged
    } | None,
    "priority": {...} | None,          # verbatim issue-priority output
    "category": {...} | None,          # verbatim issue-category output
    "overall_confidence": "High" | "Medium" | "Low" | None,  # min() over subskills run; None only when Steps 1-5 were skipped
    "logs": [...]  # only files actually written
}
```

`duplication`/`not_target`/`target_component`/`priority`/`category` are
`None` ONLY when Steps 1-5 (or 3-5) were actually skipped — i.e. on the
not_target/wontfix-label short-circuit, Step 2's dynamic short-circuit,
or (for `target_component` alone) the duplicate-triaged skip. A Step 0
`preliminary_verdict` by itself never causes any of these to be `None` —
Steps 1-5 ran and populated them normally.

`overall_confidence` = lowest confidence among duplication/priority/
category that actually ran with a computed verdict (`target_component`
excluded when skipped — that path already degrades priority/category to
`Low`). `not_target` has no confidence field and is never included. It is
`None` only when Steps 1-5 were skipped (a short-circuit), never merely
because Step 0 set a `preliminary_verdict`.

## Constraints

1. Step 0's preliminary classification (task/feature-gap/NotImplemented/
   Windows-or-simulator platform/perf-with-no-test/e2e-benchmark-accuracy)
   sets `preliminary_verdict = "NEED_HUMAN"` and its reason, but does NOT
   skip Steps 1-5 — the full pipeline still runs. The ONLY Step 0
   condition that skips anything is an existing `not_target`/`wontfix`
   label, which short-circuits with `verdict = "NO_NEED_FIX"`. First
   matching row wins; never evaluate more than one Step 0 row.
2. Step 1 (`issue-duplication`) always runs unless the not_target/wontfix
   short-circuit fired — never skip it for any other reason, and never
   skip it merely because Step 0 set a `preliminary_verdict`.
3. Step 2 (`check-not-target-feature`) is skipped when the Issue JSON has
   no usable test identity — not merely because the issue looks like a
   perf/e2e issue by title alone (that classification belongs to Step 0
   and does not skip Step 2).
4. **Short-circuit on `Not applicable`**: when Step 2 runs and returns
   `is_not_target == true` with `verdict == "Not applicable"`, set
   `verdict = "NO_NEED_FIX"` and skip Steps 3-5 entirely — never spawn or
   fabricate them. `"Not not-target"`/`"CPU Case"` or a skipped Step 2
   does NOT short-circuit — continue to Step 3 normally.
5. Step 3 is skipped when EITHER (a) the not_target/wontfix short-circuit
   or Step 2's dynamic short-circuit fired, OR (b) a duplicate exists with
   `agent:triaged` in its `labels` (exact match) — not merely because a
   duplicate exists, and not merely because Step 0 set a
   `preliminary_verdict`.
6. Steps 4/5 always run UNLESS one of the two real short-circuits fired —
   including when only Step 3 was skipped for the duplicate-triaged
   reason, using the synthesized best-effort `root_cause_result` with
   degraded confidence. A Step 0 `preliminary_verdict` never skips
   Steps 4/5.
7. Never call `check-known-issue`/`gh search issues` directly —
   duplication search is entirely owned by `issue-duplication`. Never
   re-derive `not_target`/`target_component`/`priority`/`category`
   yourself — merge each leaf skill's verdict verbatim.
8. Read-only/analysis-only throughout: no edits, no `git commit`, no `gh`
   mutation (including never applying `agent:triaged`). `NEED_HUMAN`/
   `NO_NEED_FIX` are reported only, never acted on by this skill.
9. One issue per invocation; batch sweeps invoke once per issue. Log
   every step's raw JSON + a `{issue_dir}/steps.log` line as it completes —
   never deferred. Only the two real short-circuits log as `result:
   short-circuited`; a Step 0 `preliminary_verdict` alone logs as
   `result: ok`.

## Example

```bash
python3 .claude/skills/validation/issue-triage/extract-issue-information/scripts/extract_basic_info.py 4344 \
  > issue_info.json
# -> feed issue_info.json + conda_env=<env> + pytorch_folder=<path>: first
#    checks Step 0. If labeled not_target/wontfix -> short-circuits with
#    verdict=NO_NEED_FIX (Steps 1-5 never run). If Step 0 instead matches
#    task/feature-gap/NotImplemented/Windows/perf/e2e, records
#    preliminary_verdict=NEED_HUMAN but CONTINUES: runs issue-duplication,
#    conditionally check-not-target-feature (which may itself
#    short-circuit with verdict=NO_NEED_FIX on a dynamic "Not applicable"
#    result), conditionally issue-target-component, then issue-priority
#    and issue-category, returning the fully merged JSON above either way.
```

## See Also

`issue-duplication`, `validation/check_not_target_feature`,
`issue-target-component`, `issue-priority`, `issue-category`,
`extract-basic-info`, `check-known-issue`.
</content>
