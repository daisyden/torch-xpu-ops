"""Env-driven path resolution for the bug_scrub skill tree.

Defaults resolve relative to the skill location:
- If skill is at `pytorch/third_party/torch-xpu-ops/.opencode/skills/bug_scrub/`
  then data/result dirs are at `pytorch/third_party/torch-xpu-ops/issue_triage/`
- Falls back to legacy location if env vars point elsewhere

Override any of the following env vars to point elsewhere:

    BUG_SCRUB_DATA_DIR       runtime inputs (json caches, llm extractions)
    BUG_SCRUB_RESULT_DIR     persisted reports (xlsx, md, html)
    BUG_SCRUB_AGENT_SPACE    scratch / phase4b/4d/4e working dirs
    BUG_SCRUB_ARTIFACT_BASE_URL  raw URL prefix for hosted result artifacts
"""
from __future__ import annotations

import os
from pathlib import Path

# Resolve relative to skill location
_THIS_FILE = Path(__file__).resolve()
_SKILL_DIR = _THIS_FILE.parent.parent  # .opencode/skills/bug_scrub
_TORCH_XPU_OPS = _SKILL_DIR.parent.parent.parent  # torch-xpu-ops
_DEFAULT_TRIAGE_ROOT = _TORCH_XPU_OPS / "issue_triage"

# Fallback to legacy location if new location doesn't exist
_LEGACY_BASE = Path(
    "/home/daisyden/opencode/bug_scrub_verify/ai_for_validation"
)
_LEGACY_TRIAGE = _LEGACY_BASE / "opencode" / "issue_triage"

# Use default triage root if it exists, otherwise fall back to legacy
if _DEFAULT_TRIAGE_ROOT.exists():
    _TRIAGE_ROOT = _DEFAULT_TRIAGE_ROOT
else:
    _TRIAGE_ROOT = _LEGACY_TRIAGE

DATA_DIR = Path(os.environ.get("BUG_SCRUB_DATA_DIR", str(_TRIAGE_ROOT / "data")))
RESULT_DIR = Path(os.environ.get("BUG_SCRUB_RESULT_DIR", str(_TRIAGE_ROOT / "result")))
AGENT_SPACE = Path(os.environ.get("BUG_SCRUB_AGENT_SPACE", str(_TRIAGE_ROOT.parent / "agent_space")))
ARTIFACT_BASE_URL = os.environ.get(
    "BUG_SCRUB_ARTIFACT_BASE_URL",
    "https://raw.githubusercontent.com/pytorch/pytorch/main/third_party/torch-xpu-ops/issue_triage/result",
)

SKILL_ROOT = Path(__file__).resolve().parent.parent  # bug_scrub/
COMMON_DIR = SKILL_ROOT / "_common"
