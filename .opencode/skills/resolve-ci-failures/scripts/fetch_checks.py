#!/usr/bin/env python3
"""Fetch ALL head-sha check results for a PR via paged GraphQL.

`gh pr checks --json` only exists in newer gh, and a single
`check-runs?per_page=100` REST call silently truncates on PyTorch PRs (which
have hundreds of check runs) - making a red PR look green. This pages the
statusCheckRollup contexts properly.

Emits a JSON list of {name, workflow, status, conclusion, bucket, link,
completedAt} to stdout.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys


QUERY = """
query($owner:String!, $name:String!, $pr:Int!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$pr) {
      headRefOid
      commits(last:1) {
        nodes {
          commit {
            statusCheckRollup {
              state
              contexts(first:100, after:$cursor) {
                pageInfo { hasNextPage endCursor }
                nodes {
                  __typename
                  ... on CheckRun {
                    name
                    status
                    conclusion
                    detailsUrl
                    completedAt
                    checkSuite { workflowRun { workflow { name } } }
                  }
                  ... on StatusContext {
                    context
                    state
                    targetUrl
                    createdAt
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

# conclusion/state -> bucket, mirroring `gh pr checks` buckets
BUCKET = {
    "SUCCESS": "pass",
    "NEUTRAL": "skipping",
    "SKIPPED": "skipping",
    "FAILURE": "fail",
    "TIMED_OUT": "fail",
    "STARTUP_FAILURE": "fail",
    "ACTION_REQUIRED": "fail",
    "STALE": "fail",
    "ERROR": "fail",
    "CANCELLED": "cancel",
    "PENDING": "pending",
    "EXPECTED": "pending",
    "QUEUED": "pending",
    "IN_PROGRESS": "pending",
    "WAITING": "pending",
    "REQUESTED": "pending",
}


def graphql(owner: str, name: str, pr: int, cursor: str | None) -> dict:
    cmd = [
        "gh", "api", "graphql",
        "-f", f"query={QUERY}",
        "-F", f"owner={owner}",
        "-F", f"name={name}",
        "-F", f"pr={pr}",
    ]
    if cursor:
        cmd += ["-F", f"cursor={cursor}"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"error: graphql failed: {proc.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return json.loads(proc.stdout)


def normalize(node: dict) -> dict:
    if node.get("__typename") == "CheckRun":
        suite = node.get("checkSuite") or {}
        run = suite.get("workflowRun") or {}
        wf = (run.get("workflow") or {}).get("name") or ""
        raw = node.get("conclusion") or node.get("status") or "PENDING"
        return {
            "name": node.get("name") or "",
            "workflow": wf,
            "status": node.get("status"),
            "conclusion": node.get("conclusion"),
            "bucket": BUCKET.get(raw.upper(), "pending"),
            "link": node.get("detailsUrl") or "",
            "completedAt": node.get("completedAt"),
        }
    raw = (node.get("state") or "PENDING").upper()
    return {
        "name": node.get("context") or "",
        "workflow": "",
        "status": "COMPLETED" if raw not in ("PENDING", "EXPECTED") else "PENDING",
        "conclusion": raw,
        "bucket": BUCKET.get(raw, "pending"),
        "link": node.get("targetUrl") or "",
        "completedAt": node.get("createdAt"),
    }


def fetch(repo: str, pr: int) -> tuple[str, list[dict]]:
    owner, name = repo.split("/", 1)
    rows: list[dict] = []
    cursor = None
    head = ""
    while True:
        data = graphql(owner, name, pr, cursor)
        pull = (((data.get("data") or {}).get("repository") or {}).get("pullRequest")) or {}
        head = pull.get("headRefOid") or head
        nodes = (pull.get("commits") or {}).get("nodes") or []
        if not nodes:
            break
        rollup = (nodes[0].get("commit") or {}).get("statusCheckRollup")
        if not rollup:
            break
        ctx = rollup.get("contexts") or {}
        rows += [normalize(n) for n in ctx.get("nodes") or []]
        page = ctx.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return head, rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pr", required=True)
    ap.add_argument("--repo", default="pytorch/pytorch")
    ap.add_argument("--failed-only", action="store_true")
    args = ap.parse_args()

    pr = int(args.pr.rstrip("/").rsplit("/", 1)[-1])
    head, rows = fetch(args.repo, pr)
    if args.failed_only:
        rows = [r for r in rows if r["bucket"] in ("fail", "cancel")]
    json.dump({"head_sha": head, "checks": rows}, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
