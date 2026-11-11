#!/usr/bin/env python3
"""PR bundles: pick several test files' agent branches onto one target branch, push it
to the user's fork on request, let the human create the PR on GitHub's prefilled page,
then record the PR on every case (Fix PR).

  pr_bundle.py create  --target BRANCH --title T --files a.py b.py [--force]
  pr_bundle.py update  <id>             cherry-pick commits added to agent branches since
  pr_bundle.py remove  <id> --files a.py   (before submit) rebuild target without them
  pr_bundle.py set-pr  <id> <number|url>
  pr_bundle.py refresh <id>             gh pr view -> state (display only)
  pr_bundle.py delete  <id>             (before submit, or after the PR is closed)
  pr_bundle.py show    <id> | list
  pr_bundle.py record <number|url> --files a.py ... [--cases id,id]   map an existing PR to cases
  pr_bundle.py file-link <test_file>         per-file PR: push command + prefilled GitHub PR URL
  pr_bundle.py submit-file <test_file> | submit <id>   push to fork, print the prefilled PR page (asks y/N)
  pr_bundle.py file-link-pr <test_file> | link <id>    after you created the PR on GitHub: find it, fill Fix PR
  pr_bundle.py file-set-pr <test_file> <number|url>    record a PR you opened some other way

Bundle JSON (_prs/<id>.json):
  {id, title, target_branch, base_sha, created, status: picked|submitted|merged|closed,
   files: [{slug, test_file, state: picked|conflict, picked: {agent_sha: target_sha},
            conflicts: [...]}],
   pr: {number, url, state, head, checked_at}, description}
Each file is cherry-picked as its own commit(s) (-x), never squashed. Pushing and PR
of the branch happens only via submit / submit-file (user click or y/N prompt); the PR
itself is always created by the human on GitHub's prefilled page.
"""
import argparse
import json
import os
import random
import subprocess
import sys
import time
import urllib.parse

import gitops
import store
import verify_evidence
from common import PR_REPO, WORKSPACE_JSON, parse_ref, slug as make_slug

ACTIVE = {"picked", "submitted", "merged"}
PR_BASE = "main"
MAX_URL = 7500  # GitHub rejects much longer URLs (414)


def _ws() -> dict:
    with open(WORKSPACE_JSON) as fh:
        return json.load(fh)


def preflight(slugs: list[str]) -> dict:
    """Blocking errors + warnings the UI asks the human to confirm."""
    errors, warns = [], []
    if not gitops.is_clean():
        errors.append("workspace clone is dirty")
    bundles = {b["id"]: b for b in store.list_bundles()}
    probs = verify_evidence.summary(verify_evidence.check([store.load_file(s)["test_file"] for s in slugs], False))
    for s in slugs:
        f = store.load_file(s)
        if not f["commit"] or not f["agent_branch"]:
            errors.append(f"{f['test_file'] or s}: no agent commit")
        b = bundles.get(f.get("bundle") or "")
        if b and b.get("status") in ACTIVE:
            errors.append(f"{f['test_file']}: already in bundle {b['id']} ({b['status']})")
        rv = store.load_reviews(s)["verdicts"]
        cases = store.load_cases(s)
        pend = [c for c in cases if rv.get(c) != "Confirmed"]
        if pend:
            warns.append(f"{f['test_file']}: {len(pend)} case(s) not Confirmed")
        if any((not d.get("number")) for d in store.load_drafts(s)):
            warns.append(f"{f['test_file']}: unsubmitted issue draft (placeholder still in patch)")
        errs = sum(p["severity"] == "ERROR" for ps in probs.get(f["test_file"], {}).values() for p in ps)
        if errs:
            warns.append(f"{f['test_file']}: {errs} evidence error(s)")
    return {"errors": errors, "warnings": warns}


def _pick_files(b: dict, files: list[dict]) -> None:
    for fe in files:
        f = store.load_file(fe["slug"])
        done = fe.setdefault("picked", {})
        todo = [c for c in gitops.commits(f["base_sha"], f["agent_branch"]) if c not in done]
        new, conflicts = gitops.cherry_pick(todo)
        done.update(dict(zip(todo, new)))
        fe["state"] = "conflict" if conflicts else "picked"
        fe["conflicts"] = conflicts
        if not conflicts:
            store.update_file(fe["slug"], bundle=b["id"])


def create(target: str, title: str, files: list[str], force: bool = False) -> dict:
    slugs = [make_slug(x) for x in files]
    pf = preflight(slugs)
    if pf["errors"] or (pf["warnings"] and not force):
        return {"ok": False, **pf, "need_confirm": not pf["errors"]}
    bases = [store.load_file(s)["base_sha"] for s in slugs]
    base = max(bases, key=lambda sha: int(gitops.git("rev-list", "--count", sha)))
    b = {"id": f"pr_{time.strftime('%Y%m%d')}_{random.randint(1000, 9999)}", "title": title,
         "target_branch": target, "base_sha": base, "created": store.now(), "status": "picked",
         "files": [{"slug": s, "test_file": store.load_file(s)["test_file"]} for s in slugs],
         "pr": {}, "description": ""}
    if gitops.branch_exists(target):
        gitops.switch(target)
    else:
        gitops.switch(target, create_from=base)
    try:
        _pick_files(b, b["files"])
    finally:
        gitops.restore(base)
    b["description"] = draft_description(b)
    store.save_bundle(b)
    _write_body(b)
    return {"ok": True, "bundle": b, "log": log(b), "warnings": pf["warnings"]}


def update(bid: str) -> dict:
    b = store.load_bundle(bid)
    if b.get("kind") == "external":
        return {"ok": False, "error": "external PR: update its own branch (" + b.get("target_branch", "") + ")"}
    if b.get("kind") == "single":
        fe, f = b["files"][0], store.load_file(b["files"][0]["slug"])
        fe["picked"] = {c: c for c in gitops.commits(f["base_sha"], f["agent_branch"])}
        store.save_bundle(b)
        return {"ok": True, "bundle": b, "log": log(b), "note": "push the agent branch again"}
    gitops.switch(b["target_branch"])
    try:
        _pick_files(b, b["files"])
    finally:
        gitops.restore(b["base_sha"])
    store.save_bundle(b)
    return {"ok": True, "bundle": b, "log": log(b)}


def pending_commits(b: dict) -> dict[str, int]:
    out = {}
    if b.get("kind") == "external":
        return out
    for fe in b.get("files", []):
        f = store.load_file(fe["slug"])
        if f["agent_branch"] and gitops.branch_exists(f["agent_branch"]):
            n = len([c for c in gitops.commits(f["base_sha"], f["agent_branch"]) if c not in fe.get("picked", {})])
            if n:
                out[fe["slug"]] = n
    return out


def remove(bid: str, files: list[str]) -> dict:
    b = store.load_bundle(bid)
    if b.get("kind") == "single":
        return {"ok": False, "error": "single-file PR: delete it instead"}
    if b["status"] != "picked":
        return {"ok": False, "error": "only allowed before the PR number is set"}
    drop = {make_slug(x) for x in files}
    keep = [fe for fe in b["files"] if fe["slug"] not in drop]
    for fe in b["files"]:
        if fe["slug"] in drop:
            store.update_file(fe["slug"], bundle="")
    gitops.require_clean()
    gitops.git("branch", "-f", b["target_branch"], b["base_sha"])
    b["files"] = [{"slug": fe["slug"], "test_file": fe["test_file"]} for fe in keep]
    gitops.switch(b["target_branch"])
    try:
        _pick_files(b, b["files"])
    finally:
        gitops.restore(b["base_sha"])
    b["description"] = draft_description(b)
    store.save_bundle(b)
    _write_body(b)
    return {"ok": True, "bundle": b, "log": log(b)}


def _gh_pr(number: int) -> dict:
    p = subprocess.run(["gh", "pr", "view", str(number), "--repo", PR_REPO, "--json",
                        "number,url,state,isDraft,headRefName,title,mergedAt"],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip())
    return json.loads(p.stdout)


def set_pr(bid: str, ref: str, confirm_head: bool = False) -> dict:
    b = store.load_bundle(bid)
    num, url = parse_ref(ref, PR_REPO, "pull")
    try:
        d = _gh_pr(num)
    except RuntimeError as e:
        return {"ok": False, "error": f"gh pr view {num}: {e}"}
    if d["headRefName"] != b["target_branch"] and not confirm_head:
        return {"ok": False, "need_confirm": True,
                "warnings": [f"PR head is {d['headRefName']!r}, bundle target is {b['target_branch']!r}"]}
    b["pr"] = {"number": d["number"], "url": d["url"], "head": d["headRefName"],
               "state": "DRAFT" if d.get("isDraft") and d["state"] == "OPEN" else d["state"],
               "checked_at": store.now()}
    b["status"] = "merged" if d["state"] == "MERGED" else "submitted"
    n = 0
    for fe in b["files"]:
        if fe.get("state") != "picked":
            continue
        cases = store.load_cases(fe["slug"])
        store.update_cases(fe["slug"], {cid: {"fix_pr": d["url"]} for cid in cases})
        n += len(cases)
    store.save_bundle(b)
    return {"ok": True, "bundle": b, "cases_updated": n}


def refresh(bid: str) -> dict:
    b = store.load_bundle(bid)
    if not b.get("pr", {}).get("number"):
        return {"ok": False, "error": "no PR number yet"}
    d = _gh_pr(b["pr"]["number"])
    b["pr"].update(state="DRAFT" if d.get("isDraft") and d["state"] == "OPEN" else d["state"],
                   checked_at=store.now())
    if d["state"] == "MERGED":
        b["status"] = "merged"
    elif d["state"] == "CLOSED":
        b["status"] = "closed"
    store.save_bundle(b)
    return {"ok": True, "bundle": b}


def delete(bid: str) -> dict:
    b = store.load_bundle(bid)
    if b.get("kind") == "external" and b.get("status") != "closed":
        return {"ok": False, "error": "recorded external PR; it can be deleted only after it is closed"}
    if b.get("status") in ("submitted", "merged"):
        return {"ok": False, "error": f"bundle is {b['status']}; delete only before submit or after close"}
    for fe in b.get("files", []):
        if store.load_file(fe["slug"]).get("bundle") == bid:
            store.update_file(fe["slug"], bundle="")
        if b.get("pr", {}).get("url"):
            cases = store.load_cases(fe["slug"])
            store.update_cases(fe["slug"], {c: {"fix_pr": ""} for c in cases
                                            if cases[c].get("fix_pr") == b["pr"]["url"]})
    if (b.get("kind") != "single" and gitops.branch_exists(b["target_branch"])
            and gitops.current_branch() != b["target_branch"]):
        gitops.git("branch", "-D", b["target_branch"])
    store.delete_bundle(bid)
    return {"ok": True}


def log(b: dict) -> str:
    if b.get("kind") == "external" or not gitops.branch_exists(b["target_branch"]):
        return ""
    return (gitops.git("log", "--oneline", f"{b['base_sha']}..{b['target_branch']}") + "\n\n"
            + gitops.git("diff", "--stat", f"{b['base_sha']}..{b['target_branch']}"))


def test_plan_line(test_file: str, cases: dict) -> str:
    """Portable repro command for a file's cases: XPU test names, no local paths or flags."""
    from common import cuda_to_xpu
    names = sorted({cuda_to_xpu(c.get("test_name", "")) for c in cases.values() if c.get("test_name")})
    path = test_file if test_file.startswith("test/") else "test/" + test_file
    if not names:
        return f"python -m pytest {path} -v"
    return f"python -m pytest {path} -v -k \"{' or '.join(names)}\""


def draft_description(b: dict) -> str:
    rows, plans, case_lines = [], [], []
    for fe in b["files"]:
        if fe.get("state") != "picked":
            continue
        cases = store.load_cases(fe["slug"])
        cnt = {}
        for c in cases.values():
            cnt[c.get("portability") or "unset"] = cnt.get(c.get("portability") or "unset", 0) + 1
        rows.append(f"> | `{fe['test_file']}` | " + ", ".join(f"{k}: {v}" for k, v in sorted(cnt.items())) + " |")
        for c in cases.values():
            case_lines.append(f"{fe['test_file']},{c.get('class_name', '')},{c.get('test_name', '')}"
                              f"  # {c.get('portability', '')}"
                              + (f" #{c['issue_number']}" if c.get("issue_number") else ""))
        plans.append(test_plan_line(fe["test_file"], cases))
    return "\n".join([
        "## Linked issue or supporting maintainer", "", "Related cases:", "", "```", *case_lines, "```", "",
        "## BC-breaking?", "", "No", "",
        "## Summary (human written only)", "", "### Background", "", "<!-- TODO (human): fill in -->", "",
        "### Solution", "", "<!-- TODO (human): fill in -->", "",
        "## Agent details (if applicable)", "",
        "The analysis below was generated by an AI assistant (Claude via OpenCode).", "", "<details>", "",
        "> Each test file is a separate commit that makes the file device-agnostic and enables it on XPU;",
        "> cases that still fail on XPU keep a skip whose message is the tracking intel/torch-xpu-ops issue.",
        ">", "> | file | verdicts |", "> |---|---|", *rows, ">", "> **Test Plan.**", ">", "> ```",
        *[f"> {p}" for p in plans], "> ```", "", "</details>", "",
        "cc @gujinghui @EikanWang @fengyuan14 @guangyey", ""])


def _write_body(b: dict) -> str:
    path = _body_path(b)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(b.get("description", ""))
    return path


def save_description(bid: str, text: str) -> dict:
    b = store.load_bundle(bid)
    b["description"] = text
    store.save_bundle(b)
    return {"ok": True, "path": _write_body(b)}


# ---- per-file PRs + links ---------------------------------------------------

def record_external(ref: str, files: list[str], case_ids: list[str] | None = None) -> dict:
    """Record an already-submitted PR (not created by these tools): set fix_pr on the
    given files' cases (or only case_ids) and store a kind=external bundle."""
    num, _ = parse_ref(ref, PR_REPO, "pull")
    d = _gh_pr(num)
    only = set(map(str, case_ids)) if case_ids else None
    entries, n = [], 0
    for tf in files:
        s = make_slug(tf)
        cases = store.load_cases(s)
        ids = [c for c in cases if only is None or c in only]
        if not ids:
            continue
        store.update_cases(s, {c: {"fix_pr": d["url"]} for c in ids})
        store.update_file(s, bundle=f"pr_{num}")
        entries.append({"slug": s, "test_file": tf, "state": "picked", "picked": {}, "conflicts": [],
                        **({"cases": ids} if only is not None or len(ids) != len(cases) else {})})
        n += len(ids)
    b = {"id": f"pr_{num}", "kind": "external", "title": d["title"], "target_branch": d["headRefName"],
         "base_sha": "", "created": store.now(), "description": "", "files": entries,
         "status": "merged" if d["state"] == "MERGED" else ("closed" if d["state"] == "CLOSED" else "submitted"),
         "pr": {"number": num, "url": d["url"], "head": d["headRefName"], "checked_at": store.now(),
                "state": "DRAFT" if d.get("isDraft") and d["state"] == "OPEN" else d["state"]}}
    store.save_bundle(b)
    return {"ok": True, "bundle": b["id"], "files": len(entries), "cases_updated": n}


def single_bundle(slug: str) -> dict:
    """The file's own single-file PR bundle (saved, or a virtual one if none yet).
    Its target is the agent branch itself, so nothing is cherry-picked."""
    f = store.load_file(slug)
    b = store.load_bundle(f["bundle"]) if f.get("bundle") else {}
    if b.get("kind") == "single":
        if b.get("status") == "picked":                # not yet submitted: keep description current
            fresh = draft_description(b)
            edited = _user_edited(f.get("pr_body", ""), f.get("pr_body_generated", ""))
            b["description"] = f["pr_body"] if edited else fresh
            b["description_stale"] = edited and f["pr_body"] != fresh
        return b
    commits = (gitops.commits(f["base_sha"], f["agent_branch"])
               if f["agent_branch"] and gitops.branch_exists(f["agent_branch"]) else [])
    b = {"id": f"pr_{slug}", "kind": "single",
         "title": f.get("pr_title") or f"[xpu] Enable {f['test_file']} on XPU",
         "target_branch": f["agent_branch"], "base_sha": f["base_sha"], "created": store.now(),
         "status": "picked", "pr": {},
         "files": [{"slug": slug, "test_file": f["test_file"], "state": "picked",
                    "picked": {c: c for c in commits}, "conflicts": []}]}
    fresh = draft_description(b)
    # keep the user's edited body only if they changed it; an untouched old draft is
    # regenerated so verdicts/issues in it stay current
    edited = _user_edited(f.get("pr_body", ""), f.get("pr_body_generated", ""))
    b["description"] = f["pr_body"] if edited else fresh
    b["description_stale"] = edited and f["pr_body"] != fresh
    return b


def _user_edited(body: str, generated: str) -> bool:
    """A stored body counts as the user's own text only if it differs from what we
    generated AND the human Summary placeholders were filled in (an untouched old draft
    is just a stale snapshot and gets regenerated)."""
    if not body or body == generated:
        return False
    return "<!-- TODO (human): fill in -->" not in body or body.count("TODO (human)") < 2


def regenerate_file_pr(slug: str) -> dict:
    """Drop a hand-edited body and go back to the generated one (current verdicts)."""
    store.update_file(slug, pr_body="", pr_body_generated="")
    b = single_bundle(slug)
    if store.load_bundle(b["id"]):
        b2 = store.load_bundle(b["id"]); b2["description"] = b["description"]; store.save_bundle(b2); _write_body(b2)
    return {"ok": True, "description": b["description"]}


def save_file_pr(slug: str, title: str, text: str) -> dict:
    gen = draft_description(single_bundle(slug)) if text else ""
    store.update_file(slug, pr_title=title, pr_body=text if text != gen else "", pr_body_generated=gen)
    b = single_bundle(slug)
    if store.load_bundle(b["id"]):
        b.update(title=title, description=text)
        store.save_bundle(b)
        _write_body(b)
    return {"ok": True}


def set_file_pr(slug: str, ref: str, confirm_head: bool = False) -> dict:
    f = store.load_file(slug)
    other = store.load_bundle(f["bundle"]) if f.get("bundle") else {}
    if other and other.get("kind") != "single" and other.get("status") in ACTIVE:
        return {"ok": False, "error": f"file is in PR bundle {other['id']} ({other['status']})"}
    if not f["agent_branch"]:
        return {"ok": False, "error": "file has no agent branch / commit"}
    b = single_bundle(slug)
    store.save_bundle(b)
    _write_body(b)
    store.update_file(slug, bundle=b["id"])
    return set_pr(b["id"], ref, confirm_head)


# ---- one-click submit (user-confirmed in the UI) -------------------------------

def _gh(args: list[str], timeout: int = 120) -> str:
    p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip())
    return p.stdout.strip()


def _existing_pr(owner: str, branch: str) -> int:
    out = _gh(["pr", "list", "--repo", PR_REPO, "--head", branch, "--state", "open",
               "--json", "number,headRepositoryOwner", "--limit", "20"])
    for d in json.loads(out or "[]"):
        if (d.get("headRepositoryOwner") or {}).get("login") == owner:
            return d["number"]
    return 0


def submit(b: dict, dry_run: bool = False) -> dict:
    """Push the branch to the fork and return GitHub's prefilled 'Open a pull request'
    page. The human reviews and clicks 'Create (draft) pull request' there; the tools
    never create the PR. If an open PR from this branch already exists it is linked
    instead (and Fix PR filled)."""
    ws = _ws()
    remote, owner = ws.get("fork_remote"), ws.get("fork_owner")
    if not (remote and owner):
        return {"ok": False, "error": "fork not configured: setup_ws.py --fork-remote R --fork-owner O"}
    branch = b["target_branch"]
    if not branch or not gitops.branch_exists(branch):
        return {"ok": False, "error": f"local branch {branch!r} not found"}
    if dry_run:
        return {"ok": True, "dry_run": True, "would": submit_commands(b)[:1], "open_url": compare_url(b)}
    gitops.push(remote, branch)
    _PUSH_CACHE.pop(branch, None)
    if (b.get("pr") or {}).get("number"):
        return {"ok": True, "updated": True, "bundle": b, "pr": b["pr"], "open_url": b["pr"]["url"]}
    store.save_bundle(b)
    _write_body(b)
    num = _existing_pr(owner, branch)
    if num:
        return {**set_pr(b["id"], str(num), confirm_head=True), "linked": True,
                "open_url": f"https://github.com/{PR_REPO}/pull/{num}"}
    return {"ok": True, "pushed": True, "open_url": compare_url(b), "branch": branch}


def link_pr(b: dict) -> dict:
    """After the human created the PR on GitHub: find the open PR from this branch and
    record it (fills Fix PR). No-op if none exists yet."""
    owner = _ws().get("fork_owner", "")
    if (b.get("pr") or {}).get("number"):
        return {"ok": True, "found": True, "pr": b["pr"]}
    num = _existing_pr(owner, b["target_branch"]) if owner and b.get("target_branch") else 0
    if not num:
        return {"ok": True, "found": False}
    if not store.load_bundle(b["id"]):
        store.save_bundle(b)
    return {**set_pr(b["id"], str(num), confirm_head=True), "found": True}


def submit_file(slug: str, title: str = "", text: str = "", dry_run: bool = False) -> dict:
    f = store.load_file(slug)
    other = store.load_bundle(f["bundle"]) if f.get("bundle") else {}
    if other and other.get("kind") != "single" and other.get("status") in ACTIVE:
        return {"ok": False, "error": f"file is already in PR {other['id']} ({other['status']})"}
    if title or text:
        save_file_pr(slug, title or f.get("pr_title", ""), text or f.get("pr_body", ""))
    b = single_bundle(slug)
    r = submit(b, dry_run)
    if r.get("ok") and not dry_run:
        store.update_file(slug, bundle=b["id"])
    return r


def submit_bundle(bid: str, dry_run: bool = False) -> dict:
    return submit(store.load_bundle(bid), dry_run)


def link_file_pr(slug: str) -> dict:
    b = single_bundle(slug)
    r = link_pr(b)
    if r.get("found"):
        store.update_file(slug, bundle=b["id"])
    return r


def compare_url(b: dict) -> str:
    """GitHub 'open a pull request' URL with title and body prefilled. Works once
    the target branch has been pushed to the user's fork."""
    if b.get("kind") == "external":
        return ""
    owner = _ws().get("fork_owner", "")
    if not owner or not b.get("target_branch"):
        return ""
    head = f"https://github.com/{PR_REPO}/compare/{PR_BASE}...{owner}:{b['target_branch']}?"
    q = {"expand": "1", "draft": "1", "title": b.get("title", ""), "body": b.get("description", "")}
    url = head + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)
    if len(url) > MAX_URL:
        q["body"] = (b.get("description", "").split("Related cases:")[0]
                     + f"(description too long for a link; paste it from {_body_path(b)})\n")
        url = head + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)
    return url


def fork_repo() -> str:
    ws = _ws()
    return ws.get("fork_repo") or (f"{ws['fork_owner']}/pytorch" if ws.get("fork_owner") else "")


_PUSH_CACHE: dict = {}


def push_state(branch: str, ttl: int = 60) -> dict:
    """{pushed, remote_sha, local_sha, in_sync} for `branch` on the fork."""
    import time
    ws = _ws()
    remote = ws.get("fork_remote")
    if not (remote and branch and gitops.branch_exists(branch)):
        return {"pushed": False, "in_sync": False}
    hit = _PUSH_CACHE.get(branch)
    local = gitops.git("rev-parse", branch)
    if hit and time.time() - hit[0] < ttl and hit[1].get("local_sha") == local:
        return hit[1]
    out = gitops.git("ls-remote", remote, f"refs/heads/{branch}", check=False)
    rsha = out.split()[0] if out.strip() else ""
    st = {"pushed": bool(rsha), "remote_sha": rsha[:12], "local_sha": local, "in_sync": rsha == local}
    _PUSH_CACHE[branch] = (time.time(), st)
    return st


def review_url(b: dict) -> str:
    """Shareable diff of the branch on the user's fork (base..branch). Works once the
    branch is pushed; pushing it also pushes its base commit, so the compare resolves."""
    repo = fork_repo()
    if b.get("kind") == "external":
        return (b.get("pr") or {}).get("url", "") + "/files" if (b.get("pr") or {}).get("url") else ""
    if not repo or not b.get("target_branch") or not b.get("base_sha"):
        return ""
    return f"https://github.com/{repo}/compare/{b['base_sha'][:12]}...{b['target_branch']}"


def links(b: dict) -> dict:
    """Everything the UI needs to let the human share and submit this PR by themselves."""
    ws = _ws()
    return {"review_url": review_url(b), "compare_url": compare_url(b), "push": submit_commands(b)[0],
            "pr_page": (f"https://github.com/{PR_REPO}/compare/{PR_BASE}...{ws['fork_owner']}:{b['target_branch']}"
                        if ws.get("fork_owner") and b.get("target_branch") and b.get("kind") != "external" else ""),
            "gh": submit_commands(b)[1], "fork_owner": ws.get("fork_owner", ""),
            "fork_repo": fork_repo(), "body_file": _body_path(b)}


def _body_path(b: dict) -> str:
    return f"{store.bundles_dir()}/{b['id']}.body.md"


def submit_commands(b: dict) -> list[str]:
    ws = _ws()
    remote, owner = ws.get("fork_remote", "<your-fork-remote>"), ws.get("fork_owner", "<fork-owner>")
    clone = gitops.CLONE
    return [f"git -C {clone} push {remote} {b['target_branch']}",
            f"gh pr create --draft --repo {PR_REPO} --head {owner}:{b['target_branch']} "
            f"--title {json.dumps(b['title'])} --body-file {store.bundles_dir()}/{b['id']}.body.md"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create")
    p.add_argument("--target", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--files", nargs="+", required=True)
    p.add_argument("--force", action="store_true", help="accept warnings")
    for name in ("update", "refresh", "delete", "show"):
        sub.add_parser(name).add_argument("id")
    p = sub.add_parser("remove")
    p.add_argument("id")
    p.add_argument("--files", nargs="+", required=True)
    p = sub.add_parser("set-pr")
    p.add_argument("id")
    p.add_argument("ref")
    p.add_argument("--confirm-head", action="store_true")
    sub.add_parser("list")
    sub.add_parser("file-link").add_argument("test_file")
    sub.add_parser("submit-file").add_argument("test_file")
    sub.add_parser("submit").add_argument("id")
    sub.add_parser("link").add_argument("id")
    sub.add_parser("file-link-pr").add_argument("test_file")
    p = sub.add_parser("record")
    p.add_argument("ref")
    p.add_argument("--files", nargs="+", required=True)
    p.add_argument("--cases", help="comma-separated case ids (default: all cases of the files)")
    p = sub.add_parser("file-set-pr")
    p.add_argument("test_file")
    p.add_argument("ref")
    p.add_argument("--confirm-head", action="store_true")
    a = ap.parse_args()
    try:
        if a.cmd == "create":
            r = create(a.target, a.title, a.files, a.force)
        elif a.cmd == "update":
            r = update(a.id)
        elif a.cmd == "remove":
            r = remove(a.id, a.files)
        elif a.cmd == "set-pr":
            r = set_pr(a.id, a.ref, a.confirm_head)
        elif a.cmd == "refresh":
            r = refresh(a.id)
        elif a.cmd == "delete":
            r = delete(a.id)
        elif a.cmd in ("submit-file", "submit"):
            what = a.test_file if a.cmd == "submit-file" else a.id
            if input(f"Push {what} to your fork and print the prefilled PR page? [y/N] ").strip().lower() != "y":
                return
            r = submit_file(make_slug(what)) if a.cmd == "submit-file" else submit_bundle(what)
        elif a.cmd == "link":
            r = link_pr(store.load_bundle(a.id))
        elif a.cmd == "file-link-pr":
            r = link_file_pr(make_slug(a.test_file))
        elif a.cmd == "record":
            r = record_external(a.ref, a.files, a.cases.split(",") if a.cases else None)
        elif a.cmd == "file-link":
            r = links(single_bundle(make_slug(a.test_file)))
        elif a.cmd == "file-set-pr":
            r = set_file_pr(make_slug(a.test_file), a.ref, a.confirm_head)
        elif a.cmd == "show":
            b = store.load_bundle(a.id)
            r = {"bundle": b, "log": log(b), "pending": pending_commits(b), **links(b)}
        else:
            r = [{k: b.get(k) for k in ("id", "title", "target_branch", "status", "pr")} for b in store.list_bundles()]
    except gitops.GitError as e:
        sys.exit(f"ERROR: {e}")
    print(json.dumps(r, indent=2))
    if isinstance(r, dict) and r.get("ok") and a.cmd in ("submit", "submit-file") and r.get("open_url"):
        nxt = (f"pr_bundle.py link {a.id}" if a.cmd == "submit" else f"pr_bundle.py file-link-pr {a.test_file}")
        print(f"\nOpen in a browser and create the PR yourself:\n  {r['open_url']}"
              + ("" if r.get("linked") or r.get("updated") else f"\nThen fill Fix PR:\n  {nxt}"))
    if isinstance(r, dict) and r.get("ok") and a.cmd == "create":
        print(f"\nNext: click Push to fork on the bundle card, or run: pr_bundle.py submit {r['bundle']['id']}")


if __name__ == "__main__":
    main()
