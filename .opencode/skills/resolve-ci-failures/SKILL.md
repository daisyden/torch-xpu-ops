---
name: resolve-ci-failures
description: Triage and resolve CI failures on a PyTorch pull request, then rebase it onto pytorch/pytorch viable/strict. Classifies each failing job as new-but-unrelated, caused-by-the-PR, lint, XPU timeout, broken trunk, flaky or unstable, fixes what the PR broke, reruns failed jobs when a rerun is the right answer, and stops for human review before any commit, push, rebase or GitHub write. Use when the user gives a PyTorch PR number or URL and asks to resolve/fix/triage its CI failures, make CI green, "rebase to viable/strict", deal with a red PR, handle lint failures, an XPU test timeout, or a broken-trunk/flaky signal. Triggers include "fix the CI failures on this PR", "why is my PR red", "rebase my PR onto viable/strict", "rerun the failed jobs", "is this failure mine or trunk's".
---

# Resolve CI Failures on a PyTorch PR

Input: a `pytorch/pytorch` PR number or URL. Output: a triage table, any
necessary code fixes, and a rebase onto `viable/strict` — with a human review
gate before every commit, push, rebase and GitHub write action.

## Hard rules

- **Never act on GitHub autonomously.** Reruns and pushes are GitHub-visible.
  Show the exact command and wait for explicit approval (`AI_POLICY.md`,
  repo `AGENTS.md`).
- **Stop for review before every commit, push and rebase.** Print `git diff`
  and the commands you intend to run, then wait.
- **Rebase conflict ⇒ stop.** Do not resolve. Leave the rebase in progress,
  report the conflicting files and `git status`, hand it to the user. Do not
  `--abort`, `--skip`, or guess a resolution.
- **Never force push anywhere but the PR's own head branch**, and only with
  `--force-with-lease`. Never touch `gh/<user>/N` branches (ghstack owns them).

## Step 1 — Triage

```bash
python3 scripts/ci_triage.py --pr <PR> \
  --out agent_space/ci_triage_<PR>.md \
  --json-out agent_space/ci_triage_<PR>.json
```

Read-only. It combines three sources:

1. `gh pr view --json ...` — head repo/branch/sha, draft, labels, mergeable.
2. The first `pytorch-bot[bot]` (Dr. CI) comment — **source of truth** for the
   `NEW FAILURE` / `BROKEN TRUNK` / `FLAKY` / `UNSTABLE` buckets. The parser
   also extracts the failing-test/lint **hint** and Dr. CI's own
   **`AI verdict: related|unrelated`** badge, plus the `trunk failure` link.
3. `scripts/fetch_checks.py` — all head-sha check runs via **paged GraphQL**.
   Do not replace this with `gh pr checks --json` (missing on gh 2.45) or
   `check-runs?per_page=100` (truncates at 100 on PyTorch PRs, which routinely
   have 140+ checks, making red look green).

Checks are matched to Dr. CI bullets by **job id**, since Dr. CI renders names
as `workflow / job` while GraphQL splits them.

| Class | Meaning | Rule | Step |
|---|---|---|---|
| `lint` | job name matches lint | rule 4 | 2 |
| `xpu_timeout` | xpu/intel job, cancelled or timeout text | rule 3 | 3 |
| `xpu_check_log` | xpu job failing for some other reason | — | 3 (read log first) |
| `broken_trunk` | Dr. CI `BROKEN TRUNK`, not PR-related | rule 6 | 4 |
| `broken_trunk_related` | `BROKEN TRUNK` + AI verdict `related` | rule 6 | 4 |
| `flaky` / `unstable` | Dr. CI `FLAKY` / `UNSTABLE` | — | ignore, record |
| `new_related` | `NEW FAILURE`, verdict `related` or hint overlaps diff | rule 2 | 5 |
| `new_unrelated` | `NEW FAILURE`, verdict `unrelated` / no overlap | rule 1 | 6 |
| `unknown` | failing but in no Dr. CI bucket | — | read the log |

The script warns if the Dr. CI comment's as-of sha ≠ current head — the bot lags
a fresh push by ~15 min, so **wait and re-run** rather than trust stale buckets.

**Always verify `new_related` vs `new_unrelated` against the real log** before
acting; the heuristic is weak when Dr. CI gives no AI verdict:

```bash
gh run view --log-failed --job <job_id>    # job_id is in the report's Log column
```

Relatedness checklist — the failure is caused by the PR if any holds:
- the failing test file or module is in the PR diff
- the failing test exercises a symbol the PR changed
- the error names a symbol, op, flag or file the PR touched
- it reproduces on the PR head but not on the merge base

Otherwise it is unrelated.

## Step 2 — Lint (rule 4: fix, then rebase)

Only `spin` for linting (repo `AGENTS.md`):

```bash
spin lint        # report
spin fixlint     # auto-fix
lintrunner -a    # required before committing
```

Fix by hand whatever `spin fixlint` cannot. Never silence `S101` with
`# noqa: S101`; rewrite to `if not cond: raise AssertionError(...)`.
For B950 inside a multi-line string, put `# noqa: B950` on the line with the
closing triple quote.

→ Step 7 review gate → commit → Step 8 rebase.

## Step 3 — XPU timeout (rule 3: rerun all failed jobs)

A timeout is an infra/duration signal, not a code signal. For `xpu_check_log`,
read the log first; only treat it as a timeout if the log says so, otherwise
re-triage as `new_related` / `new_unrelated`.

Rerun **all failed jobs in the Checks tab**:

```bash
python3 scripts/rerun_failed.py --pr <PR> --dry-run   # ALWAYS first
python3 scripts/rerun_failed.py --pr <PR>             # only after approval
```

It groups failed checks by workflow run and issues `gh run rerun <run_id> --failed`
once per run. HTTP 403 means no write access — report it and ask the user to
press "Re-run failed jobs" in the UI. Never substitute an empty commit.

Then wait for CI and re-run Step 1. A second timeout on the rerun is no longer
"just a timeout" — re-triage it as a real failure.

## Step 4 — Broken trunk (rule 6)

- `broken_trunk_related` (or your own read says the PR touches that area):
  rerun all failed jobs (Step 3 command), then re-triage.
- `broken_trunk`: **ignore.** Record the trunk-failure link as evidence.
  The Step 8 rebase normally clears it, since `viable/strict` only advances on
  green trunk — which is exactly what Dr. CI itself recommends.

## Step 5 — Failure caused by the PR (rule 2: fix, then rebase)

1. Reproduce locally: `python test/test_foo.py -k test_bar`. If a rebuild is
   needed, use only `pip install -e . -v --no-build-isolation` — never another
   build command. Check memory for build env vars first; ask if absent.
2. Root-cause it, and write the cause down before editing.
3. Fix minimally, matching surrounding style (repo `AGENTS.md` coding style).
4. Re-run the failing test and its neighbours.
5. `lintrunner -a`.
6. → Step 7 review gate → commit → Step 8 rebase.

## Step 6 — New failure unrelated to the PR (rule 1)

No code change. Go straight to Step 8. Record the failure and why you judged it
unrelated, so it can be rechecked after the rebase.

## Step 7 — Review gate (rule 7)

Before *any* commit, push or rebase, show:
- `git status` and the full `git diff` (`--stat` first if large)
- the exact git commands you plan to run

Then wait for approval. Commit rules (repo `AGENTS.md`):

- Only commit when the user explicitly says so; `lintrunner -a` first.
- Message: no bullet list of individual changes; a `Test Plan` section with the
  literal commands in fenced code blocks; root cause + how the fix works for a
  bug fix; disclose AI-assisted authorship.
- **ghstack commit** (detached HEAD, or `ghstack-source-id` / `Pull-Request`
  trailer): do not amend unless explicitly asked — leave changes uncommitted for
  `git diff` review. Preserve `Pull-Request:` and `ghstack-source-id:` trailers,
  re-read from HEAD at amend time (ghstack rewrites the source id on every push,
  so never reuse a cached message body). Submit with `ghstack --no-stack`.
- Branch PR on a real branch (including `main`): branch first before committing.

## Step 8 — Rebase onto viable/strict (rules 1, 2, 4; rule 5 on conflict)

```bash
python3 scripts/rebase_viable_strict.py --dry-run   # fetch + ahead/behind only
python3 scripts/rebase_viable_strict.py             # after approval
```

The script auto-detects the remote pointing at `pytorch/pytorch`, refuses to run
with a dirty tree or a rebase already in progress, detects detached HEAD
(ghstack), and **on conflict prints the conflicting paths and exits 2 with the
rebase left in progress** — that is rule 5: stop and let the user check.

Equivalent by hand:

```bash
git fetch <upstream> viable/strict
git rebase <upstream>/viable/strict
```

Clean rebase ⇒ review gate (`git log --oneline <upstream>/viable/strict..HEAD`,
and `git range-diff` against the pre-rebase sha) ⇒ then push:

```bash
git push --force-with-lease <fork-remote> HEAD:<pr-head-branch>
```

ghstack PR: do not push branches — run `ghstack --no-stack` after approval.

After the push, wait for CI and loop back to Step 1 until the only reds left are
`flaky`, `unstable`, or ignored `broken_trunk`.

## Reporting

`scripts/ci_triage.py` writes the table to `agent_space/ci_triage_<PR>.md`:

| Job | Workflow | Dr. CI bucket | Class | Evidence | Action | Log |
|---|---|---|---|---|---|---|

Append an outcome per row as you work. `agent_space/` is git-ignored — never
commit from it.

## Gotchas

- A PR's **labels** are the lifecycle source of truth:
  `gh pr view <PR> --json labels,isDraft`. Next steps live in the Dr. CI comment.
- `gh pr checks` / the rollup reflect the **current head sha**; after a push the
  old results are gone. Re-run Step 1 instead of trusting a stale JSON dump.
- `unstable` jobs never block a merge. Don't spend effort on them.
- `.ci/docker/` is content-hashed — any edit forces a full Docker image rebuild.
  Never touch it to "fix" CI.
- A `ghstack-mergeability-check` failure usually means the stack needs a restack
  or the base PR moved, not a code bug.
- `gh pr checks` exits non-zero (8) when checks are failing; that is expected,
  don't treat it as a tooling error.
