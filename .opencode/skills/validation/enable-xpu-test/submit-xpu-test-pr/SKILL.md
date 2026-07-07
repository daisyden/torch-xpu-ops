---
name: submit-xpu-test-pr
description: Submit a draft GitHub pull request for XPU test enablement after develop-xpu-test made the edits and verify-xpu-test confirmed them locally. Use when the XPU-enable changes (extended instantiate_device_type_tests to include "xpu"/HAS_GPU guard, and widened op_db DecorateInfo device_type entries to ('cuda', 'xpu') in common_methods_invocations.py) are complete and verified, and you now need to stage, commit, push to the daisyden/pytorch fork, and open a draft PR. Confirm-gated: never pushes or creates a PR without explicit user approval. Regular fork PR via gh (NOT ghstack).
---

# Submit XPU Test PR

Submit a draft pull request for XPU test enablement work. This skill is the
**final** step of the enable-xpu-test pipeline:

```
develop-xpu-test  ->  verify-xpu-test  ->  submit-xpu-test-pr  (this skill)
```

It assumes the edits are already made (by `develop-xpu-test`) and already
locally XPU-verified (by `verify-xpu-test`). Its only job is to turn those edits
into a clean, confirm-gated draft PR on the `daisyden/pytorch` fork.

The XPU-enable edits live in the **pytorch/pytorch checkout** (default
`$HOME/daisy_pytorch`), not in torch-xpu-ops:
- `test/<file>.py` — instantiation extended to include XPU.
- `torch/testing/_internal/common_methods_invocations.py` — `DecorateInfo`
  `device_type` entries widened to `('cuda', 'xpu')`.

## When to Use

- After `develop-xpu-test` + `verify-xpu-test` have completed and the changes
  are ready to review.
- When the user asks to open / submit / push a PR for XPU test enablement.

Do **not** use this skill to make code edits or to run the local verification —
those belong to `develop-xpu-test` and `verify-xpu-test`. This skill only stages,
commits, pushes, and opens the PR.

## Preconditions

1. **Verified changes present.** `verify-xpu-test` returned a **verified**
   verdict (no unreverted widened `expectedFailure` that unexpectedly passes on
   XPU). If verification was not run or did not pass, STOP and ask the user to
   run `verify-xpu-test` first.
2. **GitHub auth.** `gh auth status` succeeds with a token that can push to the
   `daisyden/pytorch` fork.
3. **Git remotes** in the pytorch checkout:
   - `origin` (or `daisyden`) -> `https://github.com/daisyden/pytorch.git` (fork)
   - `upstream` -> `https://github.com/pytorch/pytorch.git` (optional, for base ref)

   Verify and, if missing, ask the user before adding a remote.

## Tools Used

- **bash**: `git`, `gh` (status, diff, commit, push, pr create).
- **read / grep**: inspect the diff before drafting the commit/PR message.
- **question**: get explicit user approval before push and before PR creation.

## Workflow

Run all git/gh commands from the pytorch checkout directory
(`<pytorch_folder>`, default `$HOME/daisy_pytorch`).

### Step 1: Inspect the Working Tree

Confirm the changes are exactly the XPU-enable edits and nothing stray.

```bash
cd <pytorch_folder>
git status
git diff --stat
git diff
```

Sanity checks on the diff:
- Only expected files changed: the enabled `test/<file>.py` and/or
  `torch/testing/_internal/common_methods_invocations.py`.
- The diff contains XPU-enable tokens (`only_for=("cuda", "xpu")`,
  `allow_xpu=True`, `device_type=('cuda', 'xpu')`, or the `HAS_GPU` guard).
- The diff does **not** add new `@skipIfXpu` / `skipXPU` skip decorators and
  does **not** modify pre-existing XPU skips/decorators (those are out of scope
  for enablement — flag to the user if present).

If unexpected files are dirty, ask the user how to proceed (stash / exclude /
abort). Do not blindly `git add -A`.

### Step 2: Choose / Create the Branch

```bash
git branch --show-current
```

- If already on a dedicated feature branch, use it.
- Otherwise create one:
  ```bash
  git checkout -b xpu/enable-<short-scope>   # e.g. xpu/enable-test-sdpa
  ```

Never commit XPU-enable work directly onto `main`/`master`.

### Step 3: Draft the Commit

Stage only the intended files (explicit paths, never `-A` blindly):

```bash
git add test/<file>.py torch/testing/_internal/common_methods_invocations.py
```

Draft a commit message following the repo convention (imperative subject, a
body explaining the "why", and a Test Plan section with the literal verification
commands). Example:

```
[XPU] Enable <ClassName> / <op(s)> on XPU

Extend instantiate_device_type_tests to only_for=("cuda", "xpu") with
allow_xpu=True, and widen the CUDA-registered DecorateInfo xfail/skip entries
in common_methods_invocations.py to device_type=('cuda', 'xpu') so the same
expected failures apply on XPU. No new XPU skip decorators are added and no
existing XPU skips/decorators are changed.

Test Plan:
  source ~/miniforge3/bin/activate pytorch_opencode_env
  cd /tmp
  python -m pytest <repo>/test/<file>.py -v -k "<ClassName> and xpu" --tb=short
  python -m pytest <repo>/test/test_ops.py -v -k "<op_name> and xpu" --tb=short

Authored with an AI assistant.
```

Notes:
- Disclose AI assistance in the body ("Authored with an AI assistant.").
- Do **NOT** add a `Co-authored-by:` AI trailer (interferes with the Linux
  Foundation CLA bot).
- Do not commit yet — present the message in Step 4 first.

### Step 4: Confirm Before Committing / Pushing (MANDATORY)

Present to the user, via the `question` tool:
- the file list and `git diff --stat`,
- the drafted commit message,
- the target: branch `<branch>` -> fork `daisyden/pytorch`, draft PR.

Ask for explicit approval (approve / edit / abort). Only on **approve** proceed.

### Step 5: Commit and Push to the Fork

```bash
git commit -m "$(cat <<'EOF'
<approved commit message>
EOF
)"

# Push to the daisyden/pytorch fork. Use --force-with-lease (never --force),
# and never force-push main/master.
git push -u origin <branch> --force-with-lease
```

Force-push rules: always `--force-with-lease`, never plain `--force`; if it
rejects, fetch and reconcile — do not escalate to `--force`.

### Step 6: Open the Draft PR

Open a **draft** PR against the `daisyden/pytorch` fork (per user preference),
using `gh`. This is a regular fork PR — **NOT ghstack**.

```bash
gh pr create \
  --repo daisyden/pytorch \
  --base main \
  --head <branch> \
  --draft \
  --title "[XPU] Enable <ClassName> / <op(s)> on XPU" \
  --body "$(cat <<'EOF'
## Summary
- Extend `instantiate_device_type_tests` for `<ClassName>` to
  `only_for=("cuda", "xpu"), allow_xpu=True` (or add a `HAS_GPU` guard).
- Widen CUDA-registered `DecorateInfo` xfail/skip/tolerance entries in
  `torch/testing/_internal/common_methods_invocations.py` to
  `device_type=('cuda', 'xpu')` so the same expected failures apply on XPU.
- No new XPU skip decorators added; existing XPU skips/decorators untouched.

## Test plan
- XPU: enabled variants run and behave as expected (pass / skip / xfail):
  ```
  python -m pytest <repo>/test/<file>.py -v -k "<ClassName> and xpu" --tb=short
  python -m pytest <repo>/test/test_ops.py -v -k "<op_name> and xpu" --tb=short
  ```
- CUDA behavior unchanged (validated by CI on a CUDA host).

Authored with an AI assistant.
EOF
)"
```

Return the PR URL to the user.

### Step 7: Report

Report: branch name, commit hash, fork push result, and the draft PR URL.

## Constraints

1. **Confirm-gated.** NEVER `git commit`, `git push`, or `gh pr create` without
   explicit user approval (draft -> confirm -> submit). This applies even when
   invoked automatically by an upstream orchestrator.
2. **PR target is `daisyden/pytorch`.** Open the PR against the fork, as a draft.
3. **Regular fork PR, NEVER ghstack.** Do not use ghstack for this workflow.
4. **Force-push safety.** Always `--force-with-lease`, never `--force`; never
   force-push to `main`/`master`.
5. **Stage explicit paths.** Never `git add -A` / `git add .` blindly — add only
   the intended test file(s) and `common_methods_invocations.py`.
6. **No new edits.** This skill does not modify test code, op_db, or run local
   verification — it only packages the already-verified changes into a PR. If
   the diff contains new XPU skip decorators or touches existing XPU
   skips/decorators, flag it and stop (out of scope for enablement).
7. **AI disclosure, no CLA-breaking trailer.** Disclose AI assistance in the
   commit/PR body; do NOT add a `Co-authored-by:` AI trailer.
8. **ASCII only** in authored commit/PR content.

## See Also

- `develop-xpu-test` — makes the XPU-enable edits this skill submits.
- `verify-xpu-test` — locally verifies the edits before this skill packages them.
