import json, statistics as st, glob, os
from collections import Counter
from datetime import datetime, timezone

recs_all=json.load(open('/tmp/pr_analysis.json'))
# "data updated" = when PR data was last fetched (newest pr_cache file), UTC
_cache=glob.glob('pr_cache/*.json')
data_updated=(datetime.fromtimestamp(max(os.path.getmtime(f) for f in _cache), timezone.utc)
              if _cache else datetime.now(timezone.utc))# Section 3 (PR status / three gates) tracks INTEL PRs only. Community/refactor
# PRs from the Google doc (col O) are discounted here - they are surfaced
# separately in the Refactor PR/Owner/Status columns.
recs_intel=[r for r in recs_all if not r.get('is_refactor')]
nRefactor=len(recs_all)-len(recs_intel)
# Drop abandoned PRs (closed but never merged) from ALL data: they carry no
# progress, so files whose only PR was abandoned correctly fall back to TBD.
def _abandoned(r): return r['state']=='CLOSED' and not r['merged']
recs=[r for r in recs_intel if not _abandoned(r)]
nAbandoned=len(recs_intel)-len(recs)
owned=json.load(open('/tmp/owned.json'))
rows=owned['rows']
# community test-refactor tracker (path -> {status,owner,ready_prs,merged_prs,...})
try:
    REFACTOR=json.load(open('/tmp/refactor_tracker.json'))
except FileNotFoundError:
    REFACTOR={}

# ---- Test file (owned, col L) stats ----
tot_owned=len(rows)
team=Counter(r['team'] for r in rows)
xpu=Counter({True:'xpu-enabled=True',False:'xpu-enabled=False',None:'xpu-enabled=blank'}.get(r['xpu'], str(r['xpu'])) for r in rows)
devrel=Counter(r['device_relevance'] for r in rows)
status_done=sum(1 for r in rows if str(r['status']).lower() in ('done','merged'))
with_pr=sum(1 for r in rows if r['prs'] or r['comm_prs'])

# ---- unified status/coverage classification (shared by both chart families) ----
CAT_ORDER=['Done','Community Review','Internal Review','CI','PRed','Not Applicable','WIP','TBD']
def classify(r):
    haspr=bool(r.get('prs') or r.get('comm_prs'))
    if r['status'] in (None,''):
        return 'PRed' if haspr else 'TBD'      # blank status but a PR exists -> PRed
    low=' '.join(str(r['status']).split()).rstrip('.').lower()   # collapse ws, drop trailing '.'
    MAP={
        'done':'Done','merged':'Done',
        'community review':'Community Review',
        'ci pass, wait for community review':'Community Review',
        'internal review':'Internal Review','in review':'Internal Review',
        'ci pass, wait for internal review':'Internal Review',
        'ci':'CI','in ci':'CI',
        'not applicable':'Not Applicable','feature gap':'Not Applicable','cuda only':'Not Applicable',
        'wip':'WIP','in progress':'WIP',
    }
    return MAP.get(low, 'PRed' if haspr else 'TBD')
teams_order=[t for t,_ in team.most_common()]

# ---- details data (chart label -> item list) for click-through panel ----
def fmt(h): return '' if h is None else (f'{h/24:.1f}d')

# pr-number -> analysis record lookup (for enriching file rows with PR detail)
PRLOOK={int(r['pr']):r for r in recs}
PRLOOK_S={str(r['pr']):r for r in recs}

# ---- real pipeline stage from PR data (source of truth for display) ----
# order: PRed -> CI -> Internal Review (guangyey/etaf) -> Community Review -> Done
def _gates(r):
    """(internal, ci, community, merged) from real PR data.
       Open PRs govern the file (every open PR must pass a gate).
       A merged PR completes the file only when no open PR is still pending.
       Community only counts once internal review has also passed."""
    prs=[PRLOOK_S.get(str(n)) for n in (r['prs']+r['comm_prs'])]
    prs=[p for p in prs if p]
    # discount PRs that are closed but never merged (abandoned) - they carry no progress
    prs=[p for p in prs if not (p['state']=='CLOSED' and not p['merged'])]
    open_prs=[p for p in prs if p['state']=='OPEN']
    if open_prs:
        i=all(p['internal_ok'] for p in open_prs)
        c=all(p['ci_state']=='passed' for p in open_prs)
        cm=all(p['community_ok'] for p in open_prs)
        return i,c,(cm and i),False
    if any(p['merged'] for p in prs):
        return True,True,True,True
    if not prs:
        return None,None,None,None          # no cached PR data
    i=all(p['internal_ok'] for p in prs)
    c=all(p['ci_state']=='passed' for p in prs)
    cm=all(p['community_ok'] for p in prs)
    return i,c,(cm and i),False
def real_stage(r):
    """Real pipeline stage from PR data, or None when no PR data is cached."""
    i,c,cm,merged=_gates(r)
    if i is None: return None
    if merged: return 'Done'
    if not c: return 'CI'
    if not i: return 'Internal Review'
    return 'Community Review'   # community pending or passed-but-not-merged
def pr_detail_list(row):
    nums=[]
    for n in (row.get('prs') or [])+(row.get('comm_prs') or []):
        try: nums.append(int(n))
        except (TypeError,ValueError): pass
    seen=set(); out=[]
    for n in nums:
        if n in seen: continue
        seen.add(n)
        r=PRLOOK.get(n)
        if r:
            out.append({'pr':r['pr'],'title':r['title'] or '','author':r['author'] or '',
                'dist':'yes' if r['distributed'] else '',
                'reqci':' '.join(l.replace('ciflow/','') for l in r['req_labels']),
                'internal':'Y' if r['internal_ok'] else '-','ci':r['ci_state'],
                'community':'Y' if r['community_ok'] else '-',
                't_int':fmt(r['t_internal_h']),'t_ci':fmt(r['t_ci_h']),
                't_com':fmt(r['t_community_h']),'t_mrg':fmt(r['t_merge_h']),
                'state':'merged' if r['merged'] else r['state'].lower()})
        else:
            out.append({'pr':n,'title':'(PR not in cache)','author':'','dist':'','reqci':'',
                'internal':'-','ci':'','community':'-','t_int':'','t_ci':'','t_com':'','t_mrg':'','state':''})
    return out
def file_item(r):
    d={'type':'file','k':r['file'] or r['path'],'sub':(r['path'] or ''),
       'team':r['team'] or '','extra':status_label(r),
       'owner':(str(r['assignee']) if r.get('assignee') not in (None,'') else (str(r['person']) if r.get('person') not in (None,'') else '')),
       'prs':pr_detail_list(r)}
    # community refactor tracker join (only for To Do / not-yet-Done files)
    if status_label(r)!='Done':
        t=REFACTOR.get(r['path'])
        if t:
            prs=(t.get('ready_prs') or [])+(t.get('merged_prs') or [])
            d['rpr']=prs           # list of [num,url]
            d['rowner']=t.get('owner') or ''
            d['rstatus']=t.get('status') or ''
    return d
DETAILS={'team':{},'xpu':{},'dev':{},'status':{}}
def status_label(r):
    """Effective status shown in the report. The REAL PR stage overrides the
       spreadsheet for in-flight files (so a status can't over-state progress,
       e.g. 'Community Review' when internal review hasn't happened). A human
       'Done' / 'Not Applicable' / 'WIP' is kept authoritative, and the
       spreadsheet is used when no PR data is cached."""
    s=classify(r)
    if s in ('Done','Not Applicable','WIP','TBD'): return s
    rs=real_stage(r)
    # No fallback to the sheet's in-flight status: if there is no PR data the
    # file has no real progress, so it is TBD (can't be CI/Review without a PR).
    return rs if rs is not None else 'TBD'
for r in rows:
    DETAILS['team'].setdefault(r['team'],[]).append(file_item(r))
    xl={True:'xpu-enabled=True',False:'xpu-enabled=False',None:'xpu-enabled=blank'}.get(r['xpu'],str(r['xpu']))
    DETAILS['xpu'].setdefault(xl,[]).append(file_item(r))
    DETAILS['dev'].setdefault(str(r['device_relevance']),[]).append(file_item(r))
    DETAILS['status'].setdefault(status_label(r),[]).append(file_item(r))
status_c=Counter(status_label(r) for r in rows)
# collapsed status for section-1 chart: PR-in-flight stages -> "Open PR"
OPEN_SET={'Community Review','Internal Review','CI','PRed'}
def collapse_status(r):
    s=status_label(r)
    return 'Open PR' if s in OPEN_SET else s
OSTAT_ORDER=['Done','Open PR','Not Applicable','WIP','TBD']
ostatus_c=Counter(collapse_status(r) for r in rows)
OSTAT_ORDER=[s for s in OSTAT_ORDER if ostatus_c.get(s)]
DETAILS['ostatus']={}
for r in rows:
    DETAILS['ostatus'].setdefault(collapse_status(r),[]).append(file_item(r))

def pr_item(r):
    return {'type':'pr','pr':r['pr'],'title':(r['title'] or ''),
            'team':r['team'] or '','author':r['author'] or '',
            'dist':'yes' if r['distributed'] else '',
            'reqci':' '.join(l.replace('ciflow/','') for l in r['req_labels']),
            'internal':'Y' if r['internal_ok'] else '-',
            'ci':r['ci_state'],
            'community':'Y' if r['community_ok'] else '-',
            't_int':fmt(r['t_internal_h']),'t_ci':fmt(r['t_ci_h']),
            't_com':fmt(r['t_community_h']),'t_mrg':fmt(r['t_merge_h']),
            'state':'merged' if r['merged'] else r['state'].lower()}
DETAILS['ci']={}
DETAILS['gates']={}
DETAILS['gates_pending']={}
# abandoned PRs were already dropped from recs at load time
recs_active=recs
for r in recs_active:
    DETAILS['ci'].setdefault(r['ci_state'],[]).append(pr_item(r))
    if r['internal_ok']: DETAILS['gates'].setdefault('Internal review',[]).append(pr_item(r))
    if r['ci_state']=='passed': DETAILS['gates'].setdefault('CI passed',[]).append(pr_item(r))
    if r['community_ok']: DETAILS['gates'].setdefault('Community review',[]).append(pr_item(r))
    if r['internal_ok'] and r['community_ok'] and r['ci_state']=='passed':
        DETAILS['gates'].setdefault('All 3 gates',[]).append(pr_item(r))
    # pending gates: only OPEN PRs still need work (abandoned already excluded)
    if r['state']=='OPEN':
        if not r['internal_ok']: DETAILS['gates_pending'].setdefault('Internal review',[]).append(pr_item(r))
        if r['ci_state']!='passed': DETAILS['gates_pending'].setdefault('CI',[]).append(pr_item(r))
        # community review is only pending once internal review AND CI have passed
        if r['internal_ok'] and r['ci_state']=='passed' and not r['community_ok']:
            DETAILS['gates_pending'].setdefault('Community review',[]).append(pr_item(r))
        if not (r['internal_ok'] and r['community_ok'] and r['ci_state']=='passed'):
            DETAILS['gates_pending'].setdefault('Any gate',[]).append(pr_item(r))

# per-team test-file status detail groups (statt_<i>)
STATUS_ORDER=[s for s in CAT_ORDER if status_c.get(s)]
status_by_team={t:Counter() for t in teams_order}
for i,t in enumerate(teams_order):
    g=f'statt_{i}'
    DETAILS[g]={}
    for r in rows:
        if r['team']==t:
            sl=status_label(r)
            status_by_team[t][sl]+=1
            DETAILS[g].setdefault(sl,[]).append(file_item(r))
# overall (all teams) status group
status_all=Counter()
DETAILS['statt_all']={}
for r in rows:
    sl=status_label(r)
    status_all[sl]+=1
    DETAILS['statt_all'].setdefault(sl,[]).append(file_item(r))

# ---- PR stats (abandoned closed-not-merged PRs already dropped at load) ----
nPR=len(recs_active)
internal_ok=sum(1 for r in recs_active if r['internal_ok'])
community_ok=sum(1 for r in recs_active if r['community_ok'])
ci_state=Counter(r['ci_state'] for r in recs_active)
merged=sum(1 for r in recs_active if r['merged'])
all3=sum(1 for r in recs_active if r['internal_ok'] and r['community_ok'] and r['ci_state']=='passed')
# pending (needs work) counts among OPEN PRs
nOpen=sum(1 for r in recs_active if r['state']=='OPEN')
pend_int=sum(1 for r in recs_active if r['state']=='OPEN' and not r['internal_ok'])
pend_ci =sum(1 for r in recs_active if r['state']=='OPEN' and r['ci_state']!='passed')
pend_com=sum(1 for r in recs_active if r['state']=='OPEN' and r['internal_ok'] and r['ci_state']=='passed' and not r['community_ok'])
pend_any=sum(1 for r in recs_active if r['state']=='OPEN' and not (r['internal_ok'] and r['community_ok'] and r['ci_state']=='passed'))

def dist_days(key):
    buckets=['<1d','1-3d','3-7d','1-2w','2-4w','>4w']
    b=Counter()
    for r in recs_active:
        h=r[key]
        if h is None: continue
        days=h/24.0
        if days<1: b['<1d']+=1
        elif days<3: b['1-3d']+=1
        elif days<7: b['3-7d']+=1
        elif days<14: b['1-2w']+=1
        elif days<28: b['2-4w']+=1
        else: b['>4w']+=1
    return [b[x] for x in buckets], buckets

def stat(key):
    v=[r[key] for r in recs_active if r[key] is not None]
    if not v: return None
    return {'n':len(v),'median_d':round(st.median(v)/24,1),'mean_d':round(st.mean(v)/24,1),'p90_d':round(sorted(v)[int(len(v)*0.9)-1]/24,1),'max_d':round(max(v)/24,1)}

timing={k:stat(k) for k in ('t_internal_h','t_community_h','t_ci_h','t_merge_h')}
tlabels={'t_internal_h':'Creation \u2192 Internal review','t_community_h':'Creation \u2192 Community review','t_ci_h':'Creation \u2192 CI pass','t_merge_h':'Creation \u2192 Merge'}

# ---- timing detail groups (click-through on timing charts) ----
def bucket_of(h):
    d=h/24.0
    if d<1: return '<1d'
    if d<3: return '1-3d'
    if d<7: return '3-7d'
    if d<14: return '1-2w'
    if d<28: return '2-4w'
    return '>4w'
DETAILS['timing']={}          # main chart: milestone label -> PRs that reached it
_HIST={'hint':'t_internal_h','hci':'t_ci_h','hcom':'t_community_h','hmg':'t_merge_h'}
for g in _HIST: DETAILS[g]={}  # histograms: milestone group -> bucket -> PRs
for r in recs_active:
    for key in ('t_internal_h','t_community_h','t_ci_h','t_merge_h'):
        if r[key] is None: continue
        DETAILS['timing'].setdefault(tlabels[key],[]).append(pr_item(r))
    for g,key in _HIST.items():
        if r[key] is None: continue
        DETAILS[g].setdefault(bucket_of(r[key]),[]).append(pr_item(r))

int_d,buckets=dist_days('t_internal_h')
com_d,_=dist_days('t_community_h')
ci_d,_=dist_days('t_ci_h')
mg_d,_=dist_days('t_merge_h')

# per-PR table rows section removed (details now shown in right-side panel)

def js(x): return json.dumps(x)

# bottleneck insight
meds={tlabels[k]:timing[k]['median_d'] for k in timing if timing[k]}
worst=max(meds,key=meds.get) if meds else 'n/a'

# per-team test-file status pie HTML + JS
statpies_html=(f'<div class=panel><h3>Status \u2013 All teams ({tot_owned})</h3><div class=ch><canvas id="statt_all"></canvas></div></div>'
    + ''.join(f'<div class=panel><h3>Status \u2013 {t} ({team[t]})</h3><div class=ch><canvas id="statt_{i}"></canvas></div></div>' for i,t in enumerate(teams_order)))
statpies_js=(f"PIE('statt_all',{js(STATUS_ORDER)},{js([status_all[k] for k in STATUS_ORDER])},cols({js(STATUS_ORDER)}),'statt_all');"
    + ''.join(f"PIE('statt_{i}',{js(STATUS_ORDER)},{js([status_by_team[t][k] for k in STATUS_ORDER])},cols({js(STATUS_ORDER)}),'statt_{i}');" for i,t in enumerate(teams_order)))

# ---- Section 5: forecast (4-week pace, gate-aware PR units) ----
from datetime import timedelta, timezone
def _p(t): return datetime.fromisoformat(t.replace('Z','+00:00')) if t else None
now=datetime.now(timezone.utc)
# active = owned files not Done and not Not Applicable (uses effective status)
active=[r for r in rows if status_label(r) not in ('Done','Not Applicable')]
backlog=len(active)
FKEY='Remaining not-Done files'
DETAILS['forecast']={FKEY:[file_item(r) for r in active]}
import math as _math
def _units(files):
    """PR units: existing PRs grouped (shared PR = 1) + new PRs for files with no PR.
       Un-PR'd inductor & distributed files are batched 4 files per PR;
       device_agnostic collapsed to 1 per team; all others 1 each."""
    existing=set(); new_spec=0; agn_teams=set(); ind_f=0; dist_f=0
    for r in files:
        prs=r['prs']+r['comm_prs']
        if prs:
            for n in prs: existing.add(str(n)); 
            continue
        if 'inductor' in (r.get('path') or '').lower(): ind_f+=1
        elif r.get('distributed'): dist_f+=1
        elif str(r['device_relevance'])=='device_agnostic': agn_teams.add(r['team'])
        else: new_spec+=1
    ind_u=_math.ceil(ind_f/4); dist_u=_math.ceil(dist_f/4)
    tot=len(existing)+new_spec+len(agn_teams)+ind_u+dist_u
    return {'tot':tot,'ex':len(existing),'ns':new_spec,'at':len(agn_teams),
            'ind_f':ind_f,'ind_u':ind_u,'dist_f':dist_f,'dist_u':dist_u}
def _mtimes(h): return sorted(_p(r['created'])+timedelta(hours=r[h]) for r in recs_active if r[h] is not None and r['created'])
def _rate4(ts): return (sum(1 for t in ts if t>=ts[-1]-timedelta(weeks=4))/4.0) if ts else 0.0
def _date(wk): return (now+timedelta(weeks=wk)).date().isoformat() if wk is not None else 'n/a'
# PR creation rate (last 4 weeks) from PR createdAt
_ctimes=sorted(_p(r['created']) for r in recs_active if r['created'])
rate_create=_rate4(_ctimes)
# files still needing a NEW PR (no existing PR); gate-passed files already have one
rem_new=[r for r in active if not (r['prs']+r['comm_prs'])]
# remaining files per milestone (exclude those already past the gate)
rem_int=[r for r in active if not _gates(r)[0]]
rem_ci =[r for r in active if not _gates(r)[1]]
# milestone rows: (label, timing-key or None-for-creation, remaining-files, rate)
FC_MILE=[('PR created',None,rem_new,rate_create),
         ('Internal review','t_internal_h',rem_int,None),
         ('CI pass','t_ci_h',rem_ci,None),
         ('Merge / all pass','t_merge_h',active,None)]
fc_rows=[]
for lab,h,rem,fixed in FC_MILE:
    u=_units(rem)
    r4=fixed if fixed is not None else _rate4(_mtimes(h))
    # gate throughput can't exceed PR-creation supply -> effective rate is the bottleneck
    eff=r4 if h is None else min(r4,rate_create)
    wk=(u['tot']/eff) if eff>0 else None
    fc_rows.append({'lab':lab,'files':len(rem),'units':u['tot'],'ex':u['ex'],'ns':u['ns'],
                    'at':u['at'],'ind_f':u['ind_f'],'ind_u':u['ind_u'],'dist_f':u['dist_f'],
                    'dist_u':u['dist_u'],'rate':r4,'eff':eff,'wk':wk,'date':_date(wk)})
_mrg=fc_rows[-1]
# burn-down: one line per milestone, y = PR units remaining vs weeks
def _burn(u,rate):
    if not rate: return []
    wk=u/rate; step=max(wk/40,0.5); pts=[]; w=0.0
    while w<=wk:
        pts.append({'x':round(w,1),'y':round(max(u-rate*w,0),1)}); w+=step
    pts.append({'x':round(wk,1),'y':0}); return pts
burn_new=_burn(fc_rows[0]['units'],fc_rows[0]['eff'])
burn_int=_burn(fc_rows[1]['units'],fc_rows[1]['eff'])
burn_ci =_burn(fc_rows[2]['units'],fc_rows[2]['eff'])
burn_mrg=_burn(fc_rows[3]['units'],fc_rows[3]['eff'])
rc0,rc1,rc2,rc3=(fc_rows[i]['eff'] for i in range(4))
# per-PR milestone timing stats (median/mean/p90 days from PR open)
def _mstat(key):
    v=[r[key]/24.0 for r in recs_active if r[key] is not None]
    if not v: return {'median':0,'mean':0,'p90':0,'n':0}
    return {'median':round(st.median(v),1),'mean':round(st.mean(v),1),
            'p90':round(sorted(v)[max(int(len(v)*0.9)-1,0)],1),'n':len(v)}
PRED_KEYS=[('t_internal_h','Internal review'),('t_community_h','Community review'),('t_ci_h','CI pass')]
pred_stats=[(_mstat(k),lab) for k,lab in PRED_KEYS]
pred_labels=[lab for _,lab in pred_stats]
pred_median=[s['median'] for s,_ in pred_stats]
pred_mean=[s['mean'] for s,_ in pred_stats]
pred_p90=[s['p90'] for s,_ in pred_stats]

fc_formula=(
 "<b>Method</b> (4-week pace; gate-aware; PR units, not raw files):<br>"
 "&bull; Rate = events in the last 4 weeks &divide; 4 &mdash; PR creation {rp:.2f}, "
 "internal {ri:.2f}, CI {rc:.2f}, merge {rm:.2f} PR/wk.<br>"
 "&bull; Remaining counts only files not yet past that gate; already-open PRs are reused.<br>"
 "&bull; <b>PR units</b> = existing PRs (files sharing a PR = 1) + new PRs for un-PR'd files: "
 "inductor &amp; distributed batched 4 files/PR, device_agnostic 1 per team, others 1 each.<br>"
 "&bull; A gate can't outrun PR creation, so its effective rate = min(gate rate, creation rate).<br>"
 "&bull; Finish date = today + (PR units &divide; effective rate)."
).format(rp=rate_create,ri=fc_rows[1]['rate'],rc=fc_rows[2]['rate'],rm=fc_rows[3]['rate'])
fc_note=(f"Of {backlog} active files (not Done / not N-A): at the 4-week pace, "
         f"all needed PRs are <b>created</b> ~<b>{fc_rows[0]['date']}</b>, "
         f"<b>internal review</b> done ~<b>{fc_rows[1]['date']}</b>, "
         f"<b>CI</b> ~<b>{fc_rows[2]['date']}</b>, and "
         f"<b>all merged</b> ~<b>{_mrg['date']}</b>.")
fc_table=("<table class=dt><thead><tr><th>Milestone</th><th>Files left</th><th>PR units</th>"
          "<th>= existing + specific + agnostic-team + inductor(4/PR) + distributed(4/PR)</th><th>Rate (PR/wk)</th><th>Eff. rate</th><th>Weeks</th><th>Finish date</th></tr></thead><tbody>"
          + ''.join(f"<tr><td>{x['lab']}</td><td>{x['files']}</td><td><b>{x['units']}</b></td>"
                    f"<td>{x['ex']} + {x['ns']} + {x['at']} + {x['ind_u']}(&larr;{x['ind_f']}f) + {x['dist_u']}(&larr;{x['dist_f']}f)</td><td>{x['rate']:.2f}</td>"
                    f"<td>{x['eff']:.2f}</td><td>{x['wk']:.0f}</td><td><b>{x['date']}</b></td></tr>" for x in fc_rows)
          + "</tbody></table>")
mstat_rows=''.join(f"<tr><td>{lab}</td><td>{s['median']}</td><td>{s['mean']}</td><td>{s['p90']}</td><td>{s['n']}</td></tr>"
                   for s,lab in pred_stats)
fc_mtable=(f"<table class=dt><thead><tr><th>Milestone (days from PR open)</th><th>median</th><th>mean</th>"
           f"<th>p90</th><th>n</th></tr></thead><tbody>{mstat_rows}</tbody></table>")

# ---- missing-ciflow flag + full status audit (sheet stage vs real PR stage) ----
# files whose OPEN PR(s) can't run CI because no required ciflow label is set
noflow_files=[]
for r in rows:
    if classify(r) in ('Done','Not Applicable'): continue
    bad=[PRLOOK_S.get(str(n)) for n in (r['prs']+r['comm_prs'])]
    bad=[p for p in bad if p and p['state']=='OPEN' and p['ci_state']=='no_required_label']
    if bad: noflow_files.append((r,bad))
DETAILS['no_ciflow']={}
for r,bad in noflow_files:
    DETAILS['no_ciflow'].setdefault('Missing ciflow label',[]).append(file_item(r))
n_noflow=len(noflow_files)
NOFLOW_KEY='Missing ciflow label'

# audit: raw spreadsheet stage (classify) vs real PR stage (real_stage)
STAGE_RANK={'PRed':0,'CI':1,'Internal Review':2,'Community Review':3,'Done':4}
def _audit_item(r,sheet,real):
    it=file_item(r); it['extra']=f'sheet: {sheet} | real: {real}'
    return it
AHEAD_KEY='Status ahead of real PR stage'
BEHIND_KEY='Status behind real PR stage (can advance)'
NODATA_KEY='Active file, no cached PR data'
DETAILS['ahead']={};DETAILS['behind']={};DETAILS['nodata']={}
n_ahead=n_behind=n_nodata=0
for r in rows:
    sheet=classify(r)                       # raw spreadsheet stage
    if sheet in ('Not Applicable','WIP'): continue
    real=real_stage(r)
    if real is None:
        if sheet not in ('PRed','TBD','Done'):
            DETAILS['nodata'].setdefault(NODATA_KEY,[]).append(_audit_item(r,sheet,'n/a')); n_nodata+=1
        continue
    if sheet not in STAGE_RANK: continue
    if STAGE_RANK[sheet]>STAGE_RANK[real]:
        DETAILS['ahead'].setdefault(AHEAD_KEY,[]).append(_audit_item(r,sheet,real)); n_ahead+=1
    elif STAGE_RANK[sheet]<STAGE_RANK[real]:
        DETAILS['behind'].setdefault(BEHIND_KEY,[]).append(_audit_item(r,sheet,real)); n_behind+=1

flag_html=''
_parts=[]
if n_noflow:
    _parts.append(f"<b>{n_noflow}</b> active file(s) have open PR(s) with <b>no ciflow label</b> "
                  f"&mdash; CI cannot run until a label is added "
                  f"(<a href=\"#\" onclick=\"showDetail('no_ciflow','{NOFLOW_KEY}');return false\">list</a>).")
if n_ahead:
    _parts.append(f"<b>{n_ahead}</b> file(s) have a spreadsheet status <b>ahead of</b> real PR stage "
                  f"(e.g. marked Community/Internal review but gates not actually passed) "
                  f"(<a href=\"#\" onclick=\"showDetail('ahead','{AHEAD_KEY}');return false\">list</a>).")
if n_behind:
    _parts.append(f"<b>{n_behind}</b> file(s) are <b>behind</b> reality &mdash; PR has already progressed "
                  f"past the spreadsheet status (status can be advanced) "
                  f"(<a href=\"#\" onclick=\"showDetail('behind','{BEHIND_KEY}');return false\">list</a>).")
if n_nodata:
    _parts.append(f"<b>{n_nodata}</b> active file(s) have a status but <b>no cached PR data</b> to verify "
                  f"(<a href=\"#\" onclick=\"showDetail('nodata','{NODATA_KEY}');return false\">list</a>).")
if _parts:
    flag_html="<div class=insight style='border-left:4px solid #ea4335'>"+"<br>".join(_parts)+"</div>"

html=f"""<!DOCTYPE html><html><head><meta charset=utf-8>
<title>XPU Upstream Test-File & PR Status</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#f4f6f8;color:#1a1f26}}
header{{background:#0b3d91;color:#fff;padding:22px 32px}}
header h1{{margin:0;font-size:22px}} header p{{margin:6px 0 0;opacity:.8;font-size:13px}}
.wrap{{max-width:1680px;margin:0 auto;padding:24px}}
.cards{{display:flex;flex-wrap:wrap;gap:14px;margin-bottom:22px}}
.card{{background:#fff;border-radius:10px;padding:16px 20px;flex:1;min-width:150px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.card .v{{font-size:28px;font-weight:700;color:#0b3d91}} .card .l{{font-size:12px;color:#667;margin-top:4px}}
.split{{display:flex;gap:22px;align-items:flex-start}}
.left{{flex:1 1 620px;min-width:420px}}
.right{{flex:1 1 640px;min-width:420px;position:sticky;top:16px}}
.cgrid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:18px}}
.panel{{background:#fff;border-radius:10px;padding:16px 18px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.panel h3{{margin:0 0 10px;font-size:14px}}
.ch{{position:relative;height:200px}}
.ch.tall{{height:230px}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
th,td{{padding:6px 8px;border-bottom:1px solid #eee;text-align:left}}
th{{background:#f0f3f7;position:sticky;top:0}}
td.c{{text-align:center}} td.r{{text-align:right;font-variant-numeric:tabular-nums}} td.t{{color:#445}}
.y{{color:#137333;font-weight:700}} .n{{color:#c5221f;font-weight:700}} .g{{color:#999}}
.insight{{background:#fff8e1;border-left:4px solid #f9a825;padding:14px 18px;border-radius:6px;margin-bottom:22px;font-size:14px}}
.note{{font-size:12px;color:#667;margin-top:6px}}
h2{{font-size:16px;margin:20px 0 10px;color:#0b3d91}}
canvas{{cursor:pointer}}
#dpanel{{display:flex;flex-direction:column;max-height:calc(100vh - 40px)}}
#dpanel .dh{{background:#0b3d91;color:#fff;padding:12px 16px;font-size:14px;border-radius:8px 8px 0 0;display:flex;justify-content:space-between;align-items:center}}
#dpanel .dh b{{font-weight:600}}
#dbody{{overflow:auto;padding:0}}
#dbody table.dt{{font-size:11px}}
#dbody table.dt th{{background:#f0f3f7;position:sticky;top:0;white-space:nowrap;z-index:1}}
#dbody table.dt td.tt{{max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#445}}
#dbody table.dt tr.fltr th,#dbody table.dt tr.fltr td{{position:sticky;top:22px;background:#e8edf3;padding:2px 3px;z-index:1}}
#dbody table.dt tr.fltr input{{width:100%;min-width:34px;box-sizing:border-box;font-size:10px;padding:2px 4px;border:1px solid #c4cdd8;border-radius:3px;background:#fff}}
#dbody .hint{{padding:18px;color:#888;font-size:13px}}
</style></head><body>
<header><h1>XPU Upstream &mdash; Test-File &amp; PR Status Report</h1>
<p>Scope: owned test files (Owner/team column set) &bull; {nPR} unique pytorch/pytorch PRs &bull; PR data updated {data_updated:%Y-%m-%d %H:%M} UTC &bull; report generated {now:%Y-%m-%d %H:%M} UTC</p></header>
<div class=wrap>

<div class=cards>
<div class=card><div class=v>{tot_owned}</div><div class=l>Owned test files</div></div>
<div class=card><div class=v>{status_done}</div><div class=l>Status = Done</div></div>
<div class=card><div class=v>{with_pr}</div><div class=l>Files with a PR</div></div>
<div class=card><div class=v>{nPR}</div><div class=l>Unique PRs</div></div>
<div class=card><div class=v>{all3}</div><div class=l>PRs passing all 3 gates</div></div>
<div class=card><div class=v>{merged}</div><div class=l>PRs merged</div></div>
</div>

<div class=insight><b>Bottleneck:</b> the longest median wait is <b>{worst}</b>
({meds.get(worst,'?')} days median). Community review and CI turnaround dominate cycle time.
&nbsp;<b>Tip:</b> click any chart segment/bar to load matching files or PRs into the panel on the right.</div>

<div class=split>
<div class=left>

<h2>1. Test-file status</h2>
<div class=cgrid>
<div class=panel><h3>Owned files by team</h3><div class=ch><canvas id=team></canvas></div></div>
<div class=panel><h3>xpu-enabled status</h3><div class=ch><canvas id=xpu></canvas></div></div>
<div class=panel><h3>device_relevance</h3><div class=ch><canvas id=dev></canvas></div></div>
<div class=panel><h3>Test-file Status</h3><div class=ch><canvas id=status></canvas></div></div>
</div>

<h2>2. Test-file Status by team</h2>
<div class=cgrid>
{statpies_html}
</div>

<h2>3. PR status &mdash; the three gates</h2>
<div class=note>Intel PRs only: {nRefactor} community/refactor PR(s) from the Google doc are discounted (tracked in the Refactor columns). {nAbandoned} abandoned (closed-but-never-merged) PR(s) dropped; files whose only PR was abandoned show as TBD. Charts below cover {nPR} active Intel PRs.</div>
{flag_html}
<div class=cgrid>
<div class=panel><h3>Gate pass counts (of {nPR} PRs)</h3><div class=ch><canvas id=gates></canvas></div></div>
<div class=panel><h3>Gate pending / needs work ({nOpen} open PRs)</h3><div class=ch><canvas id=gates_pending></canvas></div></div>
<div class=panel><h3>CI state breakdown</h3><div class=ch><canvas id=ci></canvas></div></div>
</div>

<h2>4. PR Timing &mdash; creation to each milestone</h2>
<div class=panel><h3>Median / mean / p90 / max (days)</h3><div class=ch tall><canvas id=timing></canvas></div>
<div class=note>Lower is better. n = number of PRs that reached that milestone.</div></div>
<div class=cgrid style=margin-top:16px>
<div class=panel><h3>Creation &rarr; Internal review (dist.)</h3><div class=ch><canvas id=hint></canvas></div></div>
<div class=panel><h3>Creation &rarr; CI pass (dist.)</h3><div class=ch><canvas id=hci></canvas></div></div>
<div class=panel><h3>Creation &rarr; Community review (dist.)</h3><div class=ch><canvas id=hcom></canvas></div></div>
<div class=panel><h3>Creation &rarr; Merge (dist.)</h3><div class=ch><canvas id=hmg></canvas></div></div>
</div>

<h2>5. Forecast &mdash; when will all files pass?</h2>
<div class=insight>{fc_formula}</div>
<div class=insight>{fc_note}</div>
<div class=cgrid>
<div class=panel><h3>Burn-down: files remaining until all pass</h3><div class=ch tall><canvas id=burn></canvas></div>
<div class=note>Assumes constant historical merge throughput. Click to list the {backlog} remaining files.</div></div>
<div class=panel><h3>Predicted time per PR (days from open)</h3><div class=ch tall><canvas id=pred></canvas></div>
<div class=note>Median / mean / p90 of historical open&rarr;milestone times. Click to list remaining files.</div></div>
</div>
<div class=cgrid style=margin-top:16px>
<div class=panel><h3>Per-PR milestone timing</h3>{fc_mtable}
<div class=note>Assumes 1 file = 1 PR following the historical distribution independently; p90(community) &gt; p90(CI) is a small-sample artifact.</div></div>
<div class=panel><h3>Completion scenarios</h3>{fc_table}</div>
</div>

</div><!-- /left -->

<div class=right>
<div class="panel" id=dpanel style=padding:0>
<div class=dh><b id=dtitle>Details</b><span id=dcount style=opacity:.8;font-size:12px></span>
<input id=dfilter placeholder="filter all rows\u2026" oninput="filterDetail()" style="margin-left:auto;font-size:12px;padding:3px 8px;border:none;border-radius:4px;width:160px">
<button onclick="exportCSV()" style="margin-left:8px;font-size:12px;padding:3px 10px;border:none;border-radius:4px;background:#137333;color:#fff;cursor:pointer">Export CSV</button>
</div>
<div id=dbody><div class=hint>Click a chart segment or bar on the left to list the matching files or PRs here.</div></div>
</div>
<div class=note>Internal = approval by etaf/guangyey. Community = approval by any external maintainer (jansel, fffrog, albanD, ...). CI = required ciflow workflow(s) green: refactor&rarr;ciflow/trunk, XPU&rarr;ciflow/xpu, distributed&rarr;ciflow/h100-distributed.</div>
</div>

</div><!-- /split -->
</div>
<script>
const DETAILS={js(DETAILS)};
const badge=v=>v==='Y'?'<span class=y>Y</span>':(v==='-'?'<span class=g>-</span>':(v||''));
const cistate=v=>({{passed:'<span class=y>pass</span>',failing:'<span class=n>fail</span>',not_triggered:'<span class=g>not run</span>',no_required_label:'<span class=g>no label</span>'}}[v]||v||'');
const esc=s=>(s||'').replace(/"/g,'&quot;');
function prHeadCells(it){{
  return `<td><a href='https://github.com/pytorch/pytorch/pull/${{it.pr}}' target=_blank>#${{it.pr}}</a></td>`
    +`<td class=tt title="${{esc(it.title)}}">${{(it.title||'').slice(0,50)}}</td>`
    +`<td>${{it.author||''}}</td>`;
}}
function prTailCells(it){{
  return `<td class=c>${{it.dist||''}}</td><td>${{it.reqci||''}}</td>`
    +`<td class=c>${{badge(it.internal)}}</td><td class=c>${{cistate(it.ci)}}</td><td class=c>${{badge(it.community)}}</td>`
    +`<td class=r>${{it.t_int||''}}</td><td class=r>${{it.t_ci||''}}</td><td class=r>${{it.t_com||''}}</td><td class=r>${{it.t_mrg||''}}</td>`
    +`<td class=c>${{it.state||''}}</td>`;
}}
function prCells(it){{ return prHeadCells(it)+prTailCells(it); }}
function colFilterRow(cols){{
  return '<tr class=fltr>'+cols.map((_,i)=>`<td><input data-c=${{i}} oninput="filterDetail()" placeholder="\u2315"></td>`).join('')+'</tr>';
}}
function headWith(cols){{
  return '<thead><tr>'+cols.map(c=>`<th>${{c}}</th>`).join('')+'</tr>'+colFilterRow(cols)+'</thead>';
}}
const PR_HEAD=['PR','Title','Author'];
const PR_TAIL=['dist','req CI','Int','CI','Com','t.int','t.CI','t.com','t.mrg','state'];
const REFAC_COLS=['Refactor PR','R.Owner','R.Status'];
const PR_COLS=PR_HEAD.concat(PR_TAIL);
// refactor columns placed right after the Author column
const FILE_COLS=['Path','Team','Status'].concat(PR_HEAD).concat(REFAC_COLS).concat(PR_TAIL);
function refactorCells(it){{
  const prs=it.rpr||[];
  const links=prs.length
    ? prs.map(p=>`<a href='${{p[1]}}' target=_blank>#${{p[0]}}</a>`).join(' ')
    : '';
  return `<td>${{links}}</td><td>${{esc(it.rowner||'')}}</td><td class=c>${{esc(it.rstatus||'')}}</td>`;
}}
function showDetail(group,label){{
  const items=(DETAILS[group]||{{}})[label]||[];
  const fi=document.getElementById('dfilter'); if(fi) fi.value='';
  document.getElementById('dtitle').innerText=label;
  document.getElementById('dcount').innerText='('+items.length+')';
  const b=document.getElementById('dbody');
  if(!items.length){{ b.innerHTML='<div class=hint>No items.</div>'; return; }}
  if(items[0].type==='pr'){{
    let h='<table class=dt>'+headWith(PR_COLS)+'<tbody>';
    h+=items.map(it=>'<tr>'+prCells(it)+'</tr>').join('');
    b.innerHTML=h+'</tbody></table>';
  }} else {{
    // file rows, expanded per associated PR; refactor cols sit after Author
    let h='<table class=dt>'+headWith(FILE_COLS)+'<tbody>';
    items.forEach(it=>{{
      const prs=it.prs||[];
      const fc=`<td title="${{esc(it.sub)}}"><b>${{it.sub||it.k}}</b></td><td>${{it.team}}</td><td>${{it.extra||''}}</td>`;
      const rc=refactorCells(it);
      const ownerCell=`<td>${{esc(it.owner||'')}}</td>`;  // Author col = excel owner
      if(!prs.length){{
        h+=`<tr>`+fc+`<td colspan=2 class=g>&mdash; no PR &mdash;</td>`+ownerCell+rc+`<td colspan=10 class=g></td></tr>`;
      }} else {{
        prs.forEach(p=>{{
          const head=`<td><a href='https://github.com/pytorch/pytorch/pull/${{p.pr}}' target=_blank>#${{p.pr}}</a></td>`
            +`<td class=tt title="${{esc(p.title)}}">${{(p.title||'').slice(0,50)}}</td>`+ownerCell;
          h+='<tr>'+fc+head+rc+prTailCells(p)+'</tr>';
        }});
      }}
    }});
    b.innerHTML=h+'</tbody></table>';
  }}
  document.getElementById('dbody').scrollTop=0;
}}
function filterDetail(){{
  const tbl=document.querySelector('#dbody table.dt');
  if(!tbl) return;
  const q=(document.getElementById('dfilter').value||'').toLowerCase().trim();
  const filters=[...tbl.querySelectorAll('tr.fltr input')].map(inp=>inp.value.toLowerCase().trim());
  const rows=tbl.querySelectorAll('tbody tr');
  let shown=0;
  rows.forEach(tr=>{{
    const cells=tr.children;
    let ok=!q || tr.innerText.toLowerCase().includes(q);
    if(ok){{
      for(let i=0;i<filters.length;i++){{
        if(filters[i] && !((cells[i]?cells[i].innerText:'').toLowerCase().includes(filters[i]))){{ ok=false; break; }}
      }}
    }}
    tr.style.display=ok?'':'none';
    if(ok) shown++;
  }});
  document.getElementById('dcount').innerText='('+shown+')';
}}
function exportCSV(){{
  const tbl=document.querySelector('#dbody table.dt');
  if(!tbl) return;
  const q=s=>'"'+(s||'').replace(/"/g,'""').replace(/\\s+/g,' ').trim()+'"';
  const hdr=[...tbl.querySelectorAll('thead tr:first-child th')].map(th=>q(th.innerText));
  const lines=[hdr.join(',')];
  tbl.querySelectorAll('tbody tr').forEach(tr=>{{
    if(tr.style.display==='none') return;
    lines.push([...tr.children].map(td=>q(td.innerText)).join(','));
  }});
  const blob=new Blob([lines.join('\\n')],{{type:'text/csv'}});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=(document.getElementById('dtitle').innerText||'details').replace(/[^\\w]+/g,'_').slice(0,60)+'.csv';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}}
const clickCfg=(group)=>({{onClick:(e,els,ch)=>{{ if(els.length){{ showDetail(group,ch.data.labels[els[0].index]); }} }}}});
const BASE={{maintainAspectRatio:false,responsive:true}};
Chart.register(ChartDataLabels);
const fmtVal=v=>{{ const n=(v&&typeof v==='object')?v.y:v; return (typeof n==='number'&&!Number.isInteger(n))?n.toFixed(1):n; }};
// does the label text physically fit inside the doughnut slice?
const labelFits=(ctx)=>{{
  const arc=ctx.chart.getDatasetMeta(0).data[ctx.dataIndex]; if(!arc) return false;
  const span=arc.endAngle-arc.startAngle;
  const rMid=(arc.innerRadius+arc.outerRadius)/2;
  const arcLen=span*rMid;                       // tangential room at mid radius
  const ring=arc.outerRadius-arc.innerRadius;    // radial room
  const c=ctx.chart.ctx; c.save(); c.font="700 11px sans-serif";
  const tw=c.measureText(String(fmtVal(ctx.dataset.data[ctx.dataIndex]))).width; c.restore();
  return (tw+6)<=arcLen && 13<=ring;
}};
Chart.defaults.set('plugins.datalabels',{{
  color:ctx=>ctx.chart.config.type==='doughnut'?'#fff':'#202124',
  font:{{size:11,weight:'700'}},
  display:ctx=>{{
    const v=ctx.dataset.data[ctx.dataIndex];
    const n=(v&&typeof v==='object')?v.y:v;
    if(n===null||n===undefined||n===0) return false;
    // doughnut: draw only when the number fits; otherwise it shows on hover (tooltip)
    if(ctx.chart.config.type==='doughnut') return labelFits(ctx);
    return true;
  }},
  formatter:fmtVal,
  anchor:ctx=>ctx.chart.config.type==='line'?'end':(ctx.chart.config.type==='doughnut'?'center':'end'),
  align:ctx=>ctx.chart.config.type==='line'?'top':(ctx.chart.config.type==='doughnut'?'center':'end'),
  offset:2,
  clamp:true
}});
const PIE=(id,labels,data,colors,group)=>new Chart(document.getElementById(id),{{type:'doughnut',data:{{labels:labels,datasets:[{{data:data,backgroundColor:colors}}]}},options:{{...BASE,...(group?clickCfg(group):{{}}),layout:{{padding:6}},plugins:{{legend:{{position:'right',labels:{{boxWidth:12,font:{{size:11}}}}}}}}}}}});
const BAR=(id,labels,data,color,horiz,group)=>new Chart(document.getElementById(id),{{type:'bar',data:{{labels:labels,datasets:[{{data:data,backgroundColor:color}}]}},options:{{...BASE,...(group?clickCfg(group):{{}}),indexAxis:horiz?'y':'x',plugins:{{legend:{{display:false}}}},scales:{{y:{{beginAtZero:true}}}}}}}});
const C=['#0b3d91','#1a73e8','#34a853','#fbbc04','#ea4335','#9aa0a6','#a142f4','#00acc1'];
const CATCOLOR={{'Done':'#137333','Open PR':'#1a73e8','Community Review':'#1a73e8','Internal Review':'#a142f4','CI':'#fbbc04','PRed':'#00acc1','Not Applicable':'#9aa0a6','WIP':'#ea4335','TBD':'#dadce0'}};
const cols=labels=>labels.map(l=>CATCOLOR[l]||'#888');
PIE('team',{js(list(team.keys()))},{js(list(team.values()))},C,'team');
PIE('xpu',{js(list(xpu.keys()))},{js(list(xpu.values()))},['#34a853','#ea4335','#9aa0a6'],'xpu');
PIE('dev',{js([str(k) for k in devrel.keys()])},{js(list(devrel.values()))},C,'dev');
PIE('status',{js(list(OSTAT_ORDER))},{js([ostatus_c[k] for k in OSTAT_ORDER])},cols({js(list(OSTAT_ORDER))}),'ostatus');
{statpies_js}
BAR('gates',['Internal review','CI passed','Community review','All 3 gates'],[{internal_ok},{ci_state['passed']},{community_ok},{all3}],'#0b3d91',false,'gates');
BAR('gates_pending',['Internal review','CI','Community review','Any gate'],[{pend_int},{pend_ci},{pend_com},{pend_any}],'#ea4335',false,'gates_pending');
PIE('ci',{js(list(ci_state.keys()))},{js(list(ci_state.values()))},['#34a853','#ea4335','#fbbc04','#9aa0a6'],'ci');
new Chart(document.getElementById('timing'),{{type:'bar',data:{{labels:{js([tlabels[k] for k in timing])},
 datasets:[
 {{label:'median',data:{js([timing[k]['median_d'] if timing[k] else 0 for k in timing])},backgroundColor:'#1a73e8'}},
 {{label:'mean',data:{js([timing[k]['mean_d'] if timing[k] else 0 for k in timing])},backgroundColor:'#34a853'}},
 {{label:'p90',data:{js([timing[k]['p90_d'] if timing[k] else 0 for k in timing])},backgroundColor:'#fbbc04'}},
 {{label:'max',data:{js([timing[k]['max_d'] if timing[k] else 0 for k in timing])},backgroundColor:'#ea4335'}}
 ]}},options:{{...BASE,...clickCfg('timing'),scales:{{y:{{title:{{display:true,text:'days'}}}}}}}}}});
BAR('hint',{js(buckets)},{js(int_d)},'#1a73e8',false,'hint');
BAR('hci',{js(buckets)},{js(ci_d)},'#ea4335',false,'hci');
BAR('hcom',{js(buckets)},{js(com_d)},'#34a853',false,'hcom');
BAR('hmg',{js(buckets)},{js(mg_d)},'#a142f4',false,'hmg');
const showForecast=()=>showDetail('forecast',{js(FKEY)});
const fcClick={{onClick:(e,els)=>{{ if(els.length) showForecast(); }}}};
new Chart(document.getElementById('burn'),{{type:'line',data:{{datasets:[
 {{label:'PR created ({rc0:.2f}/wk)',data:{js(burn_new)},borderColor:'#00acc1',backgroundColor:'#00acc1',tension:.1,pointRadius:0}},
 {{label:'Internal review ({rc1:.2f}/wk)',data:{js(burn_int)},borderColor:'#a142f4',backgroundColor:'#a142f4',tension:.1,pointRadius:0}},
 {{label:'CI pass ({rc2:.2f}/wk)',data:{js(burn_ci)},borderColor:'#fbbc04',backgroundColor:'#fbbc04',tension:.1,pointRadius:0}},
 {{label:'Merge / all pass ({rc3:.2f}/wk)',data:{js(burn_mrg)},borderColor:'#137333',backgroundColor:'#137333',tension:.1,pointRadius:0}}
 ]}},options:{{...BASE,...fcClick,plugins:{{datalabels:{{display:false}}}},scales:{{x:{{type:'linear',title:{{display:true,text:'weeks from now'}}}},y:{{beginAtZero:true,title:{{display:true,text:'PR units remaining'}}}}}}}}}});
new Chart(document.getElementById('pred'),{{type:'bar',data:{{labels:{js(pred_labels)},datasets:[
 {{label:'median',data:{js(pred_median)},backgroundColor:'#1a73e8'}},
 {{label:'mean',data:{js(pred_mean)},backgroundColor:'#34a853'}},
 {{label:'p90',data:{js(pred_p90)},backgroundColor:'#fbbc04'}}
 ]}},options:{{...BASE,...fcClick,scales:{{y:{{beginAtZero:true,title:{{display:true,text:'days'}}}}}}}}}});
</script></body></html>"""
open('report.html','w').write(html)
print('wrote report.html', len(html),'bytes')
print('median days:',{tlabels[k]:(timing[k]['median_d'] if timing[k] else None) for k in timing})
