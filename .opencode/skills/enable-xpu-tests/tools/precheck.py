#!/usr/bin/env python3
"""Portability pre-check for each case of a file (step A + step B of the workflow).

  precheck.py --file inductor/test_fp8.py [--csv NEW.csv] [--json]
  precheck.py --update-catalog [--yes]

Step A (API support): AST-scan each case's test method (plus same-file helpers it
calls and the decorators on it and its class) for torch.cuda.* / torch.xpu.* calls,
hardcoded "cuda" strings and device gates, and classify every API against the
pinned references/device_api_catalog.yaml (first match wins: C > B > A):
  cat A            -> replace with torch.accelerator.<api> (name_differs mapping applied)
  cat B, xpu ok    -> torch.get_device_module(device).<api>
  cat B no xpu, C  -> cuda_only_api (keep onlyCUDA; verdict comes from measurement)
Step B (wontfix): keywords (cuda-only API groups, test name, CSV xpu_message) are
matched against references/wontfix_features.md, then `gh search --label wontfix`.

Writes results.json entries {run: "precheck", apis, cuda_only_apis, gates, plan,
wontfix_candidates} per case. A precheck never sets a verdict: every case is still
force-enabled and measured (measure.py --run gate_removed).
"""
import argparse
import ast
import difflib
import json
import os
import re
import sys
import urllib.request

import yaml

import issues
import store
from common import CLONE, REFS_DIR, cuda_to_xpu, slug as make_slug

CATALOG = os.path.join(REFS_DIR, "device_api_catalog.yaml")
CATALOG_URL = ("https://raw.githubusercontent.com/KarhouTam/agent-skills/master/"
               "pytorch-test-refactoring/reference/device_api_catalog.yaml")
WONTFIX_MD = os.path.join(REFS_DIR, "wontfix_features.md")

GATE_NAMES = {"onlyCUDA", "onlyOn", "onlyCPU", "onlyNativeDeviceTypes", "onlyAccelerator",
              "skipIfXpu", "skipXPUIf", "skipXPU", "skipCUDAIf", "expectedFailureXPU",
              "requires_cuda", "requires_cuda_and_triton", "requires_gpu", "requires_triton",
              "skipIfRocm", "skipCUDAIfRocm", "largeTensorTest", "dtypesIfCUDA", "dtypesIfXPU",
              "skipUnless", "skipIf", "expectedFailure", "unittest.skip", "skip"}


# ---- catalog --------------------------------------------------------------

def load_catalog(path: str = CATALOG) -> dict[str, dict]:
    """api name -> {cat, group, backends, accel} for torch.cuda.<api>."""
    with open(path) as fh:
        y = yaml.safe_load(fh)
    out: dict[str, dict] = {}
    a = y.get("category_a", {})
    for e in a.get("same_name", []):
        out.setdefault(e["accelerator_api"], {"cat": "A", "group": "same_name",
                                              "backends": e.get("backends", []),
                                              "accel": e["accelerator_api"]})
    for e in a.get("name_differs", []):
        for d in e.get("device_apis", []):
            if d["device"] == "cuda":
                out.setdefault(d["api"], {"cat": "A", "group": "name_differs",
                                          "backends": ["cuda", "xpu"], "accel": e["accelerator_api"]})
    for group, items in (y.get("category_b") or {}).items():
        for e in items:
            out[e["api"]] = {"cat": "B", "group": group, "backends": e.get("backends", []),
                             "accel": ""}
    for group, g in ((y.get("category_c") or {}).get("cuda") or {}).items():
        for full in g.get("apis", []):
            name = full.split("#")[0].strip()
            if name.startswith("torch.cuda."):
                name = name[len("torch.cuda."):]
            out[name] = {"cat": "C", "group": group, "backends": ["cuda"], "accel": ""}
            if "." in name:
                out.setdefault(name.split(".")[0], out[name])
    # Submodules that the catalog lists only via their re-exported functions.
    for mod, group in (("jiterator", "jiterator"), ("gds", "gds"), ("green_contexts", "green_contexts"),
                       ("_sanitizer", "sanitizer"), ("comm", "nccl")):
        out.setdefault(mod, {"cat": "C", "group": group, "backends": ["cuda"], "accel": ""})
    return out


def classify(api: str, cat: dict) -> dict:
    """api is the dotted name after torch.cuda. (e.g. 'nvtx.range_push')."""
    for cand in (api, api.split(".")[0]):
        if cand in cat:
            e = dict(cat[cand])
            break
    else:
        return {"cat": "?", "group": "unknown", "action": "verify on XPU (not in catalog)"}
    if e["cat"] == "A":
        e["action"] = f"torch.accelerator.{e['accel']}"
    elif e["cat"] == "B" and "xpu" in e["backends"]:
        e["action"] = f"torch.get_device_module(device).{api}"
    else:
        e["action"] = "cuda_only_api: keep onlyCUDA"
        e["cuda_only"] = True
    return e


# ---- AST scan -------------------------------------------------------------

def _dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _deco_name(d) -> str:
    return _dotted(d.func if isinstance(d, ast.Call) else d)


class Index:
    def __init__(self, src: str):
        self.src = src
        self.tree = ast.parse(src)
        self.funcs: dict[str, ast.FunctionDef] = {}
        self.methods: dict[tuple[str, str], tuple[ast.ClassDef, ast.FunctionDef]] = {}
        self.classes: dict[str, ast.ClassDef] = {}
        for n in ast.walk(self.tree):
            if isinstance(n, ast.ClassDef):
                self.classes[n.name] = n
                for b in n.body:
                    if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.methods[(n.name, b.name)] = (n, b)
        for n in self.tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs[n.name] = n

    def find(self, cls: str, test: str):
        """Resolve an instantiated test (class TestFooXPU, test_bar_xpu_float32) to its source."""
        names = [test]
        t = re.sub(r"_(cuda|xpu|cpu)(_[a-z0-9]+)*$", "", test)
        while t and t not in names:
            names.append(t)
            t = t.rsplit("_", 1)[0] if "_" in t[5:] else ""
        bases = [cls, re.sub(r"(CUDA|XPU|CPU|GPU)$", "", cls)]
        for n in names:
            for b in bases:
                if (b, n) in self.methods:
                    return self.methods[(b, n)]
            hits = [v for k, v in self.methods.items() if k[1] == n]
            if hits:
                return hits[0]
        return None, None


def scan_case(idx: Index, cls: str, test: str, cat: dict) -> dict:
    klass, fn = idx.find(cls, test)
    if fn is None:
        return {"found": False, "plan": "source not found; locate manually (generated or parametrized test)"}
    seen, apis, gates, strings = set(), [], [], []
    todo = [fn]
    while todo:
        f = todo.pop()
        if f.name in seen:
            continue
        seen.add(f.name)
        for n in ast.walk(f):
            if isinstance(n, ast.Attribute):
                d = _dotted(n)
                if d.startswith("torch.cuda.") and not any(a["name"].startswith(d + ".") for a in apis):
                    api = d[len("torch.cuda."):]
                    apis.append({"name": d, "line": n.lineno, **classify(api, cat)})
            elif isinstance(n, ast.Constant) and n.value == "cuda":
                strings.append(n.lineno)
            elif isinstance(n, ast.Call):
                callee = _dotted(n.func)
                if callee in idx.funcs:
                    todo.append(idx.funcs[callee])
                elif callee.startswith("self.") and klass is not None and (klass.name, callee[5:]) in idx.methods:
                    todo.append(idx.methods[(klass.name, callee[5:])][1])
    uniq = {}
    for a in apis:
        uniq.setdefault(a["name"], a)
    apis = sorted(uniq.values(), key=lambda a: a["line"])
    for owner in (fn, klass):
        for d in getattr(owner, "decorator_list", []) or []:
            name = _deco_name(d)
            if name.split(".")[-1] in GATE_NAMES or name in GATE_NAMES:
                gates.append({"decorator": ast.get_source_segment(idx.src, d) or name,
                              "line": d.lineno, "on": "class" if owner is klass else "method"})
    inst = [ln for ln in idx.src.splitlines() if "instantiate_device_type_tests" in ln and klass is not None
            and re.sub(r"(CUDA|XPU|CPU)$", "", cls) in ln]
    cuda_only = [a for a in apis if a.get("cuda_only")]
    plan = []
    if any(g["decorator"].startswith("onlyCUDA") for g in gates):
        plan.append("onlyCUDA -> keep" if cuda_only else "onlyCUDA -> onlyAccelerator (only cat A/B APIs)")
    for a in apis:
        if a["cat"] in ("A", "B") and not a.get("cuda_only"):
            plan.append(f"L{a['line']}: {a['name']} -> {a['action']}")
    if strings:
        plan.append(f"hardcoded 'cuda' at lines {sorted(set(strings))} -> self.device / device")
    for ln in inst:
        if "allow_xpu" not in ln and "only_for" not in ln:
            plan.append("instantiate_device_type_tests: add allow_xpu=True")
        elif "only_for" in ln and "xpu" not in ln:
            plan.append("instantiate_device_type_tests: add 'xpu' to only_for")
    for g in gates:
        if g["decorator"] not in ("onlyCUDA",) and not g["decorator"].startswith("dtypesIf"):
            plan.append(f"remove gate L{g['line']} for the gate_removed run: {g['decorator'][:90]}")
    return {"found": True, "def_line": fn.lineno, "class": klass.name if klass else "",
            "apis": apis, "cuda_only_apis": [f"{a['name']} ({a['group']})" for a in cuda_only],
            "hardcoded_cuda_lines": sorted(set(strings)), "gates": gates, "plan": plan,
            "_groups": sorted({a["group"] for a in cuda_only})}


# ---- wontfix --------------------------------------------------------------

def load_wontfix_table(path: str = WONTFIX_MD) -> list[tuple[list[str], int]]:
    out = []
    if not os.path.exists(path):
        return out
    for ln in open(path, encoding="utf-8"):
        m = re.match(r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|", ln)
        if m:
            out.append(([k.strip().lower() for k in m.group(1).split(",") if k.strip()], int(m.group(2))))
    return out


def wontfix_candidates(test: str, src: str, groups: list[str], message: str,
                       table, use_gh: bool) -> list[dict]:
    hay = " ".join([test, src, " ".join(groups), message]).lower()
    found = {}
    for kws, num in table:
        hit = next((k for k in kws if k in hay), None)
        if hit:
            found[num] = {"number": num, "via": f"wontfix_features.md: {hit}"}
    if use_gh:
        for kw in groups:
            for h in issues.search(kw, label="wontfix", limit=5):
                found.setdefault(h["number"], {"number": h["number"], "via": f"gh search: {kw}",
                                               "title": h["title"]})
    for num, rec in found.items():
        m = issues.meta(num) if use_gh else {}
        rec.update(state=m.get("state", ""), wontfix=m.get("wontfix", None))
    return [r for r in found.values() if r.get("wontfix") is not False]


# ---- CLI ------------------------------------------------------------------

def update_catalog(yes: bool) -> None:
    new = urllib.request.urlopen(CATALOG_URL, timeout=60).read().decode()
    old = open(CATALOG, encoding="utf-8").read()
    body = "\n".join(old.splitlines()[2:]) + "\n"
    if body.strip() == new.strip():
        print("catalog unchanged")
        return
    sys.stdout.writelines(difflib.unified_diff(body.splitlines(True), new.splitlines(True),
                                               "pinned", "upstream"))
    if not yes and input("\noverwrite pinned catalog? [y/N] ").strip().lower() != "y":
        return
    import time
    hdr = old.splitlines()[0] + "\n" + f"# Fetched: {time.strftime('%Y-%m-%d')}. Refresh with: tools/precheck.py --update-catalog\n"
    open(CATALOG, "w", encoding="utf-8").write(hdr + new)
    print("updated", CATALOG)


def run(test_file: str, csv_path: str = "", use_gh: bool = True) -> dict:
    s = make_slug(test_file)
    f = store.load_file(s)
    cases = store.load_cases(s)
    src_path = os.path.join(CLONE, f["source"] or ("test/" + test_file))
    idx = Index(open(src_path, encoding="utf-8").read())
    cat = load_catalog()
    table = load_wontfix_table()
    msgs = {r["case_id"]: r.get("xpu_message", "") for r in store.read_csv(csv_path)} if csv_path else {}
    entries, out = {}, {}
    for cid, c in cases.items():
        r = scan_case(idx, c.get("class_name", ""), c.get("test_name", ""), cat)
        groups = r.pop("_groups", [])
        fn_src = ""
        if r.get("found"):
            fn_src = "\n".join(idx.src.splitlines()[r["def_line"] - 1:r["def_line"] + 80])
        r["wontfix_candidates"] = wontfix_candidates(c.get("test_name", ""), fn_src, groups,
                                                     msgs.get(cid, ""), table, use_gh)
        entries[cid] = {"run": "precheck", "nodeid": cuda_to_xpu(c.get("test_name", "")), **r}
        out[cid] = r
        if r.get("cuda_only_apis"):
            store.update_cases(s, {cid: {"cuda_only": True}})
    store.add_results(s, entries)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file")
    ap.add_argument("--csv", default="")
    ap.add_argument("--no-gh", action="store_true", help="skip gh lookups (offline)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--update-catalog", action="store_true")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    if a.update_catalog:
        update_catalog(a.yes)
        return
    if not a.file:
        ap.error("--file is required")
    out = run(a.file, a.csv, not a.no_gh)
    if a.json:
        print(json.dumps(out, indent=2))
        return
    cases = store.load_cases(make_slug(a.file))
    for cid, r in out.items():
        c = cases[cid]
        print(f"\n[{cid}] {c.get('class_name')}::{c.get('test_name')}")
        if not r.get("found"):
            print("   ", r["plan"])
            continue
        for a_ in r["apis"]:
            print(f"    L{a_['line']} {a_['name']}: cat {a_['cat']} ({a_['group']}) -> {a_['action']}")
        for p in r["plan"]:
            print("    plan:", p)
        for w in r["wontfix_candidates"]:
            print(f"    wontfix candidate #{w['number']} ({w['via']}, state={w.get('state')})")


if __name__ == "__main__":
    main()
