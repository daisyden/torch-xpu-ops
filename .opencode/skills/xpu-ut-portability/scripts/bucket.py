import csv, subprocess, sys, os, re, json
CSV="/home/daisyden/opencode/ut_result_refresh/ut_regression_pvc_cuda_notrun.csv"
ROOT="/home/daisyden/opencode/ut_result_refresh"
rows=list(csv.reader(open(CSV)))
H=rows[0]
cf=H.index('CUDA Test File');cc=H.index('CUDA Test Class');cn=H.index('CUDA Test Name');ai=H.index('Action')
def xclass(c): return c[:-4]+"XPU" if c.endswith("CUDA") else c
def xname(n): return re.sub(r'_cuda(?=_|$)','_xpu',n)
d=json.load(open("/tmp/xpucollect/rerun_selectable.json"))
# collect the affected files
files=sorted({k.split('|',1)[0] for k,v in d.items() if v is False and k.split('|')[1].endswith('CUDA')})
node_re=re.compile(r'::([^:]+)::([^:]+)\s*$')
RUNNER="/tmp/xpucollect/run_collect.py"
buckets={}  # key -> 'TBD'(cuda-only class) or 'Feature gap'(dtype/backend)
for f in files:
    path=os.path.join(ROOT,"test",f)
    out=subprocess.run([sys.executable,RUNNER,path],cwd="/tmp/xpucollect",
        capture_output=True,text=True,timeout=900).stdout
    collected=set(); xpu_classes=set()
    for line in out.splitlines():
        m=node_re.search(line.strip())
        if m:
            collected.add((m.group(1),m.group(2)))
            if m.group(1).endswith("XPU"): xpu_classes.add(m.group(1))
    for k,v in d.items():
        kf,kc,kn=k.split('|',2)
        if kf!=f or v is not False or not kc.endswith('CUDA'): continue
        xc=xclass(kc)
        # if the mapped XPU class exists at all -> dtype/backend gap; else cuda-only class
        buckets[k]= "Feature gap" if xc in xpu_classes else "TBD"
json.dump(buckets,open("/tmp/xpucollect/buckets.json","w"))
import collections
print(collections.Counter(buckets.values()))
print("files:",len(files),"cases:",len(buckets))
