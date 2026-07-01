# Phase 1.0 — Test Environment Setup (PREREQUISITE for the entire bug-scrub pipeline)

> **Base constraints**: This subskill is governed by [`../../base-constraints.md`](../../base-constraints.md) (C1 logs->agent_space, C2 ask-on-blocker, C3 background status, C4 no over-skip). They apply even when not restated here.

> **Path convention**: `${PYTORCH_REPO_ROOT}` (default `~/upstream/pytorch`) — see [`../../SKILL.md`](../../SKILL.md) for the full convention.

Run this **once at the start of every bug-scrub session**, before any other
phase touches the workbook. The same conda env + synced source repo are
consumed by every later phase that imports `torch` or runs tests
(notably Phase 2.5 local-case-verification).

Skip with `SKIP_ENV_UPDATE=1` if a prior session in the same day already
performed the update (`git pull` shows no upstream advance and
`torch.__version__` is unchanged).

### 1.0.1 — Activate conda env

```bash
source "${CONDA_ACTIVATE:-$HOME/miniforge3/bin/activate}" "${PYTORCH_ENV:-pytorch_opencode_env}"
```

### 1.0.2 — Refresh pytorch + torch-xpu-ops sources

```bash
cd "${PYTORCH_REPO_ROOT:-$HOME/upstream/pytorch}"
git pull --ff-only

cd "${PYTORCH_REPO_ROOT:-$HOME/upstream/pytorch}/third_party/torch-xpu-ops"
git pull --ff-only
```

### 1.0.3 — Install latest XPU nightly torch

```bash
pip3 install --pre torch \
    --index-url https://download.pytorch.org/whl/nightly/xpu
```

### 1.0.4 — Install latest XPU nightly triton

Downloaded separately so pip's dependency resolver does not pin torch:

```bash
pip download --no-deps \
    --index-url https://download.pytorch.org/whl/nightly/xpu \
    --pre pytorch-triton-xpu \
    --dest triton_whl
pip install --root-user-action=ignore triton_whl/pytorch_triton_xpu-*.whl
```

### 1.0.5 — Sync source repo to installed torch's git commit

The pytorch source tree at `${PYTORCH_REPO_ROOT}` and the installed `torch`
package must point at the **same git commit**. If the source HEAD has advanced
past the installed package's build commit, in-tree test files will silently
import newer symbols absent from the installed `torch.testing._internal.*`
modules, and every collection will fail with `ImportError`.

```python
target = torch.version.git_version
head   = git rev-parse HEAD     (in ${PYTORCH_REPO_ROOT})

if target == head:               # already in sync, no-op
    return
if worktree has uncommitted tracked changes:
    abort  # refuses to clobber user work; user must stash/commit
if target not present locally:
    git fetch origin <target>
git branch bug_scrub_pre_commit_sync_<UTC_timestamp> <head>   # safety branch
git checkout <target>            # detached HEAD on target
```

The previous HEAD is preserved on `bug_scrub_pre_commit_sync_<YYYYMMDD_HHMMSS>`;
the resulting HEAD is detached on the target commit. Untracked files (e.g.
the `third_party/torch-xpu-ops/` clone) are not touched.

### 1.0.6 — Record versions for reproducibility

```bash
python -c "import torch; print('torch', torch.__version__, 'xpu', torch.xpu.is_available())"
python -c "import pytorch_triton_xpu; print('triton-xpu', pytorch_triton_xpu.__version__)" 2>/dev/null || true
```

These commands run in the order shown. **Failure of any step aborts the
session** with an explicit error — do not silently continue, because every
downstream verdict (CI matching, local verification, triage) would be
against a stale environment.

### Trust rule (downstream consumers of test results)

Phase 3.3 (`triage_skills`) and Phase 4a (`close_or_skip`) **may treat any
locally-produced test verdict as authoritative evidence iff**:

- Issue body / labels indicate the platform is **PVC (Ponte Vecchio)**, AND
- Issue body / labels indicate the OS is **Linux**.

Otherwise local results are informational only. The PVC + Linux check is
performed by the consuming phase, not by Phase 1.0.
