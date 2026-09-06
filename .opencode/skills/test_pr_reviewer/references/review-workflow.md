# Review workflow

The order below is deliberate: it establishes *shape* before *detail*, so you
never read a relocated method line by line.

## 1. Get the shape of the refactor first

Press **`m`** (Refactor map). This is the part GitHub cannot give you. For
PR #189250 it shows:

```
TestDataLoader           -> TestDataLoader, TestDataLoaderCUDA
TestDataLoaderDeviceType -> TestDataLoaderDevice
TestDictDataLoader       -> TestDictDataLoader, TestDictDataLoaderDevice
```

Check this against the PR's stated intent. If the author said "split the CUDA
tests out" but a CUDA-only test landed in the generic class, it is visible here
immediately — and that is a real bug, because the generic class runs on every
backend.

## 2. Read the "methods needing attention" table

Same dialog, second table. This is the review queue; everything absent from it is
proven to be a verbatim move.

- #189250 → the table is **empty** (all 244 methods moved unchanged).
- #195840 → **3 of 225**.
- #195155 → **10 of 14** (that PR genuinely rewrites most methods).

Click any row to jump to it.

## 3. Inspect each flagged method

Panes 2 and 3 show base vs head. Press **`n`** to jump to the next real
difference, skipping identical / indent / device-only rows. The match bar states
the verdict, e.g. `8 line(s) to review`.

Because panes 2 and 3 are whole files, scroll freely for context — the breadcrumb
in each header keeps telling you which class you are in.

## 4. Check what is genuinely new

Tick **only new code (+)** (or press `a`). This filters pane 1 to added lines with
no base counterpart — about 2% of a typical refactor PR:

| what an added line turns out to be | share |
| --- | --- |
| in-place edit of a method that stayed put | 64.4 % |
| moved/renamed from a base method | 27.1 % |
| free line (import, `instantiate_device_type_tests`, blank) | 6.5 % |
| genuinely new code | **2.1 %** |

## 5. Spot-check the moves you are trusting

The map's third table lists verbatim moves. Click one and confirm the match bar
reads `no real line change`. Doing two or three is enough to build confidence in
the matching for that file.

## 6. Multi-file PRs

Use the file dropdown in the header. Each file is analysed independently; the
counters and Refactor map are per file. A PR touching 31 files (like #188963)
still resolves quickly because analysis is cached per file.

## Keyboard reference

| key | action |
| --- | --- |
| `n` / `p` | next / previous **real change** (pane 1) |
| `Shift+J` / `Shift+K` | next / previous change of any kind (pane 1) |
| `j` / `k` | next / previous differing row inside the selected pair |
| `m` | Refactor map |
| `h` | hide benign diff lines |
| `a` | only new code (+) |
| `l` | link the scrolling of panes 2 and 3 |
| `Esc` | close the Refactor map |
