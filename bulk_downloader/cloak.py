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
import contextvars
import math
import os
import random
import sys
import threading
import time
from collections.abc import Callable
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


def resolve_backend(config: dict | None = None, *, use_default: bool = True) -> str | None:
    """Single source of truth for the browser backend — returns
    a backend, or no explicit choice when ``use_default=False``.

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
    if requested is None: requested = (CLOAKBROWSER if is_available() else PLAYWRIGHT) if use_default else None
    if requested is None: return None
    if requested == CLOAKBROWSER and not is_available():
        return PLAYWRIGHT
    return requested


def use_cloak(config: dict | None = None) -> bool:
    """Back-compat bool shim: ``True`` iff the resolved backend is CloakBrowser.
    Prefer :func:`resolve_backend` in new code."""
    return resolve_backend(config) == CLOAKBROWSER


# ── row 723: real-Chrome -> bundled-Chromium degradation ledger ──────────────
# `use_real_chrome` sets Playwright channel="chrome", which ONLY a Google Chrome
# install satisfies -- bundled Chromium does not. Every launch seam retries
# without the channel when that fails, and before v3.66 that retry existed only
# as a stderr line in the SERVICE log. A capability that degrades without
# telling the SITE'S run record is indistinguishable from one that worked
# (CLAUDE.md A2), so each seam records the degradation here and whoever owns a
# run record drains it. Bounded so a long-lived process cannot grow without
# limit; the oldest note is dropped, never a newer one.
_CHANNEL_FALLBACKS: list[dict] = []
_CHANNEL_FALLBACKS_MAX = 200
_CHANNEL_FALLBACK_LOCK = threading.Lock()
# Row 723 residual: a site config carries no site_id (it is not a CFG_FIELD),
# so a login flow launched with the bare config would file its note under ""
# -- a key no site's drain can match. The OWNER of the flow (the runner, the
# keeper) knows the site; it declares it around the flow with `owning_site`
# and every note keys on `ledger_site_id`. Per thread/context: the runner's
# login thread declares only its own site.
_OWNING_SITE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bd_owning_site", default="")


def ledger_site_id(config: dict | None = None) -> str:
    """The site a channel-fallback note is filed under: the config's own
    ``site_id`` (or ``sid``, the login-flow spelling), else the site declared
    by the enclosing :func:`owning_site`, else ``""`` (unowned)."""
    cfg = config or {}
    return str(cfg.get("site_id") or cfg.get("sid") or _OWNING_SITE.get() or "")


@contextlib.contextmanager
def owning_site(site_id: str):
    """Declare, for the duration of the block on THIS thread, the site that
    owns any browser launch inside it. Nested declarations shadow and restore."""
    token = _OWNING_SITE.set(str(site_id or ""))
    try:
        yield
    finally:
        _OWNING_SITE.reset(token)


def note_channel_fallback(*, site_id: str, flow: str, channel: str,
                          error: str, recovered: bool) -> dict:
    """Record ONE real-browser-channel degradation and return the note.

    ``recovered`` is True when the bundled-Chromium retry launched and False
    when it too failed -- the second is a harder failure, not an absence of
    one, so it is recorded rather than dropped."""
    note = {
        "site_id": str(site_id or ""),
        "flow": str(flow or ""),
        "channel": str(channel or ""),
        "error": str(error or "")[:200],
        "recovered": bool(recovered),
        "ts": time.time(),
    }
    with _CHANNEL_FALLBACK_LOCK:
        _CHANNEL_FALLBACKS.append(note)
        while len(_CHANNEL_FALLBACKS) > _CHANNEL_FALLBACKS_MAX:
            _CHANNEL_FALLBACKS.pop(0)
    return note


def drain_channel_fallbacks(site_id: str | None = None) -> list[dict]:
    """Remove and return the recorded degradations, oldest first. With
    ``site_id`` only that site's notes are taken; notes for other sites stay
    for their own owner. Destructive by design: a later run must not re-report
    a degradation that did not happen in it."""
    with _CHANNEL_FALLBACK_LOCK:
        if site_id is None:
            taken = list(_CHANNEL_FALLBACKS)
            _CHANNEL_FALLBACKS.clear()
            return taken
        want = str(site_id)
        taken = [n for n in _CHANNEL_FALLBACKS if n["site_id"] == want]
        _CHANNEL_FALLBACKS[:] = [n for n in _CHANNEL_FALLBACKS
                                 if n["site_id"] != want]
        return taken


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
    try:
        from .browser_sentinel import get_chromium_memory_flags as _gcmf
        for _f in _gcmf():
            if not any(a.startswith(_f.split("=")[0]) for a in args):
                args.append(_f)
    except Exception:
        pass
    extra = dict(extra)
    domain = extra.pop("domain", None)
    egress_ip = extra.pop("egress_ip", None)
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
            if domain:
                _apply_clearance_to_context(context, domain=domain, egress_ip=egress_ip, config=config)
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
    if domain:
        _apply_clearance_to_context(context, domain=domain, egress_ip=egress_ip, config=config)
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
    try:
        from .browser_sentinel import get_chromium_memory_flags as _gcmf
        for _f in _gcmf():
            if not any(a.startswith(_f.split("=")[0]) for a in args):
                args.append(_f)
    except Exception:
        pass
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
    except Exception as _exc:
        if channel_fallback and "channel" in extra:
            _ch = extra.get("channel")
            extra = {k: v for k, v in extra.items() if k != "channel"}
            try:
                ctx, pw, backend = open_persistent_context(
                    user_data_dir=user_data_dir, headless=headless, args=args,
                    user_agent=user_agent, config=config, **extra)
            except Exception as _e2:
                note_channel_fallback(
                    site_id=ledger_site_id(config),
                    flow="persistent_context", channel=str(_ch),
                    error=f"{type(_e2).__name__}: {_e2}", recovered=False)
                raise
            note_channel_fallback(
                site_id=ledger_site_id(config),
                flow="persistent_context", channel=str(_ch),
                error=f"{type(_exc).__name__}: {_exc}", recovered=True)
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


def _apply_clearance_to_context(
    context: Any,
    domain: str,
    egress_ip: str | None = None,
    config: dict | None = None,
) -> bool:
    """Inject valid cached clearance tokens/cookies into browser context.

    Falls back non-blocking on empty cache.
    """
    try:
        from .login_impl.token_manager import get_default_cache

        ip = egress_ip or "UNKNOWN"
        clearance = get_default_cache().get_clearance(ip, domain)
        if clearance:
            if isinstance(clearance, list):
                context.add_cookies(clearance)
                return True
            elif isinstance(clearance, dict) and "cookies" in clearance:
                context.add_cookies(clearance["cookies"])
                return True
    except Exception:
        pass
    return False


def save_clearance_from_context(
    context: Any,
    domain: str,
    egress_ip: str | None = None,
    config: dict | None = None,
    ttl_seconds: float = 7200.0,
) -> bool:
    """Save clearance cookies from a browser context into the lifecycle cache."""
    try:
        from .login_impl.token_manager import get_default_cache

        ip = egress_ip or "UNKNOWN"
        raw_cookies = context.cookies()
        clearance_cookies = [
            c for c in raw_cookies
            if c.get("name") in ("cf_clearance", "__cf_bm") or "clearance" in c.get("name", "").lower()
        ]
        if clearance_cookies:
            get_default_cache().set_clearance(ip, domain, clearance_cookies, ttl_seconds=ttl_seconds)
            return True
    except Exception:
        pass
    return False


def reset_cache_for_tests() -> None:
    """Reset the module-level probe + warn caches (test isolation)."""
    global _AVAILABLE, _IMPORT_ERR, _CLOAK_LPC, _WARNED_LAUNCH_FALLBACK
    _AVAILABLE = None
    _IMPORT_ERR = ""
    _CLOAK_LPC = None
    _WARNED_LAUNCH_FALLBACK = False
    with _CHANNEL_FALLBACK_LOCK:
        _CHANNEL_FALLBACKS.clear()
    try:
        from .login_impl.token_manager import reset_cache_for_tests as _reset_tokens
        _reset_tokens()
    except Exception:
        pass


def get_stealth_args() -> list[str]:
    """Default launch arguments including V8 heap bounding (Row 927)."""
    try:
        from .browser_sentinel import get_chromium_memory_flags
        extra = get_chromium_memory_flags()
    except Exception:
        extra = ["--js-flags=--max-old-space-size=512", "--disable-dev-shm-usage"]
    return [
        "--no-sandbox",
        "--disable-notifications",
        "--disable-popup-blocking",
        "--disable-infobars",
        "--no-default-browser-check",
        "--no-first-run",
        "--disable-features=PushMessaging,Translate,AutomationControlled",
        "--disable-blink-features=AutomationControlled",
    ] + extra


# ---------------------------------------------------------------------------
# Natural input trajectory simulation (Row 913)
# ---------------------------------------------------------------------------

def calculate_click_intervals(
    *,
    min_hold_ms: float = 40.0,
    max_hold_ms: float = 120.0,
    min_pre_click_ms: float = 15.0,
    max_pre_click_ms: float = 50.0,
    min_post_click_ms: float = 10.0,
    max_post_click_ms: float = 40.0,
) -> dict[str, float]:
    """Calculate realistic human timing intervals for mouse interactions.

    Returns millisecond delays for:
      - pre_click_ms: pause after moving to target before mousedown
      - hold_ms: duration between mousedown and mouseup (human click duration)
      - post_click_ms: pause after mouseup before subsequent interaction
    """
    return {
        "pre_click_ms": round(random.uniform(min_pre_click_ms, max_pre_click_ms), 2),
        "hold_ms": round(random.uniform(min_hold_ms, max_hold_ms), 2),
        "post_click_ms": round(random.uniform(min_post_click_ms, max_post_click_ms), 2),
    }


def generate_bezier_mouse_path(
    start: tuple[float, float],
    target: tuple[float, float],
    *,
    steps: int = 18,
    deviation: float = 0.2,
    pacing: str = "ease_in_out",
    wobble: float = 0.5,
) -> list[tuple[float, float]]:
    """Generate curved Bezier mouse path with variable speed pacing.

    Simulates natural human arm/hand movements:
      1. Cubic Bezier curve with perpendicular offset control points.
      2. Variable speed pacing (acceleration at start, peak velocity in middle,
         deceleration at target) via smoothstep/cosine pacing.
      3. Sub-pixel micro-jitter (wobble) modeling human motor tremor.
      4. Exact target landing.
    """
    x0, y0 = float(start[0]), float(start[1])
    x1, y1 = float(target[0]), float(target[1])

    dx = x1 - x0
    dy = y1 - y0
    distance = math.hypot(dx, dy)

    if steps <= 1 or distance < 1e-4:
        return [(x0, y0), (x1, y1)] if (x0, y0) != (x1, y1) else [(x1, y1)]

    # Tangent and perpendicular normal vectors
    tangent_x = dx / distance
    tangent_y = dy / distance
    normal_x = -tangent_y
    normal_y = tangent_x

    # Lateral deviation: side offset for control points
    dev_scale = deviation * distance
    sign = 1.0 if random.random() < 0.5 else -1.0
    offset1 = sign * dev_scale * (0.8 + 0.4 * random.random()) if deviation > 0 else 0.0
    offset2 = sign * dev_scale * (0.7 + 0.5 * random.random()) if deviation > 0 else 0.0

    # Two intermediate cubic control points at 1/3 and 2/3 along path
    c1_x = x0 + tangent_x * (distance * 0.33) + normal_x * offset1
    c1_y = y0 + tangent_y * (distance * 0.33) + normal_y * offset1

    c2_x = x0 + tangent_x * (distance * 0.67) + normal_x * offset2
    c2_y = y0 + tangent_y * (distance * 0.67) + normal_y * offset2

    points: list[tuple[float, float]] = []
    for i in range(steps + 1):
        u = i / steps
        # Variable speed pacing: smoothstep 3u^2 - 2u^3
        if pacing == "ease_in_out":
            t = 3 * (u ** 2) - 2 * (u ** 3)
        elif pacing == "cosine":
            t = (1.0 - math.cos(math.pi * u)) / 2.0
        else:
            t = u

        # Cubic Bezier evaluation
        omt = 1.0 - t
        bx = (omt ** 3) * x0 + 3 * (omt ** 2) * t * c1_x + 3 * omt * (t ** 2) * c2_x + (t ** 3) * x1
        by = (omt ** 3) * y0 + 3 * (omt ** 2) * t * c1_y + 3 * omt * (t ** 2) * c2_y + (t ** 3) * y1

        # Micro-tremor / wobble (0 at endpoints)
        if 0 < i < steps and wobble > 0:
            bx += random.uniform(-wobble, wobble)
            by += random.uniform(-wobble, wobble)

        # Pin endpoints
        if i == 0:
            points.append((x0, y0))
        elif i == steps:
            points.append((x1, y1))
        else:
            points.append((round(bx, 2), round(by, 2)))

    return points


def cloaked_mouse_move(
    page: Any,
    target_x: float,
    target_y: float,
    *,
    start_pos: tuple[float, float] | None = None,
    steps: int = 18,
    deviation: float = 0.2,
    pacing: str = "ease_in_out",
    wobble: float = 0.5,
    step_delay_s: float = 0.008,
    sleep_fn: Callable[[float], None] | None = time.sleep,
) -> list[tuple[float, float]]:
    """Dispatch a human-like mouse movement along a curved Bezier trajectory.

    Dispatches a cascade of mousemove events on page.mouse.
    If sleep_fn is None or step_delay_s <= 0, executes without blocking (zero test suite overhead).
    """
    mouse = getattr(page, "mouse", page)
    if start_pos is None:
        current_x = getattr(mouse, "current_x", 0.0)
        current_y = getattr(mouse, "current_y", 0.0)
        start_pos = (float(current_x), float(current_y))

    path = generate_bezier_mouse_path(
        start=start_pos,
        target=(float(target_x), float(target_y)),
        steps=steps,
        deviation=deviation,
        pacing=pacing,
        wobble=wobble,
    )

    for pt in path:
        mouse.move(pt[0], pt[1])
        if sleep_fn is not None and step_delay_s > 0:
            jittered = step_delay_s * random.uniform(0.7, 1.3)
            sleep_fn(jittered)

    return path


def cloaked_mouse_click(
    page: Any,
    target_x: float | None = None,
    target_y: float | None = None,
    *,
    selector: str | None = None,
    start_pos: tuple[float, float] | None = None,
    button: str = "left",
    steps: int = 18,
    deviation: float = 0.2,
    step_delay_s: float = 0.008,
    click_intervals: dict[str, float] | None = None,
    sleep_fn: Callable[[float], None] | None = time.sleep,
) -> dict[str, Any]:
    """Execute realistic human mouse click on page or targeted element.

    Follows human interaction lifecycle:
      1. Curved Bezier movement to target
      2. Pre-click pause (orientation delay)
      3. Mousedown event
      4. Realistic hold delay (40-120ms)
      5. Mouseup event
      6. Post-click pause

    Zero test suite overhead when sleep_fn is None or delays are zero.
    """
    mouse = getattr(page, "mouse", page)

    # If selector provided, resolve target coordinates from bounding box
    if selector is not None:
        loc = page.locator(selector).first
        box = loc.bounding_box() if loc else None
        if box is None:
            raise ValueError(f"Target selector {selector!r} not found or not visible")
        target_x = box["x"] + box["width"] / 2.0
        target_y = box["y"] + box["height"] / 2.0

    if target_x is None or target_y is None:
        raise ValueError("Must provide either (target_x, target_y) or selector")

    intervals = click_intervals or calculate_click_intervals()

    # Step 1: Trajectory movement
    path = cloaked_mouse_move(
        page=page,
        target_x=target_x,
        target_y=target_y,
        start_pos=start_pos,
        steps=steps,
        deviation=deviation,
        step_delay_s=step_delay_s,
        sleep_fn=sleep_fn,
    )

    # Step 2: Pre-click pause
    pre_click_s = intervals.get("pre_click_ms", 30.0) / 1000.0
    if sleep_fn is not None and pre_click_s > 0:
        sleep_fn(pre_click_s)

    # Step 3: Mousedown
    if hasattr(mouse, "down"):
        mouse.down(button=button)

    # Step 4: Hold delay
    hold_s = intervals.get("hold_ms", 75.0) / 1000.0
    if sleep_fn is not None and hold_s > 0:
        sleep_fn(hold_s)

    # Step 5: Mouseup
    if hasattr(mouse, "up"):
        mouse.up(button=button)

    # Step 6: Post-click pause
    post_click_s = intervals.get("post_click_ms", 20.0) / 1000.0
    if sleep_fn is not None and post_click_s > 0:
        sleep_fn(post_click_s)

    return {
        "success": True,
        "target": (target_x, target_y),
        "steps": len(path),
        "intervals": intervals,
    }


# Canonical aliases
generate_bezier_trajectory = generate_bezier_mouse_path
bezier_mouse_move = cloaked_mouse_move
bezier_mouse_click = cloaked_mouse_click


def discover_frames(page: Any) -> dict[str, Any]:
    """Discover nested frame hierarchy and media elements on a Playwright page.
    Delegates to :func:`bulk_downloader.frame_hierarchy.discover_frame_hierarchy`."""
    from .frame_hierarchy import discover_frame_hierarchy
    return discover_frame_hierarchy(page)


