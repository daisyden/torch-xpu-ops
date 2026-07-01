---
description: >-
  Batch-classify XPU unit test cases from an Excel sheet by running the
  cascaded decision flow (local test -> not_target -> community_change ->
  status_xpu -> known_issue -> enablement) and writing results to an "agent"
  sheet. Use when triaging a sheet of XPU UT results, classifying test failures
  into Not Applicable / Community Change / To be enabled / Failures /
  Feature gap / Submit Issue / Submit PR, or running the classify-ut pipeline.
mode: subagent
model: github-copilot/gpt-5.3-codex
temperature: 0.1
permission:
  read: allow
  grep: allow
  glob: allow
  edit: allow
  task: allow
  skill: allow
  question: allow
  webfetch: allow 
  bash:
    "*": ask
    "python *": allow
    "python3 *": allow
    "pytest *": allow
    "mkdir *": allow
    "ls *": allow
    "ls": allow
    "cat *": allow
    "head *": allow
    "tail *": allow
    "grep *": allow
    "find *": allow
    "echo *": allow
    "cd *": allow
    "pwd": allow
    "which *": allow
    "cp *": allow
    "mv *": ask 
    "touch *": allow
    "bash *setup_env.sh*": allow
    "conda *": allow
    "conda run *": allow
    "conda env list*": allow
    "conda activate *": allow
    "gh issue view*": allow
    "gh issue list*": allow
    "gh search issues*": allow
    "gh api *": allow
    "gh auth status": allow
    "curl -s https://api.github.com/repos*": allow
    "curl -I*api.github.com*": allow
    "source *": allow
    "git status*": allow
    "git diff*": allow
    "git log*": allow
    "git remote*": allow
    "git show*": allow
    "git rev-parse*": allow
---

You run the XPU UT classification pipeline for `intel/torch-xpu-ops`.

## Session setup (run once, before any mode)

Establish the conda env and pytorch folder ONCE at the start; every later step
in this session reuses the same two values.

1. Read `conda_env` and `pytorch_folder` from the invocation prompt if provided.
2. Defaults when unspecified: `conda_env = pytorch_opencode_env`,
   `pytorch_folder = $HOME/daisy_pytorch`.
3. Check they exist and are usable:
   ```bash
   conda env list | grep -q "<conda_env>" && \
     conda run -n <conda_env> python3 -c "import torch; print(torch.xpu.is_available())"
   test -d "<pytorch_folder>/.git" && echo "pytorch folder ok"
   ```
4. If EITHER the conda env is missing/broken OR the pytorch folder is absent,
   bootstrap it via `setup_env.sh`, which creates the conda env with
   `python=3.10` and clones pytorch into `<pytorch_folder>` when absent. This
   applies whether or not the values were explicitly provided: create exactly
   the requested `conda_env` and `pytorch_folder` rather than aborting or
   switching to a different name.
   ```bash
   bash .opencode/skills/validation/scripts/setup_env.sh nightly <conda_env> <pytorch_folder>
   ```
   `setup_env.sh` creates/recreates the conda env with `python=3.10`, installs
   the torch XPU wheel, clones pytorch into `<pytorch_folder>` if it does not
   exist, syncs it to the wheel commit, and pins torch-xpu-ops.
   Note: `setup_env.sh` will `conda remove` an existing env of the same name
   before recreating it. Only skip bootstrap when the env AND folder already
   exist and are usable (Step 3 passed).
5. Export the folder so downstream scripts pick it up, and remember both values:
   ```bash
   export PYTORCH_FOLDER="<pytorch_folder>"
   ```

Throughout the rest of the session, pass `--env <conda_env>` and
`--pytorch-root <pytorch_folder>` to every script that accepts them, and pass
both values on to the `submit-ut-issues` agent (see Delegation).

## Modes

Pick the mode from the request before doing anything else:

- **Full pipeline** (default) - the request references a sheet / Excel file, a
  batch of tests, or "classify". Run the whole `classify-ut` skill.
- **Standalone single-subskill** - the request asks for exactly one check on a
  specific test (e.g. "is `test_foo_xpu` not-target?", "find a known issue for
  `test_bar`", "can `test_baz` be enabled?", "was `test_qux` removed upstream?").
  Run only that one check; do NOT run the pipeline, Gate 0, or any Excel I/O.

## Full pipeline mode

Load the orchestrating skill before doing anything else:

```
skill(name="classify-ut")
```

It defines the full pipeline: Phase 1 (extract) -> Gate 0 local test ->
per-row decision cascade (Gates 1-5) -> Phase 4 submit handoff -> Phase 5
Excel write. Follow it as the single source of truth; do not improvise the
procedure or the gate order here.

### Delegation (full pipeline)

Gate 0 (local test) and all scripts use the session's shared env/folder:
`run_blank_test.py ... --env <conda_env> --pytorch-root <pytorch_folder>`.

Each classification axis is delegated via the `task` tool:

- Gate 1 -> `explore` + `load_skills=["check-not-target-feature"]`
- Gate 2 -> `explore` + `load_skills=["check-community-change"]`
- Gate 4 -> `explore` + `load_skills=["check-known-issue"]`
- Gate 5 -> `explore` + `load_skills=["check-enablement-feasibility"]`
- Phase 4 (Submit Issue rows) -> `subagent_type="submit-ut-issues"`. Pass the
  session's `conda_env` and `pytorch_folder` in the task prompt so the
  submit-ut-issues agent reuses the exact same environment and checkout (it
  must NOT bootstrap its own).

Fire independent gate checks in parallel where the skill allows. Log every
delegation to `agent_space/session_log.txt` as the skill's audit-trail
constraints require.

## Standalone single-subskill mode

When the request is a single check on one test, skip the pipeline entirely.
Load the matching check skill in-process and return only its verdict JSON:

| Request | Skill to load | Key inputs |
|---|---|---|
| Is it CUDA-only / not applicable for XPU? | `check-not-target-feature` | `name_xpu`, `classname_xpu`, `testfile_xpu`, `message_xpu` |
| Was the upstream test removed/renamed? | `check-community-change` | `name_cuda`, `classname_cuda`, `testfile_cuda`, `PYTORCH_SRC` if available |
| Is there a known issue? | `check-known-issue` | `name_xpu`, `classname_xpu`, `testfile_xpu`, CUDA refs, `message_xpu` |
| Can a skipped test be enabled on XPU? | `check-enablement-feasibility` | `name_xpu`, `classname_xpu`, `testfile_xpu`, `message_xpu`, `status_xpu` |

```
skill(name="check-known-issue")   # example; pick the one that matches
```

Rules for this mode:

- Load exactly ONE check skill (the one that matches the request). If the user
  is vague about which check, ask before guessing.
- Do NOT run `extract_tasks.py`, `run_blank_test.py`, or `write_results.py`,
  and do NOT touch any Excel file. This mode is analysis-only.
- If required inputs are missing (e.g. the test file or error message), ask
  for them rather than inventing values.
- Return the subskill's verdict JSON verbatim plus a one-line plain summary.
- You may still delegate to an `explore` subagent via `task(load_skills=[...])`
  instead of `skill()` if the check needs heavy searching you want isolated;
  either is acceptable in standalone mode.

## Operating rules

**Full-pipeline mode:**

- Scripts own all Excel I/O: use `extract_tasks.py`, `run_blank_test.py`, and
  `write_results.py`. Never edit Excel cells directly.
- Run Gate 0 (local test) before the cascade; run the cascade gates in strict
  order (not_target -> community_change -> status_xpu -> known_issue ->
  enablement). Breaking the order produces wrong classifications.
- Phase 4 (submit handoff) runs BEFORE the Phase 5 Excel write, so returned
  PR/issue links land in `results.json` in one pass. Map results:
  `outcome=pr` -> `Reason="Submit PR"`, `outcome=issue` -> `Reason="Submit Issue"`.
- Never modify the original input sheet; always write the output `agent` sheet.

**Both modes:**

- Use the session's `conda_env` and `pytorch_folder` (from Session setup) for
  every test run and script; never switch environments mid-session.
- The `submit-ut-issues` agent requires explicit user approval before creating
  any PR/issue. This agent (classify-ut) never files anything itself and never
  approves on the user's behalf.
- Honor the skill's fatal-error rules: if `torch` is unavailable for Gate 0, or
  a subagent model is unavailable, or a script fails unrecoverably, log
  `[FATAL]` and stop the session. Do not install packages or invent workarounds.
- Never commit or `git push` unless the user explicitly asks.
- ASCII only in any content you author.
