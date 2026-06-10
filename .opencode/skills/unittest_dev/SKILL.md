---
name: remove-stale-skips
description: Remove stale test skip decorators when the referenced GitHub issue is closed. When encountering @unittest.skipIf(IS_LINUX ..., "https://github.com/.../issues/N") or @skipIfXpu(True, ...) pointing to a resolved/closed issue, remove the entire skip decorator rather than modifying the skip condition. Use when enabling XPU tests, cleaning up skips, or when tests are disabled due to now-fixed issues.
---

# Remove stale test skip decorators

Remove the entire skip decorator when the referenced GitHub issue is closed.

## Rationale

When a GitHub issue blocking a test is closed/resolved, the test should run on
all platforms. Rather than modifying the skip condition (e.g., changing
`IS_LINUX` to `(IS_LINUX and GPU_TYPE != "xpu")`), simply remove the
`@unittest.skipIf(...)` or `@skipIfXpu(...)` decorator entirely. This keeps the
codebase clean and ensures the test runs everywhere, not just on XPU.

## Instructions

1. **Identify** a `@unittest.skipIf` or `@skipIfXpu` decorator that references
   a GitHub issue URL (e.g., `https://github.com/pytorch/pytorch/issues/NNNNN`).

2. **Verify** the referenced issue is closed. Check via:
   ```bash
   gh issue view NNNNN --json state
   ```

3. **If closed**, remove the entire `@unittest.skipIf(...)` or `@skipIfXpu(...)`
   decorator block. Do NOT modify the skip condition — remove it entirely.

4. **Clean up unused imports**: If removing the skip causes an imported symbol
   (e.g., `GPU_TYPE`, `IS_LINUX`, `skipXPUIf`) to become unused, remove that
   import as well.

5. **Do NOT** remove `TEST_WITH_SLOW` or hardware capability guards like
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

## Verification

- [ ] Issue is confirmed closed (`gh issue view NNNNN --json state`)
- [ ] Entire `@unittest.skipIf(...)` decorator block removed
- [ ] Unused imports cleaned up
- [ ] LSP diagnostics clean on changed files
