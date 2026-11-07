---
name: enable-xpu-tests
description: Enable PyTorch test cases on the XPU (Intel GPU) backend from a weekly tracking CSV, making CUDA-centric tests device-agnostic, measuring them on XPU, grouping failures, and recording a portability verdict per case. Use when the user asks to enable/port tests to XPU from a CSV (e.g. a "*_BMG_*.csv" or ut_regression-style sheet), process the rows without a fix_pr one test file at a time, make tests device-agnostic (GPU_TYPE / HAS_GPU / torch.accelerator / torch.get_device_module / skipXPUIf parity), fill in portability / portability reason / log columns, or decide whether a skipped XPU case is Portable, a Known Issue, a Feature gap, or Not Applicable. Works for any test area (inductor, eager, distributed, etc.), not inductor only. Triggers include "enable these tests on XPU", "work through the CSV", "why is this test skipped on XPU", "make this test device-agnostic for XPU".
---

# Enable XPU Tests

Work through a weekly CSV of PyTorch test cases, enabling the ones that lack a
`fix_pr` on the XPU backend. This applies to any test area (inductor, eager,
distributed, etc.), not inductor only. **Batch-run every selected file** -- do
NOT pause between files. All results, logs, patches, drafted issues and the
reviewer-facing report live under a single report tree
(`xpu_enable_report/<branch>/`); there is no separate Markdown review file.

## Environment assumptions

- Run in the user's **already-active local conda env** (do NOT create/switch
  envs). PyTorch is already installed there; do NOT rebuild torch unless the user
  explicitly asks.
- Edit test sources in the **local working checkout** (e.g. `<repo>/test/`) on
  the user's **current working branch** (verify with `git branch --show-current`;
  never commit to an unrelated branch).
- XPU is available. Use `torch.xpu`, `torch.get_device_module(...)`,
  `torch.accelerator` for device-agnostic code.
- If an installed-torch import mismatches the checkout (e.g. a symbol added in
  `torch/testing/_internal`), sync just that subtree into site-packages (keep a
  `.bak`) rather than doing a full rebuild. Prefer asking the user to update their
  env if the mismatch is in compiled code.

## Workflow

Process **every** selected file top-to-bottom in one batch. For each file:
make it device-agnostic (step 2), measure on XPU (step 3), classify (step 4),
record the verdict (step 5), commit (step 6). Then build/refresh the report
tree (step 7). Do not stop for review between files.

### 1. Select rows
Read the CSV and skip rows that already have a `fix_pr`. Also skip rows whose
`portability` column is already filled -- those were done in a previous run; do
NOT redo them (only re-process them if the user explicitly asks). Apply an
**optional** file/area filter if the user provides one (e.g. "only inductor",
"test_fp8.py"); if no filter is given, process **every** remaining test file in
the CSV. Do NOT hardcode an inductor-only filter. List the distinct test files
and process them top-to-bottom.

### 2. Per file: inspect and make device-agnostic
For each case in the file, read the test source and the skip reason. Replace
CUDA-only constructs with device-agnostic equivalents. Prefer `torch.accelerator`
and `torch.get_device_module` so the same code runs on CUDA and XPU:

- Current device / module:
  - `torch.cuda.current_device()` -> `torch.accelerator.current_device_index()`
  - `torch.cuda.<fn>` -> `torch.get_device_module(self.device).<fn>` (graphs,
    streams, events, `synchronize`, `memory_allocated`, `empty_cache`, etc.).
- Availability / count:
  - `torch.cuda.is_available()` -> `torch.accelerator.is_available()`
  - `torch.cuda.device_count()` -> `torch.accelerator.device_count()`
- Graphs: `torch.cuda.CUDAGraph()` / `torch.cuda.graph` ->
  `torch.get_device_module(self.device).XPUGraph`/`.graph`, selected by device.
- Device string / type: hardcoded `"cuda"` ->
  `torch.accelerator.current_accelerator().type`, or `self.device`. For
  **inductor** tests you can also use `GPU_TYPE` from
  `torch.testing._internal.inductor_utils`.
- Capability checks: `torch.cuda.get_device_capability()` returns a
  `(major, minor)` tuple; do NOT assume `torch.accelerator.get_device_capability()`
  matches (on XPU it can return a dict). Mirror the library's own logic instead.
- Skip-decorator parity: for every `skipCUDAIf`/`@skipIf(not SM...)` add the
  matching `skipXPUIf`. Prefer the generic device-type test decorators
  (`instantiate_device_type_tests`, `onlyXPU`, `dtypesIfXPU`, etc.) from
  `torch.testing._internal.common_device_type`. The inductor-only helpers
  `GPU_TYPE`, `HAS_GPU`, `HAS_GPU_AND_TRITON` from
  `torch.testing._internal.inductor_utils` apply **only to inductor tests** --
  do not use them in eager/distributed tests.
- Remove a stale `@skipIfXpu` only when the cited issue is confirmed
  closed/fixed (verify with `gh issue view <n> --repo intel/torch-xpu-ops`).
- For any **new** `@skipIfXpu`/`skipXPUIf` decorator you add, keep the `msg`
  to just the issue link (e.g. `msg="https://github.com/intel/torch-xpu-ops/issues/NNNN"`);
  put the root-cause explanation in the CSV `portability reason`, not the
  decorator. If the issue is still a draft awaiting approval, use
  `.../issues/PLACEHOLDER` -- the review server backfills the real number on
  submit (step 4).
- When you remove a skip/xfail, also remove the now-orphaned comment that
  described it (e.g. a `# torch-xpu-ops/issues/NNNN` line above the decorator).
  Never leave such a comment dangling. Also drop any import that the removal
  leaves unused (but keep it if the symbol is still referenced elsewhere).
- Leave x86/CPU-only tests alone; they are Not Applicable on XPU.

### 3. Measure on XPU, saving a per-file log
Run the affected cases on XPU from the test dir and **tee the full output to a
per-file log**. Write logs under the batch report tree so the report can link
them directly:

```
mkdir -p xpu_enable_report/<branch>/logs
python test/<path>/<file> -k "<selector>" -v 2>&1 \
  | tee "xpu_enable_report/<branch>/logs/<file>.log"
```

Record the command as the first line of the log. Do NOT re-run cases whose
result you already have.

**unittest `-k` gotchas:** multiple `-k` flags are OR-combined (not AND), and
`-k` does not accept boolean `and`/`or` (`-k "foo and xpu"` matches nothing). To
run exactly one device-instantiated case, pass its **full instantiated name** as
a single `-k` (e.g. `-k test_foo_xpu`, `-k test_foo_xpu_float16`), not the base
method name plus a device filter.

### 4. Group failures and classify
Group remaining failures by root cause. For each group, search
intel/torch-xpu-ops for an existing issue (verify its real state with `gh`):

- Existing **wontfix** issue -> case is **Not Applicable**; cite it, do not
  reproduce or reopen.
- Not Applicable by a deprecated/frozen API or other permanent won't-fix cause
  but NO tracking issue exists -> draft a `[wontfix]` issue (step below).
- Existing open issue for this exact failure -> **Known Issue**; cite it.
- Missing/unsupported XPU capability -> **Feature gap**; cite the broad tracking
  issue if one exists, else draft a feature-request issue.
- No issue and it's a real gap -> draft a new bug-report issue.

**Drafted issues are review artifacts shown in the report**, not a separate
Markdown file. Attach each draft to its test file in the report manifest (see
`tools/README.md` for the `issue_drafts` schema). Each draft carries: a title,
the exact body using the intel/torch-xpu-ops template fields (bug-report
`🐛 Describe the bug` / `Versions`, or feature-request `🚀 The feature...`),
`labels`, the affected case IDs, and the proposed verdict. Every bug-report
draft MUST include an **Error log / message** section with the verbatim failing
output (assertion/exception message + key traceback lines, fenced) and the exact
repro command. Add the AI-assistance disclosure line to each body. Use
`.../issues/PLACEHOLDER` in any skip/comment for a not-yet-filed issue.

The draft is rendered editably in the file's `report.html`. When the user
reviews it and clicks **Submit to intel/torch-xpu-ops**, the local review
server (`tools/review_server.py`) files the issue via `gh` and backfills the
real number into the patch, the source file, the per-file log, the CSV
(`portability reason`, `issue_number`, `issue_url`) and the served report copies.
NEVER file an issue any other way; the submit button is the only approval path
(see AI_POLICY.md).

If the enablement commit was already made with `.../issues/PLACEHOLDER` in the
source (step 6), start the server with `--amend --regen --branch <slug>` so that
on submit it also amends that commit to carry the real issue number, regenerates
the patch from the amended commit, updates the manifest `commit` field, and
re-runs `report_gen.py` so the served report shows the new hash. The amend is
guarded (only runs if HEAD already touched that source file) and is skipped
unless `--amend` is passed. After an amended ghstack/PR commit, remind the user
to update the PR description if needed.

### 5. Record verdict in the CSV
Add/fill these columns per case:
- `portability`: `Portable` | `Known Issue` | `Feature gap` | `Not Applicable`
- `portability reason`: one-line justification (what changed, or the issue #).
- `log`: path to the per-file log from step 3.

Only edit the canonical CSV the user points at (there may be duplicate copies in
subdirs; leave those untouched unless told otherwise). The CSV is the single
source of truth for per-case verdicts -- the report is generated from it, so
there is no separate Markdown table to maintain.

### 6. Commit per file
Run `lintrunner -a`, fix lint, then commit the test-file change(s) with a
concise message plus a **Test Plan** section (the literal commands run) and a
line disclosing AI assistance. Keep the CSV as an uncommitted working artifact
unless the user asks to commit it. Files with no code change (all Not Applicable
/ no-change-portable) are recorded in the CSV only and get no commit.

### 7. Build the report tree
Everything the user reviews lives under `xpu_enable_report/<branch>/`:

```
xpu_enable_report/<branch>/
  index.html            landing page: test files -> report pages, status,
                        portability breakdown, link to the full input table
  input_table.html      every case as a CSV row + review controls
  logs/<file>.log       per-file test logs from step 3
  <test_file>/
      report.html       per-file page: a `<script id="report-data">` JSON data
                        island (commit, patch/log text, per-case columns, a
                        deduped base-method source map, and drafts) rendered
                        entirely client-side (commit/patch/log links, portability
                        summary, per-case detail table, and any drafted issues
                        with an editable body + Submit button)
      <file>.patch      the file's commit patch (git show <commit> -- <file>)
```

`report.html` is self-contained: all presentation data lives in the embedded
JSON island and a small renderer builds the DOM in the browser. The `.patch`
and committed source stay as real artifacts (their text is mirrored into the
island for display). On submit, the server backfills the island structurally
(`drafts[].number/url`, each drafted case's `issue_number`/`issue_url`, the
`PLACEHOLDER` in its `portability reason`, and `patch`/`log` text) via
`_backfill_island`, so a refresh reflects the real number even without `--regen`.

Generate it with the bundled tool. All HTML/report logic lives in `tools/`;
keep this skill free of markup. See `tools/README.md` for the manifest schema,
all CLI flags, and the JUnit-XML details.

```
# 1. per-file patch + log + draft-issue manifest, then:
python3 <skill>/tools/report_gen.py \
  --csv <BATCH>.csv --out xpu_enable_report --branch <branch> \
  --manifest manifest.json [--case-base-url https://<case-host>]

# 2. serve it so Submit buttons can file issues and the reviewer can export:
python3 <skill>/tools/review_server.py \
  --root xpu_enable_report/<branch> --csv <BATCH>.csv \
  --manifest manifest.json --repo intel/torch-xpu-ops [--port 8000]
```

Open `http://localhost:<port>/index.html`. The reviewer sets per-case verdicts
(stored in `localStorage`), clicks **Submit** on an approved draft issue to file
it, then **Export review JSON**; merge that back with:

```
python3 <skill>/tools/merge_review.py --csv <BATCH>.csv --review review_<branch>.json
```

Also emit a branch-scoped JUnit XML from the CSV (`tools/report_gen.py
--junit <branch>_cases.xml`) covering exactly the cases in the files this branch
touched, so CI/dashboards can ingest the verdicts. Leave the whole report tree
and XML untracked unless the user asks to commit them.

## Verdict definitions

- **Portable**: enabled to run and pass on XPU (possibly after a device-agnostic
  edit).
- **Known Issue**: fails due to a specific bug already tracked by an open
  intel/torch-xpu-ops (or upstream) issue; stays skipped, cite the issue.
- **Feature gap**: a real XPU capability/feature limitation; stays skipped.
- **Not Applicable**: CPU/x86-only test, or covered by a wontfix issue; no XPU
  run expected.

## Guardrails

- Batch-run the whole selection; do NOT pause between files.
- Never post to GitHub except through the report's **Submit** button after the
  user has reviewed the draft (see AI_POLICY.md). Drafts only otherwise.
- Don't rebuild torch or change envs unless asked.
- Don't re-run tests you already have results for.
- Verify every cited issue's real state with `gh` before acting on it.
