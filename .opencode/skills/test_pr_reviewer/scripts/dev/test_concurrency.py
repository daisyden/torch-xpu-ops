"""Probe how the server behaves with several users reviewing different PRs.

Measures:
  * do concurrent requests for *different* PRs return correct data?
  * does one user's slow request block another's? (global lock serialisation)
  * does one user's file evict another's cached analysis? (cache thrashing)
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.parse
import urllib.request

CASES = [
    ("189250", "test/test_dataloader.py"),
    ("195840", "test/quantization/core/test_quantized_op.py"),
    ("195155", "test/test_gpu_trace.py"),
    ("195730", "test/distributions/test_distributions.py"),
    ("195722", "test/test_dlpack.py"),
    ("195002", "test/export/test_experimental.py"),
]


def get(base, route, **params):
    qs = urllib.parse.urlencode(params)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(f"{base}{route}?{qs}", headers={"Host": "localhost"})
    t0 = time.time()
    with opener.open(req, timeout=600) as r:
        data = json.loads(r.read())
    return data, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()
    base = f"http://{a.host}:{a.port}"

    fails = []

    def check(name, cond, extra=""):
        print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  -> {extra}" if not cond else ""))
        if not cond:
            fails.append(name)

    # warm one PR so we can measure cache-hit latency later
    print("warming PR 189250 ...")
    _, warm = get(base, "/api/file", ref=CASES[0][0], path=CASES[0][1])
    print(f"  cold /api/file: {warm:.2f}s")
    _, hot = get(base, "/api/file", ref=CASES[0][0], path=CASES[0][1])
    print(f"  warm /api/file: {hot:.2f}s")
    check("a repeated request is served from cache", hot < max(0.5, warm / 2),
          f"cold {warm:.2f}s vs warm {hot:.2f}s")

    # --- concurrent, different PRs ---------------------------------------- #
    print(f"\n{len(CASES)} 'users' requesting different PRs concurrently ...")
    results = {}
    errors = {}
    timings = {}

    def worker(pr, path):
        try:
            d, dt = get(base, "/api/file", ref=pr, path=path)
            results[pr] = d
            timings[pr] = dt
        except Exception as exc:  # noqa: BLE001
            errors[pr] = repr(exc)[:200]

    threads = [threading.Thread(target=worker, args=c) for c in CASES]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.time() - t0

    check("no concurrent request failed", not errors, json.dumps(errors))
    check("every concurrent request returned data", len(results) == len(CASES),
          f"{len(results)}/{len(CASES)}")

    # each response must describe the PR that was asked for, not another user's
    for pr, path in CASES:
        d = results.get(pr)
        if not d:
            continue
        check(f"{pr}: response is for the requested file",
              d["file"]["path"] == path, f'{d["file"]["path"]} != {path}')
        clickable = [l for l in d["file"]["lines"] if l["kind"] in ("del", "add")]
        check(f"{pr}: response carries verdicts", all("verdict" in l for l in clickable),
              f'{sum(1 for l in clickable if "verdict" in l)}/{len(clickable)}')

    print(f"  wall {wall:.1f}s; per-request {sorted(round(v,1) for v in timings.values())}")

    # --- does one user block another? ------------------------------------- #
    # fire a heavy request, then immediately a request that is already cached.
    print("\nmeasuring head-of-line blocking ...")
    heavy_done = threading.Event()

    def heavy():
        try:
            get(base, "/api/file", ref="188963",
                path="torch/testing/_internal/common_methods_invocations.py")
        except Exception:
            pass
        heavy_done.set()

    ht = threading.Thread(target=heavy)
    ht.start()
    time.sleep(1.0)  # let it get into the expensive part
    _, blocked = get(base, "/api/pr", ref=CASES[0][0])
    ht.join()
    print(f"  cached /api/pr while a heavy analysis ran: {blocked:.2f}s")
    check("a cached request is not blocked by another user's heavy request",
          blocked < 5.0, f"{blocked:.2f}s")

    # --- cache thrashing -------------------------------------------------- #
    print("\nchecking whether users evict each other's cached analysis ...")
    lat = []
    for _ in range(a.rounds):
        for pr, path in CASES:
            _, dt = get(base, "/api/file", ref=pr, path=path)
            lat.append((pr, dt))
    second_round = [dt for _, dt in lat[len(CASES):]]
    if second_round:
        worst = max(second_round)
        print(f"  re-request latencies: {[round(x,2) for x in second_round]}")
        check("previously analysed files stay cached across users",
              worst < 2.0, f"worst re-request {worst:.2f}s")

    print("\n" + ("ALL CONCURRENCY CHECKS PASS" if not fails else f"{len(fails)} FAILURE(S)"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
