"""tests/test_local_wheelhouse.py -- Tests for Row 861 local wheelhouse & package cache harness.

Acceptance:
- Isolated install succeeds with internet disabled.
- Cache install is under 1 s.
- Socket census shows zero external connections.
- Strict security: rejects runtime public-CDN dependency.
- Lenses review: clean abstractions, positive controls, thorough edge cases.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import pytest

from tools.local_wheelhouse import (
    LocalWheelhouse,
    PackageCacheHarness,
    SocketCensus,
    WheelInfo,
    VirtualenvResult,
    SecurityViolationError,
    PackageNotFoundError,
    IntegrityError,
    NetworkLeakError,
    normalize_package_name,
    main as wheelhouse_main,
)

BD_GATE_SCOPE = "module"


@pytest.fixture
def wheelhouse(tmp_path: Path) -> LocalWheelhouse:
    """Fixture providing a clean local wheelhouse with sample cached packages."""
    wh_dir = tmp_path / "wheelhouse"
    wh = LocalWheelhouse(root=wh_dir)
    # Seed with two offline test packages
    wh.create_fixture_wheel(
        name="testpkg-alpha",
        version="1.0.0",
        py_content='VERSION = "1.0.0"\ndef run():\n    return "alpha-ready"\n',
    )
    wh.create_fixture_wheel(
        name="testpkg-beta",
        version="2.1.0",
        py_content='VERSION = "2.1.0"\ndef compute(x):\n    return x * 10\n',
    )
    return wh


class TestLocalWheelhouseEndpoints:
    """Tests for approved local endpoint format and security invariants."""

    def test_approved_local_endpoint_url_and_find_links(self, wheelhouse: LocalWheelhouse) -> None:
        """Approved endpoint must be a local file:// URL and provide valid find-links arg."""
        url = wheelhouse.endpoint_url
        assert url.startswith("file://")
        assert str(wheelhouse.root.resolve()) in url

        find_links = wheelhouse.find_links_arg
        assert find_links.startswith("--find-links ")
        assert str(wheelhouse.root.resolve()) in find_links

        # LocalWheelhouse alias check
        assert PackageCacheHarness is LocalWheelhouse

    def test_security_rejection_of_public_cdn_and_remote_urls(self) -> None:
        """Enforces invariant: never add runtime public-CDN dependency."""
        forbidden_endpoints = [
            "https://pypi.org/simple",
            "http://pypi.org/simple",
            "https://files.pythonhosted.org/packages/12/34/abc.whl",
            "https://cdnjs.cloudflare.com/ajax/libs/package",
            "https://cdn.jsdelivr.net/npm/package",
            "https://unpkg.com/browse/package/",
            "https://s3.amazonaws.com/my-bucket/wheels",
            "http://insecure-mirror.local/repo",
            "ftp://files.example.com/wheels",
        ]
        for ep in forbidden_endpoints:
            with pytest.raises(SecurityViolationError) as exc_info:
                LocalWheelhouse.validate_endpoint(ep)
            assert "forbidden" in str(exc_info.value).lower() or "public" in str(exc_info.value).lower()

    def test_security_positive_control_accepts_approved_local_paths(self, tmp_path: Path) -> None:
        """Positive control: valid local filesystem paths and file:// URLs are approved."""
        local_dir = tmp_path / "valid_wheels"
        local_dir.mkdir()
        # file:// URL
        LocalWheelhouse.validate_endpoint(f"file://{local_dir.resolve()}")
        # raw absolute filesystem path
        LocalWheelhouse.validate_endpoint(str(local_dir.resolve()))


class TestWheelhouseIndexingAndIntegrity:
    """Tests for indexing, package normalization, and integrity checking."""

    def test_wheelhouse_indexing_and_name_normalization(self, wheelhouse: LocalWheelhouse) -> None:
        """PEP 503 normalization: underscores and dots normalize to hyphens in lookup."""
        index = wheelhouse.refresh_index()
        assert "testpkg-alpha" in index
        assert "testpkg-beta" in index

        # Lookup with underscores vs hyphens
        w1 = wheelhouse.find_wheel("testpkg_alpha")
        assert w1 is not None
        assert w1.name == "testpkg-alpha"
        assert w1.version == "1.0.0"

        w2 = wheelhouse.find_wheel("testpkg-beta", version="2.1.0")
        assert w2 is not None
        assert w2.name == "testpkg-beta"

        w_missing = wheelhouse.find_wheel("nonexistent-package")
        assert w_missing is None

    def test_corrupt_wheel_integrity_check(self, wheelhouse: LocalWheelhouse, tmp_path: Path) -> None:
        """Corrupt or truncated wheel archive is rejected with IntegrityError."""
        bad_whl = tmp_path / "corruptpkg-1.0.0-py3-none-any.whl"
        bad_whl.write_bytes(b"not-a-valid-zip-file-content")

        with pytest.raises(IntegrityError):
            wheelhouse.add_wheel(bad_whl)


class TestIsolatedVirtualenvCreation:
    """Acceptance tests: isolated install, internet disabled, <1s timing, socket census."""

    def test_isolated_install_succeeds_with_internet_disabled(
        self, wheelhouse: LocalWheelhouse, tmp_path: Path
    ) -> None:
        """Acceptance test: isolated install succeeds with internet disabled."""
        target_venv = tmp_path / "isolated_venv"
        result = wheelhouse.create_isolated_virtualenv(
            venv_path=target_venv,
            packages=["testpkg-alpha", "testpkg-beta"],
            install_method="direct",
            internet_disabled=True,
            with_pip=False,
        )

        assert result.is_success, f"Venv creation failed: {result.stderr}"
        assert target_venv.is_dir()
        assert result.python_path.exists()

        # Verify the installed packages actually work when imported by the venv's python
        verify_cmd = [
            str(result.python_path),
            "-c",
            "import testpkg_alpha, testpkg_beta; "
            "assert testpkg_alpha.run() == 'alpha-ready'; "
            "assert testpkg_beta.compute(5) == 50; "
            "print('ALL_IMPORTS_VERIFIED')",
        ]
        proc = subprocess.run(verify_cmd, capture_output=True, text=True, timeout=10)
        assert proc.returncode == 0, f"Import check failed: {proc.stderr}"
        assert "ALL_IMPORTS_VERIFIED" in proc.stdout

    def test_cache_install_is_under_one_second(
        self, wheelhouse: LocalWheelhouse, tmp_path: Path
    ) -> None:
        """Acceptance test: cache install completes in under 1 second."""
        target_venv = tmp_path / "subsecond_venv"
        t0 = time.perf_counter()
        result = wheelhouse.create_isolated_virtualenv(
            venv_path=target_venv,
            packages=["testpkg-alpha"],
            install_method="direct",
            internet_disabled=True,
        )
        total_time = time.perf_counter() - t0

        assert result.is_success
        assert result.duration_seconds < 1.0, (
            f"Cache install took {result.duration_seconds:.3f}s, expected < 1.0s"
        )
        assert total_time < 1.0, f"Total operation took {total_time:.3f}s, expected < 1.0s"

    def test_socket_census_shows_zero_external_connections(
        self, wheelhouse: LocalWheelhouse, tmp_path: Path
    ) -> None:
        """Acceptance test: socket census shows zero external connections during install."""
        census = SocketCensus(block_external=True)
        target_venv = tmp_path / "census_venv"

        with census:
            result = wheelhouse.create_isolated_virtualenv(
                venv_path=target_venv,
                packages=["testpkg-alpha", "testpkg-beta"],
                install_method="direct",
                internet_disabled=True,
            )

        assert result.is_success
        assert census.external_connections == [], (
            f"Expected zero external connections, found: {census.external_connections}"
        )
        assert census.has_external_connections is False

    def test_socket_census_positive_control_blocks_external_leak(self) -> None:
        """Positive control: probe CAN say YES when an external connection is attempted."""
        census = SocketCensus(block_external=True)
        caught_leak = False

        with census:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                # Attempt outbound connection to a non-loopback address
                s.connect(("93.184.216.34", 80))
            except NetworkLeakError as e:
                caught_leak = True
                assert "Zero-network invariant violated" in str(e)
            finally:
                s.close()

        assert caught_leak is True
        assert len(census.external_connections) == 1
        assert census.external_connections[0] == ("93.184.216.34", 80)
        assert census.has_external_connections is True

    def test_missing_package_offline_fails_gracefully(
        self, wheelhouse: LocalWheelhouse, tmp_path: Path
    ) -> None:
        """Missing package in offline mode raises PackageNotFoundError without attempting network."""
        target_venv = tmp_path / "missing_pkg_venv"
        with pytest.raises(PackageNotFoundError) as exc_info:
            wheelhouse.create_isolated_virtualenv(
                venv_path=target_venv,
                packages=["totally-nonexistent-lib"],
                install_method="direct",
                internet_disabled=True,
            )
        assert "not found in local wheelhouse cache" in str(exc_info.value)


class TestCliInterface:
    """CLI smoke tests for local_wheelhouse.py."""

    def test_cli_endpoint_and_check(self, wheelhouse: LocalWheelhouse, capsys: pytest.CaptureFixture) -> None:
        """Test --endpoint and --check-endpoint via CLI."""
        rc = wheelhouse_main(["--root", str(wheelhouse.root), "--endpoint"])
        assert rc == 0
        out, _ = capsys.readouterr()
        assert out.strip() == wheelhouse.endpoint_url

        rc_valid = wheelhouse_main(["--check-endpoint", wheelhouse.endpoint_url])
        assert rc_valid == 0

        rc_invalid = wheelhouse_main(["--check-endpoint", "https://pypi.org/simple"])
        assert rc_invalid == 1

    def test_cli_list_wheels(self, wheelhouse: LocalWheelhouse, capsys: pytest.CaptureFixture) -> None:
        """Test --list via CLI."""
        rc = wheelhouse_main(["--root", str(wheelhouse.root), "--list"])
        assert rc == 0
        out, _ = capsys.readouterr()
        assert "testpkg-alpha" in out
        assert "testpkg-beta" in out
