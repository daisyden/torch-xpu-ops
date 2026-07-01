# Match E2E Test Status from Inductor Reports

> **Base constraints**: This subskill is governed by [`../../base-constraints.md`](../../base-constraints.md) (C1 logs->agent_space, C2 ask-on-blocker, C3 background status, C4 no over-skip). They apply even when not restated here.

## Base Path Reference

Relative paths from `${BUG_SCRUB_SKILL_ROOT}`:
```
analyze_ci_result/e2e_test_cases/run_match_e2e_status.py  → in-skill Phase 2 E2E runner
../../../ci_results/                                      → CI artifacts directory
../../../result/                                          → Excel results directory
```

## Overview
This skill matches E2E test cases in torch_xpu_ops_issues.xlsx to accuracy status from Inductor_E2E_Test_Report.xlsx files.
Optionally fetches torchbench model names from the pytorch/benchmark GitHub repository to enrich the model matching process.

## Input Parameters
- **Excel File**: `${BUG_SCRUB_SKILL_ROOT}/../../../result/torch_xpu_ops_issues.xlsx`
- **Base Dir**: `${BUG_SCRUB_SKILL_ROOT}/../../../ci_results/torch-xpu-ops/`
- E2E report folders within base dir contain Inductor_E2E_Test_Report.xlsx
- **[NEW]** GitHub torchbench models from pytorch/benchmark repo (optional)

## Usage
```bash
cd "${BUG_SCRUB_SKILL_ROOT}/analyze_ci_result/e2e_test_cases"

# With GitHub torchbench model fetching (default)
python run_match_e2e_status.py --excel "${BUG_SCRUB_SKILL_ROOT}/../../../result/torch_xpu_ops_issues.xlsx" --base-dir "${BUG_SCRUB_SKILL_ROOT}/../../../ci_results/torch-xpu-ops/"

# Skip GitHub model fetching if gh CLI not available
python run_match_e2e_status.py --no-github-models

# Preserve already-filled cells
python run_match_e2e_status.py --skip-filled
```

## Workflow
1. **[NEW]** Optionally fetch torchbench models from GitHub via `gh` CLI
   - Directories: `pytorch/benchmark/torchbenchmark/models` + `canary_models`
2. Load E2E Test Cases sheet from torch_xpu_ops_issues.xlsx
3. Parse Inductor_E2E_Test_Report.xlsx files from report directories
4. Extract benchmark (huggingface/timm/torchbench), dtype, phase (inf/tra), AMP mode
5. Build status map with model name variants for fuzzy matching
6. Merge GitHub torchbench models into status map (if fetched)
7. Match each E2E test case to accuracy status with fallback logic

## Key Features
- **GitHub TorchBench Integration**: **[NEW]** Fetches model names from pytorch/benchmark repo
  - Improves coverage for torchbench models
  - Non-blocking: Falls back to E2E reports only if fetch fails
  - Models fetched: from models + canary_models (26)
- **Model name fuzzy matching**: Handles variants like `AllenaiLongformerBase` ↔ `allenailongformerbase`
- **Dtype fallback**: bfloat16 → float32 → float16
- **AMP fallback**: AMP enabled → AMP disabled
- **Phase handling**: inference (inf), training (tra)

## Reports Format
Inductor_E2E_Test_Report.xlsx sheets named as:
- `<benchmark>_<dtype>_<inf|tra>_acc` (e.g., `huggingface_float32_inf_acc`)
- `<benchmark>_<dtype>_<inf|tra>_amp_acc` (e.g., `huggingface_amp_bf16_tra_acc`)

## Output
Column "XPU Accuracy Status" populated with values:
- `pass`, `pass_due_to_skip`, `fail`, `accuracy_mismatch`, etc.
- `Status not found` if no matching entry exists
- `E2E report not found` if reports cannot be loaded

## Requirements for GitHub Model Fetching
- `gh` CLI installed: https://cli.github.com/
- GitHub CLI authenticated (or public API endpoint available)
- Network access to api.github.com
- If unavailable: Use `--no-github-models` flag to skip

## Known Limitations
- Corrupt timm report files will result in unmatched entries
- Model naming mismatches may occur (e.g., `hf_Roberta_base` vs `RobertaForCausalLM`)
- Unknown benchmarks cannot be matched
- GitHub fetch requires `gh` CLI; will fail gracefully if not installed

## Changes in v2.1

### GitHub TorchBench Model Integration
- **Added**: Automatic fetching of torchbench models from pytorch/benchmark repository
- **Added**: `fetch_torchbench_models_from_github()` function using `gh` CLI
- **Added**: `--no-github-models` flag to skip GitHub fetching
- **Changed**: Status map now pre-populated with GitHub torchbench models (if available)
- **Impact**: Improved E2E model matching coverage by ~31 models

## Downstream
Issues where **every** row this skill produces has `XPU Status` blank /
`not found` / `E2E report not found` are picked up by Phase 2.5
[`local-case-verification`](../local-case-verification/SKILL.md), which runs
the Phase 1.1-extracted reproducer locally and writes a single aggregated
`Local status` value on the Issues sheet. This skill's per-row outputs remain
the CI-authoritative source and are never modified by 2.5.
