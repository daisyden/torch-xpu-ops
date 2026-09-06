"""Measure class-header pairing coverage and added-line resolvability.

For every PR/file: how many head class headers get a counterpart, and for every
added (+) line, does clicking it produce a useful pane 2/3 view?
"""
from __future__ import annotations

import sys
from collections import Counter

import matcher
import prdata

NUMS = [int(x) for x in sys.argv[1:]]

hdr = Counter()
addk = Counter()
unmatched_hdrs = []

for num in NUMS:
    try:
        pr = prdata.load_pr(str(num))
    except Exception:
        continue
    for fd in pr.files:
        if not fd.path.endswith(".py") or fd.binary:
            continue
        bt, ht = pr.base_text(fd), pr.head_text(fd)
        if not bt or not ht:
            continue
        m = matcher.build_match(bt, ht, fd.path)

        for h in m.head.class_headers:
            c = m.primary("head", h.uid)
            if c:
                hdr["matched"] += 1
                if h.qualname == c.other.qualname:
                    hdr["  same name"] += 1
                elif matcher.is_device_rename(c.other.name, h.name):
                    hdr["  device rename"] += 1
                else:
                    hdr["  other rename/split"] += 1
            else:
                hdr["UNMATCHED"] += 1
                unmatched_hdrs.append((num, fd.path, h.qualname, len(m.head.methods_of(h.qualname))))

        # added lines
        for ln in fd.lines:
            if ln.kind != "add" or ln.head_no is None:
                continue
            u = m.head.owner(ln.head_no)
            if u is None:
                addk["free line (import/module level)"] += 1
                continue
            c = m.primary("head", u.uid)
            if not c:
                addk["genuinely new code"] += 1
            elif u.cls != c.other.cls or u.name != c.other.name:
                addk["moved/renamed from base"] += 1
            else:
                addk["in-place edit"] += 1

print("=== head class headers ===")
for k, v in hdr.most_common():
    print(f"  {k:<24} {v}")
tot = hdr["matched"] + hdr["UNMATCHED"]
print(f"  coverage: {100*hdr['matched']/max(tot,1):.2f}%")

print("\n=== added (+) lines: what clicking them yields ===")
t2 = sum(addk.values())
for k, v in addk.most_common():
    print(f"  {k:<34} {v:>6}  ({100*v/max(t2,1):.1f}%)")
print(f"  total added lines: {t2}")

print(f"\n=== unmatched head class headers ({len(unmatched_hdrs)}) ===")
for n, p, q, nm in unmatched_hdrs[:40]:
    print(f"  {n} {p[-40:]:<40} {q[:50]:<50} methods={nm}")
