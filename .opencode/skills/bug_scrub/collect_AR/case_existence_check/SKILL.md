# case_existence_check (Phase 4c)

> **Base constraints**: This subskill is governed by [`../../base-constraints.md`](../../base-constraints.md) (C1 logs->agent_space, C2 ask-on-blocker, C3 background status, C4 no over-skip). They apply even when not restated here.

## Overview

Phase 4c of the bug_scrub workflow. Identifies issues whose test cases
could not be verified on the XPU side and emits an AR flag
(`check_case_avaliablity`) on the parent issue so a human can validate
the missing case before the issue is acted upon.

This step has **two supported execution modes**:

- **Mode A — Phase-2.4-driven** (canonical when Phase 2.4 has run): cross-references the
  boolean `xpu_case_existence` column populated by Phase 2.4
  (`analyze_ci_result/check_xpu_case_existence/`) and aggregates per issue.
  Pure mechanical — no LLM / explore-agent reasoning needed because
  classification was already done in Phase 2.4.

- **Mode B — Inline (when Phase 2.4 was skipped)**: the Phase 4b sub-agent
  applies the case-existence rule itself per issue and emits the
  `check_case_avaliablity` token directly. Phase 4c then **writes back** an
  enum `xpu_case_existence` value (`ok` / `unverified`) onto the Test Cases
  sheet for downstream visibility. The trigger condition for the inline
  rule is:

  > For each Test Case row of the issue: if `XPU Status` is blank/null
  > AND `Stock Status` is NOT in `{passed, skipped}`, the case is
  > unverified → emit `check_case_avaliablity`.

  Both modes produce the same `action_TBD` output. They differ only in
  how `xpu_case_existence` on the Test Cases sheet is populated.

## Position in the Workflow

```
Phase 4: Collect AR
    4a close_or_skip
        ↓
    4b get_AR_from_issue (+ check_pr_status)
        ↓
    4c case_existence_check   ← THIS SKILL
```

Phase 4c runs last. It appends to `action_TBD` / `owner_transferred` on
issues that were not already closed/skipped in 4a and that carry at
least one missing case.

## Preconditions

- **Mode A**: Phase 2.4 must have run — `Test Cases.xpu_case_existence` is
  populated (True / False / blank) for every row Phase 2.4 examined.
- **Mode B**: Phase 4b sub-agent applied the inline rule and emitted
  `check_case_avaliablity` in `action_TBD` for affected issues. The
  `Test Cases.xpu_case_existence` column is then **written by this skill**
  (downstream), using the enum values defined in the Outputs section.
- In both modes the Issues sheet has a populated `Reporter` column.

## Inputs

| Source | Column(s) Used |
|---|---|
| `Test Cases` sheet (Mode A) | `Issue ID`, `xpu_case_existence` (boolean), `case_existence_comments` |
| `Test Cases` sheet (Mode B) | `Issue ID`, `XPU Status`, `Stock Status` |
| `Issues` sheet | `Issue ID`, `Reporter`, existing `action_TBD`, `owner_transferred` (if already populated by 4a / 4b) |

File: `../../../../result/torch_xpu_ops_issues.xlsx` (relative from this
SKILL.md; resolved absolute path is
`opencode/issue_triage/result/torch_xpu_ops_issues.xlsx`).

## Outputs

Columns on the **Issues** sheet:

| Column | Value Written |
|---|---|
| `action_TBD` | append `"check_case_avaliablity"` using the canonical separator (see below) |
| `owner_transferred` | set to the issue's `Reporter` login (actual GitHub username — NEVER the literal string `"reporter"`) |
| `action_reason` | distinct non-empty `case_existence_comments` aggregated across the issue's Test Cases rows (Mode A) or a fixed phrase like `"XPU status blank; stock status not confirmed passed/skipped"` (Mode B). Single comment → plain string; multiple distinct comments → JSON array. Only written when `action_reason` is blank (does not overwrite values produced by Phase 4a / 4b). |

Column on the **Test Cases** sheet (Mode B output, downstream of Phase 4c):

| Column | Value Written |
|---|---|
| `xpu_case_existence` | enum `"ok"` (case verified on XPU) / `"unverified"` (Phase 4b flagged it as missing). Mode B writes this for ALL Test Cases rows, not only flagged ones. Mode A leaves Phase 2.4's boolean values as-is and does NOT rewrite this column. |

### Canonical `action_TBD` format

`action_TBD` is a `;`-delimited list of tokens. The full set of canonical
values is fixed (6 bases × optional `check_case_avaliablity` suffix = 11 forms):

| Base verb (from 4a / 4b) | With 4c suffix appended |
|---|---|
| `Close the fixed issue` | `Close the fixed issue; check_case_avaliablity` |
| `Skip issue` | `Skip issue; check_case_avaliablity` |
| `Verify fix` | `Verify fix; check_case_avaliablity` |
| `Land PR` | `Land PR; check_case_avaliablity` |
| `Request info` | `Request info; check_case_avaliablity` |
| `No action - investigate further` | `No action - investigate further; check_case_avaliablity` |

**Strict formatting rules** (sub-agents and post-processors MUST normalize):

- Separator between multi-token `action_TBD` values: `; ` (ASCII semicolon + single space). NOT comma, NOT space-only, NOT double-semicolon.
- Dash in `No action - investigate further`: **ASCII hyphen** `-` (U+002D). NOT em-dash `—` (U+2014), NOT double-hyphen `--`.
- Token spelling for the case-existence flag: literally `check_case_avaliablity` (yes — `avaliablity`, not `availability`). Do not correct the spelling; downstream tooling reads this exact string.
- If the column already has content from Phase 4a / 4b, append the 4c suffix with `; ` rather than overwriting.

A normalization pass should run at merge time (post-sub-agent) to coerce
any em-dash / double-dash / comma-separated variants to the canonical
form above.

### Downstream consumer: Phase 4d `AR`

After Phase 4c writes its final `action_TBD` token set, Phase 4d derives
an `AR` column (Action-Required bucket) from `action_TBD` + `Root Cause`
+ `Fix Approach` + `Assignee` + a per-issue `no_response` boolean. The
mapping is fully documented in `bug_scrub/SKILL.md` → "Phase 4d AR".
Key dependency on this file's tokens:

| action_TBD token written by 4a/4b/4c | AR bucket triggered |
|---|---|
| `Close the fixed issue`, `Skip issue`, `label not_target and close` | `Close/Skip` (v4.16: `Close the fixed issue` is downgraded to `Need Response` by Phase 4d when EITHER (a) 1+ Test Cases sheet rows for the Issue ID fail strict RULE 1 - `XPU Status ∈ {passed, fixed}` AND `Stock Status ∉ {fail, error, timeout}`, blank `XPU Status` counts as failure - OR (b) the row's `action_reason` admits a pending ack via regex `pending|awaiting @<user>'s ack/approval/sign-off/confirmation/response/reply/review` or `pending a final verification` or `awaiting confirmation from @<user>`. Alt-path closes with zero Test Cases sheet rows AND no ack-pending language - manual-verification, perf-investigation, won't-fix - remain `Close/Skip`.) |
| `Land PR <ref>`, PR review/CI gate verbs (`Address CI failures on PR`, `Resolve unresolved review comments on PR`, `Wait for review on PR`), or PR-like fix-path text | `Land PR` |
| `Wait for fix PR` (v4.15 canonical) | `Wait for PR` |
| `Wait for PR` (v4.14 alias) | `Wait for PR` |
| `Monitor ...` (legacy v4.6 prefix only) | `Wait for PR` (renamed bucket) |
| `Verify fix from merged PR <ref> and close` | `Verify` (v4.17: previously misrouted to `Land PR` for 11 versions; now correctly routed to `Verify` since the next-actor is the Reporter signing off on a merged fix, not a maintainer landing an open PR). Phase 4d additionally checks live PR-state via `gh pr view --json state` (24h cache) and only routes to `Verify` if state is `MERGED`. |
| `Request info`, any token starting with `@` and containing ` please ` (v4.15 generic Need-Response template - covers `please reply`/`please verify`/`please re-run`/`please confirm`/`please test`/...), or any token with ` (>1 week)` suffix | `Need Response` (or `Land PR` after PR-state downgrade - see `bug_scrub/SKILL.md` -> "PR-status downgrade matrix") |
| `No action - investigate further` (or em-dash variant `No action — investigate further`) | `Need Response` (per v4.14 / explicitly documented v4.15: assignee is investigating without a concrete next verb; surfaces in the weekly stale-request review). |
| `RETRIAGE_PRS` | (v4.15: token no longer emitted by Phase 4b; if seen in legacy data, route to `Need Response`). |

Keep the canonical spellings above stable — Phase 4d does substring
matching, so a drift (e.g. `Land-PR` instead of `Land PR`) would
break the AR derivation silently.

## Rule

For each issue `I` in the Issues sheet:

1. Gather `I`'s rows in the Test Cases sheet (match by `Issue ID`).
2. If **any** such row has `xpu_case_existence == False`:
   - Append `check_case_avaliablity` to `I.action_TBD`
     (de-duplicate — only add if not already present).
   - Set `I.owner_transferred` to `I.Reporter`
     (if `owner_transferred` already has a different value from 4a / 4b,
     union with the reporter so both owners are preserved).

If no test case for the issue has `xpu_case_existence == False`, do
nothing for that issue in this phase.

### Precedence: `check_case_avaliablity` overrides `No action - investigate further`

After the append step, do a final pass over every row whose
`action_TBD` contains both `check_case_avaliablity` and
`No action - investigate further`: drop the latter. Rationale: the
case-existence question must be resolved before any "investigate
upstream" verdict is meaningful — a missing test case cannot be
investigated further until its identity is verified or fixed. Phase 4b
emits `No action - investigate further` only when its 6-vector PR
search comes up empty, which is irrelevant once the test case itself is
in question.

`action_reason` is **not** modified — the Phase 4b PR-discovery
narrative is preserved so the future investigator still has that
context.

Note on spelling: the token written is literally
`check_case_avaliablity` (as specified by the workflow owner). Do not
correct the spelling when writing — downstream tooling reads this exact
string.

## Execution (Python sketch)

```python
import openpyxl
from collections import defaultdict

EXCEL = "opencode/issue_triage/result/torch_xpu_ops_issues.xlsx"
TOKEN = "check_case_avaliablity"

wb = openpyxl.load_workbook(EXCEL)
issues = wb["Issues"]
cases  = wb["Test Cases"]

# Header lookup
def col_idx(ws, name):
    return [c.value for c in ws[1]].index(name)

ci_issue_id   = col_idx(cases, "Issue ID")
ci_existence  = col_idx(cases, "xpu_case_existence")

# Aggregate: issue_id -> has at least one missing case
missing = defaultdict(bool)
for row in cases.iter_rows(min_row=2, values_only=True):
    iid = row[ci_issue_id]
    val = row[ci_existence]
    # Treat only strict False as "missing"; blanks/True are ignored.
    if val is False or (isinstance(val, str) and val.strip().lower() == "false"):
        missing[iid] = True

ii_id       = col_idx(issues, "Issue ID")
ii_reporter = col_idx(issues, "Reporter")
# action_TBD / owner_transferred are Phase 4 columns — create if absent
def ensure_col(ws, name):
    headers = [c.value for c in ws[1]]
    if name in headers:
        return headers.index(name)
    ws.cell(row=1, column=ws.max_column + 1, value=name)
    return ws.max_column - 1

ii_action = ensure_col(issues, "action_TBD")
ii_owner  = ensure_col(issues, "owner_transferred")

for row in issues.iter_rows(min_row=2):
    iid = row[ii_id].value
    if not missing.get(iid):
        continue

    # Append token to action_TBD (dedupe, canonical "; " separator)
    cur = (row[ii_action].value or "").strip()
    tokens = [t.strip() for t in cur.split(";") if t.strip()]
    if TOKEN not in tokens:
        tokens.append(TOKEN)
    row[ii_action].value = "; ".join(tokens)

    # Union reporter login into owner_transferred (actual GitHub username,
    # NEVER the literal string "reporter")
    reporter = (row[ii_reporter].value or "").strip()
    cur_own  = (row[ii_owner].value or "").strip()
    owners = [o.strip() for o in cur_own.split(",") if o.strip()]
    if reporter and reporter not in owners:
        owners.append(reporter)
    row[ii_owner].value = ", ".join(owners)

# Back up before write (per project convention)
import shutil; shutil.copy(EXCEL, EXCEL.replace(".xlsx",
                                                "_bk_before_phase4c.xlsx"))
wb.save(EXCEL)
```

## Validation

After running, spot-check a handful of issues:

```python
# Example: confirm issues with any False xpu_case_existence now carry
# the token.
for iid in list(missing)[:10]:
    row = next(r for r in issues.iter_rows(min_row=2)
               if r[ii_id].value == iid)
    assert TOKEN in (row[ii_action].value or "")
```

Expected post-run row counts (sanity): number of Issues rows with the
token equals `len(missing)` from the aggregation step above.

## Backup Policy

Before writing, copy the Excel to
`result/torch_xpu_ops_issues_bk_before_phase4c.xlsx`. This matches the
backup convention used by Phase 3 (`_bk_before_phase3_write.xlsx`,
`_bk_before_category_normalize.xlsx`, etc.).

## Non-Goals

- Does not re-verify `xpu_case_existence`; trusts Phase 2.4's values.
- Does not produce any per-issue narrative; the token is a flag that
  downstream triage tooling expands.

## Scripts (in this folder)

| Script | Purpose |
|---|---|
| [`run_action_reason_backfill.py`](./run_action_reason_backfill.py) | For issues whose `action_TBD` contains `check_case_avaliablity` and whose `action_reason` is blank, aggregate distinct non-empty `case_existence_comments` from the Test Cases sheet and write them into `action_reason` (single → plain string, multiple → JSON array). Backs up the workbook to `_bk_before_action_reason_backfill.xlsx` before writing. Anchored via `__file__` so it runs from any CWD. |

Typical run:

```bash
python3 opencode/issue_triage/.opencode/skills/bug_scrub/collect_AR/case_existence_check/run_action_reason_backfill.py
```

## Version

- v1.5.0 — 2026-05-24 — v4.17 Phase 4d alignment: corrected the AR-bucket mapping table (`"Verify fix from merged PR <ref> and close"` now routes to `Verify` bucket, not `Land PR`; the 11-version-old misrouting is fixed). `Land PR` row narrowed to just `Land PR <ref>` + PR review/CI gate verbs. Phase 4c's own `check_case_avaliablity` owner_transferred=Reporter rule is unaffected (it predates the v4.17 close/verify carve-out and is scoped to Phase 4c only).
- v1.4.0 — 2026-05-20 — documented new Need-Response template ("@A: please reply to @B's request for <X>") and PR-state downgrade matrix as inputs to Phase 4d AR bucket selection.
- v1.3.0 — 2026-05-20 — documented downstream consumer Phase 4d (`AR` column derivation in `bug_scrub/SKILL.md`); enumerated the substring-match contract that Phase 4d depends on for each canonical `action_TBD` token. Stable spellings are now load-bearing for AR classification.
- v1.2.0 — 2026-05-19 — added Mode B (inline rule when Phase 2.4 skipped); documented canonical `; ` separator and ASCII hyphen for `No action - investigate further`; clarified `owner_transferred` must be actual GitHub login (never literal `"reporter"`); enumerated the 11 canonical `action_TBD` forms; documented Mode B output `xpu_case_existence ∈ {ok, unverified}` on Test Cases sheet.
- v1.1.0 — 2026-04-22 — populate `action_reason` from `case_existence_comments`
  (see `run_action_reason_backfill.py`); clarified Outputs + Scripts sections.
- v1.0.0 — 2026-04-21 — initial skill.
