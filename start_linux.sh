#!/usr/bin/env bash
# start_linux.sh - launch BulkDownloader on Linux.
# Linux counterpart of the Windows app launcher. Uses the venv if one
# exists. The app serves on http://localhost:5555 by default.
set -u
cd "$(dirname "$(readlink -f "$0")")"

PYEXE=""
if [ -x "venv/bin/python" ]; then
    PYEXE="venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYEXE="python3"
elif command -v python >/dev/null 2>&1; then
    PYEXE="python"
fi
if [ -z "$PYEXE" ]; then
    echo "  ERROR: no Python found. Run ./install_linux.sh first."
    exit 1
fi

echo "  Starting BulkDownloader (Ctrl+C to stop)..."
exec "$PYEXE" downloader_ui.py "$@"
