#!/usr/bin/env python3
"""Background agent jobs started from the review UI (one at a time, per workspace).

  jobs.py start process [--files F ...]     enable the active session's CSV (all or some files)
  jobs.py start retest --files F ...        re-measure the given files' final code
  jobs.py status | log [N] | stop

A job is `opencode run` (headless, non-interactive) with the enable-xpu-tests
instructions for the active session. It holds $WS/job.lock for its lifetime, so only
one job uses the shared clone at a time; tools that change the clone call
`require_idle()` and refuse while a job runs (the job itself is exempt via
$XPU_ENABLE_JOB). GitHub writes are denied to the agent through OPENCODE_PERMISSION
(git push, gh issue/pr create/edit/comment, gh api), so issues, pushes and PRs stay
on the reviewer's buttons. opencode writes its JSON events to $WS/jobs/<id>.jsonl
(rendered to readable lines on read) and runs in its own process group, so restarting
the review server does not stop it; <id>.log has the command and prompt, <id>.json the
state.
"""
import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time

import session
from common import CLONE, SKILL_DIR, WS

JOBS_DIR = os.path.join(WS, "jobs")
LOCK = os.path.join(WS, "job.lock")
TOOLS = os.path.join(SKILL_DIR, "tools")

# Last matching rule wins; everything else stays allowed (headless runs cannot answer "ask").
DENY = ["git push*", "*git push*", "gh issue create*", "gh issue edit*", "gh issue comment*",
        "gh issue close*", "gh pr create*", "gh pr edit*", "gh pr comment*", "gh pr merge*",
        "gh pr ready*", "gh api*", "*gh api *", "gh release*", "gh repo*"]
PERMISSION = {"bash": {"*": "allow", **{p: "deny" for p in DENY}}, "edit": "allow", "webfetch": "allow",
              "external_directory": "allow", "question": "deny"}

RULES = """Rules for this unattended run (started from the review UI):
- Follow the enable-xpu-tests skill and its sub-skills exactly. Tools: {tools}. Work only in
  the workspace clone {clone}, never in the user's own checkout.
- The active session is {session}; every tool already uses it. Do not create or switch sessions.
- Never push, never file issues, never create or edit PRs. Draft issues only; the reviewer
  submits them from the UI. Such commands are blocked anyway.
- Do not ask questions: nobody is watching. If something needs a human decision, record it in
  the file's case reasons / a draft and continue with the next file.
- Batch-run every file in scope without pausing. At the end print one summary line per file:
  file, verdict counts, verify_evidence result."""

PROMPTS = {
    "process": """Use the enable-xpu-tests skill to enable the XPU tests of the active session's CSV.
Scope: {scope}.
For each file: file_branch.py start, precheck, port, measure (initial, then gate_removed for
every non-passing case), classify with evidence, draft issues where needed, commit, rework
measure of the final code, verify_evidence.py, file_branch.py finish.""",
    "retest": """Use the enable-xpu-tests skill to retest these files of the active session: {scope}.
For each file: measure.py --file <f> --run rework --force (the final code on its agent branch;
for a file without a branch, its current recorded state), then update portability / reason in
cases.json only where the new result contradicts the recorded verdict (a Portable case that no
longer passes, or a gated case whose gate-removed failure no longer reproduces: re-run
gate_removed for those), and run verify_evidence.py --file <f>. Do not change test code unless
a verdict can only be fixed that way; if so commit it on the file's agent branch and finish.""",
}


def _read(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie still answers kill 0; check its state
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split()[2] != "Z"
    except OSError:
        return True


def current() -> dict | None:
    """The running job, or None. A lock left by a dead job is cleared."""
    lk = _read(LOCK)
    if not lk:
        return None
    if _alive(lk.get("pid", 0)):
        return _read(os.path.join(JOBS_DIR, f"{lk['id']}.json"), lk)
    _finish(lk["id"], "ended")  # exited while nobody was waiting (e.g. the server restarted)
    return None


class Busy(RuntimeError):
    pass


def require_idle() -> None:
    """For tools that change the clone: refuse while a UI job owns it (except the job itself)."""
    if os.environ.get("XPU_ENABLE_JOB"):
        return
    j = current()
    if j:
        raise Busy(f"a {j['kind']} job ({j['id']}) is running in the workspace clone; "
                         "wait for it or stop it from the review UI (jobs.py stop)")


def _finish(jid: str, status: str, rc: int | None = None) -> None:
    p = os.path.join(JOBS_DIR, f"{jid}.json")
    j = _read(p, {})
    if j and j.get("status") in ("running", "stopping"):
        if j["status"] == "stopping":
            status = "stopped"
        j.update(status=status, ended=time.strftime("%Y-%m-%dT%H:%M:%S"), rc=rc)
        _write(p, j)
    lk = _read(LOCK)
    if lk and lk.get("id") == jid:
        os.remove(LOCK)


def _render(line: str) -> str:
    """One readable line per opencode JSON event (text, tool call + short result)."""
    try:
        e = json.loads(line)
    except ValueError:
        return line.rstrip("\n")
    p = e.get("part") or {}
    if p.get("type") == "text" and p.get("text", "").strip():
        return p["text"].strip()
    if p.get("type") == "tool":
        st = p.get("state") or {}
        inp = st.get("input") or {}
        what = inp.get("command") or inp.get("filePath") or inp.get("pattern") or inp.get("description") or ""
        out = (st.get("output") or st.get("error") or "").strip().splitlines()
        tail = (" -> " + out[-1][:200]) if out else ""
        return f"[{p.get('tool')}] {st.get('status', '')}: {str(what)[:300]}{tail}"
    return ""


def start(kind: str, files: list[str] | None = None, model: str = "") -> dict:
    if kind not in PROMPTS:
        return {"ok": False, "error": f"unknown job kind {kind!r}"}
    if kind == "retest" and not files:
        return {"ok": False, "error": "retest needs at least one file"}
    if current():
        return {"ok": False, "error": "another job is running; stop it first", "job": current()}
    from gitops import is_clean
    if not is_clean():
        return {"ok": False, "error": "workspace clone has uncommitted changes; clean it first"}
    cur = session.active()
    if kind == "process" and not cur.get("csv"):
        return {"ok": False, "error": f"session {cur['name']!r} has no CSV"}
    os.makedirs(JOBS_DIR, exist_ok=True)
    jid = time.strftime("%Y%m%d_%H%M%S") + f"_{kind}"
    scope = ", ".join(files) if files else "every test file of the CSV that select_cases.py still selects"
    prompt = PROMPTS[kind].format(scope=scope) + "\n\n" + RULES.format(session=cur["name"], tools=TOOLS, clone=CLONE)
    cmd = ["opencode", "run", "--format", "json", "--title", f"xpu {kind} {cur['name']}", "--dir", TOOLS]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    env = dict(os.environ, OPENCODE_PERMISSION=json.dumps(PERMISSION), XPU_ENABLE_SESSION=cur["name"],
               XPU_ENABLE_JOB=jid)
    raw, log = os.path.join(JOBS_DIR, f"{jid}.jsonl"), os.path.join(JOBS_DIR, f"{jid}.log")
    with open(log, "w") as fh:
        fh.write(f"$ {' '.join(shlex.quote(c) for c in cmd[:-1])} <prompt>\n--- prompt ---\n{prompt}\n--- output ---\n")
    with open(raw, "w") as rf:
        proc = subprocess.Popen(cmd, cwd=TOOLS, env=env, stdout=rf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    job = {"id": jid, "kind": kind, "files": files or [], "session": cur["name"], "pid": proc.pid,
           "status": "running", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "log": log, "raw": raw}
    _write(os.path.join(JOBS_DIR, f"{jid}.json"), job)
    _write(LOCK, {"id": jid, "pid": proc.pid, "kind": kind})

    def reap():
        rc = proc.wait()
        _finish(jid, "done" if rc == 0 else "failed", rc)
        _idle_clone(cur.get("base_sha", ""))

    threading.Thread(target=reap, daemon=True).start()
    return {"ok": True, "job": job}


def _idle_clone(base: str) -> None:
    """Return a clean clone to its idle state (detached at base); leave a dirty one for a human."""
    from gitops import git, is_clean
    if base and is_clean() and git("branch", "--show-current", check=False):
        git("switch", "--detach", base, check=False)


def stop() -> dict:
    j = current()
    if not j:
        return {"ok": False, "error": "no job is running"}
    p = os.path.join(JOBS_DIR, f"{j['id']}.json")
    _write(p, {**_read(p, j), "status": "stopping"})
    try:
        os.killpg(j["pid"], signal.SIGTERM)
        for _ in range(20):
            if not _alive(j["pid"]):
                break
            time.sleep(0.5)
        else:
            os.killpg(j["pid"], signal.SIGKILL)
    except ProcessLookupError:
        pass
    _finish(j["id"], "stopped")
    return {"ok": True, "stopped": j["id"],
            "note": "the clone may be on an agent branch with uncommitted edits; check before the next job"}


def recent(n: int = 5) -> list[dict]:
    if not os.path.isdir(JOBS_DIR):
        return []
    out = [_read(os.path.join(JOBS_DIR, f)) for f in sorted(os.listdir(JOBS_DIR), reverse=True) if f.endswith(".json")]
    return [j for j in out if j][:n]


def tail(jid: str = "", n: int = 200) -> dict:
    j = _read(os.path.join(JOBS_DIR, f"{jid}.json")) if jid else (current() or (recent(1) or [None])[0])
    if not j:
        return {"ok": True, "job": None, "text": ""}
    current()  # clears a dead lock so status is right
    j = _read(os.path.join(JOBS_DIR, f"{j['id']}.json"), j)
    lines = []
    for path, render in ((j["log"], False), (j["raw"], True)):
        try:
            with open(path, errors="replace") as fh:
                lines += [t for t in (_render(x) if render else x.rstrip("\n") for x in fh) if t]
        except OSError:
            pass
    return {"ok": True, "job": j, "text": "\n".join(lines[-n:]), "lines": len(lines)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("kind", choices=sorted(PROMPTS))
    s.add_argument("--files", nargs="*")
    s.add_argument("--model", default="")
    sub.add_parser("status")
    lg = sub.add_parser("log")
    lg.add_argument("n", nargs="?", type=int, default=60)
    sub.add_parser("stop")
    a = ap.parse_args()
    if a.cmd == "start":
        r = start(a.kind, a.files, a.model)
        print(json.dumps(r, indent=2))
        if r["ok"]:  # keep the pump alive when started from the CLI
            while current():
                time.sleep(5)
            print(json.dumps(tail(r["job"]["id"], 5), indent=2))
        sys.exit(0 if r["ok"] else 1)
    if a.cmd == "status":
        print(json.dumps({"running": current(), "recent": recent()}, indent=2))
    elif a.cmd == "log":
        print(tail(n=a.n)["text"])
    else:
        print(json.dumps(stop(), indent=2))


if __name__ == "__main__":
    main()
