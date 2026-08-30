#!/usr/bin/env bash
# run_test.sh - run a single BulkDownloader test file (Linux).
# Linux counterpart of run_test.bat.
#     ./run_test.sh tests/test_contracts.py [--summary] [--json]
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

if [ -z "${1:-}" ]; then
    echo "  Usage: ./run_test.sh <test_file> [--summary] [--json]"
    echo
    echo "  Available test files:"
    for F in tests/test_*.py; do
        [ -e "$F" ] && echo "    $(basename "$F" .py)"
    done
    echo
    exit 0
fi

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
exit "$RC"
