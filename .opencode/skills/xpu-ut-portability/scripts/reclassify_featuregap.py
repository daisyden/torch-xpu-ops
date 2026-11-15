"""Reclassify no-xpu-variant rows (XPU class generated but this specific
dtype/parametrization method is absent) as Feature gap / blank Action.
Only applies to rows where the generated XPU class EXISTS but the translated
_xpu method does NOT (true backend/dtype gap). A whole CUDA-only class
(no XPU class at all) is left as-is (that is a TBD instantiation edit)."""
import sys, os, json, importlib, csv, shutil
from collections import Counter
SRC = os.environ.get('XPU_SRC_TREE', '/home/daisyden/upstream/ut_result_analysis/test')
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(SRC, 'functorch'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir('/tmp')

CSV = os.environ.get('XPU_UT_CSV', '/home/daisyden/opencode/ut_result_refresh/ut_regression_pvc_cuda_notrun.csv')
mods = {}
def getmod(f):
    mn = f[:-3].replace('/', '.')
    base = f.split('/')[-1][:-3]
    for cand in (mn, base):
        if cand in mods:
            return mods[cand]
    try:
        m = importlib.import_module(base)
    except BaseException:
        try:
            m = importlib.import_module(mn)
        except BaseException as e:
            m = ('ERR', type(e).__name__)
    mods[mn] = m
    return m

def xpu_class_name(cls):
    return (cls[:-4] if cls.endswith('CUDA') else cls) + 'XPU'

rows = list(csv.reader(open(CSV)))
hits = []
for i, r in enumerate(rows):
    if i == 0 or r[16] != 'Portable':
        continue
    m = getmod(r[6])
    if isinstance(m, tuple):
        continue
    klass = getattr(m, xpu_class_name(r[7]), None)
    if klass is None:
        continue
    xn = r[8].replace('_cuda', '_xpu')
    if not hasattr(klass, xn):
        hits.append(i)

print('no-xpu-variant rows found:', len(hits))
print('by file:', Counter(rows[i][6] for i in hits).most_common(10))
print('current Action of these:', Counter(rows[i][18] for i in hits))
shutil.copyfile(CSV, CSV + '.bak14')
for i in hits:
    rows[i][16] = 'Feature gap'
    rows[i][18] = ''
    rows[i][17] = 'XPU class is generated but this dtype/parametrization variant is not produced for XPU (backend does not cover this dtype/case); not fixable by a test edit.'
with open(CSV, 'w', newline='') as f:
    csv.writer(f).writerows(rows)
print('reclassified to Feature gap:', len(hits), 'backup .bak14')
print('new Reason dist:', Counter(r[16] for r in rows[1:]))
print('new Action dist:', Counter(r[18] for r in rows[1:]))
