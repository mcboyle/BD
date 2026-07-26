#!/usr/bin/env python3
"""Standalone entry point for BulkDownloader's offline test runner.

Import runner helpers from run_tests_core; importing this module never
activates the pytest compatibility stub.
"""
from run_tests_core import activated_pytest_stub, main


if __name__ == "__main__":
    with activated_pytest_stub():
        main()
