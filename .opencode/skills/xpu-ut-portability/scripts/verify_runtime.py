"""Universal re-verification of all Portable rows.

For each Portable row decide rerun vs TBD:
 - If the module imports and a generated XPU class exists for the CSV class
   (device-generic parametrized test), the row 'runs as-is' (=> rerun) iff the
   translated _xpu method exists on that class; else it is a device/dtype gap
   (=> TBD).
 - Otherwise (plain class, non-device test, or import failure) fall back to the
   static any_gate decorator/guard audit verdict.
"""
import sys, os, json, importlib, csv
from collections import Counter
SRC = os.environ.get("XPU_SRC_TREE", "/home/daisyden/upstream/ut_result_analysis/test")
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(SRC, "functorch"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir('/tmp')
import audit  # static verdict + file analyzer

CSV = os.environ.get("XPU_UT_CSV", "/home/daisyden/opencode/ut_result_refresh/ut_regression_pvc_cuda_notrun.csv")

mods = {}
def getmod(f):
    mn = f[:-3].replace('/', '.')
    if mn not in mods:
        try:
            mods[mn] = importlib.import_module(mn)
        except BaseException as e:
            mods[mn] = ('ERR', type(e).__name__)
    return mods[mn]

def xpu_class_name(cls):
    base = cls[:-4] if cls.endswith('CUDA') else cls
    return base + 'XPU'

NO_EVIDENCE = {'noclass', 'file-missing'}

def decide(f, cls, name, cur):
    m = getmod(f)
    sa, sr = audit.verdict(f, cls, name)  # static: catches skipXPU/onlyCUDA/guards/only_for
    if isinstance(m, tuple):
        # import failed: trust static only if confident, else keep current
        if sa is None or sr in NO_EVIDENCE:
            return (cur, 'keep:import-err-no-evidence', 'keep')
        return (sa, 'static:' + sr, 'import-err')
    klass = getattr(m, xpu_class_name(cls), None)
    if klass is None:
        # plain/non-device class: trust static only if confident
        if sa is None or sr in NO_EVIDENCE:
            return (cur, 'keep:no-evidence', 'keep')
        return (sa, 'static:' + sr, 'plain')
    # device-generic: generated XPU class exists
    xn = name.replace('_cuda', '_xpu')
    if not hasattr(klass, xn):
        return ('TBD', 'no-xpu-variant', 'runtime')      # dtype/device gap
    if sa == 'TBD':
        return ('TBD', 'static-block:' + sr, 'runtime+static')  # e.g. skipXPU present
    return ('rerun', 'xpu-variant-runs', 'runtime')


rows = list(csv.reader(open(CSV)))
diff = Counter(); path = Counter(); changes = []
for i, r in enumerate(rows):
    if i == 0 or r[16] != 'Portable':
        continue
    act, reason, how = decide(r[6], r[7], r[8], r[18])
    path[how] += 1
    if act != r[18]:
        diff[(r[18], act, how)] += 1
        changes.append((i, r[6], r[7], r[8], r[18], act, reason, how))

print('decision path:', dict(path))
print('total changes:', len(changes))
agg = Counter()
for (cur, new, how), v in diff.items():
    agg[(cur, new)] += v
print('net:', dict(agg))
byfile = Counter((c[1], c[4], c[5]) for c in changes)
print('\ntop changed files:')
for k, v in byfile.most_common(20):
    print(f'  {v:5d} {k[1]}->{k[2]} {k[0]}')
OUT = os.environ.get('XPU_CHANGES_JSON', '/tmp/universal_changes.json')
json.dump(changes, open(OUT, 'w'))
print('wrote', OUT)
