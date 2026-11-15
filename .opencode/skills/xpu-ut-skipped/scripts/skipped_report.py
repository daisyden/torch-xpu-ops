#!/usr/bin/env python3
"""Build ut_regression_pvc_cuda_skipped.xlsx from passed -> skipped cases.

For every case whose Status is "passed -> skipped" in ut_regression_pvc_cuda.csv,
copy the row and determine WHY it is skipped, writing a "Detailed Reason" column.

Detailed Reason is resolved in priority order:
  1. Issue column already set -> enrich with the issue title + state from
     intel/torch-xpu-ops (via `gh`, cached).
  2. In-source skip decorator on the XPU test method (parsed from test/<file>.py):
     use the decorator's msg and any issue number/URL it references.
  3. Match the test name against OPEN intel/torch-xpu-ops issues (cached index).
  4. Fallback: "Skipped on XPU; no explicit reason found in source or tracked
     issue."

Usage:
    python skipped_report.py --csv ut_regression_pvc_cuda.csv \
        --out ut_regression_pvc_cuda_skipped.xlsx [--repo-root .] [--no-gh]

Requires `gh` authenticated for issue titles (skip with --no-gh).
"""
import argparse
import csv
import json
import os
import re
import subprocess
from functools import lru_cache

REPO = "intel/torch-xpu-ops"
CACHE = "/tmp/opencode/xpu_ops_issue_cache.json"

SKIP_DECORATORS = (
    "skipIfXpu", "skipXPU", "skipXPUIf", "unittest.skip", "skip(",
    "expectedFailureXPU", "skipCUDAIf", "skipIf",
)
ISSUE_RE = re.compile(r"(?:torch-xpu-ops/issues/|#)(\d+)")


def norm_header(rows):
    H = [c.strip().strip('"').lstrip("\ufeff") for c in rows[0]]
    return H, {c: i for i, c in enumerate(H)}


# ---- gh issue lookup -------------------------------------------------------

def _load_cache():
    if os.path.exists(CACHE):
        with open(CACHE) as fh:
            return json.load(fh)
    return {}


def _save_cache(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as fh:
        json.dump(c, fh)


def fetch_issue(num, cache, enable_gh):
    key = str(num)
    if key in cache:
        return cache[key]
    if not enable_gh:
        return None
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{REPO}/issues/{num}",
             "--jq", "{title: .title, state: .state}"],
            capture_output=True, text=True, timeout=30)
        data = json.loads(out.stdout) if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        data = None
    cache[key] = data
    return data


# ---- in-source decorator extraction ---------------------------------------

def file_to_path(xpu_file, root):
    # 'test.test_ops' -> test/test_ops.py ; 'test.nn.test_convolution' -> ...
    rel = xpu_file.replace(".", "/") + ".py"
    p = os.path.join(root, rel)
    return p if os.path.exists(p) else None


def base_method(name):
    # strip device/dtype/param suffixes: keep up to first '_<lowerword>_cuda'/param
    n = re.sub(r"_(cuda|xpu)(_\w+)?$", "", name)
    return n


@lru_cache(maxsize=256)
def _read(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.readlines()
    except OSError:
        return []


def source_skip_reason(path, method):
    """Find a skip decorator (and its msg/issue/comment) above the def whose
    name is the longest prefix of the (mangled) test name."""
    lines = _read(path)
    if not lines:
        return None
    # map every def name -> its line index
    defs = {}
    for i, ln in enumerate(lines):
        m = re.match(r"\s*def (\w+)\s*\(", ln)
        if m:
            defs.setdefault(m.group(1), i)
    # longest def name that is a prefix of method at a word boundary
    best = None
    for name, i in defs.items():
        if (method == name or method.startswith(name + "_")) and \
                (best is None or len(name) > len(best[0])):
            best = (name, i)
    if best is None:
        return None
    target = best[1]
    # collect the decorator region above the def, bracket-aware so that
    # multi-line decorators (e.g. @unittest.skipIf(cond, "long msg")) are kept.
    block = []
    j = target - 1
    depth = 0
    while j >= 0:
        s = lines[j]
        st = s.strip()
        o = s.count("(") + s.count("[")
        c = s.count(")") + s.count("]")
        if depth > 0:
            block.append(s)
            depth += c - o
            j -= 1
            continue
        if st.startswith("@") or st.startswith("#") or st == "":
            block.append(s)
            depth += c - o
            j -= 1
            continue
        if c > o:  # trailing continuation line of a multi-line decorator
            block.append(s)
            depth += c - o
            j -= 1
            continue
        break
    block_lines = list(reversed(block))
    block = "".join(block_lines)
    if not any(d in block for d in SKIP_DECORATORS):
        return None
    parts = []
    # choose the decorator that actually triggers the XPU skip: prefer the
    # @-line carrying an issue ref, then an XPU-specific decorator, else first.
    XPU_PRI = ("skipXPUIf", "skipIfXpu", "skipXPU", "expectedFailureXPU")
    hits = []  # (priority, name, line)
    for ln in block_lines:
        if not ln.lstrip().startswith("@"):
            continue
        for d in sorted(SKIP_DECORATORS, key=len, reverse=True):
            if ("@" + d) in ln:
                nm = d.rstrip("(")
                pri = 0 if ISSUE_RE.search(ln) else (
                    1 if any(x in ln for x in XPU_PRI) else 2)
                hits.append((pri, nm, ln))
                break
    deco = min(hits, key=lambda h: h[0])[1] if hits else "skip"
    # any quoted string inside a skip decorator (msg= or positional)
    msgs = []
    for ln in block_lines:
        if ln.lstrip().startswith("@") and any(d in ln for d in SKIP_DECORATORS):
            for q in re.findall(r'["\']([^"\']{4,})["\']', ln):
                if not q.startswith("http"):
                    msgs.append(q)
    if msgs:
        parts.append('reason: "' + " | ".join(dict.fromkeys(msgs)) + '"')
    issue = ISSUE_RE.search(block)
    if issue:
        parts.append(f"issue #{issue.group(1)}")
    # explanatory comment only when no explicit msg/issue, trimmed
    if not msgs and not issue:
        comments = " ".join(ln.strip().lstrip("#").strip()
                            for ln in block_lines if ln.lstrip().startswith("#"))
        comments = comments.strip()
        if comments:
            parts.append("context: " + comments[:180])
    return f"In-source @{deco}" + (" (" + "; ".join(parts) + ")." if parts else " (unconditional XPU skip).")


# ---- op_db skip matching ---------------------------------------------------

def load_opdb(path):
    """Index op-db skips by op_us (op name underscored). Returns (index, order)
    where order is op_us sorted longest-first for greedy matching."""
    if not path or not os.path.exists(path):
        return {}, []
    data = json.load(open(path))
    index = {}
    for e in data:
        index.setdefault(e["op_us"], []).append(e)
    order = sorted(index, key=len, reverse=True)
    return index, order


def opdb_skip_reason(name, klass, index, order):
    """Match an OpInfo test (mangled name + class) to an op-db skip/xfail."""
    if not index:
        return None
    padded = "_" + name + "_"
    base_cls = re.sub(r"(XPU|CUDA)$", "", klass)
    for op_us in order:
        if ("_" + op_us + "_") not in padded:
            continue
        for e in index[op_us]:
            tn, cn = e["test_name"], e["cls_name"]
            if tn and not (name == tn or name.startswith(tn + "_") or ("_" + tn + "_") in padded):
                continue
            if cn and cn != base_cls:
                continue
            reason = f' reason: "{e["reason"]}"' if e["reason"] else ""
            dev = e["device_type"] or "all-devices"
            active = "" if e["active_if"] in ("True", "None") else f" active_if={e['active_if']}"
            return (f"op_db DecorateInfo {e['kind']} for op '{e['op']}' "
                    f"(device_type={dev}{active}).{reason}")
    return None


# ---- main ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", default="ut_regression_pvc_cuda_skipped.xlsx")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--no-gh", action="store_true")
    ap.add_argument("--open-issues", help="JSON list of open issues "
                    "[{number,title}] to match issueless cases by test name")
    ap.add_argument("--opdb-skips", help="JSON from dump_opdb_skips.py")
    a = ap.parse_args()

    with open(a.csv, newline="") as fh:
        rows = list(csv.reader(fh))
    H, idx = norm_header(rows)
    S = idx["Status"]
    skipped = [r for r in rows[1:] if r[S] == "passed \u2192 skipped"]

    cache = _load_cache()
    open_index = []
    if a.open_issues and os.path.exists(a.open_issues):
        open_index = json.load(open(a.open_issues))
    opdb_index, opdb_order = load_opdb(a.opdb_skips)

    out_header = H + ["Detailed Reason"]
    out_rows = [out_header]
    stats = {"source": 0, "opdb": 0, "issue": 0, "openmatch": 0, "none": 0}
    for r in skipped:
        issue = r[idx["Issue"]].strip()
        url = r[idx["Issue URL"]].strip()
        istat = r[idx["Issue Status"]].strip()
        name = r[idx["XPU Test Name"]]
        detail = None

        # 1) in-source skip decorator on the method (most specific)
        path = file_to_path(r[idx["XPU Test File"]], a.repo_root)
        if path:
            sr = source_skip_reason(path, name)
            if sr:
                detail = sr
                stats["source"] += 1

        # 2) op_db DecorateInfo skip/xfail (OpInfo tests)
        if detail is None:
            od = opdb_skip_reason(name, r[idx["XPU Test Class"]], opdb_index, opdb_order)
            if od:
                detail = od
                stats["opdb"] += 1

        # 3) curated Issue column enriched via gh
        if detail is None and issue:
            num = issue.lstrip("#")
            info = fetch_issue(num, cache, not a.no_gh)
            title = f": {info['title']}" if info and info.get("title") else ""
            state = info["state"] if info and info.get("state") else (istat or "unknown")
            detail = f"Skipped per {REPO} {issue} ({state}){title}. {url}".strip()
            stats["issue"] += 1

        # 4) open intel/torch-xpu-ops issue naming the exact test
        if detail is None and open_index and name and len(name) >= 12:
            wb_re = re.compile(r"\b" + re.escape(name) + r"\b")
            for it in open_index:
                if wb_re.search(it.get("title", "")):
                    detail = (f"Likely tracked by open {REPO} #{it['number']}: "
                              f"{it['title']}.")
                    stats["openmatch"] += 1
                    break

        # 5) fallback
        if detail is None:
            detail = ("Skipped on XPU; no explicit reason found in source, op_db, "
                      "or a tracked intel/torch-xpu-ops issue. Needs manual triage.")
            stats["none"] += 1

        # never drop a curated issue number: append if present and not shown
        if issue and issue.lstrip("#") not in detail:
            detail += f" [tracked {REPO} {issue}{(' ' + url) if url else ''}]"

        out_rows.append(r + [detail])

    _save_cache(cache)

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "passed to skipped"
    for rr in out_rows:
        ws.append(rr)
    ws.freeze_panes = "A2"
    wb.save(a.out)
    print(f"wrote {a.out}: {len(skipped)} rows")
    print("detail sources:", stats)


if __name__ == "__main__":
    main()
