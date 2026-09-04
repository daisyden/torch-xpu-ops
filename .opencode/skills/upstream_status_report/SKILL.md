---
name: xpu-upstream-report
description: Build the interactive XPU upstream test-file status report (report.html) from the "test_files_by_category" xlsx and PyTorch PR data. Use when the user asks to regenerate/update/build the upstream status report, refresh the test-file status HTML dashboard, re-run the report pipeline, mark merged PRs as Done, or produce the forecast/timing/status charts for owned XPU test files. Triggers include "build the report", "regenerate report.html", "update the upstream status report", "refresh PR status and rebuild the dashboard", "mark merged files Done".
---

# XPU Upstream Test-file Status Report

Generates `report.html` — a self-contained, interactive (Chart.js) dashboard of
owned XPU upstream test-file status and PyTorch PR progress. It covers six
sections: (1) test-file status, (2) status by team, (3) PR gates, (4) PR timing,
(5) forecast (when all files pass, with burn-down + throughput rate formulas),
(6) trends — historical PR/file stock-by-stage per day, last-30-day flow of
milestone events, and a 30-day-rate forecast table.

## Data directory

All scripts operate on a working directory that must contain:
- `test_files_by_category_*.xlsx` (the source spreadsheet, "Test Files" sheet)
- `pr_cache/` (cached `gh` PR JSON, one `<pr>.json` per PR)

The canonical location is `/home/daisyden/opencode/upstream_status`. Run the
pipeline **from that directory** (scripts resolve the xlsx and `pr_cache/`
relative to their own location, so copy them into the data dir if running the
skill copies). Prefer the scripts already present in the data dir.

## Prerequisites

- `gh` CLI installed and authenticated (`gh auth status`) — needed for PR fetch.
- `python3` with `openpyxl` (xlsx read/write).

## Quick start

Run the whole pipeline from the data directory:

```bash
cd /home/daisyden/opencode/upstream_status
./build_report.sh                 # incremental: fetch only missing PRs
./build_report.sh --all           # FULL refresh: SharePoint xlsx + all PRs + new PRs + Google doc
./build_report.sh --pull-xlsx     # first pull latest xlsx from SharePoint (rclone)
./build_report.sh --refresh       # re-fetch every PR (realtime state)
./build_report.sh --discover      # also find new owner PRs not yet in the xlsx
./build_report.sh --mark-done     # realtime check + mark merged files Done in xlsx
./build_report.sh --pull-xlsx --refresh --discover --mark-done
```

**To refresh the report from all three sources, use `--all`.** It is shorthand for
`--pull-xlsx --refresh --discover`, which:
1. pulls the latest **SharePoint xlsx** (`pull_xlsx.sh`, rclone),
2. re-fetches **every PR** from GitHub and **discovers new owner PRs**, and
3. re-fetches the **Google-doc refactor tracker**.

The **Google-doc refactor tracker is always refreshed on every run** (step 4b),
regardless of flags — no flag is needed for it.

Note: a plain `--refresh` only re-fetches PRs **already recorded** in the xlsx; it
does **not** find new owner PRs. Use `--discover` (included in `--all`) to pull in
newly submitted owner PRs that aren't in the table yet.

### Pulling the source xlsx from SharePoint (`--pull-xlsx` / `pull_xlsx.sh`)

The spreadsheet lives on the Intel "DLFT dGPU" SharePoint site
(`.../sites/MLTSHdGPU`, path `PyTorch GPU/Upstream/UT/UT_Upstream/`). It is **not**
anonymously downloadable, so `pull_xlsx.sh` fetches it through an `rclone` OneDrive
remote named `mlts` (`drive_type=documentLibrary`, the site's "Documents" library
`drive_id`). rclone stores the OAuth refresh token and auto-renews the short-lived
access token, so no re-auth is needed for routine pulls.

```bash
./pull_xlsx.sh                    # backs up current xlsx, then rclone copy latest
```

One-time / re-auth setup (only if the refresh token is lost or revoked):

```bash
rclone authorize "onedrive"                       # produces a token JSON
rclone config update mlts token '<new-json>'      # or `config create mlts ...`
```

Output: `report.html` in the data directory.

## Pipeline (what build_report.sh runs)

| Step | Script | In → Out |
|------|--------|----------|
| 1 | `extract_owned.py` | xlsx → `/tmp/owned.json` (owned rows where team col L set, + referenced PR numbers) |
| 2 | `fetch_prs.py [--refresh]` | `gh` → `pr_cache/*.json` (fetch missing, or all with `--refresh`) |
| 2b | `discover_prs.py --apply` *(only with --discover)* | realtime `gh` → xlsx: finds new owner PRs touching tracked files and appends links (backs up xlsx first), then re-extracts + re-fetches |
| 3 | `mark_done.py` *(only with --mark-done)* | realtime `gh` → xlsx: sets `Status=Done` for files whose PR(s) are **all merged** (backs up xlsx first) |
| 4 | `analyze.py` | `pr_cache/` → `/tmp/pr_analysis.json` (per-PR gates: internal/community review, CI state; timing to each milestone) |
| 4b | `fetch_refactor_tracker.py` | Google Sheet → `/tmp/refactor_tracker.json` (community test-refactor PRs: status, owner, PR links, keyed by file path) |
| 5 | `gen_report.py` | `/tmp/*.json` → `report.html` |

The community refactor tracker (`fetch_refactor_tracker.py`) pulls the public "Test Class Refactoring Tracker" sheet (ID `1cDNiLW4KvPcGYPlA3KCDm0zV5PLPUWubno1OyCznKBw`, tabs Core/Tensor/Distributed/Graph/Math/Quantization/Utils) via the gviz CSV endpoint. `gen_report.py` joins it onto **not-yet-Done** (To Do) files and, in the detail-panel file tables, adds columns right after the **Author** column: **Refactor PR** (links), **R.Owner** (assignee), **R.Status** (🔵 Todo / 🟡 In Progress / 🟢 Done), then **PR not recorded** (see below). If the JSON is missing the refactor columns are simply blank.

### Detail-table PR columns (what each column means)

In the detail-panel file tables, PRs are split into three separate buckets so
Intel port progress is never mixed with community/refactor work:

- **PR** column — **Intel PRs only** (xlsx col F, the Excel assignee's port PRs).
  Rendered from the full PR cache, so any fetched Intel PR (even a discounted
  one) shows its real data rather than a placeholder.
- **Refactor PR / R.Owner / R.Status** — community refactor PRs **recorded in the
  Google-doc tracker** for that file.
- **PR not recorded** — community PRs listed in the xlsx **Community PRs** column
  (col O) that are **not** found in the Google-doc tracker for that file. These
  are surfaced as links so untracked community work can be triaged.

The **Author** column of the file tables is the Excel **Assignee** (col P) **only**
— no fallback to the `owner` column. Unassigned files show a blank Author.

Each PR row ends with a **state** column (open/merged/closed) and a **Ready**
column showing the PR's ready-for-review status: `ready` (green) when the PR is
not a draft, `draft` (grey) when it is still a GitHub draft. Sourced from the
`isDraft` field (`analyze.py` → `is_draft` in `pr_analysis.json`).

`write_refactor_cols.py` writes the same tracker info back into the **xlsx** for To Do files (Status ≠ Done): cols **T** Refactor PR, **U** Refactor Owner, **V** Refactor Status (backs up first). Run `fetch_refactor_tracker.py` before it. It runs automatically in `build_report.sh --refac-cols` (which re-extracts afterward).

If `--mark-done` is used, the xlsx changes, so step 1 is re-run before analyze.

## Discovering new PRs (`discover_prs.py`)

Finds and fills in PR links for tracked files that gained new PRs since the last run. Run `extract_owned.py` first so `/tmp/owned.json` exists.

1. Collects the author logins of all PRs already recorded in the table (from `pr_cache/`).
2. Cutoff = newest `createdAt` among recorded PRs (override with `--since YYYY-MM-DD`).
3. `gh search prs` for each author's pytorch/pytorch PRs created after the cutoff.
4. For each candidate, fetches changed files; a PR matches when a changed file path equals a tracked file path (col C) and the PR isn't already recorded.
5. Appends the PR URL to the PR column (col F), de-duplicated; backs up the xlsx before saving.

```bash
python3 discover_prs.py                    # dry-run: list matches, no writes
python3 discover_prs.py --apply            # write links into xlsx (backup .predisc_*.bak.xlsx)
python3 discover_prs.py --apply --rebuild  # then run build_report.sh --refresh
python3 discover_prs.py --since 2026-08-01 --limit 200
```

Note: `--rebuild` runs `build_report.sh --refresh` which re-fetches every PR and can take several minutes.

## Assigning internal reviewers (`assign_reviewers.py`)

Proposes an internal reviewer for every **open** PR that doesn't have one yet
(no internal reviewer has actually *reviewed* it — a stalled reviewer *request*
that nobody acted on still counts as needing one). **Scope by default: only PRs
authored by an Excel assignee** (a PR recorded in the Intel-PR column, col F, of
a row whose assignee col P is set — plus known assignee logins such as
`madhumitha0102`). Pass `--all-open` to consider every open PR. Needs
`/tmp/owned.json` and `/tmp/pr_analysis.json` (produced by a normal build).

- **Domain** is inferred from the PR's test-file paths (sdpa/attention,
  distributed, inductor/dynamo, runtime, ops, else refactor/other).
- **Expertise preference** (a *soft* preference, not a hard rule): guangyey=runtime,
  etaf=inductor, CuiYifeng=ops, liangan1=sdpa, newtdms=distributed,
  astachowiczhabana & pbielak=test refactor/other.
- **Load balancing**: each reviewer's current pending open-PR load is counted;
  the assignee minimizes `load + penalty·(not-expert)`. A `--penalty` of 0 gives
  pure balancing; higher values weight expertise more (default 2). An overloaded
  expert therefore spills over to lighter-loaded reviewers.
- **Delivery**: collaborators get `method=request` (formal review request);
  `CuiYifeng`/`newtdms` (non-collaborators) get `method=comment` with a ready-to-
  paste `@mention` comment. **No GitHub calls are made** — apply mode only writes
  a file for manual action.

```bash
python3 assign_reviewers.py                 # DRY-RUN: print plan + resulting loads
python3 assign_reviewers.py --apply         # write /tmp/reviewer_assignments.{json,csv}
python3 assign_reviewers.py --github-apply   # LIVE: request reviewers / post @mention comments
python3 assign_reviewers.py --penalty 0     # pure load-balancing (ignore expertise)
python3 assign_reviewers.py --skip-drafts   # don't assign draft PRs
python3 assign_reviewers.py --all-open      # consider every open PR, not just assignee PRs
./build_report.sh --assign                  # run a build then print the dry-run plan
./build_report.sh --assign-apply            # run a build then LIVE-post requests/comments
```

**Live apply (`--github-apply` / `build_report.sh --assign-apply`)** actually
writes to GitHub. It first prints the plan and **prompts for confirmation**
(type `yes`) before posting anything — pass `--yes`/`-y` to skip the prompt in
non-interactive runs. It is **idempotent**:
- Collaborators get a formal reviewer request (`gh api ... /requested_reviewers`);
  skipped if the reviewer is already requested (live check).
- Non-collaborators (`CuiYifeng`, `newtdms`) get an `@mention` comment
  (`gh pr comment`) tagged with a hidden marker `<!-- xpu-auto-review-request -->`;
  skipped if a prior auto-request comment for that reviewer already exists.
- Scope is the same Excel-assignee default (drafts included) unless `--all-open`.

Section 7 of the report ("Internal review workload") charts the current state:
open PRs waiting for internal review per reviewer, and (all PRs) open-under-review
vs. approved per reviewer. Both are click-through to the PR lists.

## Sharing the report with others

Serve `report.html` over HTTP so others can open it in a browser:

```bash
cd /home/daisyden/opencode/upstream_status
python3 serve_report.py               # binds 0.0.0.0:8000, prints shareable URLs
python3 serve_report.py --port 9000   # custom port
python3 serve_report.py --bind 127.0.0.1   # local only
```

It prints `http://<your-lan-ip>:<port>/report.html` for people on the same
network. For viewers outside the network, expose the port with a tunnel:

```bash
ngrok http 8000
# or
cloudflared tunnel --url http://localhost:8000
```

Since `report.html` is fully self-contained, you can also just send the file
directly (e.g. email/Slack) — no server required.

## Running steps individually

```bash
python3 extract_owned.py          # xlsx  -> /tmp/owned.json
python3 fetch_prs.py --refresh    # refresh PR cache (realtime)
python3 mark_done.py              # mark merged files Done (edits xlsx, makes backup)
python3 analyze.py                # -> /tmp/pr_analysis.json
python3 gen_report.py             # -> report.html
```

## Key domain rules (encoded in the scripts)

- **Owned** = spreadsheet rows with team/owner column L (SH/PL/US) set.
- **Merged** detection = PR `state == MERGED`, or `Merged` label + `closedAt`
  (ghstack closes without MERGED state).
- **Internal reviewers** (7): `guangyey`, `etaf`, `CuiYifeng`, `liangan1`,
  `newtdms`, `astachowiczhabana`, `pbielak`. **Community** = any external
  (non-Intel) approver (e.g. `jansel`, `fffrog`).
- **Informal approvals**: `CuiYifeng` and `newtdms` are **not formal
  collaborators**, so they may not be able to cast a formal *Approve* review. For
  these two, an approval expressed in a **review body** or a **plain PR comment**
  (matching `lgtm|approved?|looks good|ok to merge`) is accepted as an internal
  approval (`fetch_prs.py` pulls `comments`; logic in `analyze.py`).
- **CI required labels**: refactor→`ciflow/trunk`, XPU→`ciflow/xpu`,
  distributed→`ciflow/h100-distributed`. A PR with **no** required ciflow label
  cannot run CI (`ci_state == 'no_required_label'`).
- **Gates** (computed from real PR data in `analyze.py`): **internal review**
  (`etaf`/`guangyey`) must pass **before community review** — community only
  counts once internal has passed. **CI is independent** of the review pipeline
  (a PR can be community-approved while CI never ran due to a missing ciflow
  label). In the **Gate pending** chart, the **Community review** bar counts only
  open PRs that have **already passed internal review AND CI** but still lack
  community approval (a PR isn't "pending community" until it's otherwise ready).
- **Sections 3/4/5 track Intel PRs only.** `analyze.py` tags each PR with
  `is_refactor` = true when it comes **only** from the xlsx Community PRs column
  (col O) and is not also an Intel PR (col F). `gen_report.py` **discounts these
  community/refactor PRs** from the PR gates (Section 3), timing (Section 4) and
  forecast (Section 5). They remain visible in the **Refactor** / **PR not
  recorded** columns. The Section-3 note reports how many were discounted.
- **File-level gates** (`_gates()` in gen_report.py) are derived from real PR
  data, not the spreadsheet status ordering:
  - Open PRs govern the file: a gate passes only if **every open PR** passes it.
  - Community review only counts when internal review has also passed.
  - A merged PR completes the file only when **no open PR is still pending**.
  - **Closed-but-never-merged (abandoned) PRs are dropped** everywhere: they are
    removed from `recs` at load time in `gen_report.py`, so they never appear in
    gates, charts, timing, or the detail tables. A file whose only PR was
    abandoned has no PR data and falls back to **TBD**.
  - No fallback to the spreadsheet's in-flight status: if a file has **no PR
    data**, its in-flight stage can't be trusted, so it is shown as **TBD**
    (only `Done`/`Not Applicable`/`WIP`/`TBD` human statuses are authoritative).
- **Effective status shown in the report** (`status_label()`): the **real PR
  stage** (from PR data) overrides the spreadsheet for in-flight files, so a
  status can never over-state progress (e.g. a file marked "Community review"
  whose PR hasn't passed internal review by guangyey/etaf is shown as
  **Internal Review**). A human `Done` / `Not Applicable` / `WIP` is kept
  authoritative; the spreadsheet value is used when no PR data is cached.
  Raw spreadsheet stage (`classify()`) is retained only for the status audit.
- **Flags / status audit** surfaced in Section 3 (raw spreadsheet stage vs real
  PR stage, ranked `PRed<CI<Internal Review<Community Review<Done`):
  - `no_ciflow` — open PRs missing a ciflow label (CI can't run).
  - `ahead` — spreadsheet status is **ahead of** the real PR stage (e.g. marked
    Community/Internal review but gates not actually passed).
  - `behind` — PR has already progressed **past** the spreadsheet status
    (status can be advanced).
  - `nodata` — active file has a status but no cached PR data to verify.
  Each file row shows `sheet: <stage> | real: <stage>`.
- **Status categories** (`CAT_ORDER` in gen_report.py): Done, Community Review,
  Internal Review, CI, PRed, Not Applicable, WIP, TBD. Section-1 chart collapses
  Community/Internal/CI/PRed into a single **Open PR** slice.
- **Forecast** (Section 5, gate-aware, in **PR units** not raw files):
  - Active backlog = files not `Done` and not `Not Applicable`.
  - Four milestones: **PR created**, **Internal review**, **CI pass**,
    **Merge / all pass**. Each counts only files not yet past that gate
    (already-open PRs are reused).
  - **PR units** = existing PRs (files sharing a PR count once) + new PRs for
    un-PR'd files, where new PRs are batched:
    - **inductor** files (path contains `inductor`) → 4 files per PR
      (`ceil(n/4)`).
    - **distributed** files (`distributed == True`) → 4 files per PR.
    - `device_agnostic` files → 1 PR per team.
    - all other files → 1 PR each.
  - Rates = events in the **last 4 weeks ÷ 4** for creation and each gate
    (`rate_create`, internal, CI, merge PR/wk).
  - A gate can't outrun PR creation, so its **effective rate = min(gate rate,
    creation rate)**. Finish date = today + (PR units ÷ effective rate).
  - Burn-down chart plots one line per milestone (PR units remaining vs weeks).

## Notes

- The report **header** shows two timestamps: **PR data updated** (newest
  `pr_cache/*.json` mtime — when PR data was actually fetched) and **report
  generated** (when `gen_report.py` ran), both UTC. The header **"Status = Done"**
  card uses the **effective** status count (same as the Section-1 chart), so it
  includes files whose Intel PR is merged even if the sheet's Status cell wasn't
  updated to "Done" — it can be higher than the raw spreadsheet "done" count.
- **xlsx columns used** (1-indexed): C Path, D File Name, E `owner` (lead),
  F PR (Intel PRs), G device_relevance, H xpu-enabled, L `Owner` (team code
  SH/PL/US), O Community PRs, P Assignee (drives the report **Author**),
  Q Status, T/U/V Refactor PR/Owner/Status (written by `write_refactor_cols.py`).
- `mark_done.py` edits the source xlsx and writes a timestamped `.bak.xlsx`
  backup — only run it when the user wants to update spreadsheet status.
- `report.html` is self-contained (loads Chart.js + datalabels from CDN) and
  ~2 MB; open it directly in a browser.
- To tweak charts/sections, edit `gen_report.py` (f-string HTML; JS braces are
  doubled `{{ }}`; `DETAILS` is embedded JSON parsed client-side). **After
  editing, always validate the embedded JS**: extract the `<script>` block and
  run `node --check` on it, because a stray un-doubled `\n`/`{`/`}` in a JS
  string silently breaks the whole script (the report renders "no data").
