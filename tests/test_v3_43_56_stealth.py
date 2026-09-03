"""v3.43.56 tests: optional playwright-stealth library integration.

The stealth library is an opt-in dependency. When installed AND the
site's config has `use_stealth_library=True`, the runner applies
the library's evasions on each new page on top of the built-in
STEALTH_JS. When not installed or not opted in, the existing
STEALTH_JS path runs unchanged.

These tests verify the gating logic, fail-open behavior, and the
wiring of the helper into the page-creation sites. The actual
library application can't be unit-tested without spinning up a
real Playwright context — that's an integration concern.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parent.parent

def _APP_SRC():
    """app.py + extracted app_*.py modules (Phase 4 thin-core-shell; DECOMP-R2a kernels)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)



def _login_impl_src():
    """Concatenated source of the decomposed login_impl/ package. login.py is now an
    ADD-only re-export shim (DECOMP-LEAF cut 3); the implementation lives in
    login_impl/*.py, so structure/path tests read the package, not the shim."""
    from pathlib import Path as _P
    import bulk_downloader.login_impl as _pkg
    _d = _P(_pkg.__file__).parent
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_d.glob("*.py")))


# ── Helper module ────────────────────────────────────────────────


def test_stealth_module_imports():
    """The stealth module loads even when the library isn't installed."""
    from bulk_downloader import stealth as _stealth
    assert hasattr(_stealth, "apply_to_page")
    assert hasattr(_stealth, "is_library_available")
    assert hasattr(_stealth, "get_library_status")
    assert hasattr(_stealth, "reset_cache_for_tests")


def test_apply_to_page_no_op_when_disabled():
    """When use_stealth_library is False (default), apply_to_page
    returns (False, detail) without touching the page."""
    from bulk_downloader import stealth as _stealth
    page = mock.MagicMock()
    # No config passed — default False
    applied, detail = _stealth.apply_to_page(page)
    assert applied is False
    assert "disabled" in detail.lower()
    # Page methods not called
    page.evaluate.assert_not_called()


def test_apply_to_page_no_op_with_explicit_false():
    """Explicit `use_stealth_library: False` is also a no-op."""
    from bulk_downloader import stealth as _stealth
    page = mock.MagicMock()
    applied, detail = _stealth.apply_to_page(
        page, {"use_stealth_library": False})
    assert applied is False


def test_apply_to_page_fail_open_when_library_missing():
    """When config requests the library but it's not installed,
    return (False, detail) AND log a one-time warning, NOT raise."""
    from bulk_downloader import stealth as _stealth
    _stealth.reset_cache_for_tests()
    # Patch the import to fail
    import sys
    saved = sys.modules.pop("playwright_stealth", None)
    try:
        with mock.patch.dict(sys.modules, {"playwright_stealth": None}):
            with mock.patch("builtins.__import__",
                              side_effect=lambda n, *a, **k: (
                                _real_import(n, *a, **k)
                                if n != "playwright_stealth"
                                else (_ for _ in ()).throw(
                                    ImportError("simulated"))
                              )):
                page = mock.MagicMock()
                applied, detail = _stealth.apply_to_page(
                    page, {"use_stealth_library": True})
                assert applied is False
                assert "not installed" in detail.lower() or \
                       "import" in detail.lower() or \
                       "library" in detail.lower()
    finally:
        if saved is not None:
            sys.modules["playwright_stealth"] = saved
        _stealth.reset_cache_for_tests()


_real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__


def test_get_library_status_shape():
    """get_library_status returns a dict with the expected keys."""
    from bulk_downloader import stealth as _stealth
    status = _stealth.get_library_status()
    assert "available" in status
    assert isinstance(status["available"], bool)
    assert "import_error" in status
    assert "version" in status


def test_is_library_available_cached():
    """is_library_available() caches its result — calling N times
    only probes once."""
    from bulk_downloader import stealth as _stealth
    _stealth.reset_cache_for_tests()
    # First call sets the cache
    r1 = _stealth.is_library_available()
    # Subsequent calls return the same value
    for _ in range(5):
        assert _stealth.is_library_available() == r1


# ── Config default + field whitelist ──────────────────────────────


def test_default_use_stealth_library_is_false():
    """Defaults must NOT auto-enable the library — it's opt-in."""
    src = _APP_SRC()
    pos = src.find("DEFAULTS=")
    assert pos > 0
    block = src[pos:pos + 8000]
    lib_pos = block.find('"use_stealth_library"')
    assert lib_pos > 0
    # The line/region should set False
    line = block[lib_pos:lib_pos + 200]
    assert "False" in line


def test_use_stealth_library_in_field_whitelist():
    """The save handler must include use_stealth_library in the
    field list, otherwise toggling the UI checkbox has no effect."""
    src = _APP_SRC()
    # The CFG_FIELDS list is built once near the top
    assert '"use_stealth_library"' in src


# ── Runner wiring ────────────────────────────────────────────────


def test_runner_has_apply_stealth_library_method():
    from bulk_downloader.runner import SiteRunner
    assert hasattr(SiteRunner, "_apply_stealth_library_to_page")


def test_runner_calls_stealth_library_on_new_pages():
    """The two main page-creation sites in runner.py must call
    _apply_stealth_library_to_page right after ctx.new_page()."""
    _pd = _REPO_ROOT / "bulk_downloader"
    src = "\n".join(q.read_text(encoding="utf-8")
                    for q in [_pd / "runner.py"] + sorted(_pd.glob("runner_*.py")))
    # Both call sites should appear near a ctx.new_page() or ctx.pages
    count = src.count("_apply_stealth_library_to_page")
    assert count >= 2, (
        f"expected ≥2 call sites for _apply_stealth_library_to_page, "
        f"got {count}")


def test_login_applies_stealth_library_in_do_login():
    """do_login's page-creation path applies the library too."""
    src = _login_impl_src()
    # The do_login function should call apply_to_page or import stealth
    # in proximity to ctx.new_page()
    pos = src.find("page=ctx.new_page()")
    if pos == -1:
        pos = src.find("page = ctx.new_page()")
    assert pos > 0
    body = src[pos:pos + 600]
    assert "stealth" in body.lower()


def test_manual_login_applies_stealth_library():
    """The manual takeover path (open_manual_login_browser) also
    applies the library on its page."""
    src = _login_impl_src()
    pos = src.find("ctx.pages[0] if (self._manual_profile_dir")
    assert pos > 0
    body = src[pos:pos + 600]
    assert "stealth" in body.lower()


def test_verify_replay_applies_stealth_library():
    """The wizard's headless verify path applies the library when
    enabled, so the verify happens under the same anti-detection
    profile as the worker will use."""
    src = _login_impl_src()
    pos = src.find("def verify_login_replay")
    assert pos > 0
    body = src[pos:pos + 5000]
    # Apply call within the verify body
    assert "stealth" in body.lower() and "apply_to_page" in body


# ── UI wiring ────────────────────────────────────────────────────




# ── Optional dep declared ────────────────────────────────────────


def test_playwright_stealth_in_requirements():
    """Library is declared in requirements.txt (merged from the optional
    manifest 2026-09-03) so users install it via pip install -r."""
    src = (_REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "playwright-stealth" in src or "playwright_stealth" in src
