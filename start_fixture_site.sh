#!/usr/bin/env bash
# start_fixture_site.sh - start the test fixture site (Linux).
# Linux counterpart of start_fixture_site.bat. Default port 8899.
#     ./start_fixture_site.sh [port]
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
    echo "  ERROR: no Python found. Install Python 3.9+ first."
    exit 1
fi
if [ ! -f "tools/fixture_site.py" ]; then
    echo "  ERROR: tools/fixture_site.py not found next to this script."
    exit 1
fi

PORT="${1:-8899}"
echo "  Starting fixture site on port $PORT (Ctrl+C to stop)..."
exec "$PYEXE" tools/fixture_site.py "$PORT"
