# Bug Scrub Workflow

> **Path conventions**:
> - `${PYTORCH_REPO_ROOT}` is the pytorch checkout used by every skill in this hierarchy (sources, tests, `third_party/torch-xpu-ops`). Default `~/upstream/pytorch`. Override per run with `export PYTORCH_REPO_ROOT=/path/to/your/pytorch`.
> - `${BUG_SCRUB_SKILL_ROOT}` is the directory containing this `SKILL.md` (the `bug_scrub` skill folder). Resolve dynamically per run (e.g., `BUG_SCRUB_SKILL_ROOT=$(dirname "$(readlink -f path/to/bug_scrub/SKILL.md)")`) so the same workflow runs from any workspace clone. All in-skill static resources (e.g., `prepare_data/pytorch_xpu_backend_analysis/xpu_supported_operators_complete_list.md`) are referenced via this variable.
>
> All path examples in this document and its sub-skills use these variables; expand them before passing literal paths to shell commands.

## Overview
Comprehensive workflow for triaging torch-xpu-ops issues through 5 phases,
collecting AR (Action Required) data for each issue with deep analysis.

**Incremental by default**: when re-running on an existing Excel, each phase
skips rows that already have completed analysis columns. See
[Incremental Mode](#incremental-mode-skip-already-completed-work) for the
full skip-rule table.

## Workflow Phases

```
Phase 1: Prepare Data
    ↓
Phase 2: Analyze CI Result (match-ut → match-e2e → case-duplication → check case)
    ↓
Phase 3: Analyze Issue (dup detection → triage)
    ↓
Phase 4: Collect AR (close_or_skip → get_AR_from_issue [with check_pr_status] → case_existence_check)
```

**Relative Path Note**: In SKILL.md files, relative paths from bug_scrub/ to issue_triage root:
- `../../ci_results/` → CI artifacts
- `../../result/` → Excel results
- `../../data/` → JSON data
- `../../` prefix not shown in paths below

---

## Incremental Mode (Skip Already-Completed Work)

When re-running the pipeline on an existing `torch_xpu_ops_issues.xlsx`,
each phase MUST check for prior results and **skip rows that are already
populated**. This avoids duplicated work and preserves manually-curated
values.

### Skip Rules by Phase

| Phase | Column(s) to Check | Skip Condition |
|-------|-------------------|----------------|
| 2.3 case-duplication-detection | `duplicate_group_id` | If the cell is non-blank, skip duplicate detection for this row. Do NOT force a full case-duplication rerun in incremental mode. |
| 2.4 check_xpu_case_existence | `xpu_case_existence` | If the cell is non-blank (True or False already set), skip this case entirely. Do NOT re-run the deep analysis. Within a single issue, classify one representative blank row via explore-agent and propagate the verdict to the issue's other blank rows; rows already non-blank are never overwritten. |
| 3.3 triage_skills | `Category`, `Priority`, `Root Cause`, `Fix Approach` | If **all four** columns are non-blank for an issue, skip triage for that issue. If any of the four is blank, re-run triage for that issue and fill only the missing columns (preserve existing non-blank values). `Dependency` is optional and must not be used as a completion gate because not all issues have one. |
| 4a–4c (all Phase 4) | — | **NEVER skip.** Phase 4 always re-runs for every issue because PR status, CI results, and comment activity change frequently. Stale AR verdicts are worse than re-computation cost. |

### How to Detect "Already Done"

```python
import openpyxl

wb = openpyxl.load_workbook("result/torch_xpu_ops_issues.xlsx")

# Phase 2.3: check Test Cases sheet for duplicate detection
tc_sheet = wb["Test Cases"]
for row in tc_sheet.iter_rows(min_row=2):
    duplicate_group_id = row[col_index("duplicate_group_id")].value
    if duplicate_group_id is not None:
        # SKIP 2.3 - duplicate detection already checked
        continue

# Phase 2.4: check Test Cases sheet for case existence
for row in tc_sheet.iter_rows(min_row=2):
    xpu_case_existence = row[col_index("xpu_case_existence")].value
    if xpu_case_existence is not None:
        # SKIP 2.4 - case existence already checked
        continue

# Phase 3.3: check Issues sheet
issues_sheet = wb["Issues"]
for row in issues_sheet.iter_rows(min_row=2):
    category = row[col_index("Category")].value
    priority = row[col_index("Priority")].value
    root_cause = row[col_index("Root Cause")].value
    fix_approach = row[col_index("Fix Approach")].value
    if all(v is not None and str(v).strip() for v in
           [category, priority, root_cause, fix_approach]):
        # SKIP - already triaged
        continue
```

### Execution Logic with Incremental Checks

Each phase's "For each Issue/Case" loop MUST prepend the skip check:

```
For each Issue/Case:
    IF already_done(row, phase):    ← NEW: incremental check
        LOG "Skipping Issue #{id} for Phase X.Y - already completed"
        CONTINUE
    ... original logic ...
```

### Notes

- Phase 1 (Prepare Data) always runs fully — it fetches fresh data from
  GitHub and CI. New issues are appended; existing issues are updated with
  fresh metadata (labels, status, comments) but analysis columns are
  preserved.
- Phase 2.1–2.2 (match-ut, match-e2e) always re-run because CI results may
  have changed. Phase 2.3 case-duplication can skip rows with an existing
  `duplicate_group_id` in incremental mode.
- Phase 5 (Generate Report) always re-runs — it is purely presentational.
- When in doubt, **preserve existing values**. Never overwrite a non-blank
  analysis column unless the user explicitly requests a full re-run.

---

## Phase 1: Prepare Data

### 1.0 Test Environment Setup (PREREQUISITE)
- **Skill**: documented in `prepare_data/issue-basic-info-extraction/SKILL.md` §"Phase 1.0 — Test Environment Setup"
- **Steps**:
  1. Activate conda env (`pytorch_opencode_env`)
  2. `git pull --ff-only` in `${PYTORCH_REPO_ROOT}` and `${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops`
  3. `pip3 install --pre torch --index-url https://download.pytorch.org/whl/nightly/xpu`
  4. `pip download` then `pip install` the `pytorch-triton-xpu` nightly wheel
  5. Sync `${PYTORCH_REPO_ROOT}` HEAD to `torch.version.git_version` (safety branch preserves prior HEAD)
  6. Record versions for reproducibility
- **Trigger**: Once at the start of every bug-scrub session, before any other phase imports `torch` or runs tests. Skip with `SKIP_ENV_UPDATE=1` if a prior session in the same day already performed the update.
- **Output**: Active conda env + nightly XPU torch + triton installed + source repo synced to installed torch's git commit.

### 1.1 Issue Basic Info Extraction
- **Skill**: `prepare_data/issue-basic-info-extraction/`
- **Steps**:
  1. Fetch open issues from GitHub REST API → `data/torch_xpu_ops_issues.json` (skip with `SKIP_PHASE_1_1=1`)
  2. Fetch 5 PyTorchXPU project fields per issue via a single `gh api graphql` request
  3. **Script-first** extraction: `parse_test_cases_from_body` (matches `Cases:` blocks etc.) and `parse_e2e_info` (matches structured benchmark commands)
  4. **LLM fallback**: only consulted for issues the script extractors did not match (prose-only references, embedded scripts, URL-bearing reproducers). Cached by body hash; never overrides a successful script match.
  5. Build sheets via routing decision tree (UNITTEST → E2E → Others)
  6. Post-pass: align `Issues.Test Module` to actual placement
- **Routing**: Each issue is placed in **exactly one** of Test Cases / E2E Test Cases / Others / Not applicable. The `Issues.Test Module` column is rewritten in the final post-pass to equal the sheet the issue is in. See the skill's SKILL.md for the full decision tree, schema, and invariants.
- **Output**: `result/torch_xpu_ops_issues.xlsx` with five sheets:
  - **Issues** (20 columns): basic info + Type/`GitHub Type`/Module/Test Module/Dependency/Priority + `PyTorchXPU Status` / `PyTorchXPU Estimate` / `PyTorchXPU Depending` / `PyTorchXPU Short Comments` (cols 17–20). `GitHub Type` is the native GitHub `issueType.name` (fetched via `gh api graphql`); issues whose native type is `Task` are **dropped at Phase 1** (no row written) per v4.30 policy.
  - **Test Cases**
  - **E2E Test Cases**
  - **Others**: issues with no enumerable UT/E2E test (columns: ID, Title, Labels, reproduce step, Error Message, Traceback). Phase 1 leaves `reproduce step`, `Error Message`, and `Traceback` blank; Phase 2.5 performs deep issue-body extraction immediately before local verification.
  - **Not applicable** (owned by Phase 1.3, not by this step)
- **Priority initialization**: If `PyTorchXPU Priority` is non-blank and matches `P0`/`P1`/`P2`/`P3`, set Excel `Priority` to that value. The other four PyTorchXPU fields are written verbatim (sanitized for Excel illegal chars, truncated to 32767 chars).

### 1.2 Download CI Result
- **Skill**: `prepare_data/download_ci_result/`
- **Steps**: Download artifacts from torch-xpu-ops + stock pytorch CI
- **Output**: CI artifacts in `ci_results/`

### 1.3 Create Not Applicable Sheet
- **Skill**: `prepare_data/create-not-applicable-sheet/`
- **Default mode (carry-forward)**: Copy the `Not applicable` sheet verbatim from the latest trusted backup `result/torch_xpu_ops_issues_bk_*.xlsx`. No re-analysis.
- **Deep-analysis mode**: Used for **new** wontfix/not_target issues only. Spawn one Explore Agent per issue to determine root-cause category (CUDA-specific / hardware / deprecated / etc.) and the operation/API involved. Append the row to the sheet.
- **Output**: `Not applicable` sheet in Excel with columns Issue ID, Title, Operation/API, Category, Technical Details, Workaround, Labels.
- **Note**: This skill never touches Issues / Test Cases / E2E Test Cases / Others.

### 1.4 PyTorch XPU Backend Analysis
- **Skill**: `prepare_data/pytorch_xpu_backend_analysis/`
- **Steps**: Deep analysis of XPU operator implementation in `${PYTORCH_REPO_ROOT}` + `${PYTORCH_REPO_ROOT}/third_party/torch-xpu-ops` (registration mechanism, dispatch paths, dependency libraries)
- **Resources** (consumed by Phase 3.3 dependency analysis):
  - `result/pytorch_xpu_backend_analysis.md` — narrative architecture/dependency document (~51KB). Referenced by Phase 3.3 Step 5 (root cause) for deep context. The same narrative is also embedded in this skill's `SKILL.md` body for local reading.
  - `prepare_data/pytorch_xpu_backend_analysis/xpu_supported_operators_complete_list.md` — **authoritative operator → dependency mapping** for all 749 XPU-supported operators. This registry is bundled inside the skill folder and read by Phase 3.3 Step 6 (`get_operator_dependencies()` in `SKILL_Triage_Logic.md`) to populate the `Dependency` column with values like `oneDNN`, `oneMKL`, `SYCL kernel:<file>`, `xccl`, `triton`, `CPU fallback`, `upstream-pytorch`, `driver`, `oneAPI`, or blank.
- **Re-run cadence**: One-time / on-demand. The operator registry ships with the skill as a static resource; do not regenerate it as part of routine Phase 1 runs. Re-run only when XPU operator coverage changes materially (new ops registered, new dispatch layer added, new dependency library introduced). Stale operator list causes Phase 3.3 to misclassify dependencies for newly-registered operators.

---

## Phase 2: Analyze CI Result

### 2.1 Match UT CI Matching
- **Skill**: `analyze_ci_result/match-ut-ci-matching/`
- **Output**: Match UT test cases to CI results

### 2.2 Match E2E CI Matching
- **Skill**: `analyze_ci_result/match-e2e-ci-matching/`
- **Output**: Match E2E benchmark tests to CI results

### 2.3 Case Duplicate Detection
- **Skill**: `analyze_ci_result/case-duplication-detection/`
- **Output**: `duplicate_group_id` column

### 2.4 Check XPU Case Existence
- **Skill**: `analyze_ci_result/check_xpu_case_existence/`
- **Runner note**: `analyze_ci_result/test_cases/run_processor_steps.py --steps 3` only prints the manual worklist; it does **not** classify cases or call an automated LLM endpoint. Complete this phase by applying the `check_xpu_case_existence` skill to each listed issue with mandatory explore-agent assisted deep analysis.
- **Trigger**: Rows where BOTH XPU Status AND Stock Status are blank
- **Scope**: FIRST blank case per issue only

| Output Column | Type | Description |
|--------------|------|-------------|
| `xpu_case_existence` | Boolean | True = case found, False = not found |
| `case_existence_comments` | Text | Explanation of where/how/not found |

**Execution Logic**:
```
For each Issue:
    Find Test Cases
    For each Test Case row:
        IF XPU_Status blank AND Stock_Status blank:
            Run explore-agent assisted deep analysis on this test case
            Set xpu_case_existence, case_existence_comments
            SKIP remaining test cases for this Issue  ← FIRST ONE ONLY
            BREAK
```

### 2.5 Local Case Verification
- **Skill**: `analyze_ci_result/local-case-verification/`
- **Default scope (v1.1+)**: **Others sheet only.** UT and E2E lanes are
  opt-in via `--lanes ut,e2e,others` or `--all-lanes`; the rationale is that
  Phase 2.1/2.2 already exercise UT/E2E against CI artifacts, while Others
  rows have no CI coverage by definition (no enumerable test).
- **Trigger** (when broadened beyond default):
  - issues with all rows on `Test Cases` blank → run UT path with `PYTORCH_TEST_WITH_SLOW=1 pytest <file> -k <case>`
  - issues with all rows on `E2E Test Cases` blank → run the E2E reproducer recorded on the E2E row
  - non-performance issues on `Others` sheet → deep-extract a runnable reproducer from the current issue body, then run it locally — **default lane**; performance issues are marked `skipped` and not locally benchmarked
- **Precondition**: Phase 1.0 (test env setup) must have run in this session. v1.2 of this skill no longer performs `git pull`, nightly install, or commit sync — those moved to Phase 1.0.
- **Outputs** (on `Issues` sheet only — CI per-row columns remain authoritative and untouched):

| Output Column | Type | Description |
|--------------|------|-------------|
| `Local status` | Enum | `pass` / `fail` / `error` / `timeout` / `skipped` / `notfound` / `noreproducer` / `mixed` / blank (CI covered); `skipped` includes performance Others issues and unavailable XPU torch |
| `Local status comments` | Text | Relative path to per-issue log under `local_logs/` |

- **Trust rule**: Phase 3.3 (`triage_skills`) and Phase 4a (`close_or_skip`) may treat `Local status` as authoritative evidence **iff** the issue's platform is **PVC** and OS is **Linux**. Otherwise treat as informational. The check is performed by the consuming phase, not by 2.5 itself.
- **Invariant**: 2.5 never modifies `Test Cases.XPU Status` / `E2E Test Cases.XPU Status` / `Stock Status`. Those remain the CI-authoritative source.

---

## Phase 3: Analyze Issue

### 3.1 Duplicated Issue Detection
- **Skill**: `analyze_issue/duplicated-issue-detection/`
- **Output**: Issue duplicate groups

### 3.3 Triage Skills
- **Skill**: `analyze_issue/triage_skills/`
- **Trigger**: Each issue in Issues sheet
- **Scope**: Full deep triage for EACH issue (NO batch script - one-by-one)

| Output Column | Type | Description |
|--------------|------|-------------|
| `Category` | Text | Issue type (bug/feature/perf/API/ci/distributed) |
| `Priority` | Text | P0/P1/P2/P3 |
| `Dependency` | Text | Components (torch-xpu-ops, PyTorch core, CI, upstream) |
| `Root Cause` | Text | Technical root cause category |
| `Fix Approach` | Text | Recommended fix strategy |

**Execution Logic**:
```
For each Issue in ['Issues' sheet]:
    Incremental Mode gate (rule from §"Skip Rules by Phase" above):
        If Category, Priority, Root Cause, AND Fix Approach are all non-blank:
            Skip this issue entirely (no fetch, no triage).
            Continue to next issue.

    Step 0 (Details Fast Path - two published-data sources, Incremental Mode):
        Source A: Highlight HTML
            Fetch (once per run, cached) https://raw.githubusercontent.com/daisyden/ai_for_validation/main/opencode/issue_triage/result/bug_scrub_highlight.html
            Find <tr data-issue="<id>"> for this issue.
            If found:
                If Category cell blank: write data-category attribute
                If Priority cell blank: write data-priority attribute (P0/P1/P2/P3)
                If Dependency cell blank: write data-dependency attribute (may be "")
            (If not found, fall through to live classification for those columns.)

        Source B: Per-issue detail markdown
            Fetch https://raw.githubusercontent.com/daisyden/ai_for_validation/main/opencode/issue_triage/result/details/<issue_id>.md
            If the file exists and parses cleanly:
                If Root Cause cell blank: copy `## Root Cause` section -> Root Cause
                If Fix Approach cell blank: copy `## Fix Approach` section -> Fix Approach
            (Incremental Mode in both sources: never overwrite an existing non-blank cell.)

        If every blank column got filled by Sources A+B, skip live triage entirely.
        Otherwise run live triage only for columns still blank.

    Run deep triage analysis only for columns that are still blank:
        Analyze title, body, error logs, AR
        Classify Category using predefined patterns       (skip if filled)
        Determine Root Cause type                          (skip if filled)
        Propose Fix Approach                               (skip if filled)
        Identify Dependency components from confirmed root cause + fix approach (skip if filled)
        Assign Priority based on severity unless Priority is already populated
        from the GitHub Projects `PyTorchXPU Priority` field (skip if filled)

    Set blank columns only (Incremental Mode preserves existing non-blank values):
        Category, Priority, Root Cause, Fix Approach, Dependency
```

---

## Phase 4: Collect AR

### Phase 4 Execution Order
```
4a. close_or_skip   → 4b. get_AR_from_issue (+ check_pr_status) → 4c. case_existence_check
```

---

### 4a close_or_skip

**Workflow**:
```
For each Issue in Issues sheet:
    1. Check if all test cases have XPU Status ∈ {passed, fixed} AND Stock Status ∉ {fail, error, timeout}
       → If YES, apply RULE 1 (Close Fixed)
    2. If not closed, check if Issue has labels 'not target' OR 'wontfix'
       → If YES, apply RULE 2 (Skip Not Target)
    3. If neither rule applies, proceed to Phase 4b (get_AR_from_issue)
```

> **owner_transferred convention** (applies to ALL of 4a/4b/4c): write the **actual GitHub login** from the issue's `Reporter` column (`Issues.Reporter` cell value, e.g. `"daisyden"`). NEVER write the literal string `"reporter"` — that is a placeholder for the field source, not the value. If multiple owners are accumulated across sub-steps, comma-join unique logins.

**RULE 1: Close Issue (Fixed)**
| Condition | Output |
|-----------|--------|
| ALL test cases of issue have `XPU Status ∈ {passed, fixed}` AND `Stock Status` not in `{fail, error, timeout}` |
| `action_TBD = "Close the fixed issue"` |
| `owner_transferred = <Issues.Reporter login>` |
| `action_reason = "Fixed and passed in CI"` |

> **v4.16 strictness note.** `XPU Status` MUST be the literal string `"passed"` or `"fixed"` after `.strip().lower()`. A blank/empty `XPU Status` cell does NOT count as passed - it counts as a violation. Likewise a `Stock Status` of `fail`, `error`, or `timeout` blocks RULE 1 even if the corresponding `XPU Status` is `passed`/`fixed`. Phase 4d enforces this via an audit pass (see below).

> **v4.16 RULE 1 audit (Phase 4d).** After Phase 4a/4b emit `action_TBD`, Phase 4d (`run_phase4d_ar.py`) re-verifies every row whose `action_TBD` contains `Close the fixed issue` against the `Test Cases` + `E2E Test Cases` sheets. Three outcomes:
> - **ok** - 1+ Test Cases rows for this Issue ID, all pass strict RULE 1 → `Close/Skip` AR bucket kept.
> - **out_of_scope** - 0 Test Cases sheet rows for this Issue ID → this is an alt-path close (manual benchmark verification, performance investigation, won't-fix) where RULE 1 was never the gating rule. `Close/Skip` AR bucket kept; row is logged as `out_of_scope` for transparency.
> - **violation** - 1+ Test Cases rows for this Issue ID with `XPU Status ∉ {passed, fixed}` OR `Stock Status ∈ {fail, error, timeout}` → `Close/Skip` AR bucket is **suppressed** and the row is force-routed to `Need Response` so the triage lead sees it in the weekly stale-request review. The violating case(s) are printed at the end of the Phase 4d run.
>
> **v4.16 pending-ack guard (Phase 4d, parallel to RULE 1 audit).** Even when RULE 1 passes (or is out_of_scope), `Close/Skip` is **suppressed** and the row is force-routed to `Need Response` if the row's own `action_reason` admits the close is gated on a still-open ack. Regex matches (case-insensitive): `pending @<user>'s ack/approval/sign-off/confirmation/response/reply/review`, `awaiting @<user>'s ack/approval/sign-off/confirmation/response/reply/review`, `pending a final verification`, `awaiting confirmation from @<user>`. Rationale: a MEMBER assignee writing "pending @X's ack" is asking for an explicit acknowledgement that has not yet arrived; silence is not consent. The matched phrase is printed at the end of the Phase 4d run.
>
> Alt-path close examples that legitimately produce `Close the fixed issue` with zero Test Cases sheet rows: `#3489` (manual cuDNN benchmark verification by 2 MEMBERs, no ack-pending language). Phase 4a/4b agents MUST NOT emit `Close the fixed issue` when Test Cases sheet rows exist but fail strict RULE 1, or when the close is gated on a still-open ack - instead emit a Need-Response verb naming the unverified case or the maintainer whose ack is still pending (e.g. `@<assignee>: please confirm <test_case_name> status before closing`, or `@<maintainer>: please ack <user>'s close request for this investigation issue`).

**RULE 2: Skip Issue (Not Target)**
| Condition | Output |
|-----------|--------|
| Issue carries a label whose **normalized form** equals `not target`, `wontfix`, or `won t fix` |
| `action_TBD = "Skip issue"` |
| `owner_transferred = <Issues.Reporter login>` |
| `action_reason = "not target feature"` |

> **Label normalization (REQUIRED for RULE 2 matching).** GitHub labels in this repo use underscores (`not_target`), hyphens, and occasional mixed case (`Not Target`, `Wontfix`, `won-fix`). Before comparing, lowercase the label and collapse any run of `_`, `-`, or whitespace into a single space. The set of canonical match strings is `{"not target", "wontfix", "won t fix"}`. Reference predicate:
>
> ```python
> import re
> def _norm_label(s: str) -> str:
>     return re.sub(r"[\s_\-]+", " ", s.strip().lower())
> NOT_TARGET_TAGS = {"not target", "wontfix", "won t fix"}
> def is_not_target(labels_cell: str) -> bool:
>     tags = (t for t in str(labels_cell or "").replace(";", ",").split(",") if t.strip())
>     return any(_norm_label(t) in NOT_TARGET_TAGS for t in tags)
> ```

**Decision Priority**:
```
1. Apply RULE 1 if all cases fixed
2. Apply RULE 2 if labeled not target/wontfix
3. ELSE → Proceed to 4b
```

---

### 4b get_AR_from_issue (includes check_pr_status)

- **Location**: `analyze_issue/get_AR_from_issue/`
- **Skill**: `analyze_issue/get_AR_from_issue/SKILL.md`
- **Execution**: After 4a, only if issue not closed/skipped
- **Trigger**: Each issue in Issues sheet
- **Integration**: Internally calls check_pr_status logic for PR analysis

| Output Column | Description |
|--------------|-------------|
| `action_TBD` | AR list from get_AR_from_issue (includes PR AR via check_pr_status) |
| `action_reason` | AR reasons from PR status analysis |
| `owner_transferred` | Owner list from PR status |

**Tools Used by get_AR_from_issue**:
1. `gh api` - GitHub API access for PR/comment data
2. `WebFetch` - Fallback for PR/issue pages
3. `Explore Agent` - Deep PR and comment analysis
4. `check_pr_status` logic - Integrated PR gate analysis

**Source Paths** (relative from bug_scrub/):
```
CI results: ../../ci_results/
Excel file: ../../result/torch_xpu_ops_issues.xlsx
```

---

### 4c case_existence_check

- **Location**: `collect_AR/case_existence_check/`
- **Cross-Reference**: Phase 2.4 xpu_case_existence data (Mode A) OR Phase 4b inline rule output (Mode B)

**Execution Logic** (Mode A — Phase 2.4 has run):
```
For each Issue:
    For each Case with XPU+Stock blank:
        IF xpu_case_existence == False:
            Append '; check_case_avaliablity' to action_TBD
            Append case_existence_comments to action_reason
```

**Execution Logic** (Mode B — Phase 2.4 skipped, inline rule applied during 4b):
```
For each Issue I:
    For each Test Case row of I:
        IF XPU Status is blank/null AND Stock Status NOT IN {passed, skipped}:
            mark I as flagged
            BREAK
    IF flagged:
        Append '; check_case_avaliablity' to action_TBD
        Set xpu_case_existence='unverified' on Test Cases sheet
    ELSE:
        Set xpu_case_existence='ok' on Test Cases sheet
```

**Canonical `action_TBD` formatting** (enforce in BOTH modes):
- Separator: `; ` (ASCII semicolon + space). NOT `,`, NOT `--`, NOT em-dash `—`.
- The "no action" verb is `No action - investigate further` (ASCII hyphen `-`, U+002D).
- Token spelling is literally `check_case_avaliablity` (the typo is intentional; downstream tooling depends on it).
- Run a normalization pass at merge time to coerce em-dash / comma / double-hyphen variants to canonical form.

See `collect_AR/case_existence_check/SKILL.md` for the full canonical-value enumeration (11 forms = 6 bases × optional 4c suffix).

---

## Phase 5: Generate Report

- **Skill**: `collect_AR/generate_report/`
- **Trigger**: After Phase 4d (AR column), once the Issues sheet is stable.

Renders the human-readable `result/bug_scrub.md` grouped by the `AR`
column written by Phase 4d. Sections are flat — one per AR bucket, in
canonical bucket-priority order: `Close/Skip`, `Need Owner`, `Land PR`,
`Wait for PR`, `Need Response`, `Need check case existence`, `Verify`, plus an
`UNCLASSIFIED` section for rows whose AR is empty (Phase 4 produced a
verb but no bucket fired). Closed issues are excluded from Phase 5 reports.

`AR` is multi-value (`; `-delimited), so an issue with `Need Owner;
Need Response` appears in BOTH the `Need Owner` and `Need Response`
sections. The legacy 17-leaf `action_Type` column and its `run_action_type.py`
populator are NO LONGER USED — Phase 5 reads `AR` directly from the
Issues sheet.

Phase 5 is **purely presentational** — it does not call `gh` or rewrite
verdict columns. PR-state correctness is owned by Phase 4b (Vector E +
Step 2.5 live re-check in `get_AR_from_issue/`); AR derivation is owned
by Phase 4d. If a row reaches Phase 5 with the wrong AR, fix it in
Phase 4d and re-run; do not patch in Phase 5.

Per-issue detail files (`result/details/<id>.md`) now include:
- `## UT Test Case Results` — adds `Local Status` column (from `Test Cases` sheet)
- `## E2E Test Case Results` — adds `Local Status` column (from `E2E Test Cases` sheet)
- `## Others Test Case Results` — NEW section, from the `Others` sheet (issues not on E2E/UT tracks). Cols: `# | Title | Local Status | Error Message`. Issue identifier on Others is the `ID` column (NOT `Issue ID`).

**Execution Order**:
```
gen_bug_scrub_md.py               # render result/bug_scrub.md + per-issue details
```

| Output | Description |
|---|---|
| `result/bug_scrub.md` | Section-per-AR-bucket report |
| `result/details/<id>.md` | Per-issue detail file (incl. UT/E2E/Others test-case tables with Local Status) |

See `collect_AR/generate_report/SKILL.md` for section layout, table
columns, and the Others-sheet ingestion contract.

---

## Phase 5b: Generate HTML Report (optional, on demand)

- **Skill**: `collect_AR/generate_html_report/`
- **Trigger**: After Phase 4d, when an interactive triage console is wanted.

Wraps Phase 5 — re-runs `gen_bug_scrub_md.py` internally so the HTML
always reflects the current workbook, then converts the markdown to a
single self-contained `result/bug_scrub.html` with:

- Per-row "Done" checkbox in each AR-bucket section; checked-state
  persists in browser `localStorage` (per-browser, not shared, not
  embedded in the file).
- Sticky filter bar with dropdowns (Assignee, Owner Transferred,
  Priority, Category, Dependency, **AR**), free-text search, and a
  "Hide Done" toggle. Filters apply across all sections and are
  AND-combined. The AR filter uses the canonical 7 buckets read
  directly from the `AR` column; the legacy `action_Type → AR` remap
  table has been removed.
- "Export Done IDs" button — copies the comma-separated list of
  done-checked issue IDs to clipboard.

Phase 5b is purely presentational and never touches the workbook.
`bug_scrub.html` is regenerated on demand and not committed by default —
`bug_scrub.md` remains the canonical, diffable artifact.

**Execution Order**:
```
gen_bug_scrub_html.py
    ├── (calls) gen_bug_scrub_md.py     # refresh result/bug_scrub.md
    └── parse markdown → render HTML    # emit result/bug_scrub.html
```

| Output | Description |
|---|---|
| `result/bug_scrub.html` | Self-contained interactive report (CSS/JS inlined) |

See `collect_AR/generate_html_report/SKILL.md` for filter mapping,
markdown subset supported, and customization points.

---

## Phase 4 Column Summary

| Phase | Column | Description |
|-------|--------|-------------|
| 2.4 | `xpu_case_existence` | (Mode A) Boolean True/False if case found by Phase 2.4 deep analysis. (Mode B, when 2.4 skipped) Enum `ok` / `unverified` written downstream by Phase 4c. |
| | `case_existence_comments` | Explanation text |
| 3.3 | `Category` | Issue category |
| | `Priority` | P0/P1/P2/P3 |
| | `Dependency` | Components |
| | `Root Cause` | Root cause type |
| | `Fix Approach` | Fix strategy |
| 4a | `action_TBD` | Close/Skip decision |
| | `action_reason` | Close/Skip reason |
| | `owner_transferred` | Reporter login (actual GitHub username, e.g. `daisyden`; NEVER the literal string `"reporter"`) |
| 4b | `action_TBD` | + PR action items |
| | `action_reason` | + PR reasons |
| | `owner_transferred` | + owner info |
| 4c | `action_TBD` | + `; check_case_avaliablity` (canonical `; ` separator) |
| | `action_reason` | + case_existence_comments |
| | `xpu_case_existence` (Test Cases sheet) | `ok` / `unverified` (Mode B only — written by Phase 4c when Phase 2.4 was skipped) |
| 4d | `AR` | Action-Required bucket(s) derived from Phase 4 outputs. See "Phase 4d AR" below. |

---

## Phase 4d AR (Action-Required classification)

After Phase 4a-4c finish writing `action_TBD`, `action_reason`,
`owner_transferred`, `Root Cause`, and `Fix Approach`, Phase 4d projects
each issue onto one or more **AR buckets** suitable for triage review.
The column is named `AR`, appended at the end of the `Issues` sheet
(do NOT hard-code a column index — look up `AR` by header on every
read/write).

### AR buckets (canonical set)

| Bucket | Meaning |
|---|---|
| `Close/Skip` | Terminal disposition; nothing further to act on. |
| `Need Owner` | Issue has no assignee — someone needs to be put on the hook. |
| `Land PR` | A concrete PR number is listed in `action_TBD` and landing/merging/verifying that PR is the next action. |
| `Wait for PR` | Fix path is known but no PR is filed yet; row is waiting for PR submission. (v4.14 rename of `Monitor`. v4.15 renamed the canonical verb itself `Wait for PR` -> `Wait for fix PR`; both verb spellings are accepted by `fires_wait_for_pr` for backward compatibility.) |
| `Need Response` | Maintainer attention required (no reply yet, or open request stale) when no PR is already the next action. |
| `Need check case existence` | Test case identity (rename / removal / refactor) must be resolved. |
| `Verify` | A PR referenced in `action_TBD` is now MERGED on GitHub AND `owner_transferred` lists the issue's `Reporter` (i.e. ownership has been transferred back to the reporter for verification). Coexists with other buckets (e.g. `Land PR; Verify`). |
| `Wait for dependency fix` | Fix is blocked on an unresolved (open) issue/PR in an upstream dependency component (`driver`, `oneAPI`, `oneDNN`, `oneCCL`, `oneMKL`, `triton`, `upstream-pytorch`). The blocking ref is recorded in `action_TBD` and `dependency_reason`. See "Phase 4e Dependency Audit" below. |
| `Submit issue` | The issue truly depends on a tracked component for which **no upstream issue/PR exists yet** and one must be filed by the Assignee. See "Phase 4e Dependency Audit" below. |
| `Add label` | The issue truly depends on a tracked component but the GitHub `Labels` field lacks the corresponding `dependency component: <Name>` label. See "Phase 4e Dependency Audit" below. |

An issue MAY carry multiple buckets. The cell is a `; `-delimited list,
same separator convention as `action_TBD`.

### PR hyperlink rendering in action_TBD

Every PR reference in `action_TBD` cells (workbook + md + html + highlight) must be rendered as a clickable hyperlink. Storage: workbook stores plain text in `cell.value` and the URL in `cell.hyperlink` (openpyxl `Hyperlink` attribute). Excel renders it as a clickable link; `openpyxl.load_workbook(data_only=True)` returns the plain text correctly. (Earlier draft used `=HYPERLINK()` formula but that returns None when reading with `data_only=True`, breaking the report regenerators.) md uses `[display](url)`; html uses `<a href="url">display</a>`. Supported ref forms use regex `(?:https://github\.com/[\w.-]+/[\w.-]+/pull/\d+|[\w.-]+/[\w.-]+#\d+|\bPR\s*#?\d+)`. Default repo for bare `#N` / `PR #N` is `intel/torch-xpu-ops`. Need-Response rewrites store the full text in `action_TBD`; existing cache entries may still attach `new_action_tbd_full` as an `openpyxl.comments.Comment` for backward compatibility.

### Derivation rules (deterministic, no LLM)

Apply ALL rules below; the result is the union of every bucket that
fires. Order in the cell follows the bucket-priority listed in the
table above (so `Close/Skip; Need Owner` not `Need Owner; Close/Skip`).

**Inputs read per issue (lookup by header, never by index):**
`action_TBD`, `action_reason`, `Root Cause`, `Fix Approach`, `Assignee`,
plus a `no_response` boolean computed from `gh issue view --json comments`
(see "no-response detection" below).

| Bucket | Fires when ANY of: |
|---|---|
| `Close/Skip` | `action_TBD` contains `Close the fixed issue` / `Skip issue` / `label not_target and close`. |
| `Need Owner` | `Assignee` is empty / blank / literal `None` / `unassigned`. Fires INDEPENDENTLY of other buckets (additive). New ingest writes unassigned values as `""`, not literal `"None"`; the literal `None` rule is defensive for old workbook data. |
| `Land PR` (downgrade from Need Response) | Maintainer-question PR-state cross-check (see "PR-status downgrade matrix" below) determined an OPEN green PR is the next action. |
| `Land PR` | `action_TBD` contains `Land PR <repo>#<number>`, `Verify fix from merged PR <repo>#<number> and close`, `Resolve unresolved review comments on PR <repo>#<number>`, or `Address CI failures on PR <repo>#<number>`. A bare `Land PR`, `Verify fix`, or PR-like wording in `Fix Approach` is NOT sufficient. |
| `Wait for PR` | `action_TBD` contains `Wait for fix PR` exactly (v4.15 canonical), OR `Wait for PR` exactly (v4.14 alias), OR (legacy v4.6 data only) `action_TBD` starts with `Monitor`, AND the row did not already fire `Land PR`. |
| `Need Response` | `action_TBD` contains `Request info`; OR `action_TBD` contains the `(>1 week)` staleness suffix on any non-PR-response verb; OR `no_response == True` (zero maintainer comments + issue > 7 days old) AND the row did not already fire `Land PR`. Maintainer = comment author with `authorAssociation` in `OWNER / COLLABORATOR / MEMBER`. |
| `Need check case existence` | `action_TBD` contains `check_case_avaliablity` (typo preserved per Phase 4c spec); OR `Root Cause` / `Fix Approach` text mentions "case removed" / "test renamed" / "missing case" / similar. |
| `Verify` | At least one PR referenced in `action_TBD` (matched as `<org>/<repo>#<n>`, `https://github.com/<org>/<repo>/pull/<n>`, or bare `#<n>` defaulting to `intel/torch-xpu-ops`) has `state=MERGED` per live `gh pr view --json state,mergedAt` AND `owner_transferred` (split on `,;|`, case-insensitive) contains a username that equals the `Reporter` column value, i.e. ownership has been transferred back to the issue's reporter. PR-state results are cached in `agent_space/phase4d_pr_state_cache.json` with a 24h TTL so newly-merged PRs flip the bucket on the next Phase 4d run. |
| `Wait for dependency fix` | `action_TBD` contains the verb `Wait for dependency fix <org>/<repo>#<N>` where `<ref>` is an OPEN issue/PR on a tracked component repo. Set by Phase 4e (Dependency Audit). |
| `Submit issue` | `action_TBD` contains `Assignee to submit issue to <component> upstream - ...`. The Assignee is responsible for filing the upstream tracking issue. Set by Phase 4e. |
| `Add label` | `action_TBD` contains `Add label '<canonical>'` (the issue truly depends on a tracked component but lacks the corresponding GitHub label). Set by Phase 4e (Dependency Audit). |

If an issue has zero maintainer issue-comments but already has a numbered PR
in `action_TBD`, do **not** classify it as `Need Response` only because the
issue thread is quiet; classify it as `Land PR` so the next action is tied to
that PR. If no PR number is present, keep it out of `Land PR` and fall back to
the other buckets such as `Need Response`, `Need Owner`, or
`Need check case existence`.

### `no_response` detection

Per issue, run `gh issue view <N> --repo intel/torch-xpu-ops --json comments,createdAt`
once. Compute:

```
maintainer_comments = [c for c in comments
                       if c.authorAssociation in {"OWNER","COLLABORATOR","MEMBER"}]
no_response = (len(maintainer_comments) == 0) and (now - createdAt).days > 7
```

Reporter's own comments do NOT count. The 7-day threshold matches the
existing `(>1 week)` staleness convention used for gate verbs in
`analyze_issue/get_AR_from_issue/AGENT_INSTRUCTIONS.md`.

### PR-status downgrade matrix (applies during Need-Response derivation)

| linked-PR state | issue state | resulting action_TBD |
|---|---|---|
| OPEN + all required CI checks green + no unresolved review threads | open | `"Land PR <ref>"` (AR downgrades to `Land PR`) |
| OPEN + red/pending CI OR unresolved reviews | open | `"Need Response"` form (R3) plus appended note: `"; PR <ref> CI <status> / reviews <status>"` |
| MERGED | open | `"@<reporter>: please confirm fix in PR <ref> resolves the issue and close"` |
| CLOSED (not merged) | open | `"@<reporter>: PR <ref> was closed without merging - please clarify next steps"` |

This matrix runs during the Need-Response rewrite, before finalizing `action_TBD`. It re-checks every linked PR candidate so a maintainer-question row becomes `Land PR` when the real next action is merging a green PR, keeps the detailed Need-Response template when CI or reviews block the PR, and asks the reporter to confirm or clarify when the linked PR has already merged or closed.

### Implementation guidance

- Read `Issues` sheet, build per-issue derivation inputs, compute AR
  set, write back to the `AR` column (create if missing — append to
  the right of the last existing column).
- Backup the workbook before writing:
  `result/torch_xpu_ops_issues_bk_before_phase4d.xlsx`.
- Pure Python — no LLM, no sub-agents. Phase 4d is deterministic post-
  processing.

---

## Phase 4e: Dependency Audit

After Phase 4b emits `action_TBD` / `action_reason` / `owner_transferred`
and **before** Phase 4d projects those onto AR buckets, Phase 4e runs a
per-issue audit of the `Dependency` column against the GitHub `Labels`,
`action_reason`, and the live issue body + comments via
`gh issue view --json body,comments`. It can append new verbs to
`action_TBD`, set `owner_transferred`, write a new
`dependency_reason` column, and (in the strict D1 case) clear the
`Dependency` cell when the fix turns out NOT to need the listed
component.

### Tracked components

The 7 dependency components covered by Phase 4e (v4.26):

| Component (canonical) | Label canonical | Upstream repo(s) for ref discovery |
|---|---|---|
| `driver` | `dependency component: driver` | `intel/compute-runtime`, `intel/intel-graphics-compiler` |
| `oneAPI` | `dependency component: oneAPI` | `intel/llvm`, `oneapi-src/level-zero` |
| `oneDNN` | `dependency component: oneDNN` | `oneapi-src/oneDNN`, `uxlfoundation/oneDNN` |
| `oneCCL` | `dependency component: oneCCL` | `oneapi-src/oneCCL`, `intel/torch-ccl` |
| `oneMKL` | `dependency component: oneMKL` | `oneapi-src/oneMKL`, `uxlfoundation/oneMath` |
| `triton` | `dependency component: Triton` | `intel/intel-xpu-backend-for-triton`, `triton-lang/triton` |
| `upstream-pytorch` | `dependency component: upstream-pytorch` | `pytorch/pytorch` |

Match is case-insensitive on the canonical name; underscore / hyphen /
whitespace variants in labels are normalized via the `_norm_label`
helper already used by RULE 2. **Strict rename**: the legacy `xccl` /
`XCCL` literal is **no longer recognized** — rows whose `Dependency`
cell says `xccl` simply fall out of Phase 4e scope and need manual
migration to `oneCCL` before re-running.

### Scope of execution

Phase 4e **defines** its rules across **every issue** (any of the 7
component names appearing in `Dependency`, `Labels`, `Root Cause`,
`Fix Approach`, or the issue title triggers the audit). Concrete runs
MAY narrow scope to "rows with non-blank Dependency" for incremental
cost reasons; the rules and outputs are the same regardless of scope.

### Rule D1 — True-dependency check (per-issue agent classification)

For each candidate issue, an explore-style sub-agent reads:
- `Title`, `Root Cause`, `Fix Approach` (from xlsx),
- the issue body and all comments (`gh issue view <N> --repo intel/torch-xpu-ops --json body,comments`),
- and emits one of: `true_dep` / `false_dep`.

`true_dep` means the fix is **blocked by** the named component — i.e.
the issue cannot be resolved until something changes upstream in that
component's repo. Reporting a symptom that surfaces in the component
is **not** sufficient; the fix path must require an upstream change.

`false_dep` means the symptom merely surfaces through the component but
the actual fix lives in `pytorch/pytorch` or `intel/torch-xpu-ops`
(e.g., a test tolerance override, a wrapper-side skip-list change, a
CMake flag tweak in this repo).

| D1 verdict | Effect |
|---|---|
| `false_dep` | Clear the `Dependency` cell (write `""`). Clear `dependency_reason`. No verb added. Phase 4d proceeds with the row's other inputs. |
| `true_dep` | Proceed to D2; populate `dependency_reason` with `<Component>: <ref> (<state>) — <one-line reason>` (or `<Component>: no upstream ref — <reason>` when none). |

**Carve-out for `upstream-pytorch` (v4.27)**: `Dependency=upstream-pytorch` means
the **root cause** is an unresolved condition in `pytorch/pytorch` that we are
waiting on upstream maintainers to fix. If the row's `upstream_ref` is a
**pytorch.git PR** (regardless of state — open, merged, closed), the path to
resolution is "land/track a PR", which is the team's own work, not a blocking
upstream dependency. Similarly, if **no `pytorch/pytorch` issue ref** can be
found, the AR is "Submit issue / file the PR ourselves", which is again our
own work, not a dependency. In both cases the row is treated as if D1 returned
`false_dep`: `Dependency` and `dependency_reason` are cleared and no D2/D3
verbs are emitted. The only configuration in which `upstream-pytorch` remains
as a dependency is when the resolved `upstream_ref` is a `pytorch/pytorch`
**issue** (open or closed) — i.e. the team has filed an issue upstream and is
waiting on the pytorch core maintainers to act. This carve-out applies **only**
to the `upstream-pytorch` component; the other 6 components keep the
unchanged D2/D3 behaviour (driver / oneAPI / oneDNN / oneCCL / oneMKL / triton
are external SDKs we cannot patch ourselves, so a PR ref against their repos
still represents an upstream dependency).

### Rule D2 — Label hygiene

After D1 confirms `true_dep` for component `C`:

| Condition | Effect |
|---|---|
| `Labels` already contains a normalized form of `dependency component: <Name>` | No action. |
| `Labels` lacks `dependency component: <Name>` | Append to `action_TBD`: `Add label '<canonical>' - <one-line reason from D1>`. AR bucket `Add label` fires in Phase 4d. |

Label normalization reuses the `_norm_label` helper (lowercase,
collapse `_`/`-`/whitespace runs to single space).

### Rule D3 — Upstream tracking ref (simplified v2)

For each `true_dep` row, scan in this order for upstream issue/PR refs
to repos in the "Upstream repo(s)" column above:
1. Existing `action_reason` text.
2. The fetched issue body.
3. The fetched issue comments (any author).

Ref shapes accepted: `https://github.com/<org>/<repo>/(issues|pull)/<N>`
and `<org>/<repo>#<N>`. Bare `#N` is **ignored** (ambiguous).

| Ref state (via `gh issue view`/`gh pr view --json state,mergedAt`) | action_TBD verb | owner_transferred | AR bucket |
|---|---|---|---|
| Open issue OR open PR | `Wait for dependency fix <org>/<repo>#<N>` | (unchanged) | `Wait for dependency fix` |
| Merged PR | `Reporter to verify the fix from <org>/<repo>#<N> landed in <component> and provide reason` | Reporter | `Verify` |
| Closed (not merged) PR OR closed issue | `Reporter to re-investigate: upstream ref <org>/<repo>#<N> was closed without resolving and provide reason` | Reporter | `Need Response` |
| No ref found at all | `Assignee to submit issue to <component> upstream - <one-line reason from D1>` | Assignee | `Submit issue` |

**Internal-tracker prefix carve-out (v4.28)**: when no github upstream ref is
found, Phase 4e additionally scans `action_reason`, `root_cause`, and
`fix_approach` for Intel-internal tracker IDs. If a component-aligned prefix
is found, the row is treated as if it had an OPEN upstream ref and emits
`Wait for dependency fix <PREFIX-NNNN>` (bucket `Wait for dependency fix`)
instead of `Submit issue`. Prefix-to-component map:

| Prefix | Tracker | Allowed Dependency |
|---|---|---|
| `MFDNN-NNNN` | Intel MFDNN (oneDNN Jira) | `oneDNN` |
| `IGC-NNNN` | Intel Graphics Compiler | `driver` |
| `GSD-NNNN` | Intel Graphics Software Driver | `driver` |
| `PTI-NNNN` | Intel Profiling Tools Interface | `driver` |
| `CMPLRLLVM-NNNN` | Intel oneAPI DPC++/LLVM compiler | `oneAPI` |
| `CMPLRTOOLS-NNNN` | Intel oneAPI compiler tools | `oneAPI` |
| `LLVMSPIRV-NNNN` | Intel LLVM SPIR-V translator | `oneAPI` |
| `CMPLR-NNNN` | Intel oneAPI compiler (generic) | `oneAPI` |

Pattern requires `-` or `_` separator and 3-7 digits (`PTI 0.16` style version
numbers are rejected). State is hardcoded as `open` because these are
Intel-internal IDs with no public API to check; `ref_kind = "internal"`.
A prefix found on a row whose `Dependency` does not match the allowed
component is ignored (e.g. a stray `MFDNN-` mention in a `driver` row).

PR/issue state results are cached at
`agent_space/phase4e_dep_ref_state_cache.json` with a 24h TTL to keep
re-runs cheap. Cache key: `<org>/<repo>#<N>`.

### Rule D4 — `dependency_reason` column (v4.26)

Phase 4e writes a new `dependency_reason` column on every `true_dep`
row, in the format:

- With upstream ref: `<Component>: <org>/<repo>#<N> (<state>) — <one-line reason>`
- Without upstream ref: `<Component>: no upstream ref — <one-line reason>`

`<state>` is one of `open` / `merged` / `closed`. The column is
read-only by downstream phases (4d ignores it; Phase 5b html surfaces
it as the hover tooltip on the `Dependency` column).

### Idempotency

Phase 4e MUST be safe to re-run. Before appending any of the new verbs,
check whether the same canonical verb (with the same `<ref>` or
`<component>`) already exists in `action_TBD` — if yes, skip. This
preserves Incremental Mode semantics: existing non-blank work is never
overwritten or duplicated.

### Inputs / outputs summary

| | Read | Written |
|---|---|---|
| Phase 4e | `Issues.{Title, Dependency, Labels, Root Cause, Fix Approach, action_TBD, action_reason, owner_transferred, Assignee, Reporter}`, live `gh issue view` for body/comments, live `gh pr view`/`gh issue view` for ref state | `Issues.{Dependency (D1 clear only), action_TBD, owner_transferred (D3 cases), dependency_reason (new column)}` |

### Execution order

```
Phase 4a → 4b → 4c → 4e (Dependency Audit) → 4d (AR derivation)
```

Phase 4e runs **before** 4d so that the new `action_TBD` verbs are
visible to the deterministic AR-bucket router. 4d does not need to know
about D1/D2/D3 directly; it only needs to recognise the new verbs.

---

## Documents Created During Workflow

| Document | Phase | Relative Path |
|----------|-------|---------------|
| `torch_xpu_ops_issues.xlsx` | 1.1 | `result/` |
| CI artifacts | 1.2 | `ci_results/` |
| Not Applicable sheet | 1.3 | In Excel |
| XPU Backend Analysis (narrative) | 1.4 | `result/pytorch_xpu_backend_analysis.md` |
| XPU Operator Registry (op → dependency) | 1.4 | `prepare_data/pytorch_xpu_backend_analysis/xpu_supported_operators_complete_list.md` |
| AR documentation | 4 | Various per skill |

---

## Path Reference (Relative from bug_scrub/)

| Destination | Relative Path |
|-------------|---------------|
| CI results | `../../ci_results/` |
| Excel results | `../../result/` |
| JSON data | `../../data/` |
| Skills root | `../../.claude/skills/` |

---

## Version
v4.31 - May 26, 2026 - Phase 4b **owner_transferred=Reporter invariant strengthened**. The prior invariant in `run_phase4b_merge.py` only cleared `owner_transferred==Reporter` for `"No action — investigate further"` rows where `Assignee` was empty. It missed 27 rows where `owner_transferred==Reporter` despite a non-empty `Assignee≠Reporter` and a non-carve-out verb (e.g. Land PR, Address CI, Wait for fix PR, Resolve review, @<user> response, Submit issue, reassess fix path). New invariant: when `owner==Reporter`, `Assignee≠Reporter` (non-empty), and the verb-token set is NOT purely carve-out (`Verify fix from merged PR`, `Close the fixed issue`, `label_not_target_and_close`, `close_as_not_planned`, `Confirm fix and close`, `Reporter to verify the fix`, `Reporter to re-investigate`) → overwrite with Assignee; if Assignee empty → clear. The legitimate case `Assignee==Reporter` is preserved untouched (the value is sourced from Assignee, not Reporter). Applied as one-shot cleanup: 27 rows reassigned to Assignee (#2888 Stonepia, #2783 daisyden, #1171 xuhancn/chunhuanMeng, ...), 2 rows cleared (#2605, #1996 — no Assignee), 40 rows left untouched (Assignee==Reporter). Phase 5 + 5b re-rendered.
v4.30 - May 26, 2026 - Phase 1 **GitHub native `issueType` ingestion + Task exclusion**. Added `fetch_all_issue_types()` + `populate_issue_types()` to `generate_excel.py` (uses `gh api graphql` because REST does not expose `issueType`). New `GitHub Type` column (column L, between `Summary`/`Type` and `Module`) records the native value verbatim. Issues with `github_type == "Task"` are dropped at Phase 1 before row append, so they never appear in xlsx / md / html / details. Existing heuristic `Type` column (populated by `classify_issue_type`) is unchanged. Applied as one-shot cleanup: 12 Task issues removed (#3503, #3266, #3189, #3150, #2766, #2327, #2207, #2199, #2140, #2128, #2127, #2063), corresponding `result/details/*.md` deleted. xlsx 300→288 rows; md 281 open issues; html 281 cards. Phase 4d untouched (no AR redistribution needed — Task rows simply gone).
v4.29 - May 26, 2026 - Phase 4e Dependency Audit **internal-tracker prefix extension**. Extended `INTERNAL_TRACKER_PREFIXES` map and `INTERNAL_TRACKER_RE` in `run_phase4e_dependency.py` with four `oneAPI` prefixes: `CMPLRLLVM` (Intel oneAPI DPC++/LLVM compiler), `CMPLRTOOLS` (oneAPI compiler tools), `LLVMSPIRV` (LLVM SPIR-V translator), `CMPLR` (generic). Regex alternation orders longer prefixes first so `CMPLRLLVM` is never partially matched as `CMPLR`. Applied as one-shot cleanup: 1 row (#3142) reclassifies from `Submit issue` to `Wait for dependency fix CMPLRLLVM-72057`. Phase 4d + 5 + 5b re-run.
v4.28 - May 26, 2026 - Phase 4e Dependency Audit **internal-tracker prefix carve-out**. New fallback inside Rule D3 (encoded in `run_phase4e_dependency.py` via `INTERNAL_TRACKER_PREFIXES` + `INTERNAL_TRACKER_RE`): when no github upstream ref is discoverable, scan `action_reason` + `root_cause` + `fix_approach` for Intel-internal tracker IDs (`MFDNN-NNNN` for `oneDNN`; `IGC-NNNN`, `GSD-NNNN`, `PTI-NNNN` for `driver`). A component-aligned match is treated as an OPEN upstream ref and emits `Wait for dependency fix <PREFIX-NNNN>` (bucket `Wait for dependency fix`) instead of `Submit issue`. Regex requires `-` or `_` separator and 3-7 digits to reject version strings like `PTI 0.16`. State is hardcoded `open` (no public API to query Intel-internal trackers); `ref_kind = "internal"`. Applied as one-shot cleanup over the 39 pre-v4.28 `Submit issue` rows: 12 rows match a component-aligned prefix and switch to `Wait for dependency fix`; the remaining 27 stay as `Submit issue`. Phase 4d + 5 + 5b re-run.
v4.27 - May 25, 2026 - Phase 4e Dependency Audit **upstream-pytorch carve-out**. New rule (encoded in `run_phase4e_dependency.py` and the SKILL.md "Rule D1 — Carve-out for upstream-pytorch" block): when `component == "upstream-pytorch"`, the row is treated as `true_dep` only if the resolved upstream ref is a `pytorch/pytorch` **issue** (open or closed); otherwise (ref is a PR in any state, ref is in a non-`pytorch/pytorch` repo, or no ref is discoverable in the body/comments/action_reason) the row is silently demoted to `false_dep` — `Dependency` and `dependency_reason` are cleared, and no D2/D3 verbs are added. Rationale: a pytorch.git PR (ours or otherwise) means the fix is owned/being-landed, which is the team's own work, not a blocking dependency; no-ref-at-all means the AR is "submit/PR it ourselves", also our own work. The 6 other components (driver / oneAPI / oneDNN / oneCCL / oneMKL / triton) keep unchanged D2/D3 behaviour because they are external SDKs that the team cannot patch directly. `ref_state()` now returns `(state, kind)` where `kind ∈ {"pr","issue","unknown"}`; cache schema gains a `kind` field and entries written by v4.26.1 are auto-upgraded on next lookup. Applied as one-shot cleanup over the full 110 `upstream-pytorch` `true_dep` rows: 4 rows kept (refs are `pytorch/pytorch` issues — #2436, #2981, #3094, #3331), 106 rows cleared (48 with pytorch/pytorch PR refs, 1 with a non-pytorch ref, 5 with refs in other pytorch-org repos, 54 with no ref at all). Phase 4d + 5 + 5b re-run.
v4.26 - May 25, 2026 - Phase 4e Dependency Audit **v2** (revised). Component list grew to **7**: `driver`, `oneAPI`, `oneDNN`, **`oneCCL`** (strict rename from `xccl` — legacy literal no longer recognized), `oneMKL`, `triton`, **`upstream-pytorch`** (NEW, upstream repo `pytorch/pytorch`). D3 simplified: open → `Wait for dependency fix <ref>` (bucket `Wait for dependency fix`); merged PR → `Reporter to verify the fix from <ref> ... and provide reason` (owner_transferred=Reporter, bucket `Verify`); closed-unmerged → `Reporter to re-investigate: upstream ref <ref> was closed ... and provide reason` (owner_transferred=Reporter, bucket `Need Response`); no ref → `Assignee to submit issue to <component> upstream` (owner_transferred=Assignee, bucket `Submit issue`). Split the old combined `Add labels` bucket into two distinct buckets: **`Submit issue`** (no upstream ref case) and **`Add label`** (label hygiene case). New rule **D4**: Phase 4e writes a new `dependency_reason` xlsx column on every true_dep row in the form `<Component>: <ref> (<state>) — <reason>` (or `<Component>: no upstream ref — <reason>`); Phase 5b surfaces this as the Dependency-cell hover tooltip in the html report. Phase 4e remains idempotent and applies its rules across all 300 issues (concrete runs may scope to non-blank Dependency).
v4.25 - May 25, 2026 - Added **Phase 4e Dependency Audit** with two new AR buckets (`Wait for dependency fix`, `Add labels`). Phase 4e runs between 4c and 4d, per-issue applies rules D1 (true-dependency check via explore agent on title/body/comments/root-cause/fix-approach), D2 (require `dependency component: <Name>` GitHub label when true; emit `Add label '<canonical>'` verb otherwise), and D3 (upstream tracking ref discovery: open ref -> `Wait for dependency fix <ref>`; merged PR -> `Verify the fix from <ref>`; closed-not-merged -> `Re-investigate: upstream ref <ref> was closed`; no ref -> `Submit issue to <component> upstream` with `owner_transferred = Assignee`). The 6 tracked components are `driver`, `oneAPI`, `oneDNN`, `oneMKL`, `Triton`, `XCCL`, each mapped to known upstream repos for ref discovery and label canonicalization. Rules are defined to apply to every issue; concrete runs may narrow scope to non-blank `Dependency` rows for cost. Phase 4e is idempotent — never duplicates existing canonical verbs.
v4.24 - May 24, 2026 - Phase 4d AR-derivation fixes four systemic bugs surfaced by #3433. (1) **`Verify fix from merged PR <ref> and close` removed from `LAND_PR_VERBS`**: this verb was incorrectly tagged Land PR for 11 versions, so 22 issues with a merged fix waiting for reporter sign-off were misrouted to "Land PR" (waiting to merge) instead of "Verify" (waiting to verify). `LAND_PR_VERBS` is now only the 4 truly open-PR verbs (`Land PR`, `Resolve unresolved review comments on PR`, `Address CI failures on PR`, `Wait for review on PR`). (2) **`fires_verify` split into Path A (explicit) + Path B (silent)**: Path A fires Verify whenever a `Verify fix from merged PR <ref> and close` verb references a MERGED PR (checked against pr_candidates cache or `gh pr view --json state` live fallback), regardless of whether Reporter ∈ owner_transferred. Path B is the v4.6 legacy silent inference (Reporter ∈ owner_transferred set AND any merged PR in pr_cache) preserved for issues without an explicit Verify verb. (3) **`load_pr_analysis_cache` key bug fixed**: function was reading `d.get("pr_analysis", [])` but phase4b emits `pr_candidates` -- the cache had been silently empty for 11 versions, and the only working Verify path was the v4.15 live `gh pr view` fallback. Cache entries are now normalized to `{state, url, repo, pr_number, relationship, verdict}` for downstream consumers. (4) **owner_transferred Close/Skip+Verify carve-out**: per the new spec rule "for rows whose AR is purely Close/Skip and/or Verify, `owner_transferred = Reporter` (the Reporter is the next-actor for verification/closure, not the maintainer/tracker)". Rewrote 25 result.json + xlsx rows (21 Verify-verb rows + 4 Close/Skip rows where Phase 4b had set owner_transferred to a maintainer/tracker). `fires_need_owner` (the blank-Assignee check) is now suppressed when the only buckets are Close/Skip and/or Verify, since the Reporter is on the hook for those (no missing owner). For all other buckets (Land PR, Wait for PR, Need Response, Need check case existence), the legacy rule stands: blank Assignee → Need Owner, regardless of owner_transferred. Phase 4a/4b prompts updated accordingly. **AR counts: Close/Skip=10 (unchanged), Need Owner=28→26 (-2, suppressed for pure Close/Skip and Verify rows), Land PR=124→101 (-23, Verify-verb routing fix), Wait for PR=30 (unchanged), Need Response=137→140 (+3, latent Need Response triggers no longer shadowed by Land PR), Verify=6→34 (+28, Path A + Path B both active), Need check case existence=0, UNCLASSIFIED=0.**
v4.23 - May 23, 2026 - Phase 3.3 formalises the Blank Cell Definition for Incremental Mode: a cell is blank if its value is `None`, `""`, whitespace-only, **or the case-insensitive literal string `"None"`** (the legacy Phase 1 sentinel). Source A / Source B / live-triage write gates and the 4-column skip check all use this definition. Writers must normalize `"None"` strings to true blank on overwrite. Prevents the `Dependency` column from being permanently stuck at 254 stringified `"None"` values that resist Source A (`data-dependency`) refill.
v4.22 - May 22, 2026 - Phase 3.3 Step 0 Details Fast Path now has two published-data sources, both Incremental-Mode-safe. **Source A** = `bug_scrub_highlight.html` (`<tr data-issue=...>` rows): fills blank `Category` / `Priority` / `Dependency` from `data-category` / `data-priority` / `data-dependency` attributes. **Source B** = `details/<id>.md`: fills blank `Root Cause` / `Fix Approach` from the corresponding `##` sections. Both sources only write cells that are currently blank; live triage (Step 6) is still the fallback for anything not covered. Existing non-blank cells are never overwritten.
v4.21 - May 22, 2026 - Phase 1 no longer pre-fills Others `reproduce step`, `Error Message`, or `Traceback`; Phase 2.5 now deep-extracts Others reproducers from current issue bodies, skips performance Others issues, and gates local tests on XPU torch availability.
v4.20 - May 22, 2026 - Clarified Phase 2.4 alignment: `run_processor_steps.py --steps 3` is worklist-only, while `check_xpu_case_existence` must perform explore-agent assisted deep source analysis before writing `xpu_case_existence` / `case_existence_comments`.
v4.19 - May 22, 2026 - Removed obsolete Phase 2 Test Cases torch-ops extraction and dependency RAG passes. Phase 3.3 dependency classification uses root-cause/fix-approach evidence plus the static XPU operator registry from Phase 1.4; Phase 4 consumes the Issues.Dependency and case-existence/CI status fields, not Test Cases torch-ops/dependency columns.
v4.18 - May 21, 2026 - Swept all hardcoded `~/pytorch` references to `${PYTORCH_REPO_ROOT}` across active skill files (80 replacements in 6 files: `triage_skills/SKILL.md`, `SKILL_Triage_Logic.md`, `SKILL_Domain_Patterns.md`, `SKILL_E2E_Benchmark.md`, `SKILL_Category_Analysis.md`, `get_AR_from_issue/SKILL.md`). Historical `PROCESSED_Issue_*.md` artifacts left untouched (record of past triage runs). Combined with v4.17, the active skill surface now has zero hardcoded paths to either the pytorch checkout or the in-skill operator registry.
v4.17 - May 21, 2026 - Introduced `${BUG_SCRUB_SKILL_ROOT}` path-convention variable (resolves to the directory containing this `SKILL.md`). All references to `xpu_supported_operators_complete_list.md` in `triage_skills/` (both `.claude/skills/triage_skills/` and `.opencode/skills/bug_scrub/analyze_issue/triage_skills/`) and `analyze_issue/triage_skills/SKILL_Batch_Orchestration.md` now use `${BUG_SCRUB_SKILL_ROOT}/prepare_data/pytorch_xpu_backend_analysis/xpu_supported_operators_complete_list.md` instead of hardcoded `~/ai_for_validation/...` or `/home/daisyden/...` paths. Skill is now workspace-portable. Reverted accidental move of `pytorch_xpu_backend_analysis.md` (narrative deep-dive duplicate of this skill's body content) -- the narrative file remains at `result/` and is not bundled as a skill resource; only `xpu_supported_operators_complete_list.md` (the operator registry) is bundled.
v4.16 - May 21, 2026 - Phase 1.4 reframed from a regenerating step to a **static-resource carrier**. The operator registry it previously produced -- `xpu_supported_operators_complete_list.md` (operator -> dependency registry, 749 ops) -- is now bundled inside the skill folder at `prepare_data/pytorch_xpu_backend_analysis/`; the narrative `pytorch_xpu_backend_analysis.md` remains in `result/` and is also embedded in the Phase 1.4 skill body for local reading. Routine Phase 1 runs do not regenerate these resources; they are re-derived only when XPU operator coverage changes materially. Downstream consumers read the narrative for Phase 3.3 Step 5 context and the registry for Phase 3.3 Step 6 `get_operator_dependencies()` lookup.
v4.16 - May 24, 2026 - Phase 4d adds two parallel guards on `Close the fixed issue` rows: (1) **RULE 1 audit** - re-verifies every Test Cases / E2E Test Cases sheet row for the Issue ID against strict RULE 1 (`XPU Status ∈ {passed, fixed}` AND `Stock Status ∉ {fail, error, timeout}`; blank XPU Status counts as violation). Three outcomes: `ok` (Close/Skip kept), `out_of_scope` (zero Test Cases rows - alt-path close like manual-verification - Close/Skip kept), `violation` (Close/Skip suppressed, row force-routed to Need Response, violating cases printed). (2) **Pending-ack guard** - regex over `action_reason` (`pending|awaiting @X's ack/approval/sign-off/confirmation/response/reply/review`, `pending a final verification`, `awaiting confirmation from @X`) suppresses Close/Skip even when RULE 1 passes, because Phase 4b admitted in its own reason field that the close is gated on a still-open maintainer ack. Spot-fixed #2966 (RULE 1 violation: blank XPU Status on `test_compile_forward_clone_xpu_float32` → rewrote to `@mengfei25: please confirm ...`) and #2766 (pending-ack: BBBela's 2026-05-07 close request awaiting @EikanWang's ack for 17 days → rewrote to `@EikanWang: please ack ...`). #3489 stays Close/Skip via the `out_of_scope` alt-path. Phase 4a/4b agent prompts updated to forbid emitting `Close the fixed issue` when (a) Test Cases sheet rows exist but fail strict RULE 1, or (b) the close is gated on a still-open ack.
v4.15 - May 21, 2026 - Phase 2.5 (`local-case-verification`) default scope narrowed to **Others lane only**. UT and E2E lanes are now opt-in via the new `--lanes ut,e2e,others` flag or `--all-lanes`; the legacy `--only-lane {ut,e2e,others}` flag remains supported for back-compat. Phase 2.1/2.2 already cover UT/E2E via CI artifacts, so Phase 2.5's value-add is verifying Others-sheet issues (no enumerable test, hence no CI coverage). See `analyze_ci_result/local-case-verification/SKILL.md` v1.1 for invocation examples.
v4.14 - May 20, 2026 - Need-Response rewrite now filters maintainer non-requests (status notes, guilty-commit findings, link-only/code-heavy comments), writes short action_TBD summaries while preserving full comment text for tooltips, drops false Need Response legs when no real request remains, and upgrades action_TBD HTML hover cards with wider formatted code-aware custom tooltips.
v4.13 - May 20, 2026 - Cleaned up 52 mislabeled PR refs in `action_TBD`: 42 high-num (`intel/torch-xpu-ops#NNNNN` with N>=10000) auto-rewritten to `pytorch/pytorch#NNNNN` (all verified to exist as real PyTorch PRs); 10 low-num (`intel/torch-xpu-ops#<100` LLM hallucinations) investigated via issue body + comments + cross-reference timeline + `gh search prs` -- 6 rewritten to their real PRs (#3388/#3006/#2331/#2329/#1969/#3140) and 4 stripped to `PR not identified yet` when no merged/open PR could be found (#3657/#2752/#2701/#2700).
v4.12 - May 20, 2026 - Re-pulled assignee/milestone handling writes empty GitHub values as `""` rather than literal `"None"`; Need-Response `action_TBD` rewrites no longer truncate the 120-char display and store full text directly; after assignee re-pull, AR and `owner_transferred` are re-derived for rows that previously had `Need Owner` so assigned rows drop that bucket and reporter placeholders are cleared only for those prior-Need-Owner rows.
v4.12 - May 21, 2026 - Moved test environment setup from Phase 2.5 to a new Phase 1.0 (test env setup) owned by `prepare_data/issue-basic-info-extraction/SKILL.md`. Phase 1.0 runs once at session start: activate conda env, `git pull` PyTorch + torch-xpu-ops, install nightly XPU torch + pytorch-triton-xpu wheels, sync `${PYTORCH_REPO_ROOT}` HEAD to `torch.version.git_version` (safety branch preserves prior HEAD). Phase 2.5 (`local-case-verification`) v1.2 now only verifies the env is usable; `--skip-env-update` and `--skip-commit-sync` flags are deprecated no-ops.
v4.15 - May 24, 2026 - Phase 4d AR refinements (B1+C+D+F+H): (B1) explicitly documented that bare `No action - investigate further` rows route to `Need Response` so triage leads see them in the weekly stale-request review (previous v4.14 behaviour, now explicit); (C) rewrote the 3 `RETRIAGE_PRS` rows (#3006 / #2968 / #2752) into concrete user-facing verbs and dropped the `RETRIAGE_PRS` literal-match from `fires_need_response` (Phase 4b no longer emits the token); (D) collapsed the 3 specific Need-Response @-templates (`please reply`/`please verify`/`please re-run`) into one generic rule `a.startswith("@") and "please " in a.lower()` so any reporter/assignee-directed ask routes correctly; (F) added a live `gh pr view --json state,mergedAt` fallback to `fires_verify` (24h-TTL cache at `agent_space/phase4d_verify_pr_state_cache.json`) so issues whose Phase 4b `pr_analysis` cache is empty but whose `Verify fix from merged PR <ref>` verb references a now-MERGED PR still get the `Verify` AR bucket; (H) renamed the canonical Phase 4b verb `Wait for PR` -> `Wait for fix PR` across 30 result.json + xlsx rows (`Wait for PR` and `Monitor ...` kept as defensive aliases in `fires_wait_for_pr`; AR bucket label `Wait for PR` unchanged).
v4.14 - May 24, 2026 - Renamed Phase 4d AR bucket `Monitor` -> `Wait for PR` (Option D collapse). The bucket label is the verb-centric name now. `fires_wait_for_pr` matches the `Wait for PR` exact verb and (defensively) any legacy `Monitor ...` prefix. `No action - investigate further` and `RETRIAGE_PRS` are kept in `fires_need_response` (so 99 such rows surface in the weekly stale-request review rather than going UNCLASSIFIED). Added Need-Response verb templates: `@<reporter>: please verify ...` and `@<assignee>: please re-run ...`. Spot-fixed #3705 (replaced `Close the fixed issue` with re-run-evidence request; no full-suite pass on driver 8801 was reported) and #2908 (replaced `No action - investigate further` with reporter-verification request).
v4.11 - May 20, 2026 - Corrected PR-hyperlink workbook storage from =HYPERLINK formula to cell.hyperlink attribute (formula breaks openpyxl data_only=True reads).
v4.10 - May 20, 2026 - Added PR-hyperlink rendering in action_TBD (workbook HYPERLINK formula, md [text](url), html <a>), Need-Response action_TBD rewrite template "@A: please reply to @B's request for <X>" (120-char cell display + full hover tooltip), PR-status downgrade matrix (OPEN+green->Land PR, OPEN+red/pending->Need Response with CI note, MERGED->confirm-fix prompt, CLOSED->clarify-next-steps prompt), and default Need-Response filter pre-selection in bug_scrub_highlight.html.
v4.7 - May 20, 2026 - Phase 5/5b switched from legacy `action_Type` 17-bucket grouping to AR-column-driven 5-bucket grouping. `run_action_type.py` is no longer part of Phase 5 execution; `gen_bug_scrub_md.py` reads `AR` directly from the Issues sheet, and `gen_bug_scrub_highlight.py` drops the `MERGE`/`merged_ar()` remap. Per-issue detail files (`result/details/<id>.md`) now include `Local Status` in UT + E2E test-case tables and a new `## Others Test Case Results` section sourced from the `Others` sheet (whose issue-identifier column is `ID`, not `Issue ID`).
v4.9 - May 20, 2026 - Tightened `Land PR`: the bucket now requires a concrete PR number in `action_TBD`; bare PR-like fix approach text no longer qualifies. Phase 5 reports are open-issue-only.
v4.8 - May 20, 2026 - Added `Land PR` to Phase 4d AR. PR-like next actions now classify as `Land PR`; no-response issue-thread silence is suppressed when a related/identified PR path is already the next action.
v4.6 - May 20, 2026 - Added **Phase 4d AR** column (deterministic post-processing of Phase 4a-4c outputs): projects each issue onto AR buckets `Close/Skip` / `Need Owner` / `Monitor` / `Need Response` / `Need check case existence`. Multi-value cell (`; `-delimited). Inputs: `action_TBD`, `action_reason`, `Root Cause`, `Fix Approach`, `Assignee`, plus a per-issue `no_response` boolean (zero maintainer comments + issue > 7 days old). Column is appended to the `Issues` sheet by header name — no hard-coded column index. Existing `(>1 week)` staleness suffix rule in `analyze_issue/get_AR_from_issue/AGENT_INSTRUCTIONS.md` is the input for the `Need Response` bucket's stale-request leg.
v4.5 - May 19, 2026 - Skill alignment fixes from 321-issue Phase 4 run: (1) Phase 4a `owner_transferred` now explicitly says "actual GitHub login from Issues.Reporter" with anti-example forbidding the literal string `"reporter"`; (2) Phase 4a RULE 1 made precise (`XPU Status ∈ {passed, fixed}` AND `Stock Status NOT IN {fail, error, timeout}`) instead of vague "all cases fixed + double-verify"; (3) Phase 4c now documents Mode A (Phase 2.4 boolean input) vs Mode B (inline rule when 2.4 skipped, writes `xpu_case_existence ∈ {ok, unverified}` downstream); (4) canonical `action_TBD` formatting fixed: `; ` separator (not comma), ASCII hyphen `-` in `No action - investigate further` (not em-dash, not double-dash); (5) 11 canonical `action_TBD` forms enumerated in `collect_AR/case_existence_check/SKILL.md`.
v4.4 - May 17, 2026 - Added Phase 2.5 `local-case-verification`: when Phase 2.1/2.2 leaves an issue's CI status blank, attempt the test locally (UT via `pytest -k <case>` with `PYTORCH_TEST_WITH_SLOW=1`; E2E and Others via the Phase 1.1-extracted reproducer block) in a freshly updated conda env (`git pull` on `${PYTORCH_REPO_ROOT}` + `third_party/torch-xpu-ops`, nightly XPU torch + triton wheels). One aggregated `Local status` column is written on the Issues sheet (per-row CI columns remain authoritative and untouched). Downstream phases (3.3, 4a) trust `Local status` only when the issue is on PVC + Linux.
v4.3 - May 17, 2026 - Phase 1.1 reworked to a **script-first, LLM-fallback** pipeline: script extractors (`parse_test_cases_from_body`, `parse_e2e_info`) run first and are authoritative when they match; LLM extraction (parallel sub-agents emitting structured JSON, cached by body hash) is consulted only for issues the scripts could not handle. Added filesystem-verified test paths and a post-pass that aligns `Issues.Test Module` to the actual sheet placement. Routing decision tree (UNITTEST → E2E → Others) now guarantees exactly-one-sheet placement and zero overlap. URLs in reproducers are preserved verbatim. Phase 1.3 split into carry-forward (default) and deep-analysis (new wontfix issues only) modes. Phase 1.4 outputs explicitly documented as two artifacts (narrative `pytorch_xpu_backend_analysis.md` + operator registry `xpu_supported_operators_complete_list.md`), with the registry being the authoritative source for Phase 3.3 Dependency-column classification.
v4.2 - May 14, 2026 - Phase 1.1 now extracts all 5 PyTorchXPU project fields (Priority, Status, Estimate, Depending, Short Comments) via a single GraphQL request per issue and writes the four non-Priority fields to Issues cols 16-19. Added "Others" sheet listing issues with no parseable UT or E2E test case (columns: ID, Title, Labels, reproduce step, Error Message, Traceback).
v4.1 - May 13, 2026 - Refined Incremental Mode: Phase 2.3 case-duplication can skip rows with existing duplicate_group_id, and Phase 3.3 completion no longer requires Dependency because not all issues have one.
v4.0 - May 11, 2026 - Added Incremental Mode: skip rules for Phases 2.4 and 3.3 to avoid re-processing rows that already have completed analysis columns. Phase 4 always re-runs. Preserves existing non-blank values.
v3.5 - April 27, 2026 - Added Phase 5b (`collect_AR/generate_html_report/`): on-demand interactive HTML report with per-row Done checkboxes (§3/§4, persisted in browser localStorage), sticky filter bar (Assignee / Owner Transferred / Priority / Category / Dependency + free-text + Hide Done), and "Export Done IDs" — fully self-contained, regenerated on demand from the markdown report. Phase 5 markdown remains canonical.
v3.4 - April 27, 2026 - Phase 4b: added Vector E (scan `Fix Approach` text for PR references) and Step 2.5 (mandatory live `gh pr view` re-check + replacement-PR search via Vectors C/D/E for CLOSED-only verified sets) to fix stale-snapshot and missed-PR mis-verdicts. Phase 5 remains purely presentational.
v3.3 - April 22, 2026 - Reorganized helper scripts into skill-colocated folders (`analyze_issue/get_AR_from_issue/`, `analyze_issue/triage_skills/`, `collect_AR/generate_report/`) with `__file__`-anchored paths. Added Phase 5 (generate_report) section.
v3.2 - April 21, 2026 - All paths updated to relative paths, directory renamed (case-duplication-detection)
