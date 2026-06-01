# issue-basic-info-extraction regression tests

End-to-end regression tests for the `prepare_data/issue-basic-info-extraction`
skill, exercising both routing paths it produces in `generate_excel.py`:

| Sub-folder | Routing path | Fixture | What it verifies |
|---|---|---|---|
| [`test_cases_extraction/`](test_cases_extraction/) | UNITTEST GATE (Test Cases sheet) | issue #1171 | The schema extractor identifies the exact `(test_file, test_class, test_method)` triple and emits one row to `Test Cases`. |
| [`others_repro_extraction/`](others_repro_extraction/) | OTHERS GATE (Others sheet, kind=other) | issue #2200 | The skill classifies `kind=other` and preserves the multi-line python reproducer **verbatim**, including the `SDPBackend.MATH/FLASH_ATTENTION/EFFICIENT_ATTENTION` lines required by MANDATORY rule 1. |

Both call into [`_runner.sh`](_runner.sh), which sends the live LLM the same
Schema + MANDATORY rules block from `SKILL.md`, then parses the returned JSON
and asserts against the fixture's `expected` block.

## Running

```bash
bash test_cases_extraction/run_regression.sh
bash others_repro_extraction/run_regression.sh
```

Env overrides: `OPENCODE_MODEL`, `OPENCODE_BIN`, `SKILL_DIR`.

## Cost

~1 LLM call per fixture, ~30-60s each.

## Pass criteria

The agent must emit exactly one JSON object matching the schema. The runner
asserts:

- `kind` matches expected.
- `test_cases` shape: either empty (Others fixture) or matches expected
  `test_file_contains` / `test_class` / `test_method`.
- `reproducer` contains every token in `expected.reproducer_must_contain`.

A regression in the LLM prompt or merging logic typically surfaces as either a
wrong `kind`, a fabricated `test_case`, or a paraphrased reproducer that drops
the required tokens — all caught here.

## Logs

Per-run output saved to `<fixture-dir>/_runs/<timestamp>/run.{out,err}`.
