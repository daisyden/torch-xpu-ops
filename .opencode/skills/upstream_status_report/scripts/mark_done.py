import json, os, subprocess, shutil, datetime, openpyxl

HERE=os.path.dirname(__file__)
XLSX=os.path.join(HERE,'test_files_by_category_20260723.xlsx')
CACHE=os.path.join(HERE,'pr_cache')
os.makedirs(CACHE,exist_ok=True)

# column map (1-indexed) on 'Test Files' sheet  (must match extract_owned.py)
C_PATH=3; C_PR=6; C_TEAM=12; C_COMMPR=15; C_STATUS=17

owned=json.load(open('/tmp/owned.json'))
prs=owned['prs']

def fetch(n):
    """Query realtime PR state via gh, refresh cache, return json dict."""
    try:
        out=subprocess.run(
            ['gh','pr','view',str(n),'--repo','pytorch/pytorch','--json',
             'number,title,state,createdAt,mergedAt,closedAt,labels,author,'
             'reviews,statusCheckRollup,isDraft'],
            capture_output=True,text=True,check=True).stdout
        d=json.loads(out)
        json.dump(d,open(os.path.join(CACHE,f'{n}.json'),'w'))
        return d
    except subprocess.CalledProcessError as e:
        print(f'  ! PR {n}: gh failed ({e.stderr.strip().splitlines()[-1] if e.stderr else "?"})')
        return None

def is_merged(d):
    if not d: return False
    if str(d.get('state','')).upper()=='MERGED': return True
    labels={l['name'] for l in (d.get('labels') or [])}
    return 'Merged' in labels and bool(d.get('closedAt'))

print(f'checking realtime status of {len(prs)} PRs ...')
merged={}
for i,n in enumerate(prs,1):
    d=fetch(n)
    merged[str(n)]=is_merged(d)
    st=d.get('state') if d else 'ERR'
    print(f'[{i}/{len(prs)}] PR {n}: {st}{" (merged)" if merged[str(n)] else ""}')

nmerged=sum(merged.values())
print(f'\nmerged PRs: {nmerged}/{len(prs)}')

# ---- decide which files to mark Done: has PR(s) AND all associated merged ----
wb=openpyxl.load_workbook(XLSX)          # writable (formulas preserved)
wbv=openpyxl.load_workbook(XLSX,data_only=True)  # cached values for reading
ws=wb['Test Files']; wsv=wbv['Test Files']

import re
PULL_RE=re.compile(r'pytorch/pytorch/pull/(\d+)')
HASH_RE=re.compile(r'#(\d+)')
def parse_prs(v):
    if v is None: return []
    s=str(v); nums=[m.group(1) for m in PULL_RE.finditer(s)]
    if not nums: nums=[m.group(1) for m in HASH_RE.finditer(s)]
    seen=set(); out=[]
    for x in nums:
        if x not in seen: seen.add(x); out.append(x)
    return out

changed=[]; skipped_open=0
for ri in range(2, wsv.max_row+1):
    path=wsv.cell(ri,C_PATH).value
    team=wsv.cell(ri,C_TEAM).value
    if not path or team in (None,''):
        continue
    assoc=parse_prs(wsv.cell(ri,C_PR).value)+parse_prs(wsv.cell(ri,C_COMMPR).value)
    assoc=[p for p in dict.fromkeys(assoc)]
    if not assoc:
        continue
    status=wsv.cell(ri,C_STATUS).value
    cur=str(status).strip().lower() if status is not None else ''
    if all(merged.get(p,False) for p in assoc):
        if cur!='done':
            ws.cell(ri,C_STATUS).value='Done'
            changed.append((ri,os.path.basename(str(path)),assoc,status))
    else:
        skipped_open+=1

print(f'\nfiles with all PRs merged -> newly marked Done: {len(changed)}')
print(f'files with >=1 non-merged PR left unchanged: {skipped_open}')
for ri,f,assoc,old in changed:
    print(f'  row {ri}: {f}  PRs={assoc}  ({old!r} -> Done)')

if changed:
    bak=XLSX.replace('.xlsx',f'.premark_{datetime.datetime.now():%Y%m%d_%H%M%S}.bak.xlsx')
    shutil.copy(XLSX,bak)
    wb.save(XLSX)
    print(f'\nbackup: {os.path.basename(bak)}')
    print(f'saved: {os.path.basename(XLSX)}')
else:
    print('\nno changes; xlsx untouched')
