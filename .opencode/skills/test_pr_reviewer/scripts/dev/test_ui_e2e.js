// End-to-end UI test that loads the REAL app.js into a DOM and drives it with
// real API responses.  The earlier test_fileview.js re-implemented the render
// logic, so it could not catch a function deleted from app.js (segHtml).  This
// one executes the shipped file, so such a break fails loudly.
//
// usage: node dev/test_ui_e2e.js <fixture-dir>
//   fixture-dir must contain pr.json, file.json, linemap.json, resolve.json

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

const PR = fx('pr.json');
const FILE = fx('file.json');
const LINEMAP = fx('linemap.json');
const RESOLVE = fx('resolve.json');

let fail = 0;
const errors = [];
const check = (name, cond, extra) => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${!cond && extra ? '  -> ' + extra : ''}`);
  if (!cond) fail++;
};

// capture *any* script error - that is the class of bug we are hunting
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push(String(e.message || e)));
vc.on('error', (m) => errors.push('console.error: ' + m));

const dom = new JSDOM(fs.readFileSync(`${DIR}/index.html`, 'utf8'), {
  runScripts: 'outside-only',
  pretendToBeVisual: true,
  virtualConsole: vc,
  url: 'http://localhost:8765/?pr=189250',
});
const { window } = dom;
const doc = window.document;

const style = doc.createElement('style');
style.textContent = fs.readFileSync(`${DIR}/app.css`, 'utf8');
doc.head.appendChild(style);

// jsdom lacks layout; give the panes plausible geometry so scroll maths works
for (const id of ['diffBody', 'baseBody', 'headBody']) {
  Object.defineProperty(doc.getElementById(id), 'clientHeight', { value: 600, configurable: true });
}
Object.defineProperty(window.HTMLElement.prototype, 'offsetTop', {
  get() { return Number(this.dataset && this.dataset.n ? this.dataset.n : 0) * 18; },
  configurable: true,
});

// stub fetch with the recorded API responses
window.fetch = async (url) => {
  const u = new window.URL(url, 'http://localhost:8765');
  const route = u.pathname;
  let body;
  if (route === '/api/pr') body = PR;
  else if (route === '/api/file') body = FILE;
  else if (route === '/api/linemap') body = LINEMAP;
  else if (route === '/api/resolve') body = RESOLVE;
  else return { ok: false, status: 404, json: async () => ({ error: 'no route' }) };
  return { ok: true, status: 200, json: async () => body };
};

// run the real application code
window.eval(fs.readFileSync(`${DIR}/app.js`, 'utf8'));

(async () => {
  // let init()'s async chain settle
  for (let i = 0; i < 60; i++) await new Promise((r) => setTimeout(r, 10));

  check('no script errors during load', errors.length === 0, errors.join(' | '));

  const diffRows = doc.querySelectorAll('#diffBody tr');
  check('pane 1 rendered diff rows', diffRows.length > 0, String(diffRows.length));

  const baseRows = doc.querySelectorAll('#baseBody tr');
  const headRows = doc.querySelectorAll('#headBody tr');
  check('pane 2 rendered the whole base file',
        baseRows.length === LINEMAP.base.text.length,
        `${baseRows.length} vs ${LINEMAP.base.text.length}`);
  check('pane 3 rendered the whole head file',
        headRows.length === LINEMAP.head.text.length,
        `${headRows.length} vs ${LINEMAP.head.text.length}`);

  // panes must contain real text, not be blank
  const baseText = doc.getElementById('baseBody').textContent.trim();
  const headText = doc.getElementById('headBody').textContent.trim();
  check('pane 2 is not empty', baseText.length > 100, `${baseText.length} chars`);
  check('pane 3 is not empty', headText.length > 100, `${headText.length} chars`);

  // word-level marks must have rendered (this is what segHtml produces)
  const marks = doc.querySelectorAll('#headBody mark.w').length
              + doc.querySelectorAll('#baseBody mark.w').length;
  check('word-level marks rendered via segHtml', marks > 0, String(marks));

  // --- now simulate the user clicking the reported line ------------------- //
  const target = RESOLVE.lineno;
  const side = RESOLVE.side;
  const tr = doc.querySelector(`#diffBody tr[data-side="${side}"][data-line="${target}"]`);
  check(`diff row for ${side}:${target} exists and is clickable`,
        !!tr && tr.classList.contains('clickable'));

  if (tr) {
    errors.length = 0;
    tr.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    for (let i = 0; i < 40; i++) await new Promise((r) => setTimeout(r, 10));

    check('no script errors after clicking', errors.length === 0, errors.join(' | '));

    const bFocus = doc.querySelector('#baseBody tr.focusrow');
    const hFocus = doc.querySelector('#headBody tr.focusrow');
    check('pane 2 highlighted a line', !!bFocus,
          bFocus ? '' : 'no tr.focusrow in baseBody');
    check('pane 3 highlighted a line', !!hFocus,
          hFocus ? '' : 'no tr.focusrow in headBody');

    if (bFocus && hFocus) {
      const row = RESOLVE.rows[RESOLVE.focus];
      // A clicked line often has no counterpart (pure insertion/deletion); the
      // app then focuses the nearest aligned line, so only assert an exact
      // match when the row itself is aligned on both sides.
      if (row.base_no !== null) {
        check('pane 2 focused the expected base line',
              bFocus.dataset.n === String(row.base_no), `${bFocus.dataset.n} vs ${row.base_no}`);
      } else {
        check('pane 2 focused a nearby line', Number(bFocus.dataset.n) > 0, bFocus.dataset.n);
      }
      if (row.head_no !== null) {
        check('pane 3 focused the expected head line',
              hFocus.dataset.n === String(row.head_no), `${hFocus.dataset.n} vs ${row.head_no}`);
      } else {
        check('pane 3 focused a nearby line', Number(hFocus.dataset.n) > 0, hFocus.dataset.n);
      }
    }

    // the matched method is outlined only in `unit` mode; `line` mode has no unit
    const bUnit = doc.querySelectorAll('#baseBody tr.inunit').length;
    const hUnit = doc.querySelectorAll('#headBody tr.inunit').length;
    if (RESOLVE.mode === 'unit') {
      const expB = RESOLVE.base_unit.end - RESOLVE.base_unit.start + 1;
      const expH = RESOLVE.head_unit.end - RESOLVE.head_unit.start + 1;
      check('pane 2 outlined the matched method', bUnit === expB, `${bUnit} vs ${expB}`);
      check('pane 3 outlined the matched method', hUnit === expH, `${hUnit} vs ${expH}`);
    } else {
      check('no method outline outside unit mode', bUnit === 0 && hUnit === 0, `${bUnit}/${hUnit}`);
    }

    // both panes must have scrolled to the match, not sit at the top
    const bTop = doc.getElementById('baseBody').scrollTop;
    const hTop = doc.getElementById('headBody').scrollTop;
    check('pane 2 scrolled to the match', bTop > 0, String(bTop));
    check('pane 3 scrolled to the match', hTop > 0, String(hTop));

    // the bottom bar must describe the correspondence
    const mb = doc.getElementById('matchbar');
    check('match bar is visible', mb.hidden === false);
    const info = doc.getElementById('mbInfo').textContent;
    if (RESOLVE.mode === 'unit') {
      check('match bar names both units',
            info.includes(RESOLVE.base_unit.name) && info.includes(RESOLVE.head_unit.qualname.split('.')[0]),
            info.slice(0, 120));
    } else {
      check('match bar explains a non-unit line', info.trim().length > 0, info.slice(0, 120));
    }

    // --- class breadcrumbs must always be shown -------------------------- //
    const bCls = doc.getElementById('baseCls');
    const hCls = doc.getElementById('headCls');

    // Only assert exact class names for real units.  In `line` mode the units
    // are synthetic "region" placeholders whose `cls` is a positional guess, so
    // the breadcrumb intentionally falls back to a line-range lookup.
    if (RESOLVE.mode === 'unit') {
      const wantB = RESOLVE.base_unit.cls ? RESOLVE.base_unit.cls.split('.').pop() : null;
      const wantH = RESOLVE.head_unit.cls ? RESOLVE.head_unit.cls.split('.').pop() : null;

      if (wantB) {
        check(`pane 2 shows base class "${wantB}"`,
              bCls.querySelector('.cls') && bCls.querySelector('.cls').textContent === wantB,
              bCls.textContent);
      } else {
        check('pane 2 marks a module-level unit', bCls.textContent.includes('module'), bCls.textContent);
      }
      if (wantH) {
        check(`pane 3 shows head class "${wantH}"`,
              hCls.querySelector('.cls') && hCls.querySelector('.cls').textContent === wantH,
              hCls.textContent);
      }
      if (wantB && wantH && wantB !== wantH) {
        check('the two panes show *different* class names (the split is visible)',
              bCls.querySelector('.cls').textContent !== hCls.querySelector('.cls').textContent,
              `${bCls.textContent} / ${hCls.textContent}`);
      }
      if (RESOLVE.base_unit.kind === 'method') {
        check('pane 2 breadcrumb includes the method name',
              bCls.textContent.includes(RESOLVE.base_unit.name), bCls.textContent);
        check('pane 3 breadcrumb includes the method name',
              hCls.textContent.includes(RESOLVE.head_unit.name), hCls.textContent);
      }
      if (RESOLVE.base_unit.kind === 'class_header') {
        check('a class header shows the class itself, without a method suffix',
              !bCls.querySelector('.meth'), bCls.textContent);
      }
      check('breadcrumb tooltip carries the full class path',
            !wantB || (bCls.title && bCls.title.includes(RESOLVE.base_unit.cls)), bCls.title);
    }

    // in every mode the breadcrumb must say *something* about where we are
    check('pane 2 breadcrumb is populated', bCls.textContent.trim().length > 0, bCls.textContent);
    check('pane 3 breadcrumb is populated', hCls.textContent.trim().length > 0, hCls.textContent);

    // --- breadcrumb must follow scrolling -------------------------------- //
    const basePane = doc.getElementById('baseBody');
    const before = bCls.textContent;
    basePane.scrollTop = 18 * 5;
    basePane.dispatchEvent(new window.Event('scroll'));
    for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 10));
    const after = bCls.textContent;
    check('breadcrumb still populated after scrolling', after.trim().length > 0, `"${after}"`);
  }

  console.log(fail ? `\n${fail} FAILURE(S)` : '\nALL UI E2E TESTS PASS');
  process.exit(fail ? 1 : 0);
})();
