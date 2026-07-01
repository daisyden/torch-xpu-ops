# PASS 1: CI Result Matching

> **Base constraints**: This subskill is governed by [`../../base-constraints.md`](../../base-constraints.md) (C1 logs->agent_space, C2 ask-on-blocker, C3 background status, C4 no over-skip). They apply even when not restated here.

## Base Path Reference

Relative paths from `${BUG_SCRUB_SKILL_ROOT}`:
```
analyze_ci_result/test_cases/run_processor_steps.py  → in-skill Phase 2 Test Cases runner
../../../ci_results/                                  → CI artifacts directory
../../../result/                                      → Excel results directory
```

## Overview
Collect and match CI test results for stock PyTorch and torch-xpu-ops into test_cases_all.xlsx.
Automatically extract test_class and test_case from test file paths when they are blank, and route
issues without extractable test cases to the Others sheet for manual review.

## Workflow
1. Create test_cases_all.xlsx with 'stock' and 'torch-xpu-ops' sheets
2. Collect stock CI test cases from PyTorch repository
3. Collect torch-xpu-ops CI test cases from third_party/torch-xpu-ops
4. Match test cases between stock and xpu CI results
5. **[NEW]** Extract test_class and test_case from test file path (pytest format: `file.py::Class::method`)
6. **[NEW]** Route non-UT issues without extractable test cases to Others sheet with reason

## Usage
```bash
cd "${BUG_SCRUB_SKILL_ROOT}/analyze_ci_result/test_cases"
python3 run_processor_steps.py --steps 1

# With incremental mode (skip already-processed rows):
python3 run_processor_steps.py --steps 1 --incremental
```

## Input
- Stock CI test files from PyTorch repo (`test/`)
- XPU CI test files from torch-xpu-ops (`third_party/torch-xpu-ops/test/xpu/`)

## Output
- `test_cases_all.xlsx` with stock and torch-xpu-ops sheets
- 'Test Cases' sheet in torch_xpu_ops_issues.xlsx updated with CI matching info
- **[NEW]** 'Others' sheet populated with issues that have no unittest test case (reason: "No unittest test case found")

## Test Case Extraction

This skill now extracts test_class and test_case from test file paths in pytest format.

### Extraction Logic
When `test_class` and/or `test_case` are blank:
1. Check if `test_file` contains `::` delimiters (pytest format)
2. If yes, parse as: `file.py::ClassName::method_name`
   - Extract `ClassName` → `test_class`
   - Extract `method_name` → `test_case`
3. If extraction succeeds, populate columns and continue matching
4. If extraction fails, route to Others sheet

### Examples
- Input: `test_file = "test/dynamo/test_ctx_manager.py::CtxManager::test_method"`
- Output: `test_class = "CtxManager"`, `test_case = "test_method"`

- Input: `test_file = "test/dynamo/test_ctx_manager.py"` (no `::`)
- Output: No extraction (already blank or insufficient info)

## Others Sheet Routing

Issues without extractable test cases are automatically routed to the Others sheet:

### When Routing Occurs
- Issue has `test_type != 'e2e'` (unittest or non-categorized)
- After extraction attempt, `test_case` is still blank
- Original row deleted from Test Cases sheet to avoid duplication

### Row Mapping (Test Cases → Others)
| Test Cases Column | Others Column | Value |
|---|---|---|
| Issue ID | ID | issue_id |
| Title / Test Reproducer | Title | title or test_reproducer |
| - | reproduce step | "No unittest test case found" |

## Related Files
- `${BUG_SCRUB_SKILL_ROOT}/analyze_ci_result/test_cases/run_processor_steps.py`
- `${BUG_SCRUB_SKILL_ROOT}/analyze_ci_result/test_cases/pass1_ci_matcher.py`
- `extract_test_case_from_path()` function for pytest path parsing

## Downstream
1. **For Issues in Test Cases sheet (with extracted test_case)**:
   Issues where every row has `XPU Status` blank or `not found` are picked up by Phase 2.5
   [`local-case-verification`](../local-case-verification/SKILL.md), which runs
   `pytest -k <case>` (with `PYTORCH_TEST_WITH_SLOW=1`) locally and writes a
   single aggregated `Local status` value on the Issues sheet. This skill's
   per-row outputs remain the CI-authoritative source and are never modified by 2.5.

2. **For Issues in Others sheet (no test_case extracted)**:
   Handled by Phase 2.4 [`check_xpu_case_existence`](../check_xpu_case_existence/SKILL.md)
   for deep analysis and manual investigation.

## Changes in v2

### v2.0 - Test Case Extraction & Others Routing
- **Added**: `extract_test_case_from_path()` to parse pytest format paths
- **Added**: Automatic extraction of test_class/test_case from test_file when blank
- **Added**: Others sheet routing for issues without extractable test cases
- **Changed**: Phase 2.1 no longer skips rows with blank test_case silently
- **Impact**: 
  - Test Cases sheet: Cleaner rows with valid test_case values only
  - Others sheet: Explicit audit trail of issues needing manual review
  - Detection: 10 issues (5 unique) rerouted from Test Cases to Others in initial run