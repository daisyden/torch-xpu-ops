---
name: collect-opens
description: "Collect open action requests from a single GitHub issue (intel/torch-xpu-ops by default). Determines the next required action (AR) by checking issue status, labels, issue-body/comment requests, and linked PR state (CI, reviews, approvals). Returns a JSON verdict with AR code, reason, and extracted request details. Use when you need to know what action is still pending on an open issue — reply needed, CI fix, reviewer assignment, PR landing, or skip-list addition."
---

# Collect Open Requests

Inspects one GitHub issue and its linked PR(s) to determine what action is
still required. Returns a structured JSON verdict.

## Architecture

Steps 1 (closed check), 2 (label check), and 4 (linked PR analysis) are
handled deterministically by a Python script (`scripts/collect_opens.py`).
Step 3 (parsing actionable requests from comments) requires LLM judgment
and is performed by the calling agent using the structured comment data
output by the script.

## Inputs

| Input | Required | Default | Notes |
|---|---|---|---|
| `issue_link` | yes | — | URL or bare issue number. |
| `conda_env` | no | — | Conda env (unused by this skill but accepted for pipeline compat). |
| `pytorch_folder` | no | — | Local checkout (unused but accepted for pipeline compat). |
| `repo` | no | `intel/torch-xpu-ops` | Repository owner/name for bare numbers. |

Missing `issue_link` -> **hard-stop**.

## Prerequisites

- Authenticated `gh` CLI on PATH.
- Python 3.

## Usage

### Phase 1: Run the script

```bash
python3 .opencode/skills/validation/issue-triage/collect-opens/scripts/collect_opens.py \
    <issue_number_or_url> [--repo owner/name] [--output PATH]
```

The script handles steps 1, 2, and 4 and outputs JSON. Key fields in script output:

| Field | Meaning |
|---|---|
| `AR` | Action code if determined by steps 1/2/4. `null` if pending LLM step 3. |
| `AR_REASON` | Reason string if AR is set. |
| `ar_source` | `"step4_pr_analysis"` if AR came from step 4; `"pending_llm_step3"` if agent must decide. |
| `comments_for_llm` | Array of structured comments for LLM parsing (step 3). |
| `linked_prs` | Array of PR analysis results. |

### Phase 2: LLM step 3 (agent judgment)

If `ar_source == "pending_llm_step3"` (script did not resolve AR):

1. Read `comments_for_llm` from the script output.
2. Parse for actionable requests (see Step 3 below).
3. If unresolved requests found: set `AR = "NEED_REPLY"`.
4. If NO unresolved requests AND no linked PR found: set `AR = "NO_PR"`.
5. If NO unresolved requests AND linked PRs exist but none matched step 4 rules: set `AR = "NO_OPENS"`.

If `ar_source == "step4_pr_analysis"` (script already resolved AR):
- Step 3 is STILL evaluated. If unresolved requests exist, they take priority
  over the PR-based AR (step 3 wins over step 4 per decision tree order).

### Phase 3: Finalize output

Merge script output with LLM findings into the final JSON. Remove the
`comments_for_llm` and `ar_source` fields from the final output.

## Decision Tree

Evaluate in strict order. First match wins.

### Step 1 — Issue closed? (SCRIPT)

If `state == "CLOSED"`:
- **AR = `N/A`**
- **AR_REASON = `"Issue is closed"`**
- STOP.

### Step 2 — Label check (SCRIPT)

If labels contain `not_target` OR `wontfix`:
- **AR = `ADD_SKIPLIST`**
- **AR_REASON = `"Issue labeled <label>"`**
- STOP.

### Step 3 — Collect requests from comments (LLM)

Using `comments_for_llm` from script output, parse for actionable requests.

A "request" is any comment or body section where a user explicitly mentions
another user with an action verb or request phrasing (e.g. `@user please verify`,
`@user can you check`, `@user fix this`, `@user update the PR`). Also treat
direct assignments (`cc @user`, `assign @user`) as requests.

For each request, extract:
- `requestor`: login of the comment author (or issue author for body requests).
- `owner`: the @-mentioned user expected to act.
- `content`: the actionable sentence/phrase (trimmed, max 200 chars).
- `comment_url`: permalink to the comment (or issue URL for body).
- `resolved`: boolean — true if a LATER comment by `owner` acknowledges
  completion (e.g. "done", "verified", "fixed", "merged", "landed", posts a
  result/log), OR if the request is clearly superseded by events.

Ignore bot comments, CI status reports, and purely informational mentions.

If ANY request has `resolved == false`:
- **AR = `NEED_REPLY`**
- **AR_REASON = `"<requestor>: @<owner> <content>"`** (first unresolved request).
- Include ALL unresolved requests in `open_requests[]`.
- STOP.

### Step 4 — Linked PR analysis (SCRIPT)

The script finds linked PRs and evaluates them in order:

#### 4.1 — CI failure
- **AR = `FIX_CI`**, **AR_REASON = `"#<pr_number> has CI failure"`**

#### 4.2 — Unresolved review comments
- **AR = `NEED_REPLY_REVIEW`**, **AR_REASON = `"#<pr_number> has open request from <reviewer>"`**

#### 4.3 — No reviewer assigned
- **AR = `ADD_REVIEWER`**, **AR_REASON = `"#<pr_number> CI passed, no reviewer assigned"`**

#### 4.4 — Awaiting review feedback
- **AR = `NEED_REVIEW`**, **AR_REASON = `"<reviewer> please review #<pr_number>"`**

#### 4.5 — Approved, ready to land
- **AR = `LAND_PR`**, **AR_REASON = `"#<pr_number> approved and CI green"`**

### Step 5 — Fallback

If none of the above matched (open issue, no blocking label, no unresolved
requests, no linked PR issues):
- **AR = `NO_OPENS`**
- **AR_REASON = `"No open action items found"`**

## Output

Final JSON schema:

```json
{
  "issue_id": 1234,
  "repo": "intel/torch-xpu-ops",
  "title": "test_foo fails on PVC",
  "url": "https://github.com/intel/torch-xpu-ops/issues/1234",
  "status": "open",
  "labels": ["module: ut", "hw: PVC"],
  "AR": "NEED_REPLY",
  "AR_REASON": "mary: @abc please verify on local environment",
  "open_requests": [
    {
      "requestor": "mary",
      "owner": "abc",
      "content": "please verify on local environment",
      "comment_url": "https://github.com/intel/torch-xpu-ops/issues/1234#issuecomment-999",
      "resolved": false
    }
  ],
  "linked_prs": [
    {
      "pr_number": 5678,
      "pr_url": "https://github.com/intel/torch-xpu-ops/pull/5678",
      "state": "open",
      "ci_passed": true,
      "has_reviewer": true,
      "approved": false,
      "has_unresolved_review": false
    }
  ]
}
```

### AR Codes Reference

| AR Code | Meaning |
|---|---|
| `N/A` | Issue closed, no action needed. |
| `ADD_SKIPLIST` | Issue labeled not_target/wontfix, add to skip list. |
| `NEED_REPLY` | Unresolved request in comments awaiting response. |
| `NO_PR` | No linked PR found for an open issue. |
| `FIX_CI` | Linked PR has CI failures. |
| `NEED_REPLY_REVIEW` | Linked PR has unresolved review comments. |
| `ADD_REVIEWER` | Linked PR passed CI but has no reviewer. |
| `NEED_REVIEW` | Linked PR awaiting reviewer feedback. |
| `LAND_PR` | Linked PR approved with green CI, ready to merge. |
| `NO_OPENS` | No open action items found. |

## Constraints

1. Read-only analysis. No mutations (no comments, no labels, no merges).
2. Uses `gh` CLI exclusively for GitHub API access (via script).
3. Evaluates the decision tree in strict order; first match wins.
4. Step 3 request parsing: use LLM judgment to identify actionable requests.
   Ignore bot comments, CI status reports, and purely informational mentions.
5. For linked PR: script evaluates the most recent open PR first, skips merged/closed PRs.
6. Output MUST be valid JSON written to the path specified by the caller
   (or printed to stdout if no output path given).

## Hard Stops

- Missing `issue_link` input.
- `gh` CLI not authenticated or not on PATH.
- Issue fetch returns 404 (script exits with code 1).
- Malformed input reference (script exits with code 2).

## See Also

`extract-issue-information`, `triage-issue`, `check-known-issue`.
