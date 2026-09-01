#!/bin/bash
# Publish the integrator's CURRENT working tree to the continuous-validation
# hosts. Digest is content-derived, so a host only re-runs when something
# actually changed.
cd /home/mboyle/BulkDownloader || exit 1
DIG=$(find bulk_downloader tests tools toolchain scripts capture.sh -type f \
      \( -name '*.py' -o -name '*.sh' -o -name 'bd-*' \) -exec sha256sum {} + 2>/dev/null \
      | sort | sha256sum | cut -c1-12)
for ip in "$@"; do
  rsync -a --delete --exclude .git --exclude venv --exclude node_modules \
        -e "ssh -o BatchMode=yes" ./ "$ip:/tmp/bd-published/" 2>/dev/null \
    && ssh -o BatchMode=yes "$ip" "echo $DIG > /tmp/bd-published/.digest" 2>/dev/null \
    && echo "published $DIG -> $ip"
done
