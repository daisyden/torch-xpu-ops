# Phase 4b Agent Instructions (canonical)

> **Canonical location.** This file is the source of truth for the
> Phase 4b explore-agent prompt. The runtime tree
> `agent_space/phase4b/AGENT_INSTRUCTIONS.md` is a copy; orchestration
> scripts that lay out per-issue waves should `cp` this file into
> place. Edit this file, then sync.

You are executing Phase 4b `get_AR_from_issue` of the bug_scrub workflow for ONE GitHub issue. Output ONE JSON file. No code changes.

## Inputs
- Per-issue context: read entry with matching `issue_number` from the wave's `batch.json` (fields: title, reporter, assignee, labels, module, category, priority, dependency, root_cause, fix_approach, cases[]).
- gh CLI is authenticated as `daisyden`. Use `gh api`, `gh pr view`, `gh pr list`, `gh issue view`, `gh pr checks`.
- Repo: intel/torch-xpu-ops (issue lives here). Optionally also search upstream pytorch/pytorch when relevant.

## Steps (execute in order)

### STEP 0 — NOT-TARGET CHECK
```
gh issue view <N> --repo intel/torch-xpu-ops --json body,comments,labels,author,assignees
```
Determine via deep reasoning (not pattern matching) whether an authoritative owner (OWNER/COLLABORATOR/MEMBER/assignee) declared the issue out-of-scope, won't-fix, expected behavior, or duplicate.
Verdict: `"label_not_target_and_close"` (full short-circuit), `"label_not_target_partial"` (continue Part 1/2 for remaining cases), or `null` (proceed).

### PART 1 — PR DISCOVERY (run all 6 vectors)
- **V0 GraphQL** (auto-VERIFY):
  ```
  gh api graphql -f query='{ repository(owner:"intel", name:"torch-xpu-ops") { issue(number: <N>) { closedByPullRequestsReferences(first: 20, includeClosedPrs: true) { nodes { number title state author { login } repository { nameWithOwner } createdAt mergedAt } } } } }'
  ```
- **VA timeline**:
  ```
  gh api repos/intel/torch-xpu-ops/issues/<N>/timeline --paginate --jq '.[] | select(.event=="cross-referenced") | {number: .source.issue.number, title: .source.issue.title, state: .source.issue.state, pr: (.source.issue.pull_request != null), repo: .source.issue.repository_url, url: .source.issue.html_url}'
  ```
- **VB body refs**: scan issue body for `#N`, full PR URLs, `owner/repo#N`. **STRIP** `### Versions` section, fenced code blocks, and `<!-- -->` HTML comments first.
- **VC title-keyword**: `gh pr list --repo intel/torch-xpu-ops --state all --search "<phrase>" --json number,title,state,author,createdAt`. Bound by issue creation date ±90 days. Skip generic tokens (XPU, bug, error, [Bug]).
- **VD file-path**: if issue cites specific files, `gh pr list ... --search "<filename>"` and inspect via `gh pr view <pr> --json files --jq '.files[].path'`.
- **VE Fix Approach scan**: scan the `fix_approach` text from batch.json for `#N` / PR URLs (same excluded-source rules as VB).
- Optional: also search upstream `pytorch/pytorch` if the root cause lives there.

UNION + DEDUPE candidates by (repo, pr_number).

### VERIFICATION (3-tier, mandatory per candidate)
- **VERIFIED — github_linked**: appears in V0 `closedByPullRequestsReferences` result. (GitHub itself asserts the PR closes this issue.)
- **VERIFIED — explicit_reference**: PR body or commit message contains a *fixing-verb* reference to this issue: `Fixes #<N>`, `Closes #<N>`, `Resolves #<N>`, `Fix for #<N>`, `closes intel/torch-xpu-ops#<N>`, or full issue URL with one of those verbs in the same sentence — NOT inside an excluded section.
  - A bare `#<N>` mention, "see #<N>", "related to #<N>", "surfaced by #<N>", "exposed by #<N>", "tracked in #<N>", "follow-up to #<N>", "depends on #<N>" does **NOT** qualify. Mark these `verdict: "REJECTED"`, `rejection_reason: "reference_only"`.
  - When ambiguous, read the PR diff intent: a PR whose diff modifies the kernel/op/file named in the issue's `fix_approach` is a fix candidate; a PR that only adds/enables tests for an already-failing case is **not** a fix.
- **VERIFIED — content_match**: explore-style reasoning shows file overlap + symptom match + plausible timing. Justify in `match_reasoning`. Same fix-vs-surface caveat applies: enabling tests is not fixing.
- **REJECTED**: fails all of the above. Always set `rejection_reason` (`"reference_only"`, `"no_overlap"`, `"wrong_symptom"`, `"timing_mismatch"`, etc.).
- **UNVERIFIABLE_PRIVATE**: inner-source / private repo.

### RELATIONSHIP CLASSIFICATION (mandatory for every VERIFIED candidate)

For each VERIFIED PR, classify the issue↔PR relationship:

| relationship   | Meaning                                                                                | Flows into action_TBD?                  |
|----------------|----------------------------------------------------------------------------------------|------------------------------------------|
| `fixes`        | PR's intent is to make this issue's failure stop reproducing                           | YES — drives the DERIVATION RULE table   |
| `supersedes`   | PR replaces a previous fixing PR for this issue                                        | YES — recurse on this PR                 |
| `surfaces`     | PR exposes/discovers this issue (e.g. enables tests that were already failing)         | NO — log only                            |
| `related`      | Same module/area but neither fixes nor surfaces                                        | NO — log only                            |
| `unknown`      | Insufficient evidence                                                                  | NO — treat as `related`                  |

Only `fixes` and `supersedes` candidates contribute action_TBD verbs (`Land PR …`, `Verify fix from merged PR …`). `surfaces` and `related` are recorded for transparency but produce **no** PR-tracking verbs and **no** owner_transferred attribution to the PR author.

If after classification there are zero `fixes`/`supersedes` candidates, treat the issue as having zero VERIFIED PRs for action_TBD purposes — fall back to `"No action — investigate further"` (per the action_TBD derivation rule).

**Worked example (do NOT repeat this mistake)**: Issue intel/torch-xpu-ops#3530 body says "tracks an XPU numerical-accuracy gap surfaced (but not introduced) by PR #3475". PR #3475 enables 22 files of CUDA test coverage; it does not modify the `index_add_` kernel referenced in #3530's `fix_approach`. Correct classification: `relationship = "surfaces"`, do NOT emit `Land PR …#3475`. Correct verb: `"No action — investigate further"`. Correct `owner_transferred`: blank (no Assignee, and the v4.17 close/verify carve-out does not apply because the verb is "No action — investigate further", not a pure close/verify verb).

### STEP 2.5 — LIVE PR-STATE RE-CHECK (mandatory before emitting verdict)
For every VERIFIED PR (regardless of relationship — we still record live state):
```
gh pr view <pr> --repo <owner/name> --json state,mergedAt,closedAt,updatedAt,reviewDecision
```
State precedence (only applied when `relationship in {"fixes","supersedes"}`):
- **MERGED** → action: `"Verify fix from merged PR <ref> and close"`
- **OPEN** → action: `"Land PR <ref>"`
- **CLOSED unmerged** → re-run VC/VD/VE for replacement; if found use that. If still none → emit `"PR <ref> closed unmerged; reassess fix path"` AND a concrete next-step verb directed at the assignee (e.g. `"@<assignee>: please reassess fix path"`). **v4.15: the bare `"RETRIAGE_PRS"` literal is deprecated** — Phase 4d no longer matches it, and it produces a user-facing action verb only when paired as above.

For `relationship in {"surfaces","related","unknown"}`: live_state is recorded for transparency but produces NO action_TBD verb.

### STEP 3 — check_pr_status gates (only for OPEN VERIFIED `fixes`/`supersedes` PRs)

Collect the data for every gate, then apply the **Gate Priority Matrix** under
"DERIVATION RULE" below to emit exactly one verb per OPEN PR (gate verbs are
mutually exclusive with `Land PR` as of v4.13).

- **Gate 1 — CI** (highest priority): `gh pr checks <n> --repo <repo>`. Capture `completedAt` of the latest failing/required check. Any red/failing required check triggers `"Address CI failures on PR <ref>"`.
- **Gate 2 — Resolving**: unresolved review threads (`gh api repos/<repo>/pulls/<n>/reviews`, comments). Capture the `createdAt` of the most recent unresolved-thread comment. If CI is green but unresolved threads exist, emit `"Resolve unresolved review comments on PR <ref>"`.
- **Gate 3 — Review approval**: use `reviewDecision` from `gh pr view --json reviewDecision`. If CI green AND no unresolved reviews AND `reviewDecision != "APPROVED"` (typically `REVIEW_REQUIRED` or `CHANGES_REQUESTED`), emit `"Wait for review on PR <ref>"`. For the staleness clock, fetch the most-recent commit push timestamp via `gh api repos/<repo>/pulls/<n>/commits --jq '.[-1].commit.committer.date'`; fall back to PR `createdAt` if no commits are returned.
- **Gate 4 — Land** (terminal): CI green, no unresolved threads, `reviewDecision == "APPROVED"`. Emit `"Land PR <ref>"`.

### STEP 3.5 — STALENESS COMPUTATION (mandatory)
Compute "now" from bash: `date -u +%Y-%m-%dT%H:%M:%SZ`. Then for every gating signal and every comment AR, compute `age_days = (now - signal_timestamp).days` and `stale = age_days > 7`.

### PART 2 — COMMENT AR
For each issue comment from `gh issue view <N> --json comments`, capture `createdAt`, classify by author association (OWNER/COLLABORATOR/MEMBER/CONTRIBUTOR/NONE) and request type (`blocking` | `informational` | `answered`). For each blocking unresolved request, set `created_at`, compute `age_days` and `stale = age_days > 7`, and record `owner_should_act`.

## Output JSON (write ONE file, no other side effects)

Path: `/home/daisydeng/pytorch/agent_space/phase4b/wave<W>/result_<N>.json`

```json
{
  "issue_number": <N>,
  "validation_status": "OK" | "ERROR" | "PRIVATE_ONLY",
  "not_target_verdict": "label_not_target_and_close" | "label_not_target_partial" | null,
  "not_target_reasoning": "...",
  "pr_candidates": [
    {
      "pr_number": 0,
      "repo": "owner/name",
      "vector": "0|A|B|C|D|E",
      "verdict": "VERIFIED|REJECTED|UNVERIFIABLE_PRIVATE",
      "verdict_source": "github_linked|explicit_reference|content_match",
      "rejection_reason": "reference_only|no_overlap|wrong_symptom|timing_mismatch|null",
      "relationship": "fixes|supersedes|surfaces|related|unknown",
      "live_state": "MERGED|OPEN|CLOSED",
      "live_merged_at": null,
      "review_decision": null,
      "blocking_gate": null,
      "blocking_signal_at": null,
      "blocking_signal_age_days": null,
      "blocking_signal_stale": false,
      "match_reasoning": "...",
      "files_overlap": []
    }
  ],
  "comment_ar": [
    {
      "comment_idx": 0,
      "author": "login",
      "association": "OWNER|COLLABORATOR|MEMBER|CONTRIBUTOR|NONE",
      "request_type": "blocking|informational|answered",
      "created_at": "2026-04-01T12:00:00Z",
      "age_days": 0,
      "stale": false,
      "text": "...",
      "owner_should_act": "login"
    }
  ],
  "action_TBD": ["..."],
  "action_reason": ["..."],
  "owner_transferred": ["earliest binding owner login (Assignee for fix/track verbs; Reporter for pure close/verify verbs per v4.17 carve-out)"],
  "summary": "1-2 sentence narrative"
}
```

## Canonical action_TBD phrases (use when applicable)

> **v4.16 RULE 1 + pending-ack guards (Close the fixed issue).** Do NOT emit `"Close the fixed issue"` in either of these cases:
> - **RULE 1 violation**: the issue has 1+ Test Cases / E2E Test Cases sheet rows that fail strict RULE 1 (`XPU Status ∈ {passed, fixed}` AND `Stock Status ∉ {fail, error, timeout}`; blank `XPU Status` counts as a failure). Emit `"@<assignee>: please confirm <test_case_name> status (XPU Status / Stock Status currently <state> in Test Cases sheet) and post final verification-pass results before closing"`.
> - **Pending ack**: the close is gated on a still-open maintainer ack. If your own `action_reason` would naturally read "pending @X's ack", "awaiting @X's confirmation", "pending a final verification", or "awaiting confirmation from @X", you are NOT closing — you are asking. Emit `"@<maintainer>: please ack <user>'s close request for this <issue-type> (>1 week)"` instead (add the `(>1 week)` suffix when the close request is > 7 days old).
>
> Phase 4d (`run_phase4d_ar.py`) audits both conditions and force-routes violations to `Need Response`. Alt-path closes with zero Test Cases sheet rows AND no ack-pending language (manual benchmark verification, perf-investigation, won't-fix) keep `"Close the fixed issue"`.

- `"Verify fix from merged PR <ref> and close"`
- `"Land PR <ref>"` — emitted ONLY for OPEN VERIFIED `fixes`/`supersedes` PRs that pass ALL gates (CI green, no unresolved review threads, reviewDecision == APPROVED). This is the "ready to merge" terminal state. Mutually exclusive with the gate verbs below — if any gate fails, emit the matching gate verb INSTEAD of `Land PR`.
- `"RETRIAGE_PRS"` — **v4.15: deprecated literal token**. Do NOT emit it bare. Instead emit `"PR <ref> closed unmerged; reassess fix path"` plus an `"@<assignee>: please reassess fix path ..."` Need-Response verb.
- `"label not_target and close"`
- `"Resolve unresolved review comments on PR <ref>"` — Gate-2 verb (unresolved review threads). Emitted INSTEAD of `Land PR`, not in addition.
- `"Resolve unresolved review comments on PR <ref> (>1 week)"` — when latest unresolved-thread comment > 7 days old.
- `"Address CI failures on PR <ref>"` — Gate-1 verb (red/failing required CI checks). Emitted INSTEAD of `Land PR`. Highest priority among gate verbs (CI red wins over unapproved or unresolved reviews).
- `"Address CI failures on PR <ref> (>1 week)"` — when latest failing required check `completedAt` > 7 days old.
- `"Wait for review on PR <ref>"` — Gate-3 verb. Emitted when an OPEN VERIFIED `fixes`/`supersedes` PR has CI green AND no unresolved review threads BUT `reviewDecision != APPROVED` (i.e. awaiting first approval or needs re-review). Emitted INSTEAD of `Land PR`.
- `"Wait for review on PR <ref> (>1 week)"` — stale variant when the PR has been awaiting review for > 7 days (measured from the most recent commit push timestamp; fall back to PR `createdAt` if no pushes recorded).
- `"Wait for fix PR"` (v4.15 canonical; v4.14 alias `"Wait for PR"` still accepted) — when zero VERIFIED PR exists but a maintainer/assignee comment describes a concrete fix path or plan to file a PR (see WAIT_FOR_PR rule below).
- `"@<author>: please reply to @<maintainer>'s request for <X>"` — Need-Response template. `<author>` = issue Reporter login unless the maintainer's most recent comment @-mentions a different handle to reply; `<maintainer>` = the comment author with `authorAssociation in {OWNER, COLLABORATOR, MEMBER}` whose request is the most recent unanswered one; `<X>` = concise summary of what was asked (≤120 chars for workbook cell display; full text in md/html and in cell hover tooltip).
- `"@<author>: please reply to @<maintainer>'s request for <X> (>1 week)"` — stale-suffix variant when the unanswered request is > 7 days old.
- `"@<author>: please reply to @<m1>'s request for <X1>; please reply to @<m2>'s request for <X2>"` — multi-ask form: same author replying to multiple maintainers' open asks, joined with `"; "`.
- `"No action — investigate further"`

### Phase 4e Dependency Audit verbs (v4.29)

These verbs are emitted by **Phase 4e** (`run_phase4e_dependency.py`), not by Phase 4b. They are listed here so Phase 4b agents recognize them as canonical and do NOT strip them on re-runs. The 7 tracked components are `driver`, `oneAPI`, `oneDNN`, `oneCCL`, `oneMKL`, `triton`, `upstream-pytorch`.

**`upstream-pytorch` carve-out (v4.27)**: For `Dependency=upstream-pytorch` rows, Phase 4e emits these verbs ONLY when the upstream ref is a `pytorch/pytorch` **issue** (open or closed). If the ref is a PR (any state) or no ref is discoverable, the row is treated as `false_dep`: `Dependency` and `dependency_reason` are cleared and no verbs are emitted. Rationale: a pytorch.git PR means the team owns or is tracking the fix, and no-ref-at-all means the AR is "submit it ourselves" — neither is a blocking upstream dependency. The other 6 components keep unchanged behaviour.

**Internal-tracker prefix carve-out (v4.28, extended v4.29)**: When no github upstream ref is found, Phase 4e scans `action_reason` + `root_cause` + `fix_approach` for Intel-internal tracker IDs and treats a component-aligned match as an OPEN upstream ref. Prefix-to-component map: `MFDNN-NNNN` → `oneDNN`; `IGC-NNNN`, `GSD-NNNN`, `PTI-NNNN` → `driver`; `CMPLRLLVM-NNNN`, `CMPLRTOOLS-NNNN`, `LLVMSPIRV-NNNN`, `CMPLR-NNNN` → `oneAPI`. Pattern requires `-` or `_` separator and 3-7 digits; regex alternation orders longer prefixes first so `CMPLRLLVM` is never partially matched as `CMPLR`. Match emits `Wait for dependency fix <PREFIX-NNNN>` (bucket `Wait for dependency fix`) instead of `Submit issue`.

- `"Add label 'dependency component: <Name>' - <reason>"` — Phase 4e D2: issue's fix truly depends on the listed tracked component but the GitHub `Labels` field lacks the corresponding `dependency component: <Name>` label. Phase 4d routes this row to AR bucket `Add label`.
- `"Wait for dependency fix <org>/<repo>#<N>"` — Phase 4e D3: fix is blocked on an OPEN upstream issue or PR in one of the tracked component repos. Phase 4d routes this row to AR bucket `Wait for dependency fix`.
- `"Reporter to verify the fix from <org>/<repo>#<N> landed in <component> and provide reason"` — Phase 4e D3: upstream PR is MERGED; Reporter must verify the fix is picked up. Phase 4e sets `owner_transferred = Reporter`. Phase 4d routes this row to AR bucket `Verify`.
- `"Reporter to re-investigate: upstream ref <org>/<repo>#<N> was closed without resolving and provide reason"` — Phase 4e D3: upstream issue/PR closed without merging. Phase 4e sets `owner_transferred = Reporter`. Phase 4d routes this row to AR bucket `Need Response`.
- `"Assignee to submit issue to <component> upstream - <reason>"` — Phase 4e D3: issue truly depends on the listed component but no upstream tracking ref exists. Phase 4e sets `owner_transferred = Assignee` (fallback to current `owner_transferred` if Assignee blank). Phase 4d routes this row to AR bucket `Submit issue`.

Phase 4b agents MUST NOT emit any of these verbs themselves — Phase 4b lacks the upstream-repo discovery and live-state checks required to set them correctly. If Phase 4b sees one already present in `action_TBD` (idempotent re-run), it MUST preserve it verbatim.

Free-form is allowed if no canonical fits.

## DERIVATION RULE — action_TBD from pr_analysis (mandatory)

You MUST emit at least one entry in `action_TBD` whenever there is at least one
VERIFIED PR candidate. The verb is derived deterministically from the
`live_state` of the highest-priority VERIFIED PR per this precedence
(MERGED > OPEN > CLOSED-unmerged), and — for OPEN PRs — by the **Gate Priority
Matrix** below.

| Highest-priority live_state of VERIFIED `fixes`/`supersedes` PR | action_Type    | Verb to emit                                                             |
|-----------------------------------------------|----------------|--------------------------------------------------------------------------|
| MERGED (live `state==MERGED` or `mergedAt!=null`) | VERIFY_AND_CLOSE | `"Verify fix from merged PR <ref> and close"`                            |
| OPEN                                          | (apply Gate Priority Matrix below) | one of: `Address CI failures` / `Resolve unresolved review comments` / `Wait for review` / `Land PR` |
| CLOSED unmerged AND no replacement found      | reassess fix path (v4.15: was `RETRIAGE_PRS`) | `"PR <ref> closed unmerged; reassess fix path"` AND `"@<assignee>: please reassess fix path ..."` |
| CLOSED unmerged AND replacement found via VC/VD/VE re-search | (recurse on the replacement PR's live_state) | (recurse) |

### Gate Priority Matrix (OPEN VERIFIED `fixes`/`supersedes` PRs only)

For each OPEN VERIFIED `fixes`/`supersedes` PR, evaluate gates in priority
order and emit the **first matching verb only** (mutually exclusive — gate
verbs REPLACE `Land PR`, they do NOT supplement it).

| Priority | Gate state | Verb emitted |
|----------|-----------------------------------------------------|------------------------------------------------------|
| **1**    | CI has red/failing required checks                  | `"Address CI failures on PR <ref>"` (+ `(>1 week)` if latest failing-required `completedAt` > 7d old) |
| **2**    | CI green, but unresolved review threads exist       | `"Resolve unresolved review comments on PR <ref>"` (+ `(>1 week)` if latest unresolved-thread comment > 7d old) |
| **3**    | CI green, no unresolved reviews, but `reviewDecision != APPROVED` | `"Wait for review on PR <ref>"` (+ `(>1 week)` if PR awaiting review > 7d; measure via most recent commit push timestamp, fall back to `createdAt`) |
| **4**    | CI green, no unresolved reviews, `reviewDecision == APPROVED` | `"Land PR <ref>"` |

Notes on the Gate Priority Matrix:
- CI takes priority over reviews/approval — a PR with both red CI AND missing
  approval emits ONLY `"Address CI failures on PR <ref>"`. Once CI is fixed,
  the next scrub will surface the next blocker.
- `"Land PR <ref>"` is reserved for the terminal "actually ready to merge"
  state. If you find yourself wanting to emit `Land PR` alongside a gate
  verb, you are interpreting v4.12 semantics — that is wrong as of v4.13.
- A single issue can have multiple VERIFIED `fixes`/`supersedes` PRs; emit
  one verb per PR per the matrix.

Additional rules:
- **WAIT_FOR_PR fallback** (runs before NEED_ACTION on OPEN issues with zero
  VERIFIED `{fixes,supersedes}` PRs): if a comment by a user with
  `authorAssociation in {OWNER, COLLABORATOR, MEMBER}` (Assignee preferred)
  both (a) acknowledges the bug as actionable on their side AND (b) describes
  a concrete fix path / commit URL / plan to file a PR (e.g. fork commit link,
  "prepared override", "will submit", "batched PR", "plan to file upstream"),
  emit `"Wait for fix PR"` (WAIT_FOR_PR; v4.15 canonical, v4.14 alias `"Wait for PR"` also accepted) and set `owner_transferred` to that
  comment's author. A bare "I'll look into it" without a fix path / plan does
  NOT satisfy the predicate — fall through to NEED_ACTION instead.
- If the issue is OPEN and zero VERIFIED PR candidates with `relationship in {"fixes","supersedes"}` exist **and** WAIT_FOR_PR does not apply, emit
  `"No action — investigate further"` (NEED_ACTION) — do NOT leave
  `action_TBD` empty. PR candidates classified as `surfaces`/`related` do NOT count toward this check.
- If `not_target_verdict == "label_not_target_and_close"`, emit
  `"label not_target and close"` and you MAY skip PR-derived verbs.
- **Staleness suffix**: applies to gate verbs (Priorities 1–3) and to the
  Need-Response template. Append ` (>1 week)` when the relevant signal age >
  7 days. `Land PR`, `Verify fix from merged PR…`, `Wait for PR`, `RETRIAGE_PRS`,
  `label_not_target_and_close`, and `No action — investigate further` do NOT
  receive the stale suffix.
- **Comment AR staleness**: when emitting `Address comment AR from <owner>: <topic>`, check the originating `comment_ar[].stale` flag. If true, emit the `(>1 week)` form instead. Note the required space before `(>1 week)`.
- **Downstream impact of `(>1 week)`**: the suffix is consumed by **two** downstream consumers — (1) the HTML/MD report generators in `collect_AR/generate_{html_,}report/` filter stale rows by substring `(>1 week)`; (2) Phase 4d derives the `AR` bucket `Need Response` from this suffix (see `bug_scrub/SKILL.md` → "Phase 4d AR"). Omitting the suffix when the signal actually IS stale results in stale rows being miscategorized as `Wait for PR` instead of `Need Response`, hiding them from the weekly stale-request review.
- For each verb emitted, write a corresponding 1-sentence justification in
  `action_reason` (same array length is preferred but not required;
  downstream tooling unions across both arrays).

### PR-state downgrade for Need-Response candidates

This runs AFTER comment-AR detection but BEFORE final action_TBD emission. The
Gate Priority Matrix is applied here too — Need-Response forms are NEVER
emitted when a VERIFIED `fixes`/`supersedes` PR exists for the issue.

| linked-PR state | issue state | resulting action_TBD |
|---|---|---|
| OPEN | open | Apply the Gate Priority Matrix above. Possible outcomes: `"Address CI failures on PR <ref>"` / `"Resolve unresolved review comments on PR <ref>"` / `"Wait for review on PR <ref>"` / `"Land PR <ref>"`. The original Need-Response form is suppressed. |
| MERGED | open | `"@<reporter>: please confirm fix in PR <ref> resolves the issue and close"` |
| CLOSED (not merged) | open | `"@<reporter>: PR <ref> was closed without merging - please clarify next steps"` |

**Never** return `validation_status:"OK"` with empty `action_TBD` while
`pr_candidates` contains a VERIFIED entry — that combination produced
the wrong verdicts that the obsolete `run_pass_backfill.py` was patching.

## DERIVATION RULE — owner_transferred (mandatory)

`owner_transferred` is the engineer who is on the hook for the next action.
It is NOT a record of who reported the issue.

Rules:
- **v4.17 Close/Skip + Verify carve-out (HIGHEST PRIORITY).** For rows whose
  emitted verbs are purely a combination of (a) `"Verify fix from merged
  PR <ref> and close"`, (b) `"Close the fixed issue"`, (c)
  `"label_not_target_and_close"`, (d) `"close_as_not_planned"`, or
  (e) `"Confirm fix and close"`: `owner_transferred = Reporter`. Rationale:
  the next-actor for verify/close is the Reporter (they sign off that the
  fix resolves their original bug + perform the close), NOT a maintainer.
  Even if the issue has an Assignee (e.g. the maintainer who tracked the
  fix PR), `owner_transferred` is still the Reporter. Phase 4d treats this
  set of buckets as "reporter-owned" and suppresses the `Need Owner`
  bucket regardless of blank Assignee.
- Source of truth for ALL OTHER verbs (Land PR, Address CI, Resolve
  review, Wait for review, Wait for fix PR, @<user>: please ..., etc.),
  in order: (1) issue `Assignee` if set; (2) the explicit `owner_should_act`
  from a binding comment AR; (3) blank.
- **NEVER** use the issue `Reporter` as `owner_transferred` for the
  non-carve-out verbs above. The reporter filed the bug — that does not
  make them responsible for fixing or investigating it (only for
  verifying/closing a merged fix, which is the carve-out).
- For rows whose `action_TBD` contains `"No action — investigate further"`
  (alone OR combined with other verbs like `"Address comment AR from …"`):
  if no Assignee exists, leave `owner_transferred` **blank**. Do not fall
  back to the reporter. A blank cell is intentional — it lets the Phase 5
  row classifier surface the issue under `NEEDS_OWNER` so an owner can be
  assigned manually.
- This rule applies regardless of how many tokens the `action_TBD` cell
  contains. Multi-token rows (e.g. `"Close the fixed issue | No action —
  investigate further"`) are subject to the same blank-vs-Assignee logic
  when the carve-out does NOT apply (carve-out requires PURE close/verify
  verbs — mixing with any other verb falls back to the legacy rule).

## PR hyperlink rendering

Every PR reference in `action_TBD` cells (workbook + md + html + highlight) must be rendered as a clickable hyperlink. Storage: workbook stores plain text in `cell.value` and the URL in `cell.hyperlink` (openpyxl `Hyperlink` attribute). Excel renders it as a clickable link; `openpyxl.load_workbook(data_only=True)` returns the plain text correctly. (Earlier draft used `=HYPERLINK()` formula but that returns None when reading with `data_only=True`, breaking the report regenerators.) md uses `[display](url)`; html uses `<a href="url">display</a>`. Supported ref forms use regex `(?:https://github\.com/[\w.-]+/[\w.-]+/pull/\d+|[\w.-]+/[\w.-]+#\d+|\bPR\s*#?\d+)`. Default repo for bare `#N` / `PR #N` is `intel/torch-xpu-ops`. For Need-Response rewrites where the truncated 120-char display differs from the full text, the full text is stored as an `openpyxl.comments.Comment` on the cell (Excel renders it as a hover tooltip).

## Critical rules
- Use ONLY actual gh CLI / GraphQL output. Never invent PR numbers.
- Empty arrays are valid.
- Output ONLY the JSON file — do not modify any other files.
- `<ref>` in action_TBD should be human-readable like `intel/torch-xpu-ops#3475` or `pytorch/pytorch#175657`.
- For Need-Response rewrites, you MUST call `gh pr view <pr> --json state,statusCheckRollup` and `gh pr checks <pr>` on every linked-PR candidate before emitting the verb, then apply the PR-state downgrade matrix.

## Version

v4.17 - May 24, 2026 - Phase 4d AR-derivation fixes four systemic bugs surfaced by #3433. Agent-side changes: (1) `"Verify fix from merged PR <ref> and close"` is now correctly routed to the `Verify` AR bucket (not `Land PR`); emit it whenever the live PR-state is MERGED. (2) `"owner_transferred"` for rows whose verbs are PURELY a combination of close/verify verbs (`"Verify fix from merged PR <ref> and close"`, `"Close the fixed issue"`, `"label_not_target_and_close"`, `"close_as_not_planned"`, `"Confirm fix and close"`) MUST be the Reporter (not the maintainer/tracker). Rationale: the next-actor for verify/close is the Reporter (they sign off + close), not whoever tracked the fix PR. See `## DERIVATION RULE — owner_transferred` for the precise condition. For all other verbs, the legacy rule still applies (Assignee | comment-AR-owner | blank; never Reporter). (3) `fires_need_owner` (Phase 4d) suppresses the `Need Owner` bucket when AR is purely `Close/Skip` and/or `Verify`, since the Reporter is on the hook (owner_transferred is set). (4) Phase 4b script `load_pr_analysis_cache` keyname fix (was reading nonexistent `pr_analysis`; correct key is `pr_candidates`). Spot-rewrote 25 retroactive rows (21 Verify-verb + 4 Close/Skip with non-Reporter owners). Examples: #3433 (chuanqi129 maintainer tracking Triton-backend PR -> mengfei25 Reporter), #2729 (Silv3S | BartoszKokoszko maintainers -> CuiYifeng Reporter), #3489 (kaileiyx -> mengfei25 Reporter). **AR counts after v4.17: Close/Skip=10, Need Owner=26 (-2), Land PR=101 (-23), Wait for PR=30, Need Response=140 (+3), Verify=34 (+28), UNCLASSIFIED=0.**

v4.16 - May 24, 2026 - RULE 1 (Close Fixed) audit guard + pending-ack guard added in Phase 4d. Agent-side: do NOT emit `"Close the fixed issue"` if (a) 1+ Test Cases sheet rows for the Issue ID fail strict RULE 1 (blank `XPU Status` counts as failure; `Stock Status ∈ {fail, error, timeout}` blocks), OR (b) the close is gated on a still-open ack (your `action_reason` would say "pending @X's ack", "awaiting @X's confirmation", "pending a final verification", "awaiting confirmation from @X"). Emit `"@<assignee>: please confirm <test_case_name> status ..."` or `"@<maintainer>: please ack <user>'s close request for this <issue-type> (>1 week)"` Need-Response verb instead. Alt-path closes with zero Test Cases sheet rows AND no ack-pending language (manual-verification / perf-investigation / won't-fix) remain valid. Phase 4d (`run_phase4d_ar.py`) audits both conditions, suppresses Close/Skip on either trigger, force-routes to Need Response, prints both audit reports. #2966 (RULE 1 violation: `test_compile_forward_clone_xpu_float32` blank XPU Status) and #2766 (pending-ack: BBBela's 2026-05-07 close request awaiting @EikanWang for 17 days) rewritten retroactively.

v4.15 - May 24, 2026 - Phase 4d AR refinements. (B1) `No action - investigate further` -> `Need Response` routing explicitly documented (was implicit in v4.14). (C) `"RETRIAGE_PRS"` literal deprecated: do NOT emit bare; use `"PR <ref> closed unmerged; reassess fix path"` + an `"@<assignee>: please reassess fix path ..."` Need-Response verb instead. 3 prior `RETRIAGE_PRS` rows (#3006/#2968/#2752) rewritten retroactively. (D) Need-Response @-template generalized: any verb matching `startswith("@") and "please " in lower()` routes to `Need Response`. (F) `Verify` AR bucket gained a live `gh pr view --json state,mergedAt` fallback when Phase 4b `pr_analysis` cache is empty for an issue (24h-TTL cache at `agent_space/phase4d_verify_pr_state_cache.json`). (H) Canonical Phase 4b verb renamed `"Wait for PR"` -> `"Wait for fix PR"`; `fires_wait_for_pr` accepts both spellings + the legacy `"Monitor ..."` prefix for backward compatibility. AR bucket label `Wait for PR` (bucket name) unchanged.
v4.13 - May 24, 2026 - Gate verbs are now MUTUALLY EXCLUSIVE with `Land PR <ref>` (reversal of v4.12 "emit both" rule). New Gate Priority Matrix for OPEN VERIFIED `fixes`/`supersedes` PRs: (P1) red CI → `Address CI failures on PR <ref>`; (P2) green CI + unresolved review threads → `Resolve unresolved review comments on PR <ref>`; (P3) green CI + no unresolved + `reviewDecision != APPROVED` → NEW VERB `Wait for review on PR <ref>` (+ `(>1 week)` if PR awaiting review > 7d via most-recent commit push timestamp, fall back to `createdAt`); (P4) green CI + no unresolved + APPROVED → `Land PR <ref>` (terminal "ready to merge"). Only ONE verb per OPEN PR — CI red wins over unapproved or unresolved. STEP 3 renamed "Gate 1 CI / Gate 2 Resolving / Gate 3 Review / Gate 4 Land" to reflect priority order. PR-state downgrade matrix's OPEN-rows collapsed into a single "apply Gate Priority Matrix" pointer.
v4.12 - May 23, 2026 - Collapsed TRACK_PR → LAND_PR verb vocabulary. All OPEN VERIFIED `fixes`/`supersedes` PRs now emit `"Land PR <ref>"` regardless of CI state (previously OPEN+green → "Land PR", OPEN+other → "Track PR <ref> to merge"). Gate verbs (`Address CI failures on PR <ref>`, `Resolve unresolved review comments on PR <ref>`) are still emitted in addition when applicable. action_Type identifier renamed `TRACK_PR` → `LAND_PR`. PR-state downgrade matrix's R3 row (OPEN+red/pending CI) updated to emit `Land PR <ref>` + gate verb instead of the legacy Need-Response form.
v4.11 - May 20, 2026 - Corrected PR-hyperlink workbook storage from =HYPERLINK formula to cell.hyperlink attribute (formula breaks openpyxl data_only=True reads).
v4.10 - May 20, 2026 - Added PR-hyperlink rendering in action_TBD (workbook HYPERLINK formula, md [text](url), html <a>), Need-Response action_TBD rewrite template "@A: please reply to @B's request for <X>" (120-char cell display + full hover tooltip), PR-status downgrade matrix (OPEN+green->Land PR, OPEN+red/pending->Need Response with CI note, MERGED->confirm-fix prompt, CLOSED->clarify-next-steps prompt), and default Need-Response filter pre-selection in bug_scrub_highlight.html.
