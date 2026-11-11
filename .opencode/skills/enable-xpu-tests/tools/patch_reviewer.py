"""Optional companion: launch test_pr_reviewer (3-pane moved-code reviewer) for patches.

review_server.py starts it on demand as a child process when --patch-reviewer points
at its `scripts/` directory (or $XPU_PATCH_REVIEWER is set). Reviews of a file use
`local:<base>..<agent_branch>` against the workspace clone, or `?pr=<n>` once the file
has a PR. Line comments are stored by the reviewer under COMMENTS_DIR with key
= file slug, and read back here so the rework queue can act on them.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from common import CLONE, WS

COMMENTS_DIR = Path(WS) / "xpu_enable_report" / "_patch_comments"
_LOCK = threading.Lock()
_STATE: dict = {"proc": None, "port": 0, "scripts": "", "host": ""}


def configure(scripts: str, port: int, host: str) -> None:
    _STATE.update(scripts=scripts or "", port=port, host=host)


def enabled() -> bool:
    return bool(_STATE["scripts"]) and (Path(_STATE["scripts"]) / "server.py").exists()


def _up(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_running() -> dict:
    """Start the reviewer if it is not answering; returns {ok, port, error?}."""
    if not enabled():
        return {"ok": False, "error": "patch reviewer not configured (--patch-reviewer DIR)"}
    port = _STATE["port"]
    with _LOCK:
        p = _STATE["proc"]
        if _up(port):
            return {"ok": True, "port": port}
        if p is None or p.poll() is not None:
            env = dict(os.environ, REFACTOR_REVIEW_LOCAL_CLONE=CLONE,
                       REFACTOR_REVIEW_CLONE=os.environ.get("REFACTOR_REVIEW_CLONE", CLONE),
                       REFACTOR_REVIEW_COMMENTS=str(COMMENTS_DIR))
            log = open(Path(WS) / "patch_reviewer.log", "a")
            _STATE["proc"] = subprocess.Popen(
                [sys.executable, "-W", "ignore", "server.py", "--host", "0.0.0.0", "--port", str(port),
                 "--no-browser", "--local-clone", CLONE],
                cwd=_STATE["scripts"], env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(40):
        if _up(port):
            return {"ok": True, "port": port}
        time.sleep(0.25)
    return {"ok": False, "error": f"reviewer did not start on :{port}; see {WS}/patch_reviewer.log"}


def stop() -> None:
    p = _STATE.get("proc")
    if p is not None and p.poll() is None:
        p.terminate()


def url_for(f: dict, pr_number: int | None = None) -> str:
    """Reviewer URL for a test file: the agent branch against its base (or its PR)."""
    if not enabled():
        return ""
    host = _STATE["host"] or socket.getfqdn()
    q = {"ckey": f["slug"]}
    # always this file's own commits (agent branch vs its base): a bundle PR mixes
    # several files, and the agent branch is where rework happens
    if f.get("agent_branch") and f.get("base_sha"):
        q.update(base=f["base_sha"][:12], head=f["agent_branch"])
    elif pr_number:
        q["pr"] = str(pr_number)
    else:
        return ""
    if f.get("source"):
        q["file"] = f["source"]
    return f"http://{host}:{_STATE['port']}/?" + urllib.parse.urlencode(q)


def url_for_bundle(b: dict) -> str:
    """Reviewer URL for a multi-file bundle: its target branch against the base."""
    if not enabled() or not b.get("target_branch"):
        return ""
    host = _STATE["host"] or socket.getfqdn()
    q = {"ckey": b["id"]}
    if (b.get("pr") or {}).get("number"):
        q["pr"] = str(b["pr"]["number"])
    elif b.get("base_sha"):
        q.update(base=b["base_sha"][:12], head=b["target_branch"])
    else:
        return ""
    return f"http://{host}:{_STATE['port']}/?" + urllib.parse.urlencode(q)


def comments(slug: str) -> list[dict]:
    path = COMMENTS_DIR / f"{slug}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def open_comments(slug: str) -> list[dict]:
    return [c for c in comments(slug) if c.get("status") != "resolved"]
