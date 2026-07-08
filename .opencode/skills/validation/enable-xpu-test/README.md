# `/enable-xpu-test` — User Guide

Enable XPU backend coverage on PyTorch test classes that are already
device-generic (accelerator-agnostic), then open a single draft PR against
`pytorch/pytorch`.

## What It Does

The orchestrator runs 5 phases for each `test_file` group:

```
Phase 0.5:  Provision conda env + pytorch checkout (if missing)
Phase 1:    Review gate — is the test class ready for XPU?
Phase 2:    Develop — apply the enablement edits
Phase 3:    Verify — run the enabled tests on XPU
Phase 4:    Analyze — only if tests had failures (else skip)
Phase 5:    Submit — open ONE combined draft PR for all passing classes
```

Classes that fail review or verification are **isolated** — they don't block
siblings. Only batch-wide errors (broken env, missing inputs) hard-stop.

## Quick Start

### Prerequisites

- A conda environment with a PyTorch XPU wheel installed
- A local `pytorch/pytorch` checkout (the test file lives there)
- `gh` authenticated with a token that can push to `daisyden/pytorch`

### One-Line Command

```
/enable-xpu-test <test_file>  test_class=<ClassName>  conda_env=<env>  pytorch_folder=<path>
```

Or with the class inline:

```
/enable-xpu-test nn/test_dropout.py::TestDropoutNNDeviceType  conda_env=my_env  pytorch_folder=~/pytorch
```

## Walkthrough (Real Example)

Let's walk through enabling `TestDropoutNNDeviceType` from
`test/nn/test_dropout.py`.

### Step 1: Understand the Target

Before running, check the test file:

```python
# test/nn/test_dropout.py (line 326)
instantiate_device_type_tests(TestDropoutNNDeviceType, globals(), allow_mps=True)
```

The class already:
- Accepts a `device` parameter in every test method
- Uses only generic PyTorch ops (no `torch.cuda.*`, no `.cuda()`, no `@onlyCUDA`)
- Has no hardcoded `device="cuda"` strings

That means it's **Strategy 2** (device-agnostic) — exactly what this
orchestrator handles.

### Step 2: Invoke the Orchestrator

```
/enable-xpu-test nn/test_dropout.py  test_class=TestDropoutNNDeviceType  \
    conda_env=classify_ut_test  pytorch_folder=~/daisy_pytorch
```

### Step 3: What Happens (Phase by Phase)

#### Phase 0.5 — Provisioning (conditional)

If the conda env or pytorch checkout is missing, `setup_env.sh` is called:

```bash
bash .opencode/skills/validation/scripts/setup_env.sh \
    nightly "classify_ut_test" "/home/daisyden/daisy_pytorch"
```

If both already exist (as in this run), provisioning is skipped and the
orchestrator logs:

```
[2026-07-08 10:10:46] phase_0.5_provision | env: exists | pytorch: exists
```

#### Phase 1 — Review Gate

The test file is reviewed by the `review-test-refactoring` skill. It checks:

| Check | What It Looks For |
|-------|-------------------|
| Classification | Is the class really Strategy 2? |
| CUDA hardening | Any `@onlyCUDA`, `.cuda()`, `device="cuda"`? |
| Naming | Does the class name follow `FooDeviceType` convention? |
| API usage | Any `torch.cuda.*` that should be `torch.accelerator.*`? |
| XPU-specific | Any bfloat16 guards, dtype issues, memory_format concerns? |

**For `TestDropoutNNDeviceType`:** The review found **0 blockers**. The only
finding was a minor one: missing `allow_xpu=True` on line 326. All test
methods are device-generic, no CUDA-specific APIs, no issues with
`channels_last` or bfloat16.

Output artifact: `agent_space/enable_xpu_orchestrator/phase1_review.json`

```json
{
  "results": [{
    "test_class": "TestDropoutNNDeviceType",
    "verdict": "pass",
    "blockers": 0
  }],
  "eligible_classes": ["TestDropoutNNDeviceType"]
}
```

#### Phase 2 — Develop Enablement

The `develop-xpu-test` skill applies **exactly three possible edit types**:

1. **Instantiation enablement** — the only change needed here
2. **Decorator parity** — mirror CUDA-only decorators to XPU (none needed)
3. **op_db widening** — extend `DecorateInfo` entries in
   `common_methods_invocations.py` (none matched → no change)

The edit is a single line in `test/nn/test_dropout.py`:

```diff
-instantiate_device_type_tests(TestDropoutNNDeviceType, globals(), allow_mps=True)
+instantiate_device_type_tests(TestDropoutNNDeviceType, globals(), allow_mps=True, allow_xpu=True)
```

That's it — only 1 insertion, no test code touched, no new skips added.

#### Phase 3 — Verify on XPU

The `verify-xpu-test` skill runs the enabled class on the XPU host:

```bash
conda run -n classify_ut_test python -m pytest \
    test/nn/test_dropout.py -v -k "TestDropoutNNDeviceType" --tb=short
```

You see output like:

```
TestDropoutNNDeviceTypeCPU::test_Dropout_cpu          PASSED
TestDropoutNNDeviceTypeCPU::test_Dropout1d_cpu        PASSED
TestDropoutNNDeviceTypeCPU::test_Dropout2d_cpu        PASSED
TestDropoutNNDeviceTypeCPU::test_Dropout3d_cpu        PASSED
TestDropoutNNDeviceTypeCPU::test_empty_dropout_cpu    PASSED
TestDropoutNNDeviceTypeXPU::test_Dropout_xpu          PASSED
TestDropoutNNDeviceTypeXPU::test_Dropout1d_xpu        PASSED
TestDropoutNNDeviceTypeXPU::test_Dropout2d_xpu        PASSED
TestDropoutNNDeviceTypeXPU::test_Dropout3d_xpu        PASSED
TestDropoutNNDeviceTypeXPU::test_empty_dropout_xpu    PASSED
```

Verdict: **verified** — 5 CPU + 5 XPU tests, all passed.

#### Phase 4 — Analyze (only on failure)

Since all tests passed, this phase is skipped. The verify counts are used
directly as the verdict.

#### Phase 5 — Submit PR

When all classes pass, a single draft PR is opened:

```bash
gh pr create \
  --repo pytorch/pytorch \
  --base viable/strict \
  --head daisyden:xpu/enable-test-dropout \
  --draft \
  --title "[XPU][Test] Enable TestDropoutNNDeviceType on XPU"
```

PR: https://github.com/pytorch/pytorch/pull/189254

| Detail | Value |
|--------|-------|
| Branch | `xpu/enable-test-dropout` |
| Base | `viable/strict` (upstream `pytorch/pytorch`) |
| Files changed | 1 (`test/nn/test_dropout.py` — +1/-1) |
| Draft? | Yes — confirm-gated before submission |
| Fork | `daisyden/pytorch` |

If a class had failures instead, Phase 5B would:
1. `check-known-issue` — is it already tracked?
2. `create-xpu-issue` — if not, file a structured issue with cross-link

## Input Reference

### Positional

| Field | Required | Example |
|-------|----------|---------|
| `test_file` | Yes | `nn/test_dropout.py` |

### Named

| Field | Required | Example |
|-------|----------|---------|
| `test_class` | Yes | `TestDropoutNNDeviceType` |
| `conda_env` | Yes | `classify_ut_test` |
| `pytorch_folder` | Yes | `~/daisy_pytorch` |

The `test_class` can be omitted from named params by inlining it in the
`test_file`:

```
/enable-xpu-test nn/test_dropout.py::TestDropoutNNDeviceType
```

## Output Format

The orchestrator returns a JSON summary:

```json
{
  "status": "passed",
  "pytorch_folder": "/home/daisyden/daisy_pytorch",
  "pr_url": "https://github.com/pytorch/pytorch/pull/189254",
  "passed_targets": ["nn/test_dropout.py::TestDropoutNNDeviceType"],
  "followup_targets": [],
  "known_issue_urls": [],
  "created_issue_urls": [],
  "per_target": [
    {
      "test_file": "nn/test_dropout.py",
      "test_class": "TestDropoutNNDeviceType",
      "review": "pass",
      "verify": "verified",
      "analysis_verdict": "passed",
      "outcome": "enabled"
    }
  ]
}
```

### `status` Values

| Status | Meaning |
|--------|---------|
| `passed` | All classes enabled, PR opened |
| `partial` | Some enabled (PR opened), rest routed to follow-up |
| `issue-follow-up` | None enabled, only issues filed (no PR) |
| `failed-hard-stop` | Batch-wide critical error (env, auth, missing inputs) |

## Logging Artifacts

All logs go under `agent_space/` in the `torch-xpu-ops` checkout:

```
agent_space/
├── session_log.txt                          # Human-readable timeline
├── logs/
│   └── background_status.log                # Subagent status
└── enable_xpu_orchestrator/
    ├── phase1_review.json
    ├── phase2_develop.json
    ├── phase3_verify.json
    ├── phase4_analyze.json                  # Only on failure
    ├── phase5_followup.json                 # Only on failure
    └── phase5_submit_pr.json
```

Each phase JSON is keyed by file-group slug with a `per-class` results array.

## Common Scenarios

### Scenario A: All Tests Pass (Happy Path)

```
/enable-xpu-test nn/test_dropout.py test_class=TestDropoutNNDeviceType \
    conda_env=classify_ut_test pytorch_folder=~/daisy_pytorch
```

→ PR opened for all classes. Took ~15 minutes.

### Scenario B: Some Tests Fail (Partial Enablement)

If `TestA` passes but `TestB` fails:

- `TestA` → verified → PR
- `TestB` → reverted → checked against known issues → filed if new
- PR body includes links to the filed issues

### Scenario C: Review Gate Blocks a Class

If the class still has `@onlyCUDA` or `.cuda()` calls:

```
/enable-xpu-test test/test_foo.py test_class=TestFoo \
    conda_env=my_env pytorch_folder=~/pytorch
```

→ Blockers reported. Class skipped. No PR (zero passing classes). Fix the
blockers, then re-run.

### Scenario D: Environment Needs Setup

```
/enable-xpu-test nn/test_dropout.py test_class=TestDropoutNNDeviceType \
    conda_env=new_env pytorch_folder=~/new_pytorch
```

→ `setup_env.sh` creates the conda env and clones the pytorch checkout.
If it fails (e.g., no internet, broken driver), the orchestrator hard-stops
with a fatal error.

## Architecture

```
User command
    │
    ▼
┌─────────────────────────────────────────────────────┐
│              enable-xpu-test (orchestrator)          │
│  Phases: provision → review → develop → verify → PR │
│  Group-by-file, batch per phase, one PR at end       │
└──┬───┬───┬───┬───┬───┬──────────────────────────────┘
   │   │   │   │   │   │
   ▼   ▼   ▼   ▼   ▼   ▼
   │   │   │   │   │   │
   ├── review-test-refactoring    (Phase 1)
   ├── develop-xpu-test           (Phase 2)
   ├── verify-xpu-test            (Phase 3)
   ├── analyze-ut-failures        (Phase 4, only if needed)
   ├── check-known-issue          (Phase 5B)
   ├── create-xpu-issue           (Phase 5B)
   └── submit-xpu-test-pr         (Phase 5A)
```

Each subskill is a focused agent that handles one concern. The orchestrator
reuses subagent sessions via `task_id` within a file group to avoid redundant
file reads.

## Constraints (Non-Negotiable)

1. **Never add a new XPU skip.** No `@skipIfXpu`, `@skipXPU`, or inline
   `if device == "xpu": return`. If a test has a latent bug, report it — don't
   gate around it.
2. **Never edit test method body logic.** Even a one-token fix is out of scope.
3. **No op_db changes for other classes.** Only widen `DecorateInfo` entries
   belonging to the target class and its generic test names.
4. **No closed issues as gating justification.** Check issue state; only OPEN
   counts.
5. **Never skip verification.** Every enabled class must run on XPU first.
6. **Surgical revert only.** A failing class's edits are reverted without
   discarding passing siblings' changes.
7. **PR is always a draft.** Confirm-gated — never publishes without approval.
