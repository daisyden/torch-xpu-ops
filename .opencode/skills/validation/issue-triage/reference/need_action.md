# Need Action Rules

`need_action` is the user-facing action derived after target ownership is
traced. It must not replace the canonical source `verdict`.

| Condition | `need_action` |
|---|---|
| `target_component` is `pytorch` or `torch-xpu-ops`, and labels do not include `not_target` or `wontfix` | `NEED_FIX` |
| A third party must fix the cause | `NEED_3RPARTY_FIX` |
| Any other condition, including `test-case`, `N/A`, `not_target` or `wontfix` labels, an inconclusive trace, an open human decision, or a non-fixable case | `NEED_HUMAN` |

When a third party owns the fix, preserve the canonical source verdict
`NEED_FIX_3RDPARTY` in the separate `verdict` field and emit
`NEED_3RPARTY_FIX` in `need_action`.

Labels do not determine product ownership. They only suppress the product
`NEED_FIX` action when `not_target` or `wontfix` is present. Never treat a
skip or xfail as a fix.

## Minimum decision checks

1. Confirm that the target component follows the fix location in
   [target_component.md](target_component.md).
2. Confirm that the trace has cited files, symbols, and a call path.
3. Confirm that `git log` was checked for an upstream fix.
4. Preserve both `verdict` and `need_action` when their canonical values differ.
