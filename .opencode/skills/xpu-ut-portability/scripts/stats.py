#!/usr/bin/env python3
"""Distribution / breakdown stats for the portability CSV.

    python stats.py --csv ut_regression_pvc_cuda_notrun.csv
    python stats.py --csv ... --file test_varlen_attention.py
    python stats.py --csv ... --class TestTorchDeviceType
    python stats.py --csv ... --name test_batch_invariance
"""
import argparse
import csv
from collections import Counter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--file")
    ap.add_argument("--class", dest="klass")
    ap.add_argument("--name")
    a = ap.parse_args()
    with open(a.csv, newline="") as fh:
        rows = list(csv.reader(fh))
    H = rows[0]
    ri, ai = H.index("Reason"), H.index("Action")
    fi, ci, ni = (H.index("CUDA Test File"), H.index("CUDA Test Class"),
                  H.index("CUDA Test Name"))
    sel = rows[1:]
    if a.file:
        sel = [r for r in sel if r[fi] == a.file]
    if a.klass:
        sel = [r for r in sel if a.klass in r[ci]]
    if a.name:
        sel = [r for r in sel if a.name in r[ni]]
    print("rows:", len(sel))
    print("Reason:", dict(Counter(r[ri] for r in sel)))
    print("Action:", dict(Counter(r[ai] for r in sel)))
    if not (a.file or a.klass or a.name):
        print("top files:", Counter(r[fi] for r in sel).most_common(10))


if __name__ == "__main__":
    main()
