"""Env-driven path resolution for the bug_scrub skill tree.

Defaults preserve the legacy layout under
`~/opencode/bug_scrub_verify/ai_for_validation/opencode/issue_triage/` so this
relocated skill keeps working until the runtime data is migrated.

Override any of the following env vars to point elsewhere:

    BUG_SCRUB_DATA_DIR       runtime inputs (json caches, llm extractions)
    BUG_SCRUB_RESULT_DIR     persisted reports (xlsx, md, html)
    BUG_SCRUB_AGENT_SPACE    scratch / phase4b/4d/4e working dirs
    BUG_SCRUB_ARTIFACT_BASE_URL  raw URL prefix for hosted result artifacts
"""
from __future__ import annotations

import os
from pathlib import Path

_LEGACY_BASE = Path(
    "/home/daisyden/opencode/bug_scrub_verify/ai_for_validation"
)
_LEGACY_TRIAGE = _LEGACY_BASE / "opencode" / "issue_triage"

DATA_DIR = Path(os.environ.get("BUG_SCRUB_DATA_DIR", str(_LEGACY_TRIAGE / "data")))
RESULT_DIR = Path(os.environ.get("BUG_SCRUB_RESULT_DIR", str(_LEGACY_TRIAGE / "result")))
AGENT_SPACE = Path(os.environ.get("BUG_SCRUB_AGENT_SPACE", str(_LEGACY_BASE / "agent_space")))
ARTIFACT_BASE_URL = os.environ.get(
    "BUG_SCRUB_ARTIFACT_BASE_URL",
    "https://raw.githubusercontent.com/daisyden/ai_for_validation/main/opencode/issue_triage/result",
)

SKILL_ROOT = Path(__file__).resolve().parent.parent  # bug_scrub/
COMMON_DIR = SKILL_ROOT / "_common"
