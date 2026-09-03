#!/usr/bin/env bash
# Pull the latest test_files_by_category xlsx from Intel SharePoint (DLFT dGPU site)
# via the rclone "mlts" onedrive remote. rclone auto-refreshes the OAuth token.
#
# One-time setup (already done on this machine):
#   rclone config create mlts onedrive token '<authorize-json>' \
#       drive_type documentLibrary drive_id \
#       b!LryohAxFQkedWVF6cAhOy0f7gn-55YBEoU-ea1pUBRYCp9M_88BkRLhJxRQvUQ--
# If the token fully expires, re-run:  rclone authorize "onedrive"
# and update it:  rclone config update mlts token '<new-json>'
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
REMOTE="mlts:PyTorch GPU/Upstream/UT/UT_Upstream"
FILE="test_files_by_category_20260723.xlsx"
DEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Pulling '$FILE' from SharePoint ..."
if [ -f "$DEST_DIR/$FILE" ]; then
  cp "$DEST_DIR/$FILE" "$DEST_DIR/${FILE%.xlsx}.$(date +%Y%m%d_%H%M%S).prepull.bak.xlsx"
fi
rclone copy "$REMOTE/$FILE" "$DEST_DIR/" --progress
echo "Saved to $DEST_DIR/$FILE"
