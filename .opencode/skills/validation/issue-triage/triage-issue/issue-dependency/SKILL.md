---
name: issue-dependency
description: Classify whether a traced GitHub issue root cause (pytorch or torch-xpu-ops) depends on a third-party component — driver, IGC, Level Zero, oneMKL, oneDNN, oneCCL, oneAPI/DPC++ toolchain, MSVC, Triton, an open pytorch/pytorch community issue, or an external Python package (e.g. transformers) — given the JSON output of issue-target-component. Cross-references the xpu_supported_operators_complete_list operator doc for library-backed ops, infers compiler/driver/runtime dependency from traceback signals, and delegates to check-known-issue to detect an already-tracked upstream community issue. Distinguishes a torch-xpu-ops misuse/wrapper bug (still fixable in-repo) from a genuine third-party defect (not fixable in-repo). Invoked internally by issue-target-component (Step 3) via a subagent, or standalone. Analysis-only, no code changes.
---

# Issue Dependency Classification

Analysis-only third-party-dependency classification skill for one traced
issue. Takes the evidence gathered by `issue-target-component`'s Steps 0-2 —
the established failure signature and traced code path — and determines
whether the root cause depends on something **outside torch-xpu-ops's
and pytorch's own source** — a low-level library, a compiler/toolchain,
an external Python package, or an already-tracked upstream community
issue — versus a torch-xpu-ops misuse/wrapper bug around such a
dependency that remains fixable in-repo.

Normally invoked internally by `issue-target-component` at its Step 3, via
a subagent — you don't need to call this skill separately unless you
already have traced evidence (`root_cause`, `evidence.*`, `domain`,
`failure_signature`) from some other source and just need the dependency
classification in isolation.

**You analyze; you never fix.** Never edit files, `git commit`, or open PRs.

## Inputs

| Input | Required | Notes |
|---|---|---|
| `root_cause_result` | yes | Traced evidence from `issue-target-component`'s Steps 0-2: `root_cause`, `evidence.traced_symbols`, `evidence.call_path`, `domain`, `failure_signature`. |

If `issue-target-component` already stopped early with `verdict ==
"NEED_HUMAN"` (Step 0 or Step 1, before tracing), do not invoke this
skill — that is already the final verdict; there is no traced evidence to
classify.

## Dependency taxonomy

| Component | What it means | Typical signal |
|---|---|---|
| `driver` | Intel GPU driver bug | Driver-version-gated behavior, low-level `sycl::exception` at kernel submit/launch, explicit driver version mentioned |
| `IGC` | Intel Graphics Compiler (runtime JIT kernel compiler) | JIT-compile errors, `IGC`/`ocloc` mentions, illegal-instruction errors at kernel launch |
| `Level Zero` | Level Zero runtime/driver API | `zeXxx` API errors, "Level Zero" in message, device-enumeration failures |
| `oneMKL` | Intel oneAPI Math Kernel Library | matmul/BLAS, LU/cholesky/solve, FFT ops — see operator doc Part I §1.2 |
| `oneDNN` | Intel oneAPI Deep Neural Network Library | conv/linear/batchnorm ops — see operator doc Part I §1.3 |
| `oneCCL` | Intel oneAPI Collective Communications Library | `ProcessGroupXCCL`/`c10d` collective failures |
| `oneAPI` | DPC++/icpx **host-side compiler toolchain** (build time, not runtime JIT) | `icpx: error`, `dpcpp` compilation failure, internal compiler error (ICE) while building torch-xpu-ops/pytorch SYCL kernels — distinct from `IGC` (that's the runtime GPU JIT compiler, not the host build toolchain) |
| `MSVC` | Windows MSVC compiler/linker | `error C####` codes, `cl.exe`/`LINK.exe` failures, Windows-only build breakage |
| `Triton` | Intel XPU Triton backend | `triton`/`libtriton` in traceback, `torch.compile`/Inductor-generated Triton kernel crash or codegen failure specific to the XPU Triton backend (not a plain eager-mode kernel bug) |
| `community` | Root cause is already tracked as an **open** `pytorch/pytorch` issue | A `check-known-issue` match in `pytorch/pytorch` with `state: OPEN` — bug exists upstream, blocked on the pytorch community fixing it, not something to newly implement here |
| `third_party_packages` | Bug is inside an external Python package's own code, not pytorch/torch-xpu-ops | Traceback frames rooted in `site-packages/<pkg>/` (e.g. `transformers`, `deepspeed`, `numpy`, `xformers`) where the failure originates in `<pkg>`'s code, not merely a call *into* pytorch from `<pkg>` |

`components` can hold more than one entry only when multiple independent
dependencies genuinely contribute (rare) — do not double-count driver +
IGC for the same JIT-compile failure, pick the more specific one (`IGC`).

## Workflow

### Step 1 — Identify the operator(s)

From `evidence.traced_symbols`, identify the aten operator(s) involved
(`addmm`, `conv2d`, `_flash_attention_forward`, ...).

### Step 2 — Cross-reference the operator dependency doc

Check
`.claude/skills/backend-knowledge/reference/xpu_supported_operators_complete_list.md`:

| Doc section | Component |
|---|---|
| Part I §1.2 / Part III §3.2 (`Blas.cpp`, `BatchLinearAlgebra.cpp`, `SpectralOps.cpp`: matmul/BLAS, LU/cholesky/solve, FFT) | `oneMKL` |
| Part I §1.3 (`Conv.cpp`, `Linear.cpp`, `BatchNorm.cpp`) | `oneDNN` |
| Part IV §4.1 — hardcoded CPU-fallback list (no native XPU kernel at all: `cholesky`, `_flash_attention_forward`, `linalg_eig*`, `geqrf`, `ormqr`, `triangular_solve.X`, ...) | May be an unimplemented op, not a torch-xpu-ops code bug |
| Part V | Dependency matrix — quick cross-check |
| Not found, not CPU-fallback | Assume Native SYCL (Part I §1.1 / Part III §3.1) — no library dependency, though it may still depend on driver/IGC |

The operator-dependency doc is a generated snapshot (749 XPU operators),
not live truth. An operator missing from it is "not found" — assume
Native SYCL unless traceback evidence says otherwise, not proof the
operator is unsupported.

### Step 3 — Infer compiler/driver/runtime dependency from the traceback

These never appear in the operator doc — infer directly from
`root_cause_result.failure_signature`/`evidence.call_path` using the
signal column of the [taxonomy table](#dependency-taxonomy) above:
`driver`, `IGC`, `Level Zero`, `oneCCL`, `oneAPI`, `MSVC`, `Triton`.

If the failure signature matches more than one row, prefer the most
specific mechanism actually named in the traceback (e.g. a `zeXxx` API
error is `Level Zero`, not generically `driver`).

### Step 4 — Check for an already-tracked community issue

Delegate to `check-known-issue` with `test_file`/`class_name`/`test_name`
(or the operator name) and `error_message = failure_signature`, filtered
to `pytorch/pytorch`:

```
task(subagent_type="explore", run_in_background=false, load_skills=["validation/check-known-issue"],
     prompt="Search pytorch/pytorch only for an existing issue matching
     <operator_name>/<test_name> with error signature: <failure_signature>.
     Return has_known_issue, matches[] with state and issue_url.")
```

If a match returns `state: "OPEN"` in `pytorch/pytorch` — this is the
`community` component: the bug already exists upstream and is (or should
be) someone else's fix to land, not a new investigation. Record the
issue URL in `evidence`.

If the only match is `state: "CLOSED"`, this is **not** `community` —
either it's already fixed upstream (check `already_fixed_upstream` from
`issue-target-component`) or the closed issue is unrelated; do not set
`depends_on_third_party` on a closed match alone.

### Step 5 — Check for an external Python package origin

If `evidence.call_path`/the traceback shows the failure originating
inside a `site-packages/<pkg>/` frame that is **not** `torch`,
`torch_xpu`, or a torch-xpu-ops path — and the bug is in `<pkg>`'s own
logic, not merely `<pkg>` calling into pytorch — this is
`third_party_packages`. Name the package (e.g. `transformers`,
`deepspeed`, `xformers`, `numpy`) in `components` and `evidence`.

Distinguish from a misuse bug: if `<pkg>` calls a pytorch/XPU API
correctly and pytorch/torch-xpu-ops mishandles it, that is **not**
`third_party_packages` — it's an in-repo bug that happens to be
triggered by that package.

### Step 6 — Decide `depends_on_third_party`

Set `true` only when the bug is inside the third-party component itself
— dependency-version bumps, driver/compiler defects, an unfixed upstream
community bug, or an external package's own defect are all outside what
an agent can fix in-repo.

**Exception (stays `false`)**: if the actual bug is a misuse/wrapper bug
in torch-xpu-ops's/pytorch's own call into the library/package (not the
library/package itself), set `depends_on_third_party = false` — this
remains a normal in-repo fix for `issue-target-component` to route.

Before emitting, confirm: `third_party_dependency` is evidence-based (doc
cross-reference, traceback signal, or a real `check-known-issue` result),
never a guess.

## Output

```python
{
    "source_issue": {"issue_id": int, "repo": str, "title": str},
    "third_party_dependency": {
        "depends_on_third_party": bool,
        "components": [str],         # subset of ["driver", "IGC", "Level Zero", "oneMKL", "oneDNN", "oneCCL", "oneAPI", "MSVC", "Triton", "community", "third_party_packages"], [] if none
        "evidence": str,             # doc reference, traceback signal, issue URL, or package name that justified this
        "implementation_path": "Native SYCL" | "oneMKL" | "oneDNN" | "CPU Fallback" | "N/A"
    }
}
```

## Hard rules

- NEVER make code changes, commit, or open PRs — analysis-only.
- `depends_on_third_party` must be evidence-based (doc cross-reference,
  traceback signal, or a real `check-known-issue` result), never a guess.
- Do not re-derive `root_cause`/`evidence` yourself — trust
  `root_cause_result` from `issue-target-component`. This skill only classifies
  dependency, it does not re-trace.
- A misuse/wrapper bug in torch-xpu-ops's/pytorch's own call into a
  third-party library or package is `depends_on_third_party = false`, not
  `true` — only the library/package's own defect counts as third-party.
- `community` requires an actual `check-known-issue` result with an
  **OPEN** `pytorch/pytorch` match and a recorded issue URL — never assert
  it from memory or assumption.
- `third_party_packages` requires the traceback to show the failure
  **originating** inside the package's own frames, not merely a call
  path passing through it.

## Handoff

When invoked internally by `issue-target-component` (Step 3, via a
subagent), that skill merges this output's `third_party_dependency` into
its own result and continues to Step 4 (decide target repo) itself — no
further chaining needed.

When invoked standalone, merge this output's `third_party_dependency`
into your `root_cause_result` and apply `issue-target-component`'s Step
4-6 routing/verdict logic yourself.

## Logging (MANDATORY, under `agent_space/`)

When invoked as a subagent (the normal case), write this skill's own
output JSON to `agent_space/issue_dependency/output.json` (create the
directory if absent) before returning, and append one line to
`agent_space/session_log.txt`:

```
[YYYY-MM-DD HH:MM:SS] issue-dependency | task_id: <id> | result: ok | file_refs: agent_space/issue_dependency/output.json
```

If invoked standalone (no enclosing orchestrator session log yet), still
write `agent_space/issue_dependency/output.json` — the caller is
responsible for merging this file's existence into its own logging.

## Example

```bash
# Normal usage: issue-target-component calls this internally at its Step 3
# (via a subagent), then continues its own Steps 4-6 with the result.
# Standalone usage:
# root_cause_result.json has evidence traced (root_cause, evidence.*, domain, failure_signature)
# -> feed root_cause_result.json as this skill's input
# -> merge output.third_party_dependency into root_cause_result
# -> apply issue-target-component's Step 4-6 (target repo, fix strategy, verdict)
```

## Scope

One issue per invocation (mirrors `issue-target-component` scope — if it
reported per-case results for divergent root causes, classify dependency
for each case separately). Read-only: no edits, no `git commit`, no
`gh` mutation.
