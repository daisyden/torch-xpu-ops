---
name: issue-duplication
description: Check whether a GitHub issue is a duplicate of an existing issue in intel/torch-xpu-ops or pytorch/pytorch. Accepts either the JSON output of extract-basic-info/reproduce-issue, or a raw issue link/intel-torch-xpu-ops issue id (in which case extract-basic-info is called first). Delegates the actual search to check-known-issue. Use before filing a new issue, or when triaging an existing issue, to avoid duplicate reports.
---

# Issue Duplication Check

Determine whether a GitHub issue already has a **duplicate** filed in
`intel/torch-xpu-ops` or `pytorch/pytorch`. This is a thin wrapper: it does
NOT reimplement search — it delegates to the `check-known-issue` skill (which
already does the deterministic + heuristic + webfetch search across both
repos) and reframes the result as a duplication verdict.

## Inputs — two modes

**Mode A (preferred):** JSON from `extract-basic-info` or `reproduce-issue`,
passed directly.

**Mode B:** an issue URL or bare number (defaults to `intel/torch-xpu-ops`).
Call the real script first, then treat its output as Mode A input — never
hand-parse the issue yourself:

```bash
python3 .claude/skills/validation/issue-triage/extract-issue-information/scripts/extract_basic_info.py <issue_number_or_url>
```

If it exits non-zero, report the failure; do not guess at issue content.

## Workflow

1. **Normalize input** to get: `issue_id`, `repo` (for self-exclusion),
   `title`, `test_file`, `test_class`, `test_case` (or each entry of
   `test_cases[]`), and an `error_message` — use `traceback` if present, else
   `title`. For `reproduce-issue` output, also pull `actual_error`/
   `test_file`/`test_class`/`test_case` from each `results[]` entry.

2. **Invoke `check-known-issue`** with those fields: `test_file`,
   `class_name=test_class`, `test_name=test_case`, `error_message`, `device`
   (if known). If `test_cases[]` has multiple entries, invoke it once per
   entry (in parallel) and merge the results.

```
task(subagent_type="explore", run_in_background=false,
     load_skills=["validation/check-known-issue"],
     description="Check known issue for <issue_id>",
     prompt="[CONTEXT] Checking for duplicates of GitHub issue <issue_id>
     (<repo>): <title>. [GOAL] Search both intel/torch-xpu-ops and
     pytorch/pytorch for an existing issue matching this failure. [INPUT]
     test_file=<test_file>, class_name=<test_class>, test_name=<test_case>,
     error_message=<error_message>, device=<device_if_known>. [REQUEST]
     Follow the check-known-issue skill exactly and return its JSON output
     verbatim (has_known_issue, matches[], search evidence).")
```

For multiple `test_cases[]` entries, fire one such call per entry (in
parallel, `run_in_background=true`) and merge all returned `matches[]`
before Step 3. Write each raw result verbatim to
`agent_space/issue_duplication/step2_check_known_issue_<n>.json` (n = 0 for
a single case, or the case index) and append a `session_log.txt` line per
call (see Logging below).

3. **Self-exclude.** Drop any `matches[]` entry whose `(repo, issue_number)`
   equals `(repo, issue_id)` of the source issue itself.

4. **Reframe as duplication output:**

```python
{
    "source_issue": {"issue_id": int, "repo": str, "url": str, "title": str},
    "has_duplicate": bool,          # = has_known_issue, post self-exclusion
    "duplicates": [
        {
            **match,                # issue_url, repo, issue_number, title, state, labels, match_evidence, relevance
            "recommended_action": "close_as_duplicate" | "cross_link" | "merge_context"
        }
        for match in check_known_issue_result["matches"]  # minus self
    ],
    "confidence": "High|Medium|Low"  # High if check-known-issue's search fully succeeded, else Low
}
```

`recommended_action`:
- `close_as_duplicate` — HIGH relevance, **same repo** as the source issue.
- `cross_link` — HIGH/MEDIUM relevance in the **other** repo (an xpu op_ut
  issue and a matching pytorch/pytorch upstream issue legitimately coexist —
  never recommend closing one just because the other exists).
- `merge_context` — MEDIUM/LOW relevance, overlapping but not identical scope
  (e.g. a broader class-level tracker) — link/comment, don't close.

## Constraints

1. Never reimplement the search — always delegate to `check-known-issue`.
   Do not call `gh search issues` / `webfetch` directly in this skill.
2. Self-exclusion is mandatory: never report the source issue as its own
   duplicate.
3. Cross-repo matches are always `cross_link`, never `close_as_duplicate`.
4. If `check-known-issue` reports search failures (per its own "search must
   actually succeed" rule), propagate that as `Low` confidence here too —
   never claim `has_duplicate=false` on an incomplete search.
5. Mode B must call the real `extract-basic-info` script — never hand-parse.
6. Read-only/analysis-only: never edits/closes/labels issues or files.
   `close_as_duplicate` is a recommendation for the caller to act on.
7. Log every `check-known-issue` call's raw result to
   `agent_space/issue_duplication/` plus a `session_log.txt` line — never
   defer logging to the end (see Logging below).

## Logging (MANDATORY, under `agent_space/`)

Write to `agent_space/issue_duplication/` (create it if absent) and append
to `agent_space/session_log.txt`:

```
agent_space/
├── session_log.txt
└── issue_duplication/
    ├── step2_check_known_issue_<n>.json   # raw check-known-issue result, one per test_cases[] entry
    └── output.json                        # this skill's own reframed duplication output
```

`session_log.txt` line format:

```
[YYYY-MM-DD HH:MM:SS] issue-duplication:<step> | task_id: <id> | result: ok | file_refs: <path>
```

Write each `check-known-issue` result immediately after it returns; write
`output.json` right before returning the final reframed verdict.

## Example

```bash
# Mode B
python3 .claude/skills/validation/issue-triage/extract-issue-information/scripts/extract_basic_info.py 4344
# -> extract test_file/test_class/test_case/traceback, invoke check-known-issue, reframe per above

# Mode A, chained from reproduce-issue
python3 .claude/skills/validation/issue-triage/extract-issue-information/scripts/extract_basic_info.py \
    https://github.com/CuiYifeng/torch-xpu-ops-sandbox/issues/6 \
  | python3 .claude/skills/validation/issue-triage/reproduce-issue/scripts/reproduce_issue.py \
      --conda-env nightly --pytorch-folder ~/pytorch
# -> for each results[] entry, invoke check-known-issue, merge + self-exclude + reframe
```

## Scope

One source issue per invocation (its `test_cases[]`, if any, checked
together). For batch sweeps, invoke once per issue. Never mutates GitHub
state or repo files.
