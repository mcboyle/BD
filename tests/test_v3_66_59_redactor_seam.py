"""Redactor seam + dev raw-inspection mode (v3.66.59).

Covers three things:
  * the core seam defaults to the real redactor and produces redacted output
    (production is always redacted);
  * the dev-only bd_dev_inspect pass-through, when installed + enabled,
    produces a RAW capture stamped _UNREDACTED — and disabling it restores
    redaction;
  * the hard guarantee that the raw capability is excluded from release zips.

No module-level autouse fixture (the custom runner only expands autouse on
class methods), so each test that flips the seam resets it in a finally.
"""

from __future__ import annotations

import pytest

from bulk_downloader import capture_redactor
from bulk_downloader.session_capture import SessionCapture


class TestSeamDefaultsToRedacting:

    def test_active_redactor_is_real_by_default(self):
        capture_redactor._override = None
        r = capture_redactor.active_redactor()
        assert isinstance(r, capture_redactor.Redactor)
        assert r.unredacted is False
        assert r.name == "redact"

    def test_default_capture_is_redacted(self):
        capture_redactor._override = None
        cap = SessionCapture(url="https://x/", redact=True)
        cap.record_network(method="GET",
                            url="https://x/v?token=SECRET",
                            request_headers=[{"name": "Cookie", "value": "s=1"}])
        cap.set_cookies([{"name": "sid", "value": "S"}])
        d = cap.to_capture_dict()
        e = d["network_log"][0]
        assert "token=<scrubbed>" in e["url"]
        assert e["request_headers"][0]["value"] == "<scrubbed>"
        assert d["cookies"] == "<scrubbed>"
        assert "_UNREDACTED" not in d


class TestDevRawMode:
    """bd_dev_inspect is the only thing that flips the seam to pass-through.
    These tests need that dev module, which is absent from a release tree —
    so they skip cleanly there (the capability correctly does not exist)."""

    def _dev(self):
        try:
            import bd_dev_inspect
            return bd_dev_inspect
        except ImportError:
            pytest.skip("bd_dev_inspect dev package not installed "
                        "(release tree) — raw capability correctly absent")

    def test_enable_requires_flag(self, monkeypatch):
        bd_dev_inspect = self._dev()
        monkeypatch.delenv("BD_CAPTURE_RAW", raising=False)
        try:
            assert bd_dev_inspect.enable_raw_capture() is False
            assert capture_redactor.active_redactor().unredacted is False
        finally:
            capture_redactor._override = None

    def test_raw_capture_is_unredacted_and_stamped(self, monkeypatch):
        bd_dev_inspect = self._dev()
        monkeypatch.setenv("BD_CAPTURE_RAW", "1")
        try:
            assert bd_dev_inspect.enable_raw_capture() is True
            cap = SessionCapture(url="https://x/", redact=True)
            cap.record_network(
                method="GET", url="https://x/v?token=SECRET",
                request_headers=[{"name": "Cookie", "value": "s=1"}])
            cap.set_cookies([{"name": "sid", "value": "RAWCOOKIE"}])
            d = cap.to_capture_dict()
            e = d["network_log"][0]
            assert "token=SECRET" in e["url"]
            assert e["request_headers"][0]["value"] == "s=1"
            assert d["cookies"] == [{"name": "sid", "value": "RAWCOOKIE"}]
            assert d["_UNREDACTED"] is True
            assert "do not share" in d["_warning"].lower()
        finally:
            capture_redactor._override = None

    def test_disable_restores_redaction(self, monkeypatch):
        bd_dev_inspect = self._dev()
        monkeypatch.setenv("BD_CAPTURE_RAW", "1")
        try:
            bd_dev_inspect.enable_raw_capture()
            bd_dev_inspect.disable_raw_capture()
            cap = SessionCapture(url="https://x/", redact=True)
            cap.record_network(method="GET", url="https://x/v?token=SECRET")
            d = cap.to_capture_dict()
            assert "token=<scrubbed>" in d["network_log"][0]["url"]
            assert "_UNREDACTED" not in d
        finally:
            capture_redactor._override = None


class TestRawCapabilityExcludedFromRelease:
    """The hard guarantee: the unredact capability never ships."""

    def test_dev_module_is_manifest_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("bd_dev_inspect.py") is True

    def test_core_seam_is_not_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("bulk_downloader/capture_redactor.py") is False
