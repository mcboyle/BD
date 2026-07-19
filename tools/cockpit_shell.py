"""Cockpit interactive shell — an OPT-IN operator admin surface.

THIS MODULE INTENTIONALLY PROVIDES ARBITRARY LOCAL COMMAND EXECUTION.

It is deliberately separate from cockpit_core.py so the recognition cockpit's
allowlist guarantee (enforced by test_v3_66_98) stays intact and provable: the
core never imports this, and the recognition features cannot execute arbitrary
commands. This shell is a *different* surface — a web terminal to the operator's
own machine, the way RedHat Cockpit / wetty / VS Code Server provide one.

SECURITY MODEL (read before deploying):
  * ON BY DEFAULT since v3.66.183 (deliberate operator decision for the
    pre-launch, single-operator LAN deployment). Set BD_COCKPIT_SHELL=0 to
    hard-disable; with pty unavailable it also stays off.
  * Because the env gate no longer backstops it, the localhost/LAN bind +
    authentication requirement below is now LOAD-BEARING, not advisory.
  * It runs commands as the cockpit's own OS user with that user's full
    privileges. Whoever can reach this page can run anything that user can.
  * Therefore the cockpit MUST be bound to localhost/LAN and behind
    authentication. Do NOT expose it to an untrusted network.
  * Every command sent is recorded to a shell audit log.
  * F2/Phase C will revisit reachability hardening (origin/bind guard). This
    deferral is tracked as a Phase C backlog item, not left unowned.

This is a deliberate departure from the recognition-only posture, enabled by an
explicit operator decision. cockpit_core never imports this module, so the
recognition surface's allowlist guarantee stays intact and provable.
"""
from __future__ import annotations

import os
import re
import secrets
import signal
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

# pty is POSIX-only; import lazily so the module still loads on Windows (where
# the shell simply reports unavailable).
try:
    import pty as _pty
    import select as _select
    _PTY_OK = True
except Exception:  # pragma: no cover - Windows
    _PTY_OK = False


class ShellError(Exception):
    pass


_SESSIONS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()
_BUF_CAP = 200_000          # bytes of scrollback kept per session
_IDLE_REAP = 1800           # seconds; idle sessions are killed
_ANSI = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[\]P^_].*?(?:\x07|\x1b\\)|\r")


def _shell_pref() -> str:
    """v3.66.317 (CLI->GUI parity): the cockpit-shell enable preference, read
    store > env > default "1". global_config imported lazily (this is a tools
    module — thin, no Flask); falls back to env/default on any error. Returns the
    raw "0"/"1"-ish string; shell_enabled() applies the _PTY_OK gate."""
    try:
        from bulk_downloader import global_config as _gc
        st = str(_gc.get("cockpit_shell", "") or "").strip()
        if st:
            return st
    except Exception:
        pass
    return os.environ.get("BD_COCKPIT_SHELL", "1")


def shell_enabled() -> bool:
    """The single gate. Everything else refuses unless this is true.

    Default-ON since v3.66.183 (deliberate operator decision for the
    pre-launch, single-operator LAN deployment): the shell is enabled unless
    explicitly opted out with BD_COCKPIT_SHELL=0. _PTY_OK keeps it off where
    pty is unavailable.

    v3.66.317 (CLI->GUI parity): the enable is now a live GUI control via the
    global_config `cockpit_shell` key (store > env > default "1"); a GUI write
    takes effect on the next session start. A store "0" hard-disables even when
    env is unset; a store "1" force-enables even where env set "0".

    F2/Phase C — revisit shell reachability hardening HERE (e.g. an
    origin/bind guard so default-on can never mean reachable from an untrusted
    network). Intentionally deferred to the dedicated security pass; not a
    foot-gun left unowned — it is tracked as a Phase C backlog item.
    """
    return _shell_pref() != "0" and _PTY_OK


def _audit_path() -> Path:
    root = os.environ.get("BD_COCKPIT_TASKS") or os.environ.get("BD_HOME") or "."
    p = Path(root) / "shell_audit.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _audit(sid: str, text: str) -> None:
    try:
        with _audit_path().open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{sid}\t{text!r}\n")
    except Exception:
        pass


def _require(sid: str) -> Dict[str, Any]:
    if not shell_enabled():
        raise ShellError("shell disabled (BD_COCKPIT_SHELL=0)")
    sess = _SESSIONS.get(sid)
    if not sess or not sess.get("alive"):
        raise ShellError("no such shell session")
    sess["last"] = time.time()
    return sess


def _reader(sid: str) -> None:
    sess = _SESSIONS.get(sid)
    if not sess:
        return
    fd = sess["fd"]
    try:
        while sess["alive"]:
            r, _, _ = _select.select([fd], [], [], 0.4)
            if r:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                with sess["lock"]:
                    sess["buf"].extend(data)
                    if len(sess["buf"]) > _BUF_CAP:
                        del sess["buf"][:len(sess["buf"]) - _BUF_CAP]
            # idle reap
            if time.time() - sess.get("last", 0) > _IDLE_REAP:
                break
    finally:
        sess["alive"] = False
        try:
            os.close(fd)
        except OSError:
            pass


def shell_open() -> Dict[str, Any]:
    if not shell_enabled():
        raise ShellError("shell disabled (BD_COCKPIT_SHELL=0)")
    pid, fd = _pty.fork()
    if pid == 0:
        # child: become an interactive login shell
        env = dict(os.environ)
        env.setdefault("TERM", "xterm")
        env["PS1"] = r"\w\$ "
        try:
            os.execvpe("bash", ["bash", "-i"], env)
        except Exception:
            os._exit(127)
    sid = secrets.token_hex(8)
    sess = {"fd": fd, "pid": pid, "buf": bytearray(), "lock": threading.Lock(),
            "alive": True, "last": time.time()}
    with _LOCK:
        _SESSIONS[sid] = sess
    threading.Thread(target=_reader, args=(sid,), daemon=True).start()
    _audit(sid, "<session opened>")
    return {"session": sid}


def shell_input(sid: str, data: str) -> Dict[str, Any]:
    sess = _require(sid)
    if not isinstance(data, str):
        raise ShellError("input must be a string")
    _audit(sid, data)
    try:
        os.write(sess["fd"], data.encode("utf-8", "replace"))
    except OSError as e:
        raise ShellError(f"write failed: {e}")
    return {"ok": True}


def shell_signal(sid: str, sig: str = "INT") -> Dict[str, Any]:
    """Send a control signal (Ctrl-C etc.) to the session."""
    sess = _require(sid)
    ctrl = {"INT": b"\x03", "EOF": b"\x04", "TSTP": b"\x1a"}.get(sig.upper())
    if not ctrl:
        raise ShellError("unsupported signal")
    os.write(sess["fd"], ctrl)
    return {"ok": True}


def shell_poll(sid: str, offset: int = 0) -> Dict[str, Any]:
    sess = _require(sid)
    with sess["lock"]:
        buf = bytes(sess["buf"])
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    chunk = buf[offset:]
    # store raw, strip ANSI/control only for display so offsets stay stable
    display = _ANSI.sub(b"", chunk).decode("utf-8", "replace")
    return {"data": display, "offset": len(buf), "alive": sess["alive"]}


def shell_close(sid: str) -> Dict[str, Any]:
    with _LOCK:
        sess = _SESSIONS.pop(sid, None)
    if sess:
        sess["alive"] = False
        try:
            os.kill(sess["pid"], signal.SIGKILL)
        except OSError:
            pass
        _audit(sid, "<session closed>")
    return {"closed": True}


def shell_status() -> Dict[str, Any]:
    return {"enabled": shell_enabled(),
            "pty_available": _PTY_OK,
            "active_sessions": sum(1 for s in _SESSIONS.values() if s.get("alive")),
            "note": ("Interactive shell is ON by default (v3.66.183); set "
                     "BD_COCKPIT_SHELL=0 to hard-disable. It runs arbitrary "
                     "commands as the cockpit user — bind the cockpit to "
                     "localhost/LAN and put it behind auth. Every command is "
                     "recorded to shell_audit.log. F2/Phase C will revisit "
                     "reachability hardening.")}
