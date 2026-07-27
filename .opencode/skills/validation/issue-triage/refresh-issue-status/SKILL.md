---
name: refresh-issue-status
description: "Re-test a previously triaged issue to refresh its reproduce status, then update collect-opens and update-label outputs. If no prior triage exists (no final_output.json), runs full issue-triage first. Use when re-validating whether a known issue still reproduces after environment updates, driver upgrades, or code changes."
---

# Refresh Issue Status

Re-runs reproduction for a previously triaged issue and refreshes downstream
artifacts (collect-opens, update-label) in the same results folder. If no
prior triage exists, bootstraps via issue-triage first.

## Inputs

| Input | Required | Default | Notes |
|---|---|---|---|
| `issue_link` | yes | — | URL or bare issue number (defaults to `intel/torch-xpu-ops`). |
| `conda_env` | yes | — | Conda env with XPU-enabled `torch`. |
| `pytorch_folder` | yes | — | Local pytorch/torch-xpu-ops checkout. |
| `repo` | no | `intel/torch-xpu-ops` | Repository owner/name for bare numbers. |

Missing any required input -> **hard-stop**.

## Results Folder

Same convention as issue-triage:

```python
issue_dir = f"agent_space/issue_triage_orchestrator/{repo.replace('/', '_')}_issue_{issue_id}"
```

All outputs are written to this directory, co-located with any existing
triage artifacts.

## Workflow

### Step 1 — Check for existing `final_output.json`

```python
final_output_path = f"{issue_dir}/final_output.json"
```

- If `final_output.json` **exists**: proceed to Step 2 (re-test).
- If `final_output.json` **does NOT exist**: proceed to Step 1b (bootstrap).

### Step 1b — Bootstrap: run full issue-triage

Delegate to issue-triage to create the initial triage:

```
task(load_skills=["validation/issue-triage"],
     run_in_background=true,
     prompt="issue_link=<issue_link>, conda_env=<conda_env>,
     pytorch_folder=<pytorch_folder>, upload=false.
     Run full issue-triage pipeline.")
```

Wait for completion. Verify `final_output.json` was created. If issue-triage
hard-stopped, propagate the hard-stop — do not continue.

After bootstrap completes, proceed to Step 2.

### Step 2 — Re-test with reproduce-issue

Read the existing `final_output.json` to extract test case information.

Build reproduce input from `extract_result.test_cases` (or `reproduce_result.results[]`
if available). Include: `test_file`, `test_class`, `test_case`, `test_type`,
`traceback` from `extract_result`.

Delegate to reproduce-issue:

```
task(load_skills=["validation/issue-triage/reproduce-issue"],
     run_in_background=false,
     prompt="Reproduce test cases for issue <issue_id>.
     Input: <reproduce_input_json>.
     conda_env=<conda_env>, pytorch_folder=<pytorch_folder>.
     If a case comes back SKIPPED with needs_skip_removal=true,
     run the skip-removal retry loop (remove-xpu-skips, then --rerun)
     for a confirmed verdict before returning.
     Output MUST include torch_version, torch_commit, xpu_available, test_time fields.")
```

**If Step 2 fails or is skipped** (no test cases, environment unavailable,
reproduce-issue errors): log the failure and **proceed to Step 4**.
Steps 4-5 are independent of reproduction and MUST still run.

### Step 3 — Update `final_output.json` with refreshed reproduce result

Read the current `final_output.json`, then:

1. Replace the `reproduce_result` section with the new results from Step 2.
2. The new `reproduce_result` MUST include top-level fields:
   - `torch_version`: from torch probe
   - `torch_commit`: from torch probe
   - `xpu_available`: from torch probe
   - `test_time`: ISO 8601 UTC timestamp when re-test ran
   - `conda_env`: the conda env used
   - `pytorch_folder`: the pytorch folder used
   - `results[]`: per-case results
3. Write back to `final_output.json`.

If Step 2 was skipped/failed, skip Step 3 and proceed to Step 4.

### Step 4 — Run collect-opens (MANDATORY — must always execute)

**This step MUST run regardless of whether Steps 2-3 succeeded, failed, or
were skipped.** Collect-opens is independent of reproduction.

Delegate to collect-opens:

```
task(load_skills=["validation/issue-triage/collect-opens"],
     run_in_background=false,
     prompt="issue_link=<issue_link>, repo=<repo>.
     Run collect-opens. Write output to <issue_dir>/collect_opens_result.json.")
```

Write the JSON result to `{issue_dir}/collect_opens_result.json`.

If delegation fails, run the script directly as fallback:
```bash
python3 .opencode/skills/validation/issue-triage/collect-opens/scripts/collect_opens.py \
    <issue_number> --repo <repo> --output <issue_dir>/collect_opens_result.json
```
Then perform Phase 2 (LLM step 3) on the output per the collect-opens skill spec.

### Step 5 — Run update-label (MANDATORY — must always execute)

**This step MUST run regardless of whether Steps 2-3 succeeded, failed, or
were skipped.** Update-label is independent of reproduction.

Delegate to update-label:

```
task(load_skills=["validation/issue-triage/update-label"],
     run_in_background=false,
     prompt="issue_link=<issue_link>, repo=<repo>,
     triage_output=<issue_dir>/final_output.json.
     Run update-label. Write output to <issue_dir>/update_label_result.json.")
```

Write the JSON result to `{issue_dir}/update_label_result.json`.

### Step 6 — Verify outputs and report completion

**Before reporting completion, verify ALL mandatory output files exist:**

```python
required_files = [
    f"{issue_dir}/final_output.json",
    f"{issue_dir}/collect_opens_result.json",
    f"{issue_dir}/update_label_result.json",
]
for f in required_files:
    assert os.path.exists(f), f"MISSING: {f} — step was skipped!"
```

If any file is missing, go back and execute the skipped step. Do NOT report
completion with missing outputs.

Print a summary:
- Issue: `#<id>` (`<repo>`)
- Reproduce result: N cases tested, M reproduced, K passed (or "skipped")
- Torch version: `<version>` (commit `<short_hash>`)
- Test time: `<timestamp>`
- Collect-opens AR: `<AR_code>` — `<AR_REASON>`
- Update-label ARs: `<count>` actions recommended

## Output Files

After completion, `{issue_dir}/` contains:

```
{issue_dir}/
├── final_output.json            # Updated reproduce_result section
├── collect_opens_result.json    # New/refreshed
├── update_label_result.json     # New/refreshed
└── ... (other existing triage artifacts preserved)
```

## Hard Stops

- Missing required inputs (`issue_link`, `conda_env`, `pytorch_folder`).
- Step 1b (bootstrap) issue-triage fails with `status == "failed-hard-stop"`.
- `gh` CLI not authenticated or not on PATH.

## Non-Blocking Failures (proceed to next step)

- Step 2 reproduce-issue fails or has no test cases → log, skip Step 3, proceed to Step 4.
- Steps 4 and 5 delegation fails → use fallback (direct script), log warning.
- Steps 4 and 5 produce errors in output → log but still write the result file.

**CRITICAL: Steps 4 and 5 must ALWAYS be attempted.** The only acceptable
reason to skip them is a hard-stop condition listed above.

## Constraints

1. Never modifies GitHub state (no comments, no labels). Analysis-only.
2. Preserves all existing triage artifacts in `issue_dir/` — only
   `reproduce_result` in `final_output.json` is overwritten.
3. `collect_opens_result.json` and `update_label_result.json` are overwritten
   on each refresh (latest state).
4. Uses `gh` CLI exclusively for GitHub API access.
5. The reproduce-issue output MUST include `torch_version`, `torch_commit`,
   `xpu_available`, and `test_time` fields.

## See Also

`issue-triage`, `reproduce-issue`, `collect-opens`, `update-label`.
