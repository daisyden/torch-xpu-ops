# Verdicts, colours, and what to actually scrutinise

Every line of both revisions is classified. The verdict drives the colour, the
`real change` navigation, and the Refactor map's attention list.

## The verdicts

| verdict | meaning | colour in panes 2/3 | in the review queue? |
| --- | --- | --- | --- |
| `identical` | byte-identical to its counterpart | no tint | no |
| `blank` | blank on both sides | no tint | no |
| `indent` | differs only in whitespace | dark grey rail | no |
| `device` | device-only change (see below) | amber | no |
| `rename` | differs only by device words in identifiers | violet | **yes** |
| `changed` | genuine logic difference | red (base) / green (head) | **yes** |
| `missing` | exists on one side only | orange | **yes** |

`real change` = not `identical`, `blank`, `indent` or `device`.

Within a coloured line only the differing words are highlighted, so a 100-char
line that changed one argument shows just that argument.

## What counts as "device-only"

These are normalised away when comparing, because they are the expected
mechanics of this refactor:

- `def test_x(self)` → `def test_x(self, device)`
- `hw_classification = HardwareClassification.CUDA`
- `torch.cuda.X` → `torch.get_device_module(device).X`
- `self.device`, `self.device_type`, `device=...`
- `allow_xpu=True`, `only_for=[...]`, `except_for=[...]`
- device words inside identifiers: `TestFooCUDA` vs `TestFooXPU`

## Traps: benign-looking changes that still need a human

The tool deliberately classifies these as non-blocking, but they are exactly where
correctness bugs hide. Verify the *intent*, not just the text:

**1. `device` argument without registration.**
`def test_x(self, device)` only receives a device if the class is registered via
`instantiate_device_type_tests(...)`. If the class was split but the registration
call was not updated, the test silently stops running or errors. The registration
call is a module-level "free line" in pane 1 — click it.

**2. Wrong `hw_classification`.**
A CUDA-only test labelled `GENERIC` will be attempted on XPU and fail; an
accelerator-agnostic test labelled `CUDA` silently stops running on XPU. Check
each new class's label against what its tests actually require.

**3. `torch.cuda` → `torch.get_device_module(device)`.**
Classified `changed` (correctly), but confirm the replacement is equivalent —
some `torch.cuda` APIs have no generic counterpart.

**4. Helpers copied into several classes.**
`setUp`, `_get_data_loader` and friends are often duplicated into the new class
while remaining in the old one. The match bar shows `ambiguous target` and the
**alternatives** dropdown lets you inspect each copy. Verify the copies are
actually identical — a divergent `setUp` changes behaviour for a whole class.

**5. Skips that changed meaning.**
`@unittest.skipIf(not TEST_CUDA)` moved into a class that now also runs on XPU may
skip everything, or nothing. Read the decorators on a moved method, not only its
body.

**6. `rename` is in the queue on purpose.**
A method renamed `test_cuda_x` → `test_accelerator_x` is usually fine, but a
rename that collides with an existing method in the destination class silently
overrides it. That is why `rename` counts as a real change.

## Interpreting the counters

```
pane 1  CHANGE 55   REAL CHANGE 7
pane 2  CHANGE  5   REAL CHANGE 2
pane 3  CHANGE 20   REAL CHANGE 5
```

- Pane 1 counts blocks of consecutive `-`/`+` lines belonging to one method, so a
  moved 20-line test is **one** change, not 20.
- Panes 2 and 3 count within their own file. A line counts as a change on a side
  when its verdict differs **or** the diff lists it as removed (pane 2) / added
  (pane 3). The second clause matters: in a pure move the base lines are
  byte-identical to their new home, so they carry no tint yet are exactly what the
  PR deleted.
- `REAL CHANGE` is always a **subset** of `CHANGE`, never larger. If you ever see
  it equal or exceed `CHANGE`, the server is stale — see troubleshooting.md.

## Limits of the guarantee

The matcher achieved 100% top-1 pairing on 13942 name-agnostic ground-truth pairs
across 46 PRs, but that is a measurement, not a proof. Two honest caveats:

- Matching is **same-file only**. A method moved into a *new* file shows as
  `no match` / `new code` rather than being paired.
- A method rewritten so heavily that body similarity falls below the 0.35
  threshold surfaces as `new code`. The failure mode errs toward showing you
  more, not hiding things — but do not treat a low counter as proof of safety.
