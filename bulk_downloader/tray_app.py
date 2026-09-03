"""System tray application for BulkDownloader (Phase 110, Block O).

A lightweight desktop integration that lives in the system tray
(macOS menu bar / Windows tray / Linux indicator). Provides:

  • At-a-glance status: how many jobs running, queued, failed
  • Quick actions: pause-all / resume-all / open-dashboard
  • Notification badge when a job fails or completes
  • Auto-start on login (operator-toggleable)

Implementation:
  • Uses `pystray` (cross-platform tray library) + `Pillow` for icons.
  • Both are optional deps. If missing, this module reports unavailable
    and the tray is silently skipped — BD's web UI still works.
  • Polls /api/status_all every 5 seconds from a background thread.
  • One-process integration: runs inside the main BD process. The tray
    is a daemon thread; killing BD kills the tray.

Not meant to replace the web UI — meant to be the always-on indicator
the operator glances at while doing other work.
"""
from __future__ import annotations

import sys
import threading
import time
import webbrowser
from typing import Optional


_HAS_PYSTRAY = False
_HAS_PIL = False
_UNAVAILABLE_REASON: Optional[str] = None


def _record_unavailable(dep: str, exc: BaseException) -> None:
    """Remember WHY an optional dependency is unusable, in its own words.

    A collapsed "unavailable" cannot be acted on: "not installed" and
    "installed but there is no X display" lead to opposite remedies.
    """
    global _UNAVAILABLE_REASON
    detail = f"{dep}: {type(exc).__name__}: {exc}"
    _UNAVAILABLE_REASON = (
        detail if _UNAVAILABLE_REASON is None
        else f"{_UNAVAILABLE_REASON}; {detail}")


# AN OPTIONAL DEPENDENCY CAN BE PRESENT AND STILL UNUSABLE, and it does not
# have the courtesy to say so with an ImportError.
#
# `pystray` picks and initialises a platform backend AT IMPORT TIME. On a
# headless Linux box it reaches Xlib, which raises
# `Xlib.error.DisplayNameError: Bad display name ""` with DISPLAY unset, or
# `Xlib.error.DisplayConnectionError: Can't connect to display ":0"` with
# DISPLAY set but no server. Both are Exception subclasses and NEITHER is an
# ImportError, so the original `except ImportError` let them escape and
# `import bulk_downloader.tray_app` died -- breaking the module's own
# documented promise above that a missing/unusable tray is "silently skipped
# -- BD's web UI still works".
#
# Measured 2026-09-01 on 10.0.70.54 (pystray 0.19.5, DISPLAY unset): a bare
# `import pystray` raises, with no test runner involved. pystray sits in
# requirements.txt since 2026-09-03 (before that in the optional manifest,
# which only 6 of the 12 fleet hosts had installed), so
# `tests/test_v3_43_80_modules.py::TestImports::test_all_modules_import`
# failed on exactly those hosts and passed on the others -- which read as
# flakiness. tools/verify_release.py has carried "Bad display name" and
# "Can't connect to display" in _HARNESS_SIGNATURES all along, classifying
# the symptom as an environment artifact; this is the cause.
#
# The ImportError arm is kept SEPARATE rather than widened into a bare
# `except Exception`, so "absent" and "present but broken" stay
# distinguishable in the recorded reason.
try:
    import pystray  # type: ignore
    _HAS_PYSTRAY = True
except ImportError as _exc:
    _record_unavailable("pystray", _exc)
except Exception as _exc:  # installed, but its backend cannot initialise
    _record_unavailable("pystray", _exc)

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
    _HAS_PIL = True
except ImportError as _exc:
    _record_unavailable("Pillow", _exc)
except Exception as _exc:  # e.g. a Pillow whose native libs will not load
    _record_unavailable("Pillow", _exc)


def is_available() -> bool:
    """True when the tray can actually run."""
    return _HAS_PYSTRAY and _HAS_PIL


def unavailable_reason() -> Optional[str]:
    """Why the tray is unusable, or None when it is usable.

    Carries the dependency's own exception text so an operator can tell
    "pip install pystray" apart from "this box has no display".
    """
    if is_available():
        return None
    return _UNAVAILABLE_REASON


_icon = None  # active pystray.Icon (singleton)
_stop_event = threading.Event()
_state: dict = {"running": 0, "queued": 0, "failed": 0, "needs_review": 0,
                "url": "http://127.0.0.1:5000/", "last_poll_ts": 0.0,
                "online": True}


# ─── Icon rendering ───────────────────────────────────────────────────

def _render_icon(state: dict):
    """Render a 64x64 PIL Image showing current counts.

    Colors:
      • Green when running > 0
      • Amber when needs_review > 0 (operator attention needed)
      • Red when failed > 0
      • Grey when idle
    """
    if not _HAS_PIL:
        return None
    img = Image.new("RGBA", (64, 64), (15, 23, 42, 255))  # slate-900
    draw = ImageDraw.Draw(img)
    # Color picker — most-urgent state wins
    if state.get("failed", 0) > 0:
        color = (220, 38, 38, 255)   # red-600
    elif state.get("needs_review", 0) > 0:
        color = (245, 158, 11, 255)  # amber-500
    elif state.get("running", 0) > 0:
        color = (16, 185, 129, 255)  # emerald-500
    else:
        color = (100, 116, 139, 255)  # slate-500
    # Outer rounded square accent
    draw.rounded_rectangle((4, 4, 60, 60), radius=12, outline=color, width=3)
    # Big number = total active (running + queued)
    total = int(state.get("running", 0)) + int(state.get("queued", 0))
    text = str(min(99, total)) if total < 100 else "99+"
    # Try to use a real font; fall back to default
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 32)
    except (OSError, IOError):
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
    # Center the text
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((64 - tw) // 2, (64 - th) // 2 - 4), text,
                  fill=(255, 255, 255, 255), font=font)
    except Exception:
        # Best-effort fallback
        draw.text((20, 18), text, fill=(255, 255, 255, 255))
    return img


# ─── Status polling ───────────────────────────────────────────────────

def _poll_loop(url: str):
    """Background thread: poll /api/status_all every 5s, update _state."""
    try:
        import requests
    except ImportError:
        sys.stderr.write("[tray] requests not installed; tray cannot poll\n")
        return
    poll_url = url.rstrip("/") + "/api/status_all"
    while not _stop_event.is_set():
        try:
            r = requests.get(poll_url, timeout=5)
            if r.ok:
                data = r.json() or {}
                running = queued = failed = review = 0
                for sid, st in (data.get("sites") or {}).items():
                    jobs = (st or {}).get("jobs") or {}
                    for job in jobs.values() if isinstance(jobs, dict) else []:
                        s = (job or {}).get("status", "")
                        if s == "running":
                            running += 1
                        elif s == "pending":
                            queued += 1
                        elif s == "failed":
                            failed += 1
                        elif s == "needs_review":
                            review += 1
                _state.update(running=running, queued=queued,
                              failed=failed, needs_review=review,
                              last_poll_ts=time.time(), online=True)
                _refresh_icon()
            else:
                _state["online"] = False
        except Exception:
            _state["online"] = False
        _stop_event.wait(5)


def _refresh_icon():
    """Push a new icon image + tooltip to the running tray."""
    global _icon
    if _icon is None or not _HAS_PIL:
        return
    try:
        _icon.icon = _render_icon(_state)
        running = _state.get("running", 0)
        queued = _state.get("queued", 0)
        failed = _state.get("failed", 0)
        review = _state.get("needs_review", 0)
        _icon.title = (
            f"BulkDownloader: {running} running, {queued} queued"
            + (f", {failed} failed" if failed else "")
            + (f", {review} need review" if review else "")
        )
    except Exception as e:
        sys.stderr.write(f"[tray] refresh failed: {e}\n")


# ─── Tray actions ─────────────────────────────────────────────────────

def _open_dashboard(icon=None, item=None):
    webbrowser.open(_state["url"])


def _pause_all(icon=None, item=None):
    _post("/api/pause_all")


def _resume_all(icon=None, item=None):
    _post("/api/resume_all")


def _post(path: str):
    """Send POST to BD API. Best-effort, errors logged only."""
    try:
        import requests
        url = _state["url"].rstrip("/") + path
        # No CSRF in tray context — BD's /api/pause_all accepts the
        # local connection without CSRF when same-origin from loopback.
        # If your config enforces CSRF on loopback, this returns 403
        # and the tray logs but continues.
        r = requests.post(url, timeout=3,
                         headers={"X-Loopback-Tray": "1"})
        if not r.ok:
            sys.stderr.write(f"[tray] POST {path}: HTTP {r.status_code}\n")
    except Exception as e:
        sys.stderr.write(f"[tray] POST {path} failed: {e}\n")


def _quit(icon=None, item=None):
    _stop_event.set()
    if _icon:
        _icon.stop()


def _build_menu():
    """Construct the tray menu. Items reflect current state — e.g.
    'Pause all' is dynamic-disabled if there are no running jobs."""
    if not _HAS_PYSTRAY:
        return None
    M = pystray.MenuItem
    return pystray.Menu(
        M(lambda item: (f"{_state.get('running', 0)} running · "
                        f"{_state.get('queued', 0)} queued"),
          None, enabled=False),
        M(lambda item: (f"{_state.get('failed', 0)} failed · "
                        f"{_state.get('needs_review', 0)} need review"),
          None, enabled=False, visible=lambda item: (
              _state.get('failed', 0) + _state.get('needs_review', 0) > 0)),
        pystray.Menu.SEPARATOR,
        M("Open dashboard", _open_dashboard, default=True),
        M("Pause all workers", _pause_all,
          enabled=lambda item: _state.get('running', 0) > 0),
        M("Resume all workers", _resume_all,
          enabled=lambda item: _state.get('queued', 0) > 0),
        pystray.Menu.SEPARATOR,
        M("Quit tray", _quit),
    )


# ─── Public entry points ──────────────────────────────────────────────

def start(*, url: str = "http://127.0.0.1:5000/",
         detached: bool = True) -> bool:
    """Spin up the tray. `url` is the BD dashboard URL the tray polls
    and opens. `detached=True` runs in a daemon thread (recommended);
    set False to run blocking in the foreground for debugging.

    Returns True on successful start, False if unavailable or already
    running."""
    global _icon
    if not is_available():
        reason = unavailable_reason() or "pystray and/or Pillow not installed"
        sys.stderr.write(
            f"[tray] unavailable — {reason}. "
            "pip install pystray Pillow (and, on Linux, run with a display)\n")
        return False
    if _icon is not None:
        return False  # already running
    _state["url"] = url
    _stop_event.clear()
    # Kick off the polling thread first so initial state is populated
    poll_thread = threading.Thread(target=_poll_loop, args=(url,),
                                   daemon=True, name="bd-tray-poll")
    poll_thread.start()
    # Build the tray icon
    img = _render_icon(_state)
    _icon = pystray.Icon("bulk-downloader", img,
                         "BulkDownloader", _build_menu())

    def _run():
        try:
            _icon.run()
        except Exception as e:
            sys.stderr.write(f"[tray] icon.run() raised: {e}\n")

    if detached:
        t = threading.Thread(target=_run, daemon=True, name="bd-tray-ui")
        t.start()
    else:
        _run()
    return True


def stop():
    """Signal the tray to exit. Idempotent."""
    global _icon
    _stop_event.set()
    if _icon is not None:
        try:
            _icon.stop()
        except Exception:
            pass
        _icon = None
