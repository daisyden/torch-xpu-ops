# check_xpu_case_existence regression test

End-to-end regression test for the `check_xpu_case_existence` skill (Phase 2.4).

Each fixture asks the live skill (via `opencode run`) to classify one Test Cases
row and verifies the verdict + explanation tokens match a known-good baseline
that was committed to `result/torch_xpu_ops_issues.xlsx` during the 2026-05
Phase 2.4 pass.

## Fixtures (`fixtures.json`)

| Name | Row | Issue | Expected | Why it is a good regression target |
|---|---|---|---|---|
| `true_dtypesIfCUDA_intercept`     | 1376 | #2186 | True  | The True verdict only holds if the skill discovers that `XPUPatchForImport.__enter__` rebinds `dtypesIfCUDA` to `get_dtypesIf_mock('cuda')`, which writes `fn.dtypes['xpu']` (xpu_test_utils.py:855-970,1143). A shallow grep-only classifier would emit False. |
| `false_distributed_not_in_skip_dict` | 1404 | #1556 | False | Tests that the skill correctly recognizes the distributed-harness gating rule: a file not keyed in `third_party/torch-xpu-ops/test/xpu/skip_list_dist.py`'s `skip_dict` is never invoked by `run_distributed.py`. |

## Running

```bash
bash run_regression.sh                              # all fixtures
bash run_regression.sh true_dtypesIfCUDA_intercept  # single fixture
```

Env overrides: `PYTORCH_SRC`, `OPENCODE_MODEL`, `OPENCODE_BIN`, `SKILL_DIR`.

## Cost

Each fixture spawns one `opencode run` invocation that internally fires the
skill's mandatory explore sub-agent. Plan ~1-3 minutes per fixture and real
LLM tokens. Intended for manual pre-release runs, not per-commit CI.

## Pass criteria

For every fixture:
1. The agent emits `XPU_CASE_EXIST: <True|False>` matching `expected_exist`.
2. The full agent output contains the fixture's `must_contain_token`.
3. The full agent output contains at least one token from `must_contain_any`.

Failing tokens indicate the skill returned the right True/False by accident,
without doing the deep analysis the skill is designed to do.

## Logs

Per-run output saved to `_runs/<timestamp>/<fixture_name>.{out,err}`.
