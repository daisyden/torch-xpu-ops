# Issue Basic Info Extraction (Deep-Analysis Pipeline)

> **Prerequisite**: Phase 1.0 test environment setup (conda env + nightly XPU
> torch/triton + source-repo commit sync) must run once at session start, before
> this phase. See [`../test-environment-setup/SKILL.md`](../test-environment-setup/SKILL.md).

## Base Path Reference

Relative paths from this file location (`bug_scrub/prepare_data/issue-basic-info-extraction/`):

```
../../../                    → issue_triage root
../../../data/               → JSON data (issues, LLM cache, batches)
../../../result/             → Excel results directory
.                            → WORKDIR (this SKILL_DIR)
```

## Overview

Produces `result/torch_xpu_ops_issues.xlsx` from **BOTH open AND closed issues** in `intel/torch-xpu-ops`.

**Issue Scope (v4.31+)**:
- **Open issues**: All status states (in scope for active triaging)
- **Closed issues**: Only those labeled `wontfix` or `not_target` (for Not Applicable sheet population)

The pipeline is **script-first, LLM-fallback**:

1. Deterministic regex/heuristic extractors run first — they handle issues
   with `Cases:` or `test_cases:` blocks, structured E2E benchmark commands, and similar
   "well-formed" formats authored by the issue reporter.
2. The LLM extraction is consulted **only for issues the script extractors
   could not handle** (prose-only reproducers, cross-paragraph test
   references, embedded scripts, URL-bearing instructions, etc.).
3. Deterministic post-processing then routes each issue into **exactly one**
   of: `Test Cases`, `E2E Test Cases`, `Others`, `Not applicable`.
4. The `Issues` sheet's `Test Module` column is rewritten in a final
   post-pass so it **always equals** the sheet the issue actually landed in.

Script output is authoritative when it matches; the LLM never overrides or
augments a successful script match.

## When to Use

- Generate a fresh Excel from scratch
- Re-extract after issue bodies change (cache invalidates by body hash)
- Refine routing logic / prompts and re-run extraction

## Why Deep Analysis Is Needed (For the Fallback Path)

Pure regex handles well-formatted issues but fails on the long tail of
unstructured reports. The LLM fallback handles those. Concrete failure modes
the LLM must catch are below.

> **Caveat (applies to every example and table in this skill):** all issue
> numbers and model/test names are illustrative. Never hard-code them — the
> skill must work on any new issue exhibiting these patterns.

| Failure mode | What regex sees | What deep analysis must see |
|---|---|---|
| Reproducer URL dropped | shell command only | The repo / gist / dataset URL **is** part of the reproducer; drop it and the commands are useless |
| Test path in prose | no `file::Class::method` literal | "the failing case in `test/dynamo/test_higher_order_ops.py` under `ActivationCheckpointingTests`" must be assembled from the paragraph |
| Embedded repro script | `python repro.py` only | The full Python source in the fenced code block IS the canonical reproducer |
| Prose model list | none | "Timm: convnext_base, jx_nest_base" → 2 rows in E2E sheet with `benchmark=Timm` |
| `[E2E]` title bait | classified e2e | If body only describes a kernel bug, this is `other` regardless of title prefix |

## Inputs

| Input | Path | Source |
|---|---|---|
| Issues JSON | `../../../data/torch_xpu_ops_issues.json` | `gh` GraphQL fetch (Phase 1.1) |
| Project fields | embedded in issues JSON | GraphQL `PyTorchXPU` project |
| LLM cache | `../../../data/llm_extracted.json` | merge of LLM batches |
| Pytorch checkout (for file-existence verification) | `$PYTORCH_REPO_ROOT` (default `/home/daisyden/opencode/bug_scrub`) | local clone |
| Carry-forward NA rows | `../../../result/torch_xpu_ops_issues_bk_*.xlsx` | latest known-good backup |

## Outputs

`../../../result/torch_xpu_ops_issues.xlsx` — five sheets:

1. **Issues** — one row per issue; columns include `Test Module` (final ground-truth placement), PyTorchXPU project fields (`Status`, `Priority`, `Estimate`, `Depending`, `Short Comments`).
2. **Test Cases** — one row per `(test_file, test_class, test_case)`.
3. **E2E Test Cases** — one row per `(benchmark, model)`.
4. **Others** — one row per issue. `reproduce step`, `Error Message`, and `Traceback` columns are intentionally blank in Phase 1; Phase 2.5 owns deep extraction immediately before local verification.
5. **Not applicable** — copied verbatim from prior backup (see `create-not-applicable-sheet` skill).

## Pipeline (high level)

```
1.1  Fetch open issues (this skill, §Fetch)  ─►  data/torch_xpu_ops_issues.json
1.2  Download fresh CI artifacts (download_ci_result skill)
1.3  Carry forward NA sheet (create-not-applicable-sheet skill, mode=carry_forward)
1.4  (skipped — backend analysis is opt-in)
1.5  LLM extraction (this skill, §LLM Extraction) — ONLY for issues that the
     script-based extractors could not handle; cached by body hash
1.6  Build sheets via routing decision tree (this skill, §Routing)
1.7  Post-pass: align Issues.Test Module to actual placement (this skill, §Post-Pass)
1.8  Verify invariants (this skill, §Invariants)
```

## Fetch (Phase 1.1)

`generate_excel.py` performs the fetch in **two stages** (v4.31+ now fetches both open and closed):

1. **Open-issue list via the REST endpoint**
   `https://api.github.com/repos/intel/torch-xpu-ops/issues?state=open&per_page=100`
   - Pull-requests are filtered out (`pull_request` key absent).
   - All 100 open issues are fetched with full metadata.

2. **Closed issues labeled `wontfix` or `not_target`** (v4.31+ fix for Not Applicable sheet)
    Two separate queries (GitHub API uses AND logic for multiple labels):
    - `https://api.github.com/repos/intel/torch-xpu-ops/issues?state=closed&labels=wontfix&per_page=100`
    - `https://api.github.com/repos/intel/torch-xpu-ops/issues?state=closed&labels=not_target&per_page=100`
    - These populate the Not Applicable sheet in Phase 1.3.
    - Pull-requests are filtered out, and results are merged and deduped by issue number.

3. **PyTorchXPU project fields via `gh api graphql`** (single batched query, see
   `fetch_all_project_fields()` in `generate_excel.py`):
   - `PyTorchXPU Priority` → normalized to `P0`/`P1`/`P2`/`P3` and written to
     Excel `Priority` column.
   - `PyTorchXPU Status`           → col 17.
   - `PyTorchXPU Estimate`        → col 18.
   - `PyTorchXPU Depending`       → col 19.
   - `PyTorchXPU Short Comments`  → col 20 (sanitized; truncated to 32767 chars).
   - Field matching: literal name `"PyTorchXPU <Field>"` OR
     `(project_title == "PyTorchXPU", field_name == "<Field>")` — either naming
     convention is accepted.
   - Requires `gh` CLI authenticated with `repo` + `read:project` scopes.
     Without that scope, project fields are blank but issue rows are still
     written.

4. **Native GitHub `issueType` via `gh api graphql`** (v4.30, see
   `fetch_all_issue_types()` + `populate_issue_types()` in `generate_excel.py`):
   - `issueType.name` recorded verbatim in new `GitHub Type` column (col 12).
   - REST `/issues` endpoint does NOT expose `issueType`, so GraphQL search is
     used: `search(query:"repo:intel/torch-xpu-ops is:issue", type:ISSUE)`.
   - **Task exclusion (v4.30)**: any issue with `github_type == "Task"` is
     dropped at Phase 1 before the row is written. Such issues never appear in
     xlsx / md / html / `details/*.md`. Existing heuristic `Type` column
     (populated by `classify_issue_type`) is unaffected.

Skip the fetch (e.g. to re-render from cached JSON) with `SKIP_PHASE_1_1=1`.

## LLM Extraction

The LLM is a **fallback** extractor — consulted only for issues the script
extractors could not handle (see §Routing for the exact gates).

**Canonical contract — single source of truth: [`LLM_PROMPT.md`](./LLM_PROMPT.md).**
That file owns the output schema, the MANDATORY extraction rules, and the
example record. Do not restate or fork them here; edit `LLM_PROMPT.md` instead,
then force a re-extraction (§Cache invalidation). The only field the routing
tree keys on is `kind ∈ {unittest, e2e, other}`.

### Sub-agent workflow (parallel, off-line)

Inputs and outputs are JSON files on disk; sub-agents do not call GitHub.

```
data/llm_extraction_queue.json   ← list of issue IDs to extract
data/llm_batches/batch_NN.json   ← shards of queue (10 issues each)
data/llm_results/batch_NN.json   ← per-shard extraction output
data/llm_extracted.json          ← merged cache (keyed by str(issue_id))
```

Launch one background sub-agent per batch (orchestration in `LLM_PROMPT.md`
§Spawning sub-agents). When all batches complete:

```bash
python merge_llm_results.py   # merges data/llm_results/*.json → data/llm_extracted.json
```

### Cache invalidation

`get_llm_extraction(issue_id, body)` compares `entry.body_hash` to
`sha256(body)[:16]`. A body edit drops that entry → the issue is re-extracted.

A **prompt** change (e.g. a new MANDATORY rule in `LLM_PROMPT.md`) leaves body
hashes unchanged, so force a full re-extraction with the recipe in
§Usage → "Re-extract with a new prompt".

## Routing (Decision Tree)

Executed inside `generate_excel.py` per issue, in this exact order.

**LLM is a strict fallback at every gate**: the cache is consulted ONLY when the
deterministic script extractors produce no result for that slot. Once a script
extractor has matched, the LLM is not merged in (rationale in §Why fallback).

```
Issue
  │
  ▼ Not-applicable carry-forward? ── yes ─► copy to "Not applicable" sheet; STOP
  │ no
  ▼
 UNITTEST GATE
   test_cases = parse_test_cases_from_body(body)          # script first
   if not test_cases:                                     # FALLBACK only
       test_cases += llm_test_cases_for_issue(num, body)  # disk-verified
   if test_cases (after dedup, validation, disk check):
       write rows to "Test Cases"
       issues_with_ut.add(num)

   LABEL FALLBACK (only if UT gate produced nothing AND test_module != "e2e"):
     if label in {"module: ut", "skipped"} OR llm.kind == "unittest":
         (file, class, case) = best_effort_test_info(body, title)    # script first
         if not (file and case):                                     # FALLBACK
             fill blanks from llm_test_cases_for_issue(num, body)
         write one row to "Test Cases"
         issues_with_ut.add(num)
  │
  ▼
 E2E GATE   (skip entirely if num already in issues_with_ut)
   e2e_info = parse_e2e_info(body, title) if test_module == "e2e" else []
   if e2e_info:                                                      # script wins
       write rows to "E2E Test Cases"
       issues_with_e2e.add(num)
   elif test_module == "e2e":
       write one "unknown" row to "E2E Test Cases"
       issues_with_e2e.add(num)

   LLM FALLBACK (only if no rows added above AND not in issues_with_ut):
     if llm.kind == "e2e" AND llm.test_cases non-empty:
         one row per llm.test_case: benchmark=test_class, model=test_method
         issues_with_e2e.add(num)
  │
  ▼
 OTHERS
   If num NOT in issues_with_ut AND NOT in issues_with_e2e:
       reproducer    = llm.reproducer    or extract_e2e_reproducer(body, title)
       error_message = llm.error_message or extract_error_message(body)
       traceback     = llm.traceback     or extract_traceback(body)
       write one row to "Others"

   Note: LLM kind == "e2e" WITHOUT test_cases ⇒ STILL goes to Others.
         (e2e-flavored but has no enumerable benchmark/model)
```

### Why fallback (not augment)

Script extractors (`parse_test_cases_from_body`, `parse_e2e_info`) are
high-precision on author-written `Cases:` / `test_cases:` blocks and fenced benchmark commands.
Merging LLM cases on top of a successful match would inflate the sheets with
paraphrased duplicates and risk contradicting that authoritative source — so the
LLM only fills the gaps scripts cannot parse (prose references, cross-paragraph
synthesis, embedded scripts, URL-bearing reproducers).

### File-existence verification

`resolve_test_file()` / `_disk_match_rel_file()` only accept paths that exist
on disk under `<PYTORCH_REPO_ROOT>/test/` or
`<PYTORCH_REPO_ROOT>/third_party/torch-xpu-ops/test/`. This blocks fabricated
paths from polluting the Test Cases sheet.

`llm_test_cases_for_issue()` runs the same disk check on every LLM-emitted
`test_file`. LLM paths that don't resolve on disk are dropped silently — so
even when the LLM fallback kicks in, only verifiable cases make it through.

## Post-Pass (alignment)

After all sheets are populated, rewrite the Issues sheet's `Test Module` column
so it equals the sheet the issue actually landed in. The column is addressed by
header name via `write_by_name()`, never a hardcoded index:

```python
others_ids = {row[0] for row in ws_others.iter_rows(min_row=2, values_only=True)
              if row and row[0] is not None}

for row_idx in range(2, ws_issues.max_row + 1):
    num = ws_issues.cell(row=row_idx, column=1).value
    if   num in others_ids:        write_by_name(ws_issues, row_idx, "Test Module", "others")
    elif num in issues_with_e2e:   write_by_name(ws_issues, row_idx, "Test Module", "e2e")
    elif num in issues_with_ut:    write_by_name(ws_issues, row_idx, "Test Module", "ut")
    # Not applicable rows are written by create-not-applicable-sheet
```

This is the single source of truth for `Test Module`; the upstream
`classify_test_module()` value is only an intermediate hint.

## Invariants (verified after every run)

These MUST hold; verify with the snippet in §Verification:

1. Every issue appears in **exactly one** of {Test Cases, E2E Test Cases, Others, Not applicable}.
   - `UT ∩ E2E = ∅`, `UT ∩ Others = ∅`, `E2E ∩ Others = ∅`.
2. For every row in Issues, `Test Module` equals the sheet the issue is in.
3. Carry-forward Not-applicable row count equals the source backup's row count.
4. No `test_file` in Test Cases is missing from disk.
5. Project fields populated when `GITHUB_TOKEN` is set: rows with no
   `PyTorchXPU Priority` are issues genuinely absent from the project.

### Verification snippet

```python
from openpyxl import load_workbook
wb = load_workbook('result/torch_xpu_ops_issues.xlsx')

def ids(sheet):
    return {r[0] for r in wb[sheet].iter_rows(min_row=2, values_only=True)
            if r and r[0] is not None}

ut, e2e, oth = ids('Test Cases'), ids('E2E Test Cases'), ids('Others')
assert not (ut & e2e),  ut & e2e
assert not (ut & oth),  ut & oth
assert not (e2e & oth), e2e & oth

ws = wb['Issues']
tm_col = [c.value for c in ws[1]].index('Test Module')   # resolve by header, not a fixed index
tm = {r[0]: r[tm_col] for r in ws.iter_rows(min_row=2, values_only=True)
      if r and r[0] is not None}
assert all(tm[i] == 'ut'     for i in ut)
assert all(tm[i] == 'e2e'    for i in e2e)
assert all(tm[i] == 'others' for i in oth)
```

## Tools and Helpers

| Tool / function | Where | Purpose |
|---|---|---|
| `generate_excel.py` | `.` | Main entry point; runs Phase 1.1 (fetch) + 1.6/1.7 (build + align) |
| `merge_llm_results.py` | `.` | Merge `data/llm_results/*.json` → `data/llm_extracted.json` |
| `get_llm_extraction(num, body)` | inside `generate_excel.py` | Body-hash-checked cache lookup |
| `llm_test_cases_for_issue(num, body)` | inside `generate_excel.py` | Disk-verified LLM test cases |
| `resolve_test_file(test_path)` | inside `generate_excel.py` | Dotted path → relative file (filesystem-checked) |
| `_disk_match_rel_file(rel)` | inside `generate_excel.py` | Verify a `test/...` path exists on disk |
| `best_effort_test_info(body, title)` | inside `generate_excel.py` | Label-fallback miner for `module: ut` / `skipped` |
| `parse_e2e_info(body, title)` | inside `generate_excel.py` | Structured E2E row extractor (regex + LLM hybrid) |
| `extract_e2e_reproducer(body, title)` | inside `generate_excel.py` | Fallback reproducer extractor (used only when LLM field empty) |
| `extract_error_message(body)` / `extract_traceback(body)` | inside `generate_excel.py` | Fallback error/traceback miners |
| `gh` CLI | external | GraphQL fetch for issues + PyTorchXPU project fields |
| `task(subagent_type=...)` | OpenCode tool | Spawn parallel LLM extraction sub-agents |

## Usage

### Full run from a clean state

```bash
# 1.1 Fetch + 1.5 LLM extract + 1.6 build + 1.7 align
cd .  # this SKILL_DIR
python generate_excel.py
```

The script auto-loads `data/llm_extracted.json` when present. If absent, all
LLM-dependent gates degrade gracefully to regex/heuristic fallbacks (the
output will be lower-quality but consistent).

### Re-run only the sheet build (no GitHub fetch)

```bash
SKIP_PHASE_1_1=1 python generate_excel.py
```

### Re-extract with a new prompt

```bash
# Backup and clear caches
cp data/llm_extracted.json data/llm_extracted.json.bak.$(date +%Y%m%d_%H%M%S)
rm -f data/llm_results/*.json

# Rebuild batches (10 issues each), then spawn sub-agents in parallel
# (one task() call per batch, run_in_background=true)
# After all complete:
python merge_llm_results.py
SKIP_PHASE_1_1=1 python generate_excel.py
```

### Restore Not applicable sheet after regeneration

The `Not applicable` sheet is owned by the `create-not-applicable-sheet` skill —
run it in carry-forward mode to repopulate the sheet from the latest backup.
This skill never writes that sheet itself.

## Prerequisites

- `GITHUB_TOKEN` with `repo` + `read:project` scope (for PyTorchXPU project fields).
- Local PyTorch checkout at `$PYTORCH_REPO_ROOT` (default `/home/daisyden/opencode/bug_scrub`) with `third_party/torch-xpu-ops/test/` present — required for file-existence verification.
- Python ≥ 3.10 with: `openpyxl`, `requests`.
- `gh` CLI authenticated.

## Spot-Check Acceptance

Before declaring the run good, verify a small set of issues hand-picked to
cover each routing path (illustrative — see the caveat under §Why Deep Analysis;
match the **patterns** below, not specific issue IDs):

| Pattern | Expected sheet | Property to verify |
|---|---|---|
| Issue whose body is mostly a `python script.py` reproducer with no test file | Others | Phase 1 routes the issue only; Phase 2.5 extracts/materializes the script before local verification |
| Issue whose body lists `Timm: model_a, model_b` and `Torchbench: model_x` | E2E | one row per `(suite, model)` |
| Issue whose body references a real path under `test/...` (e.g. via traceback) | Test Cases | row has correct file/class/method, file exists on disk |
| Issue with `module: ut` label but no explicit test path in body | Test Cases | label-fallback row (best-effort or LLM-filled) |
| Issue whose reproducer is a GitHub repo URL plus shell commands | Others | Phase 1 routes the issue only; Phase 2.5 must preserve URLs while extracting runnable steps |

## Notes

- **Script extractors are authoritative; the LLM is a fallback** (see §Routing /
  §Why fallback). Regex handles author-structured issues that need no LLM
  round-trip; the LLM only covers what regex cannot parse.
- Never edit `Test Module` outside the post-pass — that column is regenerated
  every run.
- Never write to `Not applicable` from this skill; that sheet is owned by
  `create-not-applicable-sheet` (this is the canonical statement of that rule).

## Next Step

After this skill runs, the output is ready for downstream phases
(`bug_scrub/analyze_ci_result/`, oncall categorization, etc.).
