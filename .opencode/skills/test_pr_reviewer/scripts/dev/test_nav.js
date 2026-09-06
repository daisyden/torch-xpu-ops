// Test pane 1 navigation buttons against real API fixtures, driving the real
// app.js.  Verifies that "change" walks every edit, that "real change" skips
// blocks the matcher proved benign, and that both land in panes 2/3.
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
  url: 'http://localhost:8765/?pr=189250',
});
const { window } = dom, doc = window.document;
doc.head.appendChild(Object.assign(doc.createElement('style'),
  { textContent: fs.readFileSync(`${DIR}/app.css`, 'utf8') }));

for (const id of ['diffBody', 'baseBody', 'headBody']) {
  Object.defineProperty(doc.getElementById(id), 'clientHeight', { value: 600, configurable: true });
}
Object.defineProperty(window.HTMLElement.prototype, 'offsetTop', {
  get() { return Number(this.dataset && this.dataset.n ? this.dataset.n : (this.dataset.i || 0)) * 18; },
  configurable: true,
});

// resolve responses are keyed by side:line so navigation gets plausible data
window.fetch = async (url) => {
  const u = new window.URL(url, 'http://localhost:8765');
  const r = u.pathname;
  let body;
  if (r === '/api/pr') body = PR;
  else if (r === '/api/file') body = FILE;
  else if (r === '/api/linemap') body = LINEMAP;
  else if (r === '/api/resolve') body = RESOLVE;
  else return { ok: false, status: 404, json: async () => ({ error: 'x' }) };
  return { ok: true, status: 200, json: async () => body };
};

window.eval(fs.readFileSync(`${DIR}/app.js`, 'utf8'));
const settle = async (n = 40) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 10)); };
const click = (id) => doc.getElementById(id).dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
const key = (k) => doc.dispatchEvent(new window.KeyboardEvent('keydown', { key: k, bubbles: true }));

(async () => {
  await settle(60);
  check('no script errors on load', errors.length === 0, errors.join(' | '));

  // the four buttons must exist
  for (const id of ['prevChangeBtn', 'nextChangeBtn', 'prevRealBtn', 'nextRealBtn']) {
    check(`button ${id} exists`, !!doc.getElementById(id));
  }

  // --- independently compute what the counts should be ------------------- //
  const rows = Array.from(doc.querySelectorAll('#diffBody tr.clickable'));
  check('diff has clickable rows', rows.length > 0, String(rows.length));

  let expBlocks = 0, expReal = 0, prevI = -2, curUnit = null, curReal = false;
  rows.forEach((tr) => {
    const i = Number(tr.dataset.i), u = tr.dataset.unit || '';
    if (i !== prevI + 1 || u !== curUnit) {
      if (expBlocks > 0 && curReal) expReal++;
      expBlocks++; curUnit = u; curReal = false;
    }
    if (tr.dataset.benign !== '1') curReal = true;
    prevI = i;
  });
  if (curReal) expReal++;

  const cc = doc.getElementById('changeCount').textContent;
  const rc = doc.getElementById('realCount').textContent;
  check('change counter shows total blocks', cc.endsWith('/' + expBlocks) || cc === String(expBlocks),
        `${cc} (expected .../${expBlocks})`);
  check('real counter shows real blocks', rc.endsWith('/' + expReal) || rc === String(expReal),
        `${rc} (expected .../${expReal})`);
  console.log(`      (${expBlocks} change blocks, ${expReal} real)`);

  // --- next change ------------------------------------------------------- //
  errors.length = 0;
  click('nextChangeBtn');
  await settle(30);
  check('no errors after next change', errors.length === 0, errors.join(' | '));
  let active = doc.querySelector('#diffBody tr.active');
  check('next change selected a row', !!active);
  check('change counter advanced to 1', doc.getElementById('changeCount').textContent.startsWith('1/'),
        doc.getElementById('changeCount').textContent);
  check('panes 2/3 populated after navigating',
        doc.querySelectorAll('#baseBody tr').length > 0 && doc.querySelectorAll('#headBody tr').length > 0);

  const first = active;
  click('nextChangeBtn');
  await settle(30);
  active = doc.querySelector('#diffBody tr.active');
  check('next change moved to a different row', active !== first,
        `${first && first.dataset.line} -> ${active && active.dataset.line}`);
  check('change counter advanced to 2', doc.getElementById('changeCount').textContent.startsWith('2/'),
        doc.getElementById('changeCount').textContent);

  click('prevChangeBtn');
  await settle(30);
  check('prev change went back', doc.querySelector('#diffBody tr.active') === first);
  check('change counter back to 1', doc.getElementById('changeCount').textContent.startsWith('1/'));

  // --- real change ------------------------------------------------------- //
  if (expReal > 0) {
    errors.length = 0;
    click('nextRealBtn');
    await settle(30);
    check('no errors after next real change', errors.length === 0, errors.join(' | '));
    const ra = doc.querySelector('#diffBody tr.active');
    check('next real change selected a row', !!ra);
    check('the selected row is NOT benign', ra && ra.dataset.benign !== '1',
          ra ? `benign=${ra.dataset.benign}` : 'none');
    // NB: the change/real cursors are deliberately kept in sync, so if the
    // previous "change" navigation already landed on a real block, "next real"
    // correctly advances past it rather than re-selecting it.
    const rcNow = doc.getElementById('realCount').textContent;
    check('real counter is positioned somewhere valid',
          /^\d+\//.test(rcNow) && Number(rcNow.split('/')[0]) >= 1, rcNow);

    // walking all real changes must never land on a benign row
    let benignHits = 0;
    for (let i = Number(rcNow.split('/')[0]); i < expReal; i++) {
      click('nextRealBtn');
      await settle(6);
      const a = doc.querySelector('#diffBody tr.active');
      if (a && a.dataset.benign === '1') benignHits++;
    }
    check('walking every real change never lands on a benign row', benignHits === 0, String(benignHits));
    check('real counter reached the end',
          doc.getElementById('realCount').textContent.startsWith(`${expReal}/`),
          doc.getElementById('realCount').textContent);

    // going past the end must not wrap or crash
    click('nextRealBtn');
    await settle(10);
    check('does not run past the last real change',
          doc.getElementById('realCount').textContent.startsWith(`${expReal}/`),
          doc.getElementById('realCount').textContent);
  } else {
    check('real-change buttons disabled when there is nothing real',
          doc.getElementById('nextRealBtn').disabled === true);
    click('nextRealBtn');
    await settle(10);
    check('clicking a disabled real-change button is harmless', errors.length === 0);
  }

  // --- keyboard shortcuts ------------------------------------------------ //
  errors.length = 0;
  // rewind first: at the last block there is deliberately nowhere to advance to
  for (let i = 0; i < expBlocks; i++) { key('K'); await settle(3); }
  const beforeK = doc.getElementById('changeCount').textContent;
  key('J');
  await settle(20);
  const afterK = doc.getElementById('changeCount').textContent;
  check('Shift+J steps change', afterK !== beforeK || expBlocks <= 1, `${beforeK} -> ${afterK}`);
  check('Shift+K does not run before the first change',
        Number(beforeK.split('/')[0]) === 1, beforeK);
  if (expReal > 0) {
    const beforeN = doc.getElementById('realCount').textContent;
    key('n');
    await settle(20);
    const afterN = doc.getElementById('realCount').textContent;
    check('n steps real change forwards', afterN !== beforeN || expReal <= 1, `${beforeN} -> ${afterN}`);
    key('p');
    await settle(20);
    check('p steps real change backwards', doc.getElementById('realCount').textContent === beforeN,
          `${afterN} -> ${doc.getElementById('realCount').textContent}`);
  }
  check('no errors from keyboard navigation', errors.length === 0, errors.join(' | '));

  console.log(fail ? `\n${fail} FAILURE(S)` : '\nALL NAV TESTS PASS');
  process.exit(fail ? 1 : 0);
})();
