---
name: xpu-enable-review
description: Run and explain the enable-xpu-tests review web UI (incl. push to fork, share link, prefilled GitHub PR page) and its human-in-the-loop actions - live report over ~/xpu_enable_ws/xpu_enable_report, per-case verdicts/comments, per-file common prompts, issue draft Submit/Backfill/Reject, editable patch re-applied onto the agent branch, Export CSV (new file, input untouched), PR bundles (select test files -> cherry-pick onto a target branch -> human pushes and opens the PR -> enter PR number -> Fix PR filled on every case), and the rework queue. Use when the user asks to start/serve the review page, export the reviewed CSV, create a PR from enabled files, fill in a PR number, or process reviewer comments.
---

# Review UI, export, PR bundles, rework

`T=enable-xpu-tests/tools`. The UI is `tools/ui/` (static). All data comes live
from `/api/*` over the report tree, so a browser refresh shows new results.

```
python3 $T/review_server.py --csv BATCH.csv [--port 8100] [--bind 0.0.0.0] [--dry-run]
```

It prints the URL to share (`http://<host>:<port>/`). If the port is busy it takes the next
free one. `--dry-run` never calls `gh issue create` and never pushes. Responses are
sent `Cache-Control: no-store`, so a reload always gets the current UI.
When testing endpoints from a shell, bypass any HTTP proxy (`curl --noproxy '*'`).

## Pages

**Index**

- One row per test file, showing: status, portability counts, review counts,
  agent branch, PR. The table always fits the page width: status is shortened to its
  first word (Imported / Processed / No change / Needs rebase / In progress) and long
  cells are clipped; hover a cell to see the full text.
- **Per-column dropdown filters** under the header. Each dropdown lists the values
  found in that column, with file counts; picking one keeps only matching rows, and
  columns combine (AND). Values:
  - Test file: the directory (`inductor/`, `dynamo/`, `nn/`, `(top level)`).
  - Status, Agent branch (`has branch` / `no branch`), Cases (`1-3`, `4-10`, ...).
  - Portability: `has <verdict>`, `all Portable`.
  - Review: `not started`, `partly reviewed`, `fully reviewed`, `has <verdict>`
    (incl. `has unreviewed`), `needs rework` (Needs work / Rejected / comments).
  - PR: `no PR yet`, `in bundle (not submitted)`, `has PR`, `PR #N`, `no patch`.
  **Quick views** above the table set common combinations: *to review, no PR yet*,
  *reviewed, ready for a PR*, *needs rework*, *bundled, not submitted*, *all files*.
  Selections survive reloads and navigation; **clear** resets them. Hidden rows are
  unticked, and select-all ticks only visible rows, so filter then build a bundle.
- Coming **back** from a file page (browser back or "all files") re-fetches the data
  and scrolls to that file's row, highlighted, so edits made on the file page show.
- **PR bundles waiting to be submitted** (status `picked`) are shown at the top, above
  the file table, each with its push / Create PR actions. Submitted and recorded PRs are
  listed at the bottom.
- Checkboxes plus a target branch and title let you create a PR bundle; after
  creating, the page scrolls to the new card. In the PR column a bundle id links to
  its card.
- A banner shows how many cases are in the rework queue.

**File page**

- A status card shows the branch, commit, base and bundle.
- **Common prompt**: a standing instruction for every case of the file on
  rework. It is also logged to `references/review_notes.md`.
- **Failure summary**: cases grouped by their latest XPU message, at most 5 rows.
  Click a row or a portability pill to filter the cases table.
- **Issue drafts**:
  - The title, labels and body can be edited.
  - **Submit** files the issue via `gh` after a confirm dialog.
  - **open prefilled GitHub page** lets you file the issue manually, then
    **Backfill** the number. A title match against existing issues is suggested
    automatically.
  - **Reject and need work** sets the draft's cases to Needs work and reverts
    the gate lines carrying its placeholder.
- **Patch**: read-only with a search box. Hand-editing is rarely needed and sits in a
  separate box below the patch: **Edit** unlocks it, **Apply edited patch to the agent
  branch** (asks for confirmation) rebuilds the agent branch as one commit = base +
  your edited diff and regenerates the `.patch`; **Cancel** reverts. If the diff does
  not apply, nothing changes. Applying makes earlier measurements stale; re-measure.
- **Source**: the read-only file on the agent branch.
- **Cases table**:
  - Columns: portability, reason, issue/draft, fix PR, last run status, message,
    **evidence** badge (verify_evidence rules, hover for details), **log**,
    review, comment.
  - **log** lists every run of the case, newest first, labelled *final code* (rework
    run on the committed patch), *gate removed*, *initial* or *imported (legacy, not
    current evidence)*, with the exact pytest node id and log file. **show matching
    log lines** prints the log's command line plus every line mentioning that node
    (and its failure block), then the full log.
  - **Bulk edit** applies to the checked rows: review, comment, portability,
    reason or issue.

**Header**

- **Export CSV** writes `<input_stem>_reviewed_<YYYYmmdd_HHMM>.csv` next to the
  input. The input file is never modified.
- **Reload** refreshes the data.

## Where edits go

| UI action | stored in |
|---|---|
| review verdict, case comment | `<slug>/reviews.json` (`verdicts`, `comments`) |
| common prompt | `<slug>/file.json` `file_prompt` (+ review_notes.md) |
| portability / reason / issue | `<slug>/cases.json` |
| draft edits, filed number | `<slug>/drafts.json` |
| patch edit | agent branch in the clone, then `<slug>.patch` regenerated |
| PR bundle | `_prs/<id>.json`, `_prs/<id>.body.md` |

## Issue Submit / Backfill

On Submit or Backfill the server:

- records `number`/`url` on the draft;
- sets `issue_number`/`issue_url` on the draft's cases, and replaces the
  placeholder in `reason`. An existing issue on a case is overwritten only when
  `gh` says that old issue is CLOSED and not wontfix;
- replaces `issues/<placeholder>` in the files the agent branch changed. It
  amends HEAD when HEAD already touched those files, otherwise it adds a
  "Backfill issue #N" commit;
- regenerates the patch.

Patch hashes ignore issue numbers, so earlier measurements stay valid.

## Submitting a PR (you create it on GitHub)

The **Pull request** card on each file page, and each bundle card, adapts to where the
branch is. The server checks the fork with `git ls-remote` (cached for 60 s):

| state | badge | actions |
|---|---|---|
| not pushed | `not pushed yet` | **Push to fork** (`git push --force-with-lease fork <branch>`, SSH) |
| pushed, in sync | `pushed to <fork>` | **Create PR on GitHub ↗** (prefilled, draft preselected), **Review diff (share) ↗**, **Copy share link**, a selectable share-link box, **I created it - link PR** |
| pushed, fork behind | `fork is behind local` | the same, plus **Push latest commits** |
| PR linked | `PR #N` | **Open PR #N ↗**, **Push update** |

- The share link is `https://github.com/<fork>/compare/<base_sha>...<branch>`: exactly
  this file's diff against viable/strict. Send it to reviewers before you open the PR.
- While pushed and not linked, the page checks `gh pr list --head <branch>` every 15 s
  (for 30 min). As soon as you create the PR on GitHub, `fix_pr` is filled on the file's
  cases. **I created it - link PR** checks right away.
- The title and description are prefilled. The description is **regenerated from the
  current verdicts** unless you edited it (changed text and filled in the human
  Summary). If your edited text no longer matches the verdicts, the panel opens with a
  warning; **Regenerate from current verdicts** discards your edits. The Test Plan is a
  portable `python -m pytest test/<file> -v -k "<xpu test names>"` line, with no local paths.
- **Manual alternatives** (collapsed): the push command, an empty PR page (paste your own
  text), and "already opened a PR? -> Set PR & fill Fix PR".
- Policy: pushing happens only on your click, and PR creation is always your own action
  on GitHub (AI_POLICY.md). `review_server.py --dry-run` disables pushing.
- CLI: `pr_bundle.py submit-file <test_file>` / `submit <bundle_id>` (asks y/N) push and
  print the page URL; then `file-link-pr <test_file>` / `link <bundle_id>` fills Fix PR.

## Recording PRs that already exist

If a PR was created outside these tools, map it to its cases without resubmitting:

```
python3 $T/pr_bundle.py record <number|url> --files a.py b.py [--cases id,id]
```

This verifies the PR with `gh`, sets `fix_pr` on those cases (all cases of each file
when `--cases` is omitted), and stores a `kind: external` bundle. The index and
file pages then show the PR. External bundles have no Update or Delete, but you
can Refresh their state. They are deletable only once the PR is closed. A file can
belong to several external PRs; each case keeps its own `fix_pr`.

## PR bundles (several test files -> one upstream PR)

1. **Create.** On the index page, tick the files, enter a **target branch** and a
   **title**, then click **Create PR bundle**.
   - Preflight **blocks** on: a dirty clone, a file with no agent commit, or a
     file already in an active bundle.
   - Preflight **asks for confirmation** on: cases not Confirmed, unsubmitted
     drafts, or evidence errors.
2. **Cherry-pick.**
   - The target branch is created at the newest base among the selected files,
     or reused if it already exists.
   - Each file's `base..agent_branch` commits are cherry-picked with `-x`, in the
     order selected. Each file keeps its own commits; nothing is squashed.
   - A conflicting file is aborted and marked `conflict`, with its paths listed.
     Its cases get no Fix PR. Resolve it by rebasing that agent branch, or remove
     the file from the bundle.
3. **Description.** It is pre-filled in the `xpu-pr-description` format: related
   cases, an empty human Summary, agent details in `<details>`, and a Test Plan
   built from the pytest command lines. Edit it and click **Save**, which writes
   `_prs/<id>.body.md`.
4. **Submit.** Click **Push to fork** on the bundle card; it then shows **Create PR on
   GitHub ↗** and **Review diff (share) ↗**, and you create the PR there (see
   "Submitting a PR"). The page links it back (polls every 15 s, or **I created it
   - link PR**); the bundle becomes `submitted` and every case of every picked file gets
   `fix_pr = <PR url>`. **Push latest commits** appears if the fork falls behind.
5. **Later.**
   - **Refresh state** updates open/draft/merged/closed. It is display only and
     changes no case data.
   - **Update branch** cherry-picks commits that rework added to agent branches
     after the bundle was made. Then click **Push update**.
   - **Remove** a file is allowed before the PR is linked, and rebuilds the target.
   - **Delete** is allowed before the PR is linked or after it is closed. It removes
     the bundle record and its local target branch, not the agent branches (and not
     a branch you already pushed to the fork).

CLI equivalents: `python3 $T/pr_bundle.py create|submit|link|update|remove|set-pr|refresh|delete|show|list`
(`submit <id>` asks y/N, pushes, prints the GitHub URL; `link <id>` fills Fix PR after
you created the PR).

## Export CSV columns

- Every input row is kept.
- These columns are appended: `portability`, `portability_reason`,
  `issue_number`, `issue_url`, `log`, `review_status`, `review_comment`,
  `agent_branch`, `patch`, `pr_bundle`.
- For cases of a **picked file in a submitted/merged bundle**, these are set:

| column | Portable | Known Issue / Feature gap / Not Applicable |
|---|---|---|
| `fix_pr` | PR URL | PR URL |
| `reason` | `PR ready` | the portability value |
| `detail_reason` | portability reason | portability reason |

If the input already has a different `fix_pr`, the input value is kept and is
listed in the export summary as a conflict. CLI: `python3 $T/export.py --csv
BATCH.csv [--out PATH]`. For a JUnit XML of the verdicts: `export.py --junit out.xml`.

## Rework queue (reviewer -> agent hand-off)

The UI only collects; it never launches the agent. When the user says "process
the rework queue":

```
python3 $T/rework_queue.py [--file <test_file>] [--json]
```

- A case is queued when it is not Confirmed and any of these holds: its verdict
  is Needs work or Rejected, it has a comment, or its file has a common prompt.
- Each item's `instruction` is the common prompt followed by the case comment.
  **The case comment wins on conflict.**
- Each item also carries the agent branch and the last result. It warns when the
  file's PR is already merged, because the rework then needs a new bundle.
- Every run appends new prompts and comments to `references/review_notes.md`.
  Entries are never duplicated.

Do the work as described in `enable-xpu-tests` "Rework mode". When a case is
accepted, call `store.resolve_rework(slug, case_ids, outcome)`. It sets the
verdict to Confirmed and moves the comment and prompt into `reviews.json` →
`history`. The file prompt is cleared only once every case of the file is
Confirmed. Promote recurring lessons from the notes log into its
"Distilled lessons" section.
