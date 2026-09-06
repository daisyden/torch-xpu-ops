// Verify the overlay can actually be closed, using a real DOM + CSS cascade.
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
const html = fs.readFileSync(`${DIR}/index.html`, 'utf8');
const css = fs.readFileSync(`${DIR}/app.css`, 'utf8');

const dom = new JSDOM(html, { pretendToBeVisual: true, runScripts: 'outside-only' });
const { window } = dom;
const doc = window.document;

// inject the real stylesheet so the cascade is exercised
const style = doc.createElement('style');
style.textContent = css;
doc.head.appendChild(style);

const overlay = doc.getElementById('overlay');
const card = doc.getElementById('overlayCard');
const shown = (el) => window.getComputedStyle(el).display;

let fail = 0;
const check = (name, cond) => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}`);
  if (!cond) fail++;
};

// 1. with the hidden attribute set, computed display must be none
overlay.hidden = true;
check('overlay hidden=true  -> display:none', shown(overlay) === 'none');

// 2. without it, the flex layout applies
overlay.hidden = false;
check('overlay hidden=false -> display:flex', shown(overlay) === 'flex');

// 3. same for the matchbar (had the identical latent bug)
const mb = doc.getElementById('matchbar');
mb.hidden = true;
check('matchbar hidden=true -> display:none', shown(mb) === 'none');
mb.hidden = false;
check('matchbar hidden=false-> display:flex', shown(mb) === 'flex');

// 4. simulate the close-button handler wiring from app.js
const closeOverlay = () => { overlay.hidden = true; };
doc.getElementById('overlayClose').onclick = closeOverlay;
overlay.onclick = (e) => { if (!card.contains(e.target)) closeOverlay(); };

overlay.hidden = false;
doc.getElementById('overlayClose').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('close button hides overlay', overlay.hidden && shown(overlay) === 'none');

// 5. clicking the backdrop closes
overlay.hidden = false;
overlay.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('backdrop click hides overlay', overlay.hidden);

// 6. clicking inside the card must NOT close (regression guard)
overlay.hidden = false;
const inner = doc.getElementById('overlayBody');
inner.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('click inside card keeps overlay open', overlay.hidden === false);

// 7. Escape closes even when focus is in the PR input
const onKey = (e) => {
  if (e.key === 'Escape') { closeOverlay(); return; }
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
};
doc.addEventListener('keydown', onKey);
overlay.hidden = false;
const input = doc.getElementById('prInput');
input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
check('Escape from input hides overlay', overlay.hidden);

console.log(fail ? `\n${fail} FAILURE(S)` : '\nALL OVERLAY TESTS PASS');
process.exit(fail ? 1 : 0);
