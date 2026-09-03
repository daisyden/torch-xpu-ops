#!/usr/bin/env bash
# Build the XPU upstream test-file status report end to end.
#
# Pipeline:
#   1. extract_owned.py   xlsx  -> /tmp/owned.json      (owned rows + PR list)
#   2. fetch_prs.py       gh    -> pr_cache/*.json      (PR metadata)
#   2b.discover_prs.py    gh    -> xlsx (optional)      (new owner PRs -> PR column)
#   3. mark_done.py       gh    -> xlsx (optional)      (merged PRs -> Status=Done)
#   4. analyze.py         cache -> /tmp/pr_analysis.json(per-PR gate + timing)
#   4b.fetch_refactor_tracker.py -> /tmp/refactor_tracker.json (community refactor PRs)
#   5. gen_report.py            -> report.html          (interactive report)
#
# Usage:
#   ./build_report.sh              # incremental: fetch only missing PRs
#   ./build_report.sh --pull-xlsx  # first pull latest xlsx from SharePoint (rclone)
#   ./build_report.sh --refresh    # re-fetch every PR (realtime state)
#   ./build_report.sh --discover   # also find new owner PRs not yet in the xlsx
#   ./build_report.sh --mark-done  # realtime check + mark merged files Done in xlsx
#   ./build_report.sh --pull-xlsx --refresh --discover --mark-done
set -euo pipefail
cd "$(dirname "$0")"

REFRESH=""
MARK_DONE=0
REFAC_COLS=0
DISCOVER=0
PULL_XLSX=0
for a in "$@"; do
  case "$a" in
    --refresh)   REFRESH="--refresh" ;;
    --mark-done) MARK_DONE=1 ;;
    --refac-cols) REFAC_COLS=1 ;;
    --discover)  DISCOVER=1 ;;
    --pull-xlsx) PULL_XLSX=1 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

if [ "$PULL_XLSX" -eq 1 ]; then
  echo "== 0/5 pull latest xlsx from SharePoint =="
  ./pull_xlsx.sh
fi

echo "== 1/5 extract owned files from xlsx =="
python3 extract_owned.py

if [ "$MARK_DONE" -eq 1 ]; then
  echo "== 2/5 realtime PR check + mark merged files Done =="
  python3 mark_done.py           # fetches all PRs, edits xlsx, backs up first
  echo "== re-extract after xlsx update =="
  python3 extract_owned.py
else
  echo "== 2/5 fetch PR metadata =="
  python3 fetch_prs.py $REFRESH
fi

if [ "$DISCOVER" -eq 1 ]; then
  echo "== 2b/5 discover new owner PRs and add to xlsx =="
  # needs the PR cache populated above (derives authors + cutoff from it)
  python3 discover_prs.py --apply
  echo "== re-extract after discovery =="
  python3 extract_owned.py
  echo "== fetch newly discovered PRs =="
  python3 fetch_prs.py $REFRESH
fi

echo "== 3/5 (skipped unless --mark-done) =="

echo "== 4/5 analyze PRs (gates + timing) =="
python3 analyze.py

echo "== 4b/5 fetch community refactor tracker (Google Sheet) =="
python3 fetch_refactor_tracker.py || echo "  (tracker fetch failed; report will omit refactor columns)"

if [ "$REFAC_COLS" -eq 1 ]; then
  echo "== 4c/5 write refactor cols (T/U/V) into xlsx for To Do files =="
  python3 write_refactor_cols.py --apply
  echo "== re-extract after xlsx update =="
  python3 extract_owned.py
fi

echo "== 5/5 generate report.html =="
python3 gen_report.py

echo "done -> $(pwd)/report.html"
