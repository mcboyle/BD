#!/usr/bin/env bash
# run_all_tests.sh - run the entire BulkDownloader test suite (Linux).
# Linux counterpart of run_all_tests.bat. Sets BD_DISABLE_KEEPALIVE=1
# (required - the suite must not spawn persistent browser sessions) and
# runs run_tests.py. Extra arguments pass straight through:
#     ./run_all_tests.sh tests/test_contracts.py
#     ./run_all_tests.sh --workers=4 --summary
# Verify the pass count each run; do not treat it as a contract.
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
    echo "  ERROR: no Python found. Install Python 3.9+ or run the"
    echo "  installer first so a venv exists."
    exit 1
fi
if [ ! -f "run_tests.py" ]; then
    echo "  ERROR: run_tests.py not found next to this script."
    exit 1
fi

echo
echo " ================================================================"
echo "  BulkDownloader - Full Test Suite"
echo " ================================================================"
echo "  Clearing stale __pycache__ directories..."
find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
echo "  Running run_tests.py ..."
echo " ================================================================"
echo

env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 "$PYEXE" run_tests.py "$@"
RC=$?
echo
echo " ================================================================"
echo "  Test run finished - exit code $RC."
echo " ================================================================"
exit $RC
