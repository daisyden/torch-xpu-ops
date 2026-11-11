# Review notes (accumulated reviewer guidance)

This file is an append-only log of reviewer guidance collected from the review
web UI. Two kinds of entries land here:

- **Per-case comments** (the per-case **comment** box, persisted in
  `<slug>/reviews.json` under `comments`): a case a reviewer flagged for rework
  and the instruction they left.
- **Common prompts** (the file-level **Common prompt** box, persisted in
  `<slug>/file.json` `file_prompt`): a standing instruction for every case in that
  file. Appended here as soon as it is saved.

Purpose: turn one-off reviewer feedback into durable, searchable guidance so the
`enable-xpu-tests` agent learns from past rework (common skip-removal patterns,
how a reviewer wants borderline cases classified, etc.). Recurring themes here
are candidates to promote into `SKILL.md`.

How entries get here: every `tools/rework_queue.py` run appends new comments and
prompts (newest at the bottom), and the review server appends a prompt the moment
"Save prompt" is clicked. Both are idempotent. Do not edit past entries by hand;
add distilled lessons under "Distilled lessons" instead.

## Distilled lessons

<!-- Promote recurring guidance from the log below into short rules here. -->
- A skipped or OpInfo-gated case is never classified from its skip: remove the
  skips, measure, then cite an existing issue or draft one if it fails; remove the
  skips for good if it passes. (from test_ops.py test_errors_where_cuda review)
- Never credit a case to the patch without a run that shows the patch is what made
  it pass: measure the plain base first. (test_ops.py fft cases already passed on
  the base; the old PR description wrongly said the op_db change enabled them.)
- A closed issue cited by a skip is not evidence of anything: re-measure with the
  skip removed. Several old skips pointed at closed trackers (#2722, #3126, #6088)
  while the failure still reproduces; retarget them to the open tracker or draft one.
- The verdict must match the latest measured result: a case that PASSES with its gate
  removed is Portable, and the patch must contain the gate change that lets it run.
  Watch for host-wide gates: `@skipIfXpu` skips on any machine with an XPU, even for
  device=cpu instantiations; use device-scoped `@skipXPUIf(True, ...)` instead.
  (inductor/test_aot_inductor_arrayref review)
- A case that the shipped code still skips is never Portable, even if it passes with
  the gate removed (inductor/test_cpu_repro: host gcc-13 gate kept -> Not Applicable).
- Prefer a narrow device-generic class for the CSV cases over enabling a large
  class (allow_xpu on TestNNDeviceType would have instantiated ~2300 XPU tests).

## Log

<!-- rework_queue.py appends entries below this line. -->
