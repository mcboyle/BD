"""BulkDownloader live (online) integration tests.

These run against the RUNNING app on the deployment host — NOT the
unit suite (run_tests.py) and not a sandbox. See LIVE_TESTS_CATALOG.md
for the full catalog (L1–L36) and the artifact contract.

Entry point:
    python3 live_tests/run.py --url http://stash:5555

Nothing in this package runs at import time beyond registering the
check functions with the harness.
"""
