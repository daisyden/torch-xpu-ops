// Test the reverse-lookup feature: clicking a line in pane 2 (base file) or
// pane 3 (head file) must locate the corresponding line in the *other* file
// view and in the PR diff (pane 1).
//
// usage: node dev/test_reverse.js <fixture-dir>
const fs = require('fs');
const path = require('path');
// Locate jsdom without hardcoding a path: honour NODE_PATH, then look in the
// usual places.  Install with `npm install jsdom` in this directory if missing.
function loadJsdom() {
  const candidates = [
    'jsdom',
    ...(process.env.NODE_PATH ? process.env.NODE_PATH.split(':').filter(Boolean).map((p) => p + '/jsdom') : []),
    __dirname + '/../node_modules/jsdom',
    __dirname + '/../../node_modules/jsdom',
    process.cwd() + '/node_modules/jsdom',
  ];
  for (const c of candidates) {
    try { return require(c); } catch (e) { /* try next */ }
  }
  console.error('jsdom not found. Run:  npm install jsdom');
  process.exit(3);
}
const { JSDOM, VirtualConsole } = loadJsdom();

const DIR = '/home/daisyden/opencode/refactor-review/static';
const FIX = process.argv[2];
const fx = (n) => JSON.parse(fs.readFileSync(path.join(FIX, n), 'utf8'));
const PR = fx('pr.json'), FILE = fx('file.json'), LINEMAP = fx('linemap.json'), RESOLVE = fx('resolve.json');

let fail = 0;
const errors = [];
const check = (name, cond, extra) => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${!cond && extra ? '  -> ' + extra : ''}`);
  if (!cond) fail++;
};

const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push(String(e.message || e)));

const dom = new JSDOM(fs.readFileSync(`${DIR}/index.html`, 'utf8'), {
  runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc,
  url: 'http://localhost:8765/?pr=1',
});
const { window } = dom, doc = window.document;
doc.head.appendChild(Object.assign(doc.createElement('style'),
  { textContent: fs.readFileSync(`${DIR}/app.css`, 'utf8') }));

for (const id of ['diffBody', 'baseBody', 'headBody']) {
  Object.defineProperty(doc.getElementById(id), 'clientHeight', { value: 600, configurable: true });
}
Object.defineProperty(window.HTMLElement.prototype, 'offsetTop', {
  get() {
    const d = this.dataset || {};
    // +1 so that row 0 is not at offset 0, which would make "did it scroll?"
    // untestable for the very first row
    const idx = d.n !== undefined ? Number(d.n) : (d.i !== undefined ? Number(d.i) : 0);
    return (idx + 1) * 18;
  },
  configurable: true,
});

// record what the client asks /api/resolve for, so we can assert the side/line
const resolveCalls = [];
window.fetch = async (url) => {
  const u = new window.URL(url, 'http://localhost:8765');
  const r = u.pathname;
  let body;
  if (r === '/api/pr') body = PR;
  else if (r === '/api/file') body = FILE;
  else if (r === '/api/linemap') body = LINEMAP;
  else if (r === '/api/resolve') {
    const side = u.searchParams.get('side');
    const line = Number(u.searchParams.get('line'));
    resolveCalls.push({ side, line });
    // Mirror the real server: the focus row is the clicked line paired with its
    // counterpart from the line map.  A fixed fixture would otherwise make the
    // client look wrong when it is merely being handed inconsistent data.
    const lm = LINEMAP[side];
    const other = lm.o[line - 1] || null;
    const bno = side === 'base' ? line : other;
    const hno = side === 'base' ? other : line;
    body = {
      ...RESOLVE, side, lineno: line, mode: 'line', focus: 0,
      rows: [{ base_no: bno, head_no: hno, base: '', head: '',
               base_seg: [], head_seg: [], verdict: 'identical' }],
      base_unit: null, head_unit: null,
    };
  } else return { ok: false, status: 404, json: async () => ({ error: 'x' }) };
  return { ok: true, status: 200, json: async () => body };
};

window.eval(fs.readFileSync(`${DIR}/app.js`, 'utf8'));
const settle = async (n = 40) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 10)); };

(async () => {
  await settle(60);
  check('no script errors on load', errors.length === 0, errors.join(' | '));
  check('pane 2 built', doc.querySelectorAll('#baseBody tr[data-n]').length === LINEMAP.base.text.length);
  check('pane 3 built', doc.querySelectorAll('#headBody tr[data-n]').length === LINEMAP.head.text.length);

  // every diff row must expose both revisions' line numbers for reverse lookup
  const ctx = doc.querySelector('#diffBody tr:not(.clickable)[data-b]');
  if (ctx) {
    check('context diff rows carry both line numbers',
          ctx.dataset.b !== '' && ctx.dataset.h !== '', `b=${ctx.dataset.b} h=${ctx.dataset.h}`);
  }

  const clickFileLine = async (paneId, lineno) => {
    const tr = doc.querySelector(`#${paneId} tr[data-n="${lineno}"]`);
    if (!tr) return null;
    resolveCalls.length = 0;
    errors.length = 0;
    tr.querySelector('td.txt').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    await settle(30);
    return tr;
  };

  // ---- pick a base line that has a known counterpart --------------------- //
  let baseLine = null, wantHead = null;
  for (let i = 0; i < LINEMAP.base.o.length; i++) {
    const o = LINEMAP.base.o[i];
    // choose a line that is inside a hunk, so the diff has a row for it
    if (o && doc.querySelector(`#diffBody tr[data-b="${i + 1}"]`)) {
      baseLine = i + 1; wantHead = o; break;
    }
  }
  check('found a base line with a counterpart and a diff row', baseLine !== null,
        `baseLine=${baseLine}`);

  if (baseLine) {
    const tr = await clickFileLine('baseBody', baseLine);
    check('clicking pane 2 raised no errors', errors.length === 0, errors.join(' | '));
    check('pane 2 marks the clicked line',
          tr && tr.classList.contains('focusrow'), tr ? tr.className : 'none');

    const hFocus = doc.querySelector('#headBody tr.focusrow');
    check('pane 3 located the counterpart line', !!hFocus && hFocus.dataset.n === String(wantHead),
          hFocus ? `${hFocus.dataset.n} vs ${wantHead}` : 'no focus in pane 3');

    const active = doc.querySelector('#diffBody tr.active');
    check('pane 1 located the line', !!active,
          active ? '' : 'no active row in diff');
    if (active) {
      check('pane 1 selected the row for that base line',
            active.dataset.b === String(baseLine), `${active.dataset.b} vs ${baseLine}`);
    }
    // A row near the top of the file legitimately clamps to scrollTop 0, so
    // assert the row is within the viewport rather than that we scrolled.
    const dBody = doc.getElementById('diffBody');
    const rowTop = active ? active.offsetTop : 0;
    check('pane 1 brought the located row into view',
          rowTop >= dBody.scrollTop && rowTop <= dBody.scrollTop + dBody.clientHeight,
          `row at ${rowTop}, view ${dBody.scrollTop}..${dBody.scrollTop + dBody.clientHeight}`);
    check('a resolve request was issued for the base side',
          resolveCalls.some((c) => c.side === 'base' && c.line === baseLine),
          JSON.stringify(resolveCalls));
    check('breadcrumbs stay populated',
          doc.getElementById('baseCls').textContent.trim().length > 0 &&
          doc.getElementById('headCls').textContent.trim().length > 0);
  }

  // ---- the same in reverse: click pane 3 -------------------------------- //
  let headLine = null, wantBase = null;
  for (let i = 0; i < LINEMAP.head.o.length; i++) {
    const o = LINEMAP.head.o[i];
    if (o && doc.querySelector(`#diffBody tr[data-h="${i + 1}"]`)) {
      headLine = i + 1; wantBase = o; break;
    }
  }
  if (headLine) {
    const tr = await clickFileLine('headBody', headLine);
    check('clicking pane 3 raised no errors', errors.length === 0, errors.join(' | '));
    check('pane 3 marks the clicked line', tr && tr.classList.contains('focusrow'));
    const bFocus = doc.querySelector('#baseBody tr.focusrow');
    check('pane 2 located the counterpart line', !!bFocus && bFocus.dataset.n === String(wantBase),
          bFocus ? `${bFocus.dataset.n} vs ${wantBase}` : 'no focus in pane 2');
    const active = doc.querySelector('#diffBody tr.active');
    check('pane 1 selected the row for that head line',
          !!active && active.dataset.h === String(headLine),
          active ? `${active.dataset.h} vs ${headLine}` : 'none');
    check('a resolve request was issued for the head side',
          resolveCalls.some((c) => c.side === 'head' && c.line === headLine),
          JSON.stringify(resolveCalls));
  }

  // ---- a line with no counterpart must not break anything ---------------- //
  let lonely = null;
  for (let i = 0; i < LINEMAP.base.o.length; i++) {
    if (!LINEMAP.base.o[i] && LINEMAP.base.text[i].trim()) { lonely = i + 1; break; }
  }
  if (lonely) {
    const tr = await clickFileLine('baseBody', lonely);
    check('clicking a line with no counterpart raises no errors', errors.length === 0, errors.join(' | '));
    check('the clicked line is still marked', tr && tr.classList.contains('focusrow'));
  } else {
    console.log('SKIP  no unmatched base line in this fixture');
  }

  // ---- a hidden row must be revealed, not silently skipped -------------- //
  doc.getElementById('hideBenign').checked = true;
  doc.getElementById('hideBenign').dispatchEvent(new window.Event('change'));
  await settle(10);
  // a hidden row may be a deletion (has data-b) or an addition (has data-h)
  const hiddenRows = Array.from(doc.querySelectorAll('#diffBody tr.clickable.hidden'));
  const hb = hiddenRows.find((r) => r.dataset.b);
  const hh = hiddenRows.find((r) => r.dataset.h);
  if (hb || hh) {
    const pane = hb ? 'baseBody' : 'headBody';
    const no = Number(hb ? hb.dataset.b : hh.dataset.h);
    check('a filtered-out row exists to test with', doc.getElementById('hideBenign').checked);
    await clickFileLine(pane, no);
    const act = doc.querySelector('#diffBody tr.active');
    check('locating a filtered-out line reveals it',
          !!act && !act.classList.contains('hidden'), act ? act.className : 'none');
    check('the hide-benign filter was turned off to reveal it',
          doc.getElementById('hideBenign').checked === false);
  } else { console.log('SKIP  no hidden rows to test with'); }

  console.log(fail ? `\n${fail} FAILURE(S)` : '\nALL REVERSE-LOOKUP TESTS PASS');
  process.exit(fail ? 1 : 0);
})();
