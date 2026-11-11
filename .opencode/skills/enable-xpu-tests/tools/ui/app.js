// XPU enablement review UI. All data comes from the review server's /api/*;
// nothing is cached across reloads except what the server persists.
'use strict';
var DATA = null, FILE = null;
var LAST_FILE = sessionStorage.getItem('lastFile') || '';      // slug of the last file page opened
var BACK_TO_INDEX = false;                                      // true right after leaving a file page
var IDX_FILTER = JSON.parse(sessionStorage.getItem('idxFilter2') || '{}');  // column -> selected value ('' = all)
// Dropdown filters on the index. Each file row gets a list of tag values per column; a
// row matches a column filter when the selected value is one of its tags.
var IDX_COLS = [
  { col: 'file', label: 'Test file' }, { col: 'status', label: 'Status' }, { col: 'cases', label: 'Cases' },
  { col: 'portability', label: 'Portability' }, { col: 'review', label: 'Review' },
  { col: 'branch', label: 'Agent branch' }, { col: 'pr', label: 'PR' }];

// Short label for a free-text status ("Imported (2 commit(s); conflict resolved ...)" ->
// "Imported"); the full text is shown on hover.
function shortStatus(st) {
  st = st || '-';
  var m = st.match(/^(Needs rebase|Imported|Processed|No change|In progress)\b/);
  return m ? m[1] : (st.length > 18 ? st.slice(0, 16) + '…' : st);
}

function fileTags(f, b, prs) {
  var port = Object.keys(f.portability || {}).filter(function (k) { return f.portability[k]; });
  var rv = f.review || {}, nRev = Object.keys(rv).reduce(function (a, k) { return a + (k ? rv[k] : 0); }, 0);
  var dir = f.test_file.indexOf('/') > 0 ? f.test_file.split('/')[0] + '/' : '(top level)';
  var review = Object.keys(rv).filter(function (k) { return rv[k]; }).map(function (k) { return 'has ' + (k || 'unreviewed'); });
  review.push(nRev === 0 ? 'not started' : (nRev >= f.n_cases && !rv[''] ? 'fully reviewed' : 'partly reviewed'));
  if (rv['Needs work'] || rv['Rejected'] || f.n_comments) review.push('needs rework');
  var pr;
  if (b && b.pr && b.pr.number) pr = ['has PR', 'PR #' + b.pr.number];
  else if (b) pr = ['in bundle (not submitted)'];
  else if (prs.length) pr = ['has PR'].concat(prs.map(function (x) { return 'PR #' + x.pr.number; }));
  else pr = [f.commit ? 'no PR yet' : 'no patch'];
  return {
    file: [dir], status: [shortStatus(f.status)],
    cases: [f.n_cases <= 3 ? '1-3' : f.n_cases <= 10 ? '4-10' : f.n_cases <= 25 ? '11-25' : '26+'],
    portability: port.map(function (k) { return 'has ' + k; }).concat(port.length === 1 && port[0] === 'Portable' ? ['all Portable'] : []),
    review: review, branch: [f.agent_branch ? 'has branch' : 'no branch'], pr: pr
  };
}

function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
function cls(s) { return String(s || '').replace(/\s+/g, ''); }
function pill(v, kind, attrs) { return '<span class="pill ' + kind + '-' + cls(v) + '"' + (attrs || '') + '>' + esc(v || '(unset)') + '</span>'; }
function $(sel, root) { return (root || document).querySelector(sel); }
function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
function api(path, body) {
  var opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  return fetch(path, opt).then(function (r) { return r.json(); });
}
function msg(el, res, okText) {
  if (!el) return;
  el.innerHTML = res && res.ok ? '<span class="ok">' + esc(okText || 'saved') + '</span>'
    : '<span class="err">' + esc((res && (res.error || (res.errors || []).join('; ') || (res.warnings || []).join('; '))) || 'error') + '</span>';
}
function counts(obj, kind, order) {
  var keys = (order || []).concat(Object.keys(obj || {})).filter(function (k, i, a) { return a.indexOf(k) === i && obj[k]; });
  return keys.map(function (k) { return pill(k || (kind === 'r' ? 'unreviewed' : 'unset'), kind) + ' ' + obj[k]; }).join(' &nbsp;');
}

// ---------------------------------------------------------------- index
function renderIndex() {
  var files = DATA.files || [];
  $('#brand').textContent = files.length + ' files / ' + files.reduce(function (a, f) { return a + f.n_cases; }, 0) + ' cases';
  var h = [];
  if (DATA.rework) h.push('<div class="banner">' + DATA.rework + ' case(s) in the rework queue (comments / common prompts / Needs work). Ask the agent to "process the rework queue".</div>');
  var open = (DATA.bundles || []).filter(function (b) { return b.kind !== 'external' && b.status === 'picked'; });
  if (open.length) {
    h.push('<h2>PR bundles waiting to be submitted</h2>');
    open.forEach(function (b) { h.push(bundleCard(b)); });
  }
  h.push('<h2>Test files <span id="idx-count" class="muted small"></span></h2><div class="card row">Selected: <b id="nsel">0</b> &nbsp; Target branch <input type="text" id="pr-target" placeholder="xpu_enable_xxxx"> ' +
    'Title <input type="text" id="pr-title" size="40" placeholder="[xpu] Enable ... tests on XPU"> <button id="pr-create">Create PR bundle (cherry-pick)</button> <span id="pr-msg"></span></div>');
  var tagsBySlug = {}, valueCount = {};
  files.forEach(function (f) {
    var prs = (DATA.bundles || []).filter(function (x) { return x.kind === 'external' && (x.files || []).some(function (y) { return y.slug === f.slug; }) && x.id !== f.bundle; });
    var b = (DATA.bundles || []).filter(function (x) { return x.id === f.bundle; })[0];
    var t = tagsBySlug[f.slug] = fileTags(f, b, prs);
    IDX_COLS.forEach(function (c) { (t[c.col] || []).forEach(function (v) { var k = c.col + '\u0000' + v; valueCount[k] = (valueCount[k] || 0) + 1; }); });
  });
  function options(col) {
    var vals = Object.keys(valueCount).filter(function (k) { return k.split('\u0000')[0] === col; }).map(function (k) { return k.split('\u0000')[1]; }).sort();
    return '<option value="">(all)</option>' + vals.map(function (v) {
      return '<option value="' + esc(v) + '"' + (IDX_FILTER[col] === v ? ' selected' : '') + '>' + esc(v) + ' (' + valueCount[col + '\u0000' + v] + ')</option>';
    }).join('');
  }
  h.push('<div class="card row small">Quick views: ' +
    '<a href="#" class="qv" data-q=\'{"review":"has unreviewed","pr":"no PR yet"}\'>to review, no PR yet</a> &middot; ' +
    '<a href="#" class="qv" data-q=\'{"review":"fully reviewed","pr":"no PR yet"}\'>reviewed, ready for a PR</a> &middot; ' +
    '<a href="#" class="qv" data-q=\'{"review":"needs rework"}\'>needs rework</a> &middot; ' +
    '<a href="#" class="qv" data-q=\'{"pr":"in bundle (not submitted)"}\'>bundled, not submitted</a> &middot; ' +
    '<a href="#" class="qv" data-q=\'{}\'>all files</a></div>');
  h.push('<table id="idx"><thead><tr><th><input type="checkbox" id="chk-all" title="select all visible"></th>' +
    IDX_COLS.map(function (c) { return '<th>' + esc(c.label) + '</th>'; }).join('') + '</tr>' +
    '<tr class="filters"><th><a href="#" id="f-clear" class="small" title="clear all filters">clear</a></th>' +
    IDX_COLS.map(function (c) { return '<th><select class="f-sel" data-col="' + c.col + '">' + options(c.col) + '</select></th>'; }).join('') +
    '</tr></thead><tbody>');
  files.forEach(function (f) {
    var prs = (DATA.bundles || []).filter(function (x) { return x.kind === 'external' && (x.files || []).some(function (y) { return y.slug === f.slug; }) && x.id !== f.bundle; });
    var b = (DATA.bundles || []).filter(function (x) { return x.id === f.bundle; })[0];
    var pr = b ? (b.pr && b.pr.number ? '<a target="_blank" href="' + esc(b.pr.url) + '">#' + b.pr.number + '</a> ' + esc(b.pr.state || '') : '<a href="#" class="to-bundle" data-id="' + esc(b.id) + '" title="go to the bundle card (push / Create PR)">' + esc(b.id) + '</a> (' + esc(b.status) + ')')
      : (f.pr_link ? (f.review_link ? '<a target="_blank" href="' + esc(f.review_link) + '" title="diff on your fork; push the agent branch first">review &#8599;</a> &middot; ' : '') +
        '<a target="_blank" href="' + esc(f.pr_link) + '" title="push the agent branch to your fork first">submit PR &#8599;</a>' : (f.commit ? '<span class="muted small" title="set fork owner: setup_ws.py --fork-owner">no fork owner</span>' : ''));
    var t = tagsBySlug[f.slug];
    h.push('<tr data-slug="' + esc(f.slug) + '"' + IDX_COLS.map(function (c) { return ' data-f-' + c.col + '="' + esc((t[c.col] || []).join('\u0001')) + '"'; }).join('') + '>' +
      '<td><input type="checkbox" class="chk" data-slug="' + esc(f.slug) + '"' + (f.commit ? '' : ' disabled title="no agent commit"') + '></td>' +
      '<td><a href="#/file/' + encodeURIComponent(f.slug) + '">' + esc(f.test_file) + '</a>' + (f.n_comments ? ' <span class="muted small">(' + f.n_comments + ' comments)</span>' : '') + '</td>' +
      '<td class="st" title="' + esc(f.status) + '">' + esc(shortStatus(f.status)) + '</td><td>' + f.n_cases + '</td><td>' + counts(f.portability, 'p', DATA.portability_order) + '</td>' +
      '<td>' + counts(f.review, 'r', DATA.review_choices) + '</td><td class="br" title="' + esc(f.agent_branch || '') + '"><code>' + esc(f.agent_branch || '-') + '</code></td><td class="prc">' + pr +
      prs.map(function (x) { return ' <a target="_blank" href="' + esc(x.pr.url) + '">#' + x.pr.number + '</a>'; }).join('') + '</td></tr>');
  });
  h.push('</tbody></table>');
  var rest = (DATA.bundles || []).filter(function (b) { return open.indexOf(b) < 0; });
  h.push('<h2>Submitted / recorded PRs</h2>');
  if (!rest.length) h.push('<p class="muted">none yet</p>');
  rest.forEach(function (b) { h.push(bundleCard(b)); });
  $('#app').innerHTML = h.join('');
  wireIndex();
  applyIdxFilter();
  // return to where the reviewer was: scroll to and highlight the last opened file
  var tr = LAST_FILE && $('#idx tr[data-slug="' + LAST_FILE + '"]');
  if (tr) {
    tr.classList.add('last');                      // always mark the last reviewed file
    if (BACK_TO_INDEX) { tr.scrollIntoView({ block: 'center' }); BACK_TO_INDEX = false; }
    else if (sessionStorage.getItem('idxScroll')) window.scrollTo(0, +sessionStorage.getItem('idxScroll'));
  }
}

// A row is shown when, for every column with a selected value, that value is one of the
// row's tags for the column (columns combine with AND).
function applyIdxFilter() {
  var n = 0, rows = $$('#idx tbody tr');
  rows.forEach(function (tr) {
    var ok = Object.keys(IDX_FILTER).every(function (c) {
      var want = IDX_FILTER[c]; if (!want) return true;
      return (tr.getAttribute('data-f-' + c) || '').split('\u0001').indexOf(want) >= 0;
    });
    tr.style.display = ok ? '' : 'none'; if (ok) n++;
    if (!ok) { var cb = tr.querySelector('.chk'); if (cb) cb.checked = false; }
  });
  var hd = $('#idx-count'); if (hd) hd.textContent = n + ' / ' + rows.length + ' files';
  var ns = $('#nsel'); if (ns) ns.textContent = $$('.chk:checked').length;
}

function setIdxFilter(f) {
  IDX_FILTER = f; sessionStorage.setItem('idxFilter2', JSON.stringify(IDX_FILTER));
  $$('.f-sel').forEach(function (sel) { sel.value = IDX_FILTER[sel.getAttribute('data-col')] || ''; });
  applyIdxFilter();
}

function bundleCard(b) {
  var files = (b.files || []).map(function (f) {
    return '<li>' + esc(f.test_file) + ' ' + pill(f.state || '?', 's', '') + (f.conflicts && f.conflicts.length ? ' <span class="err small">conflicts: ' + esc(f.conflicts.join(', ')) + '</span>' : '') +
      (b.pending && b.pending[f.slug] ? ' <span class="warn small">' + b.pending[f.slug] + ' new commit(s)</span>' : '') +
      (b.status === 'picked' ? ' <a href="#" class="b-rm" data-slug="' + esc(f.slug) + '">remove</a>' : '') + '</li>';
  }).join('');
  var pending = Object.keys(b.pending || {}).length;
  if (b.kind === 'external') return '<div class="card bundle" data-id="' + esc(b.id) + '"><b>' + esc(b.title) + '</b> ' + pill(b.status, 's') +
    ' <a target="_blank" href="' + esc(b.pr.url) + '">PR #' + b.pr.number + '</a> ' + esc(b.pr.state || '') + ' <span class="muted small">recorded existing PR, branch ' + esc(b.target_branch) + '</span>' +
    '<ul class="small">' + (b.files || []).map(function (f) { return '<li>' + esc(f.test_file) + (f.cases ? ' <span class="muted">(' + f.cases.length + ' cases)</span>' : ' <span class="muted">(all cases)</span>') + '</li>'; }).join('') + '</ul>' +
    '<div class="row"><button class="ghost b-refresh">Refresh state</button> <span class="b-msg"></span></div></div>';
  return '<div class="card bundle" data-id="' + esc(b.id) + '"><b>' + esc(b.title) + '</b> <code>' + esc(b.target_branch) + '</code> ' + pill(b.status, 's') +
    (b.pr && b.pr.number ? ' <a target="_blank" href="' + esc(b.pr.url) + '">PR #' + b.pr.number + '</a> ' + esc(b.pr.state || '') : '') +
    '<ul class="small">' + files + '</ul>' +
    '<details><summary>Commits on target</summary><pre>' + esc(b.log || '') + '</pre></details>' +
    '<details' + (b.status === 'picked' ? ' open' : '') + '><summary>PR description (editable)</summary><textarea class="b-desc" style="min-height:260px">' + esc(b.description || '') + '</textarea>' +
    '<button class="ghost b-save">Save description</button> <span class="b-dmsg"></span></details>' +
    (function () {
      var ps = b.push_state || {}, has = b.pr && b.pr.number;
      if (has) return '<div class="row"><a class="btnlink" target="_blank" href="' + esc(b.pr.url) + '">Open PR #' + b.pr.number + ' &#8599;</a> <button class="ghost b-submit">Push update</button> <span class="b-smsg"></span></div>';
      if (ps.pushed) return '<div class="row"><span class="pill s-PASSED">pushed</span> ' +
        '<a class="btnlink" target="_blank" href="' + esc(b.compare_url || b.pr_page) + '">Create PR on GitHub &#8599;</a> ' +
        '<a class="ghost-link" target="_blank" href="' + esc(b.review_url) + '">Review diff (share) &#8599;</a> ' +
        (ps.in_sync ? '' : '<button class="orange b-submit">Push latest commits</button> ') + '<span class="b-smsg"></span></div>' +
        '<div class="small">Share: <input type="text" readonly size="70" value="' + esc(b.review_url) + '" onclick="this.select()"></div>';
      return '<div class="row"><span class="pill">not pushed yet</span> <button class="green b-submit">Push to fork</button> <span class="b-smsg"></span></div>';
    })() +
    '<details><summary class="small">Manual alternatives</summary><pre>' + esc((b.submit || []).join('\n')) + '</pre>' +
      (b.compare_url ? '<a target="_blank" class="small" href="' + esc(b.compare_url) + '">prefilled PR page &#8599;</a>' : '') + '</details>' +
    '<div class="row">PR number or URL <input type="text" class="b-pr" value="' + esc((b.pr && b.pr.url) || '') + '" size="40"> <button class="b-setpr">Set PR &amp; fill Fix PR</button>' +
    (b.pr && b.pr.number ? ' <button class="ghost b-refresh">Refresh state</button>' : '') +
    (pending ? ' <button class="orange b-update">Update branch (' + pending + ' file(s) with new commits)</button>' : '') +
    (b.status === 'submitted' || b.status === 'merged' ? '' : ' <button class="red b-delete">Delete bundle</button>') +
    ' <span class="b-msg"></span></div></div>';
}

function b0(id) { return (DATA.bundles || []).filter(function (x) { return x.id === id; })[0] || {}; }

function wireIndex() {
  $$('.f-sel').forEach(function (sel) {
    sel.addEventListener('change', function () {
      var f = Object.assign({}, IDX_FILTER); f[sel.getAttribute('data-col')] = sel.value; setIdxFilter(f);
    });
  });
  $$('.qv').forEach(function (a) { a.addEventListener('click', function (e) { e.preventDefault(); setIdxFilter(JSON.parse(a.getAttribute('data-q'))); }); });
  var fc = $('#f-clear');
  if (fc) fc.addEventListener('click', function (e) { e.preventDefault(); setIdxFilter({}); });
  $$('.to-bundle').forEach(function (a) {
    a.addEventListener('click', function (e) {
      e.preventDefault();
      var card = $('.bundle[data-id="' + a.getAttribute('data-id') + '"]');
      if (card) { card.scrollIntoView({ behavior: 'smooth', block: 'start' }); card.classList.add('flash'); setTimeout(function () { card.classList.remove('flash'); }, 1500); }
    });
  });
  function sel() { return $$('.chk:checked').map(function (c) { return c.getAttribute('data-slug'); }); }
  function upd() { $('#nsel').textContent = sel().length; }
  $$('.chk').forEach(function (c) { c.addEventListener('change', upd); });
  $('#chk-all').addEventListener('change', function (e) { $$('.chk:not(:disabled)').forEach(function (c) { if (c.closest('tr').style.display !== 'none') c.checked = e.target.checked; }); upd(); });
  $('#pr-create').addEventListener('click', function () {
    var files = sel(), target = $('#pr-target').value.trim(), title = $('#pr-title').value.trim(), m = $('#pr-msg');
    if (!files.length || !target || !title) { m.innerHTML = '<span class="err">select files, enter target branch and title</span>'; return; }
    function go(force) {
      m.textContent = 'cherry-picking...';
      api('/api/pr/create', { files: files, target: target, title: title, force: force }).then(function (r) {
        if (!r.ok && r.need_confirm && confirm('Warnings:\n- ' + r.warnings.join('\n- ') + '\n\nCreate the bundle anyway?')) return go(true);
        msg(m, r, 'bundle ' + (r.bundle ? r.bundle.id : '') + ' created');
        if (r.ok) load().then(function () { var c = $('.bundle[data-id="' + r.bundle.id + '"]'); if (c) c.scrollIntoView({ block: 'start' }); });
      });
    }
    go(false);
  });
  $$('.bundle').forEach(function (card) {
    var id = card.getAttribute('data-id'), m = $('.b-msg', card);
    function act(path, body, ok) { m.textContent = 'working...'; api('/api/pr/' + path, Object.assign({ id: id }, body || {})).then(function (r) { msg(m, r, ok); if (r.ok) load(); return r; }); }
    if (!$('.b-save', card)) { var rf0 = $('.b-refresh', card); if (rf0) rf0.addEventListener('click', function () { act('refresh', {}, 'refreshed'); }); return; }
    var bp = b0(id);
    if (bp.push_state && bp.push_state.pushed && !(bp.pr || {}).number) {
      var bpoll = setInterval(function () { if (!document.body.contains(card)) { clearInterval(bpoll); return; } api('/api/pr/link', { id: id }).then(function (x) { if (x.found) { clearInterval(bpoll); load(); } }); }, 15000);
      setTimeout(function () { clearInterval(bpoll); }, 30 * 60000);
    }
    var bsub = $('.b-submit', card);
    if (bsub) bsub.addEventListener('click', function () {
      var sm = $('.b-smsg', card), btn = bsub;
      btn.disabled = true; sm.textContent = 'pushing ' + b0(id).target_branch + ' to your fork (can take a minute)...';
      api('/api/pr/submit', { id: id }).then(function (r) {
        btn.disabled = false;
        if (!r.ok || r.dry_run) { msg(sm, r, 'dry-run: nothing pushed'); return; }
        load();                                  // card re-renders with Create PR / Review diff
      });
    });
    $('.b-save', card).addEventListener('click', function () { api('/api/pr/save_description', { id: id, text: $('.b-desc', card).value }).then(function (r) { msg($('.b-dmsg', card), r, 'saved ' + (r.path || '')); }); });
    $('.b-setpr', card).addEventListener('click', function () {
      var ref = $('.b-pr', card).value.trim(); if (!ref) return;
      function go(confirmHead) {
        m.textContent = 'checking PR...';
        api('/api/pr/set_number', { id: id, ref: ref, confirm_head: confirmHead }).then(function (r) {
          if (!r.ok && r.need_confirm && confirm(r.warnings.join('\n') + '\n\nUse this PR anyway?')) return go(true);
          msg(m, r, 'Fix PR set on ' + (r.cases_updated || 0) + ' cases'); if (r.ok) load();
        });
      }
      go(false);
    });
    var rf = $('.b-refresh', card); if (rf) rf.addEventListener('click', function () { act('refresh', {}, 'refreshed'); });
    var up = $('.b-update', card); if (up) up.addEventListener('click', function () { act('update', {}, 'updated; push again'); });
    var dl = $('.b-delete', card); if (dl) dl.addEventListener('click', function () { if (confirm('Delete bundle and its target branch?')) act('delete', {}, 'deleted'); });
    $$('.b-rm', card).forEach(function (a) { a.addEventListener('click', function (e) { e.preventDefault(); act('remove_file', { files: [a.getAttribute('data-slug')] }, 'removed; target rebuilt'); }); });
  });
}

// ---------------------------------------------------------------- file page
function renderFile(slug) {
  api('/api/file?slug=' + encodeURIComponent(slug)).then(function (d) {
    if (d.ok === false) { $('#app').innerHTML = '<p class="err">' + esc(d.error) + '</p>'; return; }
    FILE = d; FILE.slug = slug; drawFile();
  });
}

function evCell(c) {
  var e = c.evidence || [];
  if (!c.portability) return '';
  if (!e.length) return '<span class="ev ev-ok" title="evidence ok">ok</span>';
  var err = e.some(function (p) { return p.severity === 'ERROR'; });
  return '<span class="ev ' + (err ? 'ev-err' : 'ev-warn') + '" title="' + esc(e.map(function (p) { return p.rule + ': ' + p.message; }).join('\n')) + '">' + (err ? 'error' : 'warn') + ' (' + e.length + ')</span>';
}

function drawFile() {
  var f = FILE.file, cases = FILE.cases, RC = DATA.review_choices, PO = DATA.portability_order;
  var h = ['<a href="#/">&larr; all files</a><h2>' + esc(f.test_file) + '</h2>'];
  var pc = {}, rc = {}; cases.forEach(function (c) { pc[c.portability || ''] = (pc[c.portability || ''] || 0) + 1; rc[c.review || ''] = (rc[c.review || ''] || 0) + 1; });
  h.push('<div class="card">Status <b>' + esc(f.status) + '</b> &nbsp; branch <code>' + esc(f.agent_branch || '-') + '</code> &nbsp; commit <code>' + esc(f.commit || '-') + '</code> &nbsp; base <code>' + esc((f.base_sha || '').slice(0, 12)) + '</code>' +
    (f.bundle ? ' &nbsp; bundle <code>' + esc(f.bundle) + '</code>' : '') + '<br>Portability: ' + counts(pc, 'p', PO) + '<br>Review: ' + counts(rc, 'r', RC) + '</div>');
  if (FILE.pr) h.push(filePrCard(FILE.pr));
  h.push('<div class="card"><b>Common prompt</b> <span class="muted small">standing instruction for every case of this file on rework; a per-case comment wins on conflict</span>' +
    '<textarea id="fprompt" style="min-height:50px">' + esc(f.file_prompt || '') + '</textarea><button id="fprompt-save">Save prompt</button> <span id="fprompt-msg"></span></div>');
  h.push('<div class="card"><b>Failure summary</b> <span class="muted small">grouped by latest XPU message; click a row or a pill to filter</span><div id="groups"></div></div>');
  if (FILE.drafts.length) {
    h.push('<details id="drafts" open><summary>Issue drafts (' + FILE.drafts.length + ')</summary>');
    FILE.drafts.forEach(function (d) { h.push(draftCard(d)); });
    h.push('</details>');
  }
  h.push('<details><summary>Patch</summary><div class="row"><input type="text" class="find" placeholder="search, Enter for next"> <span class="muted small">read-only until you click Edit</span></div>' +
    '<textarea id="patch" class="code" spellcheck="false" readonly>' + esc(FILE.patch) + '</textarea>' +
    '<div class="card danger-zone small"><b>Edit the patch by hand</b> (rarely needed). Saving rebuilds this file\'s agent branch as ONE commit = base + your edited diff, ' +
    'regenerates the .patch, and invalidates earlier measurements (re-measure afterwards). If the diff does not apply, nothing changes.<br>' +
    '<button class="ghost" id="patch-edit">Edit</button> <button class="orange" id="patch-save" disabled>Apply edited patch to the agent branch</button> ' +
    '<button class="ghost" id="patch-cancel" disabled>Cancel</button> <span id="patch-msg"></span></div></details>');
  h.push('<details id="srcwrap"><summary>Source (agent branch, read-only)</summary><div class="row"><input type="text" class="find" placeholder="search, Enter for next"></div><textarea id="src" class="code" readonly spellcheck="false">loading on open...</textarea></details>');
  h.push('<div class="card" id="bulk"><b>Bulk edit</b> (applies to the checked rows) &nbsp; Set <select id="b-field"><option>review</option><option>comment</option><option>portability</option><option>reason</option><option>issue</option></select> to ' +
    '<select id="b-rev">' + RC.map(function (v) { return '<option value="' + esc(v) + '">' + esc(v || '(unset)') + '</option>'; }).join('') + '</select>' +
    '<select id="b-port" style="display:none">' + PO.map(function (v) { return '<option>' + esc(v) + '</option>'; }).join('') + '</select>' +
    '<input type="text" id="b-text" style="display:none;width:260px"> <button id="b-apply">Apply</button> <span id="b-msg"></span> &nbsp; <span id="filter-note"></span></div>');
  h.push('<h3 id="cases-head">Cases (' + cases.length + ')</h3><table id="cases"><thead><tr><th><input type="checkbox" id="c-all"></th><th>case</th><th>test</th><th>portability</th><th>reason</th><th>issue</th><th>last run</th><th>message</th><th>evidence</th><th>log</th><th>review</th><th>comment</th></tr></thead><tbody>');
  cases.forEach(function (c) {
    var iss = c.issue_number ? '<a target="_blank" href="' + esc(c.issue_url) + '">#' + esc(c.issue_number) + '</a>' : (c.draft_id ? '<a href="#" class="to-draft" data-id="' + esc(c.draft_id) + '">draft ' + esc(c.draft_id) + '</a>' : '');
    var last = c.last ? pill(c.last.status, 's') + ' <span class="muted small">' + esc(c.last.run) + '</span>' : '<span class="muted">-</span>';
    h.push('<tr data-cid="' + esc(c.case_id) + '"><td><input type="checkbox" class="c-chk"></td><td>' + esc(c.case_id) + '</td><td class="small">' + esc(c.class_name) + '<br><b>' + esc(c.test_name) + '</b>' + (c.cuda_only ? ' <span class="pill">cuda-only API</span>' : '') + '</td>' +
      '<td>' + pill(c.portability, 'p') + '</td><td class="small">' + esc(c.reason || '') + '</td><td>' + iss + (c.fix_pr ? '<br><a class="small" target="_blank" href="' + esc(c.fix_pr) + '">fix PR</a>' : '') + '</td>' +
      '<td>' + last + '</td><td class="msg">' + esc((c.message || '').slice(0, 300)) + '</td><td>' + evCell(c) + '</td>' +
      '<td>' + ((c.runs || []).filter(function (r) { return r.log; }).length ? '<a href="#" class="c-log">log</a>' : '') + '</td>' +
      '<td><select class="c-rev">' + RC.map(function (v) { return '<option value="' + esc(v) + '"' + (v === c.review ? ' selected' : '') + '>' + esc(v || '(unset)') + '</option>'; }).join('') + '</select></td>' +
      '<td class="cmt"><input type="text" class="c-cmt" placeholder="rework instruction..." value="' + esc(c.comment || '') + '"></td></tr>');
  });
  h.push('</tbody></table>');
  if (FILE.history.length) h.push('<details><summary>Rework history (' + FILE.history.length + ')</summary><pre>' + esc(FILE.history.map(function (x) { return x.at + ' [' + x.case_id + '] ' + x.outcome + (x.comment ? '\n   comment: ' + x.comment : '') + (x.prompt ? '\n   prompt: ' + x.prompt : ''); }).join('\n')) + '</pre></details>');
  $('#app').innerHTML = h.join('');
  wireFile();
}

function filePrCard(p) {
  var b = p.bundle, pr = b.pr || {}, ps = p.push_state || {};
  if (p.external) return '<div class="card bundle"><b>Already submitted:</b> ' + p.external.map(function (x) { return '<a target="_blank" href="' + esc(x.url) + '">PR #' + x.number + '</a> ' + esc(x.state) + ' <span class="muted small">' + esc(x.title) + '</span>'; }).join(' &middot; ') +
    '<div class="small muted">Fix PR is set on the covered cases (see the "fix PR" link per case). No new PR needed.</div></div>';
  if (p.in_multi) return '<div class="card bundle">Patch is part of PR bundle <code>' + esc(p.in_multi) + '</code>; submit it from the <a href="#/">index page</a>.</div>';
  var state = pr.number ? '<span class="pill s-PASSED">PR #' + pr.number + ' ' + esc(pr.state || '') + '</span>'
    : ps.pushed ? (ps.in_sync ? '<span class="pill s-PASSED">pushed to ' + esc(p.fork_repo) + '</span>'
                              : '<span class="pill s-SKIPPED">fork is behind local (' + esc(ps.remote_sha) + ')</span>')
    : '<span class="pill">not pushed yet</span>';
  // Primary actions depend on where the branch is: not pushed -> push; pushed -> share / create PR; PR -> open it.
  var actions;
  if (pr.number) {
    actions = '<a class="btnlink" target="_blank" href="' + esc(pr.url) + '">Open PR #' + pr.number + ' &#8599;</a> ' +
      '<button class="ghost" id="fp-submit">' + (ps.in_sync ? 'Push again' : 'Push update') + '</button>';
  } else if (ps.pushed) {
    actions = '<a class="btnlink" target="_blank" href="' + esc(p.compare_url || p.pr_page) + '">Create PR on GitHub &#8599;</a> ' +
      '<a class="ghost-link" target="_blank" href="' + esc(p.review_url) + '">Review diff (share) &#8599;</a> ' +
      '<button class="ghost" id="fp-copy">Copy share link</button> ' +
      '<button class="ghost" id="fp-linknow">I created it - link PR</button>' +
      (ps.in_sync ? '' : ' <button class="orange" id="fp-submit">Push latest commits</button>');
  } else {
    actions = '<button class="green" id="fp-submit">Push to ' + esc(p.fork_repo || 'fork') + '</button>';
  }
  return '<div class="card bundle" id="filepr"><b>Pull request</b> ' + state + ' <code class="small">' + esc(b.target_branch) + '</code>' +
    '<div class="row" style="margin-top:6px">' + actions + ' <span id="fp-msg"></span></div>' +
    (ps.pushed && !pr.number ? '<div class="small" style="margin-top:4px">Share: <input type="text" id="fp-share" readonly size="70" value="' + esc(p.review_url) + '"></div>' : '') +
    '<div class="small muted">You create the PR yourself on GitHub (title/description below are prefilled, draft preselected). Fix PR is filled on this file\'s cases once the PR exists.</div>' +
    '<details' + (b.description_stale ? ' open' : '') + '><summary class="small">PR title / description' + (b.description_stale ? ' <span class="warn">(your edited text differs from current verdicts)</span>' : '') + '</summary>' +
    '<input type="text" id="fp-title" style="width:100%" value="' + esc(b.title) + '">' +
    '<textarea id="fp-body" style="min-height:220px">' + esc(b.description || '') + '</textarea>' +
    '<button class="ghost" id="fp-save">Save</button> <button class="ghost" id="fp-regen" title="discard edits and rebuild from current verdicts">Regenerate from current verdicts</button> <span id="fp-smsg"></span></details>' +
    '<details><summary class="small">Manual alternatives</summary><div class="small">push: <code>' + esc(p.push) + '</code><br>' +
    'empty PR page (paste your own text): <a target="_blank" href="' + esc(p.pr_page) + '">' + esc(p.pr_page) + '</a><br>' +
    'already opened a PR? <input type="text" id="fp-ref" size="36" value="' + esc(pr.url || '') + '"> <button class="ghost" id="fp-set">Set PR &amp; fill Fix PR</button></div></details></div>';
}

function wireFilePr(slug) {
  var card = $('#filepr'); if (!card) return;
  var cp = $('#fp-copy');
  if (cp) cp.addEventListener('click', function () {
    var inp = $('#fp-share'); if (inp) { inp.select(); }
    (navigator.clipboard ? navigator.clipboard.writeText(FILE.pr.review_url) : Promise.reject()).then(function () { cp.textContent = 'copied'; },
      function () { document.execCommand && document.execCommand('copy'); cp.textContent = 'selected - press Ctrl+C'; });
  });
  var sb = $('#fp-submit');
  if (sb) sb.addEventListener('click', function () {
    var update = !!(FILE.pr.bundle.pr || {}).number;
    sb.disabled = true; $('#fp-msg').textContent = 'pushing ' + FILE.pr.bundle.target_branch + ' to your fork (can take a minute)...';
    api('/api/pr/file_submit', { slug: slug, title: $('#fp-title').value, text: $('#fp-body').value }).then(function (r) {
      sb.disabled = false;
      if (!r.ok || r.dry_run) { msg($('#fp-msg'), r, 'dry-run: nothing pushed'); return; }
      if (update || r.linked) { renderFile(slug); return; }
      renderFile(slug);                       // card now shows Create PR / Review diff / Copy
    });
  });
  var ln = $('#fp-linknow');
  if (ln) {
    var poll = setInterval(function () { if (!$('#fp-linknow')) { clearInterval(poll); return; } api('/api/pr/file_link', { slug: slug }).then(function (x) { if (x.found) { clearInterval(poll); renderFile(slug); } }); }, 15000);
    setTimeout(function () { clearInterval(poll); }, 30 * 60000);
    ln.addEventListener('click', function () {
      api('/api/pr/file_link', { slug: slug }).then(function (x) { if (x.found) renderFile(slug); else $('#fp-msg').innerHTML = '<span class="warn">no open PR from this branch yet</span>'; });
    });
  }
  $('#fp-save').addEventListener('click', function () {
    api('/api/pr/file_save', { slug: slug, title: $('#fp-title').value, text: $('#fp-body').value }).then(function (r) { msg($('#fp-smsg'), r, 'saved'); if (r.ok) renderFile(slug); });
  });
  $('#fp-regen').addEventListener('click', function () {
    if (!confirm('Discard the edited description and rebuild it from the current verdicts?')) return;
    api('/api/pr/file_regen', { slug: slug }).then(function (r) { msg($('#fp-smsg'), r, 'regenerated'); if (r.ok) renderFile(slug); });
  });
  $('#fp-set').addEventListener('click', function () {
    var ref = $('#fp-ref').value.trim(); if (!ref) return;
    function go(confirmHead) {
      $('#fp-msg').textContent = 'checking PR...';
      api('/api/pr/file_set_number', { slug: slug, ref: ref, confirm_head: confirmHead }).then(function (r) {
        if (!r.ok && r.need_confirm && confirm(r.warnings.join('\n') + '\n\nUse this PR anyway?')) return go(true);
        msg($('#fp-msg'), r, 'Fix PR set on ' + (r.cases_updated || 0) + ' cases'); if (r.ok) renderFile(slug);
      });
    }
    go(false);
  });
}

function draftCard(d) {
  var filed = d.number ? ' <span class="ok">filed as <a target="_blank" href="' + esc(d.url) + '">#' + d.number + '</a></span>' : '';
  return '<div class="draft" data-id="' + esc(d.id) + '"><b>' + esc(d.id) + '</b> ' + pill(d.verdict, 'p') + filed + ' <span class="muted small">cases: ' + esc((d.cases || []).join(', ')) + (d.placeholder ? ' &middot; placeholder <code>' + esc(d.placeholder) + '</code>' : '') + '</span>' +
    '<label class="small">Title</label><input type="text" class="d-title" value="' + esc(d.title) + '"><label class="small">Labels</label><input type="text" class="d-labels" value="' + esc((d.labels || []).join(', ')) + '">' +
    '<label class="small">Body</label><textarea class="d-body">' + esc(d.body) + '</textarea>' +
    (d.number ? '' : '<div class="row"><button class="green d-submit">Submit to ' + esc(DATA.repo) + '</button> <a class="d-manual" target="_blank" href="#">open prefilled GitHub page</a> ' +
      '<input type="text" class="d-ref" placeholder="filed manually? number or URL"> <button class="ghost d-backfill">Backfill</button> <button class="orange d-reject">Reject and need work</button> <span class="d-msg"></span></div>') + '</div>';
}

function wireFile() {
  wireFilePr(FILE.slug);
  var slug = FILE.slug, byCid = {}; FILE.cases.forEach(function (c) { byCid[c.case_id] = c; });
  var rows = $$('#cases tbody tr');
  function checked() { return rows.filter(function (tr) { return tr.style.display !== 'none' && $('.c-chk', tr).checked; }).map(function (tr) { return tr.getAttribute('data-cid'); }); }
  function count() { var n = checked().length; $('#cases-head').textContent = 'Cases (' + (n ? n + ' selected / ' : '') + FILE.cases.length + ')'; }
  rows.forEach(function (tr) {
    var cid = tr.getAttribute('data-cid');
    $('.c-chk', tr).addEventListener('change', count);
    $('.c-rev', tr).addEventListener('change', function (e) { api('/api/set_review', { slug: slug, case_ids: [cid], value: e.target.value }); });
    $('.c-cmt', tr).addEventListener('change', function (e) { api('/api/set_review', { slug: slug, case_ids: [cid], comment: e.target.value }); });
    var lg = $('.c-log', tr);
    if (lg) lg.addEventListener('click', function (e) { e.preventDefault(); toggleLog(tr, byCid[cid]); });
  });
  $('#c-all').addEventListener('change', function (e) { rows.forEach(function (tr) { if (tr.style.display !== 'none') $('.c-chk', tr).checked = e.target.checked; }); count(); });
  $('#fprompt-save').addEventListener('click', function () { api('/api/set_file_prompt', { slug: slug, prompt: $('#fprompt').value }).then(function (r) { msg($('#fprompt-msg'), r, 'saved (logged to review_notes.md)'); }); });
  // groups + filtering
  function filter(set, label) {
    rows.forEach(function (tr) { var on = !set || set[tr.getAttribute('data-cid')]; tr.style.display = on ? '' : 'none'; $('.c-chk', tr).checked = !!set && !!on; var nx = tr.nextElementSibling; if (nx && nx.classList.contains('logrow')) nx.style.display = tr.style.display; });
    $('#filter-note').innerHTML = set ? 'filtered to ' + label + ' &middot; <a href="#" id="unfilter">show all</a>' : '';
    if (set) $('#unfilter').addEventListener('click', function (e) { e.preventDefault(); filter(null); $$('#groups tr.active').forEach(function (t) { t.classList.remove('active'); }); });
    count();
  }
  var g = {}, order = [];
  FILE.cases.forEach(function (c) { var k = c.message || '(no message)'; if (!g[k]) { g[k] = []; order.push(k); } g[k].push(c); });
  order.sort(function (a, b) { return g[b].length - g[a].length; });
  if (order.length > 5) { var other = []; order.slice(4).forEach(function (k) { other = other.concat(g[k]); }); g['Other (' + (order.length - 4) + ' messages)'] = other; order = order.slice(0, 4).concat(['Other (' + (order.length - 4) + ' messages)']); }
  var gh = ['<table><thead><tr><th>Message</th><th>Cases</th><th>Portability</th><th>Issue / draft</th></tr></thead><tbody>'];
  order.forEach(function (k, i) {
    var cs = g[k], ports = {}, refs = {};
    cs.forEach(function (c) { ports[c.portability || ''] = 1; if (c.issue_number) refs['#' + c.issue_number] = c.issue_url; else if (c.draft_id) refs['draft:' + c.draft_id] = ''; });
    gh.push('<tr class="clickable" data-gi="' + i + '"><td class="msg">' + esc(k.slice(0, 200)) + '</td><td>' + cs.length + '</td><td>' + Object.keys(ports).map(function (p) { return pill(p, 'p', ' data-port="' + esc(p) + '" data-gi="' + i + '"'); }).join(' ') + '</td><td>' +
      Object.keys(refs).map(function (r) { return r.indexOf('draft:') === 0 ? '<a href="#" class="to-draft" data-id="' + esc(r.slice(6)) + '">' + esc(r) + '</a>' : '<a target="_blank" href="' + esc(refs[r]) + '">' + esc(r) + '</a>'; }).join(' ') + '</td></tr>');
  });
  gh.push('</tbody></table>');
  $('#groups').innerHTML = gh.join('');
  $$('#groups tr[data-gi]').forEach(function (tr) {
    tr.addEventListener('click', function (e) {
      if (e.target.closest('a')) return;
      var cs = g[order[+tr.getAttribute('data-gi')]], port = e.target.getAttribute('data-port'), set = {};
      cs.forEach(function (c) { if (port === null || (c.portability || '') === port) set[c.case_id] = 1; });
      $$('#groups tr.active').forEach(function (t) { t.classList.remove('active'); }); tr.classList.add('active');
      filter(set, '"' + esc(order[+tr.getAttribute('data-gi')].slice(0, 60)) + '"' + (port !== null ? ' / ' + esc(port || 'unset') : ''));
    });
  });
  $$('.to-draft').forEach(function (a) { a.addEventListener('click', function (e) { e.preventDefault(); var w = $('#drafts'); if (w) w.open = true; var d = $('.draft[data-id="' + a.getAttribute('data-id') + '"]'); if (d) { d.scrollIntoView({ behavior: 'smooth', block: 'center' }); d.classList.add('flash'); setTimeout(function () { d.classList.remove('flash'); }, 1500); } }); });
  // bulk
  function syncBulk() { var f = $('#b-field').value; $('#b-rev').style.display = f === 'review' ? '' : 'none'; $('#b-port').style.display = f === 'portability' ? '' : 'none'; $('#b-text').style.display = (f === 'review' || f === 'portability') ? 'none' : ''; }
  $('#b-field').addEventListener('change', syncBulk); syncBulk();
  $('#b-apply').addEventListener('click', function () {
    var f = $('#b-field').value, ids = checked(), m = $('#b-msg');
    if (!ids.length) { m.innerHTML = '<span class="err">no rows checked</span>'; return; }
    var p = f === 'review' ? api('/api/set_review', { slug: slug, case_ids: ids, value: $('#b-rev').value })
      : f === 'comment' ? api('/api/set_review', { slug: slug, case_ids: ids, comment: $('#b-text').value })
        : api('/api/set_case_fields', { slug: slug, case_ids: ids, field: f, value: f === 'portability' ? $('#b-port').value : $('#b-text').value });
    p.then(function (r) { msg(m, r, 'updated ' + (r.updated || 0)); if (r.ok) renderFile(slug); });
  });
  // code panels
  $$('.find').forEach(function (inp) {
    inp.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return; e.preventDefault();
      var ta = inp.closest('details').querySelector('textarea'), hay = ta.value.toLowerCase(), n = inp.value.toLowerCase();
      var i = hay.indexOf(n, ta.selectionEnd || 0); if (i < 0) i = hay.indexOf(n); if (i < 0) return;
      ta.focus(); ta.setSelectionRange(i, i + n.length); ta.scrollTop = Math.max(0, (ta.value.slice(0, i).split('\n').length - 3) * 17);
    });
  });
  $('#srcwrap').addEventListener('toggle', function () { var ta = $('#src'); if (ta.dataset.loaded) return; api('/api/text?kind=source&slug=' + encodeURIComponent(slug)).then(function (r) { ta.value = r.text || r.error || ''; ta.dataset.loaded = 1; }); });
  var orig = FILE.patch;
  $('#patch-edit').addEventListener('click', function () { $('#patch').readOnly = false; $('#patch').focus(); $('#patch-save').disabled = false; $('#patch-cancel').disabled = false; $('#patch-edit').disabled = true; });
  $('#patch-cancel').addEventListener('click', function () { $('#patch').value = orig; $('#patch').readOnly = true; $('#patch-save').disabled = true; $('#patch-cancel').disabled = true; $('#patch-edit').disabled = false; $('#patch-msg').textContent = ''; });
  $('#patch-save').addEventListener('click', function () {
    var m = $('#patch-msg');
    if ($('#patch').value === orig) { m.innerHTML = '<span class="muted">no changes</span>'; return; }
    if (!confirm('Rebuild ' + (FILE.file.agent_branch || 'the agent branch') + ' from base with this edited diff (one commit)? Earlier measurements become stale.')) return;
    m.textContent = 're-applying...';
    api('/api/save_patch', { slug: slug, text: $('#patch').value }).then(function (r) { msg(m, r, 'applied as ' + (r.commit || '') + '; re-measure the file'); if (r.ok) renderFile(slug); });
  });
  // drafts
  $$('.draft').forEach(function (card) {
    var id = card.getAttribute('data-id'), m = $('.d-msg', card);
    function vals() { return { title: $('.d-title', card).value, body: $('.d-body', card).value, labels: $('.d-labels', card).value.split(',').map(function (s) { return s.trim(); }).filter(Boolean) }; }
    var man = $('.d-manual', card);
    if (man) { var sync = function () { var v = vals(); man.href = 'https://github.com/' + DATA.repo + '/issues/new?title=' + encodeURIComponent(v.title) + '&body=' + encodeURIComponent(v.body) + '&labels=' + encodeURIComponent(v.labels.join(',')); }; sync(); card.addEventListener('input', sync); }
    var sb = $('.d-submit', card);
    if (sb) sb.addEventListener('click', function () {
      if (!confirm('File this issue on ' + DATA.repo + ' now?')) return;
      sb.disabled = true; m.textContent = 'filing...';
      api('/api/submit_issue', Object.assign({ slug: slug, id: id }, vals())).then(function (r) { sb.disabled = false; msg(m, r, r.dry_run ? 'dry-run (not filed)' : 'filed #' + r.number); if (r.ok && !r.dry_run) renderFile(slug); });
    });
    var bf = $('.d-backfill', card);
    if (bf) bf.addEventListener('click', function () { var ref = $('.d-ref', card).value.trim(); if (!ref) return; api('/api/backfill_issue', { slug: slug, id: id, issue: ref }).then(function (r) { msg(m, r, 'backfilled #' + r.number); if (r.ok) renderFile(slug); }); });
    var rj = $('.d-reject', card);
    if (rj) rj.addEventListener('click', function () { api('/api/reject_draft', { slug: slug, id: id }).then(function (r) { msg(m, r, r.updated + ' case(s) -> Needs work; ' + r.reverted_lines + ' gate line(s) reverted'); if (r.ok) renderFile(slug); }); });
    var ref = $('.d-ref', card);
    if (ref && !ref.value) api('/api/match_issue', { title: vals().title }).then(function (r) { if (r.found) { ref.value = r.url; ref.title = 'matched existing issue (' + r.state + '); click Backfill to use it'; } });
  });
}

// Per-case evidence: one line per run (latest first), the exact pytest node id, and the
// matching log lines. Imported (legacy) runs are labelled so they are not mistaken for
// the current evidence.
function toggleLog(tr, c) {
  var nx = tr.nextElementSibling;
  if (nx && nx.classList.contains('logrow')) { nx.parentNode.removeChild(nx); return; }
  var row = document.createElement('tr'); row.className = 'logrow';
  var runs = (c.runs || []).filter(function (r) { return r.log; }).slice().reverse();
  var label = { gate_removed: 'gate removed', rework: 'final code', initial: 'initial', imported: 'imported (legacy, not current evidence)' };
  row.innerHTML = '<td colspan="12">' + runs.map(function (r, i) {
    return '<div class="small' + (r.run === 'imported' ? ' muted' : '') + '"><b>' + esc(label[r.run] || r.run) + '</b> ' + pill(r.status, 's') + ' ' + esc(r.at || '') +
      ' &middot; node <code>' + esc(r.nodeid) + '</code> &middot; <code>' + esc(r.log) + '</code> ' +
      '<a href="#" data-i="' + i + '">show matching log lines</a></div>' +
      (r.error_excerpt || r.reason ? '<pre>' + esc(r.error_excerpt || r.reason) + '</pre>' : '');
  }).join('') + '</td>';
  tr.parentNode.insertBefore(row, tr.nextSibling);
  $$('a[data-i]', row).forEach(function (a) {
    a.addEventListener('click', function (e) {
      e.preventDefault(); var r = runs[+a.getAttribute('data-i')];
      api('/api/text?kind=log&slug=' + encodeURIComponent(FILE.slug) + '&path=' + encodeURIComponent(r.log)).then(function (x) {
        var tid = r.nodeid.split('::').pop(), lines = (x.text || '').split('\n'), keep = [lines[0]];
        lines.forEach(function (ln, i) {
          if (i && ln.indexOf(tid) >= 0) keep.push(ln);
          if (/^_{3,} .* _{3,}$/.test(ln) && ln.indexOf(tid.replace(/^test_/, '')) >= 0) keep.push(lines.slice(i, i + 40).join('\n'));
        });
        var pre = document.createElement('pre');
        pre.textContent = (keep.length > 1 ? keep.join('\n') : lines[0] + '\n(no line mentions ' + tid + ' in this log)') +
          '\n\n---- full log ----\n' + (x.text || x.error);
        a.parentNode.after(pre); a.remove();
      });
    });
  });
}

// ---------------------------------------------------------------- boot
function route() {
  var m = location.hash.match(/^#\/file\/(.+)$/);
  if (m) { LAST_FILE = decodeURIComponent(m[1]); sessionStorage.setItem('lastFile', LAST_FILE); renderFile(LAST_FILE); return; }
  // back on the index: reload data so edits made on the file page show up, then return
  // to the row of the file just reviewed
  BACK_TO_INDEX = !!LAST_FILE;
  api('/api/data').then(function (d) { DATA = d; renderIndex(); });
}
function load() { return api('/api/data').then(function (d) { DATA = d; if (/^#\/file\//.test(location.hash)) route(); else renderIndex(); }).catch(function (e) { $('#app').innerHTML = '<p class="err">failed to load /api/data: ' + esc(e) + '</p>'; }); }
window.addEventListener('hashchange', route);
window.addEventListener('scroll', function () { if (!/^#\/file\//.test(location.hash)) sessionStorage.setItem('idxScroll', String(window.scrollY)); });
document.addEventListener('DOMContentLoaded', function () {
  $('#reload').addEventListener('click', load);
  $('#export').addEventListener('click', function () { var m = $('#export-msg'); m.textContent = 'exporting...'; api('/api/export_csv', {}).then(function (r) { msg(m, r, r.path + ' (' + r.pr_rows + ' PR rows' + (r.fix_pr_conflicts && r.fix_pr_conflicts.length ? ', ' + r.fix_pr_conflicts.length + ' fix_pr conflicts kept' : '') + ')'); }); });
  load();
});
