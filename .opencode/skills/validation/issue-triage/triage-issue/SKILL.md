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

## Constraints (read FIRST — govern everything below)

1. **Read-only throughout.** Never edit files, `git commit`, open PRs, or
   mutate GitHub issue state (labels, project fields). `NEED_HUMAN`/
   `NO_NEED_FIX` are reported in the output only, never acted on.

2. **Only TWO conditions ever skip steps:**
   - **(A)** Step 0 finds `not_target`/`wontfix` label → `verdict = "NO_NEED_FIX"`,
     skip Steps 1-5 entirely, go to Step 6.
   - **(B)** Step 2 returns `is_not_target == true` AND `verdict == "Not applicable"`
     → `verdict = "NO_NEED_FIX"`, skip Steps 3-5, go to Step 6.

   **Nothing else skips steps.** In particular, Step 0's `preliminary_verdict
   = "NEED_HUMAN"` (task/feature-gap/NotImplemented/Windows/perf/e2e) is
   purely informational — Steps 1-5 still run in full.

3. **Step 3 has one additional skip** (not a short-circuit — Steps 4/5
   still run): when `has_duplicate == true` AND any `duplicates[]` entry's
   `labels` contains `agent:triaged` (exact match), skip Step 3 only.
   Use a synthesized best-effort `root_cause_result` for Steps 4/5 with
   `confidence = "Low"`.

4. Never call `check-known-issue`/`gh search issues` directly —
   duplication search is entirely owned by `issue-duplication`. Never
   re-derive leaf skill verdicts — merge them verbatim.

5. One issue per invocation. Log every step immediately to
   `{issue_dir}/steps.log` — never deferred. First matching Step 0 row
   wins; never evaluate more than one.

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
**context isolation** — their internal reasoning stays in their own session.
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
| 0 | *(inline, no subagent)* | Preliminary classification + the one real short-circuit (A) |
| 1 | `issue-duplication` | Is this issue already tracked elsewhere? |
| 2 | `check-not-target-feature` | Out-of-scope/CUDA-only for XPU? (short-circuit (B) on `Not applicable`) |
| 3 | `issue-target-component` | Which component owns the fix? |
| 4 | `issue-priority` | P0-P3 assignment |
| 5 | `issue-category` | 11-bucket category + Torch Ops subcategory |

## Workflow

**Step 0 — Preliminary classification (mandatory, before Step 1).**
Evaluate directly against the Issue JSON's `labels`/`title`/`body` (or
`summary`) — no subagent needed, no test identity required. First match
wins:

| Condition | Action |
|---|---|
| `labels` contains `not_target` or `wontfix` | **Short-circuit (A).** `verdict = "NO_NEED_FIX"`. Log `result: short-circuited`. Go to Step 6. |
| Labeled `task`/`[Task]`/`[Feature]`, or broad alignment/tracking issue | `preliminary_verdict = "NEED_HUMAN"`, reason "Umbrella/task issue". **Continue to Step 1.** |
| Feature gap / `NotImplementedError` with no concrete failing test | `preliminary_verdict = "NEED_HUMAN"`, reason "Feature gap". **Continue to Step 1.** |
| Platform is Windows (or simulator) | `preliminary_verdict = "NEED_HUMAN"`, reason "Non-Linux platform". **Continue to Step 1.** |
| Perf issue with no specific failing test | `preliminary_verdict = "NEED_HUMAN"`, reason "Perf issue, no test". **Continue to Step 1.** |
| E2E benchmark accuracy issue | `preliminary_verdict = "NEED_HUMAN"`, reason "E2E/benchmark". **Continue to Step 1.** |
| None of the above | Continue to Step 1 with no `preliminary_verdict`. |

On short-circuit (A): `duplication`/`not_target`/`target_component`/
`priority`/`category` are all `None` in the merged output.

**Step 1 — Duplication check.** Always runs (skipped only on short-circuit (A)).

Build slim input → write to `{issue_dir}/triage/step1_input.json`.

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

**Step 2 — Not-target (scope) check.** Skipped only on short-circuit (A),
or when Issue JSON has no usable test identity (`test_file`/`test_class`/
`test_case` all blank AND `test_cases[]` empty) — set
`not_target_source = "skipped-no-test-case"`. Otherwise run it using the
primary test identity; `device = "xpu"` always.

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

**Short-circuit (B) check (mandatory).** Immediately after Step 2
actually runs (never on a skip):

| Condition | Action |
|---|---|
| `is_not_target == true` AND `verdict == "Not applicable"` | **Short-circuit (B).** `merged_verdict = "NO_NEED_FIX"`. Log `result: short-circuited`. Go to Step 6. |
| Otherwise | Continue to Step 3. |

On short-circuit (B): `target_component`/`priority`/`category` are `None`.

**Step 3 — Target-component routing.** Skip conditions:
- Short-circuit (A) or (B) fired → already at Step 6.
- Constraint 3 applies (duplicate with `agent:triaged`) → skip Step 3
  only, proceed to Steps 4/5 with synthesized `root_cause_result`.

Otherwise run:

Before invoking, write `{issue_dir}/triage/step3_input.json` with
ONLY: `issue_id`, `repo`, `title`, `labels`, `traceback` (last 20 lines),
`test_file`, `test_class`, `test_case`, `module`, and `reproduce_summary`
(per case: `result`, `reproduced`, `matched_error`, `actual_error` first
5 lines). Do NOT include `raw_tail` or full `reproduce_steps`.

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

Set `target_component_result` = its output, `target_component_source =
"computed"`. Write `{"skipped": true, "reason": "skipped-duplicate-triaged",
"inherited_from": <url>}` to `step3_target_component.json` if skipped. Log.

**Building `root_cause_result` for Steps 4/5:**

*When Step 3 is skipped (duplicate-triaged):*

```python
root_cause_result = {
    "root_cause": issue_json.get("summary", issue_json.get("title", "")),
    "evidence": {"traced_symbols": [], "call_path": ""},
    "domain": "N/A", "third_party_dependency": None,
    "target_component": "N/A", "verdict": "INHERITED_FROM_DUPLICATE",
}
```

Mark `confidence = "Low"` in the Step 4/5 prompts.

*When Step 3 ran:*

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

Pass this synthesized object (never the raw output) to Steps 4 and 5.

**Steps 4+5 — Priority & Category (PARALLEL).** Both run unless a
short-circuit fired. Write slim inputs to `step4_input.json` /
`step5_input.json`, then fire both:

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
Never re-derive or second-guess a leaf skill's verdict — merge verbatim.
Decide the final `verdict`:

| Condition | `verdict` |
|---|---|
| Short-circuit (A) fired | `"NO_NEED_FIX"` |
| Short-circuit (B) fired | `"NO_NEED_FIX"` |
| `preliminary_verdict` was set (no short-circuit) | `"NEED_HUMAN"` (Steps 1-5 results are all populated) |
| No short-circuit and no preliminary verdict | `None` (leaf verdicts speak for themselves) |

Write `{issue_dir}/triage/output.json`, append closing log line to
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

Write each sub-step's JSON + log line immediately after it completes.

## Output

```python
{
    "source_issue": {"issue_id": int, "repo": str, "title": str},
    "preliminary_verdict": "NEED_HUMAN" | None,
    "preliminary_verdict_reason": str | None,
    "verdict": "NEED_HUMAN" | "NO_NEED_FIX" | None,
    "short_circuit_reason": str | None,  # set only when (A) or (B) fired
    "duplication": {"has_duplicate": bool, "duplicates": [...], "confidence": "High"|"Medium"|"Low"} | None,
    "not_target": {
        "result": {...} | None,
        "source": "computed" | "skipped-no-test-case",
    } | None,
    "target_component": {
        "result": {...} | None,
        "source": "computed" | "skipped-duplicate-triaged",
        "inherited_from": str,  # only when skipped-duplicate-triaged
    } | None,
    "priority": {...} | None,
    "category": {...} | None,
    "overall_confidence": "High" | "Medium" | "Low" | None,
    "logs": [...]
}
```

Fields are `None` ONLY when their step was actually skipped by a
short-circuit. `overall_confidence` = lowest among subskills that ran.

## Example

```bash
python3 .claude/skills/validation/issue-triage/extract-issue-information/scripts/extract_basic_info.py 4344 \
  > issue_info.json
# -> feed issue_info.json + conda_env + pytorch_folder: checks Step 0.
#    If labeled not_target/wontfix -> short-circuit (A).
#    If Step 0 matches task/feature-gap/Windows/perf/e2e -> records
#    preliminary_verdict=NEED_HUMAN but CONTINUES through all steps.
```

## See Also

`issue-duplication`, `validation/check_not_target_feature`,
`issue-target-component`, `issue-priority`, `issue-category`,
`extract-basic-info`, `check-known-issue`.
