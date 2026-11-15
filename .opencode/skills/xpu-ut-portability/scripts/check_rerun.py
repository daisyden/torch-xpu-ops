import csv, subprocess, sys, os, re, json
from collections import defaultdict
CSV="/home/daisyden/opencode/ut_result_refresh/ut_regression_pvc_cuda_notrun.csv"
TEST_ROOT="/home/daisyden/opencode/ut_result_refresh"
rows=list(csv.reader(open(CSV)))
H=rows[0]
cf=H.index('CUDA Test File'); cc=H.index('CUDA Test Class'); cn=H.index('CUDA Test Name'); ai=H.index('Action')
rerun=[r for r in rows[1:] if r[ai]=='rerun']
def xclass(c): return c[:-4]+"XPU" if c.endswith("CUDA") else c
def xname(n): return re.sub(r'_cuda(?=_|$)','_xpu',n)
byfile=defaultdict(list)
for r in rerun: byfile[r[cf]].append(r)
RUNNER="/tmp/xpucollect/run_collect.py"
node_re=re.compile(r'::([^:]+)::([^:]+)\s*$')
results={}
files=sorted(byfile)
sel=sys.argv[1:]
if sel: files=[f for f in files if f in sel]
for f in files:
    path=os.path.join(TEST_ROOT,"test",f)
    try:
        out=subprocess.run([sys.executable,RUNNER,path],cwd="/tmp/xpucollect",
            capture_output=True,text=True,timeout=1200).stdout
    except subprocess.TimeoutExpired as e:
        out=(e.stdout.decode() if isinstance(e.stdout,bytes) else e.stdout) or ""
    nodes=set(); names=set()
    for line in out.splitlines():
        m=node_re.search(line.strip())
        if m: nodes.add((m.group(1),m.group(2))); names.add(m.group(2))
    failed=len(nodes)==0
    has_xpu=any('_xpu' in n for n in names) or any(c.endswith('XPU') for c,_ in nodes)
    miss=0
    for r in byfile[f]:
        xn=xname(r[cn])
        ok=((xclass(r[cc]),xn) in nodes) or (xn in names)
        # inconclusive if collection empty, OR file produced no xpu artifacts at all
        results[(f,r[cc],r[cn])]= None if (failed or not has_xpu) else ok
        if (not failed and has_xpu) and not ok: miss+=1
    tag="INCONCLUSIVE" if (failed or not has_xpu) else f"NOT_SELECTABLE={miss}"
    print(f"{f}\tnodes={len(nodes)}\tfail={failed}\thas_xpu={has_xpu}\trerun={len(byfile[f])}\t{tag}",flush=True)
json.dump({f"{k[0]}|{k[1]}|{k[2]}":v for k,v in results.items()},
          open("/tmp/xpucollect/rerun_selectable.json","w"))
