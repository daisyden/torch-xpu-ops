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
```

## Files

| File | Purpose |
|------|---------|
| `run_processor_steps.py` | Canonical CLI runner for Test Cases Phase 2 steps |
| `pass1_ci_matcher.py` | Phase 2.1: collect stock/XPU CI XML and match Test Cases rows |
| `pass5_duplicate_detection.py` | Phase 2.3 duplicate detection implementation |

## Runner Steps

1. `PASS 1`: create/load `test_cases_all.xlsx`, collect stock and torch-xpu-ops CI results, match CI status.
2. `PASS 3`: print the worklist for `check_xpu_case_existence/SKILL.md`; no automated LLM endpoint is called and no classification is performed. Each listed row must be completed through the skill's explore-agent assisted deep analysis workflow.
3. `PASS 5`: cross-issue duplicate detection.

## Columns Processed

| Column | Header |
|--------|--------|
| 8 | Error Message |
| 9 | Traceback |
| 12 | XPU Status |
| 13 | Stock Status |
| 14 | No Match Reason |
| 16 | CUDA Case Exist |
| 17 | XPU Case Exist |
| 18 | case_existence_comments |
| 19 | can_enable_on_xpu |
| 20 | duplicated_issue |
