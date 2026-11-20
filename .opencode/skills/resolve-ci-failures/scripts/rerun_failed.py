#!/usr/bin/env python3
"""Rerun all failed jobs in a pytorch/pytorch PR's Checks tab.

This is a GitHub WRITE action. Always run with --dry-run first and get explicit
human approval before running for real (see AI_POLICY.md).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys


FAIL_BUCKETS = {"fail", "cancel"}


def gh(args: list[str], repo: str | None = None) -> tuple[int, str, str]:
    cmd = ["gh", *args]
    if repo:
        cmd += ["--repo", repo]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def failed_checks(pr: str, repo: str) -> list[dict]:
    """Failed head-sha checks via the paged GraphQL helper.

    Not `gh pr checks --json` (absent on older gh) and not
    `check-runs?per_page=100` (truncates on PyTorch PRs).
    """
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_checks.py")
    proc = subprocess.run(
        [sys.executable, helper, "--pr", pr, "--repo", repo, "--failed-only"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(f"error: fetch_checks.py failed: {proc.stderr.strip()}", file=sys.stderr)
        return []
    return json.loads(proc.stdout).get("checks", [])


def run_ids(checks: list[dict]) -> dict[str, list[str]]:
    """Map workflow run id -> job names that failed in it."""
    runs: dict[str, list[str]] = {}
    for c in checks:
        m = re.search(r"/actions/runs/(\d+)", c.get("link") or "")
        if not m:
            continue
        runs.setdefault(m.group(1), []).append(c.get("name") or "?")
    return runs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pr", required=True, help="PR number or URL")
    ap.add_argument("--repo", default="pytorch/pytorch")
    ap.add_argument(
        "--dry-run", action="store_true", help="print the gh commands, change nothing"
    )
    args = ap.parse_args()

    pr = args.pr.rstrip("/").rsplit("/", 1)[-1]
    checks = failed_checks(pr, args.repo)
    if not checks:
        print("No failed checks found on the current head sha. Nothing to rerun.")
        return 0

    runs = run_ids(checks)
    print(f"{len(checks)} failed job(s) across {len(runs)} workflow run(s):")
    for rid, names in sorted(runs.items()):
        print(f"  run {rid}: {len(names)} failed job(s)")
        for n in sorted(names):
            print(f"    - {n}")

    cmds = [
        ["run", "rerun", rid, "--failed", "--repo", args.repo] for rid in sorted(runs)
    ]
    print("\nCommands:")
    for c in cmds:
        print("  gh " + " ".join(c))

    if args.dry_run:
        print("\n(dry run; nothing executed)")
        return 0

    failures = 0
    for c in cmds:
        rc, out, err = gh(c)
        tag = "ok" if rc == 0 else f"FAILED rc={rc}"
        print(f"\ngh {' '.join(c)} -> {tag}")
        if out.strip():
            print(out.strip())
        if rc != 0:
            failures += 1
            print(err.strip())
            if "403" in err or "not authorized" in err.lower():
                print(
                    "Hint: you lack write access to rerun jobs on this repo. "
                    'Ask a maintainer, or use the "Re-run failed jobs" button in '
                    "the GitHub UI.",
                    file=sys.stderr,
                )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
