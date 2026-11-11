---
name: enable-xpu-tests
description: Enable PyTorch test cases on the XPU (Intel GPU) backend from a weekly tracking CSV. Diffs the CSV against the local report tree to pick the cases still to do, makes each test file device-agnostic on its own agent_<file>_NNNN branch off viable/strict in a fixed workspace clone, measures with pytest on a nightly XPU wheel, force-removes skips to collect real failures, classifies every case (Portable / Known Issue / Feature gap / Not Applicable) with issue + log evidence, and serves a review web UI (issue submit, CSV export, PR bundles). Use when the user asks to enable/port tests to XPU from a CSV (e.g. "*_BMG_*.csv", ut_regression sheet), work through the CSV, process the rework queue, make tests device-agnostic, or decide whether a skipped XPU case is portable. Works for any test area (inductor, eager, distributed, ...).
---

# Enable XPU Tests (orchestrator)

Batch-enable the CSV's remaining cases on XPU, one **independent patch per test
file**, every verdict backed by evidence. This skill is the workflow; details live
in the sub-skills:

| step | sub-skill |
|---|---|
| port a test to device-agnostic code | `xpu-port-device-agnostic` |
| precheck, measure, gate-removed rerun, classify, draft issues | `xpu-classify-and-draft` |
| review UI, issue submit, CSV export, PR bundles, rework queue | `xpu-enable-review` |
| decorator parity checklist | `xpu-test-decorator-parity` |

Tools are in `tools/` (CLI + schema: `tools/README.md`). Run them from that
directory. `T=<this skill dir>/tools`.

## Ground rules

- **Input CSV is read-only.** Never write it. Verdicts live in the report tree;
  the reviewer's **Export CSV** button writes `<input>_reviewed_<ts>.csv`.
- **SKIPPED is never Portable.** Portable needs a PASSED pytest result from the
  final code (a `rework` or `initial` run at the final patch). A PASS seen only with
  the gate removed does not count when the shipped code still skips the case; even
  files with no code change need a final-code run (`measure.py --run rework`). Every Known Issue / Feature gap / Not Applicable case needs an
  issue id (existing or drafted) *and* a log from a run with its skips removed,
  unless it cites an issue `gh` confirms OPEN or `wontfix`.
- **One branch per file, all on the same base.** Never stack files on one branch.
- **Never act on GitHub autonomously** (AI_POLICY.md). Issues are filed and
  branches are pushed only from the reviewer's button clicks in the UI (or the y/N
  CLI prompt), and PRs are created by the human on GitHub's prefilled page. The
  agent never calls those itself.
- Batch-run every selected file; do not pause between files for review.
- Do not re-run cases that already have a result at the same patch (measure.py
  skips them unless `--force`).

## Workspace (fixed location)

```
~/xpu_enable_ws/              ($XPU_ENABLE_WS overrides)
  pytorch/                    independent clone, idle = detached at viable/strict
  env/                        venv with nightly XPU torch
  xpu_enable_report/<slug>/   per-file data (see tools/README.md)
  workspace.json              base_sha, nightly version, fork remote/owner
```

First time (prints the commands; add `--run` to execute after the user agrees):

```
python3 $T/setup_ws.py                       # clone + venv + nightly wheel
python3 $T/setup_ws.py --run
python3 $T/setup_ws.py --fork-remote <remote> --fork-owner <gh-user>   # for PR commands
python3 $T/setup_ws.py --status              # base vs nightly skew
```

`--refresh --run` moves `base_sha` to the latest viable/strict (new files only;
existing files keep their recorded base). Activate `~/xpu_enable_ws/env` for every
test run; never touch the user's own env or checkout. `measure.py` overlays the
clone's `torch/testing/_internal` onto the venv's torch before each run (op_db and
helper edits take effect); `measure.py --restore` undoes it.

## Workflow

### 1. Select cases

```
python3 $T/select_cases.py --csv BATCH.csv [--filter inductor] [--file test_fp8.py]
```

Selected = no `fix_pr`, not already in any `<slug>/cases.json`, file not in a
submitted PR bundle. Apply a filter only if the user gives one. Before starting,
skim `references/review_notes.md` "Distilled lessons" for reviewer guidance.

### 2. Per file (repeat top to bottom, no pauses)

```
python3 $T/file_branch.py start <test_file> --csv BATCH.csv
```

Registers the file + its selected cases, then checks out the file's agent branch
(`agent_<slug>_NNNN` at `base_sha`; reused if it already exists). Then:

1. **Precheck** every case (API catalog + wontfix lookup):
   `python3 $T/precheck.py --file <test_file> --csv BATCH.csv`
2. **Port** the file to device-agnostic code (`xpu-port-device-agnostic`).
3. **Measure** the current state: `python3 $T/measure.py --file <f> --run initial`
4. **Force-enable and re-measure** every case that is not PASSED: remove its XPU
   gates, make sure an XPU variant is instantiated, then
   `python3 $T/measure.py --file <f> --run gate_removed --cases <ids>`.
   Repeat until no case is SKIPPED/NOT_RUN only because of a gate you can remove.
5. **Classify** each case from the gate-removed result, search/cite issues, draft
   new ones, and re-add gates whose `msg` is exactly the issue URL (or
   `.../issues/<placeholder>` for a draft). Record with `store.py set-case` and
   `store.py add-draft` (`xpu-classify-and-draft`).
6. **Confirm Portable** cases at the final code: `--run rework --cases <portable ids>`.
7. **Verify evidence** -- must be clean (no ERROR) before finishing the file:
   `python3 $T/verify_evidence.py --file <test_file>`
8. **Lint + commit** in the clone (`lintrunner -a` if available, else
   `python -m ruff`/`flake8` on the touched files). Commit message: one line
   summary, short body, a **Test Plan** with the literal pytest commands from the
   log headers, and an AI-assistance disclosure line.
9. **Finish**: `python3 $T/file_branch.py finish <test_file>` -- writes
   `<slug>.patch` = `git diff base..agent_branch`, records the commit, deletes the
   branch if it has no commits, and returns the clone to detached base.

A file with no code change (every case Portable as-is or cited existing issues)
gets no branch; its verdicts are still recorded.

### 3. Hand off for review

```
python3 $T/review_server.py --csv BATCH.csv        # prints http://<host>:8100/ (next free port)
```

Tell the user the URL and what is waiting (files, verdict counts, drafts to
submit, evidence warnings). To publish, the user clicks **Push to fork** on a file page
(one file) or on a bundle card (several files); the card then offers a share link
(diff on the fork) and **Create PR on GitHub ↗**. The user creates the PR there and
Fix PR is linked back automatically (see `xpu-enable-review`). Never push or open PRs yourself. The reviewer works in the UI (`xpu-enable-review`):
sets verdicts/comments/common prompts, submits drafts, builds PR bundles,
exports the CSV.

### 4. Rework mode ("process the rework queue")

```
python3 $T/rework_queue.py [--file <test_file>]
```

For each queued case the `instruction` is the file's **common prompt** plus the
**case comment** (the comment wins on conflict). For each file in the queue:
`file_branch.py checkout`, apply the instructions, re-measure with
`--run rework` (or `gate_removed` if you removed gates), update `cases.json`,
`verify_evidence.py`, commit (a new commit on the same branch is fine), then
`file_branch.py finish`. Mark accepted cases resolved, which moves the comment and
prompt into history instead of deleting them:

```
python3 -c "import store; store.resolve_rework('<slug>', ['<cid>', ...], '<what changed>')"
```

The queue also appends new guidance to `references/review_notes.md`; promote
recurring lessons into its "Distilled lessons" section (and into the sub-skills
when broadly applicable). If the queue warns that the file's PR is merged, tell
the user the rework needs a follow-up PR bundle.

## Verdicts

| verdict | meaning | required evidence |
|---|---|---|
| Portable | runs and passes on XPU after the port | PASSED at the final patch |
| Known Issue | runs, fails from a tracked bug | issue (open) or draft + gate-removed FAILED log |
| Feature gap | XPU lacks the capability | issue or draft + gate-removed FAILED log |
| Not Applicable | CPU/x86-only, CUDA-only API, or a wontfix feature | wontfix issue or `[wontfix]` draft + gate-removed FAILED log |

Exception: a case that already cites an issue `gh` confirms OPEN or `wontfix`
may skip the gate-removed run (set `cited_existing=true`, reason "cited existing #N").

## Guardrails

- Do not commit to the user's checkout or branches; all git work is in the clone.
- Do not rebuild torch or create/switch the user's envs; ask before `setup_ws.py --run`.
- Verify every cited issue's state with `gh` (`issues.py` caches it for a day).
- Never edit a log to make a verdict pass; re-measure instead.
- Never paste raw AI text to GitHub; drafts carry the AI disclosure line and the
  human submits them.
