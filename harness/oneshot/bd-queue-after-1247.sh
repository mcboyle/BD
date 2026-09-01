#!/bin/bash
# Wait for cut 2 (v3.66.1247) to merge, then run the remaining three cuts.
set -u
for _ in $(seq 1 120); do
  git -C /home/mboyle/BulkDownloader ls-remote origin main 2>/dev/null | grep -q . && \
  [ "$(git -C /home/mboyle/BulkDownloader show origin/main:bulk_downloader/__init__.py 2>/dev/null | sed -n 's/^__version__ = "\(.*\)"/\1/p')" = "3.66.1247" ] && break
  git -C /home/mboyle/BulkDownloader fetch --quiet origin 2>/dev/null
  sleep 30
done
exec bash /home/mboyle/bd-queue-run.sh
