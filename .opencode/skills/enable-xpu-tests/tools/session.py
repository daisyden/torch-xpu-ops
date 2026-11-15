#!/usr/bin/env python3
"""Sessions: one input CSV = one isolated report tree.

  session.py list
  session.py show                         active session (name, csv, root, base_sha)
  session.py create NAME --csv PATH [--note TEXT] [--copy] [--no-switch]
  session.py switch NAME

Registry: $XPU_ENABLE_WS/sessions.json
  {"active": NAME, "sessions": {NAME: {root, csv, base_sha, created, note}}}

Each session has its own report root (per-file data, PR bundles, issue cache, patch
comments), input CSV and base commit. New sessions live in $WS/sessions/<NAME>/;
the pre-session tree $WS/xpu_enable_report is registered on first use. The active
session is the registry's "active" entry, overridden by $XPU_ENABLE_SESSION; every
tool resolves it at call time, so the review UI and the agent CLI work on the same
session. The clone and venv are shared; agent branches get a fresh random suffix per
session, so two sessions never reuse each other's branch. references/review_notes.md
is shared on purpose (lessons carry over).
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time

from common import REPORT, WS, WORKSPACE_JSON

REGISTRY = os.path.join(WS, "sessions.json")
SESSIONS_DIR = os.path.join(WS, "sessions")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CACHE: dict = {"mtime": None, "data": None}


def _ws_base() -> str:
    try:
        with open(WORKSPACE_JSON) as fh:
            return json.load(fh).get("base_sha", "")
    except OSError:
        return ""


def _write(data: dict) -> None:
    os.makedirs(WS, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=WS, suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, REGISTRY)
    _CACHE["mtime"] = None


def load() -> dict:
    """The registry; registers the pre-session report tree as the first session."""
    if not os.path.exists(REGISTRY):
        _write({"active": "default", "sessions": {"default": {
            "root": REPORT, "csv": "", "base_sha": _ws_base(), "created": time.strftime("%Y-%m-%d"),
            "note": "report tree from before sessions existed"}}})
    mt = os.path.getmtime(REGISTRY)
    if _CACHE["mtime"] != mt:
        with open(REGISTRY) as fh:
            _CACHE["data"] = json.load(fh)
        _CACHE["mtime"] = mt
    return _CACHE["data"]


def active_name() -> str:
    reg = load()
    name = os.environ.get("XPU_ENABLE_SESSION") or reg["active"]
    if name not in reg["sessions"]:
        raise SystemExit(f"unknown session {name!r}; known: {', '.join(reg['sessions'])}")
    return name


def active() -> dict:
    name = active_name()
    return {"name": name, **load()["sessions"][name]}


def root() -> str:
    return active()["root"]


def csv_path() -> str:
    return active().get("csv", "")


def base_sha() -> str:
    return active().get("base_sha") or _ws_base()


def require_csv(given: str = "") -> str:
    """--csv if given, else the active session's CSV."""
    p = given or csv_path()
    if not p:
        raise SystemExit(f"session {active_name()!r} has no CSV: pass --csv or session.py create")
    return os.path.abspath(p)


def create(name: str, csv: str, note: str = "", copy: bool = False, switch: bool = True,
           csv_text: str | None = None) -> dict:
    """New empty session. `csv_text` (an uploaded file) or `copy` stores the CSV in the
    session dir, so later edits of the original do not change the session's input."""
    if not NAME_RE.match(name or ""):
        return {"ok": False, "error": "name: letters, digits, '_', '.', '-' (max 64), starting with a letter/digit"}
    reg = load()
    if name in reg["sessions"]:
        return {"ok": False, "error": f"session {name!r} already exists"}
    sroot = os.path.join(SESSIONS_DIR, name)
    if os.path.exists(sroot):
        return {"ok": False, "error": f"{sroot} already exists"}
    if csv_text is None and not (csv and os.path.isfile(csv)):
        return {"ok": False, "error": f"CSV not found: {csv!r}"}
    os.makedirs(os.path.join(sroot, "_input"))
    if csv_text is not None:
        dst = os.path.join(sroot, "_input", os.path.basename(csv) or "input.csv")
        with open(dst, "w", encoding="utf-8", newline="") as fh:
            fh.write(csv_text)
        csv = dst
    elif copy:
        dst = os.path.join(sroot, "_input", os.path.basename(csv))
        shutil.copy2(csv, dst)
        csv = dst
    reg["sessions"][name] = {"root": sroot, "csv": os.path.abspath(csv), "base_sha": _ws_base(),
                             "created": time.strftime("%Y-%m-%d %H:%M"), "note": note}
    if switch:
        reg["active"] = name
    _write(reg)
    return {"ok": True, "session": {"name": name, **reg["sessions"][name]}, "active": reg["active"]}


def switch_to(name: str) -> dict:
    reg = load()
    if name not in reg["sessions"]:
        return {"ok": False, "error": f"unknown session {name!r}"}
    reg["active"] = name
    _write(reg)
    return {"ok": True, "active": name}


def set_csv(name: str, csv: str) -> None:
    reg = load()
    reg["sessions"][name]["csv"] = os.path.abspath(csv)
    _write(reg)


def listing() -> list[dict]:
    reg = load()
    out = []
    for n, s in reg["sessions"].items():
        r = s["root"]
        files = [d for d in os.listdir(r) if os.path.isfile(os.path.join(r, d, "file.json"))] if os.path.isdir(r) else []
        out.append({"name": n, **s, "files": len(files), "active": n == reg["active"]})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("show")
    c = sub.add_parser("create")
    c.add_argument("name")
    c.add_argument("--csv", required=True)
    c.add_argument("--note", default="")
    c.add_argument("--copy", action="store_true", help="store a copy of the CSV in the session")
    c.add_argument("--no-switch", action="store_true")
    s = sub.add_parser("switch")
    s.add_argument("name")
    a = ap.parse_args()
    if a.cmd == "list":
        for x in listing():
            print(f"{'*' if x['active'] else ' '} {x['name']:<28} files={x['files']:<4} base={x['base_sha'][:12]}  csv={x['csv'] or '-'}")
    elif a.cmd == "show":
        print(json.dumps(active(), indent=2))
    elif a.cmd == "create":
        r = create(a.name, a.csv, a.note, a.copy, not a.no_switch)
        print(json.dumps(r, indent=2))
        sys.exit(0 if r["ok"] else 1)
    else:
        r = switch_to(a.name)
        print(json.dumps(r, indent=2))
        sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
