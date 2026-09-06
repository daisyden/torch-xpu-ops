#!/usr/bin/env python3
"""Local review server for PyTorch test-refactor PRs.

    ./server.py                      # then enter a PR in the browser
    ./server.py 189250               # open straight to a PR
    ./server.py --port 8899 --repo pytorch/pytorch

Endpoints
    GET  /                       the UI
    GET  /api/pr?ref=<pr>        PR metadata + changed-file list
    GET  /api/file?ref=&path=    diff rows, base/head source, refactor summary
    GET  /api/resolve?...        counterpart for one clicked line
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import traceback
from collections import OrderedDict
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import matcher
import prdata

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"

DEFAULT_REPO = "pytorch/pytorch"

# One lock per PR instead of a single global one.  `load_pr` shells out to `gh`,
# which takes seconds; holding a global lock across that made N users opening N
# different PRs wait N * latency instead of all being served at once.  Per-PR
# locks still prevent two threads from fetching the *same* PR twice.
_LOCK = threading.Lock()          # guards the lock registry and the caches only
_PR_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_PR_CACHE: dict[tuple[str, str], prdata.PullRequest] = {}


def _pr_lock(key: tuple[str, str]) -> threading.Lock:
    with _LOCK:
        lk = _PR_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _PR_LOCKS[key] = lk
        return lk


def get_pr(ref: str, refresh: bool = False) -> prdata.PullRequest:
    repo, num = prdata.parse_pr_ref(ref, DEFAULT_REPO)
    key = (repo, str(num))
    if not refresh:
        with _LOCK:
            hit = _PR_CACHE.get(key)
        if hit is not None:
            return hit
    # only threads interested in *this* PR serialise here
    with _pr_lock(key):
        if not refresh:
            with _LOCK:
                hit = _PR_CACHE.get(key)
            if hit is not None:
                return hit
        pr = prdata.load_pr(f"{repo}#{num}", refresh=refresh)
        with _LOCK:
            _PR_CACHE[key] = pr
        return pr


def get_match(pr: prdata.PullRequest, fd: prdata.FileDiff) -> matcher.FileMatch:
    return matcher.build_match(pr.base_text(fd), pr.head_text(fd), fd.path)


_DIFF_CACHE: "OrderedDict[tuple[str, int, str], prdata.FileDiff]" = OrderedDict()
DIFF_CACHE_MAX = 128


def get_file_diff(pr: prdata.PullRequest, path: str) -> prdata.FileDiff:
    """The file's diff, with line numbers guaranteed to match the file contents.

    GitHub truncates very large diffs, leaving later hunks with stale line
    numbers.  When that is detected the diff is recomputed locally from the real
    base/head blobs, so every downstream consumer can trust the positions.
    """
    key = (pr.repo, pr.number, path)
    with _LOCK:
        cached = _DIFF_CACHE.get(key)
        if cached is not None:
            _DIFF_CACHE.move_to_end(key)
    if cached is not None:
        return cached
    fd = pr.file_diff(path)
    if fd is None:
        raise prdata.PRDataError(f"{path} is not part of PR #{pr.number}")
    if not fd.binary and path.endswith(".py"):
        bt, ht = pr.base_text(fd), pr.head_text(fd)
        if bt and ht and not fd.verify(bt, ht):
            print(f"  ! diff for {path} is truncated/stale; rebuilding locally")
            fd = prdata.rebuild_diff(fd, bt, ht)
    with _LOCK:
        _DIFF_CACHE[key] = fd
        _DIFF_CACHE.move_to_end(key)
        while len(_DIFF_CACHE) > DIFF_CACHE_MAX:
            _DIFF_CACHE.popitem(last=False)
    return fd


# --------------------------------------------------------------------------- #
# API handlers
# --------------------------------------------------------------------------- #


def api_pr(q: dict[str, list[str]]) -> dict:
    ref = q.get("ref", [""])[0]
    refresh = q.get("refresh", ["0"])[0] in ("1", "true")
    pr = get_pr(ref, refresh=refresh)
    meta = pr.meta_json()
    meta["reviewable"] = [
        f["path"] for f in meta["files"] if f["path"].endswith(".py") and not f["binary"]
    ]
    return meta


def _line_marks(model: matcher.FileModel, fmatch: matcher.FileMatch, side: str) -> dict:
    """Per-line unit ownership, so the UI can show unit boundaries and names."""
    units = []
    for u in model.units:
        c = fmatch.primary(side, u.uid)
        units.append(
            {
                **u.to_json(),
                "linenos": u.linenos,
                "target": c.other.to_json() if c else None,
                "score": round(c.score, 3) if c else None,
                "reason": c.reason if c else None,
                "mutual": c.mutual if c else False,
                "n_candidates": len(fmatch.candidates(side, u.uid)),
            }
        )
    return {
        "path": model.path,
        "nlines": len(model.lines),
        # NB: the raw text is *not* sent here; /api/linemap carries it for the
        # file views, so shipping it twice would double the payload.
        "units": units,
        "classes": [
            {"qualname": q, **{k: v for k, v in info.items() if k != "parent"}}
            for q, info in model.class_info.items()
        ],
        "parse_error": model.parse_error,
    }


def api_file(q: dict[str, list[str]]) -> dict:
    ref = q.get("ref", [""])[0]
    path = q.get("path", [""])[0]
    pr = get_pr(ref)
    fd = get_file_diff(pr, path)
    if fd.binary or not path.endswith(".py"):
        return {
            "file": fd.to_json(),
            "unsupported": True,
            "message": "only Python text files can be analysed",
        }
    fmatch = get_match(pr, fd)
    summary = matcher.summarize(fmatch)

    # Verdict of the whole unit a line belongs to, keyed by *both* revisions'
    # qualnames, so an added line can be judged as well as a deleted one.
    unit_verdict: dict[str, str] = {}
    for item in summary["units"]:
        unit_verdict[item["base"]["qualname"]] = item["verdict"]
        if item.get("head"):
            unit_verdict.setdefault(item["head"]["qualname"], item["verdict"])

    diff = fd.to_json()
    for ln in diff["lines"]:
        no = ln["base_no"] if ln["kind"] == "del" else ln["head_no"]
        model = fmatch.base if ln["kind"] == "del" else fmatch.head
        side = "base" if ln["kind"] == "del" else "head"
        if no is None:
            continue
        u = model.owner(no)
        if u is None:
            # Outside any unit: imports, module-level calls such as
            # instantiate_device_type_tests(...), and the blank lines between
            # classes.  Blank/comment-only lines carry no meaning, so do not
            # push them into the reviewer's queue.
            stripped = ln["text"].strip()
            ln["verdict"] = "blank" if not stripped else "changed"
            continue
        c = fmatch.primary(side, u.uid)
        ln["unit"] = u.qualname
        ln["unit_kind"] = u.kind
        if c is not None:
            ln["target"] = c.other.qualname
            ln["target_uid"] = c.other.uid
            ln["unit_score"] = round(c.score, 3)
            ln["moved"] = u.cls != c.other.cls
            ln["renamed"] = u.name != c.other.name
            # What the reviewer actually needs: does this unit differ in
            # substance?  A verbatim move is "identical" even though every one
            # of its lines shows up as -/+ in the diff.  Fall back to the
            # per-line verdict for class headers, which are not in `units`.
            v = unit_verdict.get(u.qualname)
            if v is None:
                v = unit_verdict.get(c.other.qualname)
            if v is None:
                b_u = u if side == "base" else c.other
                h_u = c.other if side == "base" else u
                rows = matcher.align_lines(
                    fmatch.base, fmatch.head, b_u.linenos, h_u.linenos
                )
                v = matcher.rows_verdict(rows)
            # a blank line is never itself a review target, whatever its unit did
            if not ln["text"].strip():
                v = "blank"
            ln["verdict"] = v
        else:
            ln["target"] = None
            ln["verdict"] = "blank" if not ln["text"].strip() else "missing"

    return {
        "file": diff,
        "base": _line_marks(fmatch.base, fmatch, "base"),
        "head": _line_marks(fmatch.head, fmatch, "head"),
        "summary": summary,
        "class_map": fmatch.class_map,
        "class_map_rev": fmatch.class_map_rev,
        "base_sha": pr.base_sha,
        "head_sha": pr.head_sha,
    }


def api_linemap(q: dict[str, list[str]]) -> dict:
    """Whole-file line map for the side-by-side file views (panes 2 and 3).

    Separate from /api/file so the diff pane can render immediately while this
    (larger) payload loads.
    """
    ref = q.get("ref", [""])[0]
    path = q.get("path", [""])[0]
    pr = get_pr(ref)
    fd = get_file_diff(pr, path)
    if fd.binary or not path.endswith(".py"):
        return {"unsupported": True}
    # the unified diff's own context lines are authoritative correspondences
    anchors = [
        (ln.base_no, ln.head_no)
        for ln in fd.lines
        if ln.kind == "ctx" and ln.base_no is not None and ln.head_no is not None
    ]
    return matcher.build_line_map(get_match(pr, fd), anchors=anchors)


def api_resolve(q: dict[str, list[str]]) -> dict:
    ref = q.get("ref", [""])[0]
    path = q.get("path", [""])[0]
    side = q.get("side", ["base"])[0]
    lineno = int(q.get("line", ["1"])[0])
    tuid = q.get("target", [""])[0]
    target = int(tuid) if tuid.strip().lstrip("-").isdigit() else None
    ctx = int(q.get("context", ["8"])[0])
    pr = get_pr(ref)
    fd = get_file_diff(pr, path)
    fmatch = get_match(pr, fd)
    return matcher.resolve(fmatch, side, lineno, target_uid=target, context=ctx)


def api_health(q: dict[str, list[str]]) -> dict:
    """Cheap status endpoint, useful when several people share one server."""
    with _LOCK:
        prs = sorted(f"{r}#{n}" for r, n in _PR_CACHE)
        ndiff = len(_DIFF_CACHE)
    return {
        "ok": True,
        "default_repo": DEFAULT_REPO,
        "cached_prs": prs,
        "cached_diffs": ndiff,
        "analysis_cache": matcher.cache_stats(),
        "threads": threading.active_count(),
    }


ROUTES = {
    "/api/pr": api_pr,
    "/api/file": api_file,
    "/api/linemap": api_linemap,
    "/api/resolve": api_resolve,
    "/api/health": api_health,
}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


class Handler(BaseHTTPRequestHandler):
    server_version = "RefactorReview/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # quieter
        if "/api/" in (self.path or ""):
            print(f"  {self.path.split('?')[0]}  {args[1] if len(args) > 1 else ''}")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, obj: object, code: int = 200) -> None:
        self._send(
            code,
            json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        if route in ROUTES:
            try:
                self._json(ROUTES[route](parse_qs(parsed.query)))
            except prdata.PRDataError as exc:
                self._json({"error": str(exc)}, 400)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            return

        rel = "index.html" if route in ("/", "") else route.lstrip("/")
        target = (STATIC / rel).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


def main() -> None:
    global DEFAULT_REPO
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pr", nargs="?", help="PR number or URL to open on start")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="default repo for bare numbers")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    DEFAULT_REPO = args.repo

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    if args.pr:
        _, num = prdata.parse_pr_ref(args.pr, DEFAULT_REPO)
        url += f"?pr={num}"
    print(f"pytorch test-refactor review  ->  {url}")
    print("Ctrl-C to stop")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
