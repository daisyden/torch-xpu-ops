---
name: quick-label
description: Read-only quick triage for one GitHub issue. Accepts a full issue URL or a bare issue number (defaulting to intel/torch-xpu-ops), extracts issue metadata, delegates evidence-based root-cause tracing, assigns target ownership and need_action, and writes compact JSON to an issue-scoped quick-label output file. Use when a fast issue label decision is needed without GitHub mutations, tests, builds, or fixes.
---

# Quick Label Issue Triage

Analyze one GitHub issue. This workflow is read-only with respect to GitHub and
the worktree: it never changes issue labels, comments, source files, branches,
or pull requests. It writes only its designated local `output.json` artifact
and a temporary extraction artifact outside the worktree.

## Required inputs and hard stops

Require `issue_link` or an issue number, and a non-empty `pytorch_folder`.
Accept a full GitHub issue URL from any repository, or a bare issue number. A
bare issue number defaults to `intel/torch-xpu-ops`; a full URL determines the
repository. Load the `extract-issue-information` skill before executing this
workflow, and use only its canonical extraction script to parse and fetch the
issue. Hard-stop before writing output when the issue is missing,
`pytorch_folder` is missing, extraction fails, or no usable failure evidence
exists. Usable evidence is an extracted traceback, error message, actual error,
or test failure description. Never invent or paraphrase a failure signature.

## Workflow

1. Invoke `extract-issue-information` first and follow that skill strictly.
   Use its output as the only issue-metadata source; hard-stop if extraction
   fails. Do not run reproduction, tests, builds, or GitHub mutations.
2. If `$extract_json.low_confidence` is non-empty, resolve each listed field
   inline by reading its extracted `title` and `body`, following the
   `extract-issue-information` skill's fallback rules. Overwrite only resolved
   fields and remove their names from `low_confidence`. Do not delegate this
   metadata completion or start tracing until `low_confidence` is empty. If the
   title/body cannot resolve a field required for usable failure evidence,
   hard-stop.
3. Preserve the required extracted classification fields unchanged. Build a
   failure signature from the extracted test file, test case or cases, error
   message, and traceback. Preserve `issue_type`, `type`, `module`,
   `test_module`, and `dependency` unchanged in the final output. If none is
   usable, hard-stop.
4. Delegate deep root-cause tracing to an `explore` subagent. Pass
   `pytorch_folder`, the preserved extraction fields, and the failure
   signature. The tracing phase must consume `$extract_json`; it must not
   independently parse the issue reference or fetch/reconstruct metadata.
   Require cited file and line references, symbols, and a concrete call path.
   The subagent may use read-only source inspection, CodeGraph, LSP, `grep`,
   `read`, and local `git log` only.
5. Require the trace to check local `git log --since="7 days ago"` for an
   upstream or local fix from the past week. Record a verified fix commit when
   found. A skip or xfail is never a fix and must not justify an already-fixed
   conclusion.
6. Classify `traced_dependency` from the traced root cause using
   [dependnecy.md](../reference/dependnecy.md). Use exactly one taxonomy value,
   `none`, or `null`. Require direct traceback, source, or operator evidence;
   use `null` for missing or equally ambiguous evidence, and do not infer a
   dependency from incidental issue prose or an operator name alone.
7. Classify `priority` using [priority.md](../reference/priority.md). Apply its
   conditions in highest-severity order: P0, then P1, then P2, then P3. Use
   only evidence in the extracted issue, including extracted body and comments
   when available, and the traced root cause.
8. Determine ownership from the location that must change, using
   [target_component.md](../reference/target_component.md). Do not infer it
   from labels, domains, or where the failure surfaces.
9. Apply [need_action.md](../reference/need_action.md). Preserve the canonical
   tracing verdict in `verdict`; emit the requested user-facing value in
   `need_action`.
10. Write the result only to:
   `agent_space/quick_label/<repo_slug>_issue_<id>/output.json`.
   Derive `repo_slug` from the extracted `repo` by replacing `/` with `_`.
   Remove `$extract_json` after the final result is written or on a hard-stop.

## Compact output schema

```json
{
  "source_issue": {"issue_id": 0, "repo": "owner/repo", "title": ""},
  "issue_type": "Bug | Task | Feature | Epic",
  "type": "feature request | performance issue | accuracy issue | functionality bug | internal task | unknown",
  "module": "distributed | inductor | dynamo | aten_ops | AO | low_precision | profiling | optimizer | fx | export | autograd | unknown",
  "test_module": "ut | e2e | build | infrastructure",
  "dependency": "oneDNN | oneMKL | Triton | AO | transformers | oneAPI | driver | oneCCL | \"\"",
  "failure_signature": "verbatim extracted error or traceback",
  "traced_dependency": "driver | IGC | Level Zero | oneMKL | oneDNN | oneCCL | oneAPI | MSVC | Triton | community | third_party_packages | none | null",
  "priority": "P0 | P1 | P2 | P3",
  "target_component": "test-case | pytorch | torch-xpu-ops | third-party | N/A",
  "verdict": "NEED_FIX | NEED_FIX_CASE | NEED_FIX_3RDPARTY | NEED_HUMAN",
  "need_action": "NEED_FIX | NEED_3RPARTY_FIX | NEED_HUMAN",
  "root_cause": "Specific cause and required fix location.",
  "evidence": {
    "files": ["path:line"],
    "symbols": ["symbol"],
    "call_path": "caller -> callee -> failure",
    "upstream_fix": "commit or null",
    "skip_xfail_is_fix": false
  },
  "reason": "Why ownership and need_action were selected."
}
```

For an external dependency or other third-party owner, use
`target_component: "third-party"`. For an inconclusive or otherwise
unresolved case, use `target_component: "N/A"`.
Use `verdict: "NEED_FIX_3RDPARTY"` with `need_action: "NEED_3RPARTY_FIX"`
when a third party must fix the cause. Product ownership is `NEED_FIX` unless
the issue labels contain `not_target` or `wontfix`.

## Final checklist

- [ ] The issue was extracted first and is the requested repository.
- [ ] Any `low_confidence` fields were resolved inline before tracing.
- [ ] `pytorch_folder` and usable failure evidence were present.
- [ ] `issue_type`, `type`, `module`, `test_module`, and extracted `dependency` were preserved.
- [ ] Root-cause tracing cites files, symbols, and a call path.
- [ ] The past week's `git log` was checked; skip and xfail were not treated as fixes.
- [ ] `traced_dependency` uses direct evidence and the reference taxonomy; ambiguous evidence is `null`.
- [ ] Priority follows the reference rules in P0-to-P3 severity order.
- [ ] Ownership follows the fix location table.
- [ ] `output.json` is valid JSON and contains every required field.
- [ ] This `SKILL.md` has fewer than 200 lines.
