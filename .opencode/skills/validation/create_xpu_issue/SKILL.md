---
name: create-xpu-issue
description: Build and submit a well-structured XPU UT failure issue to intel/torch-xpu-ops, with op_ut cases, traceback, root cause, required PR Context cross-link, related-issue links, and correct labels. Returns the created issue URL and the after-filing cross-linking actions to perform in the porting PR.
---

# `create-xpu-issue`

## Purpose

Given a root-cause report for an unfixable failure (from `analyze-ut-failures`
or escalated by `fix-ut-test-code`), produce a complete issue body, submit it to
`intel/torch-xpu-ops`, and return the cross-linking actions for the porting PR.

When the failure is observed during a porting/enablement PR, the issue MUST
include a **Context** section cross-linking the PR (see template).

## Input

- A failure group: signature, tests, root cause, verified related issues.
- Optional `pr_number` / `pr_url` if filed during a porting PR.
- `GITHUB_TOKEN` with `repo` scope in the environment.

## Issue Template

```markdown
## Bug Description

<Brief description of the issue>

## Affected Tests

Cases:
op_ut,<module_path>,<TestClass.test_name>

## Error Message

```
<Full error message verbatim>
```

## Test Code Snippet

```python
# <file>:<line_start>-<line_end>
<Relevant test code showing the failing line>
```

## Traceback

```
pytest_command:
<Command to reproduce>

Traceback:
<Full stack trace>
```

## Root Cause Analysis

<Detailed explanation of WHY this failure occurs>

## Context

<REQUIRED whenever the failure is observed during a porting / enablement PR.
This section makes the issue self-contained for reviewers landing on it from
the PR, and lets future maintainers find the PR from the issue once the
underlying gap is fixed. Include all three points below.>

This issue tracks a gap identified during PR #<NNNN>, which <one-line
description of the PR scope>.

In that PR, `<test_name(s)>` is currently <skipped|failing|enabled-but-failing>
on XPU. Once <the underlying gap is resolved>, the <skip|failing assertion>
in PR #<NNNN> (`<file path>`) can be <removed|will pass without further changes>.

PR: https://github.com/intel/torch-xpu-ops/pull/<NNNN>

## Related PyTorch Issues

- pytorch#XXXXX - <Title> (DISABLED if applicable)
- pytorch#XXXXX - <Title> (related known issue)

## Related Intel/torch-xpu-ops Issues

- #XXXX - <Title>

## Versions

Intel: <relevant packages>
PyTorch: <version>
```

## Context Section: When Required

**MANDATORY** whenever the failure was observed during a porting or enablement
effort that has (or will produce) a PR on `intel/torch-xpu-ops`:

- The failing test was newly added or newly enabled in such a PR.
- The PR masks the failure with a skip / xfail / weakened assertion to revert later.
- The PR enables the test "loud-fail" so this issue tracks the underlying gap.

When required, it MUST contain all three:
1. **PR cross-link** — name the PR by number (`PR #NNNN`) AND a full URL.
2. **Current PR state for this test** — `skipped`, `failing`, or
   `enabled-but-failing`, with the file path holding the workaround.
3. **What becomes possible once resolved** — skip removable, assertion will
   pass without code changes, etc.

May be omitted only for failures outside any PR context (e.g. routine CI on `main`).

## Labels

- `skipped` - required for the dynamic skip template
- `module: ut` - unit test issue
- `module: dynamo` / `module: inductor` / `module: nn` - PyTorch component
- `dtype: amp_bf16` / `dependency component: oneAPI` / `dependency component: IGC`
  - specific feature labels

Apply <=5 focused labels. Group related failing tests into ONE issue per error
pattern. Search existing issues before creating to avoid duplicates.

## Submit and update

```bash
export GITHUB_TOKEN="<token>"

curl -s -X POST "https://api.github.com/repos/intel/torch-xpu-ops/issues" \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d '{"title":"[Bug Skip] <Title>","body":"<full body>","labels":["skipped","module: ut"]}'
```

To add missing detail after creation:

```bash
curl -s -X PATCH "https://api.github.com/repos/intel/torch-xpu-ops/issues/<n>" \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d '{"body":"<updated body>"}'
```

## After filing (when Context section was used)

- Add the issue URL to the relevant commit message in the porting PR.
- Add an inline comment next to the skip / weakened check:
  `# Tracked in intel/torch-xpu-ops#NNNN`.
- Append the URL to the PR description's "Tracking issues" section (or queue it
  for the eventual PR body if no PR exists yet).
- Optionally comment on the PR linking the new issue.

## Output

Return JSON:

```json
{
  "issue_url": "https://github.com/intel/torch-xpu-ops/issues/NNNN",
  "title": "[Bug Skip] ...",
  "labels": ["skipped", "module: ut"],
  "context_included": true,
  "after_filing_actions": ["add URL to commit msg", "inline skip comment", "..."]
}
```

## Constraints

- **No hardcoded tokens** — always `$GITHUB_TOKEN` (must have `repo` scope).
- Authenticated GitHub API limit is 5,000 req/hr; pre-collect data, then make
  minimal calls. Check `curl -I .../rate_limit`.
- Required documentation fields: `file:line` reference, full pytest command,
  verified related-issue links, package versions.
- Never invent issue numbers; cite only `gh issue view`-verified issues.
- ASCII only in issue bodies authored here.
