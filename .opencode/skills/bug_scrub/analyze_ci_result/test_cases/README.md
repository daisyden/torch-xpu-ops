# Test Cases Processor Module

## Overview

This module contains the Phase 2 Test Cases runner and implementation for the `Test Cases` sheet in `torch_xpu_ops_issues.xlsx`.

## Canonical Usage

```bash
cd "${BUG_SCRUB_SKILL_ROOT}/analyze_ci_result/test_cases"
python3 run_processor_steps.py --list
python3 run_processor_steps.py --steps 1       # Phase 2.1 UT CI matching
python3 run_processor_steps.py --steps 3       # Phase 2.4 worklist for check_xpu_case_existence
python3 run_processor_steps.py --steps 1 3 5   # Test Cases Phase 2 path

# With incremental mode (skip already-processed rows):
python3 run_processor_steps.py --steps 1 3 5 --incremental
```

## Files

| File | Purpose |
|------|---------|
| `run_processor_steps.py` | Canonical CLI runner for Test Cases Phase 2 steps |
| `pass1_ci_matcher.py` | Phase 2.1: collect stock/XPU CI XML and match Test Cases rows; extract test_class/test_case from pytest paths; route issues without test cases to Others sheet |
| `pass5_duplicate_detection.py` | Phase 2.5 duplicate detection implementation |

## Runner Steps

1. `PASS 1`: create/load `test_cases_all.xlsx`, collect stock and torch-xpu-ops CI results, match CI status.
   - **[NEW]** Attempt to extract test_class and test_case from test_file path if blank
   - **[NEW]** Route non-UT issues without test cases to Others sheet with reason "No unittest test case found"
2. `PASS 3`: print the worklist for `check_xpu_case_existence/SKILL.md`; no automated LLM endpoint is called and no classification is performed. Each listed row must be completed through the skill's explore-agent assisted deep analysis workflow.
3. `PASS 5`: cross-issue duplicate detection.

## Columns Processed

| Column | Header |
|--------|--------|
| 2 | Test Reproducer |
| 4 | Test File |
| 6 | Test Class |
| 7 | Test Case |
| 8 | Error Message |
| 9 | Traceback |
| 10 | XPU Status |
| 11 | Stock Status |
| 12 | No Match Reason |
| 13 | XPU Case Exist |
| 14 | case_existence_comments |
| 15 | duplicated_issue |
| 16 | Local Status |

## New Features (v2.0)

### Test Case Extraction
When `test_class` and/or `test_case` are blank, Phase 2.1 attempts to extract them from the `test_file` path:
- **Format**: `path/to/file.py::ClassName::method_name` (pytest format)
- **Extraction**: Parses `::` delimiters to populate test_class and test_case columns
- **Fallback**: If extraction fails, row is routed to Others sheet

### Others Sheet Routing
Issues without extractable test cases are automatically moved to the Others sheet:
- **Trigger**: `test_type != 'e2e'` AND `test_case` blank after extraction attempt
- **Action**: Copy to Others sheet with reason, delete from Test Cases sheet
- **Purpose**: Explicit audit trail for manual review in Phase 2.4

### Incremental Mode
Use `--incremental` flag to skip rows with already-filled result columns:
- Useful for re-running Phase 2 after partial failures
- Skips rows with non-blank `duplicate_group_id` or `xpu_case_existence` (Phase 2.5+)
- Speeds up re-runs by avoiding redundant processing

## Examples

### Test Case Extraction
```
Input:  test_file = "torch-xpu-ops/test/xpu/dynamo/test_ctx_manager_xpu.py::CtxManagerTests::test_cuda_event_method"
Output: test_class = "CtxManagerTests", test_case = "test_cuda_event_method"
```

### Others Sheet Routing
```
Issue #3388: "[Bug Skip] XPU Dynamo Graph Lowering - stream_index None"
Status: Blank test_case after extraction attempt
Action: Moved to Others sheet with reason "No unittest test case found"
```

## Environment Variables

- `ISSUE_TRIAGE_ROOT`: Override default issue_triage root directory (default: finds via directory traversal)
  ```bash
  export ISSUE_TRIAGE_ROOT=/path/to/issue_triage
  python3 run_processor_steps.py --steps 1
  ```
