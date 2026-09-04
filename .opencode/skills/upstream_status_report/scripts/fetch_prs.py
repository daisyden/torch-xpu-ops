#!/usr/bin/env python3
"""Fetch pytorch/pytorch PR JSON into pr_cache/ for every PR referenced in
/tmp/owned.json (produced by extract_owned.py).

By default only fetches PRs not already cached. Use --refresh to re-fetch all
(picks up realtime state changes: merges, new reviews, CI results).

Usage:
    python3 fetch_prs.py            # fetch only missing PRs
    python3 fetch_prs.py --refresh  # re-fetch every referenced PR
"""
import json, os, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'pr_cache')
FIELDS = ('number,title,state,createdAt,mergedAt,closedAt,labels,author,'
          'reviews,comments,reviewRequests,statusCheckRollup,isDraft')

def fetch(n):
    try:
        out = subprocess.run(
            ['gh', 'pr', 'view', str(n), '--repo', 'pytorch/pytorch',
             '--json', FIELDS],
            capture_output=True, text=True, check=True).stdout
        json.dump(json.loads(out), open(os.path.join(CACHE, f'{n}.json'), 'w'))
        return True
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or '').strip().splitlines()
        print(f'  ! PR {n}: gh failed ({tail[-1] if tail else "?"})')
        return False

def main():
    refresh = '--refresh' in sys.argv
    os.makedirs(CACHE, exist_ok=True)
    prs = json.load(open('/tmp/owned.json'))['prs']
    todo = prs if refresh else [n for n in prs
                                if not os.path.exists(os.path.join(CACHE, f'{n}.json'))]
    print(f'{len(prs)} referenced PRs; {"refreshing all" if refresh else "fetching missing"}: {len(todo)}')
    ok = 0
    for i, n in enumerate(todo, 1):
        if fetch(n):
            ok += 1
        print(f'[{i}/{len(todo)}] PR {n}')
    print(f'done: {ok}/{len(todo)} fetched, cache={len(os.listdir(CACHE))} files')

if __name__ == '__main__':
    main()
