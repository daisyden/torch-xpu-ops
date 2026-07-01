# Bug Scrub Base Constraints (apply to ALL skills and subskills)

These constraints are binding on the top-level `bug-scrub` workflow **and every
subskill under it** (`prepare_data/*`, `analyze_ci_result/*`, `analyze_issue/*`,
`collect_AR/*`). Each subskill `SKILL.md` references this file; when a subskill
is invoked, treat the rules below as in force even if not restated locally.

> Path note: `${AGENT_SPACE}` resolves via `_common/paths.py` to
> `third_party/torch-xpu-ops/agent_space`. `${TRIAGE_ROOT}` is
> `third_party/torch-xpu-ops/issue_triage`. Expand at runtime; never hardcode.

---

## C1 - Save logs in `agent_space`

Every command, script, and subagent that produces output MUST persist that
output to a log file under `${AGENT_SPACE}/logs/`. Console-only output is not
acceptable - a later phase (or the user) must be able to reconstruct what ran.

- Create the dir once per session: `mkdir -p "${AGENT_SPACE}/logs"`.
- Naming: `${AGENT_SPACE}/logs/<phase>_<step>[_<issue_id>].log`
  (e.g. `phase1_fetch.log`, `phase2_5_local_ut_3421.log`,
  `phase4b_pr_status.log`).
- **Local tests specifically**: any locally-run pytest / reproducer / benchmark
  (notably Phase 2.5 local-case-verification) MUST `tee` its full stdout+stderr
  into `${AGENT_SPACE}/logs/`, e.g.:

  ```bash
  cmd 2>&1 | tee "${AGENT_SPACE}/logs/phase2_5_local_${issue_id}.log"
  ```

  If a subskill already writes a per-issue verification log under
  `${TRIAGE_ROOT}/local_logs/` (workbook-referenced), keep that, but ALSO
  ensure the run is captured under `${AGENT_SPACE}/logs/` for the session record.
- Never silently discard stderr. Do not redirect to `/dev/null` for anything
  whose result feeds a triage verdict.

## C2 - Stop and ask on any critical blocker

If a precondition or external dependency is broken, **STOP and ask the user
before continuing**. Do NOT silently skip, fabricate, guess, or "continue
anyway". Critical blockers include (non-exhaustive):

- Test environment does not work (e.g. `torch.xpu.is_available()` is False,
  import errors, conda env missing, source/commit mismatch after Phase 1.0).
- A provider / endpoint cannot be accessed (GitHub API auth/rate-limit failure,
  `gh` not authenticated, CI artifact download fails, an LLM/subagent provider
  is unreachable).
- Required input files are missing or corrupt (workbook, JSON cache, CI
  artifacts) and cannot be regenerated without a decision.
- A destructive action would be needed that is not already authorized
  (overwriting unbacked-up analysis, force-resetting git state).

When blocked: write the failure detail to `${AGENT_SPACE}/logs/`, then surface a
concise summary to the user with the options you see, and **wait** for a
decision. A degraded result produced by skipping a broken step is worse than
pausing.

## C3 - Background sessions must report status to a log file

Whenever a background session / async subagent is started, its status MUST be
tracked in a log file under `${AGENT_SPACE}/logs/` so progress is observable
without polling the agent runtime.

- On launch: append a line to `${AGENT_SPACE}/logs/background_status.log`
  recording `started <task_id> <description> <UTC timestamp>`.
- On completion / failure: append `done|failed <task_id> <UTC timestamp>` and
  write the task's collected output to
  `${AGENT_SPACE}/logs/<task_id>.log`.
- Long-running background work (e.g. parallel LLM extraction batches, Phase 4b
  PR analysis) must leave enough breadcrumbs in the status log that an
  interrupted session can be resumed.

## C4 - Do not over-skip deep analysis or subagents

Incremental Mode (see the main `SKILL.md`) skips only rows whose specified
completion columns are already populated. It is NOT a license to skip the
mandated deep analysis or subagent dispatch for work that is genuinely
incomplete.

- When a phase's instructions call for an **explore/librarian/oracle subagent**
  or **per-issue deep analysis** (e.g. Phase 2.4 case-existence, Phase 3.3
  triage, Phase 4b PR status, Phase 4e dependency D1), perform it - do not
  substitute a shallow heuristic, a cached guess, or a blanket "looks fine".
- Skipping is allowed ONLY by the explicit Incremental Mode skip rules
  (non-blank completion columns) or an explicit documented flag
  (`SKIP_PHASE_1_1`, `SKIP_ENV_UPDATE`). Anything else is over-skipping.
- If you are tempted to skip deep analysis to save time/tokens but no skip rule
  authorizes it, that is a C2 situation: ask the user rather than silently
  downgrading rigor.
- Phase 4 (4a-4e) is NEVER skipped per-issue (PR/CI/comment state changes
  frequently); honor that even when an issue "looks unchanged".

---

These constraints are non-negotiable. A subskill MAY add stricter rules but MUST
NOT relax any constraint here.
