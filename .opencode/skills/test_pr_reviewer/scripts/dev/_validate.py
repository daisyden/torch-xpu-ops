"""Validate matcher.py against ground truth derived independently of it.

Ground truth for a *moved* method: a head method whose normalised body is
>=0.75 similar to exactly one base method with the same name (or a name that
differs only by device words).  These are unambiguous, so the matcher must get
them right.  Also reports coverage and 1->N / N->1 class relations.
"""
from __future__ import annotations

import ast
import difflib
import re
import sys
from collections import Counter, defaultdict

import matcher
import prdata


def gt_methods(text: str) -> dict[str, tuple[str, list[str]]]:
    """qualname -> (name, normalised body). Independent of matcher's Unit logic."""
    out: dict[str, tuple[str, list[str]]] = {}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    lines = text.split("\n")

    def walk(body, prefix):
        for n in body:
            if isinstance(n, ast.ClassDef):
                walk(n.body, prefix + n.name + ".")
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = prefix + n.name
                src = lines[n.lineno - 1 : (n.end_lineno or n.lineno)]
                body_n = [
                    re.sub(r"\s+", "", s)
                    for s in (x.strip() for x in src)
                    if s and not s.startswith("#")
                ]
                out[q] = (n.name, body_n)

    walk(tree.body, "")
    return out


def ratio(a, b):
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def build_truth(base_text, head_text):
    """Return {head_qual: base_qual} unambiguous correspondences."""
    b = gt_methods(base_text)
    h = gt_methods(head_text)
    by_name = defaultdict(list)
    for q, (nm, body) in b.items():
        by_name[nm].append(q)

    truth = {}
    for hq, (hnm, hbody) in h.items():
        if len(hbody) < 4:
            continue
        pool = list(by_name.get(hnm, []))
        if not pool:
            pool = [
                q
                for q, (nm, _) in b.items()
                if matcher.name_key(nm) == matcher.name_key(hnm) and matcher.name_key(nm)
            ]
        scored = [(ratio(b[q][1], hbody), q) for q in pool]
        good = [(s, q) for s, q in scored if s >= 0.75]
        good.sort(reverse=True)
        if len(good) == 1:
            truth[hq] = good[0][1]
        elif len(good) > 1 and good[0][0] - good[1][0] > 0.15:
            truth[hq] = good[0][1]
    return truth


NUMS = [int(x) for x in sys.argv[1:]]
tot_truth = tot_ok = tot_missing = 0
tot_head_units = tot_matched = 0
fails = []
class_rel = Counter()
file_rows = []

for num in NUMS:
    try:
        pr = prdata.load_pr(str(num))
    except Exception as e:
        print(f"!! PR {num}: {e}")
        continue
    for fd in pr.files:
        if not fd.path.endswith(".py"):
            continue
        bt, ht = pr.base_text(fd), pr.head_text(fd)
        if not bt or not ht:
            continue
        truth = build_truth(bt, ht)
        if not truth:
            continue
        m = matcher.build_match(bt, ht, fd.path)
        hq_index = {u.qualname: u for u in m.head.callables}
        ok = miss = wrong = 0
        for hq, bq in truth.items():
            hu = hq_index.get(hq)
            if hu is None:
                continue
            cands = m.candidates("head", hu.uid)
            if not cands:
                miss += 1
                fails.append((num, fd.path, hq, bq, "NO CANDIDATE", ""))
                continue
            top = cands[0].other.qualname
            if top == bq:
                ok += 1
            elif any(c.other.qualname == bq for c in cands):
                ok += 1  # present, reviewer can switch; count as acceptable
                rank = [c.other.qualname for c in cands].index(bq)
                fails.append((num, fd.path, hq, bq, f"rank {rank}", top))
            else:
                wrong += 1
                fails.append((num, fd.path, hq, bq, "WRONG", top))
        tot_truth += ok + miss + wrong
        tot_ok += ok
        tot_missing += miss + wrong
        matched_head = sum(1 for u in m.head.callables if m.candidates("head", u.uid))
        tot_head_units += len(m.head.callables)
        tot_matched += matched_head
        for bq, targets in m.class_map.items():
            strong = [t for t in targets if t["share"] >= 0.1]
            class_rel[f"1->{len(strong)}"] += 1
        file_rows.append((num, fd.path, ok, miss + wrong, len(m.base.callables), len(m.head.callables)))

print("\n=== per-file (only files with usable ground truth) ===")
for num, path, ok, bad, nb, nh in file_rows:
    flag = "" if bad == 0 else f"   <-- {bad} bad"
    print(f"{num:>7} {path[-52:]:<52} ok={ok:<4} bad={bad:<3} base_units={nb:<4} head_units={nh:<4}{flag}")

print(f"\n=== totals ===")
print(f"ground-truth pairs : {tot_truth}")
print(f"correct (top-1 or in candidate list): {tot_ok}  ({100*tot_ok/max(tot_truth,1):.2f}%)")
print(f"missed/wrong       : {tot_missing}")
print(f"head units with any candidate: {tot_matched}/{tot_head_units} ({100*tot_matched/max(tot_head_units,1):.1f}%)")
print(f"class relations    : {dict(class_rel)}")

print(f"\n=== first 40 imperfect ===")
for f in fails[:40]:
    print(f"  {f[0]} {f[1][-34:]:<34} {f[2][:52]:<52} want={f[3][:46]:<46} {f[4]} got={f[5][:40]}")
