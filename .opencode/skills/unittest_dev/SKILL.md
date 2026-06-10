---
name: remove-stale-skips
description: Remove stale test skip decorators when the referenced GitHub issue is closed AND the issue is labeled module: xpu. When encountering @unittest.skipIf(IS_LINUX ..., "https://github.com/.../issues/N") or @skipIfXpu(True, ...) pointing to a resolved/closed issue, check if the issue has module: xpu label before removing. Do NOT touch decorators referencing issues without module: xpu. Use when enabling XPU tests, cleaning up skips, or when tests are disabled due to now-fixed issues.
---

# Remove stale test skip decorators

Remove skip decorators only when the referenced issue is closed AND has
`module: xpu` label.

## Rationale

When a GitHub issue blocking a test is closed/resolved, the test should run on
all platforms. However, the decision to remove a skip depends on who owns the
issue:

- If the issue has **`module: xpu`** → removing the skip is the XPU team's
  responsibility and is safe to do.
- If the issue does **NOT** have `module: xpu` → the skip is for a different
  team's concern; do NOT remove it even if the issue is closed. The skip
  condition (e.g., `(IS_LINUX and GPU_TYPE != "xpu")`) already excludes XPU,
  so the test already runs on XPU.

Rather than modifying the skip condition (e.g., changing `IS_LINUX` to
`(IS_LINUX and GPU_TYPE != "xpu")`), remove the `@unittest.skipIf(...)` or
`@skipIfXpu(...)` decorator entirely. This keeps the codebase clean and ensures
the test runs everywhere, not just on XPU.

## Instructions

1. **Identify** a `@unittest.skipIf` or `@skipIfXpu` decorator that references
   a GitHub issue URL (e.g., `https://github.com/pytorch/pytorch/issues/NNNNN`).

2. **Verify** the referenced issue is closed. Check via:
   ```bash
   gh issue view NNNNN --json state
   ```

3. **Check the issue's labels**. For DISABLED patterns (skips with condition
   `(IS_LINUX and GPU_TYPE != "xpu")` or similar non-XPU-specific conditions):
   ```bash
   gh issue view NNNNN --json labels --jq '.labels[].name'
   ```
   - If the issue has **`module: xpu`** label → proceed with removal.
   - If the issue does **NOT** have `module: xpu` → **do NOT touch the
     decorator**. Leave it as-is, even if the issue is closed. The test already
     runs on XPU (excluded by the condition), and the issue belongs to another
     team.

4. **If closed AND has `module: xpu`**, remove the entire
   `@unittest.skipIf(...)` or `@skipIfXpu(...)` decorator block. Do NOT modify
   the skip condition — remove it entirely.

5. **Clean up unused imports**: If removing the skip causes an imported symbol
   (e.g., `GPU_TYPE`, `IS_LINUX`, `skipXPUIf`) to become unused, remove that
   import as well.

6. **Do NOT** remove `TEST_WITH_SLOW` or hardware capability guards like
   `not HAS_GPU` — those are not issue-based skips and serve a different
   purpose.

## Examples

### Correct: Remove entirely

```python
# Before:
    @unittest.skipIf(
        IS_LINUX or TEST_WITH_SLOW, "https://github.com/pytorch/pytorch/issues/184537"
    )
    def test_hints_wrapper(self):

# After:
    def test_hints_wrapper(self):
```

### Correct: Remove with import cleanup

```python
# Before:
from torch.testing._internal.inductor_utils import GPU_TYPE, HAS_GPU

    @unittest.skipIf(
        (IS_LINUX and GPU_TYPE != "xpu") or TEST_WITH_SLOW,
        "https://github.com/pytorch/pytorch/issues/180656",
    )
    def test_dynamo_dtensor_from_local_redistribute(self):

# After:
from torch.testing._internal.inductor_utils import HAS_GPU

    def test_dynamo_dtensor_from_local_redistribute(self):
```

### Wrong: Modifying condition instead of removing

```python
# Wrong — don't do this:
    @unittest.skipIf(
        (IS_LINUX and GPU_TYPE != "xpu") or TEST_WITH_SLOW,  # ❌ just remove the decorator
        "https://github.com/pytorch/pytorch/issues/184537",
    )
    def test_hints_wrapper(self):
```

### Wrong: Removing decorator for a non-XPU issue

```python
# Wrong — don't do this:
# Issue #151381 has labels: module: fx, module: flaky-tests (no "module: xpu")
    @unittest.skipIf(
        (IS_LINUX and GPU_TYPE != "xpu") or TEST_WITH_ROCM or TEST_WITH_SLOW,
        "https://github.com/pytorch/pytorch/issues/151381",
    )
    def test_remove_noop_slice1(self):
```

The decorator should remain because:
- The issue does **not** have `module: xpu` label
- The skip condition `(IS_LINUX and GPU_TYPE != "xpu")` already excludes XPU,
  meaning the test **already runs on XPU**
- The issue belongs to the FX team, not the XPU team

## Verification

- [ ] Issue is confirmed closed (`gh issue view NNNNN --json state`)
- [ ] Issue labels checked for `module: xpu` (`gh issue view NNNNN --json labels`)
- [ ] If no `module: xpu` label → decorator left untouched
- [ ] If `module: xpu` label present → entire `@unittest.skipIf(...)` decorator block removed
- [ ] Unused imports cleaned up
- [ ] LSP diagnostics clean on changed files
