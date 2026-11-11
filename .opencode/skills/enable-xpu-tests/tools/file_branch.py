#!/usr/bin/env python3
"""Per-file branch lifecycle in the workspace clone.

  file_branch.py start  <test_file> --csv NEW.csv [--cases id,id,...]
      - registers the file (file.json) and its selected cases (cases.json) from the CSV
      - reuses agent_<slug>_* if it exists, else creates agent_<slug>_<rand4> at base_sha
      - checks the branch out (refuses on a dirty clone)
  file_branch.py finish <test_file>
      - requires a clean tree (commit first), writes <slug>.patch = git diff base..branch,
        records commit/patch_sha, deletes the branch if it has no commits, and
        returns the clone to detached base
  file_branch.py regen-patch <test_file>     rewrite the patch from base..branch
  file_branch.py checkout <test_file>        switch to the existing agent branch (rework)
  file_branch.py rebuild <test_file>         rework from scratch: rename the current agent
      branch to <name>.legacy (kept for reference) and start a fresh one at base_sha
"""
import argparse
import json
import os
import sys

import gitops
import store
from common import WORKSPACE_JSON, slug as make_slug, test_path
from select_cases import select


def ws_base() -> str:
    with open(WORKSPACE_JSON) as fh:
        return json.load(fh)["base_sha"]


def start(test_file: str, csv_path: str, case_ids=None) -> dict:
    s = make_slug(test_file)
    f = store.load_file(s)
    base = f.get("base_sha") or ws_base()
    branch = f.get("agent_branch") or gitops.find_agent_branch(s)
    gitops.require_clean()
    if branch and gitops.branch_exists(branch):
        gitops.switch(branch)
    else:
        branch = gitops.new_agent_branch_name(s)
        gitops.switch(branch, create_from=base)
    f = store.init_file(test_file, source=test_path(test_file), base_sha=base,
                        agent_branch=branch, status="In progress")
    if csv_path:
        rows = store.read_csv(csv_path)
        if case_ids:
            want = set(map(str, case_ids))
            rows = [r for r in rows if str(r.get("case_id")) in want]
        else:
            rows = select(rows, files=[test_file]).get(test_file, [])
        existing = store.load_cases(s)
        store.update_cases(s, {r["case_id"]: {"class_name": r.get("class_name", ""),
                                              "test_name": r.get("test_name", "")}
                               for r in rows if str(r["case_id"]) not in existing})
    return f


def regen_patch(test_file: str) -> dict:
    s = make_slug(test_file)
    f = store.load_file(s)
    if not f["agent_branch"] or not gitops.branch_exists(f["agent_branch"]):
        return f
    psha = gitops.write_patch(f["base_sha"], f["agent_branch"], store.patch_path(s))
    return store.update_file(s, commit=gitops.git("rev-parse", "--short=12", f["agent_branch"]),
                             patch_sha=psha)


def finish(test_file: str) -> dict:
    s = make_slug(test_file)
    f = store.load_file(s)
    gitops.require_clean()
    branch, base = f["agent_branch"], f["base_sha"]
    if branch and gitops.branch_exists(branch) and not gitops.commits(base, branch):
        gitops.restore(base)
        gitops.git("branch", "-D", branch)
        if os.path.exists(store.patch_path(s)):
            os.remove(store.patch_path(s))
        f = store.update_file(s, agent_branch="", commit="", patch_sha="", status="No change")
    else:
        f = regen_patch(test_file)
        f = store.update_file(s, status="Processed")
    gitops.restore(base)
    return f


def rebuild(test_file: str) -> dict:
    s = make_slug(test_file)
    f = store.load_file(s)
    old = f.get("agent_branch") or gitops.find_agent_branch(s)
    gitops.require_clean()
    legacy = ""
    if old and gitops.branch_exists(old):
        legacy = old + ".legacy"
        if gitops.branch_exists(legacy):
            gitops.git("branch", "-D", legacy)
        gitops.git("branch", "-m", old, legacy)
    store.update_file(s, agent_branch="", commit="", patch_sha="", legacy_branch=legacy)
    return start(test_file, "")


def checkout(test_file: str) -> dict:
    f = store.load_file(make_slug(test_file))
    if not f["agent_branch"]:
        raise SystemExit(f"{test_file} has no agent branch")
    gitops.switch(f["agent_branch"])
    return f


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["start", "finish", "regen-patch", "checkout", "rebuild"])
    ap.add_argument("test_file")
    ap.add_argument("--csv")
    ap.add_argument("--cases", help="comma-separated case ids (default: select_cases rules)")
    a = ap.parse_args()
    try:
        if a.action == "start":
            f = start(a.test_file, a.csv, a.cases.split(",") if a.cases else None)
        elif a.action == "finish":
            f = finish(a.test_file)
        elif a.action == "rebuild":
            f = rebuild(a.test_file)
        elif a.action == "regen-patch":
            f = regen_patch(a.test_file)
        else:
            f = checkout(a.test_file)
    except gitops.GitError as e:
        sys.exit(f"ERROR: {e}")
    print(json.dumps(f, indent=2))


if __name__ == "__main__":
    main()
