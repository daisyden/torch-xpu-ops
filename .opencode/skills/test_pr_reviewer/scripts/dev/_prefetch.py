"""Prefetch a list of PRs into the local cache (parallel)."""
from __future__ import annotations

import concurrent.futures as cf
import sys

import prdata

NUMS = [int(x) for x in sys.argv[1:]]


def one(n: int):
    try:
        pr = prdata.load_pr(str(n))
        py = [f for f in pr.files if f.path.endswith(".py")]
        for fd in py:
            pr.base_text(fd)
            pr.head_text(fd)
        return n, len(pr.files), len(py), pr.title[:70], None
    except Exception as exc:  # noqa: BLE001
        return n, 0, 0, "", repr(exc)[:200]


with cf.ThreadPoolExecutor(max_workers=8) as ex:
    for n, nf, npy, title, err in ex.map(one, NUMS):
        print(f"{n}\t{nf}\t{npy}\t{err or title}")
