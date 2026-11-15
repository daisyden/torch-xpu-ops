#!/usr/bin/env python3
"""Dump OPEN intel/torch-xpu-ops issues to JSON for name matching.

    python fetch_open_issues.py --out /tmp/opencode/xpu_ops_open_issues.json

Produces a list of {number, title}. Used by skipped_report.py --open-issues to
attribute issueless skipped cases to a tracked open issue by test name.
"""
import argparse
import json
import subprocess


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/opencode/xpu_ops_open_issues.json")
    ap.add_argument("--limit", type=int, default=5000)
    a = ap.parse_args()
    out = subprocess.run(
        ["gh", "issue", "list", "--repo", "intel/torch-xpu-ops",
         "--state", "open", "--limit", str(a.limit),
         "--json", "number,title"],
        capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise SystemExit(out.stderr)
    data = json.loads(out.stdout)
    with open(a.out, "w") as fh:
        json.dump(data, fh)
    print(f"wrote {a.out}: {len(data)} open issues")


if __name__ == "__main__":
    main()
