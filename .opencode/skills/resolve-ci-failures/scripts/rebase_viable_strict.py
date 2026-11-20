#!/usr/bin/env python3
"""Rebase the current branch onto pytorch/pytorch viable/strict.

Stops immediately and reports on conflict (never resolves or aborts).
Never pushes: it prints the push command for human approval.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def git(*args: str, check: bool = False) -> tuple[int, str, str]:
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        print(f"error: git {' '.join(args)}: {proc.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def find_upstream() -> str:
    _, out, _ = git("remote", "-v")
    for line in out.splitlines():
        name, _, rest = line.partition("\t")
        if "pytorch/pytorch" in rest:
            return name
    print(
        "error: no remote points at pytorch/pytorch; add one, e.g.\n"
        "  git remote add upstream https://github.com/pytorch/pytorch.git",
        file=sys.stderr,
    )
    sys.exit(1)


def dirty() -> str:
    """Tracked, non-submodule modifications; these must be committed or stashed.

    Untracked files don't block a rebase, and a drifted submodule pointer is
    ubiquitous local noise in this repo rather than real work to preserve.
    """
    _, out, _ = git(
        "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"
    )
    return out


def dirty_submodules() -> str:
    _, out, _ = git("status", "--porcelain", "--untracked-files=no")
    return "\n".join(
        line for line in out.splitlines() if line not in dirty().splitlines()
    )


def rebase_in_progress() -> bool:
    for path in ("rebase-merge", "rebase-apply"):
        _, resolved, _ = git("rev-parse", "--git-path", path)
        if resolved and os.path.isdir(resolved):
            return True
    return False


def report_conflict(upstream: str) -> int:
    _, status, _ = git("status")
    _, unmerged, _ = git("diff", "--diff-filter=U", "--name-only")
    print("\n=== REBASE CONFLICT - STOPPING FOR HUMAN REVIEW ===\n")
    print("Conflicting paths:")
    print(unmerged or "(none reported)")
    print("\ngit status:\n")
    print(status)
    print(
        "\nThe rebase is left IN PROGRESS on purpose. Resolve manually, then\n"
        "  git add <files> && git rebase --continue\n"
        "or back out with\n"
        "  git rebase --abort\n"
        f"(rebasing onto {upstream}/viable/strict)"
    )
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--upstream", help="remote name for pytorch/pytorch (auto-detected)")
    ap.add_argument("--branch", default="viable/strict")
    ap.add_argument("--fork-remote", help="remote to push the PR head branch to")
    ap.add_argument("--dry-run", action="store_true", help="fetch only, no rebase")
    args = ap.parse_args()

    upstream = args.upstream or find_upstream()

    if rebase_in_progress():
        print(
            "error: a rebase is already in progress; finish or abort it first.",
            file=sys.stderr,
        )
        return 1

    pending = dirty()
    if pending:
        print(
            "error: tracked changes present. Commit (after human review) or "
            "stash first:\n" + pending,
            file=sys.stderr,
        )
        return 1

    subs = dirty_submodules()
    if subs:
        print("note: ignoring drifted submodule pointer(s):\n" + subs)

    _, head_desc, _ = git("rev-parse", "--abbrev-ref", "HEAD")
    _, head_sha, _ = git("rev-parse", "HEAD")
    detached = head_desc == "HEAD"
    print(f"HEAD: {head_desc} @ {head_sha[:12]}" + (" (detached - likely ghstack)" if detached else ""))

    git("fetch", upstream, args.branch, check=True)
    _, target, _ = git("rev-parse", "FETCH_HEAD")
    print(f"{upstream}/{args.branch} -> {target[:12]}")

    if args.dry_run:
        _, behind, _ = git("rev-list", "--count", f"HEAD..{target}")
        _, ahead, _ = git("rev-list", "--count", f"{target}..HEAD")
        print(f"ahead {ahead}, behind {behind}; would run: git rebase {target}")
        return 0

    rc, out, err = git("rebase", target)
    print(out or err)
    if rc != 0:
        return report_conflict(upstream)

    _, log, _ = git("log", "--oneline", f"{target}..HEAD")
    print("\nRebase clean. Commits on top of viable/strict:\n")
    print(log)
    print("\n=== REVIEW GATE: nothing pushed ===")
    if detached:
        print("ghstack commit detected: submit with `ghstack --no-stack` after approval.")
    else:
        remote = args.fork_remote or "<fork-remote>"
        print(f"Push after approval with:\n  git push --force-with-lease {remote} HEAD:{head_desc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
