#!/usr/bin/env python3
"""Create / refresh the fixed workspace ($XPU_ENABLE_WS, default ~/xpu_enable_ws).

  pytorch/            independent clone of pytorch/pytorch, detached at viable/strict
  env/                venv with the nightly XPU torch wheel
  xpu_enable_report/  report tree
  workspace.json      {base_ref, base_sha, nightly_git_version, fork_remote, fork_owner, fork_repo, created}

Prints the commands; runs them only with --run.

  setup_ws.py [--run] [--reference ~/upstream/oct]       first-time setup
  setup_ws.py --refresh [--run]                          fetch viable/strict, move base_sha
  setup_ws.py --fork-remote fork --fork-owner daisyden [--fork-repo daisyden/pytorch]
                                                         record the fork that branches are pushed to
                                                         (and add it as a remote of the clone)
  setup_ws.py --status
"""
import argparse
import json
import os
import re
import shlex
import subprocess

from common import CLONE, ENV_DIR, REPORT, WS, WORKSPACE_JSON, python_bin

BASE_REF = "origin/viable/strict"
NIGHTLY = "https://download.pytorch.org/whl/nightly/xpu"
TEST_DEPS = ["pytest", "expecttest", "hypothesis", "pyyaml", "numpy", "scipy", "psutil"]


def load_ws() -> dict:
    if os.path.exists(WORKSPACE_JSON):
        with open(WORKSPACE_JSON) as fh:
            return json.load(fh)
    return {}


def save_ws(d: dict) -> None:
    os.makedirs(WS, exist_ok=True)
    with open(WORKSPACE_JSON, "w") as fh:
        json.dump(d, fh, indent=2)


def run(cmds: list[list[str]], do: bool) -> None:
    for c in cmds:
        print("$", " ".join(shlex.quote(x) for x in c))
        if do:
            subprocess.check_call(c)


def nightly_version() -> str:
    try:
        return subprocess.check_output(
            [python_bin(), "-c", "import torch;print(torch.__version__, torch.version.git_version)"],
            text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001
        return ""


def base_sha() -> str:
    return subprocess.check_output(["git", "-C", CLONE, "rev-parse", BASE_REF], text=True).strip()


def skew_warning(ws: dict) -> str:
    gv = (ws.get("nightly_git_version") or "").split()[-1:] or [""]
    gv = gv[0]
    if not gv or not ws.get("base_sha"):
        return ""
    def git(*a):
        return subprocess.run(["git", "-C", CLONE, *a], capture_output=True, text=True)
    if git("cat-file", "-t", gv).returncode != 0:
        git("fetch", "-q", "--no-tags", "origin", gv)
    # nightly wheels report the nightly-branch commit; its subject names the main commit
    m = re.search(r"\(([0-9a-f]{40})\)", git("log", "-1", "--format=%s", gv).stdout)
    src = m.group(1) if m else gv
    if git("cat-file", "-t", src).returncode != 0:
        git("fetch", "-q", "--no-tags", "origin", src)
    p = git("rev-list", "--count", f"{src}..{ws['base_sha']}")
    if p.returncode != 0:
        return f"WARN: nightly source commit {src[:12]} not found in clone"
    behind = int(git("rev-list", "--count", f"{ws['base_sha']}..{src}").stdout.strip() or 0)
    n = int(p.stdout.strip() or 0)
    msg = f"skew: base is {n} commits ahead / {behind} behind the nightly wheel (source {src[:12]})"
    return ("WARN: " + msg) if n > 300 or behind > 300 else msg


def add_fork_remote(ws: dict) -> None:
    """Local-only: register the fork as a remote of the clone (push over SSH).
    Never pushes by itself; pushing happens only from the UI Submit button."""
    remote = ws.get("fork_remote")
    repo = ws.get("fork_repo") or (f"{ws['fork_owner']}/pytorch" if ws.get("fork_owner") else "")
    if not (remote and repo and os.path.isdir(os.path.join(CLONE, ".git"))):
        return
    have = subprocess.run(["git", "-C", CLONE, "remote"], capture_output=True, text=True).stdout.split()
    if remote not in have:
        subprocess.check_call(["git", "-C", CLONE, "remote", "add", remote, f"https://github.com/{repo}.git"])
        print(f"added remote {remote} -> https://github.com/{repo}.git")
    # push over SSH (gh is configured for ssh); fetch stays https
    subprocess.check_call(["git", "-C", CLONE, "remote", "set-url", "--push", remote, f"git@github.com:{repo}.git"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true", help="execute the printed commands")
    ap.add_argument("--reference", help="local pytorch checkout to borrow objects from (faster clone)")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--fork-remote")
    ap.add_argument("--fork-owner")
    ap.add_argument("--fork-repo", help="owner/name of the fork (default <fork-owner>/pytorch)")
    a = ap.parse_args()
    ws = load_ws()

    if a.fork_remote or a.fork_owner or a.fork_repo:
        ws.update({k: v for k, v in (("fork_remote", a.fork_remote), ("fork_owner", a.fork_owner),
                                     ("fork_repo", a.fork_repo)) if v})
        save_ws(ws)
        add_fork_remote(ws)
        if not (a.status or a.refresh or a.run):
            print(json.dumps(ws, indent=2))
            return
    if a.status:
        print(json.dumps(ws, indent=2))
        print(skew_warning(ws))
        return
    if a.refresh:
        run([["git", "-C", CLONE, "fetch", "origin", "viable/strict"]], a.run)
        if a.run:
            ws.update(base_sha=base_sha(), nightly_git_version=nightly_version())
            save_ws(ws)
            print(json.dumps(ws, indent=2), "\n", skew_warning(ws))
        return

    cmds = []
    if not os.path.isdir(CLONE):
        clone = ["git", "clone"]
        if a.reference:
            clone += ["--reference-if-able", os.path.expanduser(a.reference), "--dissociate"]
        cmds.append(clone + ["https://github.com/pytorch/pytorch.git", CLONE])
    cmds.append(["git", "-C", CLONE, "fetch", "origin", "viable/strict"])
    cmds.append(["git", "-C", CLONE, "switch", "--detach", BASE_REF])
    if not os.path.isdir(ENV_DIR):
        cmds.append(["python3", "-m", "venv", ENV_DIR])
    pip = os.path.join(ENV_DIR, "bin", "pip")
    cmds.append([pip, "install", "--pre", "--upgrade", "torch", "--index-url", NIGHTLY])
    cmds.append([pip, "install", *TEST_DEPS])
    run(cmds, a.run)
    os.makedirs(REPORT, exist_ok=True)
    if a.run:
        ws.update(base_ref=BASE_REF, base_sha=base_sha(), nightly_git_version=nightly_version())
        ws.setdefault("created", __import__("time").strftime("%Y-%m-%d"))
        save_ws(ws)
        print(json.dumps(ws, indent=2), "\n", skew_warning(ws))
    else:
        print("\n(dry run; re-run with --run to execute)")


if __name__ == "__main__":
    main()
