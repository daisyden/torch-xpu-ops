#!/usr/bin/env python3
"""Single reader/writer of the report tree (the source of truth; the CSV is input only).

Layout (REPORT = $XPU_ENABLE_WS/xpu_enable_report):

  <slug>/file.json      {test_file, slug, source, base_sha, agent_branch, commit,
                         patch_sha, status, file_prompt, bundle, pr_title, pr_body}
  <slug>/cases.json     {case_id: {class_name, test_name, portability, reason,
                         issue_number, issue_url, draft_id, fix_pr, cuda_only, cited_existing}}
  <slug>/reviews.json   {verdicts: {cid: v}, comments: {cid: text}, history: [...]}
  <slug>/results.json   {cid: [{run, nodeid, status, reason, error_excerpt, log,
                         patch_sha, src_snapshot, at}]}
  <slug>/drafts.json    [{id, placeholder, title, body, labels, cases, verdict, number, url}]
  <slug>/<slug>.patch   git diff base..agent_branch
  <slug>/logs/          pytest logs, junit xml, source snapshots
  _prs/<id>.json        PR bundles (see pr_bundle.py)
  _issues.json          gh issue-state cache

CLI (for the agent):
  store.py show <slug>
  store.py set-case <slug> <case_id> key=value ...
  store.py add-draft <slug> draft.json
  store.py list
"""
import argparse
import csv
import json
import os
import tempfile
import threading
import time

from common import REPORT, slug as make_slug

_LOCK = threading.RLock()

FILE_DEFAULTS = {"test_file": "", "slug": "", "source": "", "base_sha": "",
                 "agent_branch": "", "commit": "", "patch_sha": "", "status": "",
                 "file_prompt": "", "bundle": "", "pr_title": "", "pr_body": "", "pr_body_generated": ""}
CASE_FIELDS = ["class_name", "test_name", "portability", "reason", "issue_number",
               "issue_url", "draft_id", "fix_pr", "cuda_only", "cpu_only", "cited_existing"]


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, type(default)) else default


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def fdir(slug: str, root: str = REPORT) -> str:
    return os.path.join(root, slug)


def list_slugs(root: str = REPORT) -> list[str]:
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root)
                  if not d.startswith("_") and os.path.isfile(os.path.join(root, d, "file.json")))


# ---- file.json -----------------------------------------------------------

def load_file(slug: str, root: str = REPORT) -> dict:
    d = dict(FILE_DEFAULTS)
    d.update(_read(os.path.join(fdir(slug, root), "file.json"), {}))
    return d


def init_file(test_file: str, root: str = REPORT, **fields) -> dict:
    s = make_slug(test_file)
    with _LOCK:
        d = load_file(s, root)
        d.update({"test_file": test_file, "slug": s}, **fields)
        _write(os.path.join(fdir(s, root), "file.json"), d)
        os.makedirs(os.path.join(fdir(s, root), "logs"), exist_ok=True)
    return d


def update_file(slug: str, root: str = REPORT, **fields) -> dict:
    with _LOCK:
        d = load_file(slug, root)
        d.update(fields)
        _write(os.path.join(fdir(slug, root), "file.json"), d)
    return d


def patch_path(slug: str, root: str = REPORT) -> str:
    return os.path.join(fdir(slug, root), f"{slug}.patch")


# ---- cases.json ----------------------------------------------------------

def load_cases(slug: str, root: str = REPORT) -> dict:
    return _read(os.path.join(fdir(slug, root), "cases.json"), {})


def update_cases(slug: str, updates: dict, root: str = REPORT) -> dict:
    """Merge {case_id: {field: value}} into cases.json (fields are merged, not replaced)."""
    with _LOCK:
        cases = load_cases(slug, root)
        for cid, fields in updates.items():
            cases.setdefault(str(cid), {}).update(fields)
        _write(os.path.join(fdir(slug, root), "cases.json"), cases)
    return cases


def all_case_ids(root: str = REPORT) -> set[str]:
    out = set()
    for s in list_slugs(root):
        out.update(load_cases(s, root))
    return out


# ---- reviews.json --------------------------------------------------------

def load_reviews(slug: str, root: str = REPORT) -> dict:
    r = _read(os.path.join(fdir(slug, root), "reviews.json"), {})
    for k, default in (("verdicts", {}), ("comments", {}), ("history", [])):
        if not isinstance(r.get(k), type(default)):
            r[k] = default
    return r


def set_reviews(slug: str, case_ids, verdict=None, comment=None, root: str = REPORT) -> int:
    """Set a verdict and/or comment on cases. None leaves that field untouched."""
    with _LOCK:
        r = load_reviews(slug, root)
        for cid in case_ids:
            cid = str(cid)
            if verdict is not None:
                if verdict:
                    r["verdicts"][cid] = verdict
                else:
                    r["verdicts"].pop(cid, None)
            if comment is not None:
                if comment.strip():
                    r["comments"][cid] = comment
                else:
                    r["comments"].pop(cid, None)
        _write(os.path.join(fdir(slug, root), "reviews.json"), r)
    return len(list(case_ids))


def resolve_rework(slug: str, case_ids, outcome: str, root: str = REPORT) -> int:
    """Mark cases Confirmed and move their comment (+ the file prompt) into history."""
    with _LOCK:
        r = load_reviews(slug, root)
        prompt = load_file(slug, root).get("file_prompt", "")
        n = 0
        for cid in map(str, case_ids):
            r["history"].append({"case_id": cid, "comment": r["comments"].pop(cid, ""),
                                 "prompt": prompt, "outcome": outcome, "at": now()})
            r["verdicts"][cid] = "Confirmed"
            n += 1
        _write(os.path.join(fdir(slug, root), "reviews.json"), r)
        cases = load_cases(slug, root)
        if prompt and cases and all(r["verdicts"].get(c) == "Confirmed" for c in cases):
            update_file(slug, root, file_prompt="")
    return n


# ---- results.json --------------------------------------------------------

def load_results(slug: str, root: str = REPORT) -> dict:
    return _read(os.path.join(fdir(slug, root), "results.json"), {})


def add_results(slug: str, entries: dict, root: str = REPORT) -> None:
    """Append {case_id: entry} results (history is kept; latest is last)."""
    with _LOCK:
        res = load_results(slug, root)
        for cid, e in entries.items():
            e.setdefault("at", now())
            res.setdefault(str(cid), []).append(e)
        _write(os.path.join(fdir(slug, root), "results.json"), res)


def latest(results: list, runs=None, patch_sha=None):
    for e in reversed(results or []):
        if runs and e.get("run") not in runs:
            continue
        if patch_sha is not None and e.get("patch_sha") != patch_sha:
            continue
        return e
    return None


# ---- drafts.json ---------------------------------------------------------

def load_drafts(slug: str, root: str = REPORT) -> list:
    return _read(os.path.join(fdir(slug, root), "drafts.json"), [])


def save_drafts(slug: str, drafts: list, root: str = REPORT) -> None:
    with _LOCK:
        _write(os.path.join(fdir(slug, root), "drafts.json"), drafts)


def upsert_draft(slug: str, draft: dict, root: str = REPORT) -> dict:
    with _LOCK:
        drafts = load_drafts(slug, root)
        if not draft.get("id"):
            draft["id"] = f"d{len(drafts) + 1}"
        for i, d in enumerate(drafts):
            if d.get("id") == draft["id"]:
                drafts[i] = {**d, **draft}
                break
        else:
            drafts.append(draft)
        save_drafts(slug, drafts, root)
        update_cases(slug, {c: {"draft_id": draft["id"]} for c in draft.get("cases", [])}, root)
    return draft


# ---- PR bundles + issue cache -------------------------------------------

def bundles_dir(root: str = REPORT) -> str:
    return os.path.join(root, "_prs")


def list_bundles(root: str = REPORT) -> list[dict]:
    d = bundles_dir(root)
    if not os.path.isdir(d):
        return []
    return [_read(os.path.join(d, f), {}) for f in sorted(os.listdir(d)) if f.endswith(".json")]


def load_bundle(bid: str, root: str = REPORT) -> dict:
    return _read(os.path.join(bundles_dir(root), f"{bid}.json"), {})


def save_bundle(b: dict, root: str = REPORT) -> None:
    with _LOCK:
        _write(os.path.join(bundles_dir(root), f"{b['id']}.json"), b)


def delete_bundle(bid: str, root: str = REPORT) -> None:
    for ext in (".json", ".body.md"):
        p = os.path.join(bundles_dir(root), bid + ext)
        if os.path.exists(p):
            os.remove(p)


def issue_cache(root: str = REPORT) -> dict:
    return _read(os.path.join(root, "_issues.json"), {})


def save_issue_cache(cache: dict, root: str = REPORT) -> None:
    with _LOCK:
        _write(os.path.join(root, "_issues.json"), cache)


# ---- CSV (read-only) -----------------------------------------------------

def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _coerce(v: str):
    if v in ("true", "false"):
        return v == "true"
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=REPORT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("show")
    p.add_argument("slug")
    p = sub.add_parser("set-case")
    p.add_argument("slug")
    p.add_argument("case_id")
    p.add_argument("kv", nargs="+", help="field=value (fields: %s)" % ", ".join(CASE_FIELDS))
    p = sub.add_parser("add-draft")
    p.add_argument("slug")
    p.add_argument("json_file")
    a = ap.parse_args()
    if a.cmd == "list":
        for s in list_slugs(a.root):
            f = load_file(s, a.root)
            print(f"{s}\t{f['test_file']}\t{f['status']}\t{f['agent_branch']}\t{len(load_cases(s, a.root))} cases")
    elif a.cmd == "show":
        print(json.dumps({"file": load_file(a.slug, a.root), "cases": load_cases(a.slug, a.root),
                          "reviews": load_reviews(a.slug, a.root),
                          "drafts": load_drafts(a.slug, a.root)}, indent=2))
    elif a.cmd == "set-case":
        fields = {}
        for kv in a.kv:
            k, _, v = kv.partition("=")
            if k not in CASE_FIELDS:
                raise SystemExit(f"unknown field {k!r}; allowed: {CASE_FIELDS}")
            fields[k] = _coerce(v)
        update_cases(a.slug, {a.case_id: fields}, a.root)
    elif a.cmd == "add-draft":
        with open(a.json_file, encoding="utf-8") as fh:
            print(json.dumps(upsert_draft(a.slug, json.load(fh), a.root), indent=2))


if __name__ == "__main__":
    main()
