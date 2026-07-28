#!/bin/bash
# BulkDownloader -- Claude Code cloud panel BOOTSTRAP.
#
# THIS FILE IS THE TEXT TO PASTE INTO THE PANEL'S SETUP-SCRIPT BOX.
# It is deliberately the only BD provisioning text that lives outside the repo's
# reach, and it is deliberately trivial.
#
# Why it is this small: the panel used to hold a full copy of
# scripts/cloud-setup.sh. Pasted copies do not receive commits, so it forked --
# three commits and 91 lines behind, carrying a stale guard pin and a GTK list
# missing x11-utils, while thirteen tests reported green about the repo copy
# that never ran.
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

# Named probes and ONE bounded glob. No filesystem search: a depth-ranked
# `find /` prefers shallow /tmp pytest fixtures over the real checkout (70
# candidates, winner a 3-file fixture); both markers below shut that out.
# No hardcoded absolute either -- one made the "no checkout" branch unreachable
# on the machine it protected, and a fallback that always succeeds is not one.
# `$HOME/BD` alone missed too ($HOME is /root, the checkout is under /home/<user>/),
# and case is load-bearing: the deploy box is /home/mboyle/BulkDownloader, which
# `bulkdownloader` does not match and Linux will not forgive. Spell all three.
REPO=""
for candidate in "${BD_REPO:-}" "${CLAUDE_PROJECT_DIR:-}" "$PWD" \
                 /workspace /repo /src /app \
                 "$HOME/BD" "$HOME/BulkDownloader" "$HOME/bulkdownloader" \
                 /home/*/BD /home/*/BulkDownloader /home/*/bulkdownloader; do
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
    echo "\$CLAUDE_PROJECT_DIR, \$PWD, /workspace, /repo, /src, /app,"
    echo "\$HOME and /home/* each x {BD,BulkDownloader,bulkdownloader}."
    echo
    echo "NOTHING WAS PROVISIONED. There is no venv and no system tooling from"
    echo "this run. Do not read any test result from this environment as"
    echo "evidence about the code. Set BD_REPO to the checkout and re-run."
  } > "$REPORT"
  echo "FATAL: no BulkDownloader checkout found; see $REPORT" >&2
  exit 1
fi

# Hand the location over rather than letting cloud-setup.sh re-derive it: its
# own path list is narrower than this one, and a checkout found here but missed
# there provisions as HAVE_REPO=0 -- a READY verdict about a tree never located.
export BD_REPO="$REPO"
cd "$REPO" || { echo "FATAL: cannot enter $REPO" >&2; exit 1; }
exec bash "$REPO/scripts/cloud-setup.sh"
