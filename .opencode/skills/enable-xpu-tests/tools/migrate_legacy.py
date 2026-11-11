#!/usr/bin/env python3
"""One-time import of a legacy (v1) report tree + verdict CSVs into the v2 workspace.

  migrate_legacy.py --legacy-repo ~/upstream/oct --csv 2026WW42_BMG_Daisy_TBD.csv \
      [--csv 2026WW42_BMG_cases_test_torch.csv ...] [--branches daisyden/missing_eager ...] \
      [--no-branches] [--dry-run]

Sources (read-only):
  - verdict CSVs (portability / portability reason / log columns); later --csv wins per case
  - <legacy>/xpu_enable_report/<branch>/manifest.json: reviews, review_comments, file_prompt,
    issue_drafts, status
  - legacy git branches (--branches): each enablement commit after the branch's upstream
    base is mapped to the test files it touches

Writes (v2 layout, see README.md): file.json, cases.json, reviews.json, drafts.json,
results.json (run="imported", old log copied to logs/imported_<name>), and, unless
--no-branches, an agent_<slug>_NNNN branch at the workspace base_sha with the file's
legacy commits cherry-picked (-x). A file whose commits do not apply gets status
"Needs rebase" and keeps the legacy diff in <slug>.legacy.patch.

Imported results never satisfy verify_evidence (no patch hash / junit / snapshot), so
every migrated verdict shows up as needing re-measurement - nothing is faked.
"""
import argparse
import json
import os
import re
import shutil
import subprocess

import gitops
import store
from common import WORKSPACE_JSON, slug as make_slug, test_path

PORT_MAP = {"portable": "Portable", "pass": "Portable", "known issue": "Known Issue",
            "feature gap": "Feature gap", "not applicable": "Not Applicable", "n/a": "Not Applicable",
            "not portable": "Not Applicable"}
KEYWORD_OWNER = {"inductor opinfo": "inductor/test_torchinductor_opinfo.py"}


def norm_port(v: str) -> str:
    return PORT_MAP.get((v or "").strip().lower(), (v or "").strip())


def as_obj(v, default):
    if isinstance(v, str):
        try:
            v = json.loads(v.replace("'", '"')) if v.strip() else default
        except json.JSONDecodeError:
            return default
    return v if isinstance(v, type(default)) else default


def as_list(v) -> list:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        return [x.strip() for x in re.findall(r"[\w: ]+", v.strip("[]").replace("'", "")) if x.strip()] if v else []
    return []


def load_csvs(paths: list[str]) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for p in paths:
        for r in store.read_csv(p):
            reason = r.get("portability reason") or r.get("portability_reason") or ""
            cur = rows.setdefault(str(r["case_id"]), dict(r))
            cur.update({k: v for k, v in r.items() if v})
            if r.get("portability"):
                cur["portability"] = norm_port(r["portability"])
                cur["_reason"] = reason
                cur["_src"] = os.path.basename(p)
    return rows


def load_manifests(report: str) -> dict[str, dict]:
    """test_file -> merged legacy file entry (reviews/comments/prompt/drafts/status/branch)."""
    out: dict[str, dict] = {}
    for b in sorted(os.listdir(report)):
        mp = os.path.join(report, b, "manifest.json")
        if not os.path.isfile(mp) or b.startswith("_"):
            continue
        for f in json.load(open(mp)).get("files", []):
            e = out.setdefault(f["test_file"], {"reviews": {}, "comments": {}, "prompt": "", "drafts": [],
                                                "status": "", "legacy_branches": []})
            e["legacy_branches"].append(b)
            e["reviews"].update(as_obj(f.get("reviews"), {}))
            e["comments"].update(as_obj(f.get("review_comments"), {}))
            e["prompt"] = e["prompt"] or f.get("file_prompt", "")
            e["drafts"] += f.get("issue_drafts") or []
            if f.get("status") and len(f["status"]) > len(e["status"]):
                e["status"] = f["status"]
    return out


def find_log(legacy: str, rel: str) -> str:
    if not rel or rel == "n/a":
        return ""
    for cand in (rel, os.path.join("test", rel)):
        p = os.path.join(legacy, cand)
        if os.path.isfile(p):
            return p
    name = os.path.basename(rel)
    for d in ("test/logs", "agent_space", "xpu_enable_report"):
        for root, _, files in os.walk(os.path.join(legacy, d)):
            if name in files:
                return os.path.join(root, name)
    return ""


def log_status(text: str, test_name: str) -> tuple[str, str]:
    xpu = re.sub(r"_cuda(?=_|$)", "_xpu", test_name)
    for line in text.splitlines():
        if xpu in line or test_name in line:
            m = re.search(r"\b(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\b", line) or \
                re.search(r"\.\.\. (ok|FAIL|ERROR|skipped|expected failure)", line)
            if m:
                s = {"ok": "PASSED", "FAIL": "FAILED", "skipped": "SKIPPED",
                     "expected failure": "XFAIL"}.get(m.group(1), m.group(1))
                return s, line.strip()[:300]
    return "UNKNOWN", ""


def legacy_commits(legacy: str, branches: list[str], csv_files: set[str],
                   csv_names: dict[str, str]) -> dict[str, list[tuple[str, str, list[str]]]]:
    """test_file -> [(sha, subject, paths)]: each legacy enablement commit split by owner.

    A touched CSV test file owns its own path. Every other touched path (op_db,
    helper test modules) goes to: the CSV files named in the subject, else the single
    CSV file touched by the commit, else the CSV file owning a test named in the
    subject, else a keyword rule."""
    def g(*a):
        return subprocess.check_output(["git", "-C", legacy, *a], text=True).strip()
    by_base = {}
    for tf in csv_files:
        by_base.setdefault(os.path.basename(tf), []).append(tf)
    out: dict[str, list] = {}
    for br in branches:
        own = []
        for c in g("log", "--format=%H", br).splitlines():
            if re.search(r"\(#\d+\)$", g("log", "-1", "--format=%s", c)):
                break
            own.append(c)
        for c in reversed(own):
            subj = g("log", "-1", "--format=%s", c)
            paths = [p for p in g("show", "--name-only", "--format=", c).splitlines() if p]
            owners: dict[str, list[str]] = {}
            extra = []
            for p in paths:
                tf = p[5:] if p.startswith("test/") else ""
                (owners.setdefault(tf, []).append(p) if tf in csv_files else extra.append(p))
            if extra:
                named = [f for m in re.findall(r"\b(test_\w+\.py)\b", subj) for f in by_base.get(m, [])]
                named = [f for f in named if f not in owners] or named
                tgt = named or (list(owners)[:1] if len(owners) == 1 else [])
                if not tgt:
                    for t in re.findall(r"\b(test_\w+)\b", subj):
                        hit = next((f for n, f in csv_names.items() if n.startswith(t)), None)
                        if hit:
                            tgt = [hit]
                            break
                if not tgt:
                    tgt = [v for k, v in KEYWORD_OWNER.items() if k in subj] or list(owners)[:1]
                for t in tgt:
                    owners.setdefault(t, []).extend(extra)
            for tf, ps in owners.items():
                out.setdefault(tf, []).append((c, subj, ps))
    return out


def build_branch(slug: str, base: str, commits: list[tuple[str, str, list[str]]],
                 legacy: str) -> tuple[str, str, list[str]]:
    """Agent branch at base with each legacy commit's owned paths re-applied (3-way).
    Commits that apply are kept; the rest are returned as `failed` (legacy shas) so
    their diff can be saved for a manual rebase. Returns (branch, status, failed)."""
    if "legacy" not in gitops.git("remote").split():
        gitops.git("remote", "add", "legacy", legacy)
    gitops.git("fetch", "-q", "--no-tags", "legacy", *[c for c, _, _ in commits])
    branch = gitops.find_agent_branch(slug)
    if branch:
        return branch, "exists", []
    branch = gitops.new_agent_branch_name(slug)
    gitops.switch(branch, create_from=base)
    failed, errs = [], []
    try:
        for c, subj, paths in commits:
            patch = gitops.git("diff", f"{c}^", c, "--", *paths, raw=True)
            if not patch:
                continue
            p = subprocess.run(["git", "apply", "--3way", "--index", "-"], cwd=gitops.CLONE, input=patch,
                               capture_output=True, text=True)
            if p.returncode != 0:
                gitops.git("reset", "-q", "--hard", "HEAD")
                failed.append(c)
                errs.append(f"{c[:9]} ({subj[:50]})")
                continue
            gitops.git("commit", "-q", "-m", f"{subj}\n\n(imported from legacy commit {c[:12]})")
    finally:
        gitops.restore(base)
    n = len(gitops.commits(base, branch))
    if not n:
        gitops.git("branch", "-D", branch)
        branch = ""
    if failed:
        return branch, f"Needs rebase: {len(failed)} legacy commit(s) did not apply: " + "; ".join(errs), failed
    if not branch:
        return "", "Imported (legacy change already in base)", []
    return branch, f"Imported ({n} commit(s))", []


def apply_patch_file(slug: str, base: str, tf: str, path: str) -> tuple[str, str]:
    branch = gitops.find_agent_branch(slug)
    if branch:
        return branch, "exists"
    branch = gitops.new_agent_branch_name(slug)
    gitops.switch(branch, create_from=base)
    err = ""
    try:
        p = subprocess.run(["git", "apply", "--3way", "--index", path], cwd=gitops.CLONE,
                           capture_output=True, text=True)
        if p.returncode != 0:
            err = ((p.stderr or p.stdout).strip().splitlines() or ["apply failed"])[-1]
            gitops.git("reset", "-q", "--hard", "HEAD")
        else:
            gitops.git("commit", "-q", "-m", f"Enable {tf} on XPU\n\n(imported from legacy patch {os.path.basename(path)})")
    finally:
        gitops.restore(base)
    if err:
        gitops.git("branch", "-D", branch)
        shutil.copy2(path, os.path.join(store.fdir(slug), f"{slug}.legacy.patch"))
        return "", f"Needs rebase: {err}"
    return branch, "Imported (1 commit from patch)"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--legacy-repo", required=True)
    ap.add_argument("--csv", action="append", required=True)
    ap.add_argument("--branches", nargs="*", default=[])
    ap.add_argument("--patch", action="append", default=[], metavar="TEST_FILE=PATH",
                    help="uncommitted legacy change for a file (used when no legacy commit owns it)")
    ap.add_argument("--no-branches", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    legacy = os.path.abspath(os.path.expanduser(a.legacy_repo))
    rows = load_csvs([os.path.join(legacy, p) if not os.path.isabs(p) else p for p in a.csv])
    mans = load_manifests(os.path.join(legacy, "xpu_enable_report"))
    base = json.load(open(WORKSPACE_JSON))["base_sha"]
    by_file: dict[str, list[dict]] = {}
    for r in rows.values():
        if r.get("portability"):
            by_file.setdefault(r["test_file"], []).append(r)
    names = {r.get("test_name", ""): r["test_file"] for rs in by_file.values() for r in rs}
    commits = legacy_commits(legacy, a.branches, set(by_file) | set(mans), names) if a.branches else {}
    patches = {}
    for kv in a.patch:
        tf, _, path = kv.partition("=")
        patches[tf] = os.path.join(legacy, path) if not os.path.isabs(path) else path
    summary = []
    for tf in sorted(set(by_file) | set(mans)):
        s = make_slug(tf)
        cases = by_file.get(tf, [])
        man = mans.get(tf, {})
        line = {"file": tf, "cases": len(cases),
                "commits": [(c[:9], ps) for c, _, ps in commits.get(tf, [])]}
        if a.dry_run:
            summary.append(line)
            continue
        status = "Imported" if cases else "Imported (no verdicts)"
        branch = ""
        os.makedirs(os.path.join(store.fdir(s), "logs"), exist_ok=True)
        failed = []
        if commits.get(tf) and not a.no_branches:
            branch, status, failed = build_branch(s, base, commits[tf], legacy)
        elif tf in patches and not a.no_branches:
            branch, status = apply_patch_file(s, base, tf, patches[tf])
        if failed:
            diff = "".join(subprocess.check_output(["git", "-C", legacy, "diff", f"{c}^", c, "--", *ps], text=True)
                           for c, _, ps in commits[tf] if c in failed)
            with open(os.path.join(store.fdir(s), f"{s}.legacy.patch"), "w") as fh:
                fh.write(diff)
        store.init_file(tf, source=test_path(tf), base_sha=base, agent_branch=branch, status=status,
                        file_prompt=man.get("prompt", ""), legacy=man.get("legacy_branches", []),
                        legacy_status=man.get("status", ""))
        store.update_cases(s, {r["case_id"]: {
            "class_name": r.get("class_name", ""), "test_name": r.get("test_name", ""),
            "portability": r["portability"], "reason": r.get("_reason", ""),
            "issue_number": r.get("issue_number", ""), "issue_url": r.get("issue_url", ""),
            "fix_pr": r.get("fix_pr", ""), "imported_from": r.get("_src", "")} for r in cases})
        ids = {r["case_id"] for r in cases}
        rv = {k: v for k, v in man.get("reviews", {}).items() if k in ids}
        cm = {k: v for k, v in man.get("comments", {}).items() if k in ids}
        if rv or cm:
            for k, v in rv.items():
                store.set_reviews(s, [k], v)
            for k, v in cm.items():
                store.set_reviews(s, [k], None, v)
        for i, d in enumerate(man.get("drafts", [])):
            labels = [x.strip() for x in as_list(d.get("labels"))]
            labels = ["test: ut" if x == "module: xpu" else x for x in labels]
            num = str(d.get("number") or "")
            store.upsert_draft(s, {"id": f"d{i + 1}", "placeholder": d.get("placeholder") or "",
                                   "title": d.get("title", ""), "body": d.get("body", ""),
                                   "labels": sorted(set(labels)), "cases": as_list(d.get("cases")),
                                   "verdict": d.get("verdict", ""),
                                   **({"number": int(num), "url": d.get("url", "")} if num.isdigit() else {})})
        logdir = os.path.join(store.fdir(s), "logs")
        entries, copied = {}, {}
        for r in cases:
            src = find_log(legacy, r.get("log", ""))
            if not src:
                continue
            if src not in copied:
                dst = os.path.join(logdir, "imported_" + os.path.basename(src))
                shutil.copy2(src, dst)
                copied[src] = (os.path.relpath(dst, store.fdir(s)), open(src, errors="replace").read())
            rel, text = copied[src]
            st, ln = log_status(text, r.get("test_name", ""))
            entries[r["case_id"]] = {"run": "imported", "nodeid": r.get("test_name", ""), "status": st,
                                     "reason": ln, "error_excerpt": "", "log": rel, "patch_sha": "legacy",
                                     "src_snapshot": ""}
        if entries:
            store.add_results(s, entries)
        if branch:
            import file_branch
            file_branch.regen_patch(tf)
        line.update(status=status, branch=branch, logs=len(entries), reviews=len(rv), comments=len(cm),
                    drafts=len(man.get("drafts", [])))
        summary.append(line)
    for line in summary:
        print(json.dumps(line))


if __name__ == "__main__":
    main()
