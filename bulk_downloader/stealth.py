"""v3.43.56 stealth library integration.

Wraps the optional `playwright-stealth` library so the rest of the
codebase can call one function and have it gracefully degrade when
the library isn't installed.

Three modes:
  1. `use_stealth_library=False` (default) → fall through to the
     built-in STEALTH_JS init script (constants.STEALTH_JS).
  2. `use_stealth_library=True` AND library imports OK → apply the
     library's evasions on every new page.
  3. `use_stealth_library=True` but library import FAILS → log a
     one-time warning and fall back to STEALTH_JS.

The library applies its evasions per-page (not per-context). Our
runner creates a context, then a page; we hook the page creation
to call `stealth_sync(page)` after each.

Why not enable by default: the library is an extra dependency
(~5 MB installed, depends on `pyee`), and the built-in STEALTH_JS
works fine for sites without aggressive bot detection. Users who
need it (Cloudflare-protected sites, Datadome, PerimeterX) opt in
per site via the config flag. Sites that don't need it stay on
the lighter built-in stealth.
"""
from __future__ import annotations

import sys
from typing import Any


# Module-level cache. `None` = not yet probed; `True`/`False` = result.
_LIBRARY_AVAILABLE: bool | None = None
_LIBRARY_IMPORT_ERR: str = ""
_WARNED_ABOUT_MISSING: bool = False


def is_library_available() -> bool:
    """Lazy probe of the `playwright-stealth` library. Caches the
    result so we don't pay the import cost on every page creation.
    """
    global _LIBRARY_AVAILABLE, _LIBRARY_IMPORT_ERR
    if _LIBRARY_AVAILABLE is not None:
        return _LIBRARY_AVAILABLE
    try:
        # The library's name on PyPI uses a hyphen but the import
        # name uses an underscore. Some versions expose stealth_sync
        # at module top; others wrap it in a Stealth class.
        import playwright_stealth as _ps  # noqa: F401
        _LIBRARY_AVAILABLE = True
    except Exception as e:
        _LIBRARY_AVAILABLE = False
        _LIBRARY_IMPORT_ERR = f"{type(e).__name__}: {e}"
    return _LIBRARY_AVAILABLE


def get_library_status() -> dict:
    """Return a dict suitable for the API or diagnostics view.
    Forces a probe if one hasn't happened yet.
    """
    avail = is_library_available()
    return {
        "available": avail,
        "import_error": _LIBRARY_IMPORT_ERR if not avail else "",
        # Version info, if we can extract it
        "version": _try_get_version() if avail else "",
    }


def _try_get_version() -> str:
    try:
        import playwright_stealth as _ps
        return getattr(_ps, "__version__", "unknown")
    except Exception:
        return ""


def apply_to_page(page: Any, config: dict | None = None) -> tuple[bool, str]:
    """Apply stealth evasions to a Playwright page.

    Returns (applied, detail). `applied=True` means the library
    successfully wrapped the page. `applied=False` with a detail
    string means we fell through to caller's STEALTH_JS fallback.

    Calling this when `use_stealth_library` is False in config is
    a no-op (returns False with detail="library disabled in config").

    Args:
        page: the Playwright page object (sync API)
        config: site config dict (reads `use_stealth_library`)
    """
    global _WARNED_ABOUT_MISSING
    cfg = config or {}
    if not cfg.get("use_stealth_library", False):
        return False, "library disabled in config"
    if not is_library_available():
        if not _WARNED_ABOUT_MISSING:
            sys.stderr.write(
                f"  stealth-library: requested by config but library "
                f"not installed ({_LIBRARY_IMPORT_ERR}). Falling back "
                f"to built-in STEALTH_JS. To install: pip install "
                f"playwright-stealth\n")
            _WARNED_ABOUT_MISSING = True
        return False, f"library not installed: {_LIBRARY_IMPORT_ERR}"
    try:
        # The library's public API has evolved. Newer versions
        # (>=2.0) expose a `Stealth` class with `.apply_stealth_sync`;
        # older versions expose a module-level `stealth_sync`. Probe
        # both forms.
        import playwright_stealth as _ps
        # Try the newer class-based API first
        Stealth = getattr(_ps, "Stealth", None)
        if Stealth is not None:
            stealth = Stealth()
            method = getattr(stealth, "apply_stealth_sync", None)
            if method:
                method(page)
                return True, "applied via Stealth.apply_stealth_sync"
            method = getattr(stealth, "stealth_sync", None)
            if method:
                method(page)
                return True, "applied via Stealth.stealth_sync"
        # Fall back to the module-level function (older versions)
        fn = getattr(_ps, "stealth_sync", None)
        if fn is not None:
            fn(page)
            return True, "applied via stealth_sync"
        return False, "library installed but no apply function found"
    except Exception as e:
        return False, f"apply failed: {type(e).__name__}: {e}"


def reset_cache_for_tests() -> None:
    """Test-only: clear the cached availability so subsequent calls
    re-probe. Used by unit tests that mock the import."""
    global _LIBRARY_AVAILABLE, _LIBRARY_IMPORT_ERR, _WARNED_ABOUT_MISSING
    _LIBRARY_AVAILABLE = None
    _LIBRARY_IMPORT_ERR = ""
    _WARNED_ABOUT_MISSING = False
