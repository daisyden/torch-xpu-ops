#!/usr/bin/env python3
"""Generate an HTML review report for an XPU test-enablement batch.

Reads the batch CSV and an optional manifest.json describing, per test file,
the commit hash, patch file and test log. Emits a self-contained static report
tree under <out>/<batch>/:

  <out>/<batch>/
    index.html                      - batch landing page (list of test files)
    input_table.html                - full per-case input table + review status
    <test_file_dir>/
        result.html                 - per-file result page (summary + details)
        <patch copied here>
        <log copied here>

The HTML is fully static. Review verdicts are stored in the browser's
localStorage and can be exported to JSON ("Export review JSON"), then merged
back into the CSV with merge_review.py.

Usage:
  report_gen.py --csv BATCH.csv --batch NAME [--out xpu_enable_report]
                [--manifest manifest.json] [--files-with-manifest-only]

manifest.json (all fields optional except test_file):
  {
    "batch": "missing_eager",
    "files": [
      {"test_file": "test_shape_ops.py",
       "commit": "7d60338c8ac",
       "patch": "/abs/or/rel/path/test_shape_ops.py.patch",
       "log":   "/abs/or/rel/path/test_shape_ops.py.log"}
    ]
  }
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import shutil
from collections import Counter, defaultdict

# Columns pulled into the per-case detail view (in display order). Any column
# missing from the CSV is simply skipped.
DETAIL_COLUMNS = [
    "case_id", "class_name", "test_name", "xpu_status", "cuda_status",
    "last_status", "issue_number", "issue_status", "reason_category",
    "portability", "portability reason", "log",
]

PORTABILITY_ORDER = [
    "Portable", "Feature gap", "Known Issue", "Not Applicable", "", "TBD",
]

REVIEW_VALUES = ["Agree", "Disagree", "Needs info"]


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


_ISSUE_REF = re.compile(
    r"(https?://[^\s,]+)|(?:([\w.-]+/[\w.-]+)#|#)(\d+)")


def linkify_issues(v, default_repo: str = "intel/torch-xpu-ops") -> str:
    """Escape text; turn bare URLs and `repo#NNNN`/`#NNNN` refs into links."""
    text = "" if v is None else str(v)
    out, last = [], 0
    for m in _ISSUE_REF.finditer(text):
        out.append(_esc(text[last:m.start()]))
        if m.group(1):
            href = m.group(1).rstrip(".")
            out.append(f'<a href="{_esc(href)}" target="_blank" '
                       f'rel="noopener">{_esc(href)}</a>')
        else:
            repo = m.group(2) or default_repo
            num = m.group(3)
            href = f"https://github.com/{repo}/issues/{num}"
            label = f"{m.group(2)}#{num}" if m.group(2) else f"#{num}"
            out.append(f'<a href="{_esc(href)}" target="_blank" '
                       f'rel="noopener">{_esc(label)}</a>')
        last = m.end()
    out.append(_esc(text[last:]))
    return "".join(out)


def load_manifest(path: str | None) -> dict[str, dict]:
    if not path or not os.path.exists(path):
        return {}
    data = json.load(open(path))
    out = {}
    for f in data.get("files", []):
        tf = f.get("test_file")
        if tf:
            out[tf] = f
    return out


def read_csv(path: str):
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows


def _port_key(p: str) -> int:
    p = (p or "").strip()
    return PORTABILITY_ORDER.index(p) if p in PORTABILITY_ORDER else len(PORTABILITY_ORDER)


# ---------------------------------------------------------------------------
# Shared front-end (inlined into every page).
# ---------------------------------------------------------------------------
def review_assets(batch: str) -> str:
    return """
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px;
         color: #1b1f23; }
  h1, h2 { margin: 0.3em 0; }
  a { color: #0366d6; text-decoration: none; }
  a:hover { text-decoration: underline; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }
  th, td { border: 1px solid #d0d7de; padding: 6px 8px; text-align: left;
           vertical-align: top; }
  th { background: #f6f8fa; position: sticky; top: 0; }
  tr:nth-child(even) td { background: #fbfcfd; }
  code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  pre { background: #f6f8fa; padding: 12px; overflow: auto; border-radius: 6px;
        max-height: 420px; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 10px;
          font-size: 12px; font-weight: 600; }
  .Portable { background: #d6f5d6; color: #0a5c0a; }
  .Featuregap { background: #ffe9cc; color: #8a4b00; }
  .KnownIssue { background: #ffd6d6; color: #8a0a0a; }
  .NotApplicable { background: #e2e2e2; color: #444; }
  .TBD, .none { background: #eef; color: #335; }
  .rev-Agree { background: #d6f5d6; }
  .rev-Disagree { background: #ffd6d6; }
  .rev-Needsinfo { background: #fff3bf; }
  .statuspill { display: inline-block; padding: 2px 8px; border-radius: 10px;
                font-size: 12px; font-weight: 600; }
  .st-enabled { background: #d6f5d6; color: #0a5c0a; }
  .st-pending { background: #eef; color: #335; }
  td.filerev { font-weight: 600; text-align: center; white-space: nowrap; }
  td.filerev.rev-done { background: #d6f5d6; color: #0a5c0a; }
  td.filerev.rev-partial { background: #fff3bf; color: #8a4b00; }
  td.filerev.rev-none { color: #57606a; }
  .toolbar { margin: 12px 0; padding: 10px; background: #f6f8fa;
             border: 1px solid #d0d7de; border-radius: 6px; }
  .toolbar button, .reviewctl button { cursor: pointer; margin-right: 4px; }
  select, textarea { font-size: 12px; }
  textarea { width: 100%; min-height: 28px; }
  .count { color: #57606a; font-weight: normal; }
  .draft { border: 1px solid #d0d7de; border-radius: 6px; padding: 12px;
           margin: 12px 0; background: #fffdf5; }
  .draft label { display: block; margin: 6px 0; font-size: 12px;
                 font-weight: 600; }
  .draft input, .draft textarea { font-family: ui-monospace, monospace; }
  .d-status { margin-left: 8px; }
  /* test-case source: hover tooltip + click modal */
  .tncell { position: relative; }
  .tncell a.tn { cursor: pointer; border-bottom: 1px dotted #0366d6; }
  .srcpop { display: none; position: absolute; z-index: 50; left: 0; top: 1.4em;
            background: #fff; border: 1px solid #d0d7de; border-radius: 6px;
            box-shadow: 0 6px 24px rgba(0,0,0,.18); max-width: 70vw; }
  .srcpop pre { margin: 0; max-height: 50vh; max-width: 70vw; }
  .tncell:hover .srcpop { display: block; }
  #modal { display: none; position: fixed; inset: 0; z-index: 200;
           background: rgba(0,0,0,.45); }
  #modal .box { background: #fff; max-width: 90vw; max-height: 85vh;
                margin: 4vh auto; border-radius: 8px; overflow: auto;
                padding: 16px; }
  #modal .box pre { max-height: 70vh; }
  #modal h3 { margin: 0 0 8px; }
  #modal .x { float: right; cursor: pointer; font-size: 20px; color: #57606a; }
</style>
<div id="modal" onclick="if(event.target.id==='modal')closeModal()">
  <div class="box"><span class="x" onclick="closeModal()">&times;</span>
    <h3 id="modal-title"></h3><pre id="modal-body"></pre></div>
</div>
<script>
  function showModal(title, text){
    document.getElementById('modal-title').textContent = title || '';
    document.getElementById('modal-body').textContent = text || '';
    document.getElementById('modal').style.display = 'block';
  }
  function closeModal(){ document.getElementById('modal').style.display = 'none'; }
  document.addEventListener('keydown', function(e){
    if (e.key === 'Escape') closeModal(); });
  const BATCH = __BATCH__;
  const RKEY = (id) => "xpu_review::" + BATCH + "::" + id;
  function getReview(id){ try { return JSON.parse(localStorage.getItem(RKEY(id))) || {}; }
                          catch(e){ return {}; } }
  function setReview(id, status, note){
    const cur = getReview(id);
    if (status !== undefined) cur.status = status;
    if (note !== undefined) cur.note = note;
    cur.ts = new Date().toISOString();
    localStorage.setItem(RKEY(id), JSON.stringify(cur));
    paintRow(id);
  }
  function paintRow(id){
    document.querySelectorAll('[data-case="'+id+'"]').forEach(function(row){
      const r = getReview(id);
      const sel = row.querySelector('select.rev-status');
      if (sel) sel.value = r.status || "";
      const note = row.querySelector('textarea.rev-note');
      if (note && document.activeElement !== note) note.value = r.note || "";
      row.className = row.className.replace(/\\brev-\\S+/g, '').trim();
      if (r.status) row.classList.add('rev-' + r.status.replace(/\\s/g,''));
    });
  }
  function paintAll(){ document.querySelectorAll('[data-case]').forEach(function(row){
      paintRow(row.getAttribute('data-case')); }); updateProgress(); }
  function bulkSet(ids, status){ ids.forEach(function(id){ setReview(id, status, undefined); });
      updateProgress(); }
  function updateProgress(){
    const el = document.getElementById('progress'); if(!el) return;
    const rows = new Set(); document.querySelectorAll('[data-case]').forEach(function(r){
        rows.add(r.getAttribute('data-case')); });
    let done = 0; rows.forEach(function(id){ if ((getReview(id).status||"")) done++; });
    el.textContent = done + " / " + rows.size + " reviewed";
  }
  function exportJSON(){
    const out = {};
    const fixprs = {};
    const rpre = "xpu_review::" + BATCH + "::";
    const fpre = "xpu_fixpr::" + BATCH + "::";
    for (let i=0;i<localStorage.length;i++){
      const k = localStorage.key(i);
      if (k.startsWith(rpre)){ out[k.slice(rpre.length)] = JSON.parse(localStorage.getItem(k)); }
      else if (k.startsWith(fpre)){ fixprs[k.slice(fpre.length)] = localStorage.getItem(k); }
    }
    const blob = new Blob([JSON.stringify({batch: BATCH, reviews: out, fix_prs: fixprs}, null, 2)],
                          {type:"application/json"});
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    a.download = "review_" + BATCH + ".json"; a.click();
  }
  function importJSON(ev){
    const f = ev.target.files[0]; if(!f) return;
    const rd = new FileReader();
    rd.onload = function(){ const d = JSON.parse(rd.result);
      const revs = d.reviews || d;
      Object.keys(revs).forEach(function(id){
        localStorage.setItem(RKEY(id), JSON.stringify(revs[id])); });
      const fp = d.fix_prs || {};
      Object.keys(fp).forEach(function(tf){ localStorage.setItem(FKEY(tf), fp[tf]); });
      paintAll(); paintFixPRs(); };
    rd.readAsText(f);
  }
  const FKEY = (tf) => "xpu_fixpr::" + BATCH + "::" + tf;
  function getFixPR(tf){ return localStorage.getItem(FKEY(tf)) || ""; }
  function setFixPR(tf, val){ localStorage.setItem(FKEY(tf), val); }
  function paintFixPRs(){
    document.querySelectorAll('input.fixpr').forEach(function(inp){
      const tf = inp.getAttribute('data-file');
      if (document.activeElement !== inp) inp.value = getFixPR(tf);
    });
  }
  window.addEventListener('DOMContentLoaded', function(){ paintAll(); paintFixPRs(); });
  async function submitIssue(btn){
    const box = btn.closest('.draft');
    const status = box.querySelector('.d-status');
    const payload = {
      file: box.getAttribute('data-file'),
      idx: parseInt(box.getAttribute('data-idx'), 10),
      title: box.querySelector('.d-title').value,
      body: box.querySelector('.d-body').value,
      labels: box.querySelector('.d-labels').value.split(',')
                 .map(s => s.trim()).filter(Boolean),
    };
    if (location.protocol === 'file:') {
      const repo = 'intel/torch-xpu-ops';
      const url = 'https://github.com/' + repo + '/issues/new?title='
        + encodeURIComponent(payload.title)
        + '&body=' + encodeURIComponent(payload.body)
        + (payload.labels.length
             ? '&labels=' + encodeURIComponent(payload.labels.join(','))
             : '');
      window.open(url, '_blank');
      status.innerHTML = 'Opened GitHub new-issue page (review &amp; submit there). '
        + 'Then paste the issue number back to backfill patch/CSV.';
      return;
    }
    btn.disabled = true; status.textContent = 'Submitting...';
    try {
      const res = await fetch('/api/submit_issue', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)});
      const d = await res.json();
      if (!res.ok || !d.ok) throw new Error(d.error || res.statusText);
      status.innerHTML = 'Filed as <a href="' + d.url + '" target="_blank">#'
        + d.number + '</a> (patch/CSV backfilled).';
      btn.textContent = 'Already filed';
    } catch (e) {
      status.textContent = 'Error: ' + e.message; btn.disabled = false;
    }
  }
</script>
""".replace("__BATCH__", json.dumps(batch))


def review_toolbar(all_ids: list[str]) -> str:
    ids_js = json.dumps(all_ids)
    return f"""
<div class="toolbar">
  <strong>Review:</strong>
  <span id="progress" class="count"></span>
  &nbsp;|&nbsp;
  <button onclick='bulkSet({ids_js}, "Agree")'>Agree all</button>
  <button onclick='bulkSet({ids_js}, "")'>Clear all</button>
  <button onclick="exportJSON()">Export review JSON</button>
  <label>Import: <input type="file" accept="application/json" onchange="importJSON(event)"></label>
</div>
"""


def review_cell(case_id: str) -> str:
    opts = "".join(f'<option value="{v}">{v}</option>' for v in REVIEW_VALUES)
    return (
        f'<td class="reviewctl">'
        f'<select class="rev-status" onchange="setReview(\'{_esc(case_id)}\', this.value, undefined)">'
        f'<option value="">--</option>{opts}</select>'
        f'<textarea class="rev-note" placeholder="note" '
        f'onchange="setReview(\'{_esc(case_id)}\', undefined, this.value)"></textarea>'
        f'</td>'
    )


def port_pill(p: str) -> str:
    cls = (p or "none").replace(" ", "")
    return f'<span class="pill {cls}">{_esc(p or "-")}</span>'


def case_link(detail_url: str, label: str, base: str) -> str:
    """Render a link to the case-tracking page, or plain text if no URL."""
    if not detail_url:
        return _esc(label)
    href = (base.rstrip("/") + detail_url) if base else detail_url
    return f'<a href="{_esc(href)}" target="_blank" rel="noopener">{_esc(label)}</a>'


def extract_test_sources(source_path: str) -> dict[str, str]:
    """Map every `def test_*` method name -> its verbatim source in a test file."""
    import ast
    if not source_path or not os.path.exists(source_path):
        return {}
    text = open(source_path).read()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.name.startswith("test"):
            seg = ast.get_source_segment(text, node)
            if seg and node.name not in out:
                out[node.name] = seg
    return out


def resolve_case_source(test_name: str, methods: dict[str, str]) -> str:
    """Find the base test method source for a (parametrized) instantiated case.

    An instantiated case name is the method name followed by parametrization and
    device suffixes, e.g. test_mha_native_args -> test_mha_native_args_..._xpu.
    Match the longest method name that equals the case name or is a prefix of it
    at an underscore boundary (so test_foo never matches test_foobar).
    """
    if not methods or not test_name:
        return ""
    if test_name in methods:
        return methods[test_name]
    cand = [m for m in methods
            if test_name == m or test_name.startswith(m + "_")]
    if cand:
        return methods[max(cand, key=len)]
    return ""


def resolve_method_name(test_name: str, methods: dict[str, str]) -> str | None:
    """Like resolve_case_source but returns the matched base method name."""
    if not methods or not test_name:
        return None
    if test_name in methods:
        return test_name
    cand = [m for m in methods
            if test_name == m or test_name.startswith(m + "_")]
    return max(cand, key=len) if cand else None


def case_source_cell(test_name: str, methods: dict[str, str]) -> str:
    src = resolve_case_source(test_name, methods)
    if not src:
        return "<td><em>n/a</em></td>"
    return (f'<td><details><summary>show</summary>'
            f'<pre>{_esc(src)}</pre></details></td>')


def _js_str(s: str) -> str:
    """Safe JS string literal usable inside an HTML attribute."""
    return html.escape(json.dumps(s), quote=True)


def test_name_cell(case_id: str, test_name: str, methods: dict[str, str],
                   detail_url: str, base: str) -> str:
    """test_name cell: hover shows full source, click opens a modal with it."""
    label = case_link(detail_url, test_name, base)
    src = resolve_case_source(test_name, methods)
    if not src:
        return f"<td>{label}</td>"
    onclick = f'showModal({_js_str(test_name)}, {_js_str(src)})'
    return (
        f'<td class="tncell">'
        f'<a class="tn" onclick=\'{onclick}\'>{_esc(test_name)}</a>'
        f'<div class="srcpop"><pre>{_esc(src)}</pre></div>'
        f'</td>'
    )


def issue_cell(num: str) -> str:
    n = (num or "").strip().lstrip("#")
    if not n.isdigit():
        return f"<td>{_esc(num)}</td>"
    href = f"https://github.com/intel/torch-xpu-ops/issues/{n}"
    return (f'<td><a href="{href}" target="_blank" rel="noopener">'
            f'intel/torch-xpu-ops#{n}</a></td>')


def log_cell(label: str, log_text: str, href: str) -> str:
    """Log cell: click opens a modal with the full log text (inline)."""
    if log_text:
        return (f'<td><a class="tn" onclick=\'showModal("log", '
                f'{_js_str(log_text)})\'>{_esc(label or "log")}</a></td>')
    if href:
        return f'<td>{log_link(href, label)}</td>'
    return f"<td>{_esc(label or '')}</td>"


def log_link(href: str, label: str) -> str:
    if not href:
        return _esc(label or "")
    return f'<a href="{_esc(href)}" target="_blank" rel="noopener">{_esc(label or href)}</a>'


# ---------------------------------------------------------------------------
# Page builders.
# ---------------------------------------------------------------------------
def render_issue_drafts(test_file: str, drafts: list[dict]) -> str:
    """Render each drafted issue editably with a Submit button.

    Submit POSTs to the review server (tools/review_server.py), which files the
    issue via `gh` and backfills the real number into the patch, source and CSV.
    Opened over file:// the button only warns to use the review server.
    """
    if not drafts:
        return ""
    blocks = ""
    for i, d in enumerate(drafts):
        title = d.get("title", "")
        bodytext = d.get("body", "")
        labels = ", ".join(d.get("labels", []) or [])
        cases = ", ".join(str(c) for c in (d.get("cases", []) or []))
        verdict = d.get("verdict", "")
        number = d.get("number", "")
        status = ("filed as #" + str(number)) if number else "draft (not filed)"
        blocks += f"""
<div class="draft" data-file="{_esc(test_file)}" data-idx="{i}">
  <h3>{_esc(title or '(untitled draft)')}
      <span class="count">[{_esc(verdict)} &middot; cases: {_esc(cases)} &middot; {_esc(status)}]</span></h3>
  <label>Title<br><input class="d-title" value="{_esc(title)}" style="width:100%"></label>
  <label>Labels (comma-sep)<br><input class="d-labels" value="{_esc(labels)}" style="width:100%"></label>
  <label>Body<br><textarea class="d-body" style="min-height:220px">{_esc(bodytext)}</textarea></label>
  <div class="reviewctl">
    <button onclick="submitIssue(this)" {'disabled' if number else ''}>
      {'Already filed' if number else 'Submit to intel/torch-xpu-ops'}</button>
    <span class="d-status count"></span>
  </div>
</div>"""
    return f'<h2>Drafted issues <span class="count">({len(drafts)})</span></h2>{blocks}'


def write_junit(by_file: dict, out_path: str) -> int:
    """Emit a JUnit XML from CSV verdicts: Portable -> pass, else skipped."""
    import xml.sax.saxutils as sx

    def a(v):
        return sx.quoteattr("" if v is None else str(v))

    suites, total = [], 0
    for tf in sorted(by_file):
        rows = by_file[tf]
        cases_xml = ""
        for r in rows:
            total += 1
            name = r.get("test_name") or r.get("case_id", "")
            cls = r.get("class_name", "") or tf
            port = (r.get("portability") or "").strip()
            reason = r.get("portability reason", "")
            issue = (r.get("issue_number") or "").strip()
            props = (f"<properties>"
                     f"<property name=\"case_id\" value={a(r.get('case_id',''))}/>"
                     f"<property name=\"portability\" value={a(port)}/>"
                     + (f"<property name=\"issue\" value={a(issue)}/>" if issue else "")
                     + "</properties>")
            if port in ("Portable", "PASS", ""):
                body = props
            else:
                body = (f"<skipped message={a(port + ': ' + reason)}/>" + props)
            cases_xml += (f"<testcase classname={a(cls)} name={a(name)}>"
                          f"{body}</testcase>")
        suites.append(f"<testsuite name={a(tf)} tests=\"{len(rows)}\">"
                      f"{cases_xml}</testsuite>")
    xml = ("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<testsuites>"
           + "".join(suites) + "</testsuites>\n")
    with open(out_path, "w") as fh:
        fh.write(xml)
    import xml.dom.minidom as m
    m.parse(out_path)  # validate well-formed
    return total


def build_report_data(test_file, rows, info, out_dir, batch, case_base="",
                      src_root="test", branch=""):
    """Assemble the JSON data island for a per-file report.html.

    Copies the patch/log artifacts into out_dir (they remain real files) and
    mirrors their text, the per-case columns, a deduped base-method source map
    and the issue drafts into one JSON dict the browser renders client-side.
    """
    os.makedirs(out_dir, exist_ok=True)
    source_path = info.get("source") or os.path.join(src_root, test_file)
    methods = extract_test_sources(source_path)

    patch = {"name": "", "text": ""}
    log = {"name": "", "text": ""}
    if info.get("patch") and os.path.exists(info["patch"]):
        patch["name"] = os.path.basename(info["patch"])
        shutil.copy(info["patch"], os.path.join(out_dir, patch["name"]))
        patch["text"] = open(os.path.join(out_dir, patch["name"])).read()
    if info.get("log") and os.path.exists(info["log"]):
        log["name"] = os.path.basename(info["log"])
        shutil.copy(info["log"], os.path.join(out_dir, log["name"]))
        log["text"] = open(os.path.join(out_dir, log["name"])).read()

    present_cols = [c for c in DETAIL_COLUMNS if any(c in r for r in rows)]
    cases, used_methods = [], set()
    for r in rows:
        case = {c: r.get(c, "") for c in present_cols}
        case["case_id"] = r["case_id"]
        case["detail_url"] = r.get("detail_url", "")
        cases.append(case)
        m = resolve_method_name(r.get("test_name", ""), methods)
        if m:
            used_methods.add(m)
    sources = {m: methods[m] for m in used_methods}

    return {
        "schema": 1,
        "batch": batch,
        "test_file": test_file,
        "branch": branch,
        "commit": info.get("commit", ""),
        "repo": "intel/torch-xpu-ops",
        "case_base": case_base,
        "patch": patch,
        "log": log,
        "columns": present_cols,
        "sources": sources,
        "cases": cases,
        "drafts": info.get("issue_drafts", []) or [],
    }


def report_renderer_js() -> str:
    """Client-side renderer that builds the whole page from REPORT_DATA.

    Reuses the helpers defined in review_assets (showModal, setReview/getReview,
    bulkSet, exportJSON/importJSON, submitIssue). It only builds DOM; review
    painting is driven by review_assets' own DOMContentLoaded handler, which
    fires after this script runs inline at the end of <body>.
    """
    return r"""
<script>
(function(){
  var D = window.REPORT_DATA;
  var PORT_ORDER = ["Portable","Feature gap","Known Issue","Not Applicable","","TBD"];
  function esc(s){ var d=document.createElement('div');
    d.textContent = (s==null?'':String(s)); return d.innerHTML; }
  function jsAttr(s){ return esc(JSON.stringify(s==null?'':String(s))); }
  function codeOrNa(v){ return v ? '<code>'+esc(v)+'</code>' : '<em>n/a</em>'; }
  function portPill(p){ var cls=(p||'none').replace(/\s/g,'');
    return '<span class="pill '+cls+'">'+esc(p||'-')+'</span>'; }
  function portKey(p){ var i=PORT_ORDER.indexOf(p); return i<0?PORT_ORDER.length:i; }
  function linkify(v){
    var text = v==null?'':String(v);
    var re = /(https?:\/\/[^\s,]+)|(?:([\w.-]+\/[\w.-]+)#|#)(\d+)/g;
    var out='', last=0, m;
    while((m=re.exec(text))){
      out += esc(text.slice(last, m.index));
      if (m[1]){ var href=m[1].replace(/\.+$/,'');
        out += '<a href="'+esc(href)+'" target="_blank" rel="noopener">'+esc(href)+'</a>'; }
      else { var repo=m[2]||D.repo; var num=m[3];
        var h='https://github.com/'+repo+'/issues/'+num;
        var label=m[2]?(m[2]+'#'+num):('#'+num);
        out += '<a href="'+esc(h)+'" target="_blank" rel="noopener">'+esc(label)+'</a>'; }
      last = re.lastIndex;
    }
    out += esc(text.slice(last));
    return out;
  }
  function resolveSource(tn){
    var s=D.sources||{};
    if (s[tn]) return s[tn];
    var best=null;
    for (var m in s){ if (tn===m || tn.indexOf(m+'_')===0){
      if (!best || m.length>best.length) best=m; } }
    return best ? s[best] : '';
  }
  window.__openSrc = function(tn){ showModal(tn, resolveSource(tn)); };
  window.__openLog = function(){ showModal('log', (D.log&&D.log.text)||''); };
  function caseLink(detailUrl, label){
    if (!detailUrl) return esc(label);
    var base=D.case_base||'';
    var href = base ? (base.replace(/\/$/,'')+detailUrl) : detailUrl;
    return '<a href="'+esc(href)+'" target="_blank" rel="noopener">'+esc(label)+'</a>';
  }
  function issueCell(num){
    var n=String(num==null?'':num).trim().replace(/^#/,'');
    if (!/^\d+$/.test(n)) return '<td>'+esc(num)+'</td>';
    var href='https://github.com/'+D.repo+'/issues/'+n;
    return '<td><a href="'+href+'" target="_blank" rel="noopener">'+D.repo+'#'+n+'</a></td>';
  }
  function tnCell(tn, detailUrl){
    var label=caseLink(detailUrl, tn), src=resolveSource(tn);
    if (!src) return '<td>'+label+'</td>';
    return '<td class="tncell"><a class="tn" onclick="__openSrc('+jsAttr(tn)+')">'
      + esc(tn)+'</a><div class="srcpop"><pre>'+esc(src)+'</pre></div></td>';
  }
  function logCell(label){
    if (D.log && D.log.text)
      return '<td><a class="tn" onclick="__openLog()">'+esc(label||'log')+'</a></td>';
    if (D.log && D.log.name)
      return '<td><a href="'+esc(D.log.name)+'" target="_blank">'+esc(label||D.log.name)+'</a></td>';
    return '<td>'+esc(label||'')+'</td>';
  }
  function reviewCell(cid){
    var opts=["Agree","Disagree","Needs info"].map(function(v){
      return '<option value="'+v+'">'+v+'</option>'; }).join('');
    return '<td class="reviewctl"><select class="rev-status" onchange="setReview(\''
      + cid + '\', this.value, undefined)"><option value="">--</option>'+opts
      + '</select><textarea class="rev-note" placeholder="note" onchange="setReview(\''
      + cid + '\', undefined, this.value)"></textarea></td>';
  }
  function reviewToolbar(ids){
    window.__ALLIDS = ids;
    return '<div class="toolbar"><strong>Review:</strong> '
      + '<span id="progress" class="count"></span> &nbsp;|&nbsp; '
      + '<button onclick="bulkSet(window.__ALLIDS, \'Agree\')">Agree all</button> '
      + '<button onclick="bulkSet(window.__ALLIDS, \'\')">Clear all</button> '
      + '<button onclick="exportJSON()">Export review JSON</button> '
      + '<label>Import: <input type="file" accept="application/json" '
      + 'onchange="importJSON(event)"></label></div>';
  }
  function draftsBlock(){
    var drafts=D.drafts||[]; if (!drafts.length) return '';
    window.__DRAFTS = drafts;
    var h='<h2>Drafted issues <span class="count">('+drafts.length+')</span></h2>';
    drafts.forEach(function(d,i){
      var labels=(d.labels||[]).join(', ');
      var cases=(d.cases||[]).map(String).join(', ');
      var number=d.number||'';
      var st = number ? ('filed as #'+number) : 'draft (not filed)';
      h += '<div class="draft" data-file="'+esc(D.test_file)+'" data-idx="'+i+'">'
        + '<h3>'+esc(d.title||'(untitled draft)')+' <span class="count">['
        + esc(d.verdict||'')+' &middot; cases: '+esc(cases)+' &middot; '+esc(st)+']</span></h3>'
        + '<label>Title<br><input class="d-title" value="'+esc(d.title||'')+'" style="width:100%"></label>'
        + '<label>Labels (comma-sep)<br><input class="d-labels" value="'+esc(labels)+'" style="width:100%"></label>'
        + '<label>Body<br><textarea class="d-body" style="min-height:220px">'+esc(d.body||'')+'</textarea></label>'
        + '<div class="reviewctl"><button onclick="submitIssue(this)"'+(number?' disabled':'')+'>'
        + (number?'Already filed':'Submit to '+esc(D.repo))+'</button>'
        + '<span class="d-status count"></span></div></div>';
    });
    return h;
  }
  function render(){
    var h='';
    h += '<h1>'+esc(D.test_file)+'</h1>';
    var patchLink = D.patch.name
      ? '<a href="'+esc(D.patch.name)+'">'+esc(D.patch.name)+'</a>' : '<em>none</em>';
    var logLink = D.log.name
      ? '<a href="'+esc(D.log.name)+'" target="_blank">'+esc(D.log.name)+'</a>' : '<em>none</em>';
    h += '<p>Branch: '+codeOrNa(D.branch)+' &nbsp;|&nbsp; Commit: '+codeOrNa(D.commit)
      + ' &nbsp;|&nbsp; Patch: '+patchLink+' &nbsp;|&nbsp; Log: '+logLink+'</p>';
    var allIds = D.cases.map(function(c){ return c.case_id; });
    h += reviewToolbar(allIds);

    var cats={}, idsByCat={};
    D.cases.forEach(function(c){ var p=(c.portability||'').trim();
      cats[p]=(cats[p]||0)+1; (idsByCat[p]=idsByCat[p]||[]).push(c.case_id); });
    var catKeys=Object.keys(cats).sort(function(a,b){ return portKey(a)-portKey(b); });
    window.__CATIDS = catKeys.map(function(c){ return idsByCat[c]; });
    h += '<h2>Summary <span class="count">('+D.cases.length+' cases)</span></h2>';
    h += '<table><thead><tr><th>portability</th><th>count</th>'
      + '<th>set review (whole category)</th></tr></thead><tbody>';
    catKeys.forEach(function(cat,i){
      h += '<tr><td>'+portPill(cat)+'</td><td>'+cats[cat]+'</td><td class="reviewctl">'
        + '<button onclick="bulkSet(window.__CATIDS['+i+'],\'Agree\')">Agree</button>'
        + '<button onclick="bulkSet(window.__CATIDS['+i+'],\'Disagree\')">Disagree</button>'
        + '<button onclick="bulkSet(window.__CATIDS['+i+'],\'Needs info\')">Needs info</button>'
        + '</td></tr>';
    });
    h += '</tbody></table>';

    if (D.patch.text)
      h += '<details><summary>patch source</summary><pre>'+esc(D.patch.text)+'</pre></details>';
    h += draftsBlock();

    h += '<h2>Case details</h2><table><thead><tr>';
    D.columns.forEach(function(c){ h += '<th>'+esc(c)+'</th>'; });
    h += '<th>review</th></tr></thead><tbody>';
    D.cases.forEach(function(c){
      var tds='';
      D.columns.forEach(function(col){
        var v=c[col]==null?'':c[col];
        if (col==='portability') tds += '<td>'+portPill(v)+'</td>';
        else if (col==='test_name') tds += tnCell(v, c.detail_url);
        else if (col==='case_id') tds += '<td>'+caseLink(c.detail_url, v)+'</td>';
        else if (col==='issue_number') tds += issueCell(v);
        else if (col==='log') tds += logCell(v);
        else tds += '<td>'+linkify(v)+'</td>';
      });
      h += '<tr data-case="'+esc(c.case_id)+'">'+tds+reviewCell(c.case_id)+'</tr>';
    });
    h += '</tbody></table>';
    document.getElementById('app').innerHTML = h;
  }
  render();
})();
</script>
"""


def build_result_page(test_file, rows, info, out_dir, batch, case_base="",
                      src_root="test", branch=""):
    data = build_report_data(test_file, rows, info, out_dir, batch, case_base,
                             src_root, branch)
    island = json.dumps(data).replace("</", "<\\/")
    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{_esc(test_file)} - {_esc(batch)}</title>
{review_assets(batch)}
</head><body>
<p><a href="../index.html">&larr; batch index</a> &nbsp;|&nbsp;
   <a href="../input_table.html">input table</a></p>
<script type="application/json" id="report-data">{island}</script>
<script>window.REPORT_DATA = JSON.parse(document.getElementById('report-data').textContent);</script>
<div id="app"></div>
{report_renderer_js()}
</body></html>"""
    with open(os.path.join(out_dir, "report.html"), "w") as fh:
        fh.write(doc)


def build_input_table(rows, out_path, batch, case_base="", branch=""):
    cols = list(rows[0].keys()) if rows else []
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols) + "<th>review</th>"
    body = ""
    for r in rows:
        cid = r["case_id"]
        detail_url = r.get("detail_url", "")
        logval = r.get("log", "")
        log_href = f"{_slug(r.get('test_file',''))}/{os.path.basename(logval)}" if logval else ""
        tds = ""
        for c in cols:
            v = r.get(c, "")
            if c == "portability":
                tds += f"<td>{port_pill(v)}</td>"
            elif c in ("test_name", "case_id"):
                tds += f"<td>{case_link(detail_url, v, case_base)}</td>"
            elif c == "issue_number":
                tds += issue_cell(v)
            elif c == "log":
                tds += f"<td>{log_link(log_href, v) if log_href else _esc(v)}</td>"
            else:
                tds += f"<td>{linkify_issues(v)}</td>"
        body += f'<tr data-case="{_esc(cid)}">{tds}{review_cell(cid)}</tr>'
    all_ids = [r["case_id"] for r in rows]
    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Input table - {_esc(batch)}</title>
{review_assets(batch)}
</head><body>
<p><a href="index.html">&larr; batch index</a></p>
<h1>Input table &mdash; {_esc(batch)}</h1>
<p>Branch: {f'<code>{_esc(branch)}</code>' if branch else '<em>n/a</em>'}</p>
{review_toolbar(all_ids)}
<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
</body></html>"""
    with open(out_path, "w") as fh:
        fh.write(doc)


def _uniq_join(rows, col):
    vals = []
    for r in rows:
        v = (r.get(col) or "").strip()
        if v and v not in vals:
            vals.append(v)
    return ", ".join(vals)


def build_index(by_file, out_path, batch, manifest, branch=""):
    rows_html = ""
    for tf in sorted(by_file):
        rows = by_file[tf]
        cats = Counter((r.get("portability") or "").strip() for r in rows)
        cat_summary = " ".join(
            f'{port_pill(c)}&times;{cats[c]}' for c in sorted(cats, key=_port_key))
        d = _slug(tf)
        info = manifest.get(tf, {})
        commit = info.get("commit", "")
        # Per-file branch: only enabled files (those with an enablement commit)
        # carry the branch; files not yet worked on leave it blank.
        fbranch = info.get("branch") or (branch if commit else "")
        fstatus = info.get("status") or ("Enabled" if commit else "Pending")
        owner = _uniq_join(rows, "owner")
        assignee = _uniq_join(rows, "assignee")
        csv_pr = _uniq_join(rows, "fix_pr")
        pr_box = (
            f'<input class="fixpr" data-file="{_esc(tf)}" '
            f'placeholder="PR # or URL" value="{_esc(csv_pr)}" '
            f'onchange="setFixPR({json.dumps(tf)}, this.value)">'
        )
        ids_js = _esc(json.dumps([r["case_id"] for r in rows]))
        status_cls = "st-enabled" if fstatus.lower() == "enabled" else "st-pending"
        # data-* attributes power the filter box (match against any of them)
        filt = _esc(" ".join([tf, owner, assignee, fstatus,
                              " ".join(sorted(cats))]).lower())
        rows_html += (
            f'<tr class="frow" data-filter="{filt}" data-file="{_esc(tf)}" '
            f'data-ids="{ids_js}">'
            f'<td><input type="checkbox" class="selbox" data-file="{_esc(tf)}"></td>'
            f'<td><a href="{d}/report.html">{_esc(tf)}</a></td>'
            f'<td>{len(rows)}</td><td>{cat_summary}</td>'
            f'<td><span class="statuspill {status_cls}">{_esc(fstatus)}</span></td>'
            f'<td class="filerev" data-file="{_esc(tf)}">&mdash;</td>'
            f'<td>{_esc(owner)}</td><td>{_esc(assignee)}</td>'
            f'<td><code>{_esc(fbranch)}</code></td>'
            f'<td><code>{_esc(commit)}</code></td><td>{pr_box}</td></tr>'
        )
    total = sum(len(v) for v in by_file.values())
    all_ids = [r["case_id"] for v in by_file.values() for r in v]
    batch_bar = """
<div class="toolbar">
  <strong>Filter &amp; batch-set Fix PR:</strong><br>
  <label>Filter files: <input id="filter" oninput="filterRows()"
         placeholder="type to match file / owner / assignee / portability"></label>
  <span id="filtercount" class="count"></span>
  <br>
  <label><input type="checkbox" id="selall" onchange="toggleAllVisible(this.checked)">
         select all (visible)</label>
  &nbsp;|&nbsp;
  <input id="batchpr" placeholder="Fix PR # or URL">
  <button onclick="applyBatchPR()">Apply to selected</button>
  <span id="selcount" class="count"></span>
</div>
<script>
  function visibleRows(){
    return Array.prototype.filter.call(
      document.querySelectorAll('tr.frow'),
      function(r){ return r.style.display !== 'none'; });
  }
  function filterRows(){
    const q = (document.getElementById('filter').value || '').toLowerCase().trim();
    let shown = 0;
    document.querySelectorAll('tr.frow').forEach(function(r){
      const hit = !q || (r.getAttribute('data-filter') || '').indexOf(q) >= 0;
      r.style.display = hit ? '' : 'none';
      if (hit) shown++;
      if (!hit) { const cb = r.querySelector('.selbox'); if (cb) cb.checked = false; }
    });
    document.getElementById('filtercount').textContent = shown + ' file(s) shown';
    document.getElementById('selall').checked = false;
    updateSelCount();
  }
  function toggleAllVisible(on){
    visibleRows().forEach(function(r){
      const cb = r.querySelector('.selbox'); if (cb) cb.checked = on; });
    updateSelCount();
  }
  function selectedFiles(){
    return Array.prototype.map.call(
      document.querySelectorAll('.selbox:checked'),
      function(cb){ return cb.getAttribute('data-file'); });
  }
  function updateSelCount(){
    document.getElementById('selcount').textContent =
      selectedFiles().length + ' selected';
  }
  function applyBatchPR(){
    const pr = document.getElementById('batchpr').value;
    const files = selectedFiles();
    if (!files.length){ alert('No files selected.'); return; }
    files.forEach(function(tf){
      setFixPR(tf, pr);
      const inp = document.querySelector('input.fixpr[data-file="' +
        tf.replace(/"/g,'\\\\"') + '"]');
      if (inp) inp.value = pr;
    });
  }
  function paintFileReviews(){
    document.querySelectorAll('tr.frow').forEach(function(r){
      const cell = r.querySelector('td.filerev'); if (!cell) return;
      let ids = []; try { ids = JSON.parse(r.getAttribute('data-ids') || '[]'); } catch(e){}
      let done = 0;
      ids.forEach(function(id){ if ((getReview(id).status||"")) done++; });
      const n = ids.length;
      cell.textContent = done + " / " + n;
      cell.className = "filerev " + (n && done === n ? "rev-done"
                       : done ? "rev-partial" : "rev-none");
    });
  }
  document.addEventListener('DOMContentLoaded', function(){
    document.querySelectorAll('.selbox').forEach(function(cb){
      cb.addEventListener('change', updateSelCount); });
    filterRows();
    paintFileReviews();
  });
</script>
"""
    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>XPU enablement report - {_esc(batch)}</title>
{review_assets(batch)}
</head><body>
<h1>XPU test enablement &mdash; {_esc(batch)}</h1>
<p>Branch: {f'<code>{_esc(branch)}</code>' if branch else '<em>n/a</em>'}
   &nbsp;|&nbsp; {len(by_file)} test files, {total} cases.
   &nbsp;<a href="input_table.html">Open full input table &raquo;</a></p>
{review_toolbar(all_ids)}
{batch_bar}
<table><thead><tr><th></th><th>test file</th><th>cases</th>
<th>portability breakdown</th>
<th>status</th><th>reviewed</th>
<th>owner</th><th>assignee</th><th>branch</th><th>commit</th>
<th>Fix PR</th></tr></thead>
<tbody>{rows_html}</tbody></table>
<p class="count">Review verdicts and Fix PR entries are stored in your browser
   (localStorage) per batch. Use "Export review JSON" on any page, then merge
   into the CSV with <code>merge_review.py</code>.</p>
</body></html>"""
    with open(out_path, "w") as fh:
        fh.write(doc)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--batch", default=None,
                    help="localStorage namespace for review verdicts; "
                         "defaults to the branch name")
    ap.add_argument("--out", default="xpu_enable_report")
    ap.add_argument("--manifest")
    ap.add_argument("--junit", default=None,
                    help="also emit a branch-scoped JUnit XML to this path, "
                         "built from the CSV verdicts of the manifest files")
    ap.add_argument("--files-with-manifest-only", action="store_true",
                    help="only emit result pages for files listed in the manifest")
    ap.add_argument("--case-base-url", default="",
                    help="base URL prepended to each row's detail_url to link the "
                         "test case (e.g. https://host); default makes it relative")
    ap.add_argument("--src-root", default="test",
                    help="directory holding the test files, used to extract the "
                         "inline test-case source when a file has no manifest "
                         "'source' field (default: test)")
    ap.add_argument("--branch", default=None,
                    help="git branch the enablement commits live on; shown on the "
                         "index and report pages. Defaults to the manifest 'branch' "
                         "field, else the current git branch.")
    args = ap.parse_args()

    rows = read_csv(args.csv)
    manifest = load_manifest(args.manifest)

    branch = args.branch
    if branch is None and args.manifest and os.path.exists(args.manifest):
        branch = json.load(open(args.manifest)).get("branch")
    if branch is None:
        try:
            import subprocess
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                text=True).strip()
        except Exception:
            branch = ""

    batch = args.batch or branch or "batch"

    by_file = defaultdict(list)
    for r in rows:
        by_file[r.get("test_file", "")].append(r)
    by_file.pop("", None)

    if args.files_with_manifest_only and manifest:
        by_file = {k: v for k, v in by_file.items() if k in manifest}

    batch_dir = os.path.join(args.out, _slug(branch or batch))
    os.makedirs(batch_dir, exist_ok=True)

    for tf, frows in by_file.items():
        info = manifest.get(tf, {})
        fbranch = info.get("branch") or (branch if info.get("commit") else "")
        build_result_page(tf, frows, info, os.path.join(batch_dir, _slug(tf)),
                          batch, args.case_base_url, args.src_root, fbranch)

    build_input_table(rows, os.path.join(batch_dir, "input_table.html"),
                      batch, args.case_base_url, branch)
    build_index(by_file, os.path.join(batch_dir, "index.html"), batch,
                manifest, branch)

    print(f"Report written to {batch_dir}/index.html "
          f"({len(by_file)} files, {sum(len(v) for v in by_file.values())} cases)")

    if args.junit:
        n = write_junit(by_file, args.junit)
        print(f"JUnit XML written to {args.junit} ({n} cases)")


if __name__ == "__main__":
    main()
