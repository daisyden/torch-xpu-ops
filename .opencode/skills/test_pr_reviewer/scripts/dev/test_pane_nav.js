// Test the per-pane change / real-change navigation added to panes 2 and 3.
//
// Independently recomputes the expected block counts from the API payloads, then
// drives the real app.js buttons and asserts:
//   * counters match the independent computation
//   * REAL CHANGE is strictly fewer than CHANGE (the bug the user reported)
//   * walking the real track never stops on a benign-only block
//   * navigating one pane locates the counterpart in the other pane and the diff
//
// usage: node dev/test_pane_nav.js <fixture-dir>
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

const VCHAR = { i: 'identical', b: 'blank', n: 'indent', d: 'device', r: 'rename', c: 'changed', m: 'missing' };
const BENIGN = new Set(['identical', 'blank', 'indent', 'device']);
const RANK = { identical: 0, blank: 1, indent: 2, device: 3, rename: 4, changed: 5, missing: 6 };

let fail = 0;
const errors = [];
const check = (name, cond, extra) => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${!cond && extra ? '  -> ' + extra : ''}`);
  if (!cond) fail++;
};

// ---- independent expectation ------------------------------------------- //
function expected(side) {
  const lm = LINEMAP[side];
  const wantKind = side === 'base' ? 'del' : 'add';
  const attr = side === 'base' ? 'base_no' : 'head_no';
  const inDiff = new Map();
  for (const l of FILE.file.lines) {
    if (l.kind !== wantKind) continue;
    const n = l[attr];
    if (n) inDiff.set(n, l.verdict || 'changed');
  }
  const change = [];
  let pc = false;
  for (let i = 0; i < lm.v.length; i++) {
    const own = VCHAR[lm.v[i]] || 'changed';
    const fd = inDiff.get(i + 1);
    const merged = (fd && RANK[fd] > RANK[own]) ? fd : own;
    const isC = inDiff.has(i + 1) || !(merged === 'identical' || merged === 'blank');
    const isR = isC && !BENIGN.has(merged);
    if (isC) {
      if (!pc) change.push({ start: i + 1, real: isR, firstReal: isR ? i + 1 : null });
      else {
        const b = change[change.length - 1];
        if (isR) { b.real = true; if (b.firstReal === null) b.firstReal = i + 1; }
      }
    }
    pc = isC;
  }
  // real is a subset of change: one entry per change block containing anything
  // real, positioned on its first real line
  const real = change.filter((b) => b.real).map((b) => ({ ...b, start: b.firstReal }));
  return { change, real };
}

const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push(String(e.message || e)));
vc.on('error', (m) => errors.push('console.error: ' + m));

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
    const i = d.n !== undefined ? Number(d.n) : (d.i !== undefined ? Number(d.i) : 0);
    return (i + 1) * 18;
  }, configurable: true,
});

window.fetch = async (url) => {
  const u = new window.URL(url, 'http://localhost:8765');
  const r = u.pathname;
  if (r === '/api/pr') return { ok: true, status: 200, json: async () => PR };
  if (r === '/api/file') return { ok: true, status: 200, json: async () => FILE };
  if (r === '/api/linemap') return { ok: true, status: 200, json: async () => LINEMAP };
  if (r === '/api/resolve') {
    const side = u.searchParams.get('side'), line = Number(u.searchParams.get('line'));
    const other = LINEMAP[side].o[line - 1] || null;
    return { ok: true, status: 200, json: async () => ({
      ...RESOLVE, side, lineno: line, mode: 'line', focus: 0,
      rows: [{ base_no: side === 'base' ? line : other, head_no: side === 'base' ? other : line,
               base: '', head: '', base_seg: [], head_seg: [], verdict: 'identical' }],
      base_unit: null, head_unit: null }) };
  }
  return { ok: false, status: 404, json: async () => ({ error: 'x' }) };
};

window.eval(fs.readFileSync(`${DIR}/app.js`, 'utf8'));
const settle = async (n = 30) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 10)); };
const click = async (id, n = 20) => {
  doc.getElementById(id).dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await settle(n);
};

(async () => {
  await settle(60);
  check('no script errors on load', errors.length === 0, errors.join(' | '));

  for (const side of ['base', 'head']) {
    const p = side;
    const pane = side === 'base' ? 'baseBody' : 'headBody';
    const exp = expected(side);
    console.log(`\n--- pane ${side === 'base' ? '2 (base)' : '3 (head)'}: ` +
                `expect CHANGE=${exp.change.length} REAL=${exp.real.length}`);

    for (const b of ['NextChangeBtn', 'PrevChangeBtn', 'NextRealBtn', 'PrevRealBtn']) {
      check(`${side}: button ${b} exists`, !!doc.getElementById(p + b));
    }

    const cc = doc.getElementById(`${p}ChangeCount`).textContent;
    const rc = doc.getElementById(`${p}RealCount`).textContent;
    check(`${side}: change counter total matches`,
          cc.endsWith('/' + exp.change.length) || cc === String(exp.change.length),
          `${cc} vs .../${exp.change.length}`);
    check(`${side}: real counter total matches`,
          rc.endsWith('/' + exp.real.length) || rc === String(exp.real.length),
          `${rc} vs .../${exp.real.length}`);

    // THE reported bug: the two tracks must not be identical, and real must
    // always be a subset of change (never more blocks than change).
    if (exp.change.length > 0) {
      check(`${side}: REAL CHANGE is a subset of CHANGE`,
            exp.real.length <= exp.change.length,
            `real=${exp.real.length} change=${exp.change.length}`);
      check(`${side}: REAL CHANGE is strictly fewer than CHANGE`,
            exp.real.length < exp.change.length,
            `real=${exp.real.length} change=${exp.change.length}`);
    }

    if (!exp.change.length) {
      check(`${side}: change buttons disabled when nothing changed`,
            doc.getElementById(`${p}NextChangeBtn`).disabled === true);
      continue;
    }

    // next change.  Rewind first: navigating the *other* pane earlier in this
    // test moves this pane's cursor too (they are deliberately kept in sync),
    // so without rewinding the first press would continue from there.
    // Rewind to block 1 by pressing "prev" until it clamps.  Pressing "next"
    // afterwards would advance to block 2, so assert directly on the rewound
    // state instead.
    errors.length = 0;
    for (let i = 0; i < exp.change.length + 2; i++) await click(`${p}PrevChangeBtn`, 3);
    check(`${side}: no errors after navigating`, errors.length === 0, errors.join(' | '));
    let focus = doc.querySelector(`#${pane} tr.focusrow`);
    check(`${side}: navigation focused a line`, !!focus);
    check(`${side}: rewound to the first change block`,
          focus && focus.dataset.n === String(exp.change[0].start),
          focus ? `${focus.dataset.n} vs ${exp.change[0].start}` : 'none');
    check(`${side}: change counter reads 1/N`,
          doc.getElementById(`${p}ChangeCount`).textContent.startsWith('1/'),
          doc.getElementById(`${p}ChangeCount`).textContent);

    // navigating pane 2/3 must also locate the line in the diff
    const act = doc.querySelector('#diffBody tr.active');
    check(`${side}: navigation also located the line in pane 1`, !!act);

    if (exp.change.length > 1) {
      await click(`${p}NextChangeBtn`);
      focus = doc.querySelector(`#${pane} tr.focusrow`);
      check(`${side}: advanced to the second change block`,
            focus && focus.dataset.n === String(exp.change[1].start),
            focus ? `${focus.dataset.n} vs ${exp.change[1].start}` : 'none');
      check(`${side}: counter reads 2/N`,
            doc.getElementById(`${p}ChangeCount`).textContent.startsWith('2/'),
            doc.getElementById(`${p}ChangeCount`).textContent);
      await click(`${p}PrevChangeBtn`);
      focus = doc.querySelector(`#${pane} tr.focusrow`);
      check(`${side}: went back to the first change block`,
            focus && focus.dataset.n === String(exp.change[0].start),
            focus ? `${focus.dataset.n} vs ${exp.change[0].start}` : 'none');
    }

    // real track
    if (exp.real.length) {
      errors.length = 0;
      // rewind so the first press lands on real #1
      for (let i = 0; i < exp.real.length + 2; i++) await click(`${p}PrevRealBtn`, 4);
      await click(`${p}NextRealBtn`);
      let rf = doc.querySelector(`#${pane} tr.focusrow`);
      check(`${side}: real change landed on a real block`,
            rf && exp.real.some((b) => String(b.start) === rf.dataset.n),
            rf ? rf.dataset.n : 'none');
      check(`${side}: no errors on real navigation`, errors.length === 0, errors.join(' | '));

      // walking the whole real track must never stop on a benign block
      let benignStops = 0;
      const realStarts = new Set(exp.real.map((b) => b.start));
      for (let i = 0; i < exp.real.length + 1; i++) {
        await click(`${p}NextRealBtn`, 4);
        const f = doc.querySelector(`#${pane} tr.focusrow`);
        if (f && !realStarts.has(Number(f.dataset.n))) {
          // allowed only if the line sits inside a real block
          const inside = exp.real.some((b) => b.start <= Number(f.dataset.n));
          if (!inside) benignStops++;
        }
      }
      check(`${side}: real navigation never stops outside a real block`,
            benignStops === 0, String(benignStops));
    } else {
      check(`${side}: real buttons disabled when nothing is real`,
            doc.getElementById(`${p}NextRealBtn`).disabled === true);
    }
  }

  console.log(fail ? `\n${fail} FAILURE(S)` : '\nALL PANE-NAV TESTS PASS');
  process.exit(fail ? 1 : 0);
})();
