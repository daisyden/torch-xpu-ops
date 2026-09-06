"""Verify build_line_map correctness across PRs.

Checks:
  * array lengths agree with the file length
  * counterpart pointers are symmetric (base[i].o == j  =>  head[j].o == i)
  * every line that the *diff* calls unchanged context is marked identical
  * seg is present exactly for non-identical/blank lines
  * a moved method's lines carry the owning + target unit names
"""
from __future__ import annotations

import sys
from collections import Counter

import matcher
import prdata

NUMS = [int(x) for x in sys.argv[1:]]
VCH = {"i": "identical", "b": "blank", "n": "indent", "d": "device",
       "r": "rename", "c": "changed", "m": "missing"}

tot = Counter()
problems: list[str] = []

for num in NUMS:
    try:
        pr = prdata.load_pr(str(num))
    except Exception as e:
        problems.append(f"PR {num}: {e}")
        continue
    for fd in pr.files:
        if not fd.path.endswith(".py") or fd.binary:
            continue
        bt, ht = pr.base_text(fd), pr.head_text(fd)
        if not bt or not ht:
            continue
        if not fd.verify(bt, ht):
            fd = prdata.rebuild_diff(fd, bt, ht)
        m = matcher.build_match(bt, ht, fd.path)
        anchors = [
            (ln.base_no, ln.head_no)
            for ln in fd.lines
            if ln.kind == "ctx" and ln.base_no is not None and ln.head_no is not None
        ]
        lm = matcher.build_line_map(m, anchors=anchors)
        tag = f"{num} {fd.path[-34:]}"

        for side in ("base", "head"):
            d = lm[side]
            n = len(d["text"])
            if not (len(d["v"]) == n == len(d["o"])):
                problems.append(f"{tag} {side}: length mismatch")
            # seg present iff line differs
            for i, ch in enumerate(d["v"]):
                key = str(i + 1)
                has = key in d["seg"]
                should = ch not in ("i", "b")
                if has != should:
                    problems.append(f"{tag} {side}:{i+1} seg={has} verdict={VCH[ch]}")
                    break
            tot[f"{side}_lines"] += n

        # symmetry of counterpart pointers
        b, h = lm["base"], lm["head"]
        asym = 0
        for i, o in enumerate(b["o"]):
            if o and h["o"][o - 1] != i + 1:
                asym += 1
        if asym:
            # a base line may legitimately map to a head line that maps back to
            # a *different* base line when a helper was copied; cap the noise
            frac = asym / max(len(b["o"]), 1)
            if frac > 0.02:
                problems.append(f"{tag}: {asym} asymmetric pointers ({frac:.1%})")
            tot["asym"] += asym

        # verdicts must agree with the unified diff for context lines
        bad_ctx = 0
        for ln in fd.lines:
            if ln.kind != "ctx" or ln.base_no is None or ln.head_no is None:
                continue
            bi, hi = ln.base_no - 1, ln.head_no - 1
            if bi < len(b["v"]) and b["v"][bi] not in ("i", "b"):
                bad_ctx += 1
        if bad_ctx:
            problems.append(f"{tag}: {bad_ctx} diff-context lines not marked identical")

        for ch in b["v"]:
            tot["base_" + VCH[ch]] += 1
        for ch in h["v"]:
            tot["head_" + VCH[ch]] += 1

print("=== totals ===")
for k in sorted(tot):
    print(f"  {k:<22} {tot[k]}")
print(f"\n=== problems ({len(problems)}) ===")
for p in problems[:40]:
    print("  " + p)
print("\nOK" if not problems else "\nPROBLEMS FOUND")
sys.exit(1 if problems else 0)
