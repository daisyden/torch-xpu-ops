---
name: quick-label
description: Read-only quick triage for one GitHub issue. Accepts a full issue URL or a bare issue number (defaulting to intel/torch-xpu-ops), extracts issue metadata, checks for duplicates in intel/torch-xpu-ops and pytorch/pytorch, delegates evidence-based root-cause tracing, assigns target ownership and need_action, and writes compact JSON to an issue-scoped quick-label output file. Use when a fast issue label decision is needed without GitHub mutations, tests, builds, or fixes.
---

# Quick Label Issue Triage

Triage one GitHub issue through a read-only, evidence-based workflow and emit a
compact label decision as JSON.

## Inputs

| Input | Required | Notes |
|---|---|---|
| `issue_link` or issue number | Yes | Full GitHub issue URL from any repo, or a bare number defaulting to `intel/torch-xpu-ops`. |
| `pytorch_folder` | Yes | Local PyTorch checkout used for tracing. Must be non-empty. |

## Guards

- **Read-only.** Never change GitHub labels, comments, source files, branches,
  or pull requests. Never run reproduction, tests, or builds.
- **Research boundary.** Do not perform external or network research, web
  searches, or GitHub/API lookups except for the canonical issue extraction in
  Phase 1 and the duplicate searches explicitly required by Step 5 and
  `duplicates.md`. All root-cause tracing and non-duplicate classification must
  use extracted issue evidence, local source inspection, and the required local
  git history checks only; Steps 5-6 may use only their explicitly authorized
  duplicate-search results.
- **Writes.** Only the designated `output.json` plus one temporary extraction
  artifact outside the worktree.
- **Hard-stop before writing output** when the issue reference is missing,
  `pytorch_folder` is missing, extraction fails, or no usable failure evidence
  exists. Usable evidence is an extracted traceback, error message, actual
  error, or test-failure description. Never invent a failure signature.

## Workflow: Phase 1 - Extract metadata

1. **Extract first.** Follow
   [extract-issue-information](../extract-issue-information/SKILL.md) strictly
   and run its canonical script first or fallback for low_confidence fields:

   ```bash
   python3 .opencode/skills/validation/issue-triage/extract-issue-information/scripts/extract_basic_info.py \
       <issue_number_or_url> --pytorch-folder <pytorch_folder> --output $extract_json
   ```

   Its JSON is the only issue-metadata source. Hard-stop on a non-zero exit:
   exit 1 is a fetch failure or a pull-request reference, exit 2 is a malformed
   reference.

2. **Preserve extracted classification.** Build the failure signature from the
   extracted test file, test case or cases, error message, and traceback. Carry
   `issue_type`, `type`, `module`, `test_module`, and extracted `dependency` into
   the final output unchanged. Hard-stop if no usable signature remains.

## Workflow: Phase 2 - Trace, classify, and write

3. **Trace the root cause.** Delegate exclusively to a `deep` subagent with
   `pytorch_folder`, the preserved extraction fields, and the failure signature.
   Do not substitute an `explore` subagent for this tracing step.
   The subagent must consume `$extract_json`; it must not independently parse
   the issue reference or fetch or reconstruct metadata. Require cited files
   with line references, symbols, and a concrete call path. Permit only
   read-only source inspection, CodeGraph, LSP, `grep`, `read`, and local
    `git log` and `git show`. 

4. **Check the guilty commit.** Apply 
   [guilty_commit.md](../reference/guilty_commit.md) delegate exclusively to a 
   `deep` subagent to find the commit from the
   past week that introduced the failure. Scope `git log --since="7 days ago"`
   to the traced files, confirm the candidate with `git show`, and record the
   short hash and subject in `evidence.guilty_commit`, or `null` when the search
   is empty or inconclusive. Record any commit that instead fixes the root cause
   as `evidence.upstream_fix`. A skip or xfail is neither a fix nor a guilty
   commit. Do not perform external or network research.

5. **Check for duplicates.** Apply
   [duplicates.md](../reference/duplicates.md) and run its search commands
   directly. Run this after tracing so the traced root cause is available as a
   match signal alongside the test case and error message. Set
   `duplicate.has_duplicate`, `duplicate.duplicates[]`, and
   `duplicate.confidence` per that reference. Never re-fetch the issue itself.

6. **Inherit exclusion labels.** Set `not_target`, `wontfix`, and
   `exclusion_source` from the inherited-exclusion-labels table in
   [duplicates.md](../reference/duplicates.md), and cite the duplicate's URL in
   `reason` whenever a flag is inherited.

7. **Classify the traced dependency.** Use
   [dependnecy.md](../reference/dependnecy.md) and delegate exclusively to a `deep` 
   subagent to set `traced_dependency` to exactly one taxonomy value, 
   `none`, or `null`. Require direct traceback,
   source, or operator evidence; use `null` for missing or equally ambiguous
   evidence. Never infer a dependency from incidental issue prose or from an
   operator name alone.

8. **Classify priority.** Use [priority.md](../reference/priority.md) and apply
   conditions in highest-severity order: P0, P1, P2, then P3. Use only evidence
   from the extracted issue, including its body and comments when available,
   and from the traced root cause.

9. **Determine ownership.** Use
   [target_component.md](../reference/target_component.md) and select the
   location that must change. Never infer ownership from labels, domains, or
   from where the failure surfaces.

10. **Determine the action.** Apply
    [need_action.md](../reference/need_action.md), treating an inherited
    `not_target` or `wontfix` from Step 6 the same as an own label. Preserve the
    canonical tracing verdict in `verdict` and emit the user-facing value in
    `need_action`.

11. **Write the result** only to
    `agent_space/quick_label/<repo_slug>_issue_<id>/output.json`, deriving
    `repo_slug` from the extracted `repo` by replacing `/` with `_`. Remove
    `$extract_json` after writing the result or on a hard-stop.

## Output contract

```json
{
  "source_issue": {"issue_id": 0, "repo": "owner/repo", "title": ""},
  "issue_type": "Bug | Task | Feature | Epic",
  "type": "feature request | performance issue | accuracy issue | functionality bug | internal task | unknown",
  "module": "distributed | inductor | dynamo | aten_ops | AO | low_precision | profiling | optimizer | fx | export | autograd | unknown",
  "test_module": "ut | e2e | build | infrastructure",
  "dependency": "oneDNN | oneMKL | Triton | AO | transformers | oneAPI | driver | oneCCL | \"\"",
  "failure_signature": "verbatim extracted error or traceback",
  "duplicate": {
    "has_duplicate": false,
    "duplicates": [
      {
        "issue_url": "", "repo": "owner/repo", "issue_number": 0, "title": "",
        "state": "OPEN | CLOSED", "labels": [],
        "relevance": "HIGH | MEDIUM | LOW",
        "match_evidence": "Why this issue matches.",
        "recommended_action": "close_as_duplicate | cross_link | merge_context"
      }
    ],
    "confidence": "High | Medium | Low"
  },
  "not_target": false, "wontfix": false,
  "exclusion_source": "own_labels | duplicate:owner/repo#0 | null",
  "traced_dependency": "driver | IGC | Level Zero | oneMKL | oneDNN | oneCCL | oneAPI | MSVC | Triton | community | third_party_packages | none | null",
  "priority": "P0 | P1 | P2 | P3",
  "target_component": "test-case | pytorch | torch-xpu-ops | third-party | N/A",
  "verdict": "NEED_FIX | NEED_FIX_CASE | NEED_FIX_3RDPARTY | NEED_HUMAN",
  "need_action": "NEED_FIX | NEED_3RPARTY_FIX | NEED_HUMAN",
  "root_cause": "Specific cause and required fix location.",
  "evidence": {
    "files": ["path:line"], "symbols": ["symbol"],
    "call_path": "caller -> callee -> failure",
    "guilty_commit": "shorthash subject or null",
    "upstream_fix": "commit or null", "skip_xfail_is_fix": false
  },
  "reason": "Why ownership, duplication, and need_action were selected."
}
```

Field notes:

- `target_component` is `"third-party"` for an external dependency owner and
  `"N/A"` for an inconclusive or unresolved case. Pair a third-party owner with
  `verdict: "NEED_FIX_3RDPARTY"` and `need_action: "NEED_3RPARTY_FIX"`.
- Product ownership is `NEED_FIX` unless `not_target` or `wontfix` is `true`,
  whether from the issue's own labels or inherited from a duplicate.

## Final checklist

- [ ] The issue was extracted first, with the canonical script, and is the requested repository.
- [ ] Every `low_confidence` field was resolved inline before tracing, using only the extraction skill's rules.
- [ ] `pytorch_folder` and usable failure evidence were present, and `issue_type`, `type`, `module`, `test_module`, and `dependency` were preserved.
- [ ] Root-cause tracing cites files, symbols, and a call path.
- [ ] Root-cause tracing used a `deep` subagent, not an `explore` substitute.
- [ ] The guilty-commit search followed `guilty_commit.md`: right repository, scoped to traced files, `git show` confirmed.
- [ ] A fixing commit was recorded as `upstream_fix`, not as the guilty commit; skip and xfail were treated as neither.
- [ ] Duplication ran after tracing, followed `duplicates.md`, and used the traced root cause as a match signal.
- [ ] Every duplicate search requested `state` and `labels` inline, with no per-candidate `gh issue view`.
- [ ] `not_target` and `wontfix` were inherited only from a HIGH or MEDIUM relevance duplicate, with its URL cited.
- [ ] `traced_dependency` uses direct evidence and the reference taxonomy; ambiguous evidence is `null`.
- [ ] Priority follows P0-to-P3 severity order and ownership follows the fix location table.
- [ ] `output.json` is valid JSON, has every required field, and this `SKILL.md` is under 200 lines.
