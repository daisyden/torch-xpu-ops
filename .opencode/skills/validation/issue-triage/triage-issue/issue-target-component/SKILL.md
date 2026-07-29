---
name: issue-target-component
description: Determine which component must own the fix for a triaged GitHub issue (pytorch, torch-xpu-ops, the test case itself, or a third-party dependency) — or that it needs a human — given the JSON output of extract-basic-info or reproduce-issue. Establishes a failure signature, always delegates tracing the failing code path in pytorch_folder to a background explore subagent, and always delegates third-party classification to a subagent running the issue-dependency skill when the path doesn't resolve cleanly to pytorch or torch-xpu-ops product code. Returns NEED_FIX / NEED_FIX_CASE / NEED_FIX_3RDPARTY / NEED_HUMAN. Analysis-only, no code changes.
---

# Issue Target Component

Analysis-only skill that answers one narrow question for a single triaged
issue: **which component owns the fix** — `pytorch` product code,
`torch-xpu-ops` product code, the **test case** itself, a **third-party**
dependency, or none of the above (`NEED_HUMAN`). Its `root_cause_result`-
shaped output (`root_cause`, `evidence.*`, `verdict`) is the primary
evidence consumed by `issue-priority` and `issue-category` downstream —
this skill is the routing/tracing entry point of the `triage-issue` family.

**You analyze; you never fix.** Never edit files, `git commit`, or open
PRs. You MAY run read-only commands (`read`/`grep`/`git log`/`git show`)
inside the given `pytorch_folder` for Steps 1/4 — Step 2's tracing
(CodeGraph/LSP/grep) is always delegated to a subagent (see Step 2).

## Inputs

| Input | Required | Notes |
|---|---|---|
| Issue JSON | yes | `extract-basic-info` output, or `reproduce-issue` output (top-level object + `results[]`). Pull `title`, `labels`, `traceback`, `test_file`/`test_class`/`test_case` (or each `test_cases[]` entry), `module`, `type`. For `reproduce-issue` output also pull each `results[]` entry's `result`, `actual_error`, `raw_tail`. **Read from the file path specified in the prompt** (e.g. `agent_space/triage_issue/step3_input.json`) — never expect the full JSON inline in the prompt. |
| `pytorch_folder` | yes | Local `pytorch` checkout, or standalone `torch-xpu-ops` checkout. Used for read-only tracing (CodeGraph/LSP/grep). |
| `conda_env` | no | Only needed if a subagent must probe the installed `torch` (e.g. to confirm a symbol's dispatch); not required for pure source tracing. |

## Workflow

### Token accounting

Steps 2 and 3 always run as separate subagent invocations (never inline).
Each `task()` call's background result carries its own token usage, so
per-phase cost is: Step 2 = tracing subagent's usage, Step 3 = 
`issue-dependency` subagent's usage (0 if Step 2 resolved and Step 3 never
ran), Steps 1/4/emit = whatever remains in this skill's own session. Use
`background_output(task_id=...)` on each subagent's task_id to inspect its
individual usage if needed.

### Step 1 — Establish the failure signature

No live reproduction is performed here. Use whichever of these already
exists:

| Available evidence | Action |
|---|---|
| `reproduce-issue` `results[]` entry shows `reproduced=true` | Use its `actual_error`/`raw_tail` as the failure signature. Set `"verified": true`. |
| `reproduce-issue` `results[]` entry shows `reproduced=false`, but has `actual_error`/`raw_tail` from the attempt | Use that as the failure signature. Set `"verified": false`. Proceed — do not stop just because it didn't reproduce. |
| Only `extract-basic-info` output, no reproduce data | Use the issue's own `traceback` field (or, if absent, the title/body error description) as the failure signature. Set `"verified": false`. |
| No `traceback`, no `actual_error`, no error description anywhere in the input | **Stop.** `verdict = "NEED_HUMAN"`, reason "No failure signature available to analyze". |
| Root cause analysis reveals the issue is a **performance issue**, **feature request**, or **task** (not a functional bug) | **Stop.** `verdict = "NEED_HUMAN"`, reason "Issue is a performance/feature/task — requires human planning, not a code-path fix". |

Whatever signature you use, it must come **verbatim** from the input —
never fabricate or paraphrase an error that isn't actually present.

### Step 2 — Trace the failing code path and classify the target component

Always delegate the tracing itself to a background `explore` subagent —
never run `codegraph_codegraph_explore`, LSP, or inline `grep`/`read`
yourself for this step. This keeps Step 2's token cost isolated in its own
subagent result (see "Token accounting" below) and keeps this skill's own
context small regardless of how many files the trace needs to touch.

#### 2a. Fire the tracing subagent

```
task(subagent_type="explore", run_in_background=true, load_skills=[],
     description="Trace <operator_name>/<test_name> failure path",
     prompt="[CONTEXT] Triaging GitHub issue <id>: <title>. Failure signature:
     <failure_signature>. [GOAL] Find exactly where <operator_name> is (or
     isn't) dispatched for XPU, or where <test_class>::<test_method> itself
     goes wrong, and identify the specific file(s)/function(s) that must
     change to fix it — root cause, not just where it fires. [DOWNSTREAM]
     Result decides which component (pytorch product code, torch-xpu-ops
     product code, or the test file itself) owns the fix. [REQUEST] If a
     CodeGraph index exists at <pytorch_folder> (check via `codegraph status`,
     never find/ls/glob), use codegraph_codegraph_explore with one batched
     query naming every symbol already suspected relevant (operator name(s),
     test class/method name(s), exception type, skip/decorator mechanism),
     plus lsp_goto_definition/lsp_find_references to confirm definitions —
     otherwise fall back to grep/read. Search <pytorch_folder>'s
     native_functions.yaml dispatch entries, src/ATen/native/xpu/ (or
     aten/src/ATen/native/ for pytorch-side), the test file itself, and
     relevant OpInfo/DecorateInfo/skip entries. Also check `git log` and,
     via `gh search prs --repo pytorch/pytorch --repo intel/torch-xpu-ops
     <operator_name>` (or similar), whether an OPEN, unmerged PR already
     implements this exact fix. Return file:line citations, not summaries,
     plus a one-line note on whether codegraph/LSP or grep/read was used,
     and separately report any matching open PR (URL + state).")
```

End your turn after firing the subagent and wait for its result — do not
duplicate the same search yourself while it runs. Do not fire this
subagent until Step 1 has established a `failure_signature` — it needs
that to search meaningfully. When the result arrives, write it verbatim to
`agent_space/issue_target_component/step2_trace.json` and append a
`session_log.txt` line (see Logging below) before proceeding to Step 2b.

#### 2b. Investigate and classify the path from the subagent's result

- Read the failure signature carefully; determine root cause **by where the
  fix must be made**, not by keywords.
- **Skip/xfail decorators are NOT fixes** — their presence confirms the
  issue exists, never conclude "already fixed" because one exists.
- Check `git log` for an existing upstream fix (report it, don't duplicate).
- Classify the path that must change:

| Path must change under... | `target_component` |
|---|---|
| The **test file itself** — e.g. a stale assertion/tolerance, a missing `sys.path`/import fix, an un-generalized CUDA-only call, a missing-attribute skip guard, a syntax error in the test — the product code underneath is correct | `"test-case"` |
| `pytorch/aten/`, `pytorch/torch/`, `pytorch/c10/` (any `pytorch` checkout path that is NOT `third_party/torch-xpu-ops/` and NOT the test file) | `"pytorch"` |
| `third_party/torch-xpu-ops/` (or a standalone checkout's `src/`) | `"torch-xpu-ops"` |
| None of the above resolves cleanly — the traced symbol lands in a driver/compiler/library call, an already-tracked upstream issue, or an external package's own code | proceed to Step 3 |

Never recommend a torch-xpu-ops fix for a pytorch-core root cause, or vice
versa — the traced path decides this, not a label or the `domain` alone.
Distinguish "test-case" from a product bug carefully: if the test correctly
calls the API and the underlying kernel/dispatch is wrong, that is
`"pytorch"`/`"torch-xpu-ops"`, not `"test-case"` — even though the failure
surfaces in a test file.

#### 2c. Check for an existing open, unmerged PR

Before finalizing the verdict, check whether an open PR in the target repo
(`pytorch/pytorch` or `intel/torch-xpu-ops`) already implements the fix for
this exact root cause — e.g. a PR referenced in commit history, linked from
the issue, or found via `gh pr list`/`gh search prs` for the traced
symbol(s). This applies regardless of which `target_component` Step 2b
resolved to.

- If such a PR is found (state OPEN, not yet merged), the code fix already
  exists and is awaiting human action (review/merge) — not new engineering
  work. Record it in `evidence.open_pr` and route to `verdict = "NEED_HUMAN"`
  in Step 4 (see the override rule there), even though `target_component`
  still reflects where the code lives.
- If no such PR exists, set `evidence.open_pr = null` and proceed normally.

### Step 3 — Third-party classification (only when Step 2 didn't resolve)

Only reached when Step 2's subagent result does not cleanly land in
`pytorch` product code, `torch-xpu-ops` product code, or the test file
itself. Always delegate to a subagent running the `issue-dependency`
skill — never re-derive this yourself:

```
task(subagent_type="general", run_in_background=false, load_skills=["validation/issue-triage/triage-issue/issue-dependency"],
     description="Classify third-party dependency for <operator_name>",
     prompt="[CONTEXT] Determining target component for GitHub issue <id>:
     <title>. Traced evidence so far: root_cause=<root_cause>,
     traced_symbols=<traced_symbols>, call_path=<call_path>,
     failure_signature=<failure_signature>. [GOAL] Classify whether this
     root cause depends on a third-party component per the issue-dependency
     skill's workflow and taxonomy. [REQUEST] Follow the issue-dependency
     skill exactly and return its JSON output verbatim (the
     third_party_dependency object).")
```

Wait for the subagent's result before continuing. Write it verbatim to
`agent_space/issue_target_component/step3_dependency.json` and append a
`session_log.txt` line (see Logging below). Carry its returned
`third_party_dependency` object forward unmodified:

```python
{
    "depends_on_third_party": bool,
    "components": [str],
    "evidence": str,
    "implementation_path": "Native SYCL" | "oneMKL" | "oneDNN" | "CPU Fallback" | "N/A"
}
```

### Step 4 — Decide the final verdict

Apply in this exact order (first match wins):

| Condition | `target_component` | `verdict` |
|---|---|---|
| Step 2c found an existing OPEN, unmerged PR that already fixes this root cause | keep Step 2b's resolved value (`"pytorch"` / `"torch-xpu-ops"` / `"test-case"`) | `"NEED_HUMAN"` — reason: "Fix already drafted in open PR <url>; awaiting review/merge, not new engineering work" |
| Step 2 resolved the path to `pytorch` product code | `"pytorch"` | `"NEED_FIX"` |
| Step 2 resolved the path to `torch-xpu-ops` product code | `"torch-xpu-ops"` | `"NEED_FIX"` |
| Step 2 resolved the path to the test case itself | `"test-case"` | `"NEED_FIX_CASE"` |
| Step 3's `third_party_dependency.depends_on_third_party == true` | `"third-party"` | `"NEED_FIX_3RDPARTY"` — name the component(s) in `reason` |
| Anything else (Step 3 returned `depends_on_third_party == false` with no resolvable in-repo path, tracing was inconclusive, or hardware/redesign is required) | `"N/A"` | `"NEED_HUMAN"` |

The open-PR override (first row) takes priority over the otherwise-matching
`NEED_FIX`/`NEED_FIX_CASE` row below it — a resolved `target_component`
still tells the caller *where* the code lives, but `verdict = "NEED_HUMAN"`
signals that no further fix implementation is needed, only human
review/merge action on the existing PR.

### Before emitting

Confirm all of these:

1. `root_cause` is specific to where the fix must be made, not just where
   it fires.
2. "Already fixed" was never concluded solely from a skip decorator.
3. `failure_signature` came verbatim from the input, never fabricated.
4. `target_component` and `verdict` are mutually consistent per the Step 4
   table — never `NEED_FIX` with `target_component == "third-party"`, etc.
5. `NEED_FIX_3RDPARTY` iff `third_party_dependency.depends_on_third_party ==
   true`; never emit it without a real `issue-dependency` result.
6. `verdict == "NEED_HUMAN"` from the open-PR override iff `evidence.open_pr`
   is populated with a real, verified OPEN/unmerged PR — never emit this
   override from a guess or a merged/closed PR.

## Logging (MANDATORY, under `agent_space/`)

Write to `agent_space/issue_target_component/` (create it if absent) and
append to `agent_space/session_log.txt`:

```
agent_space/
├── session_log.txt
└── issue_target_component/
    ├── step2_trace.json         # raw tracing-subagent result (Step 2a)
    ├── step3_dependency.json    # raw issue-dependency-subagent result (Step 3, absent if never reached)
    └── output.json              # this skill's own final Output (below)
```

`session_log.txt` line format:

```
[YYYY-MM-DD HH:MM:SS] issue-target-component:<step> | task_id: <id> | result: ok | file_refs: <path>
```

Write each step's file and log line immediately after that subagent
returns — do not batch. Write `output.json` and its closing log line right
before returning the final verdict.

## Output

```python
{
    "source_issue": {"issue_id": int, "repo": str, "title": str},
    "verified": bool,            # true only if reproduce-issue already showed reproduced=true
    "failure_signature": str,    # exception type + key message, verbatim from the input
    "codegraph_used": bool,      # true if the Step 2 subagent reported using codegraph_explore/LSP; false if it used grep/read fallback
    "root_cause": str,           # 2-3 sentences, specific to where the fix must be made
    "evidence": {
        "traced_symbols": [str],
        "call_path": str,
        "already_fixed_upstream": bool,
        "upstream_commit": str,      # hash/URL if already_fixed_upstream, else ""
        "open_pr": str | null        # URL of an OPEN, unmerged PR that already fixes this root cause (Step 2c), else null
    },
    "target_component": "pytorch" | "torch-xpu-ops" | "test-case" | "third-party" | "N/A",
    "third_party_dependency": {
        "depends_on_third_party": bool,
        "components": [str],
        "evidence": str,
        "implementation_path": "Native SYCL" | "oneMKL" | "oneDNN" | "CPU Fallback" | "N/A"
    },  # only populated when Step 3 ran; otherwise null
    "verdict": "NEED_FIX" | "NEED_FIX_CASE" | "NEED_FIX_3RDPARTY" | "NEED_HUMAN",
    "reason": str    # populated when verdict == "NEED_HUMAN" or "NEED_FIX_3RDPARTY"
}
```

When `verdict = "NEED_HUMAN"` from an **early stop** (Step 1's "no failure
signature available" case), `evidence`, `third_party_dependency`, and
`target_component` are best-effort/empty — tracing was never reached. When
`verdict = "NEED_HUMAN"` from the **open-PR override** (Step 2c/Step 4),
`evidence` and `target_component` are still fully populated from Step 2b's
tracing — only the verdict itself is overridden, and `reason` must cite
the PR URL.

## Hard rules

- NEVER make code changes, commit, or open PRs — analysis-only.
- NEVER run pytest or any test-execution command yourself to verify
  reproduction — use `reproduce-issue`'s existing result if present,
  otherwise analyze from the issue's own traceback/error info as-is.
- NEVER conclude "already fixed" solely because a skip decorator exists.
- NEVER recommend a torch-xpu-ops fix for a pytorch-core root cause, or
  vice versa.
- Code tracing (Step 2) MUST run via a background `explore` subagent —
  never run `codegraph_codegraph_explore`, LSP, or inline `grep`/`read`
  yourself for this step, regardless of whether a CodeGraph index exists.
- Never fabricate `failure_signature` — it must come verbatim from
  `reproduce-issue`'s `actual_error`/`raw_tail` or the issue's own
  `traceback` field.
- Third-party classification (Step 3) MUST run via the `issue-dependency`
  subagent — never derive `third_party_dependency` yourself in an ad-hoc
  way, and never run it when Step 2 already resolved the path.
- `NEED_FIX_3RDPARTY` iff `third_party_dependency.depends_on_third_party ==
  true`; `NEED_FIX`/`NEED_FIX_CASE` iff Step 2 resolved a concrete in-repo
  path — mutually exclusive with each other and with `NEED_HUMAN`.
- If an existing OPEN, unmerged PR already implements the fix for the
  traced root cause, emit `verdict = "NEED_HUMAN"` (not `NEED_FIX`/
  `NEED_FIX_CASE`) — the engineering work is done and only human
  review/merge remains. Only a MERGED PR counts as `already_fixed_upstream`
  (Step 2b); a still-OPEN PR is a `NEED_HUMAN` blocker, not a completed fix.

## Example

```bash
python3 .claude/skills/validation/issue-triage/extract-issue-information/scripts/extract_basic_info.py \
    https://github.com/intel/torch-xpu-ops/issues/4344
# -> feed the resulting JSON, plus pytorch_folder=..., as this skill's input
# -> this single invocation establishes the failure
#    signature, delegates code tracing to a background explore subagent,
#    optionally delegates third-party classification to a subagent running
#    issue-dependency, and returns the final NEED_FIX / NEED_FIX_CASE /
#    NEED_FIX_3RDPARTY / NEED_HUMAN verdict
```

## Scope

One issue per invocation (its `test_cases[]`, if any, analyzed together —
pick the first representative case unless cases point to clearly different
root causes, then report each separately). Read-only: no edits, no
`git commit`, no `gh` mutation. Handoff for a fix is `fix/implement`;
handoff for priority/category analysis is `issue-priority` /
`issue-category`.
</content>
