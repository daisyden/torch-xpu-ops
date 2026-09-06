---
name: test-pr-reviewer
description: Review PyTorch test-refactor / test-decoupling PRs where a test class was split by device (TestFoo becomes TestFooCUDA / TestFooXPU / TestFooDevice) and methods were moved between classes. Starts a local 3-pane web reviewer that matches moved test methods by content, so pure moves are separated from real logic changes. Use when the user asks to review a test refactor or decoupling PR, wants to know which moved tests actually changed, says "review PR 189250", "start the review server", "open the refactor review tool", "which changes are real in this PR", or is reviewing a PR whose diff is mostly relocated test methods.
---

# PyTorch Test-Refactor PR Reviewer

Reviewing a test-decoupling PR on GitHub is impractical: splitting

```
TestFoo  ->  TestFoo (generic) + TestFooCUDA + TestFooXPU + TestFooDevice
```

relocates hundreds of lines, so GitHub shows one huge block of deletions and one
huge block of additions with **no link between them**. You cannot tell which
deletions are verbatim moves and which hide a behaviour change.

This skill launches a local web tool that rebuilds that link and answers the only
question that matters: *which of these changes do I actually have to read?*

On PR #189250 it reduces 337 changed diff lines to **7 real changes**; on #195840,
225 methods to **3** that actually changed.

Detailed references:
- [review-workflow.md](references/review-workflow.md) — the recommended review order
- [verdicts.md](references/verdicts.md) — what each colour/verdict means and what to scrutinise
- [troubleshooting.md](references/troubleshooting.md) — stale server, proxy, blank panes

## Prerequisites

- `gh` CLI, authenticated (`gh auth status`) — used to fetch PR metadata and diffs.
- `python3` 3.10+ — no third-party packages required.
- Optional but recommended: a local `pytorch` clone, so full file contents come
  from `git` instead of the GitHub API. Auto-detected at `~/pytorch`; override
  with `REFACTOR_REVIEW_CLONE=/path/to/pytorch`.
- Optional, for the UI test suite only: `node` + `npm install jsdom`.

## Quick start

```bash
cd .opencode/skills/test_pr_reviewer/scripts

./start.sh 189250      # start and print the URL for that PR
./status.sh            # is it running, reachable, and running current code?
./restart.sh           # reload after editing server.py / matcher.py / prdata.py
./stop.sh              # stop it
./selftest.sh          # verify the checkout (matcher accuracy + UI tests)
```

Then open the printed URL, e.g. `http://<host>:8765/?pr=189250`.

`PORT=8899 ./start.sh` uses another port. `HOST=127.0.0.1 ./start.sh` binds
loopback only, for use with an SSH tunnel.

**After editing any `.py` file you must `./restart.sh`.** Python modules are
loaded at startup, so a running process keeps serving old API responses even
though `static/*` is re-read per request. `./status.sh` detects this and says
`STALE`.

## What the three panes show

| pane | content |
| --- | --- |
| 1 | the PR diff, GitHub-style, each `-`/`+` line tagged `moved` / `renamed` / `new code` |
| 2 | the **whole base file**, coloured like a normal diff tool |
| 3 | the **whole head (PR) file**, coloured the same way |

Clicking a line in **any** pane locates the corresponding line in the other two.
Panes 2 and 3 are complete files, so you can scroll for context.

Each pane has **change** and **real change** ▲/▼ buttons with counters.
`real change` skips anything the tool proved harmless — a verbatim move, a
device-only edit, an indentation change. That is the review queue.

Every pane header shows the **enclosing test class**, which is the substance of
these PRs:

```
pane 2   TestDictDataLoader . test_pin_memory
pane 3   TestDictDataLoaderDevice . test_pin_memory
```

Press `m` for the **Refactor map**: the derived class mapping, the list of methods
needing attention, and the verbatim moves that are safe to skim.

## How the matching works (and why not by name)

Class names are **not** used as the key. A survey of 46 real PRs shows they are
unreliable:

```
FakeTensorPropTest        -> TestFakeTensorPropDevice        (word reorder + suffix)
TestCudaTrace             -> TestGpuTraceDevice              (device word swapped)
LoggingTests              -> TestLogging / TestLoggingCUDA / TestLoggingDevice
TestQuantizedOps          -> TestQuantizedOpsCUDNN + ...Device
                             + _QuantizedActivationTestMixin        (1 -> N)
TestQuantizeFx + TestQuantizeFxModels -> TestQuantizeFxCUDASpecific (N -> 1)
test_cuda_memory_usage    -> test_accelerator_memory_usage   (method renamed)
setUp / _get_data_loader  -> copied into the new class, kept in the old one
```

Instead, `matcher.py` parses both revisions with `ast` into per-method units and
scores pairs **body-first** (70% device-normalised line similarity, 20% name,
10% class name). Names only break ties. Matching is not forced 1:1, so 1→N copies
and N→1 merges stay visible. The class mapping is *derived* from method votes.

Measured against those 46 PRs, using ground truth computed independently of the
matcher:

```
method pairing (strict top-1)   13942 / 13942   100.00 %
   incl. 703 cross-class moves and 11 renamed methods
class mapping (primary source)    809 /   811    99.75 %
class headers paired             1130 /  1130   100.00 %
line-map consistency          534644 lines      0 problems
```

## Sharing with several reviewers

One server serves several people reviewing **different** PRs at once — the PR is
in the URL, so there is no per-user state. Per-PR locks keep concurrent cold
loads parallel (4 users: 2.7 s, not 9.6 s), and the analysis caches are LRU with
per-key locks so users do not evict each other's work.

Caveats: there is **no authentication** (anyone who can reach the port can drive
it, and it runs `gh`/`git` as the user who started it — prefer an SSH tunnel off
a trusted network), and CPU-bound analysis does not scale across cores because of
the GIL. `GET /api/health` reports cached PRs, cache sizes and thread count.

## Reporting the result

When reporting a review, lead with the reduction and the queue, not the raw diff:

1. the class mapping (what was split into what),
2. how many methods need attention out of the total,
3. for each, the specific lines that differ and why they matter,
4. the device-only edits worth a sanity check — `def test_x(self, device)` only
   works if the class is registered with `instantiate_device_type_tests`, and
   `hw_classification` must match the test's real hardware requirement.

Do **not** claim a PR is clean because the counters are low; open each flagged
method and read it. See [verdicts.md](references/verdicts.md) for the traps that
are deliberately classified benign.

## Testing the tool itself

```bash
./selftest.sh              # matcher accuracy + UI DOM tests
./selftest.sh --full       # 10 PRs instead of 3
./selftest.sh --live       # also API contract + concurrency against the server
```

The UI tests load the **real** `static/app.js` in jsdom and fail on any uncaught
script error; earlier versions re-implemented the render logic and so missed a
helper deleted from the shipped file.
