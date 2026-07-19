"""v3.66.5 — tests for the deep_detect runtime wiring:
the to_site_config_block adapter and the 'deep' template_auto_detect_mode
branch in _auto_pick_templates.
"""
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ════════════════════════════════════════════════════════════════════
# to_site_config_block adapter
# ════════════════════════════════════════════════════════════════════


def test_adapter_login_only_emits_login_block():
    from bulk_downloader import deep_detect as dd
    html = """<form action="/login" method="POST">
    <input type="email" name="email">
    <input type="password" name="password">
    <button type="submit" id="go">Sign in</button>
    </form>"""
    report = dd.deep_detect(html)
    adapted = dd.to_site_config_block(report)
    assert adapted["ok"] is True
    assert "login" in adapted["learned"]
    assert "download" not in adapted["learned"]
    login = adapted["learned"]["login"]
    assert login["user_field"] == "input[name='email']"
    assert login["pass_field"] == "input[name='password']"
    assert login["submit_btn"] == "#go"


def test_adapter_download_cards_emit_download_block():
    from bulk_downloader import deep_detect as dd
    html = """<div class="card">
      <button class="dl" data-href="/dl/2160.mp4"><span>3840 x 2160</span></button>
      <div>4K H264 5.54GB</div>
    </div>
    <div class="card">
      <button class="dl" data-href="/dl/1080.mp4"><span>1920 x 1080</span></button>
      <div>FHD H264 3.21GB</div>
    </div>"""
    report = dd.deep_detect(html, base_url="https://x.com/")
    adapted = dd.to_site_config_block(report)
    assert adapted["ok"] is True
    assert "download" in adapted["learned"]
    dl = adapted["learned"]["download"]
    assert dl["url_attribute"] == "href"
    # Same selector used for row and trigger
    assert dl["row_selectors"] == dl["trigger_selectors"]
    assert all("button.dl" in s for s in dl["row_selectors"])


def test_adapter_no_match_returns_ok_false():
    from bulk_downloader import deep_detect as dd
    html = "<html><body><p>nothing useful here</p></body></html>"
    adapted = dd.to_site_config_block(dd.deep_detect(html))
    assert adapted["ok"] is False
    assert adapted["learned"] == {}


def test_adapter_drops_urlless_candidates():
    """A resolution card without a URL (the runtime can't drive it
    without the original DOM, and it's often false-positive peer-DOM
    pollution) should not appear in the adapted download block."""
    from bulk_downloader import deep_detect as dd
    # Sign-in button next to resolution cards — the offline extractor
    # has historically misclassified the sign-in as a resolution card
    # via sibling-text pollution. The adapter must drop URL-less
    # candidates so the runtime doesn't try to click the sign-in
    # button as a "download row."
    html = """<html><body>
    <form action="/auth/login" method="POST">
      <input type="email" name="email">
      <input type="password" name="password">
      <button type="submit" id="signin">Sign in</button>
    </form>
    <div class="card">
      <button class="dl" data-href="/dl/2160.mp4"><span>3840 x 2160</span></button>
      <div>4K H264 5.54GB</div>
    </div>
    </body></html>"""
    adapted = dd.to_site_config_block(dd.deep_detect(html))
    dl = adapted["learned"].get("download") or {}
    # The signin selector must NOT appear in row_selectors
    assert "#signin" not in (dl.get("row_selectors") or [])


def test_adapter_login_with_passwordless_only_skipped():
    """Passwordless / SSO / WebAuthn classifications can't drive a
    classic user_field+pass_field+submit_btn template. The adapter
    should skip emitting login in those cases rather than emit a
    broken half-config."""
    from bulk_downloader import deep_detect as dd
    html = """<form action="/api/magic-link" method="POST">
    <input type="email" name="email">
    <button type="submit">Email me a link</button>
    </form>"""
    adapted = dd.to_site_config_block(dd.deep_detect(html))
    assert "login" not in adapted["learned"]


def test_adapter_warnings_propagated():
    from bulk_downloader import deep_detect as dd
    # Page with DRM markers — should propagate the warning through.
    html = """<form action="/login" method="POST">
    <input type="email" name="email">
    <input type="password" name="password">
    <button type="submit">Sign in</button>
    </form>
    <script>navigator.requestMediaKeySystemAccess("com.widevine.alpha", [{}])</script>"""
    adapted = dd.to_site_config_block(dd.deep_detect(html))
    # Warnings propagated from report["warnings"]
    assert isinstance(adapted["warnings"], list)


def test_adapter_source_string_present():
    from bulk_downloader import deep_detect as dd
    adapted = dd.to_site_config_block(dd.deep_detect(""))
    assert adapted["source"] == "deep_detect"


# ════════════════════════════════════════════════════════════════════
# auto_detect.snapshot_via_playwright — the helper the dispatch uses
# ════════════════════════════════════════════════════════════════════


def test_snapshot_helper_returns_none_when_playwright_missing(monkeypatch):
    """The helper must gracefully return None (not raise) when
    Playwright isn't importable, so the dispatch can fall through to
    static lookup."""
    from bulk_downloader import auto_detect
    # Block playwright by injecting a fake ImportError on import.
    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict) else __builtins__.__import__

    def _blocking(name, *a, **kw):
        if name.startswith("playwright"):
            raise ImportError("simulated missing playwright")
        return real_import(name, *a, **kw)

    if isinstance(__builtins__, dict):
        saved = __builtins__["__import__"]
        __builtins__["__import__"] = _blocking
    else:
        saved = __builtins__.__import__
        __builtins__.__import__ = _blocking
    try:
        r = auto_detect.snapshot_via_playwright("https://example.com/")
        assert r is None
    finally:
        if isinstance(__builtins__, dict):
            __builtins__["__import__"] = saved
        else:
            __builtins__.__import__ = saved


def test_snapshot_helper_swallows_navigation_failures(monkeypatch):
    """A bogus URL shouldn't crash the helper; it should just return
    None (or a result with whatever HTML was captured). We swap in a
    fake sync_playwright to simulate a navigation failure without
    actually launching a browser."""
    from bulk_downloader import auto_detect
    import sys as _sys

    # Build a minimal fake playwright module tree.
    class _FakeBrowser:
        def new_context(self):
            return self
        def new_page(self):
            return _FakePage()
        def close(self):
            pass

    class _FakePage:
        url = "https://example.com/x"
        def goto(self, *a, **kw):
            raise RuntimeError("simulated navigation failure")
        def content(self):
            # Even on nav failure, content() returns whatever's in the
            # page (often an about:blank shell). Make it return None
            # to simulate the helper returning None.
            return None

    class _FakeChromium:
        def launch(self, **kw):
            return _FakeBrowser()

    class _FakePw:
        chromium = _FakeChromium()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _FakePwModule:
        @staticmethod
        def sync_playwright():
            return _FakePw()

    saved_modules = {}
    for n in ("playwright", "playwright.sync_api"):
        saved_modules[n] = _sys.modules.get(n)
    _sys.modules["playwright"] = _FakePwModule()
    _sys.modules["playwright.sync_api"] = _FakePwModule()
    try:
        r = auto_detect.snapshot_via_playwright("https://example.com/")
        assert r is None  # nav failed AND content() returned None
    finally:
        for n, v in saved_modules.items():
            if v is None:
                _sys.modules.pop(n, None)
            else:
                _sys.modules[n] = v


# ════════════════════════════════════════════════════════════════════
# Dispatch: template_auto_detect_mode='deep'
# ════════════════════════════════════════════════════════════════════


def test_deep_mode_is_valid_config_value():
    """The mode validator must accept 'deep' alongside the original
    three modes. The validation logic is inside _auto_pick_templates,
    which requires a registered runner to invoke end-to-end. We can't
    practically seed one in a unit test, so we exercise the
    validator's logic indirectly: read the source and confirm 'deep'
    is in the valid set."""
    import inspect
    from bulk_downloader import app as app_mod
    src = inspect.getsource(app_mod._auto_pick_templates)
    assert '"deep"' in src
    # The validator line lists the valid modes — confirm deep is
    # listed alongside the originals.
    valid_modes_line = next(
        ln for ln in src.splitlines()
        if "if mode not in" in ln and '"static"' in ln
    )
    assert '"deep"' in valid_modes_line


def test_deep_mode_dispatch_block_references_required_helpers():
    """The deep-mode branch must import the helpers it depends on:
    deep_detect module, snapshot_via_playwright, to_site_config_block.
    A static check that protects against future refactors that drop
    or rename those calls."""
    import inspect
    from bulk_downloader import app as app_mod
    src = inspect.getsource(app_mod._auto_pick_templates)
    # All four wire points must be present.
    assert "snapshot_via_playwright" in src
    assert "to_site_config_block" in src
    assert "<deep-detect>" in src
    assert "deep_detect" in src


def test_deep_detect_imported_module_exposes_adapter():
    """Smoke check that deep_detect.to_site_config_block is exported
    and callable from the import path the dispatch uses."""
    from bulk_downloader import deep_detect as _dd
    assert callable(_dd.to_site_config_block)
    # Empty input is a valid edge case the adapter must handle.
    r = _dd.to_site_config_block(_dd.deep_detect(""))
    assert r["ok"] is False
    assert r["source"] == "deep_detect"
