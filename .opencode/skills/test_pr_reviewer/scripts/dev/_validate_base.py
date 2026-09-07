"""Verify the PR base commit is the *merge base*, and that the diff's line
numbers agree with the base/head file contents.

Why this exists
---------------
GitHub's `.base.sha` is a moving pointer at the current tip of the target
branch, not the commit the PR was branched from.  For pytorch#192506 it was 83
commits ahead of the true base, and 2 of the PR's 10 files had also been changed
upstream in that window -- so reading base files at `.base.sha` mixed unrelated
upstream edits into the review.  The `pulls` diff endpoint already uses the
merge base, so the diff and the file contents disagreed, which silently
corrupted every line number.

    python3 dev/_validate_base.py 192506 189250 ...
"""

from __future__ import annotations

import subprocess
import sys

import prdata

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  -> {extra}" if not cond and extra else ""))
    if not cond:
        fails.append(name)


def gh_raw(*args: str) -> str:
    """`gh api --jq` on a scalar yields a bare string, not JSON."""
    p = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    return p.stdout.strip()


def main(nums: list[str]) -> int:
    for num in nums:
        print(f"\n--- PR {num}")
        pr = prdata.load_pr(num)

        # 1. the base must be the merge base, not the branch tip
        want = gh_raw(
            f"repos/{pr.repo}/compare/{pr.branch_tip_sha}...{pr.head_sha}",
            "--jq", ".merge_base_commit.sha",
        )
        check(f"{num}: base_sha is the merge base",
              pr.base_sha == want, f"{pr.base_sha[:12]} != {str(want)[:12]}")
        if pr.branch_tip_sha != pr.base_sha:
            print(f"      branch tip {pr.branch_tip_sha[:12]} is {pr.behind_by} commits ahead")

        # 2. the diff's line numbers must match the files we fetch.  This is the
        #    property that actually breaks when the base is wrong.
        for fd in pr.files:
            if not fd.path.endswith(".py") or fd.binary:
                continue
            bt, ht = pr.base_text(fd), pr.head_text(fd)
            if not bt or not ht:
                continue
            bl, hl = bt.split("\n"), ht.split("\n")
            mb = [
                l.base_no for l in fd.lines
                if l.kind in ("ctx", "del") and l.base_no
                and (l.base_no > len(bl) or bl[l.base_no - 1] != l.text)
            ]
            mh = [
                l.head_no for l in fd.lines
                if l.kind in ("ctx", "add") and l.head_no
                and (l.head_no > len(hl) or hl[l.head_no - 1] != l.text)
            ]
            # a diff GitHub truncated is detected and rebuilt elsewhere; only
            # flag files that claim to be intact
            if fd.truncated:
                print(f"      (skipped {fd.path[-44:]}: diff was truncated and rebuilt)")
                continue
            check(f"{num}: {fd.path[-46:]} base lines match",
                  not mb, f"{len(mb)} mismatched, first at {mb[:3]}")
            check(f"{num}: {fd.path[-46:]} head lines match",
                  not mh, f"{len(mh)} mismatched, first at {mh[:3]}")

    print("\n" + ("ALL BASE-COMMIT CHECKS PASS" if not fails else f"{len(fails)} FAILURE(S)"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["192506", "189250", "195840"]))
