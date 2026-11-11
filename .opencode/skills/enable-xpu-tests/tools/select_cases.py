#!/usr/bin/env python3
"""List the cases of an input CSV that still need enabling.

A row is selected when:
  - its `fix_pr` is empty, and
  - its case_id is not already recorded in any <slug>/cases.json of the report tree, and
  - its file is not in a submitted/merged PR bundle (unless --include-pr-files).

  select_cases.py --csv NEW.csv [--filter inductor] [--file test_fp8.py] [--json]
"""
import argparse
import json
import sys

import store
from common import REPORT, slug as make_slug


def bundled_slugs(root: str) -> set[str]:
    out = set()
    for b in store.list_bundles(root):
        if b.get("status") in ("submitted", "merged"):
            out.update(f["slug"] for f in b.get("files", []) if f.get("state") == "picked")
    return out


def select(rows: list[dict], root: str = REPORT, filt: str = "", files=None,
           include_pr_files: bool = False) -> dict[str, list[dict]]:
    done = store.all_case_ids(root)
    # a bundle that lists explicit cases (an external PR covering part of a file) does
    # not block the file's other cases
    skip_files = set() if include_pr_files else {
        f["slug"] for b in store.list_bundles(root) if b.get("status") in ("submitted", "merged")
        for f in b.get("files", []) if f.get("state") == "picked" and not f.get("cases")}
    out: dict[str, list[dict]] = {}
    for r in rows:
        tf = (r.get("test_file") or "").strip()
        if not tf or (r.get("fix_pr") or "").strip():
            continue
        if filt and filt not in tf:
            continue
        if files and tf not in files and tf.rsplit("/", 1)[-1] not in files:
            continue
        if str(r.get("case_id")) in done or make_slug(tf) in skip_files:
            continue
        out.setdefault(tf, []).append(r)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--root", default=REPORT)
    ap.add_argument("--filter", default="", help="substring of test_file")
    ap.add_argument("--file", action="append", help="exact test_file (repeatable)")
    ap.add_argument("--include-pr-files", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    sel = select(store.read_csv(a.csv), a.root, a.filter, a.file, a.include_pr_files)
    if a.json:
        json.dump({tf: [r["case_id"] for r in rs] for tf, rs in sel.items()}, sys.stdout, indent=2)
        print()
        return
    total = 0
    for tf, rs in sel.items():
        total += len(rs)
        print(f"{tf}  ({len(rs)} cases)")
        for r in rs:
            print(f"    {r['case_id']}\t{r.get('class_name', '')}::{r.get('test_name', '')}"
                  f"\txpu={r.get('xpu_status', '')}\tissue={r.get('issue_number', '')}")
    print(f"\n{len(sel)} files, {total} cases to enable")


if __name__ == "__main__":
    main()
