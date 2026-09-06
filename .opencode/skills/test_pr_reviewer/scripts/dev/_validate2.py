"""Stricter, name-agnostic validation of matcher.py.

Ground truth here never looks at names: a head method is paired with a base
method when their normalised bodies are >= 0.90 similar AND that base method is
the unique such candidate by a clear margin.  This covers device renames
(test_cuda_x -> test_accelerator_x) and arbitrary renames alike.

Reports STRICT top-1 accuracy (candidate-list credit is reported separately).
"""
from __future__ import annotations

import ast
import difflib
import re
import sys
from collections import Counter, defaultdict

import matcher
import prdata


def methods(text):
    out = {}
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
                src = lines[n.lineno - 1 : (n.end_lineno or n.lineno)]
                body_n = [
                    re.sub(r"\s+", "", s)
                    for s in (x.strip() for x in src)
                    if s and not s.startswith("#")
                ]
                out[prefix + n.name] = body_n

    walk(tree.body, "")
    return out


def r(a, b):
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def truth_name_agnostic(bt, ht, min_lines=6):
    b, h = methods(bt), methods(ht)
    # index base bodies by rare lines for speed
    df = Counter()
    idx = defaultdict(list)
    for q, body in b.items():
        for ln in set(body):
            df[ln] += 1
            idx[ln].append(q)
    truth = {}
    for hq, hbody in h.items():
        if len(hbody) < min_lines:
            continue
        votes = Counter()
        for ln in set(hbody):
            if df[ln] > 25:
                continue
            for q in idx[ln]:
                votes[q] += 1
        pool = [q for q, _ in votes.most_common(25)]
        scored = sorted(((r(b[q], hbody), q) for q in pool), reverse=True)
        good = [(s, q) for s, q in scored if s >= 0.90]
        if len(good) == 1:
            truth[hq] = (good[0][1], good[0][0])
        elif len(good) > 1 and good[0][0] - good[1][0] > 0.05:
            truth[hq] = (good[0][1], good[0][0])
    return truth


NUMS = [int(x) for x in sys.argv[1:]]
T = OK1 = OKN = BAD = 0
fails = []
rows = []
kinds = Counter()

for num in NUMS:
    try:
        pr = prdata.load_pr(str(num))
    except Exception as e:
        print(f"!! {num}: {e}")
        continue
    for fd in pr.files:
        if not fd.path.endswith(".py"):
            continue
        bt, ht = pr.base_text(fd), pr.head_text(fd)
        if not bt or not ht:
            continue
        truth = truth_name_agnostic(bt, ht)
        if not truth:
            continue
        m = matcher.build_match(bt, ht, fd.path)
        hidx = {u.qualname: u for u in m.head.callables}
        ok1 = okn = bad = 0
        for hq, (bq, sim) in truth.items():
            hu = hidx.get(hq)
            if hu is None:
                continue
            cands = m.candidates("head", hu.uid)
            names = [c.other.qualname for c in cands]
            hn, bn = hq.split(".")[-1], bq.split(".")[-1]
            if hn != bn:
                kinds["renamed_method"] += 1
            if hq.rsplit(".", 1)[0] != bq.rsplit(".", 1)[0]:
                kinds["moved_class"] += 1
            if names and names[0] == bq:
                ok1 += 1
            elif bq in names:
                okn += 1
                fails.append((num, fd.path, hq, bq, f"rank{names.index(bq)}", names[0], sim))
            else:
                bad += 1
                fails.append((num, fd.path, hq, bq, "MISS", names[0] if names else "-", sim))
        T += ok1 + okn + bad
        OK1 += ok1
        OKN += okn
        BAD += bad
        if okn or bad:
            rows.append((num, fd.path, ok1, okn, bad))

print("\n=== files with imperfect top-1 ===")
for num, path, a, b, c in rows:
    print(f"{num:>7} {path[-56:]:<56} top1={a:<5} rank>0={b:<4} miss={c}")

print(f"\n=== name-agnostic totals ===")
print(f"ground-truth pairs      : {T}")
print(f"strict top-1 correct    : {OK1}  ({100*OK1/max(T,1):.3f}%)")
print(f"in candidate list only  : {OKN}  ({100*OKN/max(T,1):.3f}%)")
print(f"not found at all        : {BAD}  ({100*BAD/max(T,1):.3f}%)")
print(f"hard cases in truth set : {dict(kinds)}")

print("\n=== all imperfect cases ===")
for f in fails[:60]:
    print(f"  {f[0]} {f[1][-30:]:<30} head={f[2][:50]:<50} want={f[3][:48]:<48} {f[4]:<6} got={f[5][:44]} sim={f[6]:.2f}")
