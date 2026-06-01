#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export FIXTURE_JSON="$HERE/fixture.json"
exec bash "$HERE/../_runner.sh"
