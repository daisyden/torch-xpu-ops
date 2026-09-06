"""Contract test against a RUNNING server over HTTP.

The in-process tests import the current code, so they cannot detect that the
*live* process is stale.  This one talks to the socket and asserts the response
shape the UI depends on, which is how a "REAL CHANGE == CHANGE" regression gets
caught.

    python3 dev/test_api_contract.py [--host 127.0.0.1] [--port 8765]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

BENIGN = {"identical", "blank", "indent", "device"}

CASES = [
    # pr, path, expectation
    ("189250", "test/test_dataloader.py", "pure move: few real changes"),
    ("195840", "test/quantization/core/test_quantized_op.py", "some real changes"),
]

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  -> {extra}" if not cond and extra else ""))
    if not cond:
        fails.append(name)


def get(base: str, route: str, **params) -> dict:
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    req = urllib.request.Request(f"{base}{route}?{qs}", headers={"Host": "localhost"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=300) as r:
        return json.loads(r.read())


def blocks_of(lines: list[dict]) -> tuple[int, int]:
    """Replicate the client's block grouping, so the numbers are comparable."""
    rows = [dict(l, i=i) for i, l in enumerate(lines) if l["kind"] in ("del", "add")]
    blocks: list[dict] = []
    cur = None
    prev = -2
    unit = None
    for r in rows:
        u = r.get("unit") or ""
        if cur is None or r["i"] != prev + 1 or u != unit:
            cur = {"rows": [], "real": False}
            blocks.append(cur)
            unit = u
        cur["rows"].append(r)
        if (r.get("verdict") or "changed") not in BENIGN:
            cur["real"] = True
        prev = r["i"]
    return len(blocks), sum(1 for b in blocks if b["real"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    base = f"http://{a.host}:{a.port}"

    try:
        get(base, "/api/pr", ref="189250")
    except Exception as exc:  # noqa: BLE001
        print(f"cannot reach {base}: {exc}")
        return 2

    for pr, path, note in CASES:
        print(f"\n--- PR {pr} {path}   ({note})")
        f = get(base, "/api/file", ref=pr, path=path)
        lines = f["file"]["lines"]
        clickable = [l for l in lines if l["kind"] in ("del", "add")]
        check(f"{pr}: diff has -/+ lines", len(clickable) > 0, str(len(clickable)))

        # the field the whole real-change feature rests on
        with_v = [l for l in clickable if "verdict" in l]
        check(f"{pr}: every -/+ line carries a verdict",
              len(with_v) == len(clickable), f"{len(with_v)}/{len(clickable)}")

        verdicts = {l.get("verdict") for l in clickable}
        check(f"{pr}: verdicts are from the known set",
              verdicts <= set(BENIGN) | {"changed", "rename", "missing"}, str(verdicts))

        # a refactor PR must contain *some* benign lines, else the matcher is
        # not doing its job and every move looks like a change
        benign = [l for l in clickable if (l.get("verdict") or "changed") in BENIGN]
        check(f"{pr}: some lines are recognised as benign",
              len(benign) > 0, f"{len(benign)}/{len(clickable)}")

        nblocks, nreal = blocks_of(lines)
        print(f"      CHANGE={nblocks}  REAL CHANGE={nreal}")
        check(f"{pr}: REAL CHANGE is strictly fewer than CHANGE",
              nreal < nblocks, f"{nreal} vs {nblocks}")

        # cross-check against the Refactor map: a file whose methods all match
        # cleanly must not report a large number of real changes
        att = [u for u in f["summary"]["units"]
               if u["verdict"] in ("changed", "missing") or not u["mutual"]]
        print(f"      Refactor map: {len(att)} method(s) need attention")
        if not att:
            # only class headers / imports / renames may remain
            check(f"{pr}: a clean file yields few real changes",
                  nreal <= max(10, nblocks // 5), f"{nreal} real of {nblocks}")

        # linemap must agree in size with the files
        lm = get(base, "/api/linemap", ref=pr, path=path)
        for side in ("base", "head"):
            d = lm[side]
            check(f"{pr}: linemap {side} arrays are consistent",
                  len(d["v"]) == len(d["text"]) == len(d["o"]),
                  f"{len(d['v'])}/{len(d['text'])}/{len(d['o'])}")

    print("\n" + ("ALL API CONTRACT TESTS PASS" if not fails else f"{len(fails)} FAILURE(S)"))
    return 1 if fails else 0


if __name__ == "__main__":
    import urllib.parse  # noqa: E402
    sys.exit(main())
