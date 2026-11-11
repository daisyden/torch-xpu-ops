#!/usr/bin/env python3
"""List the cases a reviewer sent back, with the instruction to act on.

  rework_queue.py [--file inductor/test_fp8.py] [--json] [--no-notes]

Open patch-review comments (made in test_pr_reviewer on the file's diff, see
patch_reviewer.py) are queued as one "(patch)" item per file, with file/line/unit
and the comment text. A case is queued when it is not Confirmed and any of:
  - its verdict is "Needs work" or "Rejected"
  - it has a per-case comment
  - its file has a non-empty common prompt (file_prompt)

Each item carries `instruction` = common prompt + per-case comment (the per-case
comment wins on conflict), the agent branch to check out, the latest result and
the PR bundle state (a merged PR means the rework needs a follow-up PR).

Every run also appends new items to references/review_notes.md (idempotent),
so reviewer guidance survives across runs. --no-notes disables that.
"""
import argparse
import json
import os
import sys
import time

import patch_reviewer
import store
from common import REFS_DIR, slug as make_slug

NOTES = os.environ.get("XPU_ENABLE_NOTES", os.path.join(REFS_DIR, "review_notes.md"))
FLAG = {"Needs work", "Rejected"}


def queue(files=None) -> list[dict]:
    slugs = [make_slug(x) for x in files] if files else store.list_slugs()
    bundles = {b["id"]: b for b in store.list_bundles()}
    out = []
    for s in slugs:
        f = store.load_file(s)
        rv = store.load_reviews(s)
        prompt = (f.get("file_prompt") or "").strip()
        results = store.load_results(s)
        b = bundles.get(f.get("bundle") or "")
        patch_cmts = patch_reviewer.open_comments(s)
        if patch_cmts:
            out.append({"file": f["test_file"], "slug": s, "case_id": "(patch)",
                        "test": "patch review", "portability": "", "verdict": "Needs work",
                        "comment": "", "file_prompt": prompt,
                        "patch_comments": patch_cmts,
                        "instruction": "\n".join(
                            ([f"[common prompt] {prompt}"] if prompt else []) +
                            [f"[patch comment {c['id']}] {c['path']} {c['side']}:{c['line']}"
                             f"{' (' + c['unit'] + ')' if c.get('unit') else ''}: {c['text']}"
                             for c in patch_cmts]),
                        "agent_branch": f.get("agent_branch", ""), "last_result": None,
                        "pr": {"bundle": b["id"], "status": b.get("status"),
                               "number": (b.get("pr") or {}).get("number")} if b else None})
        for cid, c in store.load_cases(s).items():
            verdict = rv["verdicts"].get(cid, "")
            comment = (rv["comments"].get(cid) or "").strip()
            if verdict == "Confirmed":
                continue
            if not (verdict in FLAG or comment or prompt):
                continue
            parts = []
            if prompt:
                parts.append(f"[common prompt] {prompt}")
            if comment:
                parts.append(f"[case comment, wins on conflict] {comment}")
            last = store.latest([e for e in results.get(cid, []) if e.get("run") != "precheck"])
            out.append({"file": f["test_file"], "slug": s, "case_id": cid,
                        "test": f"{c.get('class_name', '')}::{c.get('test_name', '')}",
                        "portability": c.get("portability", ""), "verdict": verdict,
                        "comment": comment, "file_prompt": prompt,
                        "instruction": "\n".join(parts) or f"(verdict {verdict}, no comment)",
                        "agent_branch": f.get("agent_branch", ""),
                        "last_result": {k: last.get(k) for k in ("run", "status", "reason")} if last else None,
                        "pr": {"bundle": b["id"], "status": b.get("status"),
                               "number": (b.get("pr") or {}).get("number")} if b else None})
    return out


def save_notes(items: list[dict], path: str = NOTES) -> int:
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else "# Review notes\n\n## Log\n"
    added = []
    stamp = time.strftime("%Y-%m-%d %H:%M")

    def add(key: str, lines: list[str]) -> None:
        nonlocal text
        key = f"<!-- key: {key} -->"
        if key in text:
            return
        added.append("\n".join(lines + [f"  - {key}"]))
        text += "\n" + added[-1]

    for it in items:
        if it["file_prompt"]:
            add(f"{it['slug']}|prompt|{it['file_prompt']}",
                [f"- [{stamp}] common prompt for `{it['file']}`", f"  - prompt: {it['file_prompt']}"])
        for pc in it.get("patch_comments", []):
            add(f"{it['slug']}|patch|{pc['id']}",
                [f"- [{stamp}] patch comment on `{it['file']}` {pc['side']}:{pc['line']} {pc.get('unit', '')}",
                 f"  - comment: {pc['text']}"])
        if it["comment"]:
            add(f"{it['slug']}|{it['case_id']}|{it['comment']}",
                [f"- [{stamp}] [{it['verdict'] or 'unreviewed'}] `{it['test']}` "
                 f"(file `{it['file']}`, case {it['case_id']}, now: {it['portability'] or '-'})",
                 f"  - comment: {it['comment']}"])
    if added:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n")
    return len(added)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", action="append")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-notes", action="store_true")
    a = ap.parse_args()
    items = queue(a.file)
    n = 0 if a.no_notes else save_notes(items)
    if a.json:
        json.dump(items, sys.stdout, indent=2)
        print()
        return
    cur = None
    for it in items:
        if it["file"] != cur:
            cur = it["file"]
            print(f"\n== {cur}  (branch {it['agent_branch'] or '-'})")
            if it["file_prompt"]:
                print(f"   common prompt (applies to every case below): {it['file_prompt']}")
            if it["pr"] and it["pr"]["status"] == "merged":
                print(f"   WARN: PR #{it['pr']['number']} already merged; rework needs a follow-up PR")
        print(f"  [{it['case_id']}] {it['test']}  verdict={it['verdict'] or '-'} portability={it['portability'] or '-'}"
              f" last={it['last_result']['status'] if it['last_result'] else '-'}")
        if it["comment"]:
            print(f"      comment: {it['comment']}")
        for pc in it.get("patch_comments", []):
            print(f"      patch comment [{pc['id']}] {pc['path']} {pc['side']}:{pc['line']}"
                  f"{' ' + pc['unit'] if pc.get('unit') else ''}\n          {pc['text']}")
    print(f"\n{len(items)} cases queued; {n} new entries appended to {NOTES}")


if __name__ == "__main__":
    main()
