#!/usr/bin/env python3
"""Rule library for XPU UT portability classification.

Every rule takes the in-memory rows (list of lists, row[0] is the header) and a
column-index dict, mutates matching rows in place, and returns the count changed.
Grounded in the actual test source: callers should confirm a gate exists in the
file before applying a rule to that file.

Usage:
    python reclassify.py --csv ut_regression_pvc_cuda_notrun.csv --rule nccl
    python reclassify.py --csv ... --rule all          # apply every safe rule
    python reclassify.py --csv ... --save-xlsx         # also (re)write .xlsx
"""
import argparse
import csv
import os
from collections import Counter

PORTABLE = "Portable"
FEATURE_GAP = "Feature gap"
NOT_APPLICABLE = "Not Applicable"


def load(path):
    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    H = rows[0]
    idx = {name: H.index(name) for name in H}
    return rows, idx


def save(path, rows, save_xlsx=True):
    with open(path, "w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    if save_xlsx:
        try:
            from openpyxl import Workbook
        except ImportError:
            return
        wb = Workbook()
        ws = wb.active
        ws.title = "Not Run"
        for r in rows:
            ws.append(r)
        ws.freeze_panes = "A2"
        wb.save(os.path.splitext(path)[0] + ".xlsx")


def _set(row, idx, reason, detail, action=""):
    row[idx["Reason"]] = reason
    row[idx["Detailed reason"]] = detail
    row[idx["Action"]] = action


# ---- rules -----------------------------------------------------------------

def rule_nccl(rows, idx):
    ci, fi = idx["CUDA Test Class"], idx["CUDA Test File"]
    n = 0
    for r in rows[1:]:
        if "NCCL" in r[ci] or "nccl" in r[fi].lower():
            if r[idx["Reason"]] != NOT_APPLICABLE:
                n += 1
            _set(r, idx, NOT_APPLICABLE,
                 "NCCL collective backend is NVIDIA-only; no XPU equivalent "
                 "(XPU uses XCCL via a different backend). Not applicable for XPU.")
    return n


def rule_cudnn_fa2(rows, idx):
    ni = idx["CUDA Test Name"]
    n = 0
    for r in rows[1:]:
        if "backend_fa2" in r[ni] and r[idx["Reason"]] != NOT_APPLICABLE:
            _set(r, idx, NOT_APPLICABLE,
                 "FA2/cuDNN attention backend variant; NVIDIA cuDNN-specific with "
                 "no XPU equivalent. Not applicable for XPU enablement.")
            n += 1
    return n


def rule_ck_aotriton(rows, idx):
    ni = idx["CUDA Test Name"]
    n = 0
    for r in rows[1:]:
        if ("sdpa_backend_aotriton" in r[ni] or "sdpa_backend_ck" in r[ni]) \
                and r[idx["Reason"]] != NOT_APPLICABLE:
            _set(r, idx, NOT_APPLICABLE,
                 "sdpa_backend=aotriton/ck selects a ROCm-only flash-attention "
                 "library (preferred_rocm_fa_library), gated by "
                 "PLATFORM_SUPPORTS_CK_SDPA which is False on XPU. No XPU "
                 "equivalent; not applicable.")
            n += 1
    return n


def rule_portable_action(rows, idx):
    """Action: runs-as-is Portable -> rerun; needs-edit Portable -> TBD;
    non-Portable -> blank.

    "Runs as-is" is detected from the Detailed reason: a plain TestCase or an
    already-XPU-instantiated class with no method-level block. Everything else
    that is Portable requires a test edit -> TBD.
    """
    di = idx["Detailed reason"]
    runs_as_is = ("runs as-is", "no method-level block")
    n = 0
    for r in rows[1:]:
        if r[idx["Reason"]] != PORTABLE:
            want = ""
        elif any(k in r[di] for k in runs_as_is):
            want = "rerun"
        else:
            want = "TBD"
        if r[idx["Action"]] != want:
            n += 1
        r[idx["Action"]] = want
    return n


RULES = {
    "nccl": rule_nccl,
    "cudnn_fa2": rule_cudnn_fa2,
    "ck_aotriton": rule_ck_aotriton,
    "action": rule_portable_action,
}
# action must run last so it reflects final Reasons.
ALL_ORDER = ["nccl", "cudnn_fa2", "ck_aotriton", "action"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--rule", required=True, choices=list(RULES) + ["all"])
    ap.add_argument("--save-xlsx", action="store_true")
    ap.add_argument("--backup", action="store_true")
    a = ap.parse_args()
    rows, idx = load(a.csv)
    if a.backup:
        save(a.csv + ".bak", rows, save_xlsx=False)
    names = ALL_ORDER if a.rule == "all" else [a.rule]
    for name in names:
        print(f"{name}: {RULES[name](rows, idx)} rows changed")
    save(a.csv, rows, save_xlsx=a.save_xlsx)
    print("Reason:", dict(Counter(r[idx["Reason"]] for r in rows[1:])))


if __name__ == "__main__":
    main()
