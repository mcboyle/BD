"""v3.66.48 — operator-controllable provider resolution.

C2/C4-B/P5-2b were all wired but inert in production because no live
caller passed ``resolve_providers=True``. This lands a per-site config
flag (``resolve_providers``, default OFF) on the runner's live
deep_detect fallback, so an operator can turn provider resolution on
for a site. When enabled the runner also:
  * builds the C2 JWPlayer ``signing_callback`` from the same per-site
    config via ``build_signing_callback`` and threads it through, and
  * supplies an ``http_get`` adapter (the ``(url) -> (status, headers,
    body)`` contract the resolvers need) built from the live httpx
    client — deep_detect_live forwards ``http_get`` but does NOT adapt
    its ``http`` client, so without this the resolvers would get no
    fetcher and silently no-op.
  * emits a one-time WARNING to stderr (outbound third-party requests +
    the YouTube signatureCipher decipher; obfuscation-only, no DRM).

Default-off is verified too: a site with no ``resolve_providers`` key
threads ``resolve_providers=False`` and no warning, preserving the
pre-v3.66.48 behaviour exactly.
"""
import sys
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.bd_module_wipe


class _FakeCtx:
    def cookies(self):
        return []


class _FakePage:
    def __init__(self, html, url="https://example.test/p/1"):
        self._html = html
        self.url = url
        self.context = _FakeCtx()

    def content(self):
        return self._html


def _runner(config):
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner.__new__(SiteRunner)
    r.site_id = "flagtest"
    base = {"deep_detect_fallback": True, "runner_use_live_dd": True}
    base.update(config)
    r.config = base
    return r


HTML = "<html><body>" + "x" * 500 + "</body></html>"


def _drive(config, capsys=None):
    """Run _try_deep_detect_fallback with deep_detect_live patched to
    capture the kwargs it receives. Returns the captured kwargs dict."""
    r = _runner(config)
    page = _FakePage(HTML)
    from bulk_downloader import deep_detect
    captured = {}

    def fake_dd_live(html, **kwargs):
        captured.update(kwargs)
        return {"buckets": {"accepted": []}, "blockers": {}}

    with patch.object(deep_detect, "deep_detect_live", fake_dd_live), \
            patch("bulk_downloader.runner_extractors.find_best_download",
                  lambda *a, **k: None):
        r._try_deep_detect_fallback(page, "https://x.test/", {})
    return r, captured


class TestDefaultOff:
    def test_no_config_threads_resolve_providers_false(self):
        _r, cap = _drive({})
        assert cap.get("resolve_providers") is False

    def test_no_config_passes_no_signing_callback(self):
        _r, cap = _drive({})
        assert cap.get("signing_callback") is None

    def test_no_warning_when_off(self):
        r = _runner({})
        page = _FakePage(HTML)
        from bulk_downloader import deep_detect

        def fake_dd_live(html, **kwargs):
            return {"buckets": {"accepted": []}, "blockers": {}}

        with patch.object(deep_detect, "deep_detect_live", fake_dd_live), \
                patch("bulk_downloader.runner_extractors.find_best_download",
                      lambda *a, **k: None):
            import io
            from contextlib import redirect_stderr
            buf = io.StringIO()
            with redirect_stderr(buf):
                r._try_deep_detect_fallback(page, "https://x.test/", {})
        assert "resolve_providers=True" not in buf.getvalue()


class TestEnabled:
    CFG = {"resolve_providers": True}

    def test_threads_resolve_providers_true(self):
        _r, cap = _drive(self.CFG)
        assert cap.get("resolve_providers") is True

    def test_supplies_http_get_callable(self):
        _r, cap = _drive(self.CFG)
        assert callable(cap.get("http_get"))

    def test_emits_warning_once(self):
        r = _runner(self.CFG)
        from bulk_downloader import deep_detect
        import io
        from contextlib import redirect_stderr

        def fake_dd_live(html, **kwargs):
            return {"buckets": {"accepted": []}, "blockers": {}}

        buf = io.StringIO()
        with patch.object(deep_detect, "deep_detect_live", fake_dd_live), \
                patch("bulk_downloader.runner_extractors.find_best_download",
                      lambda *a, **k: None), \
                redirect_stderr(buf):
            page = _FakePage(HTML)
            r._try_deep_detect_fallback(page, "https://x.test/", {})
            r._try_deep_detect_fallback(page, "https://x.test/2", {})
        out = buf.getvalue()
        assert "resolve_providers=True" in out
        # WARN about outbound requests + the cipher, posture-honest.
        assert "no DRM" in out
        # Once only — second call must not re-warn.
        assert out.count("resolve_providers=True") == 1

    def test_builds_signing_callback_from_config(self):
        # A valid module/function in config -> threaded as the callback.
        cfg = {"resolve_providers": True,
               "signing_callback_module": "json",
               "signing_callback_function": "dumps"}
        _r, cap = _drive(cfg)
        import json
        assert cap.get("signing_callback") is json.dumps

    def test_bad_signing_config_is_none_not_raise(self):
        cfg = {"resolve_providers": True,
               "signing_callback_module": "no_such_mod_xyz",
               "signing_callback_function": "f"}
        _r, cap = _drive(cfg)
        assert cap.get("signing_callback") is None
        # resolution still enabled even if the callback couldn't build.
        assert cap.get("resolve_providers") is True
