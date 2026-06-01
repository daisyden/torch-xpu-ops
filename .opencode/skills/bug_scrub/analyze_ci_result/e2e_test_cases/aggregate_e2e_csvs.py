#!/usr/bin/env python3
"""Aggregate Inductor E2E per-config CSVs into Inductor_E2E_Test_Report.xlsx files.

Walks ${ISSUE_TRIAGE_CI_RESULTS}/torch-xpu-ops/Inductor-*-E2E-Data-*-<benchmark>-*-1/
and produces <benchmark_folder>/Inductor_E2E_Test_Report.xlsx with one sheet per
(benchmark, dtype, phase) tuple, matching the schema run_match_e2e_status.py expects:
  - Sheet name: <benchmark>_<dtype>_<phase>_acc (amp dtypes preserve their prefix)
  - Row 1: header (skipped by matcher)
  - Row 2: blank
  - Row 3+: [_, model_name, _, accuracy_status, ...]
"""
import csv
import glob
import os
import re
import sys
import argparse
import openpyxl


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_COMMON_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '_common'))
if _COMMON_DIR not in sys.path:
    sys.path.insert(0, _COMMON_DIR)
from header_utils import write_by_name  # type: ignore[reportMissingImports] # noqa: E402


CSV_PATTERN = 'inductor-results-{bench}-{dtype}-{phase}-xpu-accuracy.csv'
SHEET_FMT = '{bench}_{dtype}_{phase}_acc'
PHASE_SHORT = {'inference': 'inf', 'training': 'tra'}
KNOWN_BENCHES = {'huggingface', 'timm_models', 'torchbench', 'pt2e'}


def extract_benchmark(folder_basename):
    for bench in KNOWN_BENCHES:
        # Match -<bench>-<digits>-<digits> suffix in artifact folder name.
        if re.search(rf'-{re.escape(bench)}-\d+-\d+$', folder_basename):
            return bench
    return None


def discover_csvs(benchmark_dir):
    found = []
    bench = extract_benchmark(os.path.basename(benchmark_dir))
    if not bench:
        return found
    bench_root = os.path.join(benchmark_dir, bench)
    if not os.path.isdir(bench_root):
        return found
    for dtype in sorted(os.listdir(bench_root)):
        dtype_dir = os.path.join(bench_root, dtype)
        if not os.path.isdir(dtype_dir):
            continue
        for phase in sorted(os.listdir(dtype_dir)):
            phase_dir = os.path.join(dtype_dir, phase, 'accuracy')
            if not os.path.isdir(phase_dir):
                continue
            csv_name = CSV_PATTERN.format(bench=bench, dtype=dtype, phase=phase)
            csv_path = os.path.join(phase_dir, csv_name)
            if os.path.isfile(csv_path):
                found.append((bench, dtype, phase, csv_path))
    return found


def build_workbook(benchmark_dir, dry_run=False):
    csvs = discover_csvs(benchmark_dir)
    if not csvs:
        print(f"  No CSVs found in {benchmark_dir}")
        return None
    wb = openpyxl.Workbook()
    active = wb.active
    if active is not None:
        wb.remove(active)
    total_rows = 0
    for bench, dtype, phase, csv_path in csvs:
        phase_short = PHASE_SHORT.get(phase, phase[:3])
        sheet_name = SHEET_FMT.format(bench=bench, dtype=dtype, phase=phase_short)
        sheet_name = sheet_name[:31]  # openpyxl limit
        ws = wb.create_sheet(sheet_name)
        write_by_name(ws, 1, 'scenario', 'scenario')
        write_by_name(ws, 1, 'name', 'name')
        write_by_name(ws, 1, 'dtype', 'dtype')
        write_by_name(ws, 1, 'accuracy', 'accuracy')
        row_idx = 3
        with open(csv_path, newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                model = row.get('name')
                acc = row.get('accuracy')
                if not model:
                    continue
                write_by_name(ws, row_idx, 'name', model)
                write_by_name(ws, row_idx, 'accuracy', acc if acc else 'unknown')
                row_idx += 1
        rows_written = row_idx - 3
        total_rows += rows_written
        print(f"    {sheet_name}: {rows_written} models from {os.path.basename(csv_path)}")
    out_path = os.path.join(benchmark_dir, 'Inductor_E2E_Test_Report.xlsx')
    if dry_run:
        print(f"  [dry-run] would write {out_path} ({total_rows} total rows, {len(csvs)} sheets)")
        return out_path
    wb.save(out_path)
    print(f"  Wrote {out_path} ({total_rows} total rows, {len(csvs)} sheets)")
    return out_path


def main():
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    _default_root = os.path.normpath(os.path.join(_this_dir, '..', '..'))
    _default_ci = os.environ.get('ISSUE_TRIAGE_CI_RESULTS') or os.path.join(_default_root, 'ci_results')
    _default_base = os.path.join(_default_ci, 'torch-xpu-ops')

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-dir', default=_default_base,
                   help='Directory containing Inductor-*-E2E-Data-* folders')
    p.add_argument('--pattern', default='Inductor-*-E2E-Data-*',
                   help='Glob to identify E2E benchmark folders')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    bench_dirs = sorted(glob.glob(os.path.join(args.base_dir, args.pattern)))
    bench_dirs = [d for d in bench_dirs if os.path.isdir(d)]
    print(f"Found {len(bench_dirs)} E2E benchmark folders under {args.base_dir}")
    written = 0
    for d in bench_dirs:
        print(f"  Processing: {os.path.basename(d)}")
        if build_workbook(d, dry_run=args.dry_run):
            written += 1
    print(f"\nDone. {written}/{len(bench_dirs)} workbooks written.")


if __name__ == '__main__':
    sys.exit(main())
