#!/usr/bin/env python3
"""
Validate and fix final_output.json from issue-triage orchestrator subagents.

Ensures output conforms to the schema defined in the issue-triage skill.
Run after each subagent completes, before consuming results.

Usage:
    python validate_triage_output.py <issue_dir>
    python validate_triage_output.py agent_space/issue_triage_orchestrator/intel_torch-xpu-ops_issue_4000

Exit codes:
    0 - valid (or fixed successfully)
    1 - unfixable schema violation
"""

import json
import sys
from pathlib import Path


def load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def save_json(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  [WRITE] {path}")


def validate_and_fix(issue_dir: Path) -> list[str]:
    """Returns list of fixes applied. Raises on unfixable violations."""
    output_path = issue_dir / "final_output.json"
    if not output_path.exists():
        raise FileNotFoundError(f"No final_output.json in {issue_dir}")

    data = load_json(output_path)
    fixes = []
    warnings = []

    # --- 1. Top-level required fields ---
    required_top = ["issue", "status", "hard_stop", "issue_dir",
                    "extract_result", "reproduce_result", "triage_result",
                    "notification", "step_durations", "logs"]
    for field in required_top:
        if field not in data:
            if field == "hard_stop":
                data["hard_stop"] = None
                fixes.append(f"Added missing field: hard_stop=null")
            else:
                warnings.append(f"MISSING required top-level field: {field}")

    # --- 2. issue block ---
    issue = data.get("issue", {})
    for f in ["issue_id", "repo", "title", "url"]:
        if f not in issue:
            warnings.append(f"issue.{f} missing")

    # --- 3. triage_result.root_cause (Step 4 rule) ---
    triage = data.get("triage_result")
    if triage is not None:
        tc_root_cause = (
            (triage.get("target_component") or {})
            .get("result") or {}
        ).get("root_cause")
        existing_root_cause = triage.get("root_cause")

        if tc_root_cause and not existing_root_cause:
            triage["root_cause"] = tc_root_cause
            fixes.append(
                f"Populated triage_result.root_cause from "
                f"target_component.result.root_cause"
            )
        elif not tc_root_cause and not existing_root_cause:
            # Check if short-circuited
            short_circuit = triage.get("short_circuit_reason")
            if short_circuit:
                triage["root_cause"] = None
                fixes.append("Set triage_result.root_cause=null (short-circuited)")
            else:
                triage["root_cause"] = None
                fixes.append("Set triage_result.root_cause=null (no source available)")

    # --- 4. Verdict consistency check ---
    if triage is not None:
        top_verdict = triage.get("verdict")
        tc_verdict = (
            (triage.get("target_component") or {})
            .get("result") or {}
        ).get("verdict")
        preliminary = triage.get("preliminary_verdict")

        # The skill says preliminary NEED_HUMAN "without skipping the rest of
        # the pipeline" — meaning target_component's verdict should override
        # if it's more specific (NEED_FIX > NEED_HUMAN for non-short-circuit).
        if (
            tc_verdict in ("NEED_FIX", "NEED_FIX_CASE", "NEED_FIX_3RDPARTY")
            and top_verdict == "NEED_HUMAN"
            and not triage.get("short_circuit_reason")
        ):
            triage["verdict"] = tc_verdict
            fixes.append(
                f"Fixed verdict: {top_verdict} -> {tc_verdict} "
                f"(target_component override; preliminary was advisory only)"
            )

    # --- 5. notification block shape ---
    notif = data.get("notification")
    if notif is not None:
        required_notif = [
            "summary_path", "commented", "comment_url", "comment_error",
            "comment_action", "existing_comment_id", "need_action",
            "labeled", "label_error", "apply_label_reason",
        ]
        for f in required_notif:
            if f not in notif:
                notif[f] = None
                fixes.append(f"Added missing notification.{f}=null")

    # --- 6. step_durations block shape ---
    durations = data.get("step_durations")
    if durations is not None:
        required_dur = [
            "step1_extract_seconds", "step2_reproduce_seconds",
            "step3_triage_seconds", "step5_notify_seconds", "total_seconds",
        ]
        for f in required_dur:
            if f not in durations:
                durations[f] = None
                fixes.append(f"Added missing step_durations.{f}=null")

    # --- 7. extract_result.issue_type ---
    extract = data.get("extract_result")
    if extract is not None and "issue_type" not in extract:
        # Try to infer from github_type or type
        github_type = extract.get("github_type", "")
        if github_type:
            extract["issue_type"] = github_type
            fixes.append(f"Set extract_result.issue_type from github_type: {github_type}")
        else:
            extract["issue_type"] = "Bug"  # default
            fixes.append("Set extract_result.issue_type='Bug' (default fallback)")

    # --- 8. need_action derivation check ---
    if notif is not None and triage is not None:
        verdict = triage.get("verdict")
        reproduce = data.get("reproduce_result")
        dup_source = (
            triage.get("duplication", {}).get("source")
            if triage.get("duplication")
            else None
        )

        expected_need_action = None
        if dup_source == "skipped-duplicate-triaged":
            expected_need_action = "Inherited from duplicate"
        elif verdict == "NEED_FIX":
            expected_need_action = "Fix required (product code)"
        elif verdict == "NEED_FIX_CASE":
            expected_need_action = "Fix required (test case)"
        elif verdict == "NEED_FIX_3RDPARTY":
            expected_need_action = "Blocked — third-party dependency"
        elif verdict == "NEED_HUMAN":
            expected_need_action = "Needs human review"

        # Only fix if current value is a placeholder or upload=false sentinel
        current = notif.get("need_action", "")
        if expected_need_action and "upload=false" in current:
            # Don't override — upload=false is intentional local-only mode
            pass
        elif expected_need_action and current != expected_need_action:
            # Record as warning, don't auto-fix (may be intentional)
            warnings.append(
                f"need_action mismatch: got '{current}', "
                f"expected '{expected_need_action}' based on verdict={verdict}"
            )

    # --- Write back if fixes applied ---
    if fixes:
        save_json(output_path, data)

    return fixes, warnings


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_triage_output.py <issue_dir> [issue_dir2 ...]")
        sys.exit(1)

    all_ok = True
    for arg in sys.argv[1:]:
        issue_dir = Path(arg)
        print(f"\n{'='*60}")
        print(f"Validating: {issue_dir}")
        print(f"{'='*60}")

        try:
            fixes, warnings = validate_and_fix(issue_dir)
            if fixes:
                print(f"  FIXES APPLIED ({len(fixes)}):")
                for f in fixes:
                    print(f"    ✓ {f}")
            else:
                print("  ✓ No fixes needed")

            if warnings:
                print(f"  WARNINGS ({len(warnings)}):")
                for w in warnings:
                    print(f"    ⚠ {w}")
                all_ok = False
        except FileNotFoundError as e:
            print(f"  ✗ {e}")
            all_ok = False
        except Exception as e:
            print(f"  ✗ Unexpected error: {e}")
            all_ok = False

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
