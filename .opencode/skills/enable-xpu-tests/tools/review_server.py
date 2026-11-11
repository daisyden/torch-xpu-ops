#!/usr/bin/env python3
"""Review server: serves tools/ui/ and a JSON API over the report tree.

  review_server.py --csv NEW.csv [--port 8100] [--bind 0.0.0.0] [--dry-run]

All data is read live from $XPU_ENABLE_WS/xpu_enable_report on every request; the
input CSV is only read (to join display columns such as xpu_message) and to build
the exported CSV, which is written as a NEW file next to it.

GET  /api/data                      index: files, summaries, bundles
GET  /api/file?slug=S               one file: cases (+CSV columns, latest result,
                                    evidence problems), drafts, patch, prompt, reviews
GET  /api/text?slug=S&kind=source|log&path=P
POST /api/set_review        {slug, case_ids, value?, comment?}
POST /api/set_file_prompt   {slug, prompt}
POST /api/set_case_fields   {slug, case_ids, field, value}
POST /api/save_patch        {slug, text}            re-applies onto the agent branch
POST /api/submit_issue      {slug, id, title, body, labels}
POST /api/backfill_issue    {slug, id, issue}
POST /api/match_issue       {title}
POST /api/reject_draft      {slug, id}
POST /api/export_csv        {}
POST /api/pr/preflight      {files}
POST /api/pr/create         {files, target, title, force}
POST /api/pr/update|refresh|delete {id}
POST /api/pr/remove_file    {id, files}
POST /api/pr/save_description {id, text}
POST /api/pr/set_number     {id, ref, confirm_head}
POST /api/pr/file_save      {slug, title, text}     per-file PR title/description
POST /api/pr/file_submit    {slug, title, text}     push to fork, return the prefilled PR page URL
POST /api/pr/submit         {id}                    same for a multi-file bundle
POST /api/pr/file_link      {slug}                  find the PR the human created, fill Fix PR
POST /api/pr/link           {id}                    same for a bundle
POST /api/pr/file_set_number {slug, ref, confirm_head}  per-file PR number -> Fix PR
POST /api/reviewer_start     {}                      start test_pr_reviewer if not running
"""
import argparse
import json
import os
import re
import socket
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import export
import gitops
import patch_reviewer
import issues
import pr_bundle
import rework_queue
import store
import verify_evidence
from common import ISSUE_REPO, PORTABILITY, REVIEW_CHOICES, parse_ref

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
CSV_COLS = ["xpu_status", "cuda_status", "xpu_message", "detail_reason", "issue_number", "fix_pr"]
CFG: dict = {}


# ---- read side -------------------------------------------------------------

def _csv_index() -> dict[str, dict]:
    path = CFG["csv"]
    st = os.stat(path)
    if CFG.get("_csv_mtime") != st.st_mtime:
        CFG["_csv_rows"] = {str(r.get("case_id")): r for r in store.read_csv(path)}
        CFG["_csv_mtime"] = st.st_mtime
    return CFG["_csv_rows"]


def _counts(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def data() -> dict:
    bundles = store.list_bundles()
    files = []
    for s in store.list_slugs():
        f = store.load_file(s)
        cases = store.load_cases(s)
        rv = store.load_reviews(s)
        link = review = ""
        if f.get("commit"):
            try:
                sb = pr_bundle.single_bundle(s)
                link, review = pr_bundle.compare_url(sb), pr_bundle.review_url(sb)
            except Exception:  # noqa: BLE001
                pass
        files.append({**f, "pr_body": "", "n_cases": len(cases), "pr_link": link, "review_link": review,
                      "portability": _counts((c.get("portability") or "") for c in cases.values()),
                      "review": _counts(rv["verdicts"].get(c, "") for c in cases),
                      "n_comments": len(rv["comments"])})
    for b in bundles:
        try:
            b["pending"] = pr_bundle.pending_commits(b)
            b.update(pr_bundle.links(b))
            if patch_reviewer.enabled() and b.get("kind") != "external":
                b["reviewer_url"] = patch_reviewer.url_for_bundle(b)
                b["reviewer_open"] = len(patch_reviewer.open_comments(b["id"]))
            if b.get("kind") != "external" and b.get("status") == "picked":
                b["push_state"] = pr_bundle.push_state(b["target_branch"])
            b["submit"] = [b["push"], b["gh"]]
            b["log"] = pr_bundle.log(b)
        except Exception as e:  # noqa: BLE001
            b["log"] = f"(git error: {e})"
    return {"repo": ISSUE_REPO, "csv": CFG["csv"], "portability_order": PORTABILITY,
            "review_choices": REVIEW_CHOICES, "files": files, "bundles": bundles,
            "rework": len(rework_queue.queue())}


def file_detail(s: str) -> dict:
    f = store.load_file(s)
    cases = store.load_cases(s)
    rv = store.load_reviews(s)
    res = store.load_results(s)
    csv_rows = _csv_index()
    probs = verify_evidence.summary(verify_evidence.check_file(s, use_gh=False)).get(f["test_file"], {})
    out = []
    for cid, c in cases.items():
        row = csv_rows.get(cid, {})
        last = store.latest([e for e in res.get(cid, []) if e.get("run") != "precheck"])
        pre = store.latest(res.get(cid, []), ["precheck"])
        msg = ""
        if last and last["status"] in ("FAILED", "ERROR"):
            msg = next((ln[2:].strip() for ln in (last.get("error_excerpt") or "").splitlines()
                        if ln[2:].strip()), last.get("reason", ""))
        elif last:
            msg = last.get("reason") or last["status"]
        out.append({"case_id": cid, **c, **{f"csv_{k}": row.get(k, "") for k in CSV_COLS},
                    "message": msg or row.get("xpu_message", ""),
                    "review": rv["verdicts"].get(cid, ""), "comment": rv["comments"].get(cid, ""),
                    "last": last, "runs": res.get(cid, []), "precheck": pre,
                    "evidence": probs.get(cid, [])})
    patch = store.patch_path(s)
    pr = None
    reviewer = None
    if patch_reviewer.enabled():
        bnd = store.load_bundle(f["bundle"]) if f.get("bundle") else {}
        prn = (bnd.get("pr") or {}).get("number")
        url = patch_reviewer.url_for(f, prn)
        cm = patch_reviewer.comments(s)
        reviewer = {"url": url, "comments": cm,
                    "open": sum(1 for c in cm if c.get("status") != "resolved")}
    if f.get("commit"):
        b = pr_bundle.single_bundle(s)
        bundled = store.load_bundle(f["bundle"]) if f.get("bundle") else {}
        pr = {"bundle": b, **pr_bundle.links(b), "push_state": pr_bundle.push_state(b["target_branch"]),
              "in_multi": bundled.get("id") if bundled and bundled.get("kind") != "single" else ""}
        ext = [x for x in store.list_bundles() if x.get("kind") == "external"
               and any(fe["slug"] == s for fe in x.get("files", []))]
        if ext:
            pr["external"] = [{"number": x["pr"]["number"], "url": x["pr"]["url"], "state": x["pr"].get("state", ""),
                               "title": x["title"]} for x in ext]
    return {"file": f, "cases": out, "drafts": store.load_drafts(s), "pr": pr, "reviewer": reviewer,
            "patch": open(patch, encoding="utf-8").read() if os.path.exists(patch) else "",
            "history": rv["history"]}


def text(s: str, kind: str, path: str) -> str:
    f = store.load_file(s)
    if kind == "source":
        if f["agent_branch"] and gitops.branch_exists(f["agent_branch"]):
            return gitops.git("show", f"{f['agent_branch']}:{f['source']}", check=False)
        return gitops.git("show", f"{f['base_sha']}:{f['source']}", check=False)
    full = os.path.realpath(os.path.join(store.fdir(s), path))
    if not full.startswith(os.path.realpath(store.fdir(s))) or not os.path.isfile(full):
        raise ValueError("bad path")
    return open(full, encoding="utf-8", errors="replace").read()


# ---- write side ------------------------------------------------------------

def set_review(p):
    n = store.set_reviews(p["slug"], p["case_ids"], p.get("value"), p.get("comment"))
    return {"ok": True, "updated": n}


def set_file_prompt(p):
    store.update_file(p["slug"], file_prompt=p.get("prompt", ""))
    rework_queue.save_notes(rework_queue.queue([store.load_file(p["slug"])["test_file"]]))
    return {"ok": True}


def set_case_fields(p):
    field, value = p["field"], p.get("value", "")
    if field == "issue":
        if value.strip():
            num, url = parse_ref(value, ISSUE_REPO)
            upd = {"issue_number": str(num), "issue_url": url}
        else:
            upd = {"issue_number": "", "issue_url": ""}
    elif field in ("portability", "reason", "fix_pr"):
        upd = {field: value}
    else:
        return {"ok": False, "error": f"field {field!r} not editable"}
    store.update_cases(p["slug"], dict.fromkeys(p["case_ids"], upd))
    return {"ok": True, "updated": len(p["case_ids"])}


def save_patch(p):
    s = p["slug"]
    f = store.load_file(s)
    if not f["agent_branch"]:
        return {"ok": False, "error": "file has no agent branch"}
    old = gitops.commits(f["base_sha"], f["agent_branch"])
    msg = gitops.commit_message(old[0]) if old else f"Enable {f['test_file']} on XPU"
    msg = msg.rstrip() + "\n\n(patch edited in review)\n"
    try:
        gitops.reapply_patch(f["agent_branch"], f["base_sha"], p["text"], msg)
    except gitops.GitError as e:
        return {"ok": False, "error": f"patch did not apply; branch unchanged: {e}"}
    import file_branch
    f = file_branch.regen_patch(f["test_file"])
    return {"ok": True, "commit": f["commit"], "bytes": len(p["text"])}


def _replace_token_in_branch(f: dict, token: str, number: int) -> str | None:
    def edit(cwd):
        changed = []
        for rel in gitops.changed_files(f["base_sha"], "HEAD", cwd):
            path = os.path.join(cwd, rel)
            if not os.path.isfile(path):
                continue
            t = open(path, encoding="utf-8").read()
            n = t.replace(f"issues/{token}", f"issues/{number}")
            if n != t:
                open(path, "w", encoding="utf-8").write(n)
                changed.append(rel)
        return changed
    return gitops.edit_on_branch(f["agent_branch"], f["base_sha"], edit, f"Backfill issue #{number} in {f['test_file']}")


def _backfill(s: str, draft_id: str, number: int, url: str) -> dict:
    drafts = store.load_drafts(s)
    d = next((x for x in drafts if x.get("id") == draft_id), None)
    if not d:
        return {"ok": False, "error": f"no draft {draft_id}"}
    d.update(number=number, url=url)
    store.save_drafts(s, drafts)
    token = d.get("placeholder") or "PLACEHOLDER"
    cases = store.load_cases(s)
    upd, kept = {}, []
    for cid in map(str, d.get("cases", [])):
        c = cases.get(cid, {})
        old = str(c.get("issue_number") or "")
        if old and old != str(number) and issues.citable(issues.meta(old)) != "stale":
            kept.append(cid)
            continue
        reason = (c.get("reason") or "").replace(f"issues/{token}", f"issues/{number}").replace(token, str(number))
        upd[cid] = {"issue_number": str(number), "issue_url": url, "reason": reason}
        if d.get("verdict") and not c.get("portability"):
            upd[cid]["portability"] = d["verdict"]
    store.update_cases(s, upd)
    f = store.load_file(s)
    sha = None
    if f["agent_branch"] and gitops.branch_exists(f["agent_branch"]):
        sha = _replace_token_in_branch(f, token, number)
        import file_branch
        file_branch.regen_patch(f["test_file"])
    return {"ok": True, "number": number, "url": url, "backfilled": list(upd), "kept_existing": kept,
            "commit": sha}


def submit_issue(p):
    s = p["slug"]
    drafts = store.load_drafts(s)
    d = next((x for x in drafts if x.get("id") == p["id"]), None)
    if not d:
        return {"ok": False, "error": "no such draft"}
    if d.get("number"):
        return {"ok": False, "error": f"already filed as #{d['number']}"}
    d.update(title=p["title"], body=p["body"], labels=p.get("labels", d.get("labels", [])))
    store.save_drafts(s, drafts)
    if CFG.get("dry_run"):
        return {"ok": True, "dry_run": True}
    try:
        num, url = issues.create(d["title"], d["body"], d["labels"])
    except RuntimeError as e:
        q = urllib.parse.urlencode({"title": d["title"], "body": d["body"], "labels": ",".join(d["labels"])},
                                   quote_via=urllib.parse.quote)
        return {"ok": False, "error": str(e), "new_issue_url": f"https://github.com/{ISSUE_REPO}/issues/new?{q}"}
    return _backfill(s, d["id"], num, url)


def backfill_issue(p):
    num, url = parse_ref(p["issue"], ISSUE_REPO)
    return _backfill(p["slug"], p["id"], num, url)


def match_issue(p):
    h = issues.find_by_title(p.get("title", ""))
    return {"ok": True, "found": bool(h), **(h or {})}


def reject_draft(p):
    s = p["slug"]
    d = next((x for x in store.load_drafts(s) if x.get("id") == p["id"]), None)
    if not d:
        return {"ok": False, "error": "no such draft"}
    store.set_reviews(s, d.get("cases", []), "Needs work")
    token = d.get("placeholder")
    removed = 0
    f = store.load_file(s)
    if token and token != "PLACEHOLDER" and f["agent_branch"]:
        def edit(cwd):
            nonlocal removed
            changed = []
            for rel in gitops.changed_files(f["base_sha"], "HEAD", cwd):
                path = os.path.join(cwd, rel)
                if not os.path.isfile(path):
                    continue
                lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
                keep = [ln for ln in lines if f"issues/{token}" not in ln]
                if len(keep) != len(lines):
                    removed += len(lines) - len(keep)
                    open(path, "w", encoding="utf-8").write("".join(keep))
                    changed.append(rel)
            return changed
        gitops.edit_on_branch(f["agent_branch"], f["base_sha"], edit, f"Revert draft {token} gate")
        import file_branch
        file_branch.regen_patch(f["test_file"])
    return {"ok": True, "updated": len(d.get("cases", [])), "reverted_lines": removed}


def export_csv(_p):
    return export.export_csv(CFG["csv"])


PR_ROUTES = {
    "preflight": lambda p: {"ok": True, **pr_bundle.preflight(list(p["files"]))},
    "create": lambda p: pr_bundle.create(p["target"], p["title"],
                                         [store.load_file(s)["test_file"] for s in p["files"]],
                                         bool(p.get("force"))),
    "update": lambda p: pr_bundle.update(p["id"]),
    "refresh": lambda p: pr_bundle.refresh(p["id"]),
    "delete": lambda p: pr_bundle.delete(p["id"]),
    "remove_file": lambda p: pr_bundle.remove(p["id"], [store.load_file(s)["test_file"] for s in p["files"]]),
    "save_description": lambda p: pr_bundle.save_description(p["id"], p["text"]),
    "set_number": lambda p: pr_bundle.set_pr(p["id"], p["ref"], bool(p.get("confirm_head"))),
    "file_save": lambda p: pr_bundle.save_file_pr(p["slug"], p["title"], p["text"]),
    "file_regen": lambda p: pr_bundle.regenerate_file_pr(p["slug"]),
    "file_submit": lambda p: pr_bundle.submit_file(p["slug"], p.get("title", ""), p.get("text", ""),
                                                   CFG.get("dry_run", False)),
    "submit": lambda p: pr_bundle.submit_bundle(p["id"], CFG.get("dry_run", False)),
    "file_link": lambda p: pr_bundle.link_file_pr(p["slug"]),
    "link": lambda p: pr_bundle.link_pr(store.load_bundle(p["id"])),
    "file_set_number": lambda p: pr_bundle.set_file_pr(p["slug"], p["ref"], bool(p.get("confirm_head"))),
}
def reviewer_start(_p):
    return patch_reviewer.ensure_running()


ROUTES = {"reviewer_start": reviewer_start, "set_review": set_review, "set_file_prompt": set_file_prompt,
          "set_case_fields": set_case_fields, "save_patch": save_patch,
          "submit_issue": submit_issue, "backfill_issue": backfill_issue,
          "match_issue": match_issue, "reject_draft": reject_draft, "export_csv": export_csv}


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # the UI changes with the skill; never let the browser keep a stale app.js
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path == "/api/data":
                return self._json(200, data())
            if u.path == "/api/file":
                return self._json(200, file_detail(q["slug"]))
            if u.path == "/api/text":
                return self._json(200, {"text": text(q["slug"], q.get("kind", "log"), q.get("path", ""))})
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if u.path in ("/", ""):
            self.path = "/index.html"
        elif not os.path.isfile(os.path.join(UI_DIR, u.path.lstrip("/"))):
            return self._json(404, {"ok": False, "error": f"not found: {u.path}"})
        return super().do_GET()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        m = re.match(r"^/api/pr/(\w+)$", path)
        fn = PR_ROUTES.get(m.group(1)) if m else ROUTES.get(path.removeprefix("/api/"))
        if not fn:
            return self._json(404, {"ok": False, "error": "unknown endpoint"})
        try:
            return self._json(200, fn(payload))
        except gitops.GitError as e:
            return self._json(200, {"ok": False, "error": f"git: {e}"})
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="input CSV (read-only)")
    ap.add_argument("--port", type=int, default=8100,
                    help="first port to try; the next free one is used if it is busy")
    ap.add_argument("--bind", default="0.0.0.0", help="0.0.0.0 = reachable from other machines")
    ap.add_argument("--dry-run", action="store_true", help="never call gh issue create and never push")
    ap.add_argument("--patch-reviewer", default=os.environ.get("XPU_PATCH_REVIEWER", ""),
                    help="test_pr_reviewer/scripts dir; enables the 'Review patch' button (started on demand)")
    ap.add_argument("--patch-reviewer-port", type=int, default=8766)
    a = ap.parse_args()
    CFG.update(csv=os.path.abspath(a.csv), dry_run=a.dry_run)
    srv = None
    for port in range(a.port, a.port + 50):
        try:
            srv = ThreadingHTTPServer((a.bind, port), partial(Handler, directory=UI_DIR))
            break
        except OSError:
            continue
    if srv is None:
        raise SystemExit(f"no free port in {a.port}..{a.port + 49}")
    host = socket.getfqdn() if a.bind == "0.0.0.0" else a.bind
    patch_reviewer.configure(a.patch_reviewer, a.patch_reviewer_port, host)
    print(f"report: {store.REPORT}\ncsv:    {CFG['csv']}\n"
          f"open:   http://{host}:{port}/   (local: http://localhost:{port}/)"
          + ("  (dry-run)" if a.dry_run else ""), flush=True)
    if patch_reviewer.enabled():
        print(f"patch reviewer: {a.patch_reviewer} -> started on demand at :{a.patch_reviewer_port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        patch_reviewer.stop()


if __name__ == "__main__":
    main()
