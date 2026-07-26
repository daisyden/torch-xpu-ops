---
name: meta
description: "Mandatory constraints that ALL skills and subagents must follow. Load this skill alongside any other skill to enforce commit discipline, output format compliance, minimal edits, logging adherence, and hard-stop on infrastructure failures. Always load as load_skills=['meta', ...] when delegating."
---

# Meta — Mandatory Skill Constraints

These rules apply to ALL skill executions and subagent delegations. They are
non-negotiable and override any conflicting behavior.

## Rule 1 — Commit Discipline

Any modification to skill files (`.opencode/skills/**`, `.claude/skills/**`) or
their associated scripts (`.py`, `.sh`, etc.) MUST be committed.

**Constraints:**
- Never commit without explicit user confirmation.
- Present a summary of changes and ask "Commit these changes?" before running `git add`/`git commit`.
- If the user declines, leave changes unstaged.
- Each commit message must describe what was fixed/changed and why.

## Rule 2 — Minimal Changes

Edits to skills, scripts, or any codebase file MUST be minimal.

**Constraints:**
- Fix only the specific problem. Do not refactor adjacent code.
- Do not "improve" formatting, naming, or structure beyond what is required.
- Do not add features or capabilities that were not requested.
- If a broader change seems warranted, propose it and wait for approval.

## Rule 3 — Output Format Compliance

When a skill defines an output JSON schema or format, the agent executing that
skill MUST produce output that strictly matches the defined format.

**Constraints:**
- Use the exact field names specified in the skill's Output section.
- Use the exact AR codes, enum values, and structure as documented.
- Do not invent new field names, alternative codes, or ad-hoc formats.
- If the skill says `"AR": "label_priority"`, output exactly `"AR": "label_priority"` — never `"AR": "AR-ADD-LABEL"` or any variation.
- Before writing output, re-read the skill's Output section to verify compliance.

## Rule 4 — Hard Stop on Infrastructure Failures

If any of the following occur, the agent MUST hard-stop immediately and report
the failure reason. Do not attempt workarounds or continue with partial results.

**Hard-stop triggers:**
- Skill loading fails (file not found, parse error, missing frontmatter).
- Model rate limit is hit (429 or equivalent throttling response).
- Required tool is unavailable (gh CLI not authenticated, conda env missing).
- Background task fails to launch or times out on infrastructure error.

**On hard-stop:**
1. Stop all further processing.
2. Report: `HARD-STOP: <reason>` with the specific error message.
3. Do not produce partial output files that may be consumed downstream.

## Rule 5 — Logging Adherence

When a skill specifies logging requirements (log files, step logs, timestamps,
structured output to `agent_space/`), the agent MUST follow them exactly.

**Constraints:**
- Write logs to the exact paths specified by the skill.
- Include all required fields (timestamps, step names, durations, file references).
- Do not skip logging steps even if the result seems trivial.
- Do not invent alternative logging formats or locations.
- If a skill says "log to `{issue_dir}/steps.log`", write there — not to stdout only.

## Enforcement

These rules are enforced by including `"meta"` in `load_skills` for every
task delegation. Orchestrators (Sisyphus, issue-triage, enable-xpu-test, etc.)
MUST include `"meta"` in their `load_skills` list when delegating to subagents.

Violation of any rule above is treated as a task failure requiring correction.
