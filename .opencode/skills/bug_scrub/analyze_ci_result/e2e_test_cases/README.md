# E2E Test Cases Processor Module

## Overview

This module contains all logic for processing the 'E2E_Test_Cases' sheet in `torch_xpu_ops_issues.xlsx`.
It matches E2E test cases to accuracy status from Inductor E2E test reports, and optionally fetches
torchbench model names from the pytorch/benchmark GitHub repository to enrich model matching.

## Usage

```bash
# Basic E2E matching using E2E reports only
python3 run_match_e2e_status.py

# With GitHub torchbench model fetching (default)
python3 run_match_e2e_status.py --excel torch_xpu_ops_issues.xlsx

# Skip GitHub model fetching if gh CLI not available
python3 run_match_e2e_status.py --no-github-models

# Preserve already-filled cells
python3 run_match_e2e_status.py --skip-filled
```

## Functions

| Function | Description |
|----------|-------------|
| `fetch_torchbench_models_from_github()` | **[NEW]** Fetch torchbench model names from pytorch/benchmark GitHub repo |
| `run_match_e2e_status(excel_file, base_dir, save, skip_filled, fetch_github_models)` | Main entry point - matches E2E cases to status |
| `load_all_e2e_reports(base_dir, torchbench_models)` | Load E2E reports and merge with GitHub torchbench models |
| `normalize_key_value(value)` | Normalize key values for matching |
| `create_model_variants(name)` | Generate model name variants for matching |
| `parse_sheet_name(sheet_name)` | Parse sheet name to extract benchmark/dtype/amp/phase |

## Columns Processed

| Col | Header |
|-----|--------|
| 13 | torch-xpu-ops nightly status - accuracy |

## Input Data

### E2E Reports
E2E reports located at: `${ISSUE_TRIAGE_CI_RESULTS}/torch-xpu-ops/*E2E*/Inductor_E2E_Test_Report.xlsx`

**Supported Benchmarks:**
- `huggingface` - HuggingFace models
- `timm_models` - TIMM models
- `torchbench` - TorchBench models

**Sheet Naming Convention:**
- `{benchmark}_{dtype}_{phase}_acc` (e.g., `huggingface_float32_inf_acc`)
- `_amp_` in name indicates AMP enabled
- `inf` = inference, `tra` = training

### GitHub TorchBench Models **[NEW]**
If `--no-github-models` is not specified and `gh` CLI is available:
- Fetches directory listings from:
  - `https://github.com/pytorch/benchmark/torchbenchmark/models`
  - `https://github.com/pytorch/benchmark/torchbenchmark/canary_models`
- Merges model names into status map for improved matching
- Non-blocking: If fetch fails, continues with E2E reports only

## Processing Steps

1. **[NEW]** Optionally fetch torchbench models from GitHub via gh CLI
2. Find all `Inductor_E2E_Test_Report.xlsx` files in E2E folders
3. Parse accuracy sheets (ending with `_acc`)
4. Extract: benchmark, dtype, amp, phase, model, status
5. Build mapping: `(benchmark, dtype, amp, phase, model)` -> status
6. For each row in E2E Test Cases sheet, match and write status

## Example

```
# Input E2E report sheet: huggingface_float32_inf_acc
# Model: AlbertForMaskedLM -> Column B
# Status: pass/fail_to_run -> Column D

# In E2E Test Cases sheet:
# Row: benchmark=huggingface, model=albertbase, dtype=float32, phase=inference, amp=False
# Result: Col13 = "pass" (matching the model status from E2E report)

# GitHub torchbench models (NEW):
# Fetched: resnet50, efficientnet_b0, ... (from pytorch/benchmark repo)
# Usage: Pre-populates status map for torchbench models as 'pass' fallback
```

## Environment Variables

- `ISSUE_TRIAGE_ROOT`: Override default issue_triage root directory
- `ISSUE_TRIAGE_CI_RESULTS`: Override default CI results directory
- `RESULT_DIR` / `ISSUE_TRIAGE_RESULT_DIR`: Override default result directory

## Requirements for GitHub Model Fetching

- `gh` CLI installed and authenticated: https://cli.github.com/
- Network access to api.github.com (or gh CLI pre-authenticated)
- Optional: If not available, use `--no-github-models` to skip

## Changes in v2.1

### GitHub TorchBench Model Integration
- **Added**: `fetch_torchbench_models_from_github()` function to fetch model names from pytorch/benchmark
- **Added**: `--no-github-models` flag to skip GitHub fetching
- **Changed**: `load_all_e2e_reports()` now accepts optional torchbench_models parameter
- **Changed**: E2E matching now includes GitHub torchbench models for improved coverage
- **Impact**: 31 torchbench models fetched from GitHub and merged into status map on initial run
  - models directory: 5 models
  - canary_models directory: 26 models
