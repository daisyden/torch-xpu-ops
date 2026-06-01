# Generate Report Skill

## Overview

Final stage of the bug-scrub pipeline. After Phases 1–4d have populated the
Issues sheet of `result/torch_xpu_ops_issues.xlsx` with per-issue AR
(`action_TBD`, `action_reason`, `owner_transferred`, **and the Phase 4d
`AR` column**) and triage fields (`Category`, `Priority`, `Dependency`,
`Root Cause`, `Fix Approach`), this skill renders the human-readable
`result/bug_scrub.md` report and one per-issue detail file under
`result/details/<id>.md`.

The report is grouped by the **`AR` column** (Phase 4d). The legacy
17-leaf `action_Type` taxonomy and its `run_action_type.py` populator are
no longer part of Phase 5 — `gen_bug_scrub_md.py` reads `AR` from the
Issues sheet directly.

## When to Use

Run only after **Phase 4d** (AR-column derivation) has completed and the
Issues sheet is stable. The report includes open issues only. Re-running is
idempotent — the script overwrites its outputs cleanly.

---

## AR Bucket Sections (6 buckets, canonical order)

`AR` is a multi-value, `; `-delimited cell written by Phase 4d. The
report emits one flat section per bucket, in this order:

```
Close/Skip
Need Owner
Land PR
Monitor
Need Response
Need check case existence
```

A seventh section, **`UNCLASSIFIED`**, lists rows whose `AR` is empty. It should normally be empty after Phase 4d backfill; non-empty rows indicate a classification bug.

Because `AR` is multi-value, an issue with `Need Owner; Need Response`
appears in BOTH the `Need Owner` and `Need Response` sections. Counts
shown per section reflect membership, not exclusive partition; the
statistics section at the end reports both per-bucket counts and the
distinct combined-value distribution (matches the Phase 4d output).

### PR hyperlink rendering

PR references in `action_TBD` cells are rendered as markdown links: `[<ref-display>](<url>)`. Refs are detected by regex `(?:https://github\.com/[\w.-]+/[\w.-]+/pull/\d+|[\w.-]+/[\w.-]+#\d+|\bPR\s*#?\d+)`. Bare `#N` / `PR #N` defaults to repo `intel/torch-xpu-ops`. Full PR URL form keeps display text identical to URL. `gen_bug_scrub_md.py` runs this linkifier on every `action_TBD` cell at render time; the workbook source stores plain text in `cell.value` and the URL in `cell.hyperlink` (openpyxl `Hyperlink` attribute). Excel renders it as a clickable link; `openpyxl.load_workbook(data_only=True)` returns the plain text correctly. (Earlier draft used `=HYPERLINK()` formula but that returns None when reading with `data_only=True`, breaking the report regenerators.) For Need-Response rewrites where the truncated 120-char display differs from the full text, the full text is stored as an `openpyxl.comments.Comment` on the cell (Excel renders it as a hover tooltip).

---

## Per-Issue Detail Files

Each row writes `result/details/<issue_id>.md` containing the issue
metadata, `action_TBD` / `action_reason` / `Root Cause` / `Fix Approach`,
and up to three test-case tables:

| Section | Source sheet | Issue-ID column | Columns |
|---|---|---|---|
| `## UT Test Case Results (N)` | `Test Cases` | `Issue ID` | # \| Test Case \| Test File \| XPU Status \| Stock Status \| **Local Status** \| XPU Case Exist \| Error Message |
| `## E2E Test Case Results (N)` | `E2E Test Cases` | `Issue ID` | # \| Model \| Phase \| Dtype \| XPU Accuracy Status \| **Local Status** \| Error Message |
| `## Others Test Case Results (N)` | `Others` | `ID` (NOT `Issue ID`) | # \| Title \| **Local Status** \| Error Message |

`Local Status` was added across all three tables to surface per-case
local-verification state inline with the issue triage view. The
`Others` sheet uses the `ID` column as the issue identifier — this is
distinct from the `Issue ID` column on the other two sheets and the
ingestion code MUST handle both header names.

After the three test-case tables, a `## Test Cases & Traceback` section
renders any row with a non-empty Traceback (UT + E2E only — the Others
sheet has Traceback but it is not duplicated into this section to avoid
noise).

---

## Scripts (in this folder)

The script anchors paths on the repo root via
`Path(__file__).resolve().parents[7]`, so it is safe to run from any CWD.

| Script | Purpose |
|---|---|
| [`gen_bug_scrub_md.py`](./gen_bug_scrub_md.py) | Reads open rows from Issues + Test Cases + E2E Test Cases + Others sheets. Groups Issues by the `AR` column (multi-value, multi-section). Emits `result/bug_scrub.md` plus `result/details/<id>.md` per open issue. |
| [`run_action_type.py`](./run_action_type.py) | **DEPRECATED** — kept on disk for reference only. The `action_Type` column is no longer consumed by Phase 5 or Phase 5b. Do not invoke as part of Phase 5. |

This skill is **purely presentational**: it does not call `gh`, does not
re-query GitHub, and does not rewrite `action_TBD` / `action_reason` /
`AR`. Those columns are the responsibility of Phase 4b
([`analyze_issue/get_AR_from_issue/`](../../analyze_issue/get_AR_from_issue/SKILL.md))
for `action_TBD` and Phase 4d (in [`bug_scrub/SKILL.md`](../../SKILL.md))
for `AR`. If a row reaches Phase 5 with the wrong AR, the fix belongs
in Phase 4d — not here.

---

## Execution Order

```
gen_bug_scrub_md.py               # render bug_scrub.md + details/<id>.md
```

Typical invocation:

```bash
python3 opencode/issue_triage/.opencode/skills/bug_scrub/collect_AR/generate_report/gen_bug_scrub_md.py
```

---

## Inputs / Outputs

| | Path (relative to repo root) |
|---|---|
| Input Excel | `opencode/issue_triage/result/torch_xpu_ops_issues.xlsx` |
| Output report | `opencode/issue_triage/result/bug_scrub.md` |
| Output per-issue | `opencode/issue_triage/result/details/<issue_id>.md` |

---

## Path Reference

The script locates the repo root via:

```python
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[7]
```

The skill folder is 7 directory levels under the repo root:

```
<repo>/opencode/issue_triage/.opencode/skills/bug_scrub/collect_AR/generate_report/
    0       1            2       3      4       5         6              7
```

If the skill is ever moved, update `parents[N]` accordingly.

---

## Skill Metadata

- **Version**: 2.0.0
- **Created**: 2026-04-22
- **Updated**: 2026-05-20 v2.2.1 — Corrected PR-hyperlink workbook storage from =HYPERLINK formula to cell.hyperlink attribute (formula breaks openpyxl data_only=True reads).
- **Updated**: 2026-05-20 v2.2.0 — added PR hyperlink rendering rule for action_TBD column.
- **Updated**: 2026-05-20 v2.1.0 — added `Land PR` as a sixth AR bucket; v2.0.0 — replaced legacy `action_Type` 17-bucket grouping with `AR`-column-driven 5-bucket grouping (multi-value, multi-section). Added Others-sheet ingestion + `Local Status` column on UT/E2E/Others detail tables. `run_action_type.py` deprecated.
- **Updated**: 2026-04-27 v1.2 (clarified that Phase 5 is purely presentational)
- **Updated**: 2026-04-22 v1.0 (initial split of Phase 5 from analyze_issue)
- **Consumes**: Phase 1–4d output in `result/torch_xpu_ops_issues.xlsx`
- **Produces**: `result/bug_scrub.md` + `result/details/<id>.md` per issue
