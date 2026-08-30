#!/usr/bin/env python3
"""Fetch the community "Test Class Refactoring Tracker" Google Sheet and dump a
path -> refactor-info map to /tmp/refactor_tracker.json.

The tracker records community test-refactor PRs. For each tracked file we keep
its refactor status, owner (assignee), and PR link(s). gen_report.py joins this
onto the To Do (not-yet-Done) files and shows it as extra columns in the detail
panel.

Public sheet, no auth needed.

Usage:
    python3 fetch_refactor_tracker.py
"""
import urllib.request, urllib.parse, csv, io, json, re, os

ID = '1cDNiLW4KvPcGYPlA3KCDm0zV5PLPUWubno1OyCznKBw'
TABS = ['Core', 'Tensor', 'Distributed', 'Graph', 'Math', 'Quantization', 'Utils']
# gviz select: C=File D=Status E=Priority F=Assignee G=PRs(ReadyForReview) H=PRs(Merged) I=Notes
Q = 'select C,D,E,F,G,H,I'

PULL_RE = re.compile(r'pytorch/pytorch/pull/(\d+)')
HASH_RE = re.compile(r'#(\d+)')


def parse_pr_links(cell):
    """Return list of (number, url) from a PR cell."""
    if not cell:
        return []
    out = []; seen = set()
    for m in PULL_RE.finditer(cell):
        n = m.group(1)
        if n not in seen:
            seen.add(n); out.append((n, f'https://github.com/pytorch/pytorch/pull/{n}'))
    if not out:
        for m in HASH_RE.finditer(cell):
            n = m.group(1)
            if n not in seen:
                seen.add(n); out.append((n, f'https://github.com/pytorch/pytorch/pull/{n}'))
    return out


def fetch_tab(tab):
    url = (f'https://docs.google.com/spreadsheets/d/{ID}/gviz/tq?tqx=out:csv'
           f'&sheet={urllib.parse.quote(tab)}&tq={urllib.parse.quote(Q)}')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    data = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'replace')
    return list(csv.reader(io.StringIO(data)))[1:]  # drop header row


def main():
    tracker = {}
    total = 0
    for tab in TABS:
        try:
            rows = fetch_tab(tab)
        except Exception as e:
            print(f'{tab}: ERROR {type(e).__name__}: {e}')
            continue
        n = 0
        for r in rows:
            r = (r + [''] * 7)[:7]
            path, status, priority, assignee, ready, merged, notes = [c.strip() for c in r]
            if not path:
                continue
            ready_prs = parse_pr_links(ready)
            merged_prs = parse_pr_links(merged)
            tracker[path] = {
                'tab': tab,
                'status': status,          # e.g. "🔵 Todo", "🟡 In Progress", "🟢 Done"
                'priority': priority,
                'owner': assignee,
                'ready_prs': ready_prs,    # list of [num, url]
                'merged_prs': merged_prs,
                'notes': notes,
            }
            n += 1
        total += n
        print(f'{tab}: {n} rows')
    out = '/tmp/refactor_tracker.json'
    json.dump(tracker, open(out, 'w'), indent=1)
    print(f'wrote {out}: {total} tracked files')


if __name__ == '__main__':
    main()
