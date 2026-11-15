#!/usr/bin/env python3
"""Write community test-refactor tracker info into the xlsx for To Do files.

Adds three columns on the 'Test Files' sheet:
    T  Refactor PR      (PR links from the tracker: ready + merged)
    U  Refactor Owner   (assignee from the tracker)
    V  Refactor Status  (🔵 Todo / 🟡 In Progress / 🟢 Done)

Only owned rows (team col L set) whose Status (col Q) is not Done get filled,
matching the report's "To Do" (not-yet-Done) definition. Backs up the xlsx
before saving.

Run fetch_refactor_tracker.py first so /tmp/refactor_tracker.json exists.

Usage:
    python3 write_refactor_cols.py            # dry-run
    python3 write_refactor_cols.py --apply     # write columns (backs up xlsx)
"""
import json, os, sys, shutil, datetime, openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
XLSX = os.path.join(HERE, 'test_files_by_category_20260723.xlsx')
TRACKER = '/tmp/refactor_tracker.json'

# column map (1-indexed) on 'Test Files' sheet
C_PATH = 3; C_TEAM = 12; C_STATUS = 17
C_RPR = 20; C_ROWNER = 21; C_RSTATUS = 22   # T / U / V


def is_done(v):
    return str(v).strip().lower() in ('done', 'merged') if v is not None else False


def pr_text(t):
    prs = (t.get('ready_prs') or []) + (t.get('merged_prs') or [])
    return '\n'.join(url for _, url in prs)


def main():
    apply = '--apply' in sys.argv
    if not os.path.exists(TRACKER):
        sys.exit('missing /tmp/refactor_tracker.json — run fetch_refactor_tracker.py first')
    tracker = json.load(open(TRACKER))

    wb = openpyxl.load_workbook(XLSX)
    wbv = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb['Test Files']; wsv = wbv['Test Files']

    changes = []
    for ri in range(2, wsv.max_row + 1):
        path = wsv.cell(ri, C_PATH).value
        team = wsv.cell(ri, C_TEAM).value
        if not path or team in (None, ''):
            continue
        if is_done(wsv.cell(ri, C_STATUS).value):   # only To Do files
            continue
        t = tracker.get(path)
        if not t:
            continue
        rpr = pr_text(t)
        rowner = t.get('owner') or ''
        rstatus = t.get('status') or ''
        if not (rpr or rowner or rstatus):
            continue
        ws.cell(ri, C_RPR).value = rpr
        ws.cell(ri, C_ROWNER).value = rowner
        ws.cell(ri, C_RSTATUS).value = rstatus
        changes.append((ri, os.path.basename(str(path)), rowner, rstatus, rpr.replace('\n', ' ')))

    # headers
    ws.cell(1, C_RPR).value = 'Refactor PR'
    ws.cell(1, C_ROWNER).value = 'Refactor Owner'
    ws.cell(1, C_RSTATUS).value = 'Refactor Status'

    print(f'To Do files matched in tracker: {len(changes)}')
    for ri, f, owner, stat, pr in changes[:30]:
        print(f'  row {ri}: {f:<45} owner={owner or "-":<14} {stat:<16} {pr}')
    if len(changes) > 30:
        print(f'  ... and {len(changes) - 30} more')

    if not apply:
        print('\n(dry-run) re-run with --apply to write columns T/U/V into the xlsx.')
        return

    bak = XLSX.replace('.xlsx', f'.prerefac_{datetime.datetime.now():%Y%m%d_%H%M%S}.bak.xlsx')
    shutil.copy(XLSX, bak)
    wb.save(XLSX)
    print(f'\nbackup: {os.path.basename(bak)}')
    print(f'saved:  {os.path.basename(XLSX)} (added cols T=Refactor PR, U=Refactor Owner, V=Refactor Status)')


if __name__ == '__main__':
    main()
