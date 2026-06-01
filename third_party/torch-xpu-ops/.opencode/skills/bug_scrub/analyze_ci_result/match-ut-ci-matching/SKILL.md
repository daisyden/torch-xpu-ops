# PASS 1: CI Result Matching

## Base Path Reference

Relative paths from `${BUG_SCRUB_SKILL_ROOT}`:
```
analyze_ci_result/test_cases/run_processor_steps.py  → in-skill Phase 2 Test Cases runner
../../../ci_results/                                  → CI artifacts directory
../../../result/                                      → Excel results directory
```

## Overview
Collect and match CI test results for stock PyTorch and torch-xpu-ops into test_cases_all.xlsx.

## Workflow
1. Create test_cases_all.xlsx with 'stock' and 'torch-xpu-ops' sheets
2. Collect stock CI test cases from PyTorch repository
3. Collect torch-xpu-ops CI test cases from third_party/torch-xpu-ops
4. Match test cases between stock and xpu CI results

## Usage
```bash
cd "${BUG_SCRUB_SKILL_ROOT}/analyze_ci_result/test_cases"
python3 run_processor_steps.py --steps 1
```

## Input
- Stock CI test files from PyTorch repo (`test/`)
- XPU CI test files from torch-xpu-ops (`third_party/torch-xpu-ops/test/xpu/`)

## Output
- `test_cases_all.xlsx` with stock and torch-xpu-ops sheets
- 'Test Cases' sheet in torch_xpu_ops_issues.xlsx updated with CI matching info

## Related Files
- `${BUG_SCRUB_SKILL_ROOT}/analyze_ci_result/test_cases/run_processor_steps.py`
- `${BUG_SCRUB_SKILL_ROOT}/analyze_ci_result/test_cases/pass1_ci_matcher.py`

## Downstream
Issues where **every** row this skill produces has `XPU Status` blank or
`not found` are picked up by Phase 2.5
[`local-case-verification`](../local-case-verification/SKILL.md), which runs
`pytest -k <case>` (with `PYTORCH_TEST_WITH_SLOW=1`) locally and writes a
single aggregated `Local status` value on the Issues sheet. This skill's
per-row outputs remain the CI-authoritative source and are never modified by
2.5.