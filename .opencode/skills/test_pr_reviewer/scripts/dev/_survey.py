"""Survey base->head class-level changes across cached PRs, to learn the real
naming/splitting patterns instead of guessing them."""
from __future__ import annotations

import ast
import difflib
import re
import sys
from collections import Counter

import prdata


def classes(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    lines = text.split("\n")

    def walk(body, prefix):
        for n in body:
            if isinstance(n, ast.ClassDef):
                q = prefix + n.name
                methods = {}
                for m in n.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        src = "\n".join(lines[m.lineno - 1 : (m.end_lineno or m.lineno)])
                        methods[m.name] = src
                out[q] = {
                    "name": n.name,
                    "bases": [ast.unparse(b) for b in n.bases],
                    "methods": methods,
                    "decorators": [ast.unparse(d) for d in n.decorator_list],
                }
                walk(n.body, q + ".")
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(n.body, prefix + n.name + ".")

    walk(tree.body, "")
    return out


def body_key(src: str) -> list[str]:
    out = []
    for ln in src.split("\n"):
        s = ln.strip()
        if s and not s.startswith("#"):
            out.append(re.sub(r"\s+", "", s))
    return out


PAIRS = []
NUMS = [int(x) for x in sys.argv[1:]]

for num in NUMS:
    try:
        pr = prdata.load_pr(str(num))
    except Exception as e:
        print(f"!! {num} {e}")
        continue
    for fd in pr.files:
        if not fd.path.endswith(".py"):
            continue
        b, h = pr.base_text(fd), pr.head_text(fd)
        if not b or not h:
            continue
        bc, hc = classes(b), classes(h)
        bnames = set(bc)
        hnames = set(hc)
        gone = bnames - hnames
        new = hnames - bnames
        if not gone and not new:
            continue
        # Determine ground truth by method-body content: for each new class,
        # where did its methods come from?
        base_method_owner: dict[str, list[str]] = {}
        for q, info in bc.items():
            for mname, msrc in info["methods"].items():
                base_method_owner.setdefault(mname, []).append(q)

        for nq in sorted(new):
            info = hc[nq]
            src_counter = Counter()
            for mname, msrc in info["methods"].items():
                owners = base_method_owner.get(mname, [])
                best, bestr = None, 0.0
                for o in owners:
                    r = difflib.SequenceMatcher(
                        a=body_key(bc[o]["methods"][mname]), b=body_key(msrc), autojunk=False
                    ).ratio()
                    if r > bestr:
                        best, bestr = o, r
                if best and bestr > 0.5:
                    src_counter[best] += 1
            PAIRS.append(
                {
                    "pr": num,
                    "file": fd.path,
                    "new_class": nq,
                    "bases": info["bases"],
                    "nmethods": len(info["methods"]),
                    "origins": src_counter.most_common(3),
                    "gone": sorted(gone)[:6],
                }
            )

print(f"\n=== {len(PAIRS)} new classes across PRs ===")
for p in PAIRS:
    orig = ", ".join(f"{k}({v})" for k, v in p["origins"]) or "-"
    print(f"{p['pr']:>7} {p['file'][-40:]:<40} NEW {p['new_class']:<45} bases={p['bases']} n={p['nmethods']:<3} <- {orig}")
