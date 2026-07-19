#!/usr/bin/env bash
# deploy_overlay.sh -- v3.66.499: safe overlay deploy with dist orphan cleanup.
#
# An `unzip -o` overlay CANNOT delete files, so every cut's vite rebuild re-hashes
# the JS/CSS assets and leaves the PREVIOUS hashes orphaned under
# frontend/dist/assets/ -- dead weight that accumulates cut over cut. Each release
# zip already carries the COMPLETE new dist, so the fix is to clear the assets dir
# (and bytecode caches) BEFORE the overlay: the unzip then writes exactly the new
# set, with zero orphans.
#
# Usage:
#   tools/deploy_overlay.sh <release_zip> [install_dir]
# Defaults: install_dir = ~/BulkDownloader
#
# Steps: verify zip -> prune dist/assets + pycache -> unzip -o -> restart ->
#        confirm /api/health version. Excludes nothing; for live-cockpit edits use
#        the documented manual exclude (tools/cockpit_console.py ENDPOINT_CATALOG.md).
set -euo pipefail

ZIP="${1:?usage: deploy_overlay.sh <release_zip> [install_dir]}"
DIR="${2:-$HOME/BulkDownloader}"

if [ ! -f "$ZIP" ]; then
  echo "deploy_overlay: zip not found: $ZIP" >&2
  exit 1
fi
if [ ! -d "$DIR" ]; then
  echo "deploy_overlay: install dir not found: $DIR" >&2
  exit 1
fi

echo "==> deploy_overlay: $ZIP -> $DIR"
echo "    zip sha256: $(sha256sum "$ZIP" | cut -d' ' -f1)"

# 1) Orphan cleanup: clear the hashed asset dir so stale bundles can't linger.
#    The zip carries the full new dist, so this is non-destructive to the result.
if [ -d "$DIR/frontend/dist/assets" ]; then
  echo "    pruning orphaned dist assets ($(find "$DIR/frontend/dist/assets" -type f | wc -l) files)"
  rm -rf "$DIR/frontend/dist/assets"
fi

# 2) Bytecode caches: an overlaid .py older than an existing .pyc runs stale.
find "$DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$DIR" -name '*.pyc' -delete 2>/dev/null || true

# 3) Overlay the new tree.
( cd "$DIR" && unzip -o -q "$ZIP" )

# 4) Re-clear caches (the overlay may have re-created __pycache__ entries).
find "$DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$DIR" -name '*.pyc' -delete 2>/dev/null || true

# 5) Restart + confirm the running version flipped.
sudo systemctl restart bulkdownloader
sleep 2
echo "    /api/health:"
curl -s localhost:5555/api/health || echo "    (health probe failed -- check the service)"
echo ""
echo "==> deploy_overlay: done. Confirm the version above matches the release."
