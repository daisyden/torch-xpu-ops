#!/usr/bin/env python3
"""Update intel/torch-xpu-ops issue #5205 with markdown file-list tables for
the Done and Not Applicable test files (source: report "Status - All teams").

Replaces the old Done.csv / Not_Applicable.csv attachments with inline tables.
Idempotent: the generated block lives between HTML markers, so re-running just
refreshes the tables and leaves the rest of the issue body untouched.

Input : /tmp/status_all.json   (written by gen_report.py)
Usage : python3 update_issue.py            # dry-run: print new body to stdout
        python3 update_issue.py --apply     # push the update to GitHub
"""
import json, subprocess, sys, datetime

REPO='intel/torch-xpu-ops'
ISSUE='5205'
BEG='<!-- auto-file-lists:begin -->'
END='<!-- auto-file-lists:end -->'

def load():
    return json.load(open('/tmp/status_all.json'))

def table(title, items):
    # only the file list is needed
    paths=sorted({(it.get('sub') or it.get('k') or '') for it in items})
    lines=[f'| # | Test file |', '|---:|---|']
    lines+= [f'| {i+1} | `{p}` |' for i,p in enumerate(paths)]
    body='\n'.join(lines)
    return (f'<details>\n<summary><b>{title}</b> ({len(paths)} files)</summary>\n\n'
            f'{body}\n\n</details>')

def block(data):
    ts=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    done=table('Done test files &mdash; compare with CUDA one by one', data.get('Done',[]))
    na  =table('Not Applicable test files &mdash; can be skipped', data.get('Not Applicable',[]))
    return (f'{BEG}\n'
            f'_File lists auto-generated from the status report &bull; last updated {ts}._\n\n'
            f'{done}\n\n{na}\n'
            f'{END}')

INTRO=('### \U0001F680 The feature, motivation and pitch\n\n'
       'This is a test plan for UT upstream:\n'
       '1. For files in the **Done** list below, we can compare the test case with the CUDA one by one.\n'
       '2. For files in the **Not Applicable** list below, we can skip them.\n')
TAIL=('\n### Alternatives\n\n_No response_\n\n'
      '### Additional context\n\n_No response_\n')

def splice(old, new_block):
    if BEG in old and END in old:
        pre=old.split(BEG)[0]
        post=old.split(END,1)[1]
        return pre.rstrip()+'\n\n'+new_block+post
    # first run: rebuild body from template (drops the old *.csv attachment links)
    return INTRO+'\n'+new_block+'\n'+TAIL

def main():
    apply='--apply' in sys.argv
    data=load()
    cur=subprocess.run(['gh','issue','view',ISSUE,'--repo',REPO,'--json','body',
                        '--jq','.body'],capture_output=True,text=True,check=True).stdout
    new=splice(cur, block(data))
    if new==cur:
        print('issue #%s already up to date'%ISSUE); return
    if not apply:
        print(new)
        print('\n--- dry-run (use --apply to push) ---',file=sys.stderr)
        return
    subprocess.run(['gh','issue','edit',ISSUE,'--repo',REPO,'--body-file','-'],
                   input=new,text=True,check=True)
    print('updated issue #%s (Done=%d, Not Applicable=%d)'%(
        ISSUE,len(data.get('Done',[])),len(data.get('Not Applicable',[]))))

if __name__=='__main__':
    main()
