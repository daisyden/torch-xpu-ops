#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${BUG_SCRUB_PY:-/home/daisyden/miniforge3/envs/pytorch_opencode_env/bin/python}"
[[ -x "$PY" ]] || PY=python3
exec "$PY" "$HERE/test_extractor_classifier.py" "$@"
