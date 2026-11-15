"""Apply runtime-verified Action changes to the portability CSV.

Only confident changes are applied:
  * TBD->rerun: runtime-confirmed (translated _xpu method exists on generated
    XPU class).  Static-only 'plain-no-block' flips are NOT applied.
  * rerun->TBD: runtime 'no-xpu-variant' (dtype/device variant absent) and
    static CUDA-only guards (requires_cuda_and_triton / class-cuda-guard).
"""
import json, csv, shutil

import os
CSV = os.environ.get('XPU_UT_CSV', '/home/daisyden/opencode/ut_result_refresh/ut_regression_pvc_cuda_notrun.csv')
BAK = CSV + '.bak12'
ch = json.load(open(os.environ.get('XPU_CHANGES_JSON', '/tmp/universal_changes.json')))

def keep(c):
    cur, new, how, reason = c[4], c[5], c[7], c[6]
    if cur == 'TBD' and new == 'rerun':
        return how == 'runtime' and reason == 'xpu-variant-runs'
    if cur == 'rerun' and new == 'TBD':
        return how == 'runtime' or reason.startswith('static:')
    return False

apply = {c[0]: c for c in ch if keep(c)}   # key = row index

REASON_TEXT = {
    ('TBD', 'rerun'): 'XPU variant generated for this parametrization; device-generic test runs as-is on XPU (class allow_xpu=True, no XPU-level block).',
}

rows = list(csv.reader(open(CSV)))
n_up = n_down = 0
for i, c in apply.items():
    r = rows[i]
    new = c[5]
    r[18] = new
    if (c[4], new) == ('TBD', 'rerun'):
        r[17] = REASON_TEXT[('TBD', 'rerun')]
        n_up += 1
    else:  # rerun -> TBD
        if c[7] == 'runtime':
            r[17] = 'No XPU variant generated for this dtype/parametrization (not in XPU @dtypes / module-test set); needs XPU dtype enablement before it can run.'
        else:
            r[17] = 'CUDA-only guard (' + c[6].replace('static:', '') + '); no XPU code path, needs port.'
        n_down += 1

shutil.copyfile(CSV, BAK)
with open(CSV, 'w', newline='') as f:
    csv.writer(f).writerows(rows)
print('backup:', BAK)
print('applied TBD->rerun:', n_up, ' rerun->TBD:', n_down, ' total:', len(apply))

from collections import Counter
print('new Action dist:', Counter(r[18] for r in rows[1:]))
print('new Reason dist:', Counter(r[16] for r in rows[1:]))
