/* PyTorch test-refactor review UI
 *
 * Pane 1: GitHub-style unified diff of the selected file.
 * Panes 2+3: when a -/+ line is clicked, the *matched* unit (test method or
 *            class header) is aligned line-by-line; pane 2 shows the base
 *            revision, pane 3 the head revision, with word-level highlighting
 *            and a verdict per row.
 */

const $ = (id) => document.getElementById(id);

const S = {
  ref: null,
  meta: null,
  path: null,
  data: null,      // /api/file payload
  resolved: null,  // /api/resolve payload
  side: 'base',
  lineno: null,
  hideBenign: false,
  onlyNew: false,
  linemap: null,
  lockScroll: false,
  classIndex: { base: [], head: [] },
  pinned: { base: null, head: null },
  navBlocks: [],
  navReal: [],
  navPos: -1,
  navRealPos: -1,
  fileNav: { base: null, head: null },
  fileNavPos: { base: { change: -1, real: -1 }, head: { change: -1, real: -1 } },
};

const BENIGN = new Set(['identical', 'blank', 'indent', 'device']);

/* one-character verdict codes used by the compact line map */
const VCHAR = {
  i: 'identical', b: 'blank', n: 'indent',
  d: 'device', r: 'rename', c: 'changed', m: 'missing',
};

const VERDICT_LABEL = {
  identical: 'identical',
  blank: 'blank',
  indent: 'indent only',
  device: 'device-only change',
  rename: 'rename only',
  changed: 'real change',
  missing: 'no counterpart',
};

const VERDICT_CLASS = {
  identical: 'ok', blank: 'ok', indent: 'ok',
  device: 'warn', rename: 'warn',
  changed: 'bad', missing: 'bad',
};

/* ----------------------------------------------------------------- helpers */

function esc(s) {
  return (s === null || s === undefined ? '' : String(s))
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function status(msg, isErr) {
  const el = $('status');
  el.textContent = msg || '';
  el.className = isErr ? 'err' : '';
}

async function api(route, params) {
  const qs = new URLSearchParams(params).toString();
  const res = await fetch(`/api/${route}?${qs}`);
  const body = await res.json();
  if (!res.ok || body.error) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

function shortName(qual) {
  if (!qual) return '\u2014';
  return qual;
}

function pill(text, cls) {
  return `<span class="pill ${cls || ''}">${esc(text)}</span>`;
}

/* -------------------------------------------------------------- pane 1: diff */

function unitTag(ln) {
  if (!ln.unit) return '';
  const isAdd = ln.kind === 'add';
  if (!ln.target) {
    // For a + line this means genuinely new code; for a - line, deleted code.
    return isAdd
      ? `<span class="tag real" title="this code has no counterpart in the base revision \u2014 it is new, review it">new code</span>`
      : `<span class="tag real" title="this code has no counterpart in the PR \u2014 it was deleted">deleted</span>`;
  }
  const label = ln.target.split('.').slice(-2).join('.');
  if (ln.moved || ln.renamed) {
    const what = ln.moved && ln.renamed ? 'moved+renamed' : (ln.moved ? 'moved' : 'renamed');
    // direction of the arrow follows the side we are looking at
    const arrow = isAdd ? '\u2190' : '\u2192';
    const title = isAdd
      ? `${ln.target} \u2192 ${ln.unit} (score ${ln.unit_score})`
      : `${ln.unit} \u2192 ${ln.target} (score ${ln.unit_score})`;
    return `<span class="tag moved" title="${esc(title)}">${what} ${arrow} ${esc(label)}</span>`;
  }
  return `<span class="tag same" title="${esc(ln.unit)}">${esc(label)}</span>`;
}

function rowVerdictClass(ln) {
  // The rail colour states the unit's verdict, so it agrees with what the
  // "real change" navigation considers worth stopping on.
  const v = ln.verdict;
  if (!ln.unit) return v ? 'v-changed' : '';
  if (!ln.target) return 'v-missing';
  if (v === 'changed') return 'v-changed';
  if (v === 'device' || v === 'rename') return 'v-device';
  if (ln.moved || ln.renamed) return 'v-moved';
  return '';
}

function applyFilters() {
  const hideBenign = S.hideBenign;
  const onlyNew = S.onlyNew;
  $('diffBody').querySelectorAll('tr.clickable').forEach((tr) => {
    const benign = tr.dataset.benign === '1';
    const newcode = tr.dataset.newcode === '1';
    let hide = false;
    if (onlyNew) hide = !newcode;
    else if (hideBenign) hide = benign;
    tr.classList.toggle('hidden', hide);
  });
  // hunk headers are only useful when something under them is visible
  const body = $('diffBody');
  const rows = Array.from(body.querySelectorAll('tr'));
  let lastHunk = null, seen = false;
  rows.forEach((tr) => {
    if (tr.classList.contains('hunk')) {
      if (lastHunk) lastHunk.classList.toggle('hidden', !seen);
      lastHunk = tr; seen = false;
    } else if (!tr.classList.contains('hidden')) {
      seen = true;
    }
  });
  if (lastHunk) lastHunk.classList.toggle('hidden', !seen);
}

function renderDiff() {
  const d = S.data.file;
  const rows = [];
  let lastTag = null;

  // Guard against a stale server: `verdict` is what separates a real change
  // from a pure move, and defaulting it to 'changed' silently degrades the
  // real-change navigation into a copy of the plain change navigation.
  const clickableLines = d.lines.filter((l) => l.kind === 'del' || l.kind === 'add');
  const missingVerdict = clickableLines.filter((l) => l.verdict === undefined).length;
  if (clickableLines.length && missingVerdict === clickableLines.length) {
    status('server is out of date: no line verdicts \u2014 restart it (./restart.sh)', true);
    console.error(
      'No `verdict` on any diff line. The API is from an older server build, so ' +
      '"real change" cannot be distinguished from "change". Run ./restart.sh.'
    );
  }

  d.lines.forEach((ln, i) => {
    if (ln.kind === 'hunk') {
      rows.push(`<tr class="hunk"><td colspan="4">${esc(ln.text)}</td></tr>`);
      lastTag = null;
      return;
    }
    const isDel = ln.kind === 'del';
    const isAdd = ln.kind === 'add';
    const clickable = isDel || isAdd;
    const no = isDel ? ln.base_no : (isAdd ? ln.head_no : `${ln.base_no}`);
    const sign = isDel ? '\u2212' : (isAdd ? '+' : ' ');

    // show the unit tag only when it changes, to avoid a wall of badges
    let tag = '';
    if (clickable) {
      const key = `${ln.kind}|${ln.unit}|${ln.target}`;
      if (key !== lastTag) { tag = unitTag(ln); lastTag = key; }
    } else {
      lastTag = null;
    }

    // classify the row so the filters and navigation can act on it
    //   benign  = the owning unit is provably unchanged in substance, so the
    //             -/+ is just the code having moved or a device-only edit
    //   real    = something a reviewer must actually read
    //   newcode = added line with no base counterpart
    const verdict = ln.verdict || (clickable ? 'changed' : 'identical');
    const benign = clickable && BENIGN.has(verdict);
    const newcode = isAdd && (!ln.unit || !ln.target);

    const cls = [
      ln.kind,
      clickable ? 'clickable' : '',
      rowVerdictClass(ln),
      clickable && !benign ? 'realchange' : '',
    ].filter(Boolean).join(' ');

    const numCell = isDel
      ? `<td class="no">${ln.base_no ?? ''}</td>`
      : (isAdd ? `<td class="no">${ln.head_no ?? ''}</td>`
               : `<td class="no">${ln.base_no ?? ''}</td>`);

    rows.push(
      `<tr class="${cls}" data-i="${i}" data-side="${isDel ? 'base' : 'head'}" ` +
      `data-line="${isDel ? (ln.base_no ?? '') : (ln.head_no ?? '')}" ` +
      // explicit per-revision line numbers: a context row carries *both*, and
      // reverse lookup (from a file view back to the diff) needs to search by
      // either side independently of which one `data-line` happens to hold.
      `data-b="${ln.base_no ?? ''}" data-h="${ln.head_no ?? ''}" ` +
      `data-target="${ln.target_uid ?? ''}" ` +
      `data-unit="${esc(ln.unit ?? '')}" ` +
      `data-verdict="${esc(verdict)}" ` +
      `data-benign="${benign ? 1 : 0}" data-newcode="${newcode ? 1 : 0}">` +
      numCell +
      `<td class="sign">${sign}</td>` +
      `<td class="txt">${esc(ln.text)}${tag}</td></tr>`
    );
  });

  $('diffBody').innerHTML = `<table class="code">${rows.join('')}</table>`;
  $('diffSub').textContent = `${d.path}  +${d.additions} \u2212${d.deletions}`;
  applyFilters();
  buildNavIndex();

  $('diffBody').querySelectorAll('tr.clickable').forEach((tr) => {
    tr.addEventListener('click', () => {
      const line = parseInt(tr.dataset.line, 10);
      if (!line) return;
      selectRow(tr);
      S.navPos = S.navBlocks.findIndex((b) => b.rows.includes(tr));
      S.navRealPos = S.navReal.findIndex((b) => b.rows.includes(tr));
      updateNavCounts();
      resolveLine(tr.dataset.side, line, tr.dataset.target || null);
    });
  });
}

/* ------------------------------------------------------------- pane 1 nav */
/* Navigation works on *blocks* of consecutive -/+ lines rather than single
 * lines, because a moved 20-line method is one change to a reviewer, not 20.
 *
 * Two tracks:
 *   change      every block  (what the PR touched)
 *   real change blocks that are not provably benign, i.e. not a verbatim move
 *               and not a device-only / indentation-only edit
 */
function buildNavIndex() {
  const rows = Array.from($('diffBody').querySelectorAll('tr.clickable'));
  const blocks = [];
  let cur = null;
  let prevIdx = -2;

  rows.forEach((tr) => {
    const i = Number(tr.dataset.i);
    const unit = tr.dataset.unit || '';
    // a new block starts on a gap in the diff or when the owning unit changes
    if (!cur || i !== prevIdx + 1 || unit !== cur.unit) {
      cur = { rows: [], unit, real: false };
      blocks.push(cur);
    }
    cur.rows.push(tr);
    if (tr.dataset.benign !== '1') cur.real = true;
    prevIdx = i;
  });

  S.navBlocks = blocks;
  S.navReal = blocks.filter((b) => b.real);
  S.navPos = -1;
  S.navRealPos = -1;
  updateNavCounts();
}

/* ------------------------------------------- reverse lookup: file -> diff */
/* Clicking a line in pane 2 or 3 locates it in the other file view *and* in the
 * PR diff, so navigation works in every direction.  Clicks are delegated from
 * the pane, since a 27k-line file must not get 27k listeners.
 */
function initFileViewClicks(paneId, side) {
  const pane = $(paneId);
  if (pane._clickWired) return;
  pane._clickWired = true;
  pane.addEventListener('click', (e) => {
    const tr = e.target.closest && e.target.closest('tr[data-n]');
    if (!tr || !pane.contains(tr)) return;
    const lineno = parseInt(tr.dataset.n, 10);
    if (lineno) selectFromFileView(side, lineno);
  });
}

/* Find the diff row for (side, lineno).  Exact match first; a line that the
 * diff never mentions (unchanged context outside every hunk) has no row, so
 * fall back to the nearest one so pane 1 still moves to the right region. */
function findDiffRow(side, lineno) {
  const body = $('diffBody');
  const attr = side === 'base' ? 'b' : 'h';
  const exact = body.querySelector(`tr[data-${attr}="${lineno}"]`);
  if (exact) return { tr: exact, exact: true };

  let best = null, bestDist = Infinity;
  body.querySelectorAll(`tr[data-${attr}]`).forEach((tr) => {
    const v = parseInt(tr.dataset[attr], 10);
    if (!v) return;
    const d = Math.abs(v - lineno);
    if (d < bestDist) { bestDist = d; best = tr; }
  });
  return best ? { tr: best, exact: false, distance: bestDist } : null;
}

/* Drive everything from a click in a file view. */
function selectFromFileView(side, lineno) {
  const lm = S.linemap && S.linemap[side];
  if (!lm) return;

  // 1. mark the clicked line in its own pane straight away, so the click feels
  //    immediate even before /api/resolve answers
  const ownPane = side === 'base' ? 'baseBody' : 'headBody';
  focusLine(ownPane, lineno, { keepUnit: true });
  setBreadcrumb(side, lineno, { pinned: true });
  S.pinned[side] = lineno;

  // 2. the counterpart line in the other file, from the precomputed line map
  const other = side === 'base' ? 'head' : 'base';
  const otherPane = side === 'base' ? 'headBody' : 'baseBody';
  const counterpart = lm.o[lineno - 1] || 0;
  if (counterpart) {
    focusLine(otherPane, counterpart, { keepUnit: true });
    setBreadcrumb(other, counterpart, { pinned: true });
    S.pinned[other] = counterpart;
  }

  // 3. locate it in the diff
  const hit = findDiffRow(side, lineno);  if (hit) {
    selectRow(hit.tr);
    const body = $('diffBody');
    // reveal the row even if a filter had hidden it
    if (hit.tr.classList.contains('hidden')) {
      $('hideBenign').checked = false;
      $('onlyNew').checked = false;
      S.hideBenign = false;
      S.onlyNew = false;
      applyFilters();
    }
    body.scrollTop = Math.max(0, hit.tr.offsetTop - body.clientHeight / 3);
    S.navPos = S.navBlocks.findIndex((b) => b.rows.includes(hit.tr));
    S.navRealPos = S.navReal.findIndex((b) => b.rows.includes(hit.tr));
    updateNavCounts();
  }

  // 4. ask the server for the full correspondence, so the match bar and the
  //    unit outline are right; this also re-centres both panes coherently
  const dtr = hit && hit.exact ? hit.tr : null;
  const target = dtr ? (dtr.dataset.target || null) : null;
  resolveLine(side, lineno, target, { fromFileView: true });

  // 5. keep this pane's own change/real cursors pointing at the clicked block,
  //    so its counters agree with what is on screen
  syncFileNavCursor(side, lineno);
  if (counterpart) syncFileNavCursor(other, counterpart);

  if (hit && !hit.exact) {
    status(`line ${lineno} is unchanged context \u2014 diff shows the nearest hunk`);
  }
}

/* ------------------------------------------------- panes 2/3 navigation */
/* Each file view gets its own change / real-change navigation, walking blocks
 * of consecutive interesting lines *within that file*.
 *
 * What counts as a "change" on one side needs care.  In a pure move the base
 * lines are content-identical to their new home, so the line map marks them
 * `identical` and they carry no tint -- yet they are exactly the lines the PR
 * deleted.  So a line is a change here when either
 *    - its own verdict says it differs, or
 *    - the diff lists it as removed (pane 2) / added (pane 3).
 * It is a *real* change only when the merged verdict is not benign, which keeps
 * these buttons consistent with pane 1 and with the Refactor map.
 */
const VERDICT_RANK = {
  identical: 0, blank: 1, indent: 2, device: 3, rename: 4, changed: 5, missing: 6,
};

function buildFileNavIndex(side) {
  const lm = S.linemap && S.linemap[side];
  const empty = { change: [], real: [] };
  if (!lm || !S.data) { S.fileNav[side] = empty; return; }

  // per-line verdict contributed by the diff for this side
  const wantKind = side === 'base' ? 'del' : 'add';
  const attr = side === 'base' ? 'base_no' : 'head_no';
  const inDiff = new Map();
  for (const l of S.data.file.lines) {
    if (l.kind !== wantKind) continue;
    const n = l[attr];
    if (n) inDiff.set(n, l.verdict || 'changed');
  }

  const change = [];
  let prevC = false;

  for (let i = 0; i < lm.v.length; i++) {
    const own = VCHAR[lm.v[i]] || 'changed';
    const fromDiff = inDiff.get(i + 1);
    const merged = (fromDiff && VERDICT_RANK[fromDiff] > VERDICT_RANK[own]) ? fromDiff : own;

    const isChange = inDiff.has(i + 1) || !(merged === 'identical' || merged === 'blank');
    const isReal = isChange && !BENIGN.has(merged);

    if (isChange) {
      if (!prevC) {
        change.push({ start: i + 1, end: i + 1, verdict: merged, real: isReal,
                      firstReal: isReal ? i + 1 : null });
      } else {
        const b = change[change.length - 1];
        b.end = i + 1;
        if (VERDICT_RANK[merged] > VERDICT_RANK[b.verdict]) b.verdict = merged;
        if (isReal) {
          b.real = true;
          if (b.firstReal === null) b.firstReal = i + 1;
        }
      }
    }
    prevC = isChange;
  }

  // The real track is a *subset* of the change track, never a separate
  // partition: a single change block can contain several real runs separated by
  // benign lines, and treating those as independent blocks would make
  // REAL CHANGE exceed CHANGE, which is nonsense.  One entry per change block
  // that contains anything real, positioned on its first real line.
  const real = change
    .filter((b) => b.real)
    .map((b) => ({ ...b, start: b.firstReal !== null ? b.firstReal : b.start }));

  S.fileNav[side] = { change, real };
  S.fileNavPos[side] = { change: -1, real: -1 };
}

function updateFileNavCounts(side) {
  const nav = S.fileNav[side] || { change: [], real: [] };
  const pos = S.fileNavPos[side] || { change: -1, real: -1 };
  const p = side === 'base' ? 'base' : 'head';
  const nc = nav.change.length, nr = nav.real.length;
  $(`${p}ChangeCount`).textContent = nc
    ? `${pos.change >= 0 ? pos.change + 1 : '\u2013'}/${nc}` : '0';
  $(`${p}RealCount`).textContent = nr
    ? `${pos.real >= 0 ? pos.real + 1 : '\u2013'}/${nr}` : '0';
  $(`${p}RealCount`).classList.toggle('none', nr === 0);
  $(`${p}PrevChangeBtn`).disabled = nc === 0;
  $(`${p}NextChangeBtn`).disabled = nc === 0;
  $(`${p}PrevRealBtn`).disabled = nr === 0;
  $(`${p}NextRealBtn`).disabled = nr === 0;
}

/* step within one file view; `track` is 'change' or 'real' */
function stepFileNav(side, track, dir) {
  const nav = S.fileNav[side];
  if (!nav) return;
  const list = nav[track];
  const label = track === 'real' ? 'real change' : 'change';
  const where = side === 'base' ? 'base file' : 'PR file';
  if (!list.length) {
    status(track === 'real'
      ? `no real changes in the ${where} \u2014 all edits are moves or device-only`
      : `no changes in the ${where}`);
    return;
  }
  let pos = S.fileNavPos[side][track];
  pos = pos < 0 ? (dir > 0 ? 0 : list.length - 1) : pos + dir;
  if (pos < 0) { status(`at the first ${label} in the ${where}`); return; }
  if (pos >= list.length) { status(`at the last ${label} in the ${where}`); return; }

  S.fileNavPos[side][track] = pos;
  // keep the two tracks coherent: if this block is also in the other list,
  // move that cursor too
  const other = track === 'real' ? 'change' : 'real';
  const oi = nav[other].findIndex((b) => b.start <= list[pos].start && list[pos].start <= b.end);
  if (oi >= 0) S.fileNavPos[side][other] = oi;
  updateFileNavCounts(side);

  // reuse the click path, so the other pane and the diff follow along
  selectFromFileView(side, list[pos].start);
  status('');
}

/* Point a pane's change/real cursors at whichever block contains `lineno`. */
function syncFileNavCursor(side, lineno) {
  const nav = S.fileNav[side];
  if (!nav) return;
  for (const track of ['change', 'real']) {
    const i = nav[track].findIndex((b) => b.start <= lineno && lineno <= b.end);
    if (i >= 0) S.fileNavPos[side][track] = i;
  }
  updateFileNavCounts(side);
}

function updateNavCounts() {
  const n = S.navBlocks.length;
  const nr = S.navReal.length;
  $('changeCount').textContent = n ? `${S.navPos >= 0 ? S.navPos + 1 : '\u2013'}/${n}` : '0';
  $('realCount').textContent = nr ? `${S.navRealPos >= 0 ? S.navRealPos + 1 : '\u2013'}/${nr}` : '0';
  $('realCount').classList.toggle('none', nr === 0);
  for (const id of ['prevChangeBtn', 'nextChangeBtn']) $(id).disabled = n === 0;
  for (const id of ['prevRealBtn', 'nextRealBtn']) $(id).disabled = nr === 0;
}

/* move to a block and open it in panes 2/3 */
function gotoBlock(list, pos, opts) {
  const block = list[pos];
  if (!block) return false;
  const visible = block.rows.filter((r) => !r.classList.contains('hidden'));
  const pool = visible.length ? visible : block.rows;
  // Land on the first row that is worth reading.  A block can begin with a
  // blank line, and selecting that would show the reviewer nothing.
  let tr = null;
  if (opts && opts.preferReal) {
    tr = pool.find((r) => r.dataset.benign !== '1' && r.dataset.verdict !== 'blank');
  }
  if (!tr) tr = pool.find((r) => r.dataset.verdict !== 'blank') || pool[0];
  const line = parseInt(tr.dataset.line, 10);
  selectRow(tr);
  const body = $('diffBody');
  body.scrollTop = Math.max(0, tr.offsetTop - body.clientHeight / 3);
  if (line) resolveLine(tr.dataset.side, line, tr.dataset.target || null);
  return true;
}

function stepChange(dir) {
  const list = S.navBlocks;
  if (!list.length) { status('no changes in this file'); return; }
  let pos = S.navPos;
  pos = pos < 0 ? (dir > 0 ? 0 : list.length - 1) : pos + dir;
  if (pos < 0) { status('at the first change'); return; }
  if (pos >= list.length) { status('at the last change'); return; }
  S.navPos = pos;
  // keep the "real change" cursor consistent if this block is also a real one
  const ri = S.navReal.indexOf(list[pos]);
  if (ri >= 0) S.navRealPos = ri;
  updateNavCounts();
  gotoBlock(list, pos);
  status('');
}

function stepReal(dir) {
  const list = S.navReal;
  if (!list.length) {
    status('no real changes \u2014 every edit here is a move or device-only');
    return;
  }
  let pos = S.navRealPos;
  pos = pos < 0 ? (dir > 0 ? 0 : list.length - 1) : pos + dir;
  if (pos < 0) { status('at the first real change'); return; }
  if (pos >= list.length) { status('at the last real change'); return; }
  S.navRealPos = pos;
  S.navPos = S.navBlocks.indexOf(list[pos]);
  updateNavCounts();
  gotoBlock(list, pos, { preferReal: true });
  status('');
}

function selectRow(tr) {
  $('diffBody').querySelectorAll('tr.active').forEach((x) => x.classList.remove('active'));
  tr.classList.add('active');
}

/* Render word-level highlight segments for one line.
 * `segs` is [{t: text, m: true|false}, ...]; marked runs are the parts that
 * differ from the counterpart line. */
function segHtml(segs) {
  if (!segs || !segs.length) return '';
  return segs.map((s) => (s.m ? `<mark class="w">${esc(s.t)}</mark>` : esc(s.t))).join('');
}

/* ---------------------------------------------- panes 2/3: whole-file views */
/* Panes 2 and 3 render the *complete* base and head files, coloured like a
 * normal diff tool, so the reviewer can scroll for context.  Clicking a diff
 * line scrolls both panes to the matched location and marks it, rather than
 * showing only that fragment.
 *
 * Rows are built lazily in chunks: a 27k-line file would otherwise block the
 * UI for seconds while the browser lays out 27k table rows.
 */

const CHUNK = 1200;

function buildFileView(paneId, lm, side) {
  const pane = $(paneId);
  const text = lm.text;
  const v = lm.v;
  const o = lm.o;
  const seg = lm.seg || {};
  const n = text.length;

  const tbl = document.createElement('table');
  tbl.className = 'code fileview';
  const tbody = document.createElement('tbody');
  tbl.appendChild(tbody);
  pane.innerHTML = '';
  pane.appendChild(tbl);

  let i = 0;
  const step = () => {
    const frag = document.createDocumentFragment();
    const upto = Math.min(i + CHUNK, n);
    for (; i < upto; i++) {
      const no = i + 1;
      const verdict = VCHAR[v[i]] || 'changed';
      const tr = document.createElement('tr');
      tr.className = `f-${verdict}`;
      tr.dataset.n = String(no);
      const other = o[i] || 0;
      if (other) tr.dataset.o = String(other);

      const td1 = document.createElement('td');
      td1.className = 'no';
      td1.textContent = String(no);
      const td2 = document.createElement('td');
      td2.className = `txt ${side}`;
      const sg = seg[String(no)];
      if (sg && verdict !== 'identical' && verdict !== 'blank') {
        td2.innerHTML = segHtml(sg);
      } else {
        td2.textContent = text[i];
      }
      tr.appendChild(td1);
      tr.appendChild(td2);
      frag.appendChild(tr);
    }
    tbody.appendChild(frag);
    if (i < n) {
      requestAnimationFrame(step);
    } else {
      pane.dataset.ready = '1';
      flushPending(paneId);
    }
  };
  pane.dataset.ready = '0';
  step();
}

// a scroll requested before the pane finished building
const pending = {};
function flushPending(paneId) {
  const p = pending[paneId];
  if (!p) return;
  delete pending[paneId];
  p();
}

/* Class / method breadcrumb for panes 2 and 3.
 *
 * These refactors are *about* the class a test lives in, so the enclosing class
 * must always be visible -- both for the line you jumped to and for wherever you
 * have scrolled to since.  `classSpans` is sorted innermost-last so the deepest
 * enclosing class wins.
 */
function buildClassIndex(side) {
  const data = S.data && S.data[side];
  if (!data) return [];
  return (data.classes || [])
    .map((c) => ({ q: c.qualname, name: c.name, start: c.start, end: c.end, bases: c.bases }))
    .sort((a, b) => (a.start - b.start) || (b.end - a.end));
}

function classAt(side, lineno) {
  const idx = S.classIndex[side] || [];
  let best = null;
  for (const c of idx) {
    if (c.start <= lineno && lineno <= c.end) {
      if (!best || c.start > best.start) best = c;
    }
  }
  return best;
}

function unitAt(side, lineno) {
  const lm = S.linemap && S.linemap[side];
  if (!lm) return null;
  const i = lm.u && lm.u[String(lineno)];
  if (i === undefined || i === null) return null;
  return lm.names[i] || null;
}

/* Render the breadcrumb: class name (always) plus the method when known. */
function setBreadcrumb(side, lineno, opts) {
  const el = $(side === 'base' ? 'baseCls' : 'headCls');
  if (!lineno) { el.innerHTML = ''; el.title = ''; return; }

  const cls = classAt(side, lineno);
  const unit = unitAt(side, lineno);
  const parts = [];

  if (cls) {
    const tag = (opts && opts.pinned) ? 'cls pinned' : 'cls';
    parts.push(`<span class="${tag}">${esc(cls.name)}</span>`);
  } else {
    parts.push('<span class="cls none">&lt;module level&gt;</span>');
  }
  // the method, when the line belongs to one and it is not just the class body
  if (unit) {
    const mname = unit.split('.').pop();
    if (!cls || mname !== cls.name) {
      parts.push(`<span class="sep">.</span><span class="meth">${esc(mname)}</span>`);
    }
  }
  if (opts && opts.scrolled) parts.push('<span class="scrollnote">(scrolled)</span>');

  el.innerHTML = parts.join('');
  el.title = cls
    ? `${cls.q}(${(cls.bases || []).join(', ')})  lines ${cls.start}-${cls.end}`
    : 'outside any class';
}

/* keep the breadcrumb in sync while the user scrolls for context */
function trackScrollBreadcrumb(paneId, side) {
  const pane = $(paneId);
  pane.addEventListener('scroll', () => {
    if (pane._bcRaf) return;
    pane._bcRaf = requestAnimationFrame(() => {
      pane._bcRaf = null;
      const first = topVisibleLine(pane);
      if (!first) return;
      const pinned = S.pinned[side];
      const pinnedCls = pinned ? classAt(side, pinned) : null;
      const nowCls = classAt(side, first);
      // while the pinned method is still on screen keep showing it
      if (pinned && pinnedCls && nowCls && pinnedCls.q === nowCls.q) {
        setBreadcrumb(side, pinned, { pinned: true });
      } else {
        setBreadcrumb(side, first, { scrolled: !!pinned });
      }
    });
  }, { passive: true });
}

function topVisibleLine(pane) {
  const rows = pane.querySelectorAll('tr[data-n]');
  if (!rows.length) return null;
  // rows are uniform height, so derive the index instead of hit-testing
  const h = rows[0].offsetHeight || 18;
  const i = Math.min(rows.length - 1, Math.max(0, Math.floor(pane.scrollTop / h)));
  const tr = rows[i];
  return tr ? parseInt(tr.dataset.n, 10) : null;
}

function renderFileViews() {
  const lm = S.linemap;
  if (!lm) return;
  S.classIndex = { base: buildClassIndex('base'), head: buildClassIndex('head') };
  S.pinned = { base: null, head: null };
  buildFileView('baseBody', lm.base, 'left');
  buildFileView('headBody', lm.head, 'right');
  const bn = lm.base.path.split('/').pop();
  const hn = lm.head.path.split('/').pop();
  $('baseSub').textContent = `${bn} @ base \u00b7 ${lm.base.text.length} lines`;
  $('headSub').textContent = `${hn} @ PR \u00b7 ${lm.head.text.length} lines`;
  setBreadcrumb('base', null);
  setBreadcrumb('head', null);
  linkPaneScroll();
  trackScrollBreadcrumb('baseBody', 'base');
  trackScrollBreadcrumb('headBody', 'head');
  initFileViewClicks('baseBody', 'base');
  initFileViewClicks('headBody', 'head');
  buildFileNavIndex('base');
  buildFileNavIndex('head');
  updateFileNavCounts('base');
  updateFileNavCounts('head');
}

/* mark + centre a line in one pane */
function focusLine(paneId, lineno, opts) {
  const pane = $(paneId);
  const go = () => {
    pane.querySelectorAll('tr.focusrow').forEach((x) => x.classList.remove('focusrow'));
    // `keepUnit` preserves the existing method outline: when the user clicks
    // around inside a file view we only move the focus marker, we do not want
    // the outline to flicker off and back on.
    if (!opts || !opts.keepUnit) {
      pane.querySelectorAll('tr.inunit').forEach((x) => x.classList.remove('inunit'));
    }
    if (opts && opts.range) {
      pane.querySelectorAll('tr.inunit').forEach((x) => x.classList.remove('inunit'));
      const [a, b] = opts.range;
      for (let k = a; k <= b; k++) {
        const tr = pane.querySelector(`tr[data-n="${k}"]`);
        if (tr) tr.classList.add('inunit');
      }
    }
    if (!lineno) return;
    const tr = pane.querySelector(`tr[data-n="${lineno}"]`);
    if (!tr) return;
    tr.classList.add('focusrow');
    if (!opts || !opts.noScroll) {
      pane.scrollTop = Math.max(0, tr.offsetTop - pane.clientHeight / 3);
    }
  };
  if (pane.dataset.ready === '1') go();
  else pending[paneId] = go;
}

/* place both panes according to the resolve result */
function applyResolved(opts) {
  const r = S.resolved;
  if (!r) return;
  const keepView = !!(opts && opts.keepViewport);
  let bLine = null, hLine = null;
  let bRange = null, hRange = null;

  if (r.mode === 'unit') {
    bRange = [r.base_unit.start, r.base_unit.end];
    hRange = [r.head_unit.start, r.head_unit.end];
  }
  // the row the user actually clicked, and its counterpart
  const rows = r.rows || [];
  const row = (r.focus !== null && r.focus !== undefined) ? rows[r.focus] : null;
  if (row) {
    bLine = row.base_no;
    hLine = row.head_no;
  }
  if (r.side === 'base') {
    if (bLine === null) bLine = r.lineno;
  } else if (hLine === null) {
    hLine = r.lineno;
  }
  // The clicked line often has no counterpart on the other side (a pure
  // insertion or deletion).  Walk outwards from it to the nearest aligned row
  // so the other pane still lands next to the right place instead of staying
  // at the top of the file.
  const nearest = (key) => {
    if (r.focus === null || r.focus === undefined) return null;
    for (let d = 1; d < rows.length; d++) {
      const a = rows[r.focus - d];
      if (a && a[key] !== null && a[key] !== undefined) return a[key];
      const b = rows[r.focus + d];
      if (b && b[key] !== null && b[key] !== undefined) return b[key];
    }
    return null;
  };
  if (bLine === null) bLine = nearest('base_no');
  if (hLine === null) hLine = nearest('head_no');
  // last resort: the top of the matched unit / region
  if (bLine === null && bRange) bLine = bRange[0];
  if (hLine === null && hRange) hLine = hRange[0];
  if (bLine === null && r.base_unit) bLine = r.base_unit.start;
  if (hLine === null && r.head_unit) hLine = r.head_unit.start;

  // When the user clicked in a file view, *their* line wins: the server's
  // notion of the focus row is derived from a heuristic alignment and must not
  // override an explicit click.
  if (keepView) {
    if (S.pinned.base) bLine = S.pinned.base;
    if (S.pinned.head) hLine = S.pinned.head;
  }

  focusLine('baseBody', bLine, { range: bRange, noScroll: keepView });
  focusLine('headBody', hLine, { range: hRange, noScroll: keepView });

  // Pin the breadcrumbs to the matched units.  Use the resolved unit's own
  // class path when available: it is authoritative, whereas classAt() is a
  // line-range lookup that could disagree for a nested class.
  S.pinned = { base: bLine, head: hLine };
  setBreadcrumbFromUnit('base', r.base_unit, bLine);
  setBreadcrumbFromUnit('head', r.head_unit, hLine);

  renderMatchbar();
}

/* Prefer the unit's declared class over a positional lookup. */
function setBreadcrumbFromUnit(side, unit, lineno) {
  const el = $(side === 'base' ? 'baseCls' : 'headCls');
  if (!unit || unit.kind === 'region') {
    setBreadcrumb(side, lineno, { pinned: true });
    return;
  }
  const clsPath = unit.cls;                 // e.g. "TestDictDataLoaderDevice"
  const clsName = clsPath ? clsPath.split('.').pop() : null;
  const parts = [];
  if (clsName) {
    parts.push(`<span class="cls pinned">${esc(clsName)}</span>`);
  } else {
    parts.push('<span class="cls none">&lt;module level&gt;</span>');
  }
  if (unit.kind !== 'class_header') {
    parts.push(`<span class="sep">.</span><span class="meth">${esc(unit.name)}</span>`);
  }
  el.innerHTML = parts.join('');
  const cls = clsPath ? classAt(side, unit.def_line) : null;
  el.title = clsPath
    ? `${clsPath}(${cls ? (cls.bases || []).join(', ') : ''})  method lines ${unit.start}-${unit.end}`
    : `${unit.name}  lines ${unit.start}-${unit.end}`;
}

let scrollLock = false;
function linkPaneScroll() {
  const a = $('baseBody'), b = $('headBody');
  // Proportional linking: the two files have different lengths, so locking
  // raw scrollTop would drift.  Only sync when the user asks for it.
  const mk = (src, dst) => () => {
    if (!S.lockScroll || scrollLock) return;
    scrollLock = true;
    const sMax = src.scrollHeight - src.clientHeight;
    const dMax = dst.scrollHeight - dst.clientHeight;
    dst.scrollTop = sMax > 0 ? (src.scrollTop / sMax) * dMax : 0;
    requestAnimationFrame(() => { scrollLock = false; });
  };
  a.onscroll = mk(a, b);
  b.onscroll = mk(b, a);
}

/* ----------------------------------------------------------------- matchbar */

function renderMatchbar() {
  const r = S.resolved;
  const bar = $('matchbar');
  bar.hidden = false;
  $('layout').classList.remove('nobar');

  const st = r.stats || {};
  const parts = [];
  const clicked = r.side === 'base'
    ? `removed line ${r.lineno}`
    : `added line ${r.lineno}`;

  if (r.mode === 'unit') {
    const bu = r.base_unit, hu = r.head_unit;
    let kind = 'in place';
    if (r.moved && r.renamed) kind = 'moved + renamed';
    else if (r.moved) kind = 'moved';
    else if (r.renamed) kind = 'renamed';
    parts.push(`<span class="dim">${clicked}:</span>`);
    parts.push(`<b>${esc(bu.qualname)}</b> <span class="arrow">\u2192</span> <b>${esc(hu.qualname)}</b>`);
    parts.push(pill(kind, r.moved || r.renamed ? 'move' : 'ok'));
    parts.push(pill(VERDICT_LABEL[r.verdict] || r.verdict, VERDICT_CLASS[r.verdict]));
    parts.push(pill(`body ${Math.round((r.body_score ?? 0) * 100)}%`, r.body_score >= 0.9 ? 'ok' : 'warn'));
    if (!r.mutual) parts.push(pill('ambiguous target', 'warn'));
    const real = (st.changed || 0) + (st.missing || 0);
    parts.push(pill(real ? `${real} line(s) to review` : 'no real line change', real ? 'bad' : 'ok'));
    if (st.device) parts.push(pill(`${st.device} device-only`, 'warn'));
    if (st.indent) parts.push(pill(`${st.indent} indent-only`, 'ok'));
  } else if (r.mode === 'line') {
    parts.push(`<span class="dim">${clicked}</span> matched by content <span class="dim">(${Math.round(r.score * 100)}%)</span>`);
    parts.push(pill(VERDICT_LABEL[r.verdict] || r.verdict, VERDICT_CLASS[r.verdict]));
    parts.push(pill('outside any test method', 'warn'));
  } else {
    parts.push(`<span class="dim">${clicked}</span>`);
    if (r.side === 'head') {
      parts.push(pill('new code \u2014 nothing to compare against', 'bad'));
      parts.push(`<span class="dim">${esc((r.src_unit || {}).qualname || '')}</span>`);
    } else {
      parts.push(pill(r.message || 'no counterpart', 'bad'));
    }
  }
  $('mbInfo').innerHTML = parts.join(' ');

  const sel = $('candSelect');
  const cands = r.candidates || [];
  if (cands.length > 1) {
    sel.hidden = false;
    sel.innerHTML = cands.map((c) =>
      `<option value="${c.uid}" ${c.uid === r.chosen_uid ? 'selected' : ''}>` +
      `${esc(c.qualname)}  \u2014 ${Math.round(c.score * 100)}% ${c.mutual ? '\u2713' : ''}</option>`
    ).join('');
  } else {
    sel.hidden = true;
    sel.innerHTML = '';
  }
}

/* -------------------------------------------------------------- interaction */

async function resolveLine(side, lineno, targetUid, opts) {
  S.side = side; S.lineno = lineno;
  status('matching\u2026');
  try {
    S.resolved = await api('resolve', {
      ref: S.ref, path: S.path, side, line: lineno,
      target: targetUid || '', context: 8,
    });
    // When the user clicked inside a file view they have already told us where
    // they want to look; re-centring on the *unit* would yank the viewport away
    // from the line they picked.  Keep their line, just add the outline and the
    // match bar.
    applyResolved({ keepViewport: !!(opts && opts.fromFileView) });
    status('');
  } catch (e) {
    status(e.message, true);
  }
}

function stepDiff(dir) {
  const r = S.resolved;
  if (!r || !r.rows) return;
  const rows = r.rows;
  let i = (r.focus ?? -1) + dir;
  while (i >= 0 && i < rows.length) {
    if (!BENIGN.has(rows[i].verdict)) {
      r.focus = i;
      applyResolved();
      return;
    }
    i += dir;
  }
  status(dir > 0 ? 'no further difference' : 'no earlier difference');
}

/* ---------------------------------------------------------- refactor map UI */

function renderSummary() {
  const s = S.data.summary;
  const cm = S.data.class_map || {};
  const out = [];

  const splits = (s.classes || []).filter((c) => c.targets && c.targets.length);
  out.push('<h4>class mapping (derived from method bodies, not names)</h4>');
  out.push('<table><tr><th>base class</th><th>&rarr; head class(es)</th><th>methods</th></tr>');
  splits.forEach((c) => {
    const t = c.targets.map((x) =>
      `${esc(x.cls)} <span class="dim">(${x.methods}, ${Math.round(x.share * 100)}%)</span>`
    ).join('<br>');
    out.push(`<tr><td>${esc(c.base_cls)}${c.split ? ' ' + pill('split', 'move') : ''}</td>` +
             `<td>${t}</td><td class="dim">${c.n_methods}</td></tr>`);
  });
  out.push('</table>');

  if ((s.new_classes || []).length) {
    out.push('<h4>new classes in the PR</h4>');
    out.push('<table><tr><th>head class</th><th>&larr; source(s)</th><th>bases</th></tr>');
    s.new_classes.forEach((c) => {
      const src = (c.sources || []).map((x) =>
        `${esc(x.cls)} <span class="dim">(${x.methods})</span>`).join('<br>') || '<span class="dim">\u2014 (new code)</span>';
      out.push(`<tr><td>${esc(c.head_cls)}</td><td>${src}</td><td class="dim">${esc((c.bases || []).join(', '))}</td></tr>`);
    });
    out.push('</table>');
  }

  const attention = (s.units || []).filter((u) =>
    u.verdict === 'changed' || u.verdict === 'missing' || !u.mutual);
  out.push(`<h4>methods needing attention (${attention.length} of ${(s.units || []).length})</h4>`);
  out.push('<table><tr><th>base</th><th>&rarr; head</th><th>kind</th><th>verdict</th><th>real lines</th></tr>');
  attention.forEach((u) => {
    const changed = u.stats ? (u.stats.changed + u.stats.missing) : '';
    out.push(
      `<tr class="jump" data-line="${u.base.def_line}" data-uid="${u.head ? u.head.uid : ''}">` +
      `<td>${esc(u.base.qualname)}</td>` +
      `<td>${u.head ? esc(u.head.qualname) : '<span class="dim">\u2014</span>'}</td>` +
      `<td class="dim">${esc(u.kind)}</td>` +
      `<td>${pill(VERDICT_LABEL[u.verdict] || u.verdict, VERDICT_CLASS[u.verdict])}</td>` +
      `<td class="dim">${changed}</td></tr>`
    );
  });
  out.push('</table>');

  const clean = (s.units || []).filter((u) =>
    u.mutual && (u.verdict !== 'changed' && u.verdict !== 'missing') && u.kind.startsWith('moved'));
  out.push(`<h4>verbatim moves \u2014 safe to skim (${clean.length})</h4>`);
  out.push('<table><tr><th>base</th><th>&rarr; head</th><th>verdict</th></tr>');
  clean.forEach((u) => {
    out.push(
      `<tr class="jump" data-line="${u.base.def_line}" data-uid="${u.head ? u.head.uid : ''}">` +
      `<td>${esc(u.base.qualname)}</td><td>${esc(u.head.qualname)}</td>` +
      `<td>${pill(VERDICT_LABEL[u.verdict] || u.verdict, VERDICT_CLASS[u.verdict])}</td></tr>`
    );
  });
  out.push('</table>');

  $('overlayBody').innerHTML = out.join('');
  $('overlayBody').querySelectorAll('tr.jump').forEach((tr) => {
    tr.addEventListener('click', () => {
      $('overlay').hidden = true;
      const line = parseInt(tr.dataset.line, 10);
      resolveLine('base', line, tr.dataset.uid || null);
      const dr = $('diffBody').querySelector(`tr[data-side="base"][data-line="${line}"]`);
      if (dr) {
        selectRow(dr);
        $('diffBody').scrollTop = Math.max(0, dr.offsetTop - $('diffBody').clientHeight / 3);
      }
    });
  });
  $('overlay').hidden = false;
}

/* -------------------------------------------------------------- load flow */

async function loadPR(ref, refresh) {
  status('fetching PR\u2026');
  S.ref = ref;
  S.meta = await api('pr', { ref, refresh: refresh ? 1 : 0 });
  $('prTitle').textContent = `#${S.meta.number} ${S.meta.title}`;
  const link = $('prLink');
  link.href = S.meta.url; link.hidden = false;

  const sel = $('fileSelect');
  const files = S.meta.files.filter((f) => !f.binary);
  sel.innerHTML = files.map((f) =>
    `<option value="${esc(f.path)}">${esc(f.path)}  (+${f.additions}/\u2212${f.deletions})</option>`
  ).join('');
  const first = S.meta.reviewable[0] || (files[0] && files[0].path);
  if (!first) { status('no reviewable files', true); return; }
  sel.value = first;
  await loadFile(first);
  history.replaceState(null, '', `?pr=${S.meta.number}&file=${encodeURIComponent(first)}`);
}

async function loadFile(path) {
  status('analysing\u2026');
  S.path = path;
  S.resolved = null;
  S.linemap = null;
  S.data = await api('file', { ref: S.ref, path });
  if (S.data.unsupported) {
    $('diffBody').innerHTML = `<div class="placeholder">${esc(S.data.message)}</div>`;
    $('baseBody').innerHTML = '';
    $('headBody').innerHTML = '';
    status('');
    return;
  }
  renderDiff();
  $('matchbar').hidden = true;
  $('layout').classList.add('nobar');
  const pe = S.data.summary.base_parse_error || S.data.summary.head_parse_error;
  const url = new URL(location.href);
  url.searchParams.set('file', path);
  history.replaceState(null, '', url);

  // the whole-file views are a larger payload; load them after the diff is up
  $('baseBody').innerHTML = '<div class="placeholder">loading base file\u2026</div>';
  $('headBody').innerHTML = '<div class="placeholder">loading PR file\u2026</div>';
  status('loading files\u2026');
  try {
    S.linemap = await api('linemap', { ref: S.ref, path });
    renderFileViews();
    status(pe ? `parse warning: ${pe}` : '', !!pe);
  } catch (e) {
    $('baseBody').innerHTML = `<div class="placeholder">${esc(e.message)}</div>`;
    $('headBody').innerHTML = '';
    status(e.message, true);
  }
}

/* --------------------------------------------------------------- resizing */

function initResize() {
  document.querySelectorAll('.gutter').forEach((g) => {
    g.addEventListener('mousedown', (e) => {
      e.preventDefault();
      const which = g.dataset.resize;
      const layout = $('layout');
      const move = (ev) => {
        const total = layout.clientWidth;
        const x = ev.clientX;
        if (which === '1') {
          const pct = Math.min(80, Math.max(15, (x / total) * 100));
          $('paneDiff').style.flex = `0 0 ${pct}%`;
        } else {
          const dl = $('paneDiff').getBoundingClientRect().right;
          const rest = total - dl;
          const pct = Math.min(85, Math.max(15, ((x - dl) / rest) * 100));
          $('paneBase').style.flex = `0 0 ${(rest * pct) / 100}px`;
        }
      };
      const up = () => {
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
        document.body.style.cursor = '';
      };
      document.body.style.cursor = 'col-resize';
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    });
  });
}

/* ------------------------------------------------------------------- wiring */

function init() {
  initResize();

  $('loadBtn').onclick = () => {
    const v = $('prInput').value.trim();
    if (v) loadPR(v, false).catch((e) => status(e.message, true));
  };
  $('prInput').onkeydown = (e) => { if (e.key === 'Enter') $('loadBtn').click(); };
  $('refreshBtn').onclick = () => {
    if (S.ref) loadPR(S.ref, true).catch((e) => status(e.message, true));
  };
  $('fileSelect').onchange = (e) => loadFile(e.target.value).catch((err) => status(err.message, true));
  $('hideBenign').onchange = (e) => { S.hideBenign = e.target.checked; applyFilters(); };
  $('onlyNew').onchange = (e) => { S.onlyNew = e.target.checked; applyFilters(); };
  $('lockScroll').onchange = (e) => { S.lockScroll = e.target.checked; };
  $('summaryBtn').onclick = () => { if (S.data) renderSummary(); };
  const closeOverlay = () => { $('overlay').hidden = true; };
  $('overlayClose').onclick = closeOverlay;
  // clicking the backdrop closes; clicking inside the card must not
  $('overlay').onclick = (e) => {
    if (!$('overlayCard').contains(e.target)) closeOverlay();
  };
  $('candSelect').onchange = (e) => resolveLine(S.side, S.lineno, e.target.value);
  $('nextDiffBtn').onclick = () => stepDiff(1);
  $('prevDiffBtn').onclick = () => stepDiff(-1);
  $('nextChangeBtn').onclick = () => stepChange(1);
  $('prevChangeBtn').onclick = () => stepChange(-1);
  $('nextRealBtn').onclick = () => stepReal(1);
  $('prevRealBtn').onclick = () => stepReal(-1);
  for (const side of ['base', 'head']) {
    $(`${side}NextChangeBtn`).onclick = () => stepFileNav(side, 'change', 1);
    $(`${side}PrevChangeBtn`).onclick = () => stepFileNav(side, 'change', -1);
    $(`${side}NextRealBtn`).onclick = () => stepFileNav(side, 'real', 1);
    $(`${side}PrevRealBtn`).onclick = () => stepFileNav(side, 'real', -1);
  }

  document.addEventListener('keydown', (e) => {
    // Escape must always work, even while typing in the PR box, so the
    // overlay can never trap the user.
    if (e.key === 'Escape') { closeOverlay(); return; }
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

    // pane 1 navigation
    //   n / p        next / previous *real* change   (the review queue)
    //   Shift+J / K  next / previous change of any kind
    //   j / k        next / previous differing row inside the current pair
    if (e.key === 'n') { stepReal(1); return; }
    if (e.key === 'p') { stepReal(-1); return; }
    if (e.key === 'J') { stepChange(1); return; }
    if (e.key === 'K') { stepChange(-1); return; }
    if (e.key === 'j') { stepDiff(1); return; }
    if (e.key === 'k') { stepDiff(-1); return; }

    if (e.key === 'm' && S.data) renderSummary();
    if (e.key === 'h') { $('hideBenign').click(); }
    if (e.key === 'a') { $('onlyNew').click(); }
    if (e.key === 'l') { $('lockScroll').click(); }
  });

  const qs = new URLSearchParams(location.search);
  const pr = qs.get('pr');
  if (pr) {
    $('prInput').value = pr;
    loadPR(pr, false).then(() => {
      const f = qs.get('file');
      if (f && f !== S.path) {
        $('fileSelect').value = f;
        return loadFile(f);
      }
    }).catch((e) => status(e.message, true));
  }
}

init();
