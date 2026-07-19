#!/usr/bin/env bash
# write_deployed_version.sh — PT11.
#
# Read __version__ from bulk_downloader/__init__.py and write the value
# (plus a timestamp) to tools/deployed_version.txt. Invoked by
# install_service.sh at install time and by the bulkdownloader.service
# ExecStartPre on every service start, so the file always reflects the
# live deployed version even after an unzip-over-the-top update that
# bypassed the installer.
#
# Resolves the app dir from its own location: tools/x.sh -> $APP_DIR is
# the parent of tools/. Works regardless of cwd or who invoked it.
#
# Exit codes:
#   0 — wrote the file (or tried; absent __init__.py becomes "unknown")
#   1 — could not create tools/ directory or write the file
#
# This script is non-disruptive: a failure here MUST NOT block the
# service start. install_service.sh writes the unit with `-ExecStartPre`
# so systemd swallows a nonzero exit.

set -u

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"

INIT_PY="${APP_DIR}/bulk_downloader/__init__.py"
OUT_FILE="${SCRIPT_DIR}/deployed_version.txt"

# Pull __version__ — match `__version__ = "X.Y.Z"` or single quotes.
# If the file isn't there, record "unknown" rather than failing.
if [ -f "$INIT_PY" ]; then
    V="$(grep -E '^__version__' "$INIT_PY" 2>/dev/null \
        | head -1 \
        | sed -E "s/.*=[[:space:]]*['\"]([^'\"]+)['\"].*/\1/")"
else
    V=""
fi

if [ -z "$V" ]; then
    V="unknown"
fi

# Ensure tools/ exists (it should — this script lives in it — but a
# borked partial extract could remove the dir between writes).
mkdir -p "$SCRIPT_DIR" || exit 1

# Two-line format: version on line 1, ISO-8601 timestamp on line 2.
# Stable, machine-parseable, human-readable.
printf '%s\nstarted: %s\n' "$V" "$(date -Iseconds)" > "$OUT_FILE" || exit 1

exit 0
