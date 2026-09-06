"""Validate the derived class mapping (base class -> head class(es)).

Ground truth is built by method-body voting, computed independently of
matcher.py.  Prints every non-trivial class relation so the naming patterns can
be eyeballed, plus accuracy numbers.
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

    def walk(body, prefix, cls):
        for n in body:
            if isinstance(n, ast.ClassDef):
                walk(n.body, prefix + n.name + ".", prefix + n.name)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                src = lines[n.lineno - 1 : (n.end_lineno or n.lineno)]
                body_n = [
                    re.sub(r"\s+", "", s)
                    for s in (x.strip() for x in src)
                    if s and not s.startswith("#")
                ]
                out[prefix + n.name] = (cls, body_n)

    walk(tree.body, "", None)
    return out


def r(a, b):
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()


NUMS = [int(x) for x in sys.argv[1:]]
tot = ok = bad = 0
lines_out = []
rel_counter = Counter()

for num in NUMS:
    try:
        pr = prdata.load_pr(str(num))
    except Exception as e:
        continue
    for fd in pr.files:
        if not fd.path.endswith(".py"):
            continue
        bt, ht = pr.base_text(fd), pr.head_text(fd)
        if not bt or not ht:
            continue
        b, h = methods(bt), methods(ht)
        if not b or not h:
            continue

        # ground truth: for each head method, best base method by body (rare-line index)
        df = Counter()
        idx = defaultdict(list)
        for q, (c, body) in b.items():
            for ln in set(body):
                df[ln] += 1
                idx[ln].append(q)
        gt_cls: dict[str, Counter] = defaultdict(Counter)
        for hq, (hcls, hbody) in h.items():
            if len(hbody) < 6 or hcls is None:
                continue
            votes = Counter()
            for ln in set(hbody):
                if df[ln] > 25:
                    continue
                for q in idx[ln]:
                    votes[q] += 1
            best, bestr = None, 0.0
            for q, _ in votes.most_common(20):
                rr = r(b[q][1], hbody)
                if rr > bestr:
                    best, bestr = q, rr
            if best and bestr >= 0.90 and b[best][0]:
                gt_cls[hcls][b[best][0]] += 1

        m = matcher.build_match(bt, ht, fd.path)
        for hcls, counter in gt_cls.items():
            want = counter.most_common(1)[0][0]
            got_list = m.class_map_rev.get(hcls, [])
            got = got_list[0]["cls"] if got_list else None
            tot += 1
            mark = "OK " if got == want else "BAD"
            if got == want:
                ok += 1
            else:
                bad += 1
            nsrc = len([x for x in counter if counter[x] >= max(1, 0.1 * sum(counter.values()))])
            rel_counter[f"{nsrc}->1"] += 1
            if hcls != want or nsrc > 1 or mark == "BAD":
                lines_out.append(
                    f"  {mark} {num} {fd.path[-30:]:<30} {want[:42]:<42} -> {hcls[:42]:<42} "
                    f"n={sum(counter.values()):<4} srcs={nsrc} got={str(got)[:38]}"
                )

print("\n=== renamed / split / merged class relations (ground truth vs derived) ===")
for l in lines_out[:160]:
    print(l)
print(f"\n=== class-mapping totals ===")
print(f"head classes with ground truth: {tot}")
print(f"primary source correct        : {ok} ({100*ok/max(tot,1):.2f}%)")
print(f"wrong                         : {bad}")
print(f"merge shapes                  : {dict(rel_counter)}")
