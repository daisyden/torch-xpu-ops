import json, glob, os
from datetime import datetime, timezone

def parse(t):
    if not t: return None
    return datetime.fromisoformat(t.replace('Z','+00:00'))

INTERNAL = {'etaf','guangyey'}  # Guangye or etaf
# Intel org members (authors + intel reviewers) -> excluded from "community"
INTEL_EXTRA = {'CuiYifeng','chuanqi129','xuyun44','EikanWang','LuFinch',
               'NayanNagabhushana-28','Niran814804102','mansiag05','xuhancn','LuFinch'}
COMMUNITY_KNOWN = {'jansel','fffrog'}  # jasonL=jansel, fffrog

LABEL_WF = {
    'ciflow/trunk': 'trunk',
    'ciflow/xpu': 'xpu',
    'ciflow/h100-distributed': 'Limited CI for distributed tests on H100',
}
FAIL_CONC = {'FAILURE','CANCELLED','TIMED_OUT','ACTION_REQUIRED','STARTUP_FAILURE'}

owned = json.load(open('/tmp/owned.json'))
rows = owned['rows']

# author set = Intel team
authors=set()
for fp in glob.glob('pr_cache/*.json'):
    d=json.load(open(fp))
    if d: authors.add(d['author']['login'])
INTEL_ALL = authors | INTERNAL | INTEL_EXTRA

# map PR -> is distributed (from any owned row referencing it)
pr_dist={}
pr_team={}
intel_prs=set()   # col F: Intel-authored port PRs
comm_only=set()   # col O: community / Google-doc refactor PRs
for r in rows:
    for n in r['prs']: intel_prs.add(n)
    for n in r['comm_prs']: comm_only.add(n)
    for n in r['prs']+r['comm_prs']:
        pr_dist[n]=pr_dist.get(n,False) or r['distributed']
        if r['team']: pr_team.setdefault(n,set()).add(r['team'])
# a PR listed in col F is treated as Intel even if it also appears in col O
comm_only-=intel_prs

def hours(a,b):
    if not a or not b: return None
    return round((b-a).total_seconds()/3600.0,1)

records=[]
for n in owned['prs']:
    fp=f'pr_cache/{n}.json'
    if not os.path.exists(fp): continue
    d=json.load(open(fp))
    if not d: continue
    created=parse(d['createdAt'])
    labels={l['name'] for l in d.get('labels') or []}
    is_merged=('Merged' in labels)
    merged=parse(d.get('mergedAt')) or (parse(d.get('closedAt')) if is_merged else None)
    # reviews
    internal_t=None; community_t=None; community_by=None
    for rv in d.get('reviews') or []:
        if rv['state']!='APPROVED': continue
        lg=rv['author']['login'] if rv.get('author') else None
        t=parse(rv.get('submittedAt'))
        if lg in INTERNAL:
            if internal_t is None or (t and t<internal_t): internal_t=t
        elif lg and lg not in INTEL_ALL:  # community = any external approver
            if community_t is None or (t and t<community_t):
                community_t=t; community_by=lg
    # required workflows from labels
    req_labels=[l for l in LABEL_WF if l in labels]
    req_wfs={LABEL_WF[l] for l in req_labels}
    ci_pass=None; ci_state='no_required_label'
    if req_wfs:
        wf_runs={w:[] for w in req_wfs}
        for c in d.get('statusCheckRollup') or []:
            w=c.get('workflowName')
            if w in wf_runs: wf_runs[w].append(c)
        all_ok=True; last=None; any_run=False
        for w,runs in wf_runs.items():
            if not runs: all_ok=False; continue
            for c in runs:
                any_run=True
                conc=c.get('conclusion')
                if conc in FAIL_CONC: all_ok=False
                ct=parse(c.get('completedAt'))
                if ct and (last is None or ct>last): last=ct
        if not any_run: ci_state='not_triggered'
        elif all_ok: ci_state='passed'; ci_pass=last
        else: ci_state='failing'
    records.append({
        'pr':n,'title':d.get('title'),'state':d['state'],
        'author':d['author']['login'],
        'team':','.join(sorted(pr_team.get(n,[]))),
        'merged':is_merged,
        'created':d['createdAt'],'closed':d.get('closedAt'),
        'labels':sorted(l for l in labels if l.startswith('ciflow/')),
        'req_labels':req_labels,'distributed':pr_dist.get(n,False),
        'is_refactor':n in comm_only,   # community/refactor PR from Google doc (col O)
        'internal_ok':internal_t is not None,
        'community_ok':community_t is not None,'community_by':community_by,
        'ci_state':ci_state,
        't_internal_h':hours(created,internal_t),
        't_community_h':hours(created,community_t),
        't_ci_h':hours(created,ci_pass),
        't_merge_h':hours(created,merged),
    })

json.dump(records,open('/tmp/pr_analysis.json','w'),indent=1)

# summary
tot=len(records)
def cnt(f): return sum(1 for r in records if f(r))
print('PRs analyzed:',tot)
print('internal review passed:',cnt(lambda r:r['internal_ok']))
print('community review passed:',cnt(lambda r:r['community_ok']))
print('CI passed:',cnt(lambda r:r['ci_state']=='passed'))
print('CI failing:',cnt(lambda r:r['ci_state']=='failing'))
print('CI not triggered:',cnt(lambda r:r['ci_state']=='not_triggered'))
print('no required label:',cnt(lambda r:r['ci_state']=='no_required_label'))
print('merged:',cnt(lambda r:r['merged']))
import statistics as st
for k in ('t_internal_h','t_community_h','t_ci_h','t_merge_h'):
    vals=[r[k] for r in records if r[k] is not None]
    if vals:
        print(f'{k}: n={len(vals)} median={st.median(vals):.1f}h mean={st.mean(vals):.1f}h max={max(vals):.1f}h')
