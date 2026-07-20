"""MOD-1 C-5: the ``remote_vnc`` takeover transport (Arch B).

A DEDICATED takeover browser launched headful on its own Xvnc/KasmVNC display,
bound into the C-1 registry as ``kind="vnc"`` so the ONE shared concurrency cap
and the no-orphan sweep both count it (plan 4.3 / 6). The operator drives the
solve through the KasmVNC web client -- real X input, so unlike Arch A there is
NO CDP input pump here (that is the whole point of B; see CONFIG_GUIDE 5).

Guard-free by construction, exactly like Arch A (plan 6): this module never
touches ``session_capture``/``dom_capture`` -- it opens a plain browser on a
display, nothing more.

Three seams the rest of MOD-1 binds to:

- ``probe_endpoint(config)`` -> ``register_vnc_probe`` in ``runner_auth``. DERIVED,
  not asserted: it OBSERVES an Xvnc process AND an answering websocket port, and
  UNKNOWN (only one of the two) downgrades to unavailable. Never trusts a config
  flag (the ``bd-netns-proof`` anti-pattern).
- ``census()`` -> ``register_vnc_census`` in ``captcha_relay``. The sweep's C-1
  cross-check flags "vnc" unverified whenever ``kind="vnc"`` sessions exist but no
  census confirms them; this returns the live sids and raises if a tracked
  session's browser has died, so the sweep can reap it.
- ``launch()`` / ``teardown()`` -- lifecycle, registering/closing the C-1 channel.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
from typing import Callable, Optional

from . import takeover

_DEFAULT_WS_PORT = 8444          # KasmVNC's documented websocket_port default
_DEFAULT_DISPLAY = ":5"          # a dedicated display, off the capture display
_XVNC_COMMS = ("Xkasmvnc", "Xvnc", "Xtigervnc")  # X servers that back a vnc pane

# Live vnc takeover sessions, keyed by sid. Guarded by _LOCK. The census and the
# cap/sweep read this; it is the module's single source of truth for "what vnc
# sessions exist", and it is kept in lockstep with the C-1 registry.
_LOCK = threading.RLock()
_SESSIONS: dict = {}

# Optional pin for the browser executable (e.g. a specific chromium build). None
# uses Playwright's bundled default. Set via env BD_VNC_CHROME at import.
_executable_path: Optional[str] = os.environ.get("BD_VNC_CHROME") or None


# ── config resolution (plain .get reads -- no new declared global_config key, so
#    no CONFIG-KEY gate; a declared key can front these once the FE exposes them) ─

def resolve_ws_port(config: Optional[dict]) -> int:
    try:
        return int((config or {}).get("captcha_vnc_websocket_port", _DEFAULT_WS_PORT))
    except (TypeError, ValueError):
        return _DEFAULT_WS_PORT


def resolve_vnc_display(config: Optional[dict]) -> str:
    """The takeover display, ALWAYS as a unix-domain ``:<n>`` (optionally
    ``:<n>.<screen>``) -- never a ``host:<n>`` TCP form. MOD-1 C-7 (KASM-T8):
    unix-domain by construction, so the X protocol cannot egress over TCP. Any
    host part in the configured value is dropped, not honoured."""
    raw = str((config or {}).get("captcha_vnc_display") or _DEFAULT_DISPLAY)
    # keep only the trailing display[.screen] number: "host:5" -> "5", ":5" -> "5".
    tail = raw.rsplit(":", 1)[-1].strip()
    if tail and all(c.isdigit() or c == "." for c in tail):
        return f":{tail}"
    return _DEFAULT_DISPLAY


def viewer_url(config: Optional[dict]) -> str:
    """MOD-1 C-6: the browser-reachable URL of KasmVNC's own web client, for the
    cockpit iframe. Prefer the operator's ``novnc_url`` (the cockpit runs in the
    operator's browser, which may be remote from the host, so only they know the
    reachable address); fall back to the loopback default when unset -- correct
    when the cockpit is on the same host, and an honest 'set novnc_url' prompt in
    the FE when it is not reachable."""
    u = (config or {}).get("novnc_url")
    if u:
        return str(u)
    return f"http://127.0.0.1:{resolve_ws_port(config)}/"


# ── the DERIVED probe ────────────────────────────────────────────────────────

def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _xvnc_alive() -> bool:
    """Is an Xvnc/KasmVNC X server process running? Reads /proc rather than
    asserting -- and treats an unreadable /proc as 'cannot tell' -> False."""
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return False
    for pid in pids:
        try:
            with open(f"/proc/{pid}/comm", "r") as fh:
                if fh.read().strip() in _XVNC_COMMS:
                    return True
        except OSError:
            continue
    return False


def probe_endpoint(config: Optional[dict] = None):
    """(available, reason) for the vnc stack. OBSERVED: an Xvnc process is alive
    AND the loopback websocket port answers. Both required; only one -> UNKNOWN,
    which downgrades to unavailable (never assume vnc works). Wired into
    ``runner_auth.register_vnc_probe`` so the C-2 ladder promotes to remote_vnc
    ONLY when the stack is genuinely observable."""
    port = resolve_ws_port(config)
    proc = _xvnc_alive()
    port_ok = _port_open("127.0.0.1", port)
    if proc and port_ok:
        return (True, "")
    if not proc and not port_ok:
        return (False, "vnc backend not running")
    # exactly one is up -- indeterminate, and indeterminate fails to unavailable.
    return (False, f"vnc backend indeterminate (xvnc={proc}, ws:{port}={port_ok})")


# ── the dedicated headful-on-X browser session ───────────────────────────────

class VncTakeoverSession:
    """A thread-owned headful browser rendered on the vnc display. Playwright's
    sync API is thread-bound (see ManualLoginSession), so the browser lives on
    one dedicated thread for the session's lifetime. Minimal by design: launch,
    optionally open a url, stay alive until stopped or the browser dies. The
    operator interacts via KasmVNC, not through BD."""

    def __init__(self, config: Optional[dict], sid: str, display: str,
                 url: Optional[str] = None):
        self.config = config or {}
        self.sid = sid
        self.display = display
        self.url = url
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._error: Optional[Exception] = None
        self._alive = False
        self._thread = threading.Thread(
            target=self._run, name=f"vnc-takeover-{sid}", daemon=True)

    def start(self, timeout: float = 25.0) -> "VncTakeoverSession":
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("vnc takeover browser did not become ready in time")
        if self._error is not None:
            raise RuntimeError(f"vnc takeover launch failed: {self._error}")
        return self

    def _launch_args(self) -> list:
        # anti-automation args mirror the manual/takeover browser; a realistic
        # browser is the reason to be on X at all.
        return ["--no-sandbox", "--disable-notifications", "--disable-popup-blocking",
                "--disable-infobars", "--no-default-browser-check", "--no-first-run",
                "--disable-features=PushMessaging,Translate,AutomationControlled",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1366,800"]

    def _run(self) -> None:
        pw = None
        try:
            from contextlib import ExitStack
            from . import netns_isolation as _ni
            from . import cloak as _cloak
            with ExitStack() as stack:
                # MOD-1 C-7 (KASM-T8): confine the takeover browser's egress to the
                # operator's isolation posture, exactly like every other BD browser.
                # capture_netns FAIL-CLOSES: if isolation is required but the netns
                # cannot be created it RAISES here, so we never launch uncontained.
                # ns is None when isolation is off.
                ns = stack.enter_context(
                    _ni.capture_netns(self.config, "takeover", self.sid))
                # DISPLAY is the unix-domain form (C-7, no X-over-TCP). The launch
                # goes THROUGH cloak (cloak-parity: never a raw pw.chromium.launch),
                # which applies the netns shim when ns is set and cloak's
                # anti-automation launch either way.
                extra = {"env": {**os.environ, "DISPLAY": self.display}}
                if _executable_path:            # optional pin for the browser build
                    extra["executable_path"] = _executable_path
                browser, pw, _backend = _cloak.launch_browser(
                    headless=False, args=self._launch_args(),
                    config=self.config, netns=ns, **extra)
                try:
                    page = browser.new_page()
                    if self.url:
                        try:
                            page.goto(self.url)
                        except Exception:
                            pass  # a slow/blocked target must not fail the session
                    self._alive = True
                    self._ready.set()
                    while not self._stop.wait(0.5):
                        if not browser.is_connected():
                            break
                finally:
                    self._alive = False
                    try:
                        browser.close()
                    except Exception:
                        pass
                    if pw is not None:          # playwright backend: stop the driver
                        try:
                            pw.stop()
                        except Exception:
                            pass
        except Exception as e:  # launch failed (incl. fail-closed) -- surface via start()
            self._error = e
            self._alive = False
            self._ready.set()

    def is_alive(self) -> bool:
        return self._alive and self._thread.is_alive()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._thread.join(timeout=timeout)


# ── lifecycle: launch / teardown / census (the C-1-bound seams) ──────────────

# Injectable so unit tests can bind the registry + census logic without launching
# a real browser; production uses the real session above.
_session_factory: Callable[..., "VncTakeoverSession"] = VncTakeoverSession


def launch(config: Optional[dict], sid: str, url: Optional[str] = None):
    """Open a vnc takeover session for ``sid``: register the C-1 channel as
    ``kind="vnc"`` FIRST (so the cap/sweep can see it the instant it exists),
    then start the dedicated browser. On launch failure, unwind the channel so
    the registry never carries a phantom vnc session. Returns the session."""
    display = resolve_vnc_display(config)
    takeover.open_channel(sid, kind="vnc")
    try:
        sess = _session_factory(config, sid, display, url=url)
        sess.start()
    except Exception:
        takeover.close_channel(sid)
        with _LOCK:
            _SESSIONS.pop(sid, None)
        raise
    with _LOCK:
        _SESSIONS[sid] = sess
    return sess


def teardown(sid: str) -> None:
    """Close the vnc session for ``sid`` and its C-1 channel. Idempotent."""
    with _LOCK:
        sess = _SESSIONS.pop(sid, None)
    if sess is not None:
        try:
            sess.stop()
        except Exception:
            pass
    takeover.close_channel(sid)


def census() -> list:
    """The live vnc sids, for ``captcha_relay``'s sweep cross-check (C-1). Raises
    if a tracked session's browser has died -- an unverifiable/dead vnc surface
    must fail the sweep so it reaps, never silently pass (unknown-fails)."""
    with _LOCK:
        items = list(_SESSIONS.items())
    dead = [sid for sid, s in items if not s.is_alive()]
    if dead:
        raise RuntimeError(f"vnc session(s) not alive: {', '.join(sorted(dead))}")
    return [sid for sid, _ in items]


def _reset_for_tests() -> None:
    global _session_factory
    with _LOCK:
        _SESSIONS.clear()
    _session_factory = VncTakeoverSession


def register_all() -> None:
    """Bind the three seams into runner_auth (probe) and captcha_relay (census).
    Called once at startup. Idempotent -- re-registering just re-points the
    hooks at the same functions."""
    try:
        from . import runner_auth
        runner_auth.register_vnc_probe(probe_endpoint)
    except Exception as e:
        sys.stderr.write(f"[takeover_vnc] probe registration failed: {e}\n")
    try:
        from . import captcha_relay
        captcha_relay.register_vnc_census(census)
    except Exception as e:
        sys.stderr.write(f"[takeover_vnc] census registration failed: {e}\n")


__all__ = [
    "probe_endpoint", "census", "launch", "teardown", "register_all",
    "resolve_vnc_display", "resolve_ws_port", "viewer_url", "VncTakeoverSession",
]
