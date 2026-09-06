# PyTorch test-refactor review tool

Reviewing a test-decoupling PR on GitHub is painful: a class split like

```
TestFoo  ->  TestFoo (generic) + TestFooCUDA + TestFooXPU + TestFooDevice
```

moves hundreds of lines, so GitHub shows one giant block of deletions and one
giant block of additions with no link between them. You cannot tell which
deletions are verbatim moves and which hide a real behaviour change.

This tool rebuilds that link and shows three panes per file:

| pane | content |
| --- | --- |
| 1 | the PR diff, GitHub-style, with each `-`/`+` line tagged `moved` / `renamed` / `new code` |
| 2 | the **whole base file**, coloured like a normal diff tool |
| 3 | the **whole head (PR) file**, coloured like a normal diff tool |

Panes 2 and 3 are complete file views, not extracts: clicking a diff line scrolls
both of them to the matched location and outlines it, and you can then scroll
freely in either pane to read as much surrounding context as you want.

### Navigation works in every direction

Clicking is not limited to pane 1. Clicking any line in pane 2 or pane 3:

- marks that line in its own pane,
- locates and marks the corresponding line in the *other* file view, using the
  precomputed counterpart pointers,
- selects and scrolls to the matching row in the PR diff, revealing it if a
  filter had hidden it, and syncs the change / real-change counters.

Your clicked line stays put: the viewport is not re-centred on the enclosing
method, because you have already said where you want to look. A line the diff
never mentions (unchanged context outside every hunk) has no diff row, so pane 1
scrolls to the nearest hunk and the status bar says so.

### The class name is always visible

Each of panes 2 and 3 carries a breadcrumb showing the **enclosing test class**
and method, because which class a test ended up in *is* the substance of these
PRs. Selecting `test_pin_memory` in PR #189250 shows:

```
pane 2  TestDictDataLoader . test_pin_memory
pane 3  TestDictDataLoaderDevice . test_pin_memory
```

so the split is readable at a glance without hunting upwards for the `class`
statement. The class name comes from the matched unit itself (authoritative),
and hovering it reveals the full dotted path, base classes and line range.

When you scroll away for context the breadcrumb follows the viewport, so you
always know which class the code on screen belongs to; it keeps showing the
selected method while that method is still on screen.

### Colours in panes 2 and 3

Every line of both files is classified, so the colour tells you what kind of
change it is rather than merely that a change exists:

| colour | meaning |
| --- | --- |
| no tint | identical on both sides |
| dark grey rail | indentation only |
| amber | device-only difference (`device` arg added, `hw_classification`, `torch.cuda` → `torch.get_device_module(device)`) |
| violet | rename only (identifiers differ by device words) |
| red (pane 2) / green (pane 3) | a real difference — review this |
| orange | exists on this side only |

Within a coloured line, the exact differing words are highlighted, so a 100-char
line that changed one argument shows just that argument.

The blue-tinted block marks the matched method you jumped to, and the clicked
line's number is highlighted in blue.

Click any removed or added line in pane 1; panes 2 and 3 jump to the matched
test method in both revisions and highlight exactly what differs.

### Removed vs added lines

Both directions work, and both are useful:

- Click a **removed (`−`)** line → "where did this code go?" Panes 2/3 show the
  base method and the head method it moved into.
- Click an **added (`+`)** line → "where did this code come from?" Panes 2/3
  show the base method it came from and the new head method. The badge arrow
  points backwards (`moved ← TestFoo.test_x`) to reflect the direction.

Measured over the 46 sample PRs, the 19816 added lines break down as:

| what an added line turns out to be | share |
| --- | --- |
| in-place edit of a method that stayed put | 64.4 % |
| moved/renamed from a base method (nothing new) | 27.1 % |
| free line: import, `instantiate_device_type_tests(...)`, blank | 6.5 % |
| genuinely new code with no base counterpart | **2.1 %** |

So ~91% of additions are provably re-arranged existing code. Use **only new
code (+)** (or `a`) to show just the 2.1% that has no base counterpart and
therefore has to be read line by line; those lines are badged `new code`.

New class declarations are handled too: `class TestFooDevice(TestCase):` is
paired with the base class its methods came from, so clicking it shows the
class-header diff (decorators, base class, `hw_classification`) rather than
falling back to a fuzzy text match.

## Install / run

Requires Python 3.10+, `gh` (authenticated), and ideally a local clone of the
repo. No third-party Python packages, no npm.

```bash
# point at your clone so full file contents come from git instead of the API
export REFACTOR_REVIEW_CLONE=~/pytorch

./start.sh 189250               # start, print the URL for that PR
./status.sh                     # running? reachable? current code?
./restart.sh                    # reload after editing any .py file
./stop.sh                       # stop it
./selftest.sh                   # verify this checkout

# or drive the server directly
./server.py 189250
./server.py --port 8899 --repo pytorch/pytorch --no-browser
```

`PORT=8899 ./start.sh` picks another port; `HOST=127.0.0.1 ./start.sh` binds
loopback only (for use with an SSH tunnel).

> **After editing `server.py`, `matcher.py` or `prdata.py` you must restart.**
> Python modules are loaded into memory at startup, so a running process keeps
> serving the old API even though `static/*` (HTML/CSS/JS) is re-read from disk
> on every request. A stale process is easy to mistake for a UI bug: the pane 1
> nav would show `REAL CHANGE` equal to `CHANGE`, because the client never
> received any line verdicts. `restart.sh` kills the old process, verifies it is
> gone, import-checks the modules and waits for the new one to answer. The UI
> also detects this case and shows *"server is out of date"* in the status bar.

Accepted PR references: `189250`, `pytorch/pytorch#189250`, or a full
`https://github.com/pytorch/pytorch/pull/189250` URL.

PR metadata, diffs and file blobs are cached under
`~/.cache/pytorch-refactor-review` (override with `REFACTOR_REVIEW_CACHE`), so
reopening a PR is instant. The `↻` button re-fetches from GitHub.

## Sharing one server with several reviewers

Yes — one server handles several people reviewing **different** PRs at the same
time. There is no per-user state on the server: the PR is in the URL
(`?pr=189250&file=...`), so each browser drives its own view and two users can
sit on different PRs, or on different files of the same PR, without interfering.

Verified with `dev/test_concurrency.py`: six simultaneous users on six different
PRs each get the correct file back, a cached request is not blocked by another
user's heavy analysis (0.02 s while a 27k-line file was being analysed), and
users do not evict each other's cached work.

What makes that hold:

- **Per-PR locks, not a global one.** `load_pr` shells out to `gh`, which takes
  seconds. A single global lock made four users opening four different PRs take
  4 x latency; now they are served together (measured 9.6 s -> 2.7 s). Two
  requests for the *same* PR still share one fetch.
- **LRU analysis caches with per-key locks.** The caches used to `.clear()` when
  full, throwing away everyone's analysis; they now evict only the
  least-recently-used entry (64 matches / 128 models). Two users opening the
  same file wait on one analysis instead of both running it.
- **Serialised `git fetch`, parallel reads.** `git fetch` takes repo-wide locks,
  so fetches into one clone are serialised per clone; `git show` reads are not.
- **Atomic cache writes.** Blob and metadata files are written to a temp file and
  renamed, so a concurrent reader never sees a half-written file.

`GET /api/health` reports cached PRs, cache sizes and thread count.

### Limits to be aware of

- **No authentication.** Anyone who can reach the port can drive the tool, and it
  runs `gh`/`git` as the user who started it. Fine on a trusted lab network;
  otherwise put it behind an SSH tunnel per user (see above).
- **CPU-bound analysis is not parallel across cores.** Python's GIL means two
  simultaneous *cold* analyses of large files interleave rather than using two
  cores. Cached files are unaffected. If several people routinely analyse big
  files at once, run one server per user on different ports instead.
- **Shared disk cache.** All users share `~/.cache/pytorch-refactor-review`, which
  is a benefit (one fetch serves everyone) but means one user's `↻` refresh
  re-fetches for everyone.

## How matching works

Class names are **not** used as the key. A survey of 46 real refactor PRs shows
they are unreliable:

```
FakeTensorPropTest        -> TestFakeTensorPropDevice        (word reorder + suffix)
TestCudaTrace             -> TestGpuTraceDevice              (device word swapped)
LoggingTests              -> TestLogging / TestLoggingCUDA / TestLoggingDevice
TestQuantizedOps          -> TestQuantizedOpsCUDNN + ...Device
                             + _QuantizedActivationTestMixin        (1 -> N)
TestQuantizeFx + TestQuantizeFxModels -> TestQuantizeFxCUDASpecific (N -> 1)
TestVarlenAttention       -> _VarlenVsSdpaMixin + ...CuDNN + ...Device
test_cuda_memory_usage    -> test_accelerator_memory_usage   (method renamed)
test_weight_sharing_gpu   -> test_weight_sharing
setUp / _get_data_loader  -> copied into the new class, kept in the old one
```

Instead (`matcher.py`):

1. Both revisions are parsed with `ast` into **units** — one per method or
   module-level function, plus a class-header unit — so every line has an owner.
2. Units are scored **body-first** (70% device-normalised line-sequence
   similarity, 20% name, 10% class name). Names only break ties, so a device
   word swap or a full rename does not break the match.
3. Matching is **not forced 1:1**. The mutual-best pair is the primary; the
   other strong candidates stay available in the `alternatives` dropdown, which
   is how 1→N copies and N→1 merges are represented honestly.
4. The class mapping shown in the *Refactor map* is **derived by aggregating
   method votes**, never from names.

Each aligned row gets a verdict:

| verdict | meaning | reviewer action |
| --- | --- | --- |
| `identical` | byte-identical | none |
| `indent only` | whitespace only | none |
| `device-only change` | e.g. `def test_x(self)` → `def test_x(self, device)`, `hw_classification = ...`, `torch.cuda` → `torch.get_device_module(device)` | glance |
| `rename only` | differs only by device words in identifiers | glance |
| `real change` | genuine logic difference | **review** |
| `no counterpart` | line vanished / appeared with no match | **review** |

## Measured accuracy

Validated against the 46 PRs you supplied, using ground truth computed
independently of the matcher (body-similarity voting, name-agnostic):

```
method pairing (strict top-1)   13942 / 13942   100.00 %
   incl. 703 cross-class moves and 11 renamed methods
class mapping (primary source)    809 /   811    99.75 %
   the 2 misses are validator artifacts on boilerplate setUp/tearDown
class headers paired             1130 /  1130   100.00 %
   incl. 63 device renames and 9 rename/splits
head units with a candidate     17476 / 17500    99.9 %
```

Practical effect on PR #189250: 337 changed diff lines collapse to **0 methods
needing attention** — every deletion is proven to be a verbatim move. On
#195840, 225 methods collapse to **3** that actually changed.

Reproduce with the dev harnesses:

```bash
cd dev
python3 _prefetch.py 189250 195730 195840        # warm the cache
python3 -W ignore _validate2.py 189250 195730    # strict name-agnostic pairing
python3 -W ignore _validate_cls.py 189250        # class mapping
python3 -W ignore _survey.py 189250              # show raw class transformations
```

## UI reference

- **Click** a `-` or `+` line in pane 1 → panes 2/3 show the matched unit.
- **Refactor map** (or `m`) → derived class mapping, methods needing attention,
  and the list of verbatim moves that are safe to skim.
- **hide benign diff lines** (or `h`) → collapse lines whose unit is a clean
  in-place match, leaving only moves/renames/unmatched.
- **only new code (+)** (or `a`) → show only added lines that have no base
  counterpart: the ~2% of a refactor PR that is actually new.
### Navigating pane 1

Two navigation tracks sit above the diff, each with a position counter:

| control | keys | walks |
| --- | --- | --- |
| **change** | `Shift+J` / `Shift+K` | every block the PR touched |
| **real change** | `n` / `p` | only blocks that are *not* provably benign |

Navigation moves by **block** — a run of consecutive `-`/`+` lines belonging to
one method — because a moved 20-line test is one thing to review, not 20. A
block counts as a *real change* only when the matcher could not prove it
harmless, i.e. it is not a verbatim move and not a device-only or
indentation-only edit. Blank lines never become review targets.

The effect on a pure-move PR (#189250):

```
55 change blocks  ->  7 real changes
```

Those 7 are the new import, the renamed class headers and the
`instantiate_device_type_tests(...)` call — exactly the parts that carry meaning.

- `j` / `k` → next/previous differing row *inside* the currently selected pair.

### Navigating panes 2 and 3

Each file view has the **same two tracks** over its own file, so you can walk one
revision independently of the diff:

| track | walks |
| --- | --- |
| **change** | every line the PR touched on this side, plus any line whose verdict differs |
| **real change** | only the blocks that are not provably benign |

A line counts as a change on a given side when either its own verdict differs
*or* the diff lists it as removed (pane 2) / added (pane 3). That second clause
matters: in a pure move the base lines are byte-identical to their new home, so
they carry no tint, yet they are exactly the lines the PR deleted.

`real change` is always a **subset** of `change`, never a separate partition —
one long change block can contain several real runs separated by benign lines, so
each such block yields one real entry positioned on its first real line.

Using either track also locates the line in the other file view and in the diff,
and the counters in all three panes stay in sync.

Typical counts for #189250 (a pure move):

```
pane 1  CHANGE 55   REAL CHANGE 7
pane 2  CHANGE  5   REAL CHANGE 2
pane 3  CHANGE 20   REAL CHANGE 5
```
- **link scroll** (or `l`) → scroll panes 2 and 3 together (proportionally, since
  the two files differ in length). Off by default so you can read context on one
  side without disturbing the other.
- **alternatives** dropdown → repoint panes 2/3 at another candidate when a
  helper was copied into several classes.
- Drag the two vertical gutters to resize the panes; panes 2 and 3 scroll and
  hover in lockstep.

## Files

```
server.py        stdlib HTTP server; /api/pr, /api/file, /api/linemap, /api/resolve
prdata.py        gh + git fetch of PR metadata, diff, base/head blobs; caching
                 (detects GitHub's truncated diffs and rebuilds them locally)
matcher.py       AST units, content-first pairing, alignment, whole-file line map
static/          index.html, app.css, app.js  (no build step)
dev/             validation harnesses (python) and UI tests (node + jsdom)
```

## Tests

```bash
cd dev
# matcher accuracy against ground truth computed independently
PYTHONPATH=.. python3 -W ignore _validate2.py 189250 195730 195840
PYTHONPATH=.. python3 -W ignore _validate_cls.py 189250
PYTHONPATH=.. python3 -W ignore _validate_linemap.py 189250 195155
PYTHONPATH=.. python3 -W ignore _validate_added.py 189250

# UI tests (real DOM + real CSS via jsdom)
cd .. && node dev/test_overlay.js
node dev/test_fileview.js  /path/to/linemap.json
node dev/test_ui_e2e.js    /path/to/fixture-dir
node dev/test_nav.js       /path/to/fixture-dir
node dev/test_reverse.js   /path/to/fixture-dir
node dev/test_pane_nav.js  /path/to/fixture-dir

# contract test against the RUNNING server (catches a stale process)
python3 dev/test_api_contract.py --host 127.0.0.1 --port 8765

# multi-user behaviour against the running server
python3 dev/test_concurrency.py --host 127.0.0.1 --port 8765
```

`test_ui_e2e.js` loads the **real `app.js`** into a DOM, stubs `fetch` with
recorded API responses and drives an actual click, failing on any uncaught
script error. Earlier UI tests re-implemented the render logic and therefore
could not catch a helper accidentally deleted from `app.js`; this one can.
Build a fixture directory with `pr.json`, `file.json`, `linemap.json` and
`resolve.json` dumped from the `server.api_*` functions.

`_validate_linemap.py` checks, for every line of both files: array lengths,
symmetry of the counterpart pointers, that the unified diff's own context lines
are marked identical, and that word-level segments are present exactly where a
line differs. It reports **0 problems over 534k lines across all 46 sample PRs**.
