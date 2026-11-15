#!/usr/bin/env python3
"""Dump OpInfo op_db skip/xfail DecorateInfos relevant to XPU to JSON.

Many OpInfo tests (test_ops.py, test_export_opinfo.py, ...) are skipped not by a
method decorator but by a DecorateInfo attached to the op in op_db (op.skips /
op.decorators) with device_type in {None, "xpu"} and a skip/expectedFailure
decorator. This script introspects op_db at runtime and records those so
skipped_report.py can attribute an OpInfo test to its op-db skip.

Run inside the torch (XPU) env:
    source ~/miniforge3/etc/profile.d/conda.sh && conda activate any_gate
    python dump_opdb_skips.py --out /tmp/opencode/opdb_xpu_skips.json

Output: list of
  {op: <name>, op_us: <name with . and spaces -> _>, test_name, cls_name,
   device_type, kind, reason, active_if}
"""
import argparse
import json
import re


def deco_kind_reason(dec):
    """Classify a DecorateInfo.decorator as skip/xfail and extract any reason."""
    name = getattr(dec, "__name__", "") or dec.__class__.__name__
    # unittest.skip(reason) -> functools.partial-like; try to read closure
    reason = ""
    for attr in ("reason", "args"):
        v = getattr(dec, attr, None)
        if isinstance(v, str):
            reason = v
            break
        if isinstance(v, (tuple, list)) and v and isinstance(v[0], str):
            reason = v[0]
            break
    src = repr(dec)
    if "expectedFailure" in src or "xfail" in src.lower():
        return "xfail", reason
    if "skip" in src.lower() or name == "skip":
        return "skip", reason
    # unittest.skip returns a function with closure holding the reason
    closure = getattr(dec, "__closure__", None)
    if closure:
        for cell in closure:
            try:
                val = cell.cell_contents
            except ValueError:
                continue
            if isinstance(val, str) and not reason:
                reason = val
        return "skip", reason
    return "", reason


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/opencode/opdb_xpu_skips.json")
    a = ap.parse_args()
    from torch.testing._internal.common_methods_invocations import op_db
    try:
        from torch.testing._internal.opinfo.core import DecorateInfo
    except Exception:  # noqa: BLE001
        DecorateInfo = None  # noqa: N806

    out = []
    for op in op_db:
        name = op.name
        op_us = re.sub(r"[.\s]", "_", name)
        if op.variant_test_name:
            op_us += "_" + op.variant_test_name
        infos = list(getattr(op, "skips", ()) or ()) + \
            list(getattr(op, "decorators", ()) or ())
        for di in infos:
            if DecorateInfo is not None and not isinstance(di, DecorateInfo):
                continue
            dev = getattr(di, "device_type", None)
            if dev not in (None, "xpu"):
                continue
            for dec in getattr(di, "decorators", []):
                kind, reason = deco_kind_reason(dec)
                if not kind:
                    continue
                out.append({
                    "op": name,
                    "op_us": op_us,
                    "test_name": getattr(di, "test_name", None),
                    "cls_name": getattr(di, "cls_name", None),
                    "device_type": dev,
                    "kind": kind,
                    "reason": reason,
                    "active_if": repr(getattr(di, "active_if", None)),
                })
    with open(a.out, "w") as fh:
        json.dump(out, fh)
    print(f"wrote {a.out}: {len(out)} op-db skip/xfail entries")


if __name__ == "__main__":
    main()
