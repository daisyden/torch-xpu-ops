#!/usr/bin/env python3
"""Export the reviewed state as a new CSV (the input CSV is never modified) or JUnit XML.

  export.py [--csv NEW.csv] [--out PATH]      -> <input_stem>_reviewed_<YYYYmmdd_HHMM>.csv
                                              (default CSV: the active session's)
  export.py --csv NEW.csv --junit out.xml [--file ...]

CSV: every input row, plus portability, portability_reason, issue_number, issue_url,
log, review_status, review_comment, agent_branch, patch, pr_bundle from the report tree.
For cases with a Fix PR (case fix_pr, set by "Set PR" or by recording an existing PR):
  fix_pr        = PR url (an existing, different input fix_pr is kept and reported)
For final cases -- the file's status is Imported, or the case review is Confirmed:
  reason        = "PR ready" for Portable with a Fix PR, else the portability value
  detail_reason = portability reason
  issue_number, issue_url = the case's issue (overwrites the input, also when cleared)
  issue_status  = that issue's current state from gh (open / closed, 1-day cache)
"""
import argparse
import csv
import os
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

import issues
import session
import store
from common import slug as make_slug

EXTRA = ["portability", "portability_reason", "issue_number", "issue_url", "log",
         "review_status", "review_comment", "agent_branch", "patch", "pr_bundle"]


def _state(root=None):
    root = root or store.report_root()
    by_slug, bundle_of = {}, {}
    for b in store.list_bundles(root):
        if b.get("status") not in ("submitted", "merged") or not (b.get("pr") or {}).get("url"):
            continue
        for f in b.get("files", []):
            if f.get("state") == "picked":
                bundle_of[f["slug"]] = b
    for s in store.list_slugs(root):
        by_slug[s] = {"file": store.load_file(s, root), "cases": store.load_cases(s, root),
                      "reviews": store.load_reviews(s, root), "results": store.load_results(s, root)}
    return by_slug, bundle_of


def export_csv(csv_path: str = "", out: str = "", root=None) -> dict:
    root = root or store.report_root()
    csv_path = session.require_csv(csv_path)
    rows = store.read_csv(csv_path)
    cols = list(rows[0].keys()) if rows else []
    for c in ["fix_pr", "reason", "detail_reason"] + EXTRA:
        if c not in cols:
            cols.append(c)
    by_slug, bundle_of = _state(root)
    conflicts, n_pr, n_case, n_final = [], 0, 0, 0
    for r in rows:
        s = make_slug(r.get("test_file", ""))
        st = by_slug.get(s)
        if not st:
            continue
        cid = str(r.get("case_id"))
        c = st["cases"].get(cid)
        if c is None:
            continue
        n_case += 1
        f = st["file"]
        last = store.latest([e for e in st["results"].get(cid, []) if e.get("run") != "precheck"])
        r.update({"portability": c.get("portability", ""), "portability_reason": c.get("reason", ""),
                  "issue_number": c.get("issue_number", "") or r.get("issue_number", ""),
                  "issue_url": c.get("issue_url", "") or r.get("issue_url", ""),
                  "log": os.path.join(root, s, last["log"]) if last else "",
                  "review_status": st["reviews"]["verdicts"].get(cid, ""),
                  "review_comment": st["reviews"]["comments"].get(cid, ""),
                  "agent_branch": f.get("agent_branch", ""),
                  "patch": store.patch_path(s, root) if f.get("patch_sha") else "",
                  "pr_bundle": f.get("bundle", "")})
        b = bundle_of.get(s)
        url = c.get("fix_pr") or ""
        fe = next((x for x in (b or {}).get("files", []) if x["slug"] == s), {})
        if b and not url and not fe.get("cases"):
            url = b["pr"]["url"]
        if url and c.get("portability"):
            old = (r.get("fix_pr") or "").strip()
            if old and old != url:
                conflicts.append({"case_id": cid, "input_fix_pr": old, "bundle_pr": url})
            else:
                r["fix_pr"] = url
            n_pr += 1
        final = f.get("status", "").startswith("Imported") or st["reviews"]["verdicts"].get(cid) == "Confirmed"
        if final and c.get("portability"):
            p = c["portability"]
            r["reason"] = "PR ready" if p == "Portable" and url else p
            r["detail_reason"] = c.get("reason", "")
            num = c.get("issue_number", "")
            r["issue_number"], r["issue_url"] = num, c.get("issue_url", "")
            r["issue_status"] = (issues.meta(num).get("state") or "").lower() if str(num).isdigit() else ""
            n_final += 1
    if not out:
        stem, _ = os.path.splitext(csv_path)
        out = f"{stem}_reviewed_{time.strftime('%Y%m%d_%H%M')}.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return {"ok": True, "path": out, "rows": len(rows), "cases_with_data": n_case,
            "pr_rows": n_pr, "final_rows": n_final, "fix_pr_conflicts": conflicts}


def export_junit(out: str, files=None, root=None) -> int:
    root = root or store.report_root()
    slugs = [make_slug(x) for x in files] if files else store.list_slugs(root)
    parts, n = [], 0
    for s in slugs:
        f = store.load_file(s, root)
        cases = []
        for cid, c in store.load_cases(s, root).items():
            p = c.get("portability", "")
            props = "".join(f"<property name={quoteattr(k)} value={quoteattr(str(v))}/>"
                            for k, v in (("case_id", cid), ("portability", p),
                                         ("issue", c.get("issue_number", ""))))
            body = f"<properties>{props}</properties>"
            if p and p != "Portable":
                body += f"<skipped message={quoteattr(p + ': ' + c.get('reason', ''))}/>"
            cases.append(f"<testcase classname={quoteattr(c.get('class_name', ''))} "
                         f"name={quoteattr(c.get('test_name', ''))}>{body}</testcase>")
        n += len(cases)
        parts.append(f"<testsuite name={quoteattr(f['test_file'])} tests=\"{len(cases)}\">"
                     + "".join(cases) + "</testsuite>")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="utf-8"?>\n<testsuites>' + "".join(parts) + "</testsuites>\n")
    ET.parse(out)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default="", help="default: the active session's CSV")
    ap.add_argument("--out", default="")
    ap.add_argument("--junit")
    ap.add_argument("--file", action="append")
    a = ap.parse_args()
    if a.junit:
        print(f"wrote {export_junit(a.junit, a.file)} testcases to {a.junit}")
        return
    res = export_csv(a.csv or "", a.out)
    print(res)


if __name__ == "__main__":
    main()
