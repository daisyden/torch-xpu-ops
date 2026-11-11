#!/usr/bin/env python3
"""Check that every verdict in the report tree is backed by evidence.

  verify_evidence.py [--file inductor/test_fp8.py ...] [--no-gh] [--json] [--warn-as-error]

Rules (KI/FG/NA = Known Issue / Feature gap / Not Applicable):
  R1  Portable: the latest run of the FINAL code (initial/rework at the current
      patch_sha, not a gate_removed run) is PASSED/XPASS. A case that is still
      SKIPPED by the shipped code is never Portable.                                  ERROR
  R2  KI/FG/NA: has issue_number, or a draft_id whose draft lists the case.            ERROR
  R3  KI/FG/NA: a cited issue exists; CLOSED and not wontfix = stale.                  ERROR
  R4  NA: cited issue is OPEN or wontfix-labelled.                                     WARN
  R5  KI/FG/NA: the latest gate_removed result is FAILED/ERROR (SKIPPED/NOT_RUN there
      means a gate is still active). Exempt: cited_existing issue that gh confirms
      OPEN or wontfix.                                                                 ERROR
  R6  the result's log exists, mentions the test id, and contains its error line.     ERROR
  R7  the gate_removed snapshot differs from the final source only by added gate
      lines (skip/xfail/DecorateInfo/onlyCUDA/requires_cuda + their msg).             ERROR
  R8  final source carries the gate for the case: msg == issue URL (or
      issues/<placeholder>). Applies to cited-existing cases too. Exception:
      cuda_only / cpu_only cases (not instantiated on XPU by design) keep their
      device gate and only need the issue in cases.json.                               ERROR
  R9  an unfiled draft-backed case: draft body contains its error excerpt and a repro
      command (filed drafts are not re-checked).                                       ERROR
  R10 reason is non-empty.                                                             WARN

Exit status is nonzero when any ERROR is found (or any WARN with --warn-as-error).
If gh is unreachable, R3/R4 are downgraded to WARN.
"""
import argparse
import difflib
import json
import os
import re
import sys

import gitops
import issues
import store
from common import CLONE, NON_PORTABLE, canon_dev, cuda_to_xpu, issue_url

GATE_LINE = re.compile(r"(skipIfXpu|skipXPUIf|skipXPU|expectedFailureXPU|expectedFailure\(|DecorateInfo|unittest\.skip|"
                       r"subtest\(.*decorators=|"
                       r"unittest\.expectedFailure|onlyCUDA|requires_cuda|requires_gpu|skipIf|skipUnless|"
                       r"issues/|device_type=|msg=|^\s*\)\s*,?\s*$|^\s*#)")


# a bare name in an import list for a gate decorator (e.g. "    expectedFailure,")
GATE_IMPORT = re.compile(r"^\s+(skipIfXpu|skipXPUIf|skipXPU|expectedFailure\w*|onlyCUDA|onlyAccelerator|"
                         r"requires_gpu\w*|requires_cuda\w*|skipCUDAIf),?\s*$")


def _only_added_gate_names(before: str, after: str) -> bool:
    """True if `after` is `before` plus extra gate-decorator names (comma/backslash list)."""
    tok = lambda x: [t for t in re.split(r"[\s,\\()]+", x) if t]
    b, a = tok(before), tok(after)
    if not b or len(a) < len(b) or a[:len(b)] != b:
        return False
    return all(GATE_IMPORT.match("    " + t) for t in a[len(b):])


def _gate_decorated(before: str, after: str) -> bool:
    """`after` is `before` with only a gate added inside it, e.g. a subtest(...) line that
    gained `decorators=[skipIfXpu(...)]`."""
    if not GATE_LINE.search(after):
        return False
    stripped = re.sub(r",?\s*decorators=\[[^\]]*\]", "", after)
    return stripped.strip() == before.strip()


def _is_gate_import_cont(line: str) -> bool:
    """A continuation line of a multi-line import that only lists gate decorator names."""
    toks = [t for t in re.split(r"[\s,\\()]+", line) if t]
    return bool(toks) and all(GATE_IMPORT.match("    " + t) for t in toks)


def _final_source(f: dict, path: str) -> str:
    """The file as it will ship: the working tree when the agent branch is checked out
    (uncommitted edits count), else the agent branch tip, else the base."""
    on_branch = f["agent_branch"] and gitops.current_branch() == f["agent_branch"]
    if on_branch or not f["agent_branch"]:
        p = os.path.join(CLONE, path)
        if os.path.exists(p):
            return open(p, encoding="utf-8").read()
    if f["agent_branch"] and gitops.branch_exists(f["agent_branch"]):
        return gitops.git("show", f"{f['agent_branch']}:{path}", check=False)
    return gitops.git("show", f"{f['base_sha']}:{path}", check=False) if f.get("base_sha") else ""


def _owned_paths(f: dict) -> list[str]:
    """Files this test file's patch may touch: its own source plus anything its
    agent branch / working tree changed vs base (helpers, op_db, wrapped test modules)."""
    paths = {f["source"]} if f["source"] else set()
    try:
        tip = "HEAD" if gitops.current_branch() == f["agent_branch"] else f["agent_branch"]
        if f.get("base_sha") and tip:
            paths |= set(gitops.git("diff", "--name-only", f["base_sha"], *([] if tip == "HEAD" else [tip])).split())
    except gitops.GitError:
        pass
    return sorted(p for p in paths if p)


def _r7(fd: str, snap: str, f: dict) -> list[str]:
    """Lines changed between the gate_removed snapshot and the final source that are
    not gate lines. Only files this test file owns are compared; a file the snapshot
    has but the final patch no longer touches is compared against base."""
    bad = []
    sdir = os.path.join(fd, snap)
    owned = set(_owned_paths(f))
    for root, _, files in os.walk(sdir):
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), sdir)
            if rel not in owned:
                continue
            # lines marked MEASURE-ONLY are temporary overrides for the run; compare without the marker
            # MEASURE-ONLY lines are temporary overrides for the gate-removed run: a whole
            # line marked so is dropped, a trailing marker is stripped
            measured = []
            for ln in open(os.path.join(root, name), encoding="utf-8").read().splitlines():
                if "# MEASURE-ONLY" not in ln:
                    measured.append(ln)
                elif ln.split("# MEASURE-ONLY")[0].rstrip().endswith((":", ")")):
                    measured.append(ln.split("  # MEASURE-ONLY")[0].rstrip())
            final = _final_source(f, rel).splitlines()
            removed = {ln[1:] for ln in difflib.unified_diff(measured, final, lineterm="", n=0)
                       if ln.startswith("-") and not ln.startswith("---")}
            for ln in difflib.unified_diff(measured, final, lineterm="", n=0):
                if ln.startswith(("---", "+++", "@@")):
                    continue
                body = ln[1:]
                # an existing import line that only gained gate decorator names
                if ln.startswith("+") and any(_only_added_gate_names(r, body) for r in removed):
                    continue
                if ln.startswith("-") and any(_only_added_gate_names(body, a[1:]) or _gate_decorated(body, a[1:])
                                              for a in difflib.unified_diff(measured, final, lineterm="", n=0)
                                              if a.startswith("+") and not a.startswith("+++")):
                    continue
                # measure-only overrides (e.g. forcing a capability flag on hardware that
                # lacks it) must carry this marker and are expected to be gone in the final code
                if "MEASURE-ONLY" in body:
                    continue
                if GATE_IMPORT.match(body) or _is_gate_import_cont(body):
                    continue
                if body.strip() and not GATE_LINE.search(body):
                    bad.append(f"{rel}: {ln[:120]}")
    return bad


def _opinfo_names(test_name: str) -> list[str]:
    """Candidate OpInfo names inside an op_db test id, e.g.
    test_out_warning_torch_ops_aten__flash_attention_forward_cuda -> '_flash_attention_forward'."""
    t = re.sub(r"_(cuda|xpu)(_[a-z0-9]+)?$", "", test_name)
    parts = t.split("_")
    out = []
    for i in range(2, len(parts)):
        tail = "_".join(parts[i:])
        if len(tail) > 4:
            out += [tail, tail.replace("_", "."), "_" + tail if not tail.startswith("_") else tail]
    return out


def _gate_ok(src: str, case: dict, draft: dict | None) -> bool:
    name = re.sub(r"_(cuda|xpu)(_\w+)?$", "", case.get("test_name", ""))
    targets = []
    if case.get("issue_number"):
        targets.append(issue_url(case["issue_number"]).split("github.com/")[1])
    if draft:
        targets.append(f"issues/{draft.get('placeholder') or 'PLACEHOLDER'}")
    if not targets:
        return False
    lines = src.splitlines()
    for i, ln in enumerate(lines):
        if any(t in ln for t in targets):
            window = "\n".join(lines[max(0, i - 6):i + 25])
            if name and (name in window or canon_dev(name) in canon_dev(window)):
                return True
            # parametrized tests: the def name is a prefix of the instantiated name
            m = re.search(r"^\s*def (test_\w+)\(", window, re.M)
            if m and canon_dev(case.get("test_name", "")).startswith(canon_dev(m.group(1))):
                return True
            # op_db gate: the issue URL sits in the OpInfo entry, which names the op
            wide = "\n".join(lines[max(0, i - 40):i + 5])
            if "OpInfo(" in wide and any(f"'{n}'" in wide or f'"{n}"' in wide or f".{n}'" in wide
                                         for n in _opinfo_names(case.get("test_name", ""))):
                return True
    return False


def check_file(slug: str, use_gh: bool = True) -> list[dict]:
    f = store.load_file(slug)
    cases = store.load_cases(slug)
    results = store.load_results(slug)
    drafts = {d["id"]: d for d in store.load_drafts(slug)}
    fd = store.fdir(slug)
    src = "\n".join(_final_source(f, p) for p in _owned_paths(f))
    probs = []

    for cid, c in cases.items():
        v = (c.get("portability") or "").strip()
        if not v:
            continue

        def add(rule, sev, msg):
            probs.append({"file": f["test_file"], "case_id": cid, "test": c.get("test_name", ""),
                          "verdict": v, "rule": rule, "severity": sev, "message": msg})

        runs = [e for e in results.get(cid, []) if e.get("run") != "precheck"]
        if not (c.get("reason") or "").strip():
            add("R10", "WARN", "empty reason")

        if v == "Portable":
            # Portable is judged on the code that ships: a run at the current patch that is
            # not a gate-removed experiment. A PASS with the gate removed does not count if
            # the final code still skips the case (SKIPPED is never Portable).
            final_runs = [x for x in runs if x.get("run") != "gate_removed"
                          and (x.get("patch_sha") or "") == (f["patch_sha"] or "")]
            e = store.latest(final_runs)
            if c.get("fix_pr") and (not e or e["status"] not in ("PASSED", "XPASS")):
                # fixed by another (existing) PR: accept a PASSED run measured on that PR's branch
                e = next((x for x in reversed(runs) if "PR #" in (x.get("reason") or "")
                          and x["status"] in ("PASSED", "XPASS")), e)
            if not e:
                add("R1", "ERROR", "no measurement at the current patch")
            elif e["status"] not in ("PASSED", "XPASS"):
                add("R1", "ERROR", f"latest result is {e['status']} ({e.get('reason', '')[:80]}); "
                                   "SKIPPED is never Portable")
            else:
                _check_log(fd, e, add)
            continue
        if v not in NON_PORTABLE:
            add("R0", "ERROR", f"unknown portability {v!r}")
            continue

        draft = drafts.get(c.get("draft_id", ""))
        if draft and cid not in map(str, draft.get("cases", [])):
            draft = None
        num = str(c.get("issue_number") or "").strip()
        if not num and not draft:
            add("R2", "ERROR", "no issue_number and no draft listing this case")

        state = ""
        if num and use_gh:
            m = issues.meta(num)
            state = issues.citable(m)
            if state == "unknown":
                add("R3", "WARN", f"#{num}: could not read issue state (gh offline?)")
            elif state == "stale":
                add("R3", "ERROR", f"#{num} is CLOSED and not wontfix: draft a new issue")
            if v == "Not Applicable" and state not in ("open", "wontfix", "unknown"):
                add("R4", "WARN", f"Not Applicable cites #{num} which is {state}")

        exempt = bool(c.get("cited_existing")) and state in ("open", "wontfix")
        # evidence for a non-portable verdict must come from a run with the gate removed;
        # rework runs execute the final code (gate in place), so they don't count here
        gr = store.latest(runs, ["gate_removed"])
        if not exempt:
            if not gr:
                add("R5", "ERROR", "no gate_removed run: remove the skips and measure")
            elif gr["status"] not in ("FAILED", "ERROR"):
                add("R5", "ERROR", f"gate_removed run is {gr['status']} ({gr.get('reason', '')[:80]}): "
                                   "a gate is still active, or the case passes")
            else:
                _check_log(fd, gr, add)
                if c.get("cuda_only") or c.get("cpu_only"):
                    pass  # final code restores the device-only gate; the port attempt is not shipped
                elif gr.get("src_snapshot"):
                    extra = _r7(fd, gr["src_snapshot"], f)
                    if extra:
                        add("R7", "ERROR", "final code differs from the measured code beyond gate lines: "
                                           + "; ".join(extra[:3]))
                else:
                    add("R7", "ERROR", "gate_removed result has no source snapshot")

        if src:
            if c.get("cuda_only") or c.get("cpu_only"):
                # not instantiated on XPU by design: keep the device gate, issue lives in cases.json
                if not (num or draft):
                    add("R8", "ERROR", "device-only case needs its issue recorded in cases.json")
            elif not _gate_ok(src, c, draft):
                add("R8", "ERROR", "final source has no gate near this test whose msg is the issue URL"
                                   " / issues/<placeholder>")

        if draft and not draft.get("number") and gr and gr.get("error_excerpt"):
            body = draft.get("body", "")
            first = next((ln[2:].strip() for ln in gr["error_excerpt"].splitlines() if ln[2:].strip()), "")
            if first and first[:60] not in body:
                add("R9", "ERROR", f"draft {draft['id']} body lacks the error line: {first[:80]}")
            if "pytest" not in body and "python " not in body:
                add("R9", "ERROR", f"draft {draft['id']} body lacks a repro command")
    return probs


def _check_log(fd: str, e: dict, add) -> None:
    p = os.path.join(fd, e.get("log", ""))
    if not e.get("log") or not os.path.exists(p):
        add("R6", "ERROR", f"log not found: {e.get('log')}")
        return
    text = open(p, encoding="utf-8", errors="replace").read()
    tid = e["nodeid"].rsplit("::", 1)[-1]
    if tid not in text and cuda_to_xpu(tid) not in text:
        add("R6", "ERROR", f"log {e['log']} does not mention {tid}")
        return
    if e["status"] in ("FAILED", "ERROR"):
        first = next((ln[2:].strip() for ln in (e.get("error_excerpt") or "").splitlines() if ln[2:].strip()), "")
        if first and first[:60] not in text:
            add("R6", "ERROR", f"log lacks the recorded error line: {first[:80]}")


def check(files=None, use_gh: bool = True) -> list[dict]:
    from common import slug as make_slug
    slugs = [make_slug(x) for x in files] if files else store.list_slugs()
    out = []
    for s in slugs:
        out.extend(check_file(s, use_gh))
    return out


def summary(probs: list[dict]) -> dict[str, dict[str, list]]:
    """{file: {case_id: [problems]}} for the UI badges."""
    out: dict[str, dict[str, list]] = {}
    for p in probs:
        out.setdefault(p["file"], {}).setdefault(p["case_id"], []).append(p)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", action="append")
    ap.add_argument("--no-gh", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--warn-as-error", action="store_true")
    a = ap.parse_args()
    probs = check(a.file, not a.no_gh)
    if a.json:
        json.dump(probs, sys.stdout, indent=2)
        print()
    else:
        for p in probs:
            print(f"{p['severity']:5} {p['rule']:3} {p['file']} [{p['case_id']}] {p['test']} "
                  f"({p['verdict']}): {p['message']}")
        ne = sum(p["severity"] == "ERROR" for p in probs)
        nw = len(probs) - ne
        print(f"\n{ne} errors, {nw} warnings")
    bad = any(p["severity"] == "ERROR" or (a.warn_as_error and p["severity"] == "WARN") for p in probs)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
