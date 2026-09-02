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

# Match the existing extracted-release-suite ceiling; individual tests retain
# their own timeouts, while this bounds a wedged runner outside the process.
CAP_SECONDS="${TEST_RUN_CAP_SECONDS:-3600}"
case "$CAP_SECONDS" in
    ''|*[!0-9]*|0)
        echo "  ERROR: TEST_RUN_CAP_SECONDS must be a positive whole number."
        exit 2
        ;;
esac
if ! command -v timeout >/dev/null 2>&1; then
    echo "  ERROR: coreutils timeout is required to bound this test run."
    exit 2
fi

timeout --kill-after=10 "$CAP_SECONDS" env -u BD_INSTALL_DIR \
    BD_DISABLE_KEEPALIVE=1 "$PYEXE" run_tests.py "$@"
RC=$?
if [ "$RC" -eq 124 ]; then
    echo "  TEST-RUN-CAPPED: run_tests.py exceeded ${CAP_SECONDS}s."
fi
echo
echo " ================================================================"
echo "  Test run finished - exit code $RC."
echo " ================================================================"
exit $RC
