# Copyright 2020-2025 Intel Corporation
# Licensed under the Apache License, Version 2.0

"""
Collect open action requests from a GitHub issue.

Handles steps 1 (closed check), 2 (label check), and 4 (linked PR analysis)
deterministically via `gh` CLI. Step 3 (parsing actionable requests from
comments) requires LLM judgment and is NOT handled here -- the script outputs
the raw comments for the calling agent to parse.

Usage:
    python3 collect_opens.py <issue_number_or_url> [--repo owner/name] [--output PATH]
"""

import argparse
import json
import re
import subprocess
import sys


def run_gh(args: list[str], check: bool = True) -> str:
    """Run a gh CLI command and return stdout."""
    result = subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def parse_issue_ref(ref: str, default_repo: str) -> tuple[str, int]:
    """Parse issue reference into (repo, number)."""
    # Full URL: https://github.com/owner/repo/issues/123
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/issues/(\d+)", ref)
    if m:
        return m.group(1), int(m.group(2))
    # Bare number
    if ref.isdigit():
        return default_repo, int(ref)
    raise ValueError(f"Cannot parse issue reference: {ref}")


def step1_check_closed(repo: str, issue_id: int) -> dict | None:
    """Step 1: If issue is closed, AR=N/A."""
    data = json.loads(
        run_gh(["issue", "view", str(issue_id), "--repo", repo,
                "--json", "state,title,url,labels"])
    )
    state = data.get("state", "").upper()
    if state == "CLOSED":
        return {
            "AR": "N/A",
            "AR_REASON": "Issue is closed",
        }
    return None


def step2_check_labels(labels: list[dict]) -> dict | None:
    """Step 2: If labels contain not_target or wontfix, AR=ADD_SKIPLIST."""
    label_names = [l.get("name", "").lower() for l in labels]
    for target in ("not_target", "wontfix"):
        if target in label_names:
            return {
                "AR": "ADD_SKIPLIST",
                "AR_REASON": f"Issue labeled {target}",
            }
    return None


def find_linked_prs(repo: str, issue_id: int, body: str, comments: list[dict]) -> list[int]:
    """Find PR numbers linked to this issue from body, comments, and timeline."""
    pr_numbers: set[int] = set()

    # Search body and comments for PR references
    all_text = body + "\n" + "\n".join(c.get("body", "") for c in comments)

    # Pattern: #123, owner/repo#123, full PR URL
    for m in re.finditer(r"(?:https?://github\.com/[^/]+/[^/]+/pull/(\d+))", all_text):
        pr_numbers.add(int(m.group(1)))

    # "Fixes #N", "Closes #N" style references (same repo)
    for m in re.finditer(r"(?:fix(?:es)?|close[sd]?|resolve[sd]?)\s+#(\d+)", all_text, re.IGNORECASE):
        pr_numbers.add(int(m.group(1)))

    # Bare #N references -- filter to only PRs (not issues)
    for m in re.finditer(r"(?<!\w)#(\d+)", all_text):
        pr_numbers.add(int(m.group(1)))

    # Timeline cross-references
    try:
        timeline_json = run_gh(
            ["api", f"repos/{repo}/issues/{issue_id}/timeline",
             "--paginate", "-q",
             '[.[] | select(.event == "cross-referenced") | .source.issue.number // empty]'],
            check=False,
        )
        if timeline_json:
            for line in timeline_json.splitlines():
                line = line.strip().strip("[]").strip()
                for num_str in re.findall(r"\d+", line):
                    pr_numbers.add(int(num_str))
    except Exception:
        pass

    # Filter: keep only actual PRs (not issues) in the same repo
    confirmed_prs: list[int] = []
    for num in sorted(pr_numbers):
        try:
            pr_data = run_gh(
                ["pr", "view", str(num), "--repo", repo, "--json", "number,state"],
                check=False,
            )
            if pr_data:
                parsed = json.loads(pr_data)
                if parsed.get("number"):
                    confirmed_prs.append(num)
        except Exception:
            continue

    return confirmed_prs


def classify_pr_intent(repo: str, pr_number: int) -> str:
    """Classify whether a PR is a fix attempt or just a reference/reproducer.

    Returns: "fix", "reproducer", or "reference".

    Heuristics (in order):
    - Title contains "repro", "reproduce", "reproducer", "wip repro" -> "reproducer"
    - Title contains "fix", "resolve", "patch", "skip", "disable" -> "fix"
    - PR body contains "Fixes #", "Closes #", "Resolves #" -> "fix"
    - PR has 0 changed files or only test files added -> "reproducer"
    - PR is in a different repo than the issue -> "reference"
    - Default -> "fix" (benefit of the doubt)
    """
    try:
        pr_data = json.loads(
            run_gh(["pr", "view", str(pr_number), "--repo", repo,
                    "--json", "title,body,files"], check=False)
        )
    except Exception:
        return "fix"

    if not pr_data:
        return "fix"

    title = (pr_data.get("title") or "").lower()
    body = (pr_data.get("body") or "").lower()
    files = pr_data.get("files") or []

    reproducer_signals = ["repro", "reproduce", "reproducer", "wip repro", "failure demo"]
    for signal in reproducer_signals:
        if signal in title:
            return "reproducer"

    fix_signals = ["fix", "resolve", "patch", "skip", "disable", "workaround", "address"]
    for signal in fix_signals:
        if signal in title:
            return "fix"

    if re.search(r"(fix(es)?|close[sd]?|resolve[sd]?)\s+#\d+", body, re.IGNORECASE):
        return "fix"

    if files:
        file_paths = [f.get("path", "") for f in files]
        all_test_files = all(
            "test" in p.lower() or p.endswith("_test.py") or p.startswith("test/")
            for p in file_paths if p
        )
        if all_test_files and len(file_paths) <= 3:
            return "reproducer"

    return "fix"


def step4_analyze_pr(repo: str, pr_number: int) -> dict:
    """Step 4: Analyze a linked PR for CI, reviews, approvals."""
    pr_data = json.loads(
        run_gh(["pr", "view", str(pr_number), "--repo", repo,
                "--json", "number,url,state,statusCheckRollup,reviews,reviewRequests"])
    )

    state = pr_data.get("state", "").upper()
    pr_url = pr_data.get("url", f"https://github.com/{repo}/pull/{pr_number}")

    intent = classify_pr_intent(repo, pr_number)

    result = {
        "pr_number": pr_number,
        "pr_url": pr_url,
        "state": state.lower(),
        "intent": intent,
        "ci_passed": None,
        "has_reviewer": False,
        "approved": False,
        "has_unresolved_review": False,
        "ci_failures": [],
        "reviewers": [],
    }

    # Skip merged/closed PRs and non-fix PRs (reproducers/references)
    if state in ("MERGED", "CLOSED"):
        result["ci_passed"] = None
        return result
    if intent != "fix":
        return result

    # 4.1: CI status
    checks = pr_data.get("statusCheckRollup", []) or []
    has_failure = False
    all_complete = True
    for check in checks:
        conclusion = (check.get("conclusion") or "").upper()
        status = (check.get("status") or "").upper()
        if conclusion == "FAILURE":
            has_failure = True
            result["ci_failures"].append(check.get("name", "unknown"))
        if status != "COMPLETED":
            all_complete = False

    if has_failure:
        result["ci_passed"] = False
    elif all_complete and checks:
        result["ci_passed"] = True
    else:
        result["ci_passed"] = None  # Pending or no checks

    # 4.2: Reviews
    reviews = pr_data.get("reviews", []) or []
    review_requests = pr_data.get("reviewRequests", []) or []

    # Collect latest review state per reviewer
    reviewer_states: dict[str, str] = {}
    for review in reviews:
        author = review.get("author", {}).get("login", "")
        state_val = (review.get("state") or "").upper()
        if author and state_val:
            reviewer_states[author] = state_val

    # Check for CHANGES_REQUESTED without subsequent APPROVED
    has_unresolved = any(
        s == "CHANGES_REQUESTED" for s in reviewer_states.values()
    )

    # Check for approval
    has_approval = any(s == "APPROVED" for s in reviewer_states.values())

    # Reviewer assignment
    requested_reviewers = [
        rr.get("login", "") for rr in review_requests if rr.get("login")
    ]
    all_reviewers = list(set(list(reviewer_states.keys()) + requested_reviewers))

    result["has_reviewer"] = len(all_reviewers) > 0
    result["approved"] = has_approval and not has_unresolved
    result["has_unresolved_review"] = has_unresolved
    result["reviewers"] = all_reviewers

    return result


def step4_determine_ar(repo: str, pr_results: list[dict]) -> dict | None:
    """Apply step 4 decision logic across linked PRs."""
    open_fix_prs = [p for p in pr_results if p["state"] == "open" and p.get("intent") == "fix"]
    if not open_fix_prs:
        return None

    for pr in sorted(open_fix_prs, key=lambda x: x["pr_number"], reverse=True):
        pr_num = pr["pr_number"]

        # 4.1: CI failure
        if pr["ci_passed"] is False:
            return {
                "AR": "FIX_CI",
                "AR_REASON": f"#{pr_num} has CI failure",
            }

        # 4.2: Unresolved review
        if pr["has_unresolved_review"]:
            reviewer = next(
                (r for r, s in zip(pr["reviewers"], [None])
                 if True),  # Just pick first reviewer
                pr["reviewers"][0] if pr["reviewers"] else "unknown",
            )
            # Find who requested changes
            return {
                "AR": "NEED_REPLY_REVIEW",
                "AR_REASON": f"#{pr_num} has open request from {', '.join(pr['reviewers'])}",
            }

        # Only proceed to 4.3-4.5 if CI passed
        if pr["ci_passed"] is not True:
            continue

        # 4.3: No reviewer
        if not pr["has_reviewer"]:
            return {
                "AR": "ADD_REVIEWER",
                "AR_REASON": f"#{pr_num} CI passed, no reviewer assigned",
            }

        # 4.4: Reviewer assigned, no feedback
        if pr["has_reviewer"] and not pr["approved"] and not pr["has_unresolved_review"]:
            reviewers_str = ", ".join(pr["reviewers"]) if pr["reviewers"] else "reviewer"
            return {
                "AR": "NEED_REVIEW",
                "AR_REASON": f"{reviewers_str} please review #{pr_num}",
            }

        # 4.5: Approved
        if pr["approved"]:
            return {
                "AR": "LAND_PR",
                "AR_REASON": f"#{pr_num} approved and CI green",
            }

    return None


def main():
    parser = argparse.ArgumentParser(description="Collect open action requests from a GitHub issue")
    parser.add_argument("issue", help="Issue number or full URL")
    parser.add_argument("--repo", default="intel/torch-xpu-ops",
                        help="Repository (default: intel/torch-xpu-ops)")
    parser.add_argument("--output", "-o", help="Write JSON output to this file")
    args = parser.parse_args()

    try:
        repo, issue_id = parse_issue_ref(args.issue, args.repo)
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(2)

    # Fetch issue data
    try:
        issue_data = json.loads(
            run_gh(["issue", "view", str(issue_id), "--repo", repo,
                    "--json", "state,title,url,labels,body,comments"])
        )
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    state = issue_data.get("state", "").upper()
    title = issue_data.get("title", "")
    url = issue_data.get("url", f"https://github.com/{repo}/issues/{issue_id}")
    labels = issue_data.get("labels", [])
    body = issue_data.get("body", "")
    comments = issue_data.get("comments", [])

    output = {
        "issue_id": issue_id,
        "repo": repo,
        "title": title,
        "url": url,
        "status": state.lower(),
        "labels": [l.get("name", "") for l in labels],
        "AR": None,
        "AR_REASON": None,
        "open_requests": [],
        "linked_prs": [],
        "comments_for_llm": None,
    }

    # Step 1: Closed check
    if state == "CLOSED":
        output["AR"] = "N/A"
        output["AR_REASON"] = "Issue is closed"
        _emit(output, args.output)
        return

    # Step 2: Label check
    label_result = step2_check_labels(labels)
    if label_result:
        output.update(label_result)
        _emit(output, args.output)
        return

    # Step 3: Output raw comments for LLM parsing (script does NOT determine AR here)
    # Provide structured comment data for the agent
    comments_for_llm = []
    if body:
        comments_for_llm.append({
            "author": issue_data.get("author", {}).get("login", "") if isinstance(issue_data.get("author"), dict) else "",
            "body": body,
            "url": url,
            "is_issue_body": True,
        })
    for c in comments:
        comments_for_llm.append({
            "author": c.get("author", {}).get("login", "") if isinstance(c.get("author"), dict) else "",
            "body": c.get("body", ""),
            "url": c.get("url", ""),
            "is_issue_body": False,
        })
    output["comments_for_llm"] = comments_for_llm

    # Step 4: Linked PR analysis
    pr_numbers = find_linked_prs(repo, issue_id, body, comments)
    pr_results = []
    for pr_num in pr_numbers:
        try:
            pr_result = step4_analyze_pr(repo, pr_num)
            pr_results.append(pr_result)
        except Exception:
            continue

    output["linked_prs"] = pr_results

    pr_ar = step4_determine_ar(repo, pr_results)
    if pr_ar:
        output["AR"] = pr_ar["AR"]
        output["AR_REASON"] = pr_ar["AR_REASON"]
        # Still output, LLM may override with step 3 if it finds unresolved requests
        output["ar_source"] = "step4_pr_analysis"
    else:
        # No PR-based AR; LLM decides via step 3. If nothing found, fallback is NO_OPENS.
        output["ar_source"] = "pending_llm_step3"

    _emit(output, args.output)


def _emit(output: dict, output_path: str | None):
    """Print JSON to stdout and optionally write to file."""
    json_str = json.dumps(output, indent=2, ensure_ascii=False)
    print(json_str)
    if output_path:
        with open(output_path, "w") as f:
            f.write(json_str)
            f.write("\n")


if __name__ == "__main__":
    main()
