# enable-xpu-tests tools

Python 3.10+, stdlib + PyYAML. Run from this directory. `$XPU_ENABLE_WS`
(default `~/xpu_enable_ws`) selects the workspace; `$XPU_ENABLE_NOTES` overrides
the review-notes log (default `../references/review_notes.md`; set it when
testing).

| tool | purpose |
|---|---|
| `setup_ws.py` | create/refresh the workspace (clone @ viable/strict, venv + nightly XPU wheel), record fork remote/owner |
| `select_cases.py` | input CSV minus cases already in the report tree / with fix_pr / in a submitted bundle |
| `file_branch.py` | per-file branch lifecycle: `start`, `checkout`, `finish`, `regen-patch` |
| `precheck.py` | API catalog classification + wontfix candidates per case; `--update-catalog` |
| `measure.py` | pytest run (-v -rA --junitxml), per-case results, source snapshots, `_internal` overlay |
| `verify_evidence.py` | evidence rules R1-R10; nonzero exit on ERROR |
| `store.py` | the only reader/writer of the report tree; CLI `list/show/set-case/add-draft` |
| `issues.py` | gh issue lookup (cached 1 day), search, create |
| `gitops.py` | clone helpers: clean check, switch/restore, patch write/re-apply, amend-or-commit, cherry-pick |
| `pr_bundle.py` | PR bundles: create/update/remove/set-pr/refresh/delete/show/list |
| `export.py` | reviewed CSV (new file) and JUnit XML |
| `rework_queue.py` | reviewer-flagged cases + instructions; appends to review_notes.md |
| `review_server.py` | JSON API + serves `ui/` |
| `common.py` | paths, slug, cuda->xpu mapping, constants |
| `migrate_legacy.py` | one-time import of a v1 report tree + verdict CSVs + legacy branches into the v2 workspace (results marked `run=imported`, to be re-measured) |

## Workspace

```
$XPU_ENABLE_WS/
  workspace.json     {base_ref, base_sha, nightly_git_version, fork_remote, fork_owner, fork_repo, created}
  pytorch/           clone; idle state = detached HEAD at a file's base
  env/               venv; measure.py uses env/bin/python when present
  _internal.orig/    pristine torch/testing/_internal of the venv (for --restore)
  xpu_enable_report/
```

## Report tree

```
xpu_enable_report/
  <slug>/                      slug = test path without "test/" and ".py", "/" -> "_"
    file.json
    cases.json
    reviews.json
    results.json
    drafts.json
    <slug>.patch               git diff base_sha..agent_branch
    logs/<run>_<ts>.log        tee of pytest; first line is the literal command
    logs/<run>_<ts>.N.xml      JUnit XML per -k chunk
    logs/<run>_<ts>.src/       snapshot of files changed vs base (gate_removed/rework)
  _prs/<id>.json, <id>.body.md
  _issues.json                 gh issue state cache
```

`file.json`
```json
{"test_file": "inductor/test_fp8.py", "slug": "inductor_test_fp8",
 "source": "test/inductor/test_fp8.py", "base_sha": "<sha>",
 "agent_branch": "agent_inductor_test_fp8_4821", "commit": "<short sha>",
 "patch_sha": "<hash of patch, issue numbers normalized>",
 "status": "In progress|Processed|No change", "file_prompt": "", "bundle": "<pr id>",
 "pr_title": "", "pr_body": ""}
```

`cases.json`: `{case_id: {...}}`
```json
{"class_name": "TestFP8CUDA", "test_name": "test_x_cuda", "portability": "Feature gap",
 "reason": "...", "issue_number": "1234", "issue_url": "...", "draft_id": "d1",
 "fix_pr": "https://github.com/pytorch/pytorch/pull/N", "cuda_only": false,
 "cited_existing": false}
```

`reviews.json`
```json
{"verdicts": {"<cid>": "Confirmed|Needs work|Rejected"},
 "comments": {"<cid>": "text"},
 "history": [{"case_id": "", "comment": "", "prompt": "", "outcome": "", "at": ""}]}
```

`results.json`: `{case_id: [entry, ...]}` (append-only; latest last)
```json
{"run": "precheck|initial|gate_removed|rework", "nodeid": "file::Class::test_xpu",
 "status": "PASSED|FAILED|ERROR|SKIPPED|XFAIL|NOT_RUN", "reason": "skip reason / first error line",
 "error_excerpt": "E lines", "log": "logs/....log", "patch_sha": "...",
 "src_snapshot": "logs/....src", "at": "..."}
```
Precheck entries instead carry `apis`, `cuda_only_apis`, `gates`, `plan`,
`wontfix_candidates`.

`drafts.json`
```json
[{"id": "d1", "placeholder": "XXXX_SHORT_NAME", "title": "", "body": "", "labels": ["test: ut"],
  "cases": ["<cid>"], "verdict": "Feature gap", "number": 1234, "url": "..."}]
```

`_prs/<id>.json`
```json
{"id": "pr_20261109_1234", "kind": "multi|single|external", "title": "", "target_branch": "", "base_sha": "", "created": "",
 "status": "picked|submitted|merged|closed",
 "files": [{"slug": "", "test_file": "", "state": "picked|conflict",
            "picked": {"<agent sha>": "<target sha>"}, "conflicts": []}],
 "pr": {"number": 0, "url": "", "state": "OPEN|DRAFT|MERGED|CLOSED", "head": "", "checked_at": ""},
 "description": "markdown"}
```

## CLIs

```
setup_ws.py [--run] [--reference DIR] | --refresh [--run] | --status | --fork-remote R --fork-owner O [--fork-repo OWNER/NAME]
select_cases.py --csv CSV [--filter S] [--file F ...] [--include-pr-files] [--json]
file_branch.py start|finish|checkout|regen-patch <test_file> [--csv CSV] [--cases id,id]
precheck.py --file F [--csv CSV] [--no-gh] [--json] | --update-catalog [--yes]
measure.py --file F --run initial|gate_removed|rework [--cases id,id] [--force] [--no-overlay] [--timeout S]
measure.py --restore
verify_evidence.py [--file F ...] [--no-gh] [--json] [--warn-as-error]
store.py list | show SLUG | set-case SLUG CID k=v ... | add-draft SLUG draft.json
pr_bundle.py create --target B --title T --files F ... [--force] | update ID | remove ID --files F ...
             | set-pr ID REF [--confirm-head] | refresh ID | delete ID | show ID | list
             | file-link TEST_FILE | file-set-pr TEST_FILE REF [--confirm-head]
             | record REF --files F ... [--cases id,id]      (map an already-submitted PR)
             | submit-file TEST_FILE | submit ID              (push + print prefilled PR page; asks y/N)
             | file-link-pr TEST_FILE | link ID               (after creating the PR on GitHub: fill fix_pr)
export.py --csv CSV [--out PATH] | --junit OUT.xml [--file F ...]
rework_queue.py [--file F ...] [--json] [--no-notes]
migrate_legacy.py --legacy-repo DIR --csv CSV [--csv CSV ...] [--branches B ...] [--patch TEST_FILE=PATH ...] [--no-branches] [--dry-run]
review_server.py --csv CSV [--port 8100] [--bind 0.0.0.0] [--dry-run]   (next free port if busy)
```

## HTTP API (review_server.py)

| method | path | body | effect |
|---|---|---|---|
| GET | `/api/data` | | files + summaries, bundles (+log, pending, submit cmds), rework count |
| GET | `/api/file?slug=` | | cases (+CSV cols, last/precheck result, evidence), drafts, patch, history |
| GET | `/api/text?slug=&kind=source\|log&path=` | | agent-branch source / a log under the file dir |
| POST | `/api/set_review` | `{slug, case_ids, value?, comment?}` | verdict and/or comment (independent) |
| POST | `/api/set_file_prompt` | `{slug, prompt}` | common prompt (+ notes log) |
| POST | `/api/set_case_fields` | `{slug, case_ids, field, value}` | field in portability/reason/issue/fix_pr |
| POST | `/api/save_patch` | `{slug, text}` | rebuild agent branch as base + edited diff (one commit); UI: Edit -> Apply (confirm) |
| POST | `/api/submit_issue` | `{slug, id, title, body, labels}` | gh issue create + backfill |
| POST | `/api/backfill_issue` | `{slug, id, issue}` | backfill a manually filed issue |
| POST | `/api/match_issue` | `{title}` | find an existing issue by title |
| POST | `/api/reject_draft` | `{slug, id}` | cases -> Needs work, revert placeholder lines |
| POST | `/api/export_csv` | `{}` | write `<input>_reviewed_<ts>.csv` |
| POST | `/api/pr/preflight\|create\|update\|refresh\|delete\|remove_file\|save_description\|set_number` | see pr_bundle.py | PR bundles |
| POST | `/api/pr/file_save` | `{slug, title, text}` | per-file PR title/description (updates the link) |
| POST | `/api/pr/file_regen` | `{slug}` | drop edited description, rebuild from current verdicts |
| POST | `/api/pr/file_submit` | `{slug, title, text}` | push agent branch to fork; returns `open_url` (prefilled PR page, or the existing PR) |
| POST | `/api/pr/submit` | `{id}` | same for a multi-file bundle |
| POST | `/api/pr/file_link` / `link` | `{slug}` / `{id}` | find the PR the human created from the branch and fill fix_pr |
| POST | `/api/pr/file_set_number` | `{slug, ref, confirm_head}` | per-file PR number -> fix_pr on its cases |

Per-file PR links: `/api/data` files carry `pr_link`; `/api/file` carries
`pr = {bundle, review_url, compare_url, pr_page, push, gh, fork_repo, body_file, in_multi, push_state}`
(`push_state = {pushed, remote_sha, local_sha, in_sync}` from `git ls-remote`, 60 s cache);
files also carry `review_link`. Review link (share before the upstream PR):
`https://github.com/<fork_repo>/compare/<base_sha12>...<branch>`. The link is
`https://github.com/pytorch/pytorch/compare/main...<fork_owner>:<agent_branch>?expand=1&title=..&body=..`
(body shortened when the URL would exceed ~7.5k chars). A per-file PR is stored as
a bundle with `"kind": "single"` and `id = pr_<slug>`, whose target is the agent
branch itself, once its PR number is set.

## Evidence rules (verify_evidence.py)

See the module docstring. Summary:

| rule | applies to | requirement |
|---|---|---|
| R1 | Portable | PASSED at the current patch |
| R2-R9 | Known Issue / Feature gap / Not Applicable | an issue or draft; the issue is not stale; a gate-removed FAILED run; the log contains the case and its error; only gate lines changed after measuring; the final gate msg is the issue URL, or `onlyCUDA` for cuda_only; the draft contains the error and the repro |
| R10 | all | non-empty reason (WARN) |
