# enable-xpu-tests report tools

All HTML/report generation and issue-submission logic lives here so the skill
body stays prose-only. Three scripts:

| script | role |
|---|---|
| `report_gen.py` | Build the static report tree (`index.html`, `input_table.html`, per-file `report.html`) from the CSV + manifest, and optionally a JUnit XML. |
| `review_server.py` | Serve the report tree and file approved draft issues via `gh`, backfilling the real number into the patch, source and CSV. |
| `merge_review.py` | Merge the reviewer's exported JSON (verdicts + Fix PRs) back into the CSV. |

## Output layout

```
xpu_enable_report/<branch>/
  index.html            test files -> report pages, status, portability breakdown
  input_table.html      every case (full CSV row) + review controls
  logs/<file>.log       per-file test logs (step 3)
  <test_file>/
      report.html       commit/patch/log links, portability summary,
                        per-case detail table, drafted issues + Submit button
      <file>.patch      the file's commit patch
```

## manifest.json schema

```json
{
  "branch": "missing_eager",
  "files": [
    {
      "test_file": "test_shape_ops.py",
      "commit": "7d60338c8ac",
      "patch": "xpu_enable_report/missing_eager/test_shape_ops.py/test_shape_ops.py.patch",
      "log":   "xpu_enable_report/missing_eager/logs/test_shape_ops.py.log",
      "source": "test/test_shape_ops.py",
      "status": "Enabled",
      "issue_drafts": [
        {
          "title": "[wontfix] test_foo uses a deprecated CUDA-only API",
          "body": "### \ud83d\udc1b Describe the bug\n...\n### Error log / message\n```\n...\n```\n### Versions\n...\n\n_Drafted with AI assistance._",
          "labels": ["wontfix"],
          "cases": ["7688", "7689"],
          "verdict": "Not Applicable"
        }
      ]
    }
  ]
}
```

- `test_file` is required; everything else is optional.
- `issue_drafts` renders editably in `report.html`. After `review_server.py`
  files a draft, it writes `number` and `url` back into the draft entry and
  disables the button, so re-running `report_gen.py` shows it as filed.
- Use `.../issues/PLACEHOLDER` in any skip/comment/CSV reason for a draft; the
  server replaces `PLACEHOLDER` with the real number on submit.

## report_gen.py

```
python3 report_gen.py --csv BATCH.csv --out xpu_enable_report \
  --branch <branch> --manifest manifest.json \
  [--batch NAME] [--files-with-manifest-only] \
  [--case-base-url https://<case-host>] [--src-root test] \
  [--junit <branch>_cases.xml]
```

- Output dir is `--out/<branch>/`. `--batch` only namespaces the browser
  `localStorage` review keys; it defaults to the branch.
- `--files-with-manifest-only` restricts report pages to manifest files; omit it
  to emit a page per CSV file (no patch/commit for unlisted files).
- `--case-base-url` is prepended to each row's `detail_url` (usually a relative
  SPA route) so case links open the real tracker page.
- `--junit` writes a branch-scoped JUnit XML from the CSV verdicts:
  `Portable`/`PASS`/empty -> passing; everything else -> `<skipped>` with
  `message="<verdict>: <reason>"` and `<properties>` for `case_id`,
  `portability`, and `issue`. The file is validated well-formed.

## review_server.py

```
python3 review_server.py --root xpu_enable_report/<branch> \
  --csv BATCH.csv --manifest manifest.json \
  [--repo intel/torch-xpu-ops] [--src-root test] [--port 8000] [--dry-run]
```

Serves the report and handles the Submit button (`POST /api/submit_issue`).
`--dry-run` does everything except the actual `gh issue create`. Requires an
authenticated `gh`. Review verdicts live in the browser's `localStorage`; the
reviewer exports them with **Export review JSON**.

## merge_review.py

```
python3 merge_review.py --csv BATCH.csv --review review_<branch>.json [--out BATCH.csv]
```

Writes `review_status`, `review_note`, and `fix_pr` columns into the CSV from the
exported JSON. Only this script writes review columns back; `report_gen.py` is
read-only w.r.t. the CSV.
