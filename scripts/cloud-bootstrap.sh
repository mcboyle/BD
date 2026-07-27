#!/bin/bash
# BulkDownloader -- Claude Code cloud panel BOOTSTRAP.
#
# THIS FILE IS THE TEXT TO PASTE INTO THE PANEL'S SETUP-SCRIPT BOX.
# It is deliberately the only BD provisioning text that lives outside the repo's
# reach, and it is deliberately trivial.
#
# Why it is this small: the panel used to hold a full copy of
# scripts/cloud-setup.sh. Pasted copies do not receive commits, so it forked --
# measured at three commits and 91 lines behind, still carrying a guard pin the
# tree had moved past and a GTK package list missing x11-utils. Every gate in
# the suite asserted over the repo copy, which never ran. Thirteen tests
# reported green about a file with no bearing on the environment they ran in.
#
# So: locate the checkout, hand over, and do nothing else. Any install verb
# added below is a line that will drift out of the repo's sight again --
# tests/test_cloud_bootstrap_is_thin.py fails on one, on purpose.
#
# If you are editing this text IN THE PANEL: stop. Edit
# scripts/cloud-bootstrap.sh in the repo and re-paste it, or the fork is back.

set -uo pipefail

REPORT="$HOME/.claude-env-report.md"
MARKER="bulk_downloader/__init__.py"

# Named probes only. There is no filesystem search here, for the same reason
# cloud-setup.sh no longer has one: a depth-ranked `find /` prefers the shallow
# two-file pytest fixtures that accumulate under /tmp over the real checkout.
# Measured on a working container: 70 candidates, winner a 3-file fixture.
#
# The list is deliberately $HOME-relative and free of any absolute path to a
# live checkout. An earlier draft hardcoded /home/user/BD; that made the
# "no checkout" branch UNREACHABLE on the very machine it was meant to protect
# -- the probe matched the real repo no matter what the caller intended, and a
# test of the failure path instead exec'd the real provisioner. A fallback that
# always succeeds is not a fallback.
REPO=""
for candidate in "${BD_REPO:-}" "${CLAUDE_PROJECT_DIR:-}" "$PWD" \
                 /workspace /repo /src /app "$HOME/BD" "$HOME/bulkdownloader"; do
  if [ -n "$candidate" ] && [ -f "$candidate/$MARKER" ] \
     && [ -f "$candidate/scripts/cloud-setup.sh" ]; then
    REPO="$candidate"
    break
  fi
done

# No checkout is UNKNOWN, and unknown is a third state that FAILS. Exiting 0
# here would be indistinguishable from a successful provision, and the session
# would go on to read test results from an environment that has no venv.
if [ -z "$REPO" ]; then
  {
    echo "# Environment provisioning report"
    echo
    echo '```'
    echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "generated_against_version=UNKNOWN"
    echo "generated_against_commit=UNKNOWN"
    echo '```'
    echo
    echo "## VERDICT: BOOTSTRAP COULD NOT REACH ITS SUBJECT"
    echo
    echo "No checkout containing both bulk_downloader/__init__.py and"
    echo "scripts/cloud-setup.sh was found. Probed: \$BD_REPO,"
    echo "\$CLAUDE_PROJECT_DIR, \$PWD, /home/user/BD."
    echo
    echo "NOTHING WAS PROVISIONED. There is no venv and no system tooling from"
    echo "this run. Do not read any test result from this environment as"
    echo "evidence about the code. Set BD_REPO to the checkout and re-run."
  } > "$REPORT"
  echo "FATAL: no BulkDownloader checkout found; see $REPORT" >&2
  exit 1
fi

# The only line that does work, and it does it by handing over.
exec bash "$REPO/scripts/cloud-setup.sh"
