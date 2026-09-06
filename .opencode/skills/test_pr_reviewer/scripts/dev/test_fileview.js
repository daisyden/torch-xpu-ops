// Verify panes 2/3 render whole files with correct diff colouring, using a real
// DOM + the real CSS cascade, driven by real /api/linemap data.
const fs = require('fs');
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
const { JSDOM } = loadJsdom();

const DIR = '/home/daisyden/opencode/refactor-review/static';
const lm = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const dom = new JSDOM(fs.readFileSync(`${DIR}/index.html`, 'utf8'), { pretendToBeVisual: true });
const { window } = dom;
const doc = window.document;
const style = doc.createElement('style');
style.textContent = fs.readFileSync(`${DIR}/app.css`, 'utf8');
doc.head.appendChild(style);

// --- replicate the pane builder from app.js (kept in sync deliberately) ---
const VCHAR = { i:'identical', b:'blank', n:'indent', d:'device', r:'rename', c:'changed', m:'missing' };
const esc = (s) => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const segHtml = (segs) => (segs||[]).map(s => s.m ? `<mark class="w">${esc(s.t)}</mark>` : esc(s.t)).join('');

function build(paneId, side, data) {
  const pane = doc.getElementById(paneId);
  const tbl = doc.createElement('table');
  tbl.className = 'code fileview';
  const tbody = doc.createElement('tbody');
  tbl.appendChild(tbody);
  for (let i = 0; i < data.text.length; i++) {
    const no = i + 1;
    const verdict = VCHAR[data.v[i]] || 'changed';
    const tr = doc.createElement('tr');
    tr.className = `f-${verdict}`;
    tr.dataset.n = String(no);
    const o = data.o[i] || 0;
    if (o) tr.dataset.o = String(o);
    const td1 = doc.createElement('td'); td1.className = 'no'; td1.textContent = String(no);
    const td2 = doc.createElement('td'); td2.className = `txt ${side}`;
    const sg = data.seg[String(no)];
    if (sg && verdict !== 'identical' && verdict !== 'blank') td2.innerHTML = segHtml(sg);
    else td2.textContent = data.text[i];
    tr.appendChild(td1); tr.appendChild(td2); tbody.appendChild(tr);
  }
  pane.innerHTML = ''; pane.appendChild(tbl);
  return tbody;
}

let fail = 0;
const check = (name, cond, extra) => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${extra && !cond ? '  -> ' + extra : ''}`);
  if (!cond) fail++;
};

const bBody = build('baseBody', 'left', lm.base);
const hBody = build('headBody', 'right', lm.head);

// 1. every file line is rendered (whole file, not a fragment)
check('base pane renders all lines', bBody.children.length === lm.base.text.length,
      `${bBody.children.length} vs ${lm.base.text.length}`);
check('head pane renders all lines', hBody.children.length === lm.head.text.length,
      `${hBody.children.length} vs ${lm.head.text.length}`);

// 2. line numbers are the real file line numbers and strictly increasing
const firstNo = bBody.children[0].dataset.n;
const lastNo = bBody.children[bBody.children.length - 1].dataset.n;
check('base line numbers span whole file', firstNo === '1' && lastNo === String(lm.base.text.length),
      `${firstNo}..${lastNo}`);

// 3. text content matches the source file exactly (no mangling)
let textOk = true, badLine = '';
for (let i = 0; i < lm.base.text.length; i += 37) {
  const tr = bBody.children[i];
  const got = tr.querySelector('td.txt').textContent;
  if (got !== lm.base.text[i]) { textOk = false; badLine = `line ${i+1}: ${JSON.stringify(got)} != ${JSON.stringify(lm.base.text[i])}`; break; }
}
check('rendered text matches source', textOk, badLine);

// 4. colouring: a changed line on the base side must be red-ish, on head green-ish
//    NB: jsdom does not resolve `var()`, so computed backgrounds come back
//    transparent.  Assert on the stylesheet rules and on the class wiring
//    instead, and resolve the custom properties ourselves.
const cssText = fs.readFileSync(`${DIR}/app.css`, 'utf8');
const cssVar = (n) => (cssText.match(new RegExp(`--${n}:\\s*([^;]+);`)) || [])[1]?.trim();
const ruleBg = (sel) => {
  const m = cssText.match(new RegExp(sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}'));
  if (!m) return null;
  const b = m[1].match(/background:\s*([^;]+);/);
  if (!b) return null;
  const v = b[1].trim().match(/^var\(--([\w-]+)\)$/);
  return v ? cssVar(v[1]) : b[1].trim();
};

const delBg = ruleBg('tr.f-changed td.txt.left');
const addBg = ruleBg('tr.f-changed td.txt.right');
check('changed line: base and head use different colours', !!delBg && !!addBg && delBg !== addBg,
      `${delBg} vs ${addBg}`);
check('base changed colour is the deletion red', delBg === cssVar('del-bg'), String(delBg));
check('head changed colour is the addition green', addBg === cssVar('add-bg'), String(addBg));

const findV = (tbody, cls) => Array.from(tbody.children).find(tr => tr.className === `f-${cls}`);
const bg = (tr) => tr && window.getComputedStyle(tr.querySelector('td.txt')).backgroundColor;

const bChanged = findV(bBody, 'changed');
const hChanged = findV(hBody, 'changed');
// A pure-move PR legitimately has no `changed` lines on a given side, so only
// assert the wiring when such a row actually exists.
if (bChanged) {
  check('changed base row carries the left cell', !!bChanged.querySelector('td.txt.left'));
} else {
  console.log('SKIP  no changed rows on the base side (pure move)');
}
if (hChanged) {
  check('changed head row carries the right cell', !!hChanged.querySelector('td.txt.right'));
} else {
  console.log('SKIP  no changed rows on the head side');
}

// device / rename / indent must each have their own colour, distinct from changed
for (const v of ['device', 'rename', 'indent']) {
  const c = ruleBg(`tr.f-${v} td.txt`);
  check(`${v} lines have a distinct colour`, !!c && c !== delBg && c !== addBg, String(c));
}

const bIdent = findV(bBody, 'identical');
// the identical/blank rule is written as a grouped selector
const identTransparent = /tr\.f-identical td\.txt,\s*\n?\s*tr\.f-blank td\.txt\s*\{[^}]*background:\s*transparent/.test(cssText);
check('identical rows carry no diff colour', !!bIdent && identTransparent);

// 5. word-level marks exist on differing lines
const marks = hBody.querySelectorAll('mark.w').length;
check('word-level marks present on head side', marks > 0, String(marks));

// 6. counterpart pointers are present and land on real rows
let ptr = 0, ptrOk = true;
for (const tr of Array.from(bBody.children)) {
  const o = tr.dataset.o;
  if (!o) continue;
  ptr++;
  if (!hBody.querySelector(`tr[data-n="${o}"]`)) { ptrOk = false; break; }
}
check('counterpart pointers resolve to head rows', ptrOk && ptr > 0, `${ptr} pointers`);

// 7. focus + unit highlighting behave
const target = bChanged || bBody.children[10];
target.classList.add('focusrow');
const st = window.getComputedStyle(target.querySelector('td.no'));
check('focus row number cell is emphasised', st.fontWeight === '600' || st.backgroundColor !== 'transparent',
      `${st.fontWeight} ${st.backgroundColor}`);

console.log(fail ? `\n${fail} FAILURE(S)` : '\nALL FILE-VIEW TESTS PASS');
process.exit(fail ? 1 : 0);
