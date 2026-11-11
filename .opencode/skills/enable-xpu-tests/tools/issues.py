"""gh helpers for intel/torch-xpu-ops issues, with a 1-day cache in _issues.json."""
import json
import os
import subprocess
import tempfile
import time

import store
from common import ISSUE_REPO

TTL = 24 * 3600


def _gh(args: list[str], timeout: int = 40) -> tuple[int, str, str]:
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:  # noqa: BLE001
        return 1, "", str(e)


def meta(number, repo: str = ISSUE_REPO, refresh: bool = False) -> dict:
    """{number, state: OPEN|CLOSED|'' (unknown), labels: [...], title, wontfix: bool}."""
    key = f"{repo}#{int(number)}"
    cache = store.issue_cache()
    hit = cache.get(key)
    if hit and not refresh and time.time() - hit.get("_t", 0) < TTL:
        return hit
    rc, out, _ = _gh(["issue", "view", str(number), "--repo", repo, "--json", "number,state,labels,title"])
    if rc != 0:
        return {"number": int(number), "state": "", "labels": [], "title": "", "wontfix": False}
    d = json.loads(out)
    labels = [lab["name"] for lab in d.get("labels", [])]
    rec = {"number": d["number"], "state": d["state"].upper(), "labels": labels,
           "title": d.get("title", ""), "wontfix": "wontfix" in labels, "_t": time.time()}
    cache[key] = rec
    store.save_issue_cache(cache)
    return rec


def citable(m: dict) -> str:
    """'open' | 'wontfix' | 'stale' (closed, not wontfix) | 'unknown'."""
    if not m.get("state"):
        return "unknown"
    if m.get("wontfix"):
        return "wontfix"
    return "open" if m["state"] == "OPEN" else "stale"


def search(query: str, label: str = "", repo: str = ISSUE_REPO, limit: int = 10) -> list[dict]:
    args = ["search", "issues", query, "--repo", repo, "--limit", str(limit),
            "--json", "number,title,state,labels,url"]
    if label:
        args += ["--label", label]
    rc, out, _ = _gh(args)
    if rc != 0:
        return []
    return [{"number": h["number"], "title": h["title"], "state": h["state"].upper(),
             "url": h["url"], "labels": [lab["name"] for lab in h.get("labels", [])]}
            for h in json.loads(out or "[]")]


def find_by_title(title: str, repo: str = ISSUE_REPO) -> dict | None:
    rc, out, _ = _gh(["issue", "list", "--repo", repo, "--state", "all", "--search",
                      f'"{title}" in:title', "--limit", "20", "--json", "number,title,url,state"])
    if rc != 0:
        return None
    hits = json.loads(out or "[]")
    exact = [h for h in hits if h["title"].strip().lower() == title.strip().lower()]
    return (exact or hits or [None])[0]


def labels_of_repo(repo: str = ISSUE_REPO) -> set[str]:
    rc, out, _ = _gh(["label", "list", "--repo", repo, "--limit", "300", "--json", "name", "-q", ".[].name"])
    return {ln.strip() for ln in out.splitlines() if ln.strip()} if rc == 0 else set()


def create(title: str, body: str, labels: list[str], repo: str = ISSUE_REPO) -> tuple[int, str]:
    valid = labels_of_repo(repo)
    use = [lab for lab in labels if lab in valid] if valid else labels
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(body)
        path = fh.name
    try:
        cmd = ["issue", "create", "--repo", repo, "--title", title, "--body-file", path]
        for lab in use:
            cmd += ["--label", lab]
        rc, out, err = _gh(cmd, timeout=90)
        if rc != 0:
            raise RuntimeError((err or out or "gh issue create failed").strip())
    finally:
        os.unlink(path)
    url = out.strip().splitlines()[-1].strip()
    return int(url.rstrip("/").rsplit("/", 1)[-1]), url
