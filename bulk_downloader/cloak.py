"""Canonical CloakBrowser integration (v3.66.138).

CloakBrowser is the canonical / default browser backend for automated,
persistent-profile launches. This module wraps the optional
``cloakbrowser`` package the same way ``stealth.py`` wraps
``playwright-stealth``: a lazy, cached probe so importing this module
NEVER fails when cloakbrowser isn't installed, plus a single decision
function so every call site agrees on which backend to use.

Policy
------
Prefer CloakBrowser. Fall back to vanilla Playwright only when:
  (a) ``cloakbrowser`` isn't importable, OR
  (b) it's explicitly disabled via config / env, OR
  (c) a cloak launch raises at runtime (e.g. the stealth Chromium
      binary can't be fetched on a network-restricted host).

Because the import is probed lazily here, a bare
``from cloakbrowser import ...`` at the top of session_keeper (which
crashed module import — and therefore ``app.py`` — on hosts without
cloakbrowser, regardless of the disable flag) is no longer needed.
The disable flag is now honoured *before* anything tries to import or
launch cloak.
"""
from __future__ import annotations

import contextlib
import os
import sys
import threading
from typing import Any

# Module-level cache. ``None`` = not yet probed; ``True``/``False`` = result.
_AVAILABLE: bool | None = None
_IMPORT_ERR: str = ""
_CLOAK_LPC: Any = None  # cached cloakbrowser.launch_persistent_context
_WARNED_LAUNCH_FALLBACK: bool = False


# ---------------------------------------------------------------------------
# F5 Phase 2 (v3.66.701): browser-in-netns launch routing.
#
# Playwright spawns Chromium itself, so a caller has no argv to wrap with
# ``ip netns exec``. The 699 shim exploits Playwright's ``executable_path``
# seam. Wiring it differs per backend, and the difference was established by a
# LIVE probe (a real kernel + the real stealth Chromium), not by reading:
#
#   playwright backend -> ``executable_path=<shim>`` is a real launch param.
#   cloak backend      -> ``executable_path`` CANNOT be passed: cloakbrowser
#                         calls ``pw.chromium.launch(executable_path=binary,
#                         **kwargs)``, so a kwarg of the same name raises
#                         ``TypeError: got multiple values``. Its supported seam
#                         is the ``CLOAKBROWSER_BINARY_PATH`` local-binary
#                         override, which is read IN-PROCESS at launch time.
#
# Either way the REAL browser binary is handed to the shim via
# ``NETNS_BROWSER_BIN`` in the per-launch ``env`` -- the shim WRAPS cloak's
# stealth Chromium, never replaces it (STATE's open composition question).
#
# THREADS: BD's workers are threads sharing one ``os.environ``, so the override
# window is serialized by ``_LAUNCH_LOCK`` and always restored. The lock is only
# taken once isolation has actually been used (``_ISOLATION_ARMED``), so the
# default path -- every site that does not opt in -- pays nothing and keeps its
# existing parallel-launch behaviour exactly.
_LAUNCH_LOCK = threading.Lock()
_ISOLATION_ARMED: bool = False
_SHIM_DIR_ENV = "netns_shim"


def _real_browser_binary(backend: str) -> str:
    """Path of the browser the shim must exec INSIDE the namespace -- resolved
    from the authoritative source for each backend (never re-derived)."""
    if backend == CLOAKBROWSER:
        from cloakbrowser.download import ensure_binary
        return str(ensure_binary())
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        return str(pw.chromium.executable_path)
    finally:
        pw.stop()


def _netns_shim_path() -> str:
    from . import netns_isolation as _ni
    import tempfile
    d = os.path.join(tempfile.gettempdir(), _SHIM_DIR_ENV)
    return _ni.write_browser_shim(d)


def _netns_launch_plan(netns: str | None, backend: str):
    """Return ``(shim_path, env)`` for an isolated launch, or ``(None, None)``
    when ``netns`` is falsy -- in which case every caller's launch is byte-
    identical to the pre-701 path (no shim, no env, no override)."""
    if not netns:
        return None, None
    from . import netns_isolation as _ni
    real = _real_browser_binary(backend)
    shim = _netns_shim_path()
    env = {**os.environ, **_ni.browser_launch_env(netns, real)}
    return shim, env


@contextlib.contextmanager
def _cloak_binary_override(shim: str | None):
    """Point cloakbrowser at the shim for the duration of ONE launch.

    Serialized + always restored: a concurrent non-isolated worker thread must
    never inherit the override (live-probed: the shim with no ``NETNS_NS``
    used to kill the browser; it now passes through, but the override is still
    scoped so the default path stays untouched)."""
    global _ISOLATION_ARMED
    if not shim:
        if _ISOLATION_ARMED:
            # Isolation is live in this process: serialize against the override
            # window so a non-isolated launch cannot observe it mid-flight.
            with _LAUNCH_LOCK:
                yield
        else:
            yield
        return
    _ISOLATION_ARMED = True
    with _LAUNCH_LOCK:
        prev = os.environ.get("CLOAKBROWSER_BINARY_PATH")
        os.environ["CLOAKBROWSER_BINARY_PATH"] = shim
        try:
            yield
        finally:
            if prev is None:
                os.environ.pop("CLOAKBROWSER_BINARY_PATH", None)
            else:
                os.environ["CLOAKBROWSER_BINARY_PATH"] = prev


class CloakLaunchError(RuntimeError):
    """Raised when a persistent-context launch fails for a clearly-attributable
    reason -- currently a headed browser needing a display that isn't available.
    The triggering exception is always chained as ``__cause__`` so the original
    error is never hidden. This exists purely to make the failure legible; it
    does NOT change which backend is chosen or how launches are attempted.
    """


def is_available() -> bool:
    """Lazy probe of the ``cloakbrowser`` package. Caches the result so
    we don't pay the import cost on every launch."""
    global _AVAILABLE, _IMPORT_ERR, _CLOAK_LPC
    if _AVAILABLE is None:
        try:
            from cloakbrowser import launch_persistent_context as _lpc
            _CLOAK_LPC = _lpc
            _AVAILABLE = True
        except Exception as e:  # ImportError, or any transitive failure
            _AVAILABLE = False
            _IMPORT_ERR = f"{type(e).__name__}: {e}"
    return _AVAILABLE


def _version() -> str:
    try:
        import importlib.metadata as _m
        return _m.version("cloakbrowser")
    except Exception:
        return "unknown"


def get_status() -> dict:
    """Diagnostic snapshot for health checks / status endpoints."""
    avail = is_available()
    return {
        "available": avail,
        "version": _version() if avail else "",
        "import_error": _IMPORT_ERR,
    }


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "on"}
    return bool(v)


# Canonical backend names — the app exposes exactly these two as the
# user-facing choice (Settings → ``browser_backend`` / ``BD_BROWSER_BACKEND``).
CLOAKBROWSER = "cloakbrowser"
PLAYWRIGHT = "playwright"


def _coerce_backend(v: Any) -> str | None:
    """Map a config/env value to a canonical backend name, or ``None`` when the
    value doesn't name one. Accepts the two canonical strings, a few aliases,
    and legacy booleans (the old ``use_cloak`` flag: ``True`` → cloakbrowser)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return CLOAKBROWSER if v else PLAYWRIGHT
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"cloakbrowser", "cloak", "cloak_browser"}:
            return CLOAKBROWSER
        if s in {"playwright", "pw", "vanilla", "plain"}:
            return PLAYWRIGHT
        if s in {"1", "true", "yes", "on"}:      # legacy bool-as-string
            return CLOAKBROWSER
        if s in {"0", "false", "no", "off"}:
            return PLAYWRIGHT
    return None


def _first_backend(getter, keys) -> str | None:
    for k in keys:
        b = _coerce_backend(getter(k))
        if b is not None:
            return b
    return None


# Config / env keys, most-specific first. ``browser_backend`` is the canonical
# key; the two legacy booleans are still honoured for back-compat.
_CFG_KEYS = ("browser_backend", "use_cloak", "session_keeper_use_cloakbrowser")
_ENV_KEYS = ("BD_BROWSER_BACKEND", "BD_USE_CLOAK",
             "BD_SESSION_KEEPER_USE_CLOAKBROWSER")


def resolve_backend(config: dict | None = None) -> str:
    """Single source of truth for the browser backend — returns
    ``"cloakbrowser"`` or ``"playwright"``.

    Precedence (most specific first):
      1. per-call ``config`` (``browser_backend``, or legacy bool keys)
      2. env (``BD_BROWSER_BACKEND``, or legacy ``BD_*`` bools)
      3. global Settings (``browser_backend``, or legacy bool keys)
      4. default: ``cloakbrowser`` when importable, else ``playwright``

    A request for ``cloakbrowser`` is downgraded to ``playwright`` when the
    package isn't importable, so callers always get a usable backend.
    """
    requested = None
    if isinstance(config, dict):
        requested = _first_backend(lambda k: config.get(k, None), _CFG_KEYS)
    if requested is None:
        requested = _first_backend(lambda k: os.environ.get(k, None), _ENV_KEYS)
    if requested is None:
        try:
            from . import global_config as _gc
            requested = _first_backend(lambda k: _gc.get(k, None), _CFG_KEYS)
        except Exception:
            requested = None
    if requested is None:
        requested = CLOAKBROWSER if is_available() else PLAYWRIGHT
    if requested == CLOAKBROWSER and not is_available():
        return PLAYWRIGHT
    return requested


def use_cloak(config: dict | None = None) -> bool:
    """Back-compat bool shim: ``True`` iff the resolved backend is CloakBrowser.
    Prefer :func:`resolve_backend` in new code."""
    return resolve_backend(config) == CLOAKBROWSER


def log_choice(flow: str, backend: str, detail: str = "") -> None:
    """Emit one consistent line naming the backend a flow launched with — e.g.
    ``  [browser] worker[site/0]: cloakbrowser``. Every browser flow calls this
    so the logs show, uniformly, which backend each path used."""
    extra = f" — {detail}" if detail else ""
    sys.stderr.write(f"  [browser] {flow}: {backend}{extra}\n")


# ── persistent-context launch error clarity ──────────────────────────────────
# Markers identifying a launch failure attributable to a missing display (a
# headed Chromium needs an X server / Xvfb / noVNC). Matched case-insensitively
# against the failure's "Type: message" rendering. Used ONLY to make the error
# explicit -- never to change launch behaviour.
# DELIBERATELY display-SPECIFIC: generic crash strings ("browser closed
# unexpectedly", "target ... has been closed") are NOT markers, because with
# DISPLAY set they would misclassify ordinary headed crashes (OOM, bad flag,
# profile lock) as display problems. The no-DISPLAY case is already covered
# deterministically in _clarify_launch_error, so these markers only need to
# catch the DISPLAY-set-but-broken case.
_DISPLAY_ERROR_MARKERS = (
    "missing x server",
    "cannot open display",
    "no display",
    "$display",
    "x server",
    "xvfb",
)


def _looks_like_no_display(exc: BaseException) -> bool:
    """Heuristic: does this launch failure read like a missing-display error?"""
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(m in msg for m in _DISPLAY_ERROR_MARKERS)


def _clarify_launch_error(exc: BaseException, *, headless: bool):
    """Return a :class:`CloakLaunchError` with an explicit, actionable message
    when a launch failure is attributable to a headed browser
    needing an unavailable display; otherwise return ``None`` so the caller
    re-raises the original exception unchanged.

    A headed launch (``headless=False``) on a POSIX host cannot succeed without
    a display, so an empty ``DISPLAY`` is itself a sufficient, deterministic
    signal; the error text is a secondary signal for the display-set-but-broken
    case. Windows headed launches need no X server and are never reclassified.
    The original exception is preserved by the caller via ``raise ... from exc``.
    """
    if headless or os.name == "nt":
        return None
    if not os.environ.get("DISPLAY") or _looks_like_no_display(exc):
        return CloakLaunchError(
            "Headed browser launch requires a display; start Xvfb/noVNC or run "
            "headless. Cloak browser launch could not start. "
            f"(root cause: {type(exc).__name__}: {str(exc)[:200]})"
        )
    return None


def open_persistent_context(
    *,
    user_data_dir,
    headless: bool = True,
    args: list[str] | None = None,
    user_agent: str | None = None,
    config: dict | None = None,
    netns: str | None = None,
    **extra: Any,
):
    """Open a persistent browser context using the canonical backend.

    Returns ``(context, pw, backend)``:
      - backend ``"cloak"``      → ``pw`` is ``None``; the context owns
        its own Playwright instance and ``context.close()`` stops it.
      - backend ``"playwright"`` → ``pw`` is the started
        ``sync_playwright`` instance; the caller must ``pw.stop()`` it
        (after ``context.close()``).

    ``extra`` kwargs (viewport, proxy, accept_downloads, ...) pass
    through to whichever backend is chosen. ``channel`` is stripped on
    the cloak path because CloakBrowser supplies its own stealth
    Chromium via ``executable_path`` (the two are mutually exclusive).

    If cloak is preferred but its launch raises (e.g. the stealth
    Chromium binary is unavailable on a network-restricted host), this
    logs once and transparently falls back to Playwright so the caller
    still gets a working browser.
    """
    global _WARNED_LAUNCH_FALLBACK
    args = list(args or [])
    backend = resolve_backend(config)
    shim, ns_env = _netns_launch_plan(netns, backend)

    if backend == CLOAKBROWSER:
        try:
            cloak_kwargs = dict(extra)
            # CloakBrowser owns its binary; `channel` is incompatible.
            cloak_kwargs.pop("channel", None)
            if ns_env is not None:
                cloak_kwargs["env"] = ns_env
            with _cloak_binary_override(shim):
                context = _CLOAK_LPC(
                    user_data_dir=str(user_data_dir),
                    headless=headless,
                    args=args,
                    user_agent=user_agent,
                    **cloak_kwargs,
                )
            # cloakbrowser patches context.close() to also stop its own
            # Playwright, so we return pw=None and let the caller close
            # the context normally.
            return context, None, CLOAKBROWSER
        except Exception as e:
            if not _WARNED_LAUNCH_FALLBACK:
                _WARNED_LAUNCH_FALLBACK = True
                sys.stderr.write(
                    f"  cloak: CloakBrowser launch failed "
                    f"({type(e).__name__}: {str(e)[:120]}); "
                    f"falling back to Playwright for this and future launches\n")
            # fall through to the Playwright path

    from playwright.sync_api import sync_playwright
    # 701: if the cloak launch above FAILED while a namespace was requested, the
    # fallback must not silently drop the isolation -- re-plan for this backend
    # (here ``executable_path`` is a real Playwright param, no TypeError).
    pw_extra = dict(extra)
    if netns:
        pw_shim, pw_env = _netns_launch_plan(netns, PLAYWRIGHT)
        pw_extra["executable_path"] = pw_shim
        _caller_env = pw_extra.get("env")   # preserve caller env (e.g. DISPLAY)
        pw_extra["env"] = {**pw_env, **_caller_env} if _caller_env else pw_env
    pw = sync_playwright().start()
    try:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=headless,
            args=args,
            user_agent=user_agent,
            **pw_extra,
        )
    except Exception as e:
        # Finding B: launch_persistent_context failed AFTER
        # sync_playwright().start() succeeded, so ``pw`` owns a live node/driver
        # subprocess that nothing will ever close (the caller only gets ``pw``
        # on success). Stop it here to avoid leaking that process on every failed
        # launch. The stop is guarded so it can never mask the original launch
        # error, and the error raised is byte-for-byte what it was before.
        try:
            pw.stop()
        except Exception:
            pass
        # Surface a missing-display headed launch as an explicit, actionable
        # error (chaining the original cause); any other failure re-raises
        # unchanged so existing behaviour is preserved exactly.
        clarified = _clarify_launch_error(e, headless=headless)
        if clarified is None:
            raise
        raise clarified from e
    return context, pw, PLAYWRIGHT


def launch_browser(
    *,
    headless: bool = True,
    args: list[str] | None = None,
    config: dict | None = None,
    netns: str | None = None,
    **extra: Any,
):
    """Launch a NON-persistent browser using the canonical backend.

    Returns ``(browser, pw, backend)`` with the same lifecycle contract as
    :func:`open_persistent_context`:
      - ``"cloakbrowser"`` → ``pw`` is ``None``; ``browser.close()`` stops the
        backend's own Playwright.
      - ``"playwright"``   → ``pw`` is the started ``sync_playwright``; the
        caller must ``pw.stop()`` after ``browser.close()``.

    The caller creates its own contexts via ``browser.new_context(...)`` (set
    ``user_agent`` / ``viewport`` there — neither ``.launch()`` accepts them).
    ``channel`` is stripped on the cloak path (CloakBrowser supplies its own
    Chromium). Falls back to Playwright if a cloak launch raises.
    """
    global _WARNED_LAUNCH_FALLBACK
    args = list(args or [])
    backend = resolve_backend(config)
    shim, ns_env = _netns_launch_plan(netns, backend)
    _no_fallback = bool(extra.pop("_no_fallback", False))

    if backend == CLOAKBROWSER:
        try:
            from cloakbrowser import launch as _cloak_launch
            cloak_kwargs = dict(extra)
            cloak_kwargs.pop("channel", None)      # incompatible w/ cloak binary
            cloak_kwargs.pop("user_agent", None)   # set at new_context() instead
            if ns_env is not None:
                cloak_kwargs["env"] = ns_env
            with _cloak_binary_override(shim):
                browser = _cloak_launch(headless=headless, args=args, **cloak_kwargs)
            return browser, None, CLOAKBROWSER
        except Exception as e:
            if _no_fallback:
                raise
            if not _WARNED_LAUNCH_FALLBACK:
                _WARNED_LAUNCH_FALLBACK = True
                sys.stderr.write(
                    f"  cloak: CloakBrowser launch failed "
                    f"({type(e).__name__}: {str(e)[:120]}); "
                    f"falling back to Playwright for this and future launches\n")
            # fall through to the Playwright path

    from playwright.sync_api import sync_playwright
    launch_kwargs = dict(extra)
    launch_kwargs.pop("user_agent", None)          # .launch() takes no user_agent
    # 701: see open_persistent_context -- a fallback must never drop isolation.
    if netns:
        pw_shim, pw_env = _netns_launch_plan(netns, PLAYWRIGHT)
        launch_kwargs["executable_path"] = pw_shim
        # Preserve caller-supplied env keys (e.g. DISPLAY for a headful-on-X
        # takeover) on top of the netns env; NETNS_* still win since they are only
        # in pw_env. No-op for the existing callers, which pass no env with netns.
        _caller_env = launch_kwargs.get("env")
        launch_kwargs["env"] = {**pw_env, **_caller_env} if _caller_env else pw_env
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=headless, args=args, **launch_kwargs)
    except Exception as e:
        # Finding B (twin): launch() failed AFTER sync_playwright().start(), so
        # ``pw`` owns a live driver subprocess the caller never receives. Stop it
        # (guarded) before surfacing the error.
        try:
            pw.stop()
        except Exception:
            pass
        # v3.66.171: parity with open_persistent_context — surface a missing-display
        # headed launch as an explicit, actionable error (chaining the original
        # cause); any other failure re-raises unchanged so behaviour is preserved.
        clarified = _clarify_launch_error(e, headless=headless)
        if clarified is None:
            raise
        raise clarified from e
    return browser, pw, PLAYWRIGHT


@contextlib.contextmanager
def persistent_context(
    *,
    user_data_dir,
    headless: bool = True,
    args: list[str] | None = None,
    user_agent: str | None = None,
    config: dict | None = None,
    channel_fallback: bool = True,
    **extra: Any,
):
    """Context-manager wrapper around :func:`open_persistent_context` for
    short-lived persistent sessions (e.g. login verification, capture).

    Yields ``(context, backend)``. On exit it closes the context and, on the
    Playwright backend, stops the owned ``sync_playwright`` instance — so the
    caller never touches ``pw`` directly. When ``channel_fallback`` is set and
    a ``channel`` was supplied, a launch failure is retried once without the
    channel (bundled Chromium), matching the runner/login launch behaviour.
    """
    try:
        ctx, pw, backend = open_persistent_context(
            user_data_dir=user_data_dir, headless=headless, args=args,
            user_agent=user_agent, config=config, **extra)
    except Exception:
        if channel_fallback and "channel" in extra:
            extra = {k: v for k, v in extra.items() if k != "channel"}
            ctx, pw, backend = open_persistent_context(
                user_data_dir=user_data_dir, headless=headless, args=args,
                user_agent=user_agent, config=config, **extra)
        else:
            raise
    try:
        yield ctx, backend
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass


@contextlib.contextmanager
def cloaked_page(
    *,
    headless: bool = True,
    user_agent: str | None = None,
    args: list[str] | None = None,
    config: dict | None = None,
    viewport: dict | None = None,
    context_options: dict | None = None,
    **extra: Any,
):
    """Yield a Playwright ``Page`` from the canonical backend (CloakBrowser when
    resolved, else vanilla Playwright). This is the ephemeral (non-persistent)
    counterpart to :func:`persistent_context` — it is the single entry point any
    short-lived "fetch/render a target site" path should use so the operator's
    TEST surfaces render through the SAME stealth backend as real captures.

    Handles ``new_context`` + ``new_page`` and full teardown (context → browser
    → ``pw.stop()`` on the Playwright fallback). On the CloakBrowser path the
    backend supplies its OWN fingerprint, so ``user_agent`` is applied ONLY on
    the Playwright fallback (forcing a vanilla UA on the cloak path would partly
    defeat the stealth). ``context_options`` is forwarded only to
    ``browser.new_context`` (for example, to block service workers on a guarded
    verifier page). ``launch_browser`` already falls open to Playwright if a
    cloak launch raises, so callers get a working page either way.
    """
    browser, pw, backend = launch_browser(
        headless=headless, args=args, config=config, **extra)
    context = None
    try:
        ctx_kwargs: dict = dict(context_options or {})
        if viewport is not None:
            ctx_kwargs["viewport"] = viewport
        if user_agent and backend != CLOAKBROWSER:
            ctx_kwargs["user_agent"] = user_agent
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        yield page
    finally:
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass


def reset_cache_for_tests() -> None:
    """Reset the module-level probe + warn caches (test isolation)."""
    global _AVAILABLE, _IMPORT_ERR, _CLOAK_LPC, _WARNED_LAUNCH_FALLBACK
    _AVAILABLE = None
    _IMPORT_ERR = ""
    _CLOAK_LPC = None
    _WARNED_LAUNCH_FALLBACK = False
