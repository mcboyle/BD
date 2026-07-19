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

BD_DISABLE_KEEPALIVE=1 "$PYEXE" run_tests.py "$@"
exit $?
