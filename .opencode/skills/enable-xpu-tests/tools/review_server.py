#!/usr/bin/env python3
"""Serve an XPU-enablement report tree and file approved draft issues.

Static report pages (from report_gen.py) cannot call `gh` directly. This server
serves the report tree over HTTP and exposes POST /api/submit_issue, which the
"Submit to intel/torch-xpu-ops" button on each file's report.html calls once the
reviewer has approved a draft. On submit it:

  1. files the issue with `gh issue create --repo <repo>`,
  2. backfills the real issue number into:
       - the drafted issue entry in the manifest (adds "number"),
       - the file's committed patch (.../issues/PLACEHOLDER -> real number),
       - the source test file in the working tree (same replacement),
       - the CSV rows for the draft's cases (PLACEHOLDER in portability reason).

Only the Submit button files issues; nothing is posted automatically.

Usage:
  review_server.py --root xpu_enable_report/<branch> --csv BATCH.csv \
      --manifest manifest.json [--repo intel/torch-xpu-ops] [--port 8000] \
      [--src-root test] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import tempfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Handler(SimpleHTTPRequestHandler):
    cfg: dict = {}

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/api/submit_issue":
            self._json(404, {"ok": False, "error": "unknown endpoint"})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
            result = submit_issue(self.cfg, payload)
            self._json(200, result)
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": str(e)})


def _run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True).strip()


def _amend_backfilled_commit(cfg: dict, info: dict, tf: str) -> str | None:
    """Amend the enablement commit so its committed source carries the real
    issue number, then refresh the patch, manifest commit field and report.

    Only runs when the server is started with --amend. Guarded: the file's
    worktree change must be exactly the PLACEHOLDER->number backfill, HEAD must
    already touch this source file, and HEAD must be unpushed. Returns the new
    short hash, or None if skipped.
    """
    src = info.get("source") or os.path.join(cfg["src_root"], tf)
    if not os.path.exists(src):
        return None
    # Only amend if HEAD actually modified this source file.
    changed = _run(["git", "diff", "--name-only", "HEAD~1", "HEAD"]).splitlines()
    if not any(os.path.basename(src) == os.path.basename(c) for c in changed):
        return None
    _run(["git", "add", "--", src])
    _run(["git", "commit", "--amend", "--no-edit"])
    new_hash = _run(["git", "rev-parse", "--short", "HEAD"])
    # Refresh the canonical patch from the amended commit.
    patch = info.get("patch")
    if patch:
        with open(patch, "w") as fh:
            fh.write(_run(["git", "show", "HEAD"]) + "\n")
    # Update the manifest commit field so regenerated reports show the new hash.
    manifest = json.load(open(cfg["manifest"]))
    for f in manifest.get("files", []):
        if f.get("test_file") == tf:
            f["commit"] = new_hash
    json.dump(manifest, open(cfg["manifest"], "w"), indent=2)
    return new_hash


def _regenerate_report(cfg: dict) -> bool:
    """Re-run report_gen.py so the served tree reflects backfilled files."""
    gen = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_gen.py")
    if not (cfg.get("branch") and os.path.exists(gen)):
        return False
    cmd = ["python", gen, "--csv", cfg["csv"], "--manifest", cfg["manifest"],
           "--branch", cfg["branch"], "--out", cfg["root"],
           "--src-root", cfg["src_root"], "--files-with-manifest-only"]
    subprocess.check_call(cmd)
    return True


def file_issue(repo: str, title: str, body: str, labels: list[str]) -> tuple[int, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(body)
        body_path = fh.name
    try:
        cmd = ["gh", "issue", "create", "--repo", repo, "--title", title,
               "--body-file", body_path]
        for lab in labels:
            cmd += ["--label", lab]
        url = _run(cmd)
    finally:
        os.unlink(body_path)
    number = int(url.rstrip("/").rsplit("/", 1)[-1])
    return number, url


def _backfill_text(path: str, number: int) -> bool:
    if not path or not os.path.exists(path):
        return False
    text = open(path).read()
    if "issues/PLACEHOLDER" not in text:
        return False
    open(path, "w").write(text.replace("issues/PLACEHOLDER", f"issues/{number}"))
    return True


def _backfill_csv(csv_path: str, cases: list[str], number: int, url: str = "") -> int:
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    want = {str(c) for c in cases}
    n = 0
    for r in rows:
        if r.get("case_id", "") in want:
            for col in ("portability reason", "portability_reason"):
                if col in r and "PLACEHOLDER" in (r[col] or ""):
                    r[col] = r[col].replace("PLACEHOLDER", str(number))
                    n += 1
            if "issue_number" in r and not (r.get("issue_number") or "").strip():
                r["issue_number"] = str(number)
                n += 1
            if url and "issue_url" in r and not (r.get("issue_url") or "").strip():
                r["issue_url"] = url
                n += 1
    if n:
        with open(csv_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    return n


def _backfill_island(path: str, number: int, url: str,
                     cases: list[str], commit: str = "") -> bool:
    """Backfill the embedded JSON data island in a report.html.

    Updates drafts (number/url), the drafted cases' issue_number/issue_url and
    PLACEHOLDER in their portability reason, mirrored patch/log text, and
    optionally the commit hash. Returns True if anything changed.
    """
    import re
    text = open(path).read()
    m = re.search(r'(<script[^>]*id="report-data"[^>]*>)(.*?)(</script>)',
                  text, re.S)
    if not m:
        return False
    data = json.loads(m.group(2).replace("<\\/", "</"))
    want = {str(c) for c in cases}
    changed = False
    for d in data.get("drafts", []):
        dc = {str(c) for c in d.get("cases", [])}
        if dc & want and not d.get("number"):
            d["number"], d["url"] = number, url
            changed = True
    for c in data.get("cases", []):
        if str(c.get("case_id")) not in want:
            continue
        if "issue_number" in c and not str(c.get("issue_number") or "").strip():
            c["issue_number"] = str(number); changed = True
        if "issue_url" in c and not str(c.get("issue_url") or "").strip():
            c["issue_url"] = url; changed = True
        pr = c.get("portability reason")
        if pr and "PLACEHOLDER" in pr:
            c["portability reason"] = pr.replace("PLACEHOLDER", str(number))
            changed = True
    for art in ("patch", "log"):
        t = (data.get(art) or {}).get("text", "")
        if "issues/PLACEHOLDER" in t:
            data[art]["text"] = t.replace("issues/PLACEHOLDER", f"issues/{number}")
            changed = True
    if commit and data.get("commit") != commit:
        data["commit"] = commit
        changed = True
    if not changed:
        return False
    island = json.dumps(data).replace("</", "<\\/")
    open(path, "w").write(text[:m.start(2)] + island + text[m.end(2):])
    return True


def _backfill_served_tree(root: str, tf: str, number: int,
                          url: str = "", cases: list[str] | None = None) -> int:
    """Backfill PLACEHOLDER in the served report copies for a test file.

    report.html carries a JSON data island and is backfilled structurally via
    _backfill_island. Sibling .patch/.log artifacts are plain-text rewrites of
    the URL token (issues/PLACEHOLDER) and bare ref (#PLACEHOLDER). Matches any
    file under a folder named <tf>.
    """
    n = 0
    cases = cases or []
    if not root or not os.path.isdir(root):
        return n
    for dirpath, _dirs, names in os.walk(root):
        if os.path.basename(dirpath) != tf:
            continue
        for name in names:
            path = os.path.join(dirpath, name)
            if name.endswith(".html"):
                if _backfill_island(path, number, url, cases):
                    n += 1
                continue
            if not name.endswith((".patch", ".log")):
                continue
            text = open(path).read()
            new = text.replace("issues/PLACEHOLDER", f"issues/{number}") \
                      .replace("#PLACEHOLDER", f"#{number}")
            if new != text:
                open(path, "w").write(new)
                n += 1
    return n


def submit_issue(cfg: dict, payload: dict) -> dict:
    manifest = json.load(open(cfg["manifest"]))
    files = {f.get("test_file"): f for f in manifest.get("files", [])}
    tf = payload.get("file")
    idx = payload.get("idx")
    info = files.get(tf)
    if info is None:
        return {"ok": False, "error": f"no manifest entry for {tf}"}
    drafts = info.get("issue_drafts", [])
    if not (0 <= idx < len(drafts)):
        return {"ok": False, "error": f"draft index {idx} out of range"}
    draft = drafts[idx]
    if draft.get("number"):
        return {"ok": False, "error": f"already filed as #{draft['number']}"}

    title = payload.get("title") or draft.get("title", "")
    body = payload.get("body") or draft.get("body", "")
    labels = payload.get("labels") or draft.get("labels", [])
    cases = [str(c) for c in draft.get("cases", [])]

    if cfg.get("dry_run"):
        number, url = 0, "(dry-run, not filed)"
    else:
        number, url = file_issue(cfg["repo"], title, body, labels)

    draft["number"] = number
    draft["url"] = url
    draft["title"] = title
    draft["body"] = body
    draft["labels"] = labels
    json.dump(manifest, open(cfg["manifest"], "w"), indent=2)

    backfilled = []
    if number:
        if info.get("patch") and _backfill_text(info["patch"], number):
            backfilled.append("patch")
        src = info.get("source") or os.path.join(cfg["src_root"], tf)
        if _backfill_text(src, number):
            backfilled.append("source")
        if info.get("log") and _backfill_text(info["log"], number):
            backfilled.append("log")
        if _backfill_csv(cfg["csv"], cases, number, url):
            backfilled.append("csv")
        if _backfill_served_tree(cfg.get("root", ""), tf, number, url, cases):
            backfilled.append("served")
        if cfg.get("amend"):
            new_hash = _amend_backfilled_commit(cfg, info, tf)
            if new_hash:
                backfilled.append(f"amended@{new_hash}")
        if cfg.get("regen") and _regenerate_report(cfg):
            backfilled.append("regenerated")

    return {"ok": True, "number": number, "url": url, "backfilled": backfilled}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="report dir to serve")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--repo", default="intel/torch-xpu-ops")
    ap.add_argument("--src-root", default="test")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--bind", default="127.0.0.1",
                    help="address to bind (e.g. 0.0.0.0 to allow LAN access)")
    ap.add_argument("--dry-run", action="store_true",
                    help="do everything except actually calling gh")
    ap.add_argument("--branch", default=None,
                    help="branch slug for report regeneration (enables --regen)")
    ap.add_argument("--amend", action="store_true",
                    help="after backfill, amend the enablement commit so its "
                         "committed source carries the real issue number, and "
                         "refresh the patch + manifest commit field")
    ap.add_argument("--regen", action="store_true",
                    help="after backfill, re-run report_gen.py so the served "
                         "report reflects the backfilled files (needs --branch)")
    args = ap.parse_args()

    Handler.cfg = {
        "csv": os.path.abspath(args.csv),
        "manifest": os.path.abspath(args.manifest),
        "repo": args.repo,
        "src_root": args.src_root,
        "dry_run": args.dry_run,
        "root": os.path.abspath(args.root),
        "branch": args.branch,
        "amend": args.amend,
        "regen": args.regen,
    }
    handler = partial(Handler, directory=os.path.abspath(args.root))
    srv = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"Serving {args.root} at http://{args.bind}:{args.port}/index.html")
    print(f"Submitting issues to {args.repo}"
          + (" (dry-run)" if args.dry_run else ""))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
