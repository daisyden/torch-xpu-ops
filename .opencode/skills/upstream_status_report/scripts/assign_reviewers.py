#!/usr/bin/env python3
"""Assign internal reviewers to open PRs that don't have one yet.

Valid internal reviewers (7):
    guangyey, etaf, CuiYifeng, liangan1, newtdms, astachowiczhabana, pbielak

Expertise preference (a *preference*, not a hard rule -- load balancing can
override it when the domain expert is overloaded):
    guangyey -> runtime      etaf -> inductor     CuiYifeng -> ops
    liangan1 -> sdpa         newtdms -> distributed
    astachowiczhabana, pbielak -> test refactor / others

CuiYifeng and newtdms are NOT formal collaborators, so they cannot be added as a
formal requested reviewer; for them the assignment is delivered as an @mention
comment (the text is included in the output file).

"Already has a reviewer" (skipped) = an internal reviewer is currently REQUESTED
or has ALREADY REVIEWED the PR.

Modes:
    python3 assign_reviewers.py                 # DRY-RUN: print plan, write nothing
    python3 assign_reviewers.py --apply         # write /tmp/reviewer_assignments.{json,csv}
Options:
    --penalty N     expertise preference strength (default 2; higher = stricter
                    domain match, lower = purer load balancing)
    --skip-drafts   do not assign reviewers to draft PRs
    --include-approved   also (re)assign open PRs that already have a reviewer
"""
import json, os, sys, csv
from collections import Counter, defaultdict

INTERNAL = ['guangyey','etaf','CuiYifeng','liangan1','newtdms',
            'astachowiczhabana','pbielak']
INFORMAL = {'CuiYifeng','newtdms'}          # deliver via @mention comment
EXPERTISE = {'guangyey':'runtime','etaf':'inductor','CuiYifeng':'ops',
             'liangan1':'sdpa','newtdms':'distributed',
             'astachowiczhabana':'others','pbielak':'others'}
DOMAIN_REVIEWERS = defaultdict(list)
for _r,_d in EXPERTISE.items(): DOMAIN_REVIEWERS[_d].append(_r)

# ---- domain classification from test-file paths --------------------------------
# precedence: strongest signal first
def path_domain(p):
    p=p.lower()
    if any(k in p for k in ('sdpa','attention','flash_attention','flex_attention')):
        return 'sdpa'
    if 'distributed/' in p or p.startswith('distributed') or '/fsdp' in p or 'test_c10d' in p:
        return 'distributed'
    if 'inductor' in p or 'dynamo' in p or 'torchinductor' in p or '/fx/' in p:
        return 'inductor'
    if any(k in p for k in ('test_cuda','test_xpu','stream','event','allocator',
                            'test_memory','pin_memory','device','test_multiprocessing',
                            'runtime')):
        return 'runtime'
    if any(k in p for k in ('test_ops','test_torch','test_binary_ufuncs',
                            'test_unary_ufuncs','test_reductions','test_linalg',
                            'test_matmul','test_sparse','test_tensor','test_indexing',
                            'test_scatter','test_foreach','test_nn','test_ops_',
                            'test_segment','test_type_promotion','test_complex')):
        return 'ops'
    return 'others'

def pr_domain(paths, distributed):
    if not paths:
        return 'distributed' if distributed else 'others'
    c=Counter(path_domain(p) for p in paths)
    if distributed: c['distributed']+=1   # xlsx distributed flag reinforces
    # precedence tie-break
    order=['sdpa','distributed','inductor','runtime','ops','others']
    best=max(c.values())
    for d in order:
        if c.get(d,0)==best: return d
    return 'others'

def main():
    apply='--apply' in sys.argv
    skip_drafts='--skip-drafts' in sys.argv
    include_approved='--include-approved' in sys.argv
    penalty=2.0
    if '--penalty' in sys.argv:
        penalty=float(sys.argv[sys.argv.index('--penalty')+1])

    owned=json.load(open('/tmp/owned.json'))
    rows=owned['rows']
    recs={int(r['pr']):r for r in json.load(open('/tmp/pr_analysis.json'))}

    # PR -> set(file paths)
    pr_paths=defaultdict(set)
    for r in rows:
        for n in (r.get('prs') or [])+(r.get('comm_prs') or []):
            if r.get('path'): pr_paths[int(n)].add(r['path'])

    # existing load = pending open PRs per reviewer: requested or reviewing but
    # NOT yet approved by them (an already-approved PR is not pending work).
    load=Counter()
    for n,rec in recs.items():
        if rec['state']!='OPEN': continue
        approved=set(rec.get('internal_approved_by',[]))
        pending=(set(rec.get('internal_requested',[]))|set(rec.get('internal_reviewed_by',[])))-approved
        for who in pending:
            if who in EXPERTISE: load[who]+=1

    # PRs needing assignment
    todo=[]
    for n,rec in sorted(recs.items()):
        if rec['state']!='OPEN': continue
        if skip_drafts and rec.get('is_draft'): continue
        has=set(rec.get('internal_requested',[]))|set(rec.get('internal_reviewed_by',[]))
        if has and not include_approved:
            continue
        todo.append(n)

    assignments=[]
    for n in todo:
        rec=recs[n]
        dom=pr_domain(pr_paths.get(n,set()), rec.get('distributed',False))
        experts=set(DOMAIN_REVIEWERS[dom])
        # cost = current load + penalty if not a domain expert; pick min
        def cost(r): return load[r]+(0.0 if r in experts else penalty)
        pick=min(INTERNAL, key=lambda r:(cost(r), load[r], r not in experts, r))
        load[pick]+=1
        method='comment' if pick in INFORMAL else 'request'
        comment=(f"@{pick} could you please help review this internal port PR? Thanks!"
                 if method=='comment' else '')
        assignments.append({
            'pr':n,'url':f'https://github.com/pytorch/pytorch/pull/{n}',
            'title':rec.get('title'),'domain':dom,
            'assignee':pick,'method':method,'comment':comment,
            'is_draft':rec.get('is_draft',False),
            'files':sorted(pr_paths.get(n,set()))[:6],
        })

    # ---- report ----
    print(f"internal reviewer assignment  ({'APPLY' if apply else 'DRY-RUN'}, penalty={penalty})")
    print(f"open PRs: {sum(1 for r in recs.values() if r['state']=='OPEN')}  "
          f"needing assignment: {len(todo)}\n")
    for a in assignments:
        d=' [draft]' if a['is_draft'] else ''
        print(f"  PR {a['pr']:<7} {a['domain']:<11} -> {a['assignee']:<18} ({a['method']}){d}  {a['title'][:50]}")
    print("\nresulting open-PR load per reviewer (existing + new):")
    for r in INTERNAL:
        print(f"  {r:<18} {load[r]}  [{EXPERTISE[r]}]")

    if apply:
        json.dump(assignments, open('/tmp/reviewer_assignments.json','w'), indent=1)
        with open('/tmp/reviewer_assignments.csv','w',newline='') as f:
            w=csv.writer(f); w.writerow(['pr','url','domain','assignee','method','comment','is_draft','title'])
            for a in assignments:
                w.writerow([a['pr'],a['url'],a['domain'],a['assignee'],a['method'],
                            a['comment'],a['is_draft'],a['title']])
        print("\nwrote /tmp/reviewer_assignments.json and /tmp/reviewer_assignments.csv")
    else:
        print("\n(dry-run: no files written; re-run with --apply to save assignments)")

if __name__=='__main__':
    main()
