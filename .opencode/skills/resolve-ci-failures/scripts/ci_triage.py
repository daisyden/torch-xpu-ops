#!/usr/bin/env python3
"""Collect and classify CI failures for a pytorch/pytorch PR.

Reads PR metadata, the Dr. CI (pytorch-bot) comment and the head-only check
list, classifies each failing job, and emits a Markdown report plus JSON.

Read-only: never writes to GitHub.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys


LINT_RE = re.compile(r"\blint", re.IGNORECASE)
XPU_RE = re.compile(r"\b(xpu|intel)\b", re.IGNORECASE)
TIMEOUT_RE = re.compile(
    r"timed? ?out|timeout|exceeded the maximum execution time|cancel", re.IGNORECASE
)
FAIL_BUCKETS = {"fail", "cancel"}


def gh(args: list[str], repo: str | None = None) -> str:
    cmd = ["gh", *args]
    if repo:
        cmd += ["--repo", repo]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"warning: {' '.join(cmd)} failed: {proc.stderr.strip()}", file=sys.stderr)
        return ""
    return proc.stdout


def gh_json(args: list[str], repo: str | None = None, default=None):
    out = gh(args, repo)
    if not out.strip():
        return default
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return default


def pr_metadata(pr: str, repo: str) -> dict:
    fields = (
        "number,title,url,author,isDraft,labels,mergeable,mergeStateStatus,"
        "headRefName,headRefOid,headRepositoryOwner,headRepository,baseRefName"
    )
    return gh_json(["pr", "view", pr, "--json", fields], repo, default={}) or {}


def dr_ci_comment(pr: str, repo: str) -> str:
    out = gh(
        [
            "api",
            f"repos/{repo}/issues/{pr}/comments",
            "--jq",
            '[.[] | select(.user.login == "pytorch-bot[bot]")][0].body',
        ]
    )
    return out.strip()


BUCKET_HEADING = re.compile(
    r"<summary[^>]*>.{0,200}?<b>\s*(NEW FAILURES?|BROKEN TRUNK|FLAKY|UNSTABLE)\s*</b>",
    re.IGNORECASE | re.DOTALL,
)
# Dr. CI job line: "* [Job / name](hud-link) ([gh](actions-link)) ..."
JOB_LINE = re.compile(r"^\s*\*\s*\[([^\]]+)\]\((https?://[^)]+)\)(.*)$")
GH_JOB_LINK = re.compile(r"/actions/runs/(\d+)/job/(\d+)")
AI_VERDICT = re.compile(r"AI verdict:\s*(related|unrelated)", re.IGNORECASE)
TRUNK_FAILURE = re.compile(r"\[trunk failure\]\((https?://[^)]+)\)")


def parse_dr_ci_buckets(body: str) -> dict[str, dict]:
    """Map job name -> {bucket, hint, ai_verdict, run_id, job_id, trunk_url}.

    Dr. CI renders each bucket as a <details> block whose <summary> contains
    e.g. "<b>NEW FAILURE</b> - The following job has failed:". Job names are
    bullet list items underneath, followed by an indented backticked hint
    (the failing test or lint line) and sometimes an "AI verdict: related" badge.
    """
    out: dict[str, dict] = {}
    current: str | None = None
    last: str | None = None

    for line in body.splitlines():
        m = BUCKET_HEADING.search(line)
        if m:
            label = m.group(1).upper()
            if label.startswith("NEW FAILURE"):
                current = "new"
            elif "BROKEN TRUNK" in label:
                current = "broken_trunk"
            elif "FLAKY" in label:
                current = "flaky"
            else:
                current = "unstable"
            last = None
            # A summary line can also carry the first job on the same line.
        if "</details>" in line:
            current = None
            last = None
        if not current:
            continue

        jm = JOB_LINE.match(line)
        if jm:
            name = jm.group(1).strip()
            tail = jm.group(3) or ""
            entry: dict = {"bucket": current, "hint": "", "ai_verdict": None}
            gm = GH_JOB_LINK.search(tail)
            if gm:
                entry["run_id"], entry["job_id"] = gm.group(1), gm.group(2)
            tf = TRUNK_FAILURE.search(tail)
            if tf:
                entry["trunk_url"] = tf.group(1)
            out[name] = entry
            last = name
            continue

        if last:
            if not out[last]["hint"]:
                hint = re.search(r"`([^`]+)`", line)
                if hint:
                    out[last]["hint"] = hint.group(1).strip()
            av = AI_VERDICT.search(line)
            if av and not out[last]["ai_verdict"]:
                out[last]["ai_verdict"] = av.group(1).lower()
    return out


def failed_checks(pr: str, repo: str) -> tuple[str, list[dict]]:
    """All failing head-sha checks, via the paged GraphQL helper.

    Do NOT switch this to `check-runs?per_page=100`: it truncates on PyTorch
    PRs and makes a red PR look green.
    """
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_checks.py")
    proc = subprocess.run(
        [sys.executable, helper, "--pr", pr, "--repo", repo, "--failed-only"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(f"error: fetch_checks.py failed: {proc.stderr.strip()}", file=sys.stderr)
        return "", []
    data = json.loads(proc.stdout)
    return data.get("head_sha", ""), data.get("checks", [])


def changed_files(pr: str, repo: str) -> list[str]:
    out = gh(["pr", "diff", pr, "--name-only"], repo)
    return [line.strip() for line in out.splitlines() if line.strip()]


def dr_ci_entry(check: dict, dr_ci: dict[str, dict]) -> dict:
    """Match a check-run against its Dr. CI bullet.

    Job id is the only exact key: Dr. CI renders names as "workflow / job",
    which is close but not always identical to the GraphQL name.
    """
    jid = job_id(check.get("link", ""))
    if jid:
        for entry in dr_ci.values():
            if entry.get("job_id") == jid:
                return entry
    name = check.get("name") or ""
    workflow = check.get("workflow") or ""
    qualified = f"{workflow} / {name}" if workflow else name
    for key in (qualified, name):
        if key in dr_ci:
            return dr_ci[key]
    for key, entry in dr_ci.items():
        if key in qualified or qualified in key:
            return entry
    return {}


def looks_related(name: str, hint: str, files: list[str]) -> bool:
    """Weak heuristic: do the job name / failing-test hint overlap the PR diff?"""
    stems = set()
    for f in files:
        base = f.rsplit("/", 1)[-1]
        stems.add(base.rsplit(".", 1)[0].lower())
    haystack = f"{name} {hint}".lower()
    return any(s and len(s) > 4 and s in haystack for s in stems)


def classify(check: dict, dr_ci: dict[str, dict], files: list[str]) -> tuple[str, str]:
    name = check.get("name") or ""
    workflow = check.get("workflow") or ""
    full = f"{workflow} {name}"
    entry = dr_ci_entry(check, dr_ci)
    bucket = entry.get("bucket")
    hint = entry.get("hint") or ""
    verdict = entry.get("ai_verdict")
    cancelled = (check.get("bucket") or "").lower() == "cancel"

    if LINT_RE.search(full):
        return "lint", f"lint job{f'; hint: {hint}' if hint else ''}"
    if bucket == "flaky":
        return "flaky", "Dr. CI FLAKY"
    if bucket == "unstable":
        return "unstable", "Dr. CI UNSTABLE"
    if XPU_RE.search(full):
        if cancelled or TIMEOUT_RE.search(f"{full} {hint}"):
            return "xpu_timeout", f"xpu job, timeout/cancel signal{f': {hint}' if hint else ''}"
        return "xpu_check_log", f"xpu job; read log to confirm timeout vs real failure{f'; hint: {hint}' if hint else ''}"
    if bucket == "broken_trunk":
        ev = "Dr. CI BROKEN TRUNK"
        if entry.get("trunk_url"):
            ev += " (trunk failure linked)"
        if verdict == "related":
            return "broken_trunk_related", ev + "; AI verdict: related"
        return "broken_trunk", ev
    if bucket == "new":
        if verdict == "related":
            return "new_related", f"NEW FAILURE; Dr. CI AI verdict: related{f'; {hint}' if hint else ''}"
        if verdict == "unrelated":
            return "new_unrelated", f"NEW FAILURE; Dr. CI AI verdict: unrelated{f'; {hint}' if hint else ''}"
        if looks_related(full, hint, files):
            return "new_related", f"NEW FAILURE; name/hint overlaps PR diff{f': {hint}' if hint else ''}"
        return "new_unrelated", f"NEW FAILURE; no overlap with PR diff - VERIFY LOG{f'; {hint}' if hint else ''}"
    return "unknown", "not in any Dr. CI bucket; read the log"



ACTIONS = {
    "lint": "spin fixlint / manual fix -> review -> commit -> rebase viable/strict",
    "xpu_timeout": "rerun ALL failed jobs (rerun_failed.py), then re-triage",
    "xpu_check_log": "read log: timeout -> rerun all failed; real failure -> triage as new_*",
    "broken_trunk": "ignore (rebase onto viable/strict clears it)",
    "broken_trunk_related": "related to PR -> rerun ALL failed jobs, then re-triage",
    "flaky": "ignore",
    "unstable": "ignore (non-blocking)",
    "new_related": "reproduce locally, root-cause, fix -> review -> commit -> rebase",
    "new_unrelated": "no code change; rebase onto viable/strict",
    "unknown": "read the failure log, then classify",
}


def job_id(link: str) -> str | None:
    m = re.search(r"/job/(\d+)", link or "")
    return m.group(1) if m else None


def render(meta: dict, rows: list[dict], dr_ci_sha: str | None) -> str:
    head_owner = (meta.get("headRepositoryOwner") or {}).get("login", "?")
    head_repo = (meta.get("headRepository") or {}).get("name", "?")
    lines = [
        f"# CI triage for PR #{meta.get('number')} - {meta.get('title', '')}",
        "",
        f"- URL: {meta.get('url')}",
        f"- Author: {(meta.get('author') or {}).get('login')}",
        f"- Head: `{head_owner}/{head_repo}:{meta.get('headRefName')}` @ `{(meta.get('headRefOid') or '')[:12]}`",
        f"- Base: `{meta.get('baseRefName')}` | draft: {meta.get('isDraft')} | mergeable: {meta.get('mergeable')} ({meta.get('mergeStateStatus')})",
        f"- Labels: {', '.join(l['name'] for l in meta.get('labels') or []) or '(none)'}",
    ]
    if dr_ci_sha:
        lines.append(f"- Dr. CI comment as-of sha: `{dr_ci_sha}`")
    lines += [
        "",
        f"## Failing jobs ({len(rows)})",
        "",
        "| Job | Workflow | Dr. CI bucket | Class | Evidence | Action | Log |",
        "|---|---|---|---|---|---|---|",
    ]
    if not rows:
        lines.append("| _none_ | | | | | | |")
    for r in rows:
        jid = job_id(r.get("link", ""))
        log = f"`gh run view --log-failed --job {jid}`" if jid else r.get("link", "")
        lines.append(
            "| {name} | {wf} | {bucket} | **{cls}** | {ev} | {act} | {log} |".format(
                name=r.get("name", ""),
                wf=r.get("workflow", ""),
                bucket=r.get("dr_ci_bucket") or "-",
                cls=r["cls"],
                ev=r["evidence"],
                act=ACTIONS.get(r["cls"], ""),
                log=log,
            )
        )
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["cls"]] = counts.get(r["cls"], 0) + 1
    lines += ["", "## Summary", ""]
    for cls, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"- `{cls}`: {n} -> {ACTIONS.get(cls, '')}")
    lines += [
        "",
        "## Next",
        "",
        "Verify every classification against the real log before acting.",
        "Fixes and rebases require explicit human approval (see SKILL.md).",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pr", required=True, help="PR number or URL")
    ap.add_argument("--repo", default="pytorch/pytorch")
    ap.add_argument("--out", help="Markdown report path (default: stdout)")
    ap.add_argument("--json-out", help="Dump classified rows as JSON")
    args = ap.parse_args()

    pr = args.pr.rstrip("/").rsplit("/", 1)[-1]

    meta = pr_metadata(pr, args.repo)
    if not meta:
        print(f"error: could not read PR {pr} from {args.repo}", file=sys.stderr)
        return 1

    body = dr_ci_comment(pr, args.repo)
    dr_ci = parse_dr_ci_buckets(body)
    sha_m = re.search(r"commit\s+([0-9a-f]{7,40})", body)
    dr_ci_sha = sha_m.group(1) if sha_m else None

    files = changed_files(pr, args.repo)
    checks_head_sha, checks = failed_checks(pr, args.repo)
    rows = []
    for check in checks:
        cls, evidence = classify(check, dr_ci, files)
        entry = dr_ci_entry(check, dr_ci)
        rows.append(
            {
                **check,
                "dr_ci_bucket": entry.get("bucket"),
                "dr_ci_hint": entry.get("hint"),
                "dr_ci_ai_verdict": entry.get("ai_verdict"),
                "trunk_url": entry.get("trunk_url"),
                "run_id": entry.get("run_id"),
                "cls": cls,
                "evidence": evidence,
                "job_id": job_id(check.get("link", "")),
            }
        )
    rows.sort(key=lambda r: (r["cls"], r.get("workflow") or "", r.get("name") or ""))

    report = render(meta, rows, dr_ci_sha)
    head = meta.get("headRefOid") or checks_head_sha
    if dr_ci_sha and head and not head.startswith(dr_ci_sha):
        print(
            f"WARNING: Dr. CI comment is for {dr_ci_sha[:12]} but head is "
            f"{head[:12]}; bucket classifications may be stale. Wait for the "
            "bot to refresh before acting.",
            file=sys.stderr,
        )
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(report)
        print(f"wrote {args.out}")
    else:
        print(report)

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(
                {
                    "pr": meta.get("number"),
                    "repo": args.repo,
                    "head_sha": meta.get("headRefOid"),
                    "head_branch": meta.get("headRefName"),
                    "head_owner": (meta.get("headRepositoryOwner") or {}).get("login"),
                    "dr_ci_sha": dr_ci_sha,
                    "changed_files": files,
                    "failures": rows,
                },
                fh,
                indent=2,
            )
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
