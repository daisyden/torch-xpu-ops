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

"Already has a reviewer" (skipped) = an internal reviewer has ALREADY REVIEWED
the PR. A stalled reviewer *request* that nobody acted on still needs assignment.

Scope: only PRs authored by an Excel assignee (recorded in a file's Intel-PR
column of a row that has an assignee, plus known assignee logins). Pass
--all-open to consider every open PR instead.

Modes:
    python3 assign_reviewers.py                 # DRY-RUN: print plan, write nothing
    python3 assign_reviewers.py --apply         # write /tmp/reviewer_assignments.{json,csv}
    python3 assign_reviewers.py --github-apply   # LIVE: request reviewers / post @mention
                                                 # comments on GitHub (idempotent; prompts
                                                 # for confirmation unless --yes is given)
Options:
    --yes, -y       skip the interactive confirmation prompt (for --github-apply)
    --penalty N     expertise preference strength (default 2; higher = stricter
                    domain match, lower = purer load balancing)
    --skip-drafts   do not assign reviewers to draft PRs
    --all-open      consider all open PRs, not just Excel-assignee PRs
    --include-approved   also (re)assign open PRs already reviewed internally

--github-apply is idempotent: a formal request is skipped when the reviewer is
already requested; an @mention comment is skipped when a prior auto-request
comment (tagged with a hidden marker) for that reviewer already exists.
Collaborators get a formal reviewer request; non-collaborators (CuiYifeng,
newtdms) get an @mention comment instead.
"""
import json, os, sys, csv, subprocess
from collections import Counter, defaultdict

REPO='pytorch/pytorch'
CACHE=os.path.join(os.path.dirname(os.path.abspath(__file__)),'pr_cache')
# hidden marker so we never post a duplicate @mention review request
MARKER='<!-- xpu-auto-review-request -->'

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

def _raw(n):
    p=os.path.join(CACHE,f'{n}.json')
    return json.load(open(p)) if os.path.exists(p) else {}

def already_requested(n, login):
    """True if login is currently a requested reviewer on the PR (live check)."""
    try:
        out=subprocess.run(['gh','api',f'repos/{REPO}/pulls/{n}/requested_reviewers'],
                           capture_output=True,text=True,timeout=30)
        if out.returncode==0:
            d=json.loads(out.stdout)
            return any((u.get('login')==login) for u in d.get('users',[]))
    except Exception:
        pass
    # fall back to cached reviewRequests
    return any((x.get('login') or x.get('name'))==login
               for x in _raw(n).get('reviewRequests',[]))

def already_commented(n, login):
    """True if an auto review-request comment for login already exists."""
    for cm in _raw(n).get('comments',[]):
        b=cm.get('body') or ''
        if MARKER in b and f'@{login}' in b:
            return True
    # live re-check (cache may be stale)
    try:
        out=subprocess.run(['gh','api',f'repos/{REPO}/issues/{n}/comments','--paginate'],
                           capture_output=True,text=True,timeout=30)
        if out.returncode==0:
            for cm in json.loads(out.stdout):
                b=cm.get('body') or ''
                if MARKER in b and f'@{login}' in b:
                    return True
    except Exception:
        pass
    return False

def gh_request_reviewer(n, login):
    r=subprocess.run(['gh','api','--method','POST',
                      f'repos/{REPO}/pulls/{n}/requested_reviewers',
                      '-f',f'reviewers[]={login}'],capture_output=True,text=True,timeout=30)
    return r.returncode==0, (r.stderr.strip() or r.stdout.strip())

def gh_post_comment(n, body):
    r=subprocess.run(['gh','pr','comment',str(n),'--repo',REPO,'--body',body],
                     capture_output=True,text=True,timeout=30)
    return r.returncode==0, (r.stderr.strip() or r.stdout.strip())

def main():
    apply='--apply' in sys.argv
    github_apply='--github-apply' in sys.argv
    skip_drafts='--skip-drafts' in sys.argv
    include_approved='--include-approved' in sys.argv
    penalty=2.0
    if '--penalty' in sys.argv:
        penalty=float(sys.argv[sys.argv.index('--penalty')+1])

    all_open='--all-open' in sys.argv

    owned=json.load(open('/tmp/owned.json'))
    rows=owned['rows']
    recs={int(r['pr']):r for r in json.load(open('/tmp/pr_analysis.json'))}

    import re
    def _nums(v):
        out=set()
        for x in (v or []):
            m=re.search(r'(\d{5,7})', str(x))
            if m: out.add(int(m.group(1)))
        return out

    # PR -> set(file paths)
    pr_paths=defaultdict(set)
    for r in rows:
        for n in _nums(r.get('prs'))|_nums(r.get('comm_prs')):
            if r.get('path'): pr_paths[n].add(r['path'])

    # PRs created by an Excel assignee = recorded in a file's Intel-PR column
    # (col F -> row['prs']) whose row has an assignee set. Community PRs
    # (comm_prs) are NOT authored by our assignees, so they are excluded.
    pr_assignee=defaultdict(set)
    for r in rows:
        a=r.get('assignee')
        if not a: continue
        for n in _nums(r.get('prs')):
            pr_assignee[n].add(a)

    # GitHub logins of our Excel assignees, derived from the authors of the
    # assignee-tied PRs, plus explicit additions.
    ASSIGNEE_AUTHORS={'madhumitha0102'}
    for n in pr_assignee:
        a=(recs.get(n) or {}).get('author')
        if a: ASSIGNEE_AUTHORS.add(a)

    # existing load = pending open PRs per reviewer: requested or reviewing but
    # NOT yet approved by them (an already-approved PR is not pending work).
    load=Counter()
    for n,rec in recs.items():
        if rec['state']!='OPEN': continue
        approved=set(rec.get('internal_approved_by',[]))
        pending=(set(rec.get('internal_requested',[]))|set(rec.get('internal_reviewed_by',[])))-approved
        for who in pending:
            if who in EXPERTISE: load[who]+=1

    # PRs needing assignment. Scope: PRs authored by an Excel assignee (unless
    # --all-open). A PR needs (re)assignment when no internal reviewer has
    # actually ENGAGED yet (reviewed) -- a stalled request that nobody acted on
    # still counts as needing a reviewer.
    todo=[]
    for n,rec in sorted(recs.items()):
        if rec['state']!='OPEN': continue
        if not all_open and n not in pr_assignee and rec.get('author') not in ASSIGNEE_AUTHORS:
            continue
        if skip_drafts and rec.get('is_draft'): continue
        reviewed=set(rec.get('internal_reviewed_by',[]))
        if reviewed and not include_approved:
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
        comment=(f"{MARKER}\n@{pick} could you please help review this internal "
                 f"test port / refactor PR? Thanks!" if method=='comment' else '')
        assignments.append({
            'pr':n,'url':f'https://github.com/pytorch/pytorch/pull/{n}',
            'title':rec.get('title'),'domain':dom,
            'assignee':pick,'method':method,'comment':comment,
            'is_draft':rec.get('is_draft',False),
            'author':rec.get('author'),
            'excel_assignee':','.join(sorted(pr_assignee.get(n,{'?'}))),
            'files':sorted(pr_paths.get(n,set()))[:6],
        })

    # ---- report ----
    print(f"internal reviewer assignment  "
          f"({'GITHUB-APPLY' if github_apply else 'APPLY' if apply else 'DRY-RUN'}, "
          f"penalty={penalty})")
    print(f"open PRs: {sum(1 for r in recs.values() if r['state']=='OPEN')}  "
          f"needing assignment: {len(todo)}\n")
    for a in assignments:
        d=' [draft]' if a['is_draft'] else ''
        print(f"  PR {a['pr']:<7} {a['domain']:<11} -> {a['assignee']:<18} ({a['method']})"
              f"  excel:{a['excel_assignee']:<10}{d}  {a['title'][:42]}")
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

    if github_apply:
        print(f"\n== about to post {len(assignments)} reviewer request(s)/comment(s) "
              f"to GitHub ({REPO}) ==")
        assume_yes='--yes' in sys.argv or '-y' in sys.argv
        if not assume_yes:
            try:
                ans=input("Proceed with LIVE GitHub updates? type 'yes' to confirm: ").strip().lower()
            except EOFError:
                ans=''
            if ans!='yes':
                print("aborted -- no GitHub changes made "
                      "(re-run with --yes to skip this prompt).")
                return
        print("== posting reviewer requests to GitHub (idempotent) ==")
        done=skipped=failed=0
        for a in assignments:
            n,who,method=a['pr'],a['assignee'],a['method']
            if method=='request':
                if already_requested(n,who):
                    print(f"  PR {n}: {who} already requested -- skip"); skipped+=1; continue
                ok,msg=gh_request_reviewer(n,who)
            else:  # informal @mention comment
                if already_commented(n,who):
                    print(f"  PR {n}: {who} already @mentioned -- skip"); skipped+=1; continue
                ok,msg=gh_post_comment(n,a['comment'])
            if ok:
                print(f"  PR {n}: {method} -> {who}  OK"); done+=1
            else:
                print(f"  PR {n}: {method} -> {who}  FAILED: {msg[:120]}"); failed+=1
        print(f"\napplied: {done}  skipped(existing): {skipped}  failed: {failed}")

if __name__=='__main__':
    main()
