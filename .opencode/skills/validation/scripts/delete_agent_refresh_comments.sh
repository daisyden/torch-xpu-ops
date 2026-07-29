#!/usr/bin/env bash
# Delete all comments authored by a given user whose body starts with a given
# prefix, across a set of GitHub issues/PRs.
#
# Defaults target the batch-refresh comments left on intel/torch-xpu-ops.
#
# Usage:
#   ./delete_agent_refresh_comments.sh [--repo OWNER/REPO] [--user LOGIN]
#                                      [--prefix TEXT] [--start N] [--end N]
#                                      [--issues "N1 N2 ..."] [--dry-run]
#
# Examples:
#   # Dry-run over the default range 2000-2062
#   ./delete_agent_refresh_comments.sh --dry-run
#
#   # Actually delete over a custom range
#   ./delete_agent_refresh_comments.sh --start 2000 --end 2062
#
#   # Specific issues only
#   ./delete_agent_refresh_comments.sh --issues "2000 2005 2010"

set -euo pipefail

REPO="intel/torch-xpu-ops"
USER_LOGIN="daisyden"
PREFIX="[agent-refresh-issue-status]"
START=2000
END=2062
ISSUES=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)    REPO="$2"; shift 2 ;;
    --user)    USER_LOGIN="$2"; shift 2 ;;
    --prefix)  PREFIX="$2"; shift 2 ;;
    --start)   START="$2"; shift 2 ;;
    --end)     END="$2"; shift 2 ;;
    --issues)  ISSUES="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -n "$ISSUES" ]]; then
  NUMBERS=($ISSUES)
else
  NUMBERS=($(seq "$START" "$END"))
fi

echo "Repo:   $REPO"
echo "User:   $USER_LOGIN"
echo "Prefix: $PREFIX"
echo "Scope:  ${#NUMBERS[@]} issue(s)"
[[ "$DRY_RUN" -eq 1 ]] && echo "Mode:   DRY-RUN (no deletions)"
echo

total_found=0
total_deleted=0

for n in "${NUMBERS[@]}"; do
  # gh api --jq does not accept jq's --arg, so pipe raw JSON into standalone jq.
  # --arg keeps the prefix literal (no regex escaping). Skip if the number 404s.
  ids=$(gh api "repos/$REPO/issues/$n/comments" --paginate 2>/dev/null \
        | jq -r --arg u "$USER_LOGIN" --arg p "$PREFIX" \
          '.[] | select(.user.login==$u) | select(.body|startswith($p)) | .id') \
        || { continue; }

  [[ -z "$ids" ]] && continue

  while read -r id; do
    [[ -z "$id" ]] && continue
    total_found=$((total_found + 1))
    if [[ "$DRY_RUN" -eq 1 ]]; then
      echo "[dry-run] issue $n -> would delete comment $id"
    else
      if gh api -X DELETE "repos/$REPO/issues/comments/$id" >/dev/null 2>&1; then
        echo "issue $n -> deleted comment $id"
        total_deleted=$((total_deleted + 1))
      else
        echo "issue $n -> FAILED to delete comment $id" >&2
      fi
    fi
  done <<< "$ids"
done

echo
echo "Matched: $total_found"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Deleted: 0 (dry-run)"
else
  echo "Deleted: $total_deleted"
fi
