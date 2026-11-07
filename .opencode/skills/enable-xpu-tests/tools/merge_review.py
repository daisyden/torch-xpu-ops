#!/usr/bin/env python3
"""Merge exported review JSON (from the HTML report) back into the batch CSV.

The HTML report stores reviewer verdicts in the browser and exports them via
"Export review JSON" as:

  {"batch": "NAME", "reviews": {"<case_id>": {"status": "Agree",
                                              "note": "...", "ts": "..."}}}

This script writes into the CSV (columns created if absent):
  review_status  - the reviewer verdict (Agree / Disagree / Needs info)
  review_note    - the reviewer note
  fix_pr         - the Fix PR the owner entered on the index page (per test_file,
                   from the exported "fix_prs" map)

Usage:
  merge_review.py --csv BATCH.csv --review review_NAME.json [--out BATCH.csv]
"""
from __future__ import annotations

import argparse
import csv
import json


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--review", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    data = json.load(open(args.review))
    reviews = data.get("reviews", data)
    fix_prs = data.get("fix_prs", {})

    with open(args.csv, newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = list(reader.fieldnames or [])

    for col in ("review_status", "review_note", "fix_pr"):
        if col not in fields:
            fields.append(col)

    n = 0
    for r in rows:
        rv = reviews.get(r.get("case_id", ""))
        if rv:
            r["review_status"] = rv.get("status", "")
            r["review_note"] = rv.get("note", "")
            n += 1

    p = 0
    for r in rows:
        pr = fix_prs.get(r.get("test_file", ""))
        if pr:
            r["fix_pr"] = pr
            p += 1

    out = args.out or args.csv
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"Merged {n} review verdict(s) and {p} fix-PR cell(s) into {out}")


if __name__ == "__main__":
    main()
