#!/usr/bin/env python3
"""Measure cases on XPU with pytest and record per-case results.

  measure.py --file inductor/test_fp8.py --run initial|gate_removed|rework
             [--cases id,id | --all-cases] [--force] [--no-overlay] [--timeout 3600]
  measure.py --restore          put back the original site-packages torch/testing/_internal

What it does:
  1. Overlays the clone's torch/testing/_internal onto the venv's installed torch
     (one-time backup in $WS/_internal.orig), so op_db / helper edits take effect.
  2. Runs from <clone>/test:
       python -m pytest <file> -v -rA -p no:cacheprovider --junitxml=<xml> -k "<a> or <b>"
     (-k names are the XPU instantiations: cuda->xpu). Output is tee'd to
     <slug>/logs/<run>_<ts>.log; the first line is the literal command.
  3. Parses the JUnit XML into results.json per case:
       {run, nodeid, status, reason, error_excerpt, log, patch_sha, src_snapshot}
     status: PASSED FAILED ERROR SKIPPED XFAIL NOT_RUN (not collected).
  4. For gate_removed/rework runs, snapshots every file changed vs base into
     <slug>/logs/<run>_<ts>.src/ (used by verify_evidence R7).

A case already measured for the same run kind at the same patch_sha is not re-run
unless --force.
"""
import argparse
import os
import shlex
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import gitops
import store
from common import CLONE, WS, canon_dev, cuda_to_xpu, python_bin, slug as make_slug

ORIG_INTERNAL = os.path.join(WS, "_internal.orig")
CHUNK = 60


def site_internal() -> str:
    out = subprocess.check_output(
        [python_bin(), "-c", "import torch,os;print(os.path.dirname(torch.__file__))"],
        text=True, env=_env()).strip()
    return os.path.join(out, "testing", "_internal")


def _env() -> dict:
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = ""
    env.setdefault("PYTORCH_TEST_WITH_SLOW", "1")
    return env


def overlay() -> None:
    if not python_bin().startswith(WS):
        print("[measure.py] no workspace venv; skipping the torch/testing/_internal overlay")
        return
    dst = site_internal()
    if not os.path.isdir(ORIG_INTERNAL):
        shutil.copytree(dst, ORIG_INTERNAL)
    shutil.copytree(os.path.join(CLONE, "torch", "testing", "_internal"), dst, dirs_exist_ok=True)


def restore_overlay() -> None:
    if not os.path.isdir(ORIG_INTERNAL):
        print("nothing to restore")
        return
    dst = site_internal()
    shutil.rmtree(dst)
    shutil.copytree(ORIG_INTERNAL, dst)
    print(f"restored {dst}")


def working_patch_sha(base: str) -> str:
    text = gitops.git("diff", base, raw=True)
    return gitops.sha_of_text(text) if text else ""


def snapshot(base: str, dest: str) -> str:
    os.makedirs(dest, exist_ok=True)
    for p in gitops.git("diff", "--name-only", base).splitlines():
        src = os.path.join(CLONE, p)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(os.path.join(dest, p)), exist_ok=True)
            shutil.copy2(src, os.path.join(dest, p))
    return dest


def _status(tc: ET.Element) -> tuple[str, str, str]:
    for tag in ("failure", "error"):
        el = tc.find(tag)
        if el is not None:
            msg = (el.get("message") or "").strip()
            text = (el.text or "").strip()
            excerpt = "\n".join([ln for ln in text.splitlines() if ln.startswith("E ")][:25]) or msg
            return ("FAILED" if tag == "failure" else "ERROR"), msg.splitlines()[0] if msg else "", excerpt
    sk = tc.find("skipped")
    if sk is not None:
        kind = "XFAIL" if sk.get("type") == "pytest.xfail" else "SKIPPED"
        return kind, (sk.get("message") or "").strip(), ""
    return "PASSED", "", ""


def parse_junit(paths: list[str]) -> list[dict]:
    out = []
    for p in paths:
        if not os.path.exists(p):
            continue
        for tc in ET.parse(p).getroot().iter("testcase"):
            st, reason, exc = _status(tc)
            cls = (tc.get("classname") or "").rsplit(".", 1)[-1]
            out.append({"cls": cls, "name": tc.get("name") or "", "status": st,
                        "reason": reason, "error_excerpt": exc})
    return out


def match(case: dict, junit: list[dict]):
    raw = case.get("test_name", "")
    exact = [j for j in junit if j["name"] in (raw, cuda_to_xpu(raw))]
    name = canon_dev(raw)
    cls = canon_dev(case.get("class_name", ""))
    hits = exact or [j for j in junit if canon_dev(j["name"]) == name]
    if len(hits) > 1:
        hits = [j for j in hits if canon_dev(j["cls"]) == cls] or hits
    return hits[0] if hits else None


def run_pytest(test_rel: str, names: list[str], log: str, xml_base: str, timeout: int) -> list[str]:
    xmls = []
    with open(log, "w", encoding="utf-8") as fh:
        for i in range(0, max(len(names), 1), CHUNK):
            chunk = names[i:i + CHUNK]
            xml = f"{xml_base}.{i // CHUNK}.xml"
            cmd = [python_bin(), "-m", "pytest", test_rel, "-v", "-rA", "-p", "no:cacheprovider",
                   f"--junitxml={xml}"]
            if chunk:
                cmd += ["-k", " or ".join(chunk)]
            fh.write(f"$ cd {os.path.join(CLONE, 'test')} && {' '.join(shlex.quote(c) for c in cmd)}\n")
            fh.flush()
            try:
                p = subprocess.Popen(cmd, cwd=os.path.join(CLONE, "test"), env=_env(),
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in p.stdout:
                    fh.write(line)
                    sys.stdout.write(line)
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()
                fh.write(f"\n[measure.py] TIMEOUT after {timeout}s\n")
            xmls.append(xml)
    return xmls


def measure(test_file: str, run: str, case_ids=None, force=False, do_overlay=True,
            timeout=3600) -> dict:
    s = make_slug(test_file)
    f = store.load_file(s)
    cases = store.load_cases(s)
    want = [str(c) for c in (case_ids or cases)]
    psha = working_patch_sha(f["base_sha"])
    results = store.load_results(s)
    todo = [c for c in want if force or not store.latest(results.get(c), [run], psha)]
    if not todo:
        print("all requested cases already measured at this patch; use --force to re-run")
        return {}
    if do_overlay:
        overlay()
    ts = time.strftime("%Y%m%d_%H%M%S")
    logdir = os.path.join(store.fdir(s), "logs")
    os.makedirs(logdir, exist_ok=True)
    log = os.path.join(logdir, f"{run}_{ts}.log")
    snap = snapshot(f["base_sha"], os.path.join(logdir, f"{run}_{ts}.src")) if run != "initial" else ""
    # -k on both spellings: device-instantiated tests end in _xpu, while plain TestCase
    # methods may just contain "cuda" in their name (test_..._cuda_fallback_...)
    names = sorted({n for c in todo for n in (cuda_to_xpu(cases.get(c, {}).get("test_name", "")),
                                              cases.get(c, {}).get("test_name", ""))} - {""})
    test_rel = f["source"][5:] if f["source"].startswith("test/") else f["source"]
    junit = parse_junit(run_pytest(test_rel, names, log, os.path.join(logdir, f"{run}_{ts}"), timeout))
    entries = {}
    for c in todo:
        case = cases.get(c, {})
        j = match(case, junit)
        nodeid = f"{test_rel}::{j['cls']}::{j['name']}" if j else \
            f"{test_rel}::{cuda_to_xpu(case.get('class_name', ''))}::{cuda_to_xpu(case.get('test_name', ''))}"
        entries[c] = {"run": run, "nodeid": nodeid,
                      "status": j["status"] if j else "NOT_RUN",
                      "reason": j["reason"] if j else "not collected on XPU",
                      "error_excerpt": j["error_excerpt"] if j else "",
                      "log": os.path.relpath(log, store.fdir(s)),
                      "patch_sha": psha,
                      "src_snapshot": os.path.relpath(snap, store.fdir(s)) if snap else ""}
    store.add_results(s, entries)
    counts = {}
    for e in entries.values():
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    print(f"\n[measure.py] {test_file} run={run}: {counts}  log={log}")
    return entries


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file")
    ap.add_argument("--run", choices=["initial", "gate_removed", "rework"])
    ap.add_argument("--cases", help="comma-separated case ids (default: all cases of the file)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-overlay", action="store_true")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()
    if a.restore:
        restore_overlay()
        return
    if not (a.file and a.run):
        ap.error("--file and --run are required")
    measure(a.file, a.run, a.cases.split(",") if a.cases else None, a.force,
            not a.no_overlay, a.timeout)


if __name__ == "__main__":
    main()
