#!/usr/bin/env python3
"""cockpit_core.py — backend enforcement for the operator cockpit console.

This is the security spine of the cockpit GUI, kept separate from the Flask
blueprint so it is unit-testable with no web server. It enforces the hard
boundaries the cockpit operates under:

  * a fixed ALLOWLIST of report generators and capture tools — there is no
    free-form command path, no shell, no arbitrary executable.
  * argument VALIDATION per tool — every parameter is checked against a strict
    spec (site id charset, label charset, enum axes, bool flags).
  * path CONFINEMENT — every output/capture path must resolve strictly under an
    approved root; traversal (.., absolute escapes, symlink escapes) is refused.
  * a server-owned TASK REGISTRY — task ids are generated server-side; clients
    never supply them.
  * log + artifact REDACTION — output shown in the UI is posture-scanned and
    signing values are stripped before display or save.

What this module REFUSES, structurally (not by policy text):
  * shell execution (subprocess is always a fixed argv list, shell=False).
  * any command not in ALLOWLIST.
  * any path outside the approved roots.
  * request replay, signed-URL reconstruction, token reuse, browser-driving from
    captures, automatic selector promotion / profile update / corpus write /
    debt retirement — none of these are even expressible here; the allowlist
    contains only viewers, analyzers, and authorized local capture tools, and
    the corpus/selector/profile writers are simply not in it.

This is authorized LOCAL BD operations only. It is NOT C2: there is no remote
node, no agent dispatch, no arbitrary task surface.
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
import uuid
import shlex
import threading
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# Approved roots (config/env-driven, never client-supplied)
# ─────────────────────────────────────────────────────────────────────────────

def _cfg_path(store_key: str, env_name: str, default: str) -> str:
    """v3.66.317 (CLI->GUI parity): resolve a path root store > env > default.
    Value is honored as-written — NO BD_HOME jail (operator directive; matches the
    308 capture_raw value-honored precedent). global_config imported lazily (tools
    module); falls back to env/default on any error."""
    try:
        from bulk_downloader import global_config as _gc
        st = str(_gc.get(store_key, "") or "").strip()
        if st:
            return st
    except Exception:
        pass
    return os.environ.get(env_name, default)


def reports_root() -> Path:
    return Path(_cfg_path("framework_reports", "BD_FRAMEWORK_REPORTS", "./framework_reports")).resolve()

def captures_root() -> Path:
    return Path(os.environ.get("BD_CAPTURES_ROOT", "./captures")).resolve()

def tasks_root() -> Path:
    return Path(_cfg_path("cockpit_tasks", "BD_COCKPIT_TASKS", "./cockpit_tasks")).resolve()

def _embed_url_with_defaults(url: str) -> str:
    """Append the noVNC embed defaults ``resize=scale`` + ``autoconnect=true`` +
    ``reconnect=true`` + ``reconnect_delay=2000`` to ``url`` ONLY when each param
    is absent (case-insensitive), so the remote canvas scales to fit the iframe
    (instead of clipping at native resolution), connects without a manual click,
    and silently re-establishes if the socket bounces on a resize (BUG-2). An
    explicitly-set value for any is PRESERVED — the defaults only fill a gap,
    never override. Existing query bytes are left untouched; appended params are
    inserted before any ``#fragment`` with the correct ``?``/``&`` separator.
    Never raises (this runs inside a request handler): on any parse trouble the
    input is returned unchanged."""
    try:
        frag = ""
        core = url
        hash_at = core.find("#")
        if hash_at != -1:
            frag = core[hash_at:]
            core = core[:hash_at]
        q_at = core.find("?")
        query = core[q_at + 1:] if q_at != -1 else ""
        present = {seg.split("=", 1)[0].strip().lower()
                   for seg in query.split("&") if seg}
        add = []
        if "resize" not in present:
            add.append("resize=scale")
        if "autoconnect" not in present:
            add.append("autoconnect=true")
        # BUG-2: a window/iframe resize can bounce the VNC WebSocket; without
        # auto-reconnect noVNC drops to its manual "connect" screen (read as a
        # password re-prompt). Fill reconnect defaults (gap-only, explicit wins)
        # so the canvas silently re-establishes instead of forcing an operator
        # handoff on every reflow. Pairs with the FE freezing the iframe src so
        # the element itself is not remounted.
        if "reconnect" not in present:
            add.append("reconnect=true")
        if "reconnect_delay" not in present:
            add.append("reconnect_delay=2000")
        if not add:
            return url
        sep = "&" if query else "?"
        return core + sep + "&".join(add) + frag
    except Exception:
        return url


def novnc_url() -> str:
    """The noVNC URL is config/env ONLY (``BD_NOVNC_URL``); a browser-supplied
    URL is never accepted — this is the single source of truth. Served as the
    operator set it, EXCEPT that two embed defaults are filled when absent:
    ``resize=scale`` (canvas scales to fit the iframe rather than clipping) and
    ``autoconnect=true`` (connect without a manual click). An explicit value for
    either always wins — see ``_embed_url_with_defaults``."""
    raw = _resolve_novnc_url()
    if not raw:
        return ""
    return _embed_url_with_defaults(raw)


def _resolve_novnc_url() -> str:
    """v3.66.312 (CLI->GUI parity): the noVNC URL is config/env only — never
    browser-supplied. Precedence MATCHES cloak.resolve_backend for the Browser group:
    env (`BD_NOVNC_URL`) > global_config store (`novnc_url`) > "" — the env stays a
    deliberate deploy override, and the Settings write is honored when the env is unset.
    Read at call time so a write takes effect on the next cockpit render (no restart).
    global_config imported lazily (thin, no Flask); falls back to store-less on any error."""
    env = os.environ.get("BD_NOVNC_URL", "").strip()
    if env:
        return env
    try:
        from bulk_downloader import global_config as _gc
        v = _gc.get("novnc_url", None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    except Exception:  # noqa: BLE001
        pass
    return ""

_APPROVED_ROOTS = (reports_root, captures_root, tasks_root)


def confine(path_str: str, root: Path) -> Optional[Path]:
    """Resolve a path strictly under `root`. Returns None on any escape attempt
    (absolute path outside, .. traversal, symlink that resolves outside)."""
    if not path_str or not isinstance(path_str, str):
        return None
    # reject obviously hostile inputs early
    if "\x00" in path_str:
        return None
    root = root.resolve()
    # treat the input as relative to root unless it is already under it
    candidate = Path(path_str)
    p = candidate if candidate.is_absolute() else (root / candidate)
    try:
        p = p.resolve()
    except Exception:
        return None
    try:
        p.relative_to(root)
    except ValueError:
        return None
    return p


def confine_any(path_str: str) -> Optional[Tuple[Path, Path]]:
    """Resolve under any approved root; returns (resolved, root) or None."""
    for rfn in _APPROVED_ROOTS:
        r = rfn()
        p = confine(path_str, r)
        if p is not None:
            return p, r
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Argument validators
# ─────────────────────────────────────────────────────────────────────────────

_SITE_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$", re.I)
_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_\-.]{0,127}$", re.I)
_AXES = ("player_config", "workflow")
_MODES = ("pair", "n3", "single")


class ValidationError(ValueError):
    pass


def v_site(s: Any) -> str:
    if not isinstance(s, str) or not _SITE_RE.match(s):
        raise ValidationError(f"invalid site id (allowed: letters/digits/_-, <=64): {s!r}")
    return s


def v_label(s: Any) -> str:
    if not isinstance(s, str) or not _LABEL_RE.match(s):
        raise ValidationError(f"invalid label (allowed: letters/digits/_-., <=128): {s!r}")
    return s


def v_axis(s: Any) -> Optional[str]:
    if s in (None, "", "none"):
        return None
    if s not in _AXES:
        raise ValidationError(f"axis must be one of {_AXES}, got {s!r}")
    return s


def v_mode(s: Any) -> str:
    if s not in _MODES:
        raise ValidationError(f"mode must be one of {_MODES}, got {s!r}")
    return s


def v_bool(b: Any) -> bool:
    if isinstance(b, bool):
        return b
    if isinstance(b, str) and b.lower() in ("true", "false", "1", "0", "on", "off"):
        return b.lower() in ("true", "1", "on")
    raise ValidationError(f"expected boolean, got {b!r}")


def v_out_under_captures(s: Any) -> Path:
    """An output folder must confine under the captures root."""
    if not isinstance(s, str):
        raise ValidationError("output folder must be a string")
    p = confine(s, captures_root())
    if p is None:
        raise ValidationError(f"output path escapes the approved captures root: {s!r}")
    return p


def v_url(s: Any) -> str:
    """A capture START url. We accept an http(s) URL (the operator is navigating
    their own authorized session) but reject anything with shell metacharacters,
    newlines, or non-http schemes — and never pass it through a shell."""
    if not isinstance(s, str) or not s:
        raise ValidationError("url required")
    if not re.match(r"^https?://", s):
        raise ValidationError("url must be http(s)")
    if any(c in s for c in "\n\r\x00`$;|&<>"):
        raise ValidationError("url contains illegal characters")
    if len(s) > 2048:
        raise ValidationError("url too long")
    return s


def v_int(value: Any, *, lo: int, hi: int, name: str = "value") -> int:
    """Validate an operator-supplied integer within [lo, hi]. Rejects
    non-integers and out-of-range values. Callers only invoke this for
    *present* (non-blank) params, so a blank field keeps the backend default."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be an integer, got {value!r}")
    if n < lo or n > hi:
        raise ValidationError(f"{name} must be between {lo} and {hi}, got {n}")
    return n


# ─────────────────────────────────────────────────────────────────────────────
# The ALLOWLIST — the only things the cockpit can run
# ─────────────────────────────────────────────────────────────────────────────
# Each entry is an explicit recipe. There is NO generic "run command" path.
# kind: "inproc" calls a python function directly (no process at all);
#       "subprocess" runs a FIXED argv (shell=False, fixed interpreter+script).
# build_argv/build_call receive a VALIDATED params dict and return the exact
# invocation. Anything not here cannot be run.

def _py() -> str:
    return sys.executable or "python3"


# --- report generators (read existing artifacts / run analyzers; write reports)
REPORT_RUNNERS: Dict[str, Dict[str, Any]] = {
    "autopilot_cockpit": {
        "label": "Capture cockpit (autopilot)",
        "why": "Discovers captures under the approved root and runs the analysis "
               "chain (temporal series + optional perturbation). Writes a cockpit. "
               "Never writes corpus, never acts.",
        "kind": "subprocess",
        "human_review": False,
        "script": "tools/operator_layer.py",
        "out_subdir": "autopilot",
    },
    "exec_summary": {
        "label": "Executive summary",
        "why": "Rolls up existing per-site artifacts into a leadership view. Read-only.",
        "kind": "subprocess",
        "human_review": False,
        "script": "tools/operator_layer.py",
        "out_subdir": "exec",
    },
}

# --- authorized LOCAL capture tools
CAPTURE_TOOLS: Dict[str, Dict[str, Any]] = {
    "capture_session": {
        "label": "Capture session (headed, local)",
        "why": "Launches the local headed capture browser for ONE authorized "
               "session. The operator logs in and plays; the tool records and "
               "redacts. No token is stored, no stream reconstructed.",
        "kind": "subprocess",
        "human_review": True,
        "script": "tools/capture_session.py",
    },
    "capture_batch": {
        "label": "Capture batch (local)",
        "why": "Runs multiple authorized capture sessions in sequence under the "
               "approved root. Same per-session boundary.",
        "kind": "subprocess",
        "human_review": True,
        "script": "tools/capture_batch.py",
    },
    "offline_capture_analyze": {
        "label": "Offline perturbation analysis",
        "why": "Analyzes two existing captures on an axis. Offline, recognition-"
               "only; emits a reviewable corpus SUGGESTION, never writes it.",
        "kind": "subprocess",
        "human_review": False,
        "script": "tools/offline_capture_analyze.py",
    },
    "autopilot": {
        "label": "Autopilot (analyze a capture folder)",
        "why": "Discovers and analyzes a folder of captures, runs temporal/"
               "perturbation, writes a cockpit. Never writes corpus, never acts.",
        "kind": "subprocess",
        "human_review": False,
        "script": "tools/operator_layer.py",
    },
}


def _argv_for_report(name: str, params: Dict[str, Any], task_out: Path) -> List[str]:
    if name == "autopilot_cockpit":
        # analyze whatever captures live under the captures root
        return [_py(), str(_ROOT / "tools/operator_layer.py"), "autopilot",
                str(captures_root()), "--out-dir", str(task_out)]
    if name == "exec_summary":
        root = params.get("portfolio_root")
        p = confine(root, reports_root()) if root else (reports_root() / "portfolio")
        if p is None:
            raise ValidationError("portfolio_root escapes the reports root")
        return [_py(), str(_ROOT / "tools/operator_layer.py"), "exec",
                "--portfolio-root", str(p), "--out-dir", str(task_out)]
    raise ValidationError(f"unknown report runner: {name!r}")


def _argv_for_capture(name: str, params: Dict[str, Any], task_out: Path) -> List[str]:
    if name == "capture_session":
        url = v_url(params.get("url"))
        label = v_label(params.get("label") or "capture")
        out = task_out / f"{label}.wacz"
        argv = [_py(), str(_ROOT / "tools/capture_session.py"),
                "--url", url, "--title", label, "--out", str(out)]
        # BUGFIX (v3.66.268): isolate the url-memory file PER TASK. capture_session
        # replays a REMEMBERED page for a known --title INSTEAD of --url; the
        # interactive cockpit/SPA "Open session" flow uses a constant title
        # ("capture") with the SHARED default ./capture_url_memory.json, so every
        # session re-opened the last remembered page (e.g. example.com) and ignored
        # the typed URL. A fresh per-task memory file is empty, so the typed --url
        # always wins; any page this session remembers is scoped to the throwaway
        # task dir (no cross-session collision). capture_session.py is unchanged.
        argv += ["--url-memory-file", str(task_out / "capture_url_memory.json")]
        if v_bool(params.get("autofill", False)):
            argv.append("--autofill")
        prof = params.get("profile_dir")
        if prof:
            pp = confine(prof, captures_root())
            if pp is None:
                raise ValidationError("profile_dir escapes the approved root")
            argv += ["--profile-dir", str(pp)]
        # Numeric parity knobs — existing capture_session options. Each is
        # passed ONLY when the operator supplied a value; a blank field keeps
        # capture_session's own default, preserving the prior command exactly.
        bcap = params.get("body_cap_mib")
        if bcap not in (None, ""):
            argv += ["--body-cap-mib", str(v_int(bcap, lo=1, hi=64, name="body_cap_mib"))]
        chunks = params.get("chunk_events")
        if chunks not in (None, ""):
            argv += ["--chunk-events", str(v_int(chunks, lo=1000, hi=1_000_000, name="chunk_events"))]
        msec = params.get("max_seconds")
        if msec not in (None, ""):
            # Kept <= 1700 so it stays below the cockpit runner's 1800s kill,
            # letting the capture auto-save gracefully instead of being killed.
            argv += ["--max-seconds", str(v_int(msec, lo=1, hi=1700, name="max_seconds"))]
        # DOM capture is implicit on interaction; expose the flag only if the
        # tool supports it (it records DOM when the operator interacts).
        # Decorative HUD is ON by default; the GUI checkbox (cs_hud) is checked
        # by default, so a value arrives only when the operator unticks it.
        # Forward --no-hud ONLY when explicitly disabled; a blank/missing value
        # keeps the default (HUD on), preserving the prior command.
        hud_val = params.get("hud", True)
        if hud_val in (None, ""):
            hud_val = True
        if not v_bool(hud_val):
            argv.append("--no-hud")
        return argv
    if name == "capture_batch":
        # batch reads a validated targets file under the captures root
        targets = params.get("targets_file")
        tp = confine(targets, captures_root()) if targets else None
        if tp is None or not tp.is_file():
            raise ValidationError("targets_file must be an existing file under the approved root")
        return [_py(), str(_ROOT / "tools/capture_batch.py"),
                "--targets", str(tp), "--out-dir", str(task_out)]
    if name == "offline_capture_analyze":
        base = params.get("baseline"); pert = params.get("perturbed")
        bp = confine(base, captures_root()) if base else None
        pp = confine(pert, captures_root()) if pert else None
        if bp is None or pp is None:
            raise ValidationError("baseline and perturbed must be files under the approved root")
        axis = v_axis(params.get("axis")) or "player_config"
        return [_py(), str(_ROOT / "tools/offline_capture_analyze.py"),
                "--baseline", str(bp), "--perturbed", str(pp),
                "--axis", axis, "--out", str(task_out)]
    if name == "autopilot":
        folder = params.get("folder")
        fp = confine(folder, captures_root()) if folder else captures_root()
        if fp is None:
            raise ValidationError("folder escapes the approved captures root")
        argv = [_py(), str(_ROOT / "tools/operator_layer.py"), "autopilot",
                str(fp), "--out-dir", str(task_out)]
        axis = v_axis(params.get("axis"))
        if axis:
            argv += ["--axis", axis]
        return argv
    raise ValidationError(f"unknown capture tool: {name!r}")


def build_invocation(category: str, name: str, params: Dict[str, Any],
                     task_out: Path) -> List[str]:
    """The ONLY place an argv is built. Returns a fixed argv list (shell=False).
    Raises ValidationError for anything not allowlisted or any bad argument."""
    if category == "report":
        if name not in REPORT_RUNNERS:
            raise ValidationError(f"report runner not allowlisted: {name!r}")
        return _argv_for_report(name, params, task_out)
    if category == "capture":
        if name not in CAPTURE_TOOLS:
            raise ValidationError(f"capture tool not allowlisted: {name!r}")
        return _argv_for_capture(name, params, task_out)
    raise ValidationError(f"unknown category: {category!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Redaction (logs + artifacts before display/save)
# ─────────────────────────────────────────────────────────────────────────────

def redact(text: str) -> str:
    """Strip secret values from any text shown in the UI, via the project's
    canonical redactor (capture_artifact_redact.redact_value) so the I0008
    floor stays in one place (F-COCKPIT02-01)."""
    try:
        from bulk_downloader.capture_artifact_redact import redact_value
        return redact_value(text)
    except Exception:
        pass
    # last-ditch fallback (app not importable): strip common signing params.
    out = re.sub(r"((?:token|sig|signature|expires|st|policy|key|hmac|exp)=)[^&\s\"']+",
                 r"\1<redacted>", text, flags=re.I)
    return out


def posture_clean(text: str) -> List[str]:
    """Return posture leaks (empty list = clean). Fails CLOSED: on scanner
    unavailability/error, returns a non-empty sentinel so callers quarantine
    the artifact rather than passing it through unscanned (F-COCKPIT02-02)."""
    try:
        from bulk_downloader.capture_ingest import posture_scan
        return posture_scan(text)
    except Exception:
        return ["<posture scanner unavailable>"]


# ─────────────────────────────────────────────────────────────────────────────
# Task registry (server-owned ids; processes tracked here)
# ─────────────────────────────────────────────────────────────────────────────

_REG_LOCK = threading.Lock()
_REGISTRY: Dict[str, Dict[str, Any]] = {}


def _new_task_id() -> str:
    """Server-generated. Clients NEVER supply a task id."""
    return "t_" + uuid.uuid4().hex[:16]


def _task_dir(task_id: str) -> Path:
    d = (tasks_root() / task_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_tasks() -> List[Dict[str, Any]]:
    with _REG_LOCK:
        return [dict(v) for v in sorted(_REGISTRY.values(),
                                        key=lambda t: -t.get("started", 0))]


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    with _REG_LOCK:
        t = _REGISTRY.get(task_id)
        return dict(t) if t else None


def get_task_log(task_id: str) -> str:
    t = get_task(task_id)
    if not t:
        return ""
    logp = Path(t["log_path"])
    if not logp.is_file():
        return ""
    return redact(logp.read_text(encoding="utf-8", errors="replace"))


def finish_capture(task_id: str, discard: bool = False) -> Dict[str, Any]:
    """Signal a running interactive (noVNC) capture to stop.

    capture_session.py runs as a subprocess with no controlling terminal, so it
    cannot read ENTER; instead it polls its output dir for a sentinel (FINISH ->
    save the WACZ, CANCEL -> discard). This writes that sentinel, which is how a
    cockpit/noVNC operator ends a capture without opening a second shell.
    """
    t = get_task(task_id)
    if not t:
        raise ValidationError(f"no such task: {task_id!r}")
    if t.get("category") != "capture":
        raise ValidationError(f"task {task_id!r} is not a capture task")
    out_dir = t.get("out_dir")
    if not out_dir:
        raise ValidationError(f"task {task_id!r} has no output dir")
    name = "CANCEL" if discard else "FINISH"
    p = Path(out_dir) / name
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    except OSError as e:
        raise ValidationError(f"could not write {name} sentinel: {e}")
    return {"task_id": task_id, "wrote": str(p),
            "discard": discard, "action": "discard" if discard else "finish"}


def goto_capture(task_id: str) -> Dict[str, Any]:
    """Signal a running interactive capture to RE-NAVIGATE to its start URL.

    Drops the GOTO sentinel capture_session.py services on its poll tick — used
    when a login redirect leaves the held-open session on a host/landing page
    instead of the requested --url, so the operator can return to the deep URL
    without a second shell. Same cross-process pattern as FINISH/CANCEL.
    """
    t = get_task(task_id)
    if not t:
        raise ValidationError(f"no such task: {task_id!r}")
    if t.get("category") != "capture":
        raise ValidationError(f"task {task_id!r} is not a capture task")
    out_dir = t.get("out_dir")
    if not out_dir:
        raise ValidationError(f"task {task_id!r} has no output dir")
    p = Path(out_dir) / "GOTO"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    except OSError as e:
        raise ValidationError(f"could not write GOTO sentinel: {e}")
    return {"task_id": task_id, "wrote": str(p), "action": "goto"}


def pick_capture(task_id: str, action: str = "arm") -> Dict[str, Any]:
    """Drive the one-shot ACTIVE element-pick on a running interactive capture.

    Mirrors :func:`finish_capture`: the capture is a subprocess polling its
    out_dir, so the app cannot touch the live page directly -- it drives a
    filesystem sentinel (``PICK_ARM``) and reads the result (``PICK_RESULT.json``)
    that the capture writes. The capture's per-second on_tick injects the active
    listener while ``PICK_ARM`` is present and drains the result. See
    ``bulk_downloader.element_pick``.

    Distinct from the OBSERVATIONAL picker (which records every click passively):
    this is single-shot and cancels the click's default action, so picking a
    download row returns one finished selector without firing the download.

    action:
      ``"arm"``   -- request a one-shot pick (operator entered Pick mode);
      ``"poll"``  -- read-and-clear the picked selector (None until one lands);
      ``"clear"`` -- cancel a pending arm (operator left Pick mode).
    """
    from bulk_downloader import element_pick as ep
    t = get_task(task_id)
    if not t:
        raise ValidationError(f"no such task: {task_id!r}")
    if t.get("category") != "capture":
        raise ValidationError(f"task {task_id!r} is not a capture task")
    out_dir = t.get("out_dir")
    if not out_dir:
        raise ValidationError(f"task {task_id!r} has no output dir")
    if action == "arm":
        ok = ep.arm(out_dir)
        return {"task_id": task_id, "action": "arm",
                "armed": bool(ok and ep.is_armed(out_dir))}
    if action == "clear":
        ep.disarm(out_dir)
        return {"task_id": task_id, "action": "clear",
                "armed": ep.is_armed(out_dir)}
    if action == "poll":
        # read-and-clear; the capture removes PICK_ARM when it writes a result,
        # so a present result implies the arm was already consumed (armed False).
        result = ep.consume_result(out_dir)
        return {"task_id": task_id, "action": "poll",
                "armed": ep.is_armed(out_dir), "result": result}
    if action == "dom":
        # F2.7c: request a one-shot live-DOM excerpt (the capture's _pump_dom
        # reads outerHTML, scrubs credential values, writes DOM_RESULT.json).
        # Same cross-process sentinel pattern as arm; poll it with "dom_poll".
        ok = ep.request_dom(out_dir)
        return {"task_id": task_id, "action": "dom",
                "requested": bool(ok and ep.dom_requested(out_dir))}
    if action == "dom_poll":
        result = ep.consume_dom_result(out_dir)
        return {"task_id": task_id, "action": "dom_poll",
                "requested": ep.dom_requested(out_dir), "result": result}
    if action == "inspect_poll":
        # C3: read the LIVE-MIRROR of the HUD action timeline + verify readout
        # (read-only; the capture's pump refreshes it each tick). Structure-only
        # content. None until the first tick lands or when the inspector is off.
        state = ep.read_inspect_state(out_dir)
        return {"task_id": task_id, "action": "inspect_poll", "state": state}
    raise ValidationError(f"unknown pick action: {action!r}")


def suggest_rows_capture(task_id: str, action: str = "arm") -> Dict[str, Any]:
    """Drive the auto-detect-row-groups bridge on a running interactive capture.

    Mirrors :func:`pick_capture`'s cross-process sentinel pattern: the app drives
    AUTO_ROW_REQUEST and reads AUTO_ROW_RESULT.json that the capture's on_tick
    (``element_pick.maybe_suggest_rows``) writes after running the in-page
    ``bdAutoRowGroups`` detector.

    action:
      ``"arm"``  -- request a one-shot auto-detection;
      ``"poll"`` -- read-and-clear the ranked candidates (None until they land).

    Recommendation only -- the ranked candidates pre-fill the wizard's
    row_selectors field; nothing is promoted or enabled here.
    """
    from bulk_downloader import element_pick as ep
    t = get_task(task_id)
    if not t:
        raise ValidationError(f"no such task: {task_id!r}")
    if t.get("category") != "capture":
        raise ValidationError(f"task {task_id!r} is not a capture task")
    out_dir = t.get("out_dir")
    if not out_dir:
        raise ValidationError(f"task {task_id!r} has no output dir")
    if action == "arm":
        ok = ep.request_autorows(out_dir)
        return {"task_id": task_id, "action": "arm", "requested": bool(ok)}
    if action == "poll":
        groups = ep.consume_autorows(out_dir)
        return {"task_id": task_id, "action": "poll", "groups": groups}
    raise ValidationError(f"unknown suggest_rows action: {action!r}")


def start_task(category: str, name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + launch an allowlisted task as a fixed-argv subprocess
    (shell=False). Returns the task record. Raises ValidationError on bad input."""
    spec = (REPORT_RUNNERS if category == "report" else
            CAPTURE_TOOLS if category == "capture" else {}).get(name)
    if spec is None:
        raise ValidationError(f"not allowlisted: {category}/{name}")

    task_id = _new_task_id()
    out = _task_dir(task_id) / "out"
    out.mkdir(parents=True, exist_ok=True)

    argv = build_invocation(category, name, params, out)
    # hard guarantee: argv is a list of str, first element is our interpreter,
    # second is a script under our tree. No shell, no arbitrary path.
    assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
    assert Path(argv[1]).resolve().is_relative_to(_ROOT), "script must be under the BD tree"

    log_path = _task_dir(task_id) / "task.log"
    rec = {
        "task_id": task_id,
        "category": category,
        "name": name,
        "label": spec["label"],
        "why": spec["why"],
        "human_review": spec.get("human_review", False),
        "reduced_redaction": (bool(params.get("reduced_redaction"))
                              if category == "capture" else False),
        "argv_preview": " ".join(shlex.quote(a) for a in argv),
        "out_dir": str(out),
        "log_path": str(log_path),
        "status": "running",
        "started": time.time(),
        "finished": None,
        "returncode": None,
        "output_files": [],
    }
    with _REG_LOCK:
        _REGISTRY[task_id] = rec

    def _run():
        env = dict(os.environ)
        env["BD_DISABLE_KEEPALIVE"] = "1"
        if category == "capture" and bool(params.get("reduced_redaction")):
            # Operator opt-in (per-capture): keep signed URLs so the WACZ writes
            # even when capture-time scrubbing misses a signing shape. Relax BOTH
            # URL surfaces -- the floor gates network signed URLs and DOM-embedded
            # signed URLs (e.g. an <img>/poster ?expires=... thumbnail) by separate
            # knobs; relaxing only the network one leaves a DOM-surface signed URL
            # refused. The capture self-stamps reduced_redaction/local_only (either
            # keep_full trips it) so it is never circulated.
            env["BD_REDACT_NETWORK_URLS"] = "keep_full"
            env["BD_REDACT_DOM_URLS"] = "keep_full"
        try:
            with open(log_path, "w", encoding="utf-8") as lf:
                proc = subprocess.run(
                    argv, stdout=lf, stderr=subprocess.STDOUT,
                    shell=False,                  # <- never a shell
                    cwd=str(_ROOT), env=env, timeout=1800,
                )
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            rc = -1
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write("\n[cockpit] task timed out after 1800s\n")
        except Exception as e:  # pragma: no cover
            rc = -2
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"\n[cockpit] task error: {e}\n")
        # collect + posture-scan output files
        files = []
        if out.is_dir():
            for f in sorted(out.rglob("*")):
                if f.is_file():
                    try:
                        if f.suffix in (".md", ".json", ".txt"):
                            leaks = posture_clean(f.read_text(encoding="utf-8", errors="replace"))
                            if leaks:
                                # never surface a leaking artifact; quarantine note
                                files.append({"path": str(f.relative_to(out)),
                                              "posture": f"WITHHELD ({len(leaks)} leak(s))"})
                                continue
                        files.append({"path": str(f.relative_to(out)), "posture": "clean"})
                    except Exception:
                        files.append({"path": str(f.relative_to(out)), "posture": "unknown"})
        with _REG_LOCK:
            r = _REGISTRY[task_id]
            r["status"] = "succeeded" if rc == 0 else "failed"
            r["returncode"] = rc
            r["finished"] = time.time()
            r["output_files"] = files

        # F0.3 scrub-on-capture (default ON): after a successful capture,
        # auto-produce a share-ready *.redacted.wacz twin next to each raw
        # WACZ + a manifest line. Fail-soft — never blocks/raises and the
        # raw WACZ (local-only) is untouched. The WACZ writer
        # (tools/capture_session.py, guard #3) is NOT edited; the scrub runs
        # here at the caller, post-save.
        if category == "capture" and rc == 0:
            try:
                from bulk_downloader import capture_scrub_hook as _csh
                for _w in sorted(out.glob("*.wacz")):
                    if not _w.name.lower().endswith(".redacted.wacz"):
                        _csh.scrub_on_capture(str(_w))
            except Exception:
                pass

    th = threading.Thread(target=_run, name=f"cockpit-{task_id}", daemon=True)
    th.start()
    return dict(rec)


# ─────────────────────────────────────────────────────────────────────────────
# Spreadsheet / CSV import (structured input ONLY — never executable)
# ─────────────────────────────────────────────────────────────────────────────

_IMPORT_COLUMNS = ("site", "url", "label", "workflow", "notes", "priority")
_PRIORITY = ("low", "medium", "high", "")


def parse_plan(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate planning rows into capture-queue ITEMS. This NEVER executes
    anything — it returns validated data + per-row errors for operator review.
    A row is data: it can only ever become a queued capture item after the
    operator confirms in the UI."""
    items, errors = [], []
    for i, row in enumerate(rows):
        rerr = []
        # only known columns are read; anything else is ignored (not executed)
        site = row.get("site", "")
        url = row.get("url", "")
        label = row.get("label", "") or (site + "_capture" if site else "")
        prio = (row.get("priority", "") or "").strip().lower()
        try:
            v_site(site)
        except ValidationError as e:
            rerr.append(str(e))
        if url:
            try:
                v_url(url)
            except ValidationError as e:
                rerr.append(str(e))
        if label:
            try:
                v_label(label)
            except ValidationError as e:
                rerr.append(str(e))
        if prio not in _PRIORITY:
            rerr.append(f"priority must be low/medium/high, got {prio!r}")
        # detect injection-y content explicitly and reject the row as data
        blob = " ".join(str(v) for v in row.values())
        if any(tok in blob for tok in (";", "|", "`", "$(", "&&", "\n")):
            rerr.append("row contains shell-metacharacter content — refused as data")
        item = {
            "row": i,
            "site": site,
            "url": url,
            "label": label,
            "workflow": str(row.get("workflow", ""))[:200],
            "notes": str(row.get("notes", ""))[:500],
            "priority": prio or "medium",
            "valid": not rerr,
            "errors": rerr,
        }
        items.append(item)
        if rerr:
            errors.append({"row": i, "errors": rerr})
    return {
        "columns_expected": list(_IMPORT_COLUMNS),
        "n_rows": len(rows),
        "n_valid": sum(1 for it in items if it["valid"]),
        "n_invalid": sum(1 for it in items if not it["valid"]),
        "items": items,
        "errors": errors,
        "_note": "Preview only. No capture runs from import. Items become a "
                 "capture queue only after explicit operator confirmation, and "
                 "each capture is still launched one at a time through the "
                 "validated, allowlisted capture path.",
    }


def read_csv_text(text: str) -> List[Dict[str, Any]]:
    """Parse CSV text into rows (dicts). Pure data parsing, no execution."""
    import csv
    import io
    rdr = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in rdr]


# ─────────────────────────────────────────────────────────────────────────────
# WAVE 1 — data views (all read-only over corpus / reports / captures)
# ─────────────────────────────────────────────────────────────────────────────

def _corpus() -> List[Dict[str, Any]]:
    try:
        from bulk_downloader import validation_corpus as vc
        return vc.load_corpus()
    except Exception:
        return []


def _debt_obj() -> Dict[str, Any]:
    try:
        from bulk_downloader import validation_corpus as vc
        r = vc.debt_report(vc.load_corpus())
        return {
            "entries": len(vc.load_corpus()),
            "correction": len(r["correction_debt"]),
            "capability": len(r["capability_debt"]),
            "validation": len(r["validation_debt"]),
            "validation_items": [e["id"] for e in r["validation_debt"]],
            "correction_items": [e["id"] for e in r["correction_debt"]],
        }
    except Exception as e:
        return {"error": str(e)}


def _site_from_subject(subject: str) -> Optional[str]:
    """Best-effort site token out of a corpus subject (e.g.
    'ultrafilms_two_candies...' -> 'ultrafilms'). Heuristic, read-only."""
    if not subject:
        return None
    known = ("ultrafilms", "nubile", "filthy", "evil", "bros", "brazzers",
             "vixen", "adulttime", "naughtyamerica", "dfxtra", "wowgirls")
    low = subject.lower()
    for k in known:
        if k in low:
            return k
    return None


def _site_of_entry(e: Dict[str, Any]) -> Optional[str]:
    """Site token for a corpus entry — checks subject first, then the
    observation/evidence/notes text (the site is often named there, not in the
    subject). Read-only heuristic."""
    s = _site_from_subject(e.get("subject", ""))
    if s:
        return s
    known = ("ultrafilms", "nubile", "filthy", "evil", "bros", "brazzers",
             "vixen", "adulttime", "naughtyamerica", "dfxtra", "wowgirls")
    blob = " ".join(str(e.get(k, "")) for k in
                    ("observation", "evidence", "notes")).lower()
    for k in known:
        if k in blob:
            return k
    return None


def mission_control() -> Dict[str, Any]:
    """Single-screen SOC-style overview. All derived from existing state."""
    tasks = list_tasks()
    running = [t for t in tasks if t["status"] == "running"]
    failed = [t for t in tasks if t["status"] == "failed"]
    debt = _debt_obj()
    corpus = _corpus()
    # recent drift detections from corpus drift_verdict entries
    drift = [{"id": e["id"], "subject": e["subject"], "outcome": e["outcome"],
              "date": e.get("date")} for e in corpus
             if e.get("category") == "drift_verdict"][-8:]
    # captures present under the approved root
    caps = []
    try:
        caps = [p.name for p in captures_root().glob("*.wacz")] \
            if captures_root().is_dir() else []
    except Exception:
        pass
    # sites needing attention: open correction debt or untested validation
    attention = []
    for e in corpus:
        if e["id"] in debt.get("correction_items", []):
            attention.append({"site": _site_of_entry(e) or "(framework)",
                              "why": f"correction debt: {e['subject']}", "id": e["id"]})
    return {
        "active_captures": len(running),  # capture tasks currently running
        "running_tasks": [{"label": t["label"], "task_id": t["task_id"]} for t in running],
        "failed_tasks": [{"label": t["label"], "task_id": t["task_id"],
                         "rc": t.get("returncode")} for t in failed[:8]],
        "review_queue": _review_queue_count(),
        "debt": debt,
        "sites_needing_attention": attention[:10],
        "recent_drift": drift,
        "captures_present": len(caps),
        "capture_names": caps[:20],
        "_status": "Read-only overview. Nothing here acts.",
    }


def _review_queue_count() -> int:
    """Count corpus SUGGESTION artifacts awaiting review under the roots."""
    n = 0
    for root in (reports_root(), tasks_root()):
        if root.is_dir():
            n += sum(1 for _ in root.rglob("corpus_candidate_entry.json"))
            n += sum(1 for _ in root.rglob("suggested_corpus_entry.json"))
    return n


def evidence_timeline(site: Optional[str] = None) -> Dict[str, Any]:
    """Chronological events: corpus entries (added / resolved), by date.
    Optionally filtered to a site token."""
    corpus = _corpus()
    events = []
    resolved_by = {}
    for e in corpus:
        for rid in (e.get("resolves") or []):
            resolved_by[rid] = e["id"]
    for e in corpus:
        s = _site_of_entry(e)
        if site and s != site:
            continue
        events.append({
            "date": e.get("date", ""),
            "type": "corpus_entry",
            "id": e["id"],
            "site": s or "(framework)",
            "category": e.get("category"),
            "outcome": e.get("outcome"),
            "subject": e["subject"],
            "version": e.get("version"),
            "resolves": e.get("resolves") or [],
            "resolved_by": resolved_by.get(e["id"]),
        })
    events.sort(key=lambda x: (x["date"], x["id"]))
    return {"site": site, "n_events": len(events), "events": events}


def corpus_explorer(category: Optional[str] = None, outcome: Optional[str] = None,
                    site: Optional[str] = None, has_debt: Optional[bool] = None,
                    query: Optional[str] = None) -> Dict[str, Any]:
    """Browse the corpus like a DB with filters. Read-only."""
    corpus = _corpus()
    debt = _debt_obj()
    debt_ids = set(debt.get("validation_items", []) + debt.get("correction_items", []))
    rows = []
    for e in corpus:
        s = _site_of_entry(e)
        if category and e.get("category") != category:
            continue
        if outcome and e.get("outcome") != outcome:
            continue
        if site and s != site:
            continue
        if has_debt is not None and (e["id"] in debt_ids) != has_debt:
            continue
        if query:
            blob = " ".join(str(e.get(k, "")) for k in
                           ("id", "subject", "prediction", "observation", "notes")).lower()
            if query.lower() not in blob:
                continue
        rows.append({
            "id": e["id"], "date": e.get("date"), "version": e.get("version"),
            "category": e.get("category"), "outcome": e.get("outcome"),
            "conclusion_class": e.get("conclusion_class"),
            "site": s or "(framework)", "subject": e["subject"],
            "resolves": e.get("resolves") or [],
            "is_debt": e["id"] in debt_ids,
        })
    return {
        "n": len(rows), "rows": rows,
        "facets": {
            "categories": sorted({e.get("category") for e in corpus if e.get("category")}),
            "outcomes": sorted({e.get("outcome") for e in corpus if e.get("outcome")}),
            "sites": sorted({_site_of_entry(e) for e in corpus
                            if _site_of_entry(e)}),
        },
    }


def corpus_entry(entry_id: str) -> Optional[Dict[str, Any]]:
    for e in _corpus():
        if e["id"] == entry_id:
            # full entry, plus what resolves it / what it resolves
            resolved_by = [x["id"] for x in _corpus() if entry_id in (x.get("resolves") or [])]
            return {"entry": e, "resolved_by": resolved_by}
    return None


def drift_ops() -> Dict[str, Any]:
    """Drift operations center: drift_verdict corpus entries, severity-ranked."""
    corpus = _corpus()
    rank = {"falsified": 3, "partial": 2, "confirmed": 1, "untested": 0}
    drift = []
    for e in corpus:
        if e.get("category") != "drift_verdict":
            continue
        drift.append({
            "id": e["id"], "date": e.get("date"),
            "subject": e["subject"], "outcome": e.get("outcome"),
            "site": _site_of_entry(e) or "(framework)",
            "severity": rank.get(e.get("outcome"), 0),
        })
    drift.sort(key=lambda x: -x["severity"])
    return {"n": len(drift), "drift": drift,
            "_note": "Drift recorded in the corpus. Repeated/structural/selector "
                     "drift are distinguished by subject; severity ranks by outcome."}


def risk_board() -> Dict[str, Any]:
    """Top assumptions, weakest evidence, open debt — for prioritization."""
    corpus = _corpus()
    debt = _debt_obj()
    debt_ids = set(debt.get("validation_items", []) + debt.get("correction_items", []))
    assumptions = [{"id": e["id"], "subject": e["subject"], "outcome": e.get("outcome"),
                    "site": _site_of_entry(e) or "(framework)"}
                   for e in corpus if e.get("category") == "assumption"]
    weakest = [{"id": e["id"], "subject": e["subject"],
                "failure_class": e.get("failure_class")}
               for e in corpus
               if e.get("outcome") in ("untested", "partial") and e["id"] in debt_ids]
    return {
        "open_debt": {"correction": debt.get("correction"),
                      "capability": debt.get("capability"),
                      "validation": debt.get("validation"),
                      "items": sorted(debt_ids)},
        "assumptions": assumptions,
        "weakest_evidence": weakest,
        "_note": "Read-only prioritization view over the corpus + debt.",
    }


def site_intelligence(site: str) -> Dict[str, Any]:
    """One page per site: everything known, drawn from corpus + any reports.
    Read-only; degrades gracefully when reports are absent."""
    site = v_site(site)  # validate the token
    corpus = _corpus()
    entries = [e for e in corpus if _site_of_entry(e) == site]
    # look for any per-site report artifacts under reports root
    profile = {}
    rendition = {}
    for root in (reports_root(), tasks_root()):
        if not root.is_dir():
            continue
        for f in root.rglob("site_profile.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if d.get("site") == site or site in str(f):
                    profile = d
                    break
            except Exception:
                pass
        for f in root.rglob("rendition_profile.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if d.get("site") == site or site in str(f):
                    rendition = d
                    break
            except Exception:
                pass
    # corpus-derived concerns
    debt = _debt_obj()
    debt_ids = set(debt.get("validation_items", []) + debt.get("correction_items", []))
    concerns = [{"id": e["id"], "subject": e["subject"], "outcome": e.get("outcome")}
                for e in entries if e["id"] in debt_ids]
    return {
        "site": site,
        "corpus_entries": [{"id": e["id"], "date": e.get("date"),
                           "category": e.get("category"), "outcome": e.get("outcome"),
                           "subject": e["subject"]} for e in entries],
        "n_corpus_entries": len(entries),
        "drift_history": profile.get("drift_history", []),
        "confidence_history": profile.get("confidence_history", []),
        "known_rendition_descriptors": (rendition.get("observed_rendition_descriptors")
                                        or profile.get("known_rendition_descriptors", [])),
        "known_identity_descriptors": profile.get("known_identity_descriptors", []),
        "known_signing_markers": profile.get("known_signing_markers", []),
        "open_concerns": concerns,
        "has_profile": bool(profile),
        "_note": "Read-only. Profile fields are blank until a site_learning report "
                 "exists for this site.",
    }


def _scandir_files(root: Path):
    """Yield every regular FILE under root as a Path, via an iterative os.scandir
    walk (v3.66.464). Faithful drop-in for (f for f in root.rglob("*") if
    f.is_file()) but without rglob's per-directory Path churn + re-stat: scandir
    reuses the DirEntry.is_dir/is_file flags cached from readdir and builds a Path
    only for files. ~2.2x faster on dir-heavy trees -- growth-prevention for the
    full-tree posture/search walks. Ordering is arbitrary (as plain rglob already
    was); every consumer here is order-independent."""
    stack = [str(root)]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        yield Path(e.path)
                except OSError:
                    continue


def smart_search(query: str) -> Dict[str, Any]:
    """Search across corpus + report filenames + capture filenames. Read-only."""
    q = (query or "").strip().lower()
    if not q:
        return {"query": query, "results": []}
    results = []
    # corpus
    for e in _corpus():
        blob = " ".join(str(e.get(k, "")) for k in
                       ("id", "subject", "prediction", "observation", "notes")).lower()
        if q in blob:
            results.append({"kind": "corpus", "id": e["id"],
                           "title": f"{e['id']} — {e['subject']}",
                           "outcome": e.get("outcome")})
    # reports + captures by filename
    for root, kind in ((reports_root(), "report"), (tasks_root(), "report"),
                       (captures_root(), "capture")):
        if root.is_dir():
            for f in _scandir_files(root):
                if q in f.name.lower():
                    results.append({"kind": kind, "title": f.name,
                                   "path": str(f.relative_to(root))})
    return {"query": query, "n": len(results), "results": results[:100]}


def artifact_warehouse() -> Dict[str, Any]:
    """Central browser of all artifacts, categorized. Read-only."""
    cats: Dict[str, List[Dict[str, Any]]] = {
        "Captures": [], "Reports": [], "Validation packets": [], "Other": [],
    }
    def _cat(name: str) -> str:
        low = name.lower()
        if low.endswith(".wacz"):
            return "Captures"
        if "review_packet" in low or "validation_readiness" in low or "corpus_candidate" in low:
            return "Validation packets"
        if low.endswith((".md", ".json")):
            return "Reports"
        return "Other"
    for root, label in ((captures_root(), "captures"), (reports_root(), "reports"),
                        (tasks_root(), "tasks")):
        if not root.is_dir():
            continue
        # Iterative os.scandir walk: one cached DirEntry.stat() per file, no
        # full-tree Path materialize and no global sort -- the old
        # materialize-and-sort traversal was ~84% of the warehouse cost
        # (BUG-2 ~10s). Relative path via a cheap string slice off the absolute
        # entry path. ~4.5x faster on a large tree. Per-category lists are
        # sorted at the end so the listing stays deterministic (a small sort
        # over dicts, not Paths).
        root_str = str(root)
        prefix = len(root_str) + 1
        stack = [root_str]
        while stack:
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                            continue
                        if not e.is_file(follow_symlinks=False):
                            continue
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    rel = e.path[prefix:] if e.path.startswith(root_str) else e.name
                    cats[_cat(e.name)].append({
                        "name": e.name,
                        "path": rel,
                        "root": label,
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                    })
    for _lst in cats.values():
        _lst.sort(key=lambda f: (f["root"], f["path"]))
    return {"categories": {k: v for k, v in cats.items() if v},
            "_note": "Read-only warehouse. Preview opens the file through the "
                     "redacted report viewer."}


# ─────────────────────────────────────────────────────────────────────────────
# WAVE 2 — operator state (campaigns / queue / notebook / review / packets)
# ─────────────────────────────────────────────────────────────────────────────
# A small JSON-backed store under the tasks root. EVERYTHING here is inert DATA:
# nothing stored ever executes. A queue item is a plan; turning a plan into a
# capture still goes through the validated, allowlisted capture path (start_task)
# one at a time — the queue does not run anything itself. Reviews record an
# operator's decision; they never apply it (no corpus write, no selector promote,
# no profile update). This keeps the human gate intact.

import threading as _threading
_STORE_LOCK = _threading.Lock()


def _store_path() -> Path:
    return tasks_root() / "operator_state.json"


def _store_load() -> Dict[str, Any]:
    p = _store_path()
    if not p.is_file():
        return {"campaigns": {}, "queue": [], "notes": {}, "reviews": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"campaigns": {}, "queue": [], "notes": {}, "reviews": {}}
    for k in ("campaigns", "notes", "reviews"):
        d.setdefault(k, {})
    d.setdefault("queue", [])
    return d


def _store_save(state: Dict[str, Any]) -> None:
    """Atomic write per the project's .tmp + replace state-file invariant."""
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(p)


# --- Capture Campaigns -------------------------------------------------------

_CAMPAIGN_GOALS = ("n3_validation", "player_config", "workflow",
                   "cross_title_specificity", "general")


def campaign_create(name: Any, goal: Any, site: Any = None,
                    notes: Any = "") -> Dict[str, Any]:
    name = v_label(name)
    if goal not in _CAMPAIGN_GOALS:
        raise ValidationError(f"goal must be one of {_CAMPAIGN_GOALS}, got {goal!r}")
    s = v_site(site) if site else None
    with _STORE_LOCK:
        st = _store_load()
        cid = "camp_" + uuid.uuid4().hex[:10]
        st["campaigns"][cid] = {
            "id": cid, "name": name, "goal": goal, "site": s,
            "notes": str(notes)[:500], "created": time.time(),
            "capture_labels": [],
        }
        _store_save(st)
        return st["campaigns"][cid]


def campaign_list() -> List[Dict[str, Any]]:
    st = _store_load()
    out = []
    for c in st["campaigns"].values():
        out.append({**c, **_campaign_progress(c)})
    return sorted(out, key=lambda c: -c.get("created", 0))


def _campaign_progress(c: Dict[str, Any]) -> Dict[str, Any]:
    """Evidence collected / missing for a campaign, derived from captures present
    under the root + the corpus. Read-only and advisory."""
    caps = []
    try:
        caps = [p.name for p in captures_root().glob("*.wacz")] \
            if captures_root().is_dir() else []
    except Exception:
        pass
    # captures that look related to this campaign's site/labels
    site = c.get("site")
    related = [n for n in caps if (site and site in n.lower())
               or any(lbl in n for lbl in c.get("capture_labels", []))]
    goal = c.get("goal")
    # what the goal needs (advisory, recognition-only)
    need = {
        "n3_validation": "3 distinct same-title captures (different renditions)",
        "player_config": "2-3 same-title captures at different qualities",
        "workflow": "2-3 same-title captures via different navigation routes",
        "cross_title_specificity": "captures of 2+ different titles on the same site",
        "general": "captures as planned",
    }.get(goal, "captures as planned")
    distinct = len({n for n in related})
    done = (goal == "n3_validation" and distinct >= 3) or \
           (goal in ("player_config", "workflow") and distinct >= 2) or \
           (goal == "cross_title_specificity" and distinct >= 2)
    return {
        "evidence_collected": related,
        "evidence_count": len(related),
        "evidence_needed": need,
        "looks_complete": done,
        "recommended_next": ("review the collected evidence" if done
                             else f"capture more — {need}"),
    }


def campaign_attach_label(cid: Any, label: Any) -> Dict[str, Any]:
    cid = str(cid); label = v_label(label)
    with _STORE_LOCK:
        st = _store_load()
        c = st["campaigns"].get(cid)
        if not c:
            raise ValidationError(f"no such campaign: {cid}")
        if label not in c["capture_labels"]:
            c["capture_labels"].append(label)
        _store_save(st)
        return c


# --- Capture Queue Manager ---------------------------------------------------

_QUEUE_STATES = ("pending", "running", "completed", "failed", "requires_review")


def queue_add(site: Any, label: Any, url: Any = "", axis: Any = None,
              priority: Any = "medium", campaign: Any = None) -> Dict[str, Any]:
    """Add a PLAN item to the queue. This does NOT run anything — it is a
    reviewable intention. Running it is a separate, explicit, validated step."""
    item = {
        "id": "q_" + uuid.uuid4().hex[:10],
        "site": v_site(site),
        "label": v_label(label),
        "url": v_url(url) if url else "",
        "axis": v_axis(axis),
        "priority": priority if priority in ("low", "medium", "high") else "medium",
        "campaign": str(campaign) if campaign else None,
        "state": "pending",
        "order": 0,
        "created": time.time(),
        "task_id": None,
    }
    with _STORE_LOCK:
        st = _store_load()
        item["order"] = len(st["queue"])
        st["queue"].append(item)
        _store_save(st)
        return item


def queue_list() -> Dict[str, Any]:
    st = _store_load()
    q = sorted(st["queue"], key=lambda i: (i.get("order", 0), i.get("created", 0)))
    by_state = {s: [i for i in q if i["state"] == s] for s in _QUEUE_STATES}
    return {"queue": q, "by_state": by_state,
            "_note": "A queue item is a PLAN. Nothing runs from the queue itself; "
                     "launching a capture is a separate validated, human action."}


def queue_reorder(order_ids: List[str]) -> Dict[str, Any]:
    """Set explicit ordering (drag-and-drop result). Pure data update."""
    if not isinstance(order_ids, list):
        raise ValidationError("order must be a list of queue ids")
    with _STORE_LOCK:
        st = _store_load()
        pos = {qid: i for i, qid in enumerate(order_ids)}
        for it in st["queue"]:
            if it["id"] in pos:
                it["order"] = pos[it["id"]]
        _store_save(st)
    return queue_list()


def queue_set_state(qid: Any, state: Any) -> Dict[str, Any]:
    qid = str(qid)
    if state not in _QUEUE_STATES:
        raise ValidationError(f"state must be one of {_QUEUE_STATES}")
    with _STORE_LOCK:
        st = _store_load()
        for it in st["queue"]:
            if it["id"] == qid:
                it["state"] = state
                _store_save(st)
                return it
    raise ValidationError(f"no such queue item: {qid}")


def queue_launch(qid: Any) -> Dict[str, Any]:
    """Launch a queued item — by routing through the SAME validated, allowlisted
    capture path as a manual capture. The queue holds intent; this is the
    explicit run. Returns the started task record."""
    qid = str(qid)
    with _STORE_LOCK:
        st = _store_load()
        item = next((i for i in st["queue"] if i["id"] == qid), None)
        if not item:
            raise ValidationError(f"no such queue item: {qid}")
    # build params for capture_session from the (already-validated) queue item
    params = {"url": item["url"], "label": item["label"], "autofill": True}
    rec = start_task("capture", "capture_session", params)  # validates again
    with _STORE_LOCK:
        st = _store_load()
        for it in st["queue"]:
            if it["id"] == qid:
                it["state"] = "running"
                it["task_id"] = rec["task_id"]
        _store_save(st)
    return rec


# --- Operator Notebook (per-site notes, separate from corpus) ----------------

def note_add(site: Any, kind: Any, text: Any) -> Dict[str, Any]:
    site = v_site(site)
    if kind not in ("observation", "hypothesis", "followup", "capture_plan"):
        raise ValidationError("kind must be observation/hypothesis/followup/capture_plan")
    with _STORE_LOCK:
        st = _store_load()
        st["notes"].setdefault(site, [])
        note = {"id": "n_" + uuid.uuid4().hex[:8], "kind": kind,
                "text": str(text)[:2000], "created": time.time()}
        st["notes"][site].append(note)
        _store_save(st)
        return note


def note_list(site: Any) -> Dict[str, Any]:
    site = v_site(site)
    st = _store_load()
    return {"site": site, "notes": st["notes"].get(site, []),
            "_note": "Operator notes are separate from the corpus and never feed "
                     "it automatically."}


def notes_all_sites() -> List[str]:
    return sorted(_store_load()["notes"].keys())


# --- Review Workbench (records decisions; never applies them) ----------------

def review_items() -> Dict[str, Any]:
    """Surface everything awaiting review: corpus candidate suggestions found on
    disk + queue items flagged requires_review. NOTHING is applied here."""
    candidates = []
    for root, lbl in ((reports_root(), "reports"), (tasks_root(), "tasks")):
        if not root.is_dir():
            continue
        for pat in ("corpus_candidate_entry.json", "suggested_corpus_entry.json"):
            for f in root.rglob(pat):
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                    candidates.append({
                        "kind": "corpus_candidate",
                        "path": str(f.relative_to(root)), "root": lbl,
                        "subject": d.get("subject"), "outcome": d.get("outcome"),
                        "resolves": d.get("resolves") or [],
                    })
                except Exception:
                    pass
    st = _store_load()
    flagged = [i for i in st["queue"] if i["state"] == "requires_review"]
    decisions = st["reviews"]
    return {
        "corpus_candidates": candidates,
        "queue_requires_review": flagged,
        "decisions_recorded": decisions,
        "_note": "Review records your decision only. It NEVER writes the corpus, "
                 "promotes a selector, or updates a profile — those remain "
                 "deliberate, separate, human steps outside the cockpit.",
    }


def review_decide(item_key: Any, decision: Any, note: Any = "") -> Dict[str, Any]:
    """Record a review DECISION (accept/reject/defer) as data. For an inert review item this
    only records the note. If item_key names a PENDING guardrails change (e.g. a v1 staged
    config candidate), the accept/reject is ALSO routed through the audited guardrails review
    path: reject reverts immediately; accept stops the fail-closed clock WITHOUT promoting to
    live. Narrow: delegation fires only for a real pending change_id; otherwise behaves
    exactly as before."""
    item_key = str(item_key)[:200]
    if decision not in ("accept", "reject", "defer"):
        raise ValidationError("decision must be accept/reject/defer")
    delegated = None
    if decision in ("accept", "reject"):
        try:
            from tools import autonomy_guardrails as _agr
            rec = _agr.change_record(item_key)
            pending_ids = {v.get("change_id") for v in _agr.outstanding_unreviewed()}
            if rec is not None and item_key in pending_ids:
                delegated = _agr.mark_reviewed(item_key, decision, by="operator")
        except Exception:
            delegated = None
    with _STORE_LOCK:
        st = _store_load()
        st["reviews"][item_key] = {
            "decision": decision, "note": str(note)[:1000], "at": time.time(),
        }
        _store_save(st)
        out = {"item": item_key, **st["reviews"][item_key],
               "_note": "Decision recorded. No automatic action taken."}
        if delegated is not None:
            out["guardrails"] = delegated
            out["_note"] = ("Decision recorded AND routed to the audited guardrails review "
                            "path (reject reverts immediately; accept stops the fail-closed "
                            "clock). No promotion to live.")
        return out


# --- One-Click Review Packet Builder -----------------------------------------

def review_packet(site: Any) -> Dict[str, Any]:
    """Assemble a review packet for a site from existing data: timeline, corpus
    entries, drift history, candidates, recommended actions. Read-only — it
    PACKAGES existing artifacts; it does not generate new claims or write
    anything."""
    site = v_site(site)
    intel = site_intelligence(site)
    tl = evidence_timeline(site)
    candidates = [c for c in review_items()["corpus_candidates"]]
    notes = note_list(site)["notes"]
    # recommended actions = open concerns + any campaign recs
    actions = []
    for c in intel.get("open_concerns", []):
        actions.append(f"resolve open concern {c['id']} ({c['outcome']}): {c['subject']}")
    return {
        "site": site,
        "summary": {
            "corpus_entries": intel["n_corpus_entries"],
            "open_concerns": len(intel.get("open_concerns", [])),
            "timeline_events": tl["n_events"],
            "candidates_pending": len(candidates),
        },
        "timeline": tl["events"],
        "corpus_entries": intel["corpus_entries"],
        "open_concerns": intel.get("open_concerns", []),
        "candidates": candidates,
        "operator_notes": notes,
        "recommended_actions": actions,
        "_note": "Packaged from existing artifacts for review. No new claim is "
                 "made and nothing is written; recording any decision is a "
                 "separate, deliberate human step.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# WAVE 3 — composition + ops (release readiness / exec summary / coverage /
# resources / knowledge graph / evidence diff / health checks). All read-only.
# ─────────────────────────────────────────────────────────────────────────────

def release_readiness() -> Dict[str, Any]:
    """A read-only 'ready / not ready' preview before a release. It aggregates
    debt, risk, and a posture scan of recent artifacts. It does NOT build and
    does NOT bump the version — the authoritative drift/regression gate is
    tools/build_release.py, run deliberately on the host. This previews; it does
    not release."""
    debt = _debt_obj()
    risk = risk_board()
    # posture scan of recent text artifacts under the roots
    scanned, leaks = 0, 0
    leak_files = []
    for root in (reports_root(), tasks_root()):
        if not root.is_dir():
            continue
        for f in _scandir_files(root):
            if f.suffix in (".md", ".json", ".txt"):
                try:
                    found = posture_clean(f.read_text(encoding="utf-8", errors="replace"))
                    scanned += 1
                    if found:
                        leaks += 1
                        leak_files.append(str(f.name))
                except Exception:
                    pass
    correction = debt.get("correction", 0) or 0
    validation = debt.get("validation", 0) or 0
    # readiness verdict — conservative
    blockers = []
    if correction:
        blockers.append(f"{correction} correction debt item(s) open")
    if leaks:
        blockers.append(f"{leaks} artifact(s) with posture leaks")
    ready = not blockers
    return {
        "ready": ready,
        "verdict": "READY" if ready else "NOT READY",
        "blockers": blockers,
        "advisories": ([f"{validation} validation debt item(s) open"] if validation else []),
        "debt": debt,
        "risk_summary": {"correction": risk["open_debt"]["correction"],
                         "validation": risk["open_debt"]["validation"],
                         "weakest_evidence": len(risk["weakest_evidence"])},
        "posture_scan": {"artifacts_scanned": scanned, "with_leaks": leaks,
                         "leak_files": leak_files[:10]},
        "_note": "Read-only readiness preview. The authoritative regression + "
                 "drift gate is tools/build_release.py, run on the host; this "
                 "neither builds nor bumps the version.",
    }


def _changelog_releases(limit: int = 12) -> List[Dict[str, str]]:
    """Parse recent release headers + first line from CHANGELOG.md. Read-only."""
    p = _ROOT / "CHANGELOG.md"
    out = []
    if not p.is_file():
        return out
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    cur = None
    for ln in lines:
        if ln.startswith("## v"):
            if cur:
                out.append(cur)
            cur = {"version": ln[3:].strip(), "summary": ""}
        elif cur and not cur["summary"] and ln.strip() and not ln.startswith(("#", "-", "*", "—")):
            cur["summary"] = ln.strip()[:160]
        if len(out) >= limit:
            break
    if cur and len(out) < limit:
        out.append(cur)
    return out[:limit]


def exec_summary(period: str = "all") -> Dict[str, Any]:
    """Executive summary from existing artifacts: corpus state, recent activity,
    debt, drift, releases. period ∈ {daily, weekly, release, validation, all}.
    Read-only; generated, never written."""
    corpus = _corpus()
    debt = _debt_obj()
    import datetime as _dt
    now = _dt.date.today()
    # period window for "recent" corpus activity
    window_days = {"daily": 1, "weekly": 7, "monthly": 30, "quarterly": 90,
                   "release": 30, "validation": 90}.get(period)
    recent = []
    for e in corpus:
        try:
            d = _dt.date.fromisoformat(e.get("date", ""))
        except Exception:
            continue
        if window_days is None or (now - d).days <= window_days:
            recent.append(e)
    by_outcome = {}
    for e in (recent if window_days else corpus):
        by_outcome[e.get("outcome")] = by_outcome.get(e.get("outcome"), 0) + 1
    drift = [e for e in (recent if window_days else corpus)
             if e.get("category") == "drift_verdict"]
    summary = {
        "period": period,
        "as_of": now.isoformat(),
        "corpus_total": len(corpus),
        "recent_entries": len(recent) if window_days else len(corpus),
        "recent_by_outcome": by_outcome,
        "debt": {"correction": debt.get("correction"),
                 "capability": debt.get("capability"),
                 "validation": debt.get("validation")},
        "recent_drift": [{"id": e["id"], "subject": e["subject"],
                          "outcome": e.get("outcome")} for e in drift][:10],
        "headline": _exec_headline(debt, by_outcome),
    }
    if period in ("release", "all"):
        summary["recent_releases"] = _changelog_releases(8)
    return summary


def _exec_headline(debt: Dict[str, Any], by_outcome: Dict[str, Any]) -> str:
    c = debt.get("correction", 0) or 0
    v = debt.get("validation", 0) or 0
    conf = by_outcome.get("confirmed", 0)
    if c:
        return f"{c} correction debt item(s) need attention before the next release."
    if v:
        return f"Clean on corrections; {v} validation debt item(s) await evidence."
    return f"No open correction or validation debt; {conf} confirmed result(s) on record."


def coverage_heatmap() -> Dict[str, Any]:
    """Evidence coverage: category × outcome grid + a support classification.
    confirmed = well-supported, partial = weak, untested = untested, debt = open.
    Read-only over the corpus."""
    corpus = _corpus()
    debt = _debt_obj()
    debt_ids = set(debt.get("validation_items", []) + debt.get("correction_items", []))
    cats = sorted({e.get("category") for e in corpus if e.get("category")})
    outcomes = ["confirmed", "partial", "untested", "falsified"]
    grid = {c: {o: 0 for o in outcomes} for c in cats}
    for e in corpus:
        c, o = e.get("category"), e.get("outcome")
        if c in grid and o in grid[c]:
            grid[c][o] += 1
    # support buckets
    well = sum(1 for e in corpus if e.get("outcome") == "confirmed")
    weak = sum(1 for e in corpus if e.get("outcome") == "partial")
    untested = sum(1 for e in corpus if e.get("outcome") == "untested")
    open_debt = len(debt_ids)
    return {
        "categories": cats,
        "outcomes": outcomes,
        "grid": grid,
        "support": {"well_supported": well, "weakly_supported": weak,
                    "untested": untested, "open_debt": open_debt},
        "_note": "Coverage over the validation corpus. Well-supported = confirmed; "
                 "weak = partial; untested = untested; open debt = validation/"
                 "correction debt items.",
    }


def resource_stats() -> Dict[str, Any]:
    """Read-only host/app resource snapshot for stash. CPU/RAM/disk from psutil
    (or /proc fallback); 'active captures' and 'queue depth' from the cockpit's
    own state — NOT a system process scan. This monitors; it does not control
    any process."""
    out = {"_note": "Read-only snapshot. No process is controlled or signalled."}
    # CPU + RAM
    try:
        import psutil  # type: ignore
        out["cpu_percent"] = psutil.cpu_percent(interval=0.2)
        vm = psutil.virtual_memory()
        out["mem_percent"] = vm.percent
        out["mem_used_gb"] = round(vm.used / 1e9, 2)
        out["mem_total_gb"] = round(vm.total / 1e9, 2)
        out["source"] = "psutil"
    except Exception:
        out["source"] = "proc"
        try:
            with open("/proc/meminfo", encoding="utf-8") as f:
                mi = {}
                for ln in f:
                    k, _, v = ln.partition(":")
                    mi[k.strip()] = v.strip()
            total = int(mi.get("MemTotal", "0 kB").split()[0])
            avail = int(mi.get("MemAvailable", "0 kB").split()[0])
            if total:
                out["mem_percent"] = round((total - avail) / total * 100, 1)
                out["mem_total_gb"] = round(total / 1e6, 2)
                out["mem_used_gb"] = round((total - avail) / 1e6, 2)
        except Exception:
            out["mem_percent"] = None
        out["cpu_percent"] = None  # accurate CPU% needs two samples; skip on /proc
    # disk
    try:
        import shutil as _sh
        du = _sh.disk_usage(str(captures_root()) if captures_root().exists() else "/")
        out["disk_percent"] = round(du.used / du.total * 100, 1)
        out["disk_free_gb"] = round(du.free / 1e9, 2)
    except Exception:
        out["disk_percent"] = None
    # app-level (from the cockpit's own state, not a system scan)
    tasks = list_tasks()
    out["active_captures"] = sum(1 for t in tasks
                                 if t["status"] == "running" and t["category"] == "capture")
    out["running_tasks"] = sum(1 for t in tasks if t["status"] == "running")
    try:
        q = queue_list()["by_state"]
        out["queue_depth"] = len(q.get("pending", []))
    except Exception:
        out["queue_depth"] = 0
    # throughput: completed capture tasks in the registry
    out["captures_completed"] = sum(1 for t in tasks
                                    if t["status"] == "succeeded" and t["category"] == "capture")
    return out


def knowledge_graph() -> Dict[str, Any]:
    """Build a graph of the corpus: nodes = entries (typed by category), edges =
    'resolves' relationships (resolution → debt it retires). Read-only; this is
    the corpus's own relationship structure, nothing inferred or written."""
    corpus = _corpus()
    debt = _debt_obj()
    debt_ids = set(debt.get("validation_items", []) + debt.get("correction_items", []))
    # node type from category + outcome
    def _ntype(e):
        cat = e.get("category")
        if cat == "assumption":
            return "assumption"
        if cat == "drift_verdict":
            return "drift"
        if e.get("conclusion_class") == "method_validation":
            return "validation"
        if e["id"] in debt_ids:
            return "debt"
        return "finding"
    nodes = []
    for e in corpus:
        nodes.append({
            "id": e["id"], "type": _ntype(e),
            "label": e["id"], "subject": e["subject"],
            "outcome": e.get("outcome"), "site": _site_of_entry(e) or "(framework)",
            "is_debt": e["id"] in debt_ids,
        })
    edges = []
    for e in corpus:
        for rid in (e.get("resolves") or []):
            edges.append({"from": e["id"], "to": rid, "kind": "resolves"})
    return {
        "nodes": nodes, "edges": edges,
        "n_nodes": len(nodes), "n_edges": len(edges),
        "types": ["assumption", "finding", "validation", "drift", "debt"],
        "_note": "The corpus's own resolves-relationship graph. Read-only; click "
                 "a node to inspect its entry. Nothing is inferred or written.",
    }


_DESCRIPTOR_FIELDS = ("known_rendition_descriptors", "observed_rendition_descriptors",
                      "known_identity_descriptors", "observed_identity_descriptors",
                      "known_signing_markers")


def _descriptors_of(capture_path: Path) -> Dict[str, Any]:
    """Pull POSTURE-SAFE descriptor sets from a capture: rendition/identity
    descriptor names and signing-marker NAMES only (never values). Every URL is
    query-stripped on the way out."""
    import re
    from bulk_downloader import capture_ingest as ci
    c = ci.load_capture(str(capture_path))
    nl = c.get("network_log") or []
    rends, idents, markers = set(), set(), set()
    _MEDIA = (".mp4", ".m4v", ".webm", ".ts", ".m3u8", ".mpd", ".mov")
    for ev in nl:
        u = ev.get("url", "") if isinstance(ev, dict) else ""
        # rendition descriptors (WxH on a media URL), names only
        for m in re.findall(r"\d{3,4}x\d{3,4}[^/?\"]*", u):
            if m.lower().endswith(_MEDIA):
                rends.add(m)
        # signing marker NAMES (the query keys), never values
        for key in re.findall(r"[?&]([a-z_]{2,20})=", u, flags=re.I):
            if key.lower() in ("token", "sig", "signature", "expires", "st",
                               "policy", "key", "hmac", "exp", "expiry"):
                markers.add(key.lower())
    return {"renditions": sorted(rends), "signing_markers": sorted(markers),
            "has_cookies": bool(c.get("cookies"))}


def evidence_diff(kind: str, a: str, b: str) -> Dict[str, Any]:
    """Diff two pieces of evidence. kind ∈ {capture, site}.
      capture: compare two captures' descriptor SETS (names/types only — never
               raw signing values; posture-safe).
      site:    compare two sites' corpus entry sets.
    Read-only; highlights only meaningful differences."""
    if kind == "capture":
        pa = confine(a, captures_root())
        pb = confine(b, captures_root())
        if pa is None or pb is None or not pa.is_file() or not pb.is_file():
            raise ValidationError("both captures must be files under the captures root")
        da, db = _descriptors_of(pa), _descriptors_of(pb)
        result = {
            "kind": "capture", "a": pa.name, "b": pb.name,
            "renditions": {
                "only_a": sorted(set(da["renditions"]) - set(db["renditions"])),
                "only_b": sorted(set(db["renditions"]) - set(da["renditions"])),
                "shared": sorted(set(da["renditions"]) & set(db["renditions"])),
            },
            "signing_markers": {
                "a": da["signing_markers"], "b": db["signing_markers"],
                "note": "marker NAMES only — values are never compared or shown",
            },
            "login": {"a": da["has_cookies"], "b": db["has_cookies"]},
        }
    elif kind == "site":
        a = v_site(a); b = v_site(b)
        ea = {e["id"] for e in _corpus() if _site_of_entry(e) == a}
        eb = {e["id"] for e in _corpus() if _site_of_entry(e) == b}
        result = {
            "kind": "site", "a": a, "b": b,
            "corpus_entries": {
                "only_a": sorted(ea - eb), "only_b": sorted(eb - ea),
                "shared": sorted(ea & eb),
                "count_a": len(ea), "count_b": len(eb),
            },
        }
    else:
        raise ValidationError("kind must be 'capture' or 'site'")
    result["_note"] = ("Diff highlights meaningful differences only. Posture: "
                       "signing values are never compared or displayed — only "
                       "marker names/types.")
    return result


def health_checks(run: bool = False) -> Dict[str, Any]:
    """Read-only health checks. With run=False, lists the checks. With run=True,
    recomputes the read-only views and writes a timestamped snapshot under the
    tasks root (atomic). NO browser activity, no capture, no mutation of corpus/
    selectors/profiles — these are dashboard refreshes only.

    There is intentionally NO always-on in-app scheduler (it would add
    background work at import). To schedule, call this from host cron; the
    snapshot file is the durable output."""
    checks = [
        {"id": "debt", "label": "Recalculate debt status"},
        {"id": "coverage", "label": "Refresh evidence coverage"},
        {"id": "risk", "label": "Refresh risk board"},
        {"id": "mission", "label": "Refresh mission control"},
    ]
    if not run:
        return {"checks": checks, "ran": False,
                "_note": "Read-only refreshes. Run on demand here or via host "
                         "cron; no always-on scheduler runs inside the app."}
    import datetime as _dt
    snapshot = {
        "at": _dt.datetime.now().isoformat(timespec="seconds"),
        "debt": _debt_obj(),
        "coverage": coverage_heatmap()["support"],
        "risk": {"correction": risk_board()["open_debt"]["correction"],
                 "validation": risk_board()["open_debt"]["validation"]},
        "mission": {"review_queue": _review_queue_count(),
                    "captures_present": len(list(captures_root().glob("*.wacz")))
                    if captures_root().is_dir() else 0},
    }
    # atomic write (.tmp + replace) per the state-file invariant
    p = tasks_root() / "health_snapshot.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    tmp.replace(p)
    return {"checks": checks, "ran": True, "snapshot": snapshot,
            "snapshot_path": str(p.name),
            "_note": "Snapshot written. No browser activity, no capture, no "
                     "corpus/selector/profile change."}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 COMPLETION (this list's Waves 1-3): the genuinely-new features.
# Already-shipped equivalents are NOT rebuilt: Compare Mode=evidence_diff,
# Site Health=site_intelligence, Drift Center=drift_ops, Risk Center=risk_board,
# Validation Center=coverage_heatmap, Evidence Explorer=corpus_explorer,
# Evidence Timeline=evidence_timeline, Knowledge Graph=knowledge_graph,
# Capture Campaigns=campaign_*. All new functions below are read-only except the
# saved-views and collections stores, which are inert data (like campaigns/notes
# — nothing executes from them).
# ─────────────────────────────────────────────────────────────────────────────

# --- Wave 1: prioritization engine (powers Priority Inbox, Daily Mission,
#     Next Best Action, Smart Notifications — one computation, four views) ------

def next_best_action() -> Dict[str, Any]:
    """Rank what the operator should attend to next, from current state. One
    engine; the inbox / daily-mission / notifications pages are all views of it.
    Read-only and advisory — it recommends, it does not act."""
    corpus = _corpus()
    debt = _debt_obj()
    debt_ids = set(debt.get("validation_items", []) + debt.get("correction_items", []))
    items: List[Dict[str, Any]] = []

    # 1) correction debt — highest priority (a recorded correction is open)
    for cid in debt.get("correction_items", []):
        e = next((x for x in corpus if x["id"] == cid), None)
        items.append({
            "priority": 1, "severity": "high", "kind": "correction_debt",
            "id": cid, "site": _site_of_entry(e) if e else None,
            "title": f"Correction debt open: {e['subject'] if e else cid}",
            "action": "Review and resolve the correction.",
        })

    # 2) posture leaks in artifacts — high (must not ship)
    leaks = 0
    for root in (reports_root(), tasks_root()):
        if root.is_dir():
            for f in _scandir_files(root):
                if f.suffix in (".md", ".json", ".txt"):
                    try:
                        if posture_clean(f.read_text(encoding="utf-8", errors="replace")):
                            leaks += 1
                    except Exception:
                        pass
    if leaks:
        items.append({"priority": 1, "severity": "high", "kind": "posture",
                      "title": f"{leaks} artifact(s) with posture leaks",
                      "action": "Scrub or quarantine before any release."})

    # 3) pending reviews — medium
    rq = _review_queue_count()
    if rq:
        items.append({"priority": 2, "severity": "medium", "kind": "review",
                      "title": f"{rq} corpus candidate(s) awaiting review",
                      "action": "Open the Review Workbench."})

    # 4) validation debt — medium (untested axes await evidence)
    for vid in debt.get("validation_items", []):
        e = next((x for x in corpus if x["id"] == vid), None)
        items.append({"priority": 2, "severity": "medium", "kind": "validation_debt",
                      "id": vid, "site": _site_of_entry(e) if e else None,
                      "title": f"Validation debt: {e['subject'] if e else vid}",
                      "action": "Capture evidence to validate this axis."})

    # 5) failed tasks — medium
    for t in [t for t in list_tasks() if t["status"] == "failed"][:5]:
        items.append({"priority": 2, "severity": "medium", "kind": "failed_task",
                      "id": t["task_id"], "title": f"Task failed: {t['label']}",
                      "action": "Check the task log."})

    # 6) campaigns close to complete — low (nudge to finish/review)
    try:
        for c in campaign_list():
            if c.get("looks_complete"):
                items.append({"priority": 3, "severity": "low", "kind": "campaign",
                              "id": c["id"], "site": c.get("site"),
                              "title": f"Campaign ready to review: {c['name']}",
                              "action": c.get("recommended_next", "Review the evidence.")})
    except Exception:
        pass

    items.sort(key=lambda x: (x["priority"], x["kind"]))
    top = items[0] if items else None
    return {
        "items": items,
        "next_best": top,
        "counts": {"high": sum(1 for i in items if i["severity"] == "high"),
                   "medium": sum(1 for i in items if i["severity"] == "medium"),
                   "low": sum(1 for i in items if i["severity"] == "low")},
        "_note": "Advisory prioritization from current state. Recommends only; "
                 "nothing is acted on automatically.",
    }


def daily_mission() -> Dict[str, Any]:
    """Today's focus: the top few prioritized items + a one-line mission. View
    of next_best_action."""
    nba = next_best_action()
    top3 = nba["items"][:3]
    if not top3:
        mission = "All clear — no open debt, leaks, or pending reviews. Capture or explore."
    else:
        mission = top3[0]["title"]
    return {"mission": mission, "focus": top3, "counts": nba["counts"],
            "_note": "Derived from the prioritization engine. Advisory."}


def smart_notifications() -> Dict[str, Any]:
    """High/medium items as dismissible-style alerts. View of next_best_action;
    no push, no external delivery — surfaced in-GUI only."""
    nba = next_best_action()
    alerts = [{"severity": i["severity"], "title": i["title"], "kind": i["kind"]}
              for i in nba["items"] if i["severity"] in ("high", "medium")]
    return {"alerts": alerts, "n": len(alerts),
            "_note": "In-GUI alerts derived from current state. No external push."}


def activity_feed(limit: int = 50) -> Dict[str, Any]:
    """Operational event stream: tasks started/finished, reviews decided, queue
    launches, health snapshots. Distinct from the Evidence Timeline (which is
    corpus events). Read-only, assembled from the task registry + state store."""
    events: List[Dict[str, Any]] = []
    for t in list_tasks():
        if t.get("started"):
            events.append({"at": t["started"], "kind": "task_started",
                           "text": f"Started {t['label']}", "ref": t["task_id"]})
        if t.get("finished"):
            events.append({"at": t["finished"],
                           "kind": "task_" + ("succeeded" if t["status"] == "succeeded" else "failed"),
                           "text": f"{t['label']} {t['status']}"
                                   + (f" (rc={t.get('returncode')})" if t["status"] != "succeeded" else ""),
                           "ref": t["task_id"]})
    try:
        st = _store_load()
        for it in st.get("queue", []):
            if it.get("task_id"):
                events.append({"at": it.get("created", 0), "kind": "queue_launch",
                               "text": f"Queued capture launched: {it['label']}", "ref": it["id"]})
        for key, d in st.get("reviews", {}).items():
            events.append({"at": d.get("at", 0), "kind": "review_decision",
                           "text": f"Review {d['decision']}: {key}", "ref": key})
    except Exception:
        pass
    # health snapshot timestamp, if present
    snap = tasks_root() / "health_snapshot.json"
    if snap.is_file():
        try:
            import datetime as _dt
            d = json.loads(snap.read_text(encoding="utf-8"))
            at = _dt.datetime.fromisoformat(d["at"]).timestamp()
            events.append({"at": at, "kind": "health_snapshot",
                           "text": "Health snapshot written", "ref": "health_snapshot.json"})
        except Exception:
            pass
    events.sort(key=lambda e: -(e.get("at") or 0))
    return {"events": events[:limit], "n": len(events),
            "_note": "Operational event stream (read-only)."}


# --- Wave 2: investigation workspace, review ROI, saved views ----------------

def investigation_workspace(site: str) -> Dict[str, Any]:
    """One workspace for investigating a site without leaving the cockpit:
    combines site intelligence + timeline + operator notes + recent captures +
    open concerns. Composition of existing read-only views (Context Panels are
    the inline sections here)."""
    site = v_site(site)
    intel = site_intelligence(site)
    tl = evidence_timeline(site)
    notes = note_list(site)["notes"]
    caps = []
    try:
        caps = sorted(n.name for n in captures_root().glob("*.wacz")
                      if site in n.name.lower()) if captures_root().is_dir() else []
    except Exception:
        pass
    return {
        "site": site,
        "panels": {
            "intelligence": {"corpus_entries": intel["n_corpus_entries"],
                             "open_concerns": intel.get("open_concerns", []),
                             "known_renditions": intel.get("known_rendition_descriptors", []),
                             "signing_markers": intel.get("known_signing_markers", [])},
            "timeline": tl["events"],
            "notes": notes,
            "captures": caps,
        },
        "_note": "Read-only investigation workspace; panels compose existing "
                 "views. Nothing here writes or acts.",
    }


def review_roi() -> Dict[str, Any]:
    """Review ROI / effectiveness: how review effort relates to debt retired.
    Derived from the corpus (resolutions) + recorded review decisions. Read-only
    and approximate — a planning signal, not an accounting figure."""
    corpus = _corpus()
    resolutions = [e for e in corpus if e.get("resolves")]
    retired = sum(len(e.get("resolves") or []) for e in resolutions)
    try:
        decisions = _store_load().get("reviews", {})
    except Exception:
        decisions = {}
    accepted = sum(1 for d in decisions.values() if d.get("decision") == "accept")
    rejected = sum(1 for d in decisions.values() if d.get("decision") == "reject")
    deferred = sum(1 for d in decisions.values() if d.get("decision") == "defer")
    debt = _debt_obj()
    open_debt = (debt.get("validation", 0) or 0) + (debt.get("correction", 0) or 0)
    return {
        "debt_retired_total": retired,
        "resolution_entries": len(resolutions),
        "decisions": {"accept": accepted, "reject": rejected, "defer": deferred,
                      "total": len(decisions)},
        "open_debt": open_debt,
        "retire_ratio": round(retired / max(retired + open_debt, 1), 2),
        "_note": "Approximate ROI signal from corpus resolutions + recorded "
                 "review decisions. Read-only.",
    }


_SAVED_VIEW_KINDS = ("corpus", "search", "timeline", "site")


def saved_view_add(name: Any, kind: Any, params: Any) -> Dict[str, Any]:
    """Save a filter/search state for reuse. Pure inert data — a stored query,
    never an action. params is sanitized to a small flat dict of strings."""
    name = v_label(name)
    if kind not in _SAVED_VIEW_KINDS:
        raise ValidationError(f"kind must be one of {_SAVED_VIEW_KINDS}")
    if not isinstance(params, dict):
        raise ValidationError("params must be an object")
    # only keep short string values; reject metacharacter content defensively
    clean = {}
    for k, v in list(params.items())[:12]:
        sv = str(v)[:200]
        if any(c in sv for c in "`$;|&\n\r\x00"):
            raise ValidationError(f"param {k!r} contains illegal characters")
        clean[str(k)[:40]] = sv
    with _STORE_LOCK:
        st = _store_load()
        st.setdefault("saved_views", {})
        vid = "v_" + uuid.uuid4().hex[:10]
        st["saved_views"][vid] = {"id": vid, "name": name, "kind": kind,
                                  "params": clean, "created": time.time()}
        _store_save(st)
        return st["saved_views"][vid]


def saved_view_list() -> List[Dict[str, Any]]:
    st = _store_load()
    return sorted(st.get("saved_views", {}).values(),
                  key=lambda v: -v.get("created", 0))


def saved_view_delete(vid: Any) -> Dict[str, Any]:
    vid = str(vid)
    with _STORE_LOCK:
        st = _store_load()
        sv = st.setdefault("saved_views", {})
        existed = sv.pop(vid, None) is not None
        _store_save(st)
    return {"deleted": existed, "id": vid}


# --- Wave 3: decision trace (+ audit), assumption center, confidence
#     decomposition, evidence collections, lessons learned, org memory --------

def decision_trace(entry_id: str) -> Dict[str, Any]:
    """Trace a conclusion back through its resolves-chain to the evidence that
    supports it. Also serves Audit Mode (the provenance chain). Read-only over
    the corpus's own relationships — nothing inferred."""
    entry_id = str(entry_id)
    corpus = {e["id"]: e for e in _corpus()}
    if entry_id not in corpus:
        raise ValidationError(f"no such entry: {entry_id}")
    # walk 'resolves' forward (what this entry resolves) and backward (what
    # resolves it) to build the provenance chain
    chain = []
    seen = set()

    def _node(eid):
        e = corpus.get(eid)
        if not e:
            return {"id": eid, "missing": True}
        return {"id": eid, "subject": e["subject"], "outcome": e.get("outcome"),
                "category": e.get("category"), "date": e.get("date"),
                "evidence": e.get("evidence", "")[:300],
                "resolves": e.get("resolves") or []}

    def _walk(eid, depth=0):
        if eid in seen or depth > 12:
            return
        seen.add(eid)
        n = _node(eid)
        n["depth"] = depth
        chain.append(n)
        for rid in (corpus.get(eid, {}).get("resolves") or []):
            _walk(rid, depth + 1)

    _walk(entry_id)
    resolved_by = [x["id"] for x in _corpus() if entry_id in (x.get("resolves") or [])]
    return {
        "root": entry_id,
        "chain": chain,
        "resolved_by": resolved_by,
        "depth": max((n["depth"] for n in chain), default=0),
        "_note": "Provenance chain from the corpus's resolves relationships "
                 "(also the Audit Mode view). Read-only; nothing inferred.",
    }


def assumption_center() -> Dict[str, Any]:
    """All assumption-category entries with their validation status. Read-only."""
    corpus = _corpus()
    debt = _debt_obj()
    debt_ids = set(debt.get("validation_items", []) + debt.get("correction_items", []))
    rows = []
    for e in corpus:
        if e.get("category") != "assumption":
            continue
        rows.append({
            "id": e["id"], "subject": e["subject"], "outcome": e.get("outcome"),
            "site": _site_of_entry(e) or "(framework)", "date": e.get("date"),
            "status": ("open_debt" if e["id"] in debt_ids
                       else "validated" if e.get("outcome") == "confirmed"
                       else e.get("outcome")),
        })
    by_status: Dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {"assumptions": rows, "n": len(rows), "by_status": by_status,
            "_note": "Assumptions on record with validation status. Read-only."}


def confidence_decomposition() -> Dict[str, Any]:
    """Decompose what bounds confidence: confidence_cap + sensitivity_flag
    entries, plus the outcome mix. Explains why confidence is where it is.
    Read-only over the corpus."""
    corpus = _corpus()
    caps = [{"id": e["id"], "subject": e["subject"], "outcome": e.get("outcome")}
            for e in corpus if e.get("category") == "confidence_cap"]
    flags = [{"id": e["id"], "subject": e["subject"], "outcome": e.get("outcome")}
             for e in corpus if e.get("category") == "sensitivity_flag"]
    outcomes: Dict[str, int] = {}
    for e in corpus:
        outcomes[e.get("outcome")] = outcomes.get(e.get("outcome"), 0) + 1
    confirmed = outcomes.get("confirmed", 0)
    total = sum(outcomes.values()) or 1
    return {
        "confidence_caps": caps,
        "sensitivity_flags": flags,
        "outcome_mix": outcomes,
        "confirmed_fraction": round(confirmed / total, 2),
        "limiting_factors": ([c["subject"] for c in caps]
                             + [f["subject"] for f in flags]),
        "_note": "What bounds confidence: explicit caps + sensitivity flags + "
                 "outcome mix. Read-only.",
    }


_COLLECTION_RE = _LABEL_RE


def collection_create(name: Any, note: Any = "") -> Dict[str, Any]:
    name = v_label(name)
    with _STORE_LOCK:
        st = _store_load()
        st.setdefault("collections", {})
        cid = "col_" + uuid.uuid4().hex[:10]
        st["collections"][cid] = {"id": cid, "name": name, "note": str(note)[:500],
                                  "entry_ids": [], "created": time.time()}
        _store_save(st)
        return st["collections"][cid]


def collection_add(cid: Any, entry_id: Any) -> Dict[str, Any]:
    cid = str(cid)
    entry_id = str(entry_id)
    # entry must exist in the corpus (collections reference real evidence)
    if entry_id not in {e["id"] for e in _corpus()}:
        raise ValidationError(f"no such corpus entry: {entry_id}")
    with _STORE_LOCK:
        st = _store_load()
        c = st.get("collections", {}).get(cid)
        if not c:
            raise ValidationError(f"no such collection: {cid}")
        if entry_id not in c["entry_ids"]:
            c["entry_ids"].append(entry_id)
        _store_save(st)
        return c


def collection_list() -> List[Dict[str, Any]]:
    st = _store_load()
    return sorted(st.get("collections", {}).values(),
                  key=lambda c: -c.get("created", 0))


def lessons_learned() -> Dict[str, Any]:
    """Surface transferable lessons: the LESSONS_LEARNED.md doc if it ships in
    the tree (it is KB-only and usually excluded from the release zip), plus
    corpus entries that read as lessons (model_correction / capability_gap /
    method_validation conclusion classes). Read-only; degrades gracefully."""
    doc_present = False
    doc_excerpt = ""
    p = _ROOT / "LESSONS_LEARNED.md"
    if p.is_file():
        doc_present = True
        doc_excerpt = redact(p.read_text(encoding="utf-8", errors="replace"))[:4000]
    corpus_lessons = [
        {"id": e["id"], "subject": e["subject"],
         "conclusion_class": e.get("conclusion_class"), "outcome": e.get("outcome")}
        for e in _corpus()
        if e.get("conclusion_class") in ("model_correction", "capability_gap",
                                         "method_validation", "anomaly")
    ]
    return {
        "doc_present": doc_present,
        "doc_excerpt": doc_excerpt,
        "corpus_lessons": corpus_lessons,
        "n_corpus_lessons": len(corpus_lessons),
        "_note": "LESSONS_LEARNED.md is KB-only and usually excluded from the "
                 "release zip; when absent, corpus-derived lessons are shown. "
                 "Read-only.",
    }


def organizational_memory() -> Dict[str, Any]:
    """Aggregate institutional memory: corpus size + outcome mix, operator notes
    across sites, evidence collections, and the lessons summary. A single
    read-only 'what do we know' surface."""
    corpus = _corpus()
    outcomes: Dict[str, int] = {}
    for e in corpus:
        outcomes[e.get("outcome")] = outcomes.get(e.get("outcome"), 0) + 1
    try:
        note_sites = notes_all_sites()
        st = _store_load()
        n_notes = sum(len(v) for v in st.get("notes", {}).values())
        n_collections = len(st.get("collections", {}))
    except Exception:
        note_sites, n_notes, n_collections = [], 0, 0
    return {
        "corpus_entries": len(corpus),
        "outcome_mix": outcomes,
        "sites_with_notes": note_sites,
        "total_notes": n_notes,
        "collections": n_collections,
        "lessons": lessons_learned()["n_corpus_lessons"],
        "_note": "Aggregate institutional memory across corpus, notes, "
                 "collections, and lessons. Read-only.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# TRIVIAL reskins (Phase 2 audit): thin views/pivots/filters over data already
# exposed. No new capability — they regroup or summarize existing reads.
# ─────────────────────────────────────────────────────────────────────────────

def cross_site_drift() -> Dict[str, Any]:
    """Drift verdicts pivoted BY SITE (Wave 5 #42). Reskin of drift_ops grouped
    on the site dimension. Read-only."""
    d = drift_ops()["drift"]
    by_site: Dict[str, List[Dict[str, Any]]] = {}
    for x in d:
        by_site.setdefault(x["site"], []).append(x)
    rows = [{"site": s, "drift_count": len(v),
             "max_severity": max((i["severity"] for i in v), default=0),
             "items": v} for s, v in by_site.items()]
    rows.sort(key=lambda r: -r["max_severity"])
    return {"by_site": rows, "n_sites": len(rows),
            "_note": "Drift verdicts grouped by site (pivot of Drift Ops). "
                     "Read-only."}


def portfolio_ranking() -> Dict[str, Any]:
    """Rank sites by corpus volume + open concerns (Wave 5 #44). Sort over
    per-site signal already in site_intelligence. Read-only."""
    corpus = _corpus()
    debt = _debt_obj()
    debt_ids = set(debt.get("validation_items", []) + debt.get("correction_items", []))
    by_site: Dict[str, Dict[str, int]] = {}
    for e in corpus:
        s = _site_of_entry(e)
        if not s:
            continue
        d = by_site.setdefault(s, {"entries": 0, "concerns": 0, "confirmed": 0})
        d["entries"] += 1
        if e["id"] in debt_ids:
            d["concerns"] += 1
        if e.get("outcome") == "confirmed":
            d["confirmed"] += 1
    rows = [{"site": s, **v,
             "health": round(v["confirmed"] / max(v["entries"], 1), 2)}
            for s, v in by_site.items()]
    rows.sort(key=lambda r: (-r["entries"], r["concerns"]))
    return {"ranking": rows, "n_sites": len(rows),
            "_note": "Sites ranked by corpus volume + open concerns + a "
                     "confirmed-ratio health figure. Read-only."}


def blind_spots() -> Dict[str, Any]:
    """Under-supported areas (Wave 6 #56): untested assumptions + sites with no
    captures + categories with no confirmed evidence. Reskin of coverage +
    assumption data. Read-only."""
    corpus = _corpus()
    untested = [{"id": e["id"], "subject": e["subject"],
                 "site": _site_of_entry(e) or "(framework)"}
                for e in corpus if e.get("outcome") in ("untested", None)]
    # sites named in the corpus but with no captures under the root
    caps = set()
    try:
        if captures_root().is_dir():
            caps = {p.name.lower() for p in captures_root().glob("*.wacz")}
    except Exception:
        pass
    sites = {_site_of_entry(e) for e in corpus if _site_of_entry(e)}
    sites_no_capture = sorted(s for s in sites
                              if not any(s in c for c in caps))
    # categories lacking any confirmed entry
    cats: Dict[str, bool] = {}
    for e in corpus:
        c = e.get("category")
        if c:
            cats[c] = cats.get(c, False) or (e.get("outcome") == "confirmed")
    cats_no_confirmed = sorted(c for c, has in cats.items() if not has)
    return {
        "untested_assumptions": untested,
        "n_untested": len(untested),
        "sites_without_captures": sites_no_capture,
        "categories_without_confirmed_evidence": cats_no_confirmed,
        "_note": "Under-supported areas surfaced from coverage + assumption "
                 "data. Read-only planning signal.",
    }


def compliance_summary() -> Dict[str, Any]:
    """Compliance/posture summary (Wave 7 #61/#62): posture-scan rollup across
    artifacts + debt gate. Reskin of release_readiness's posture section, framed
    as standing compliance. Read-only."""
    rr = release_readiness()
    return {
        "posture": rr["posture_scan"],
        "correction_debt": rr["debt"].get("correction"),
        "verdict": "compliant" if (not rr["posture_scan"]["with_leaks"]
                                   and not rr["debt"].get("correction")) else "attention",
        "_note": "Standing posture/compliance rollup (artifacts scanned, leaks "
                 "withheld, correction debt). Read-only; the authoritative gate "
                 "is build_release.py.",
    }


def evidence_scarcity() -> Dict[str, Any]:
    """Evidence Scarcity Index (Wave 11 #104): where evidence is thinnest.
    Reskin of coverage 'untested' + per-site sparsity. Read-only."""
    cov = coverage_heatmap()["support"]
    pr = portfolio_ranking()["ranking"]
    total = sum(cov.values()) or 1
    scarcity = round((cov["untested"] + cov["open_debt"]) / total, 2)
    thin_sites = [r["site"] for r in pr if r["entries"] <= 2]
    return {
        "scarcity_index": scarcity,
        "untested": cov["untested"],
        "open_debt": cov["open_debt"],
        "well_supported": cov["well_supported"],
        "thinnest_sites": thin_sites,
        "_note": "Scarcity = (untested + open debt) / total corpus. Higher = "
                 "thinner evidence. Read-only.",
    }


def capture_yield() -> Dict[str, Any]:
    """Capture Yield (Wave 12 #115): captures present vs confirmed evidence they
    plausibly support. Reskin joining captures-on-disk with confirmed corpus
    entries by site. Read-only and approximate."""
    caps = []
    try:
        if captures_root().is_dir():
            caps = [p.name for p in captures_root().glob("*.wacz")]
    except Exception:
        pass
    corpus = _corpus()
    confirmed_by_site: Dict[str, int] = {}
    for e in corpus:
        if e.get("outcome") == "confirmed":
            s = _site_of_entry(e)
            if s:
                confirmed_by_site[s] = confirmed_by_site.get(s, 0) + 1
    return {
        "captures_present": len(caps),
        "confirmed_by_site": confirmed_by_site,
        "total_confirmed": sum(confirmed_by_site.values()),
        "_note": "Approximate yield: captures on disk vs confirmed corpus "
                 "entries by site. Read-only.",
    }


def decision_quality() -> Dict[str, Any]:
    """Decision Quality (Wave 7 #68): review decisions vs corpus resolution
    outcomes. Reskin over review_roi + decisions. Read-only."""
    roi = review_roi()
    corpus = _corpus()
    # of resolutions, how many landed confirmed vs falsified/partial
    resolutions = [e for e in corpus if e.get("resolves")]
    confirmed = sum(1 for e in resolutions if e.get("outcome") == "confirmed")
    return {
        "decisions_recorded": roi["decisions"]["total"],
        "accept": roi["decisions"]["accept"],
        "reject": roi["decisions"]["reject"],
        "defer": roi["decisions"]["defer"],
        "resolutions": len(resolutions),
        "resolutions_confirmed": confirmed,
        "confirm_rate": round(confirmed / max(len(resolutions), 1), 2),
        "_note": "Decision quality: recorded review decisions + the confirm-rate "
                 "of corpus resolutions. Read-only.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# BAND B — Cross-Site + What-If (genuinely-new, data exists). All read-only
# except the escalation store, which is inert flag data (like notes/collections).
# ─────────────────────────────────────────────────────────────────────────────

def impact_simulator(target: str) -> Dict[str, Any]:
    """What-if over the corpus's resolves-graph (Wave 6 #60 / Wave 13 #124).
    Given a corpus entry id OR a site, compute what DEPENDS on it — the
    transitive set of entries reachable through 'resolves' relationships — so you
    can see the blast radius if that thing changed/drifted. Read-only graph
    traversal over relationships already in the corpus; nothing is inferred,
    simulated probabilistically, or written."""
    target = str(target)
    corpus = {e["id"]: e for e in _corpus()}
    # build forward (a resolves b) and reverse (b is resolved-by a) adjacency
    fwd: Dict[str, List[str]] = {}
    rev: Dict[str, List[str]] = {}
    for e in corpus.values():
        for r in (e.get("resolves") or []):
            fwd.setdefault(e["id"], []).append(r)
            rev.setdefault(r, []).append(e["id"])

    # resolve target -> a set of seed entry ids (single entry, or all of a site)
    if target in corpus:
        seeds = [target]
        scope = "entry"
    else:
        site = v_site(target)
        seeds = [e["id"] for e in corpus.values() if _site_of_entry(e) == site]
        scope = "site"
        if not seeds:
            raise ValidationError(f"no entry or site matches {target!r}")

    def _closure(seedlist, adj):
        seen, stack = set(), list(seedlist)
        while stack:
            n = stack.pop()
            for m in adj.get(n, []):
                if m not in seen:
                    seen.add(m); stack.append(m)
        return seen

    # "depends on target" = things that resolve (transitively) the seeds (reverse)
    dependents = _closure(seeds, rev) - set(seeds)
    # "target depends on" = what the seeds resolve (forward)
    depends_on = _closure(seeds, fwd) - set(seeds)

    def _row(eid):
        e = corpus.get(eid, {})
        return {"id": eid, "subject": e.get("subject", "(missing)"),
                "site": _site_of_entry(e) or "(framework)",
                "outcome": e.get("outcome"), "category": e.get("category")}

    return {
        "target": target, "scope": scope, "seeds": seeds,
        "would_be_affected": [_row(i) for i in sorted(dependents)],
        "depends_on": [_row(i) for i in sorted(depends_on)],
        "blast_radius": len(dependents),
        "_note": "What-if blast radius over the corpus's resolves-graph: "
                 "'would be affected' = entries that (transitively) resolve the "
                 "target; 'depends on' = what the target resolves. Read-only "
                 "graph reachability — no probability, no simulation, no write.",
    }


def capture_opportunity() -> Dict[str, Any]:
    """Where to capture next (Wave 6 #55): rank sites/axes that lack evidence —
    open validation debt, untested assumptions, and sites with no captures on
    disk. Read-only prioritization over existing data; recommends, never acts."""
    corpus = _corpus()
    debt = _debt_obj()
    debt_ids = set(debt.get("validation_items", []))
    caps = set()
    try:
        if captures_root().is_dir():
            caps = {p.name.lower() for p in captures_root().glob("*.wacz")}
    except Exception:
        pass
    opportunities: List[Dict[str, Any]] = []
    # 1) open validation debt = a concrete capture target
    for e in corpus:
        if e["id"] in debt_ids:
            opportunities.append({
                "priority": 1, "site": _site_of_entry(e) or "(framework)",
                "reason": "open validation debt", "axis": e.get("subject"),
                "id": e["id"], "action": "Capture evidence to validate this axis."})
    # 2) untested assumptions
    for e in corpus:
        if e.get("category") == "assumption" and e.get("outcome") in ("untested", None) \
           and e["id"] not in debt_ids:
            opportunities.append({
                "priority": 2, "site": _site_of_entry(e) or "(framework)",
                "reason": "untested assumption", "axis": e.get("subject"),
                "id": e["id"], "action": "Capture to test this assumption."})
    # 3) sites named in the corpus with no capture on disk
    sites = {_site_of_entry(e) for e in corpus if _site_of_entry(e)}
    for s in sorted(sites):
        if not any(s in c for c in caps):
            opportunities.append({
                "priority": 3, "site": s, "reason": "no capture on disk",
                "axis": None, "id": None,
                "action": f"A fresh capture of {s} would establish baseline evidence."})
    opportunities.sort(key=lambda o: (o["priority"], o["site"]))
    return {
        "opportunities": opportunities,
        "n": len(opportunities),
        "by_priority": {p: sum(1 for o in opportunities if o["priority"] == p)
                        for p in (1, 2, 3)},
        "_note": "Capture opportunities ranked: open validation debt > untested "
                 "assumptions > sites without captures. Read-only; recommends "
                 "where evidence is missing, takes no action.",
    }


def _structural_profile(site: str) -> Dict[str, Any]:
    """Per-site structural fingerprint. Uses rich structural descriptors when
    captures have populated them (rendition descriptors, signing-marker names),
    and ALWAYS includes the corpus-category/conclusion-class profile, which
    exists without captures. The richer signal deepens automatically as captures
    are ingested."""
    si = site_intelligence(site)
    cats: Dict[str, int] = {}
    concl: Dict[str, int] = {}
    for e in _corpus():
        if _site_of_entry(e) != site:
            continue
        c = e.get("category")
        if c:
            cats[c] = cats.get(c, 0) + 1
        cc_ = e.get("conclusion_class")
        if cc_:
            concl[cc_] = concl.get(cc_, 0) + 1
    return {
        "site": site,
        # rich (capture-derived; empty until captures ingested)
        "rendition_descriptors": sorted(si.get("known_rendition_descriptors", [])),
        "signing_markers": sorted(si.get("known_signing_markers", [])),
        # always-present (corpus-derived)
        "categories": cats,
        "conclusion_classes": concl,
        "n_entries": si.get("n_corpus_entries", 0),
    }


def _profile_signature(p: Dict[str, Any]) -> set:
    """Set of feature tokens for similarity scoring. Combines rich + corpus
    signal so similarity works today (corpus tokens) and sharpens with captures
    (rendition/signing tokens)."""
    sig = set()
    for r in p["rendition_descriptors"]:
        sig.add("rend:" + r)
    for m in p["signing_markers"]:
        sig.add("sign:" + m)
    for c in p["categories"]:
        sig.add("cat:" + c)
    for c in p["conclusion_classes"]:
        sig.add("concl:" + c)
    return sig


def structural_similarity() -> Dict[str, Any]:
    """Pairwise structural similarity between sites (Wave 5 #49). Jaccard over a
    structural signature that uses capture-derived descriptors when present and
    corpus category/conclusion-class profile always. Read-only.

    Honesty note: with no captures ingested the signature is corpus-only (a
    thinner signal); it sharpens automatically once captures populate the
    rendition/signing descriptors."""
    corpus = _corpus()
    sites = sorted({_site_of_entry(e) for e in corpus if _site_of_entry(e)})
    profiles = {s: _structural_profile(s) for s in sites}
    sigs = {s: _profile_signature(profiles[s]) for s in sites}
    rich = any(profiles[s]["rendition_descriptors"] or profiles[s]["signing_markers"]
               for s in sites)
    pairs = []
    for i, a in enumerate(sites):
        for b in sites[i + 1:]:
            sa, sb = sigs[a], sigs[b]
            inter = len(sa & sb)
            union = len(sa | sb) or 1
            pairs.append({"a": a, "b": b, "similarity": round(inter / union, 2),
                          "shared": sorted(sa & sb)})
    pairs.sort(key=lambda p: -p["similarity"])
    return {
        "sites": sites, "pairs": pairs,
        "signal": "capture+corpus" if rich else "corpus-only",
        "_note": ("Pairwise Jaccard similarity over structural signatures. "
                  + ("Includes capture-derived descriptors." if rich else
                     "Currently corpus-only (no captures ingested yet); it "
                     "sharpens automatically once captures populate rendition/"
                     "signing descriptors.")),
    }


def family_explorer(threshold: float = 0.34) -> Dict[str, Any]:
    """Group sites into families by structural similarity (Wave 5 #41). A family
    = sites connected at/above the similarity threshold (single-linkage). Read-
    only. Inherits the same honesty caveat as structural_similarity."""
    sim = structural_similarity()
    sites = sim["sites"]
    # union-find over edges above threshold
    parent = {s: s for s in sites}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    for p in sim["pairs"]:
        if p["similarity"] >= threshold:
            union(p["a"], p["b"])
    fams: Dict[str, List[str]] = {}
    for s in sites:
        fams.setdefault(find(s), []).append(s)
    families = [{"family_id": f"fam_{i+1}", "members": sorted(m),
                 "size": len(m)} for i, m in enumerate(fams.values())]
    families.sort(key=lambda f: -f["size"])
    return {
        "families": families, "n_families": len(families),
        "threshold": threshold, "signal": sim["signal"],
        "_note": ("Sites grouped into families by structural similarity "
                  "(single-linkage at the threshold). " +
                  ("Corpus-only signal today; sharpens with captures."
                   if sim["signal"] == "corpus-only" else "")),
    }


def family_health() -> Dict[str, Any]:
    """Aggregate health per family (Wave 5 #43): corpus volume, confirmed ratio,
    open concerns, and drift across each family from family_explorer. Read-
    only."""
    fams = family_explorer()
    corpus = _corpus()
    debt = _debt_obj()
    debt_ids = set(debt.get("validation_items", []) + debt.get("correction_items", []))
    out = []
    for fam in fams["families"]:
        members = set(fam["members"])
        entries = [e for e in corpus if _site_of_entry(e) in members]
        confirmed = sum(1 for e in entries if e.get("outcome") == "confirmed")
        concerns = sum(1 for e in entries if e["id"] in debt_ids)
        drift = sum(1 for e in entries if e.get("category") == "drift_verdict")
        out.append({
            "family_id": fam["family_id"], "members": fam["members"],
            "entries": len(entries), "confirmed": confirmed, "concerns": concerns,
            "drift": drift,
            "health": round(confirmed / max(len(entries), 1), 2),
        })
    out.sort(key=lambda f: (-f["entries"], f["concerns"]))
    return {"families": out, "n_families": len(out), "signal": fams["signal"],
            "_note": "Per-family aggregate health (confirmed ratio) + open "
                     "concerns + drift. Read-only."}


# --- Escalation Workflows (#37): inert flag state on review/corpus items -------

def escalate(item_id: Any, reason: Any = "") -> Dict[str, Any]:
    """Flag a corpus/review item for escalation (Wave 4 #37). Pure inert data —
    a recorded flag for human attention; it triggers NO action, runs nothing,
    and does not touch the corpus. The item must reference a real corpus id."""
    item_id = str(item_id)
    if item_id not in {e["id"] for e in _corpus()}:
        raise ValidationError(f"no such corpus entry: {item_id}")
    reason = str(reason)[:500]
    if any(c in reason for c in "`$\x00"):
        raise ValidationError("reason contains illegal characters")
    with _STORE_LOCK:
        st = _store_load()
        esc = st.setdefault("escalations", {})
        esc[item_id] = {"item_id": item_id, "reason": reason,
                        "at": time.time(), "status": "open"}
        _store_save(st)
        return esc[item_id]


def escalation_list() -> Dict[str, Any]:
    st = _store_load()
    items = sorted(st.get("escalations", {}).values(),
                   key=lambda x: -x.get("at", 0))
    return {"escalations": items, "n_open": sum(1 for x in items
                                                if x.get("status") == "open"),
            "_note": "Flagged items awaiting human attention. Inert — flagging "
                     "triggers no action."}


def escalation_clear(item_id: Any) -> Dict[str, Any]:
    item_id = str(item_id)
    with _STORE_LOCK:
        st = _store_load()
        esc = st.setdefault("escalations", {})
        existed = esc.pop(item_id, None) is not None
        _store_save(st)
    return {"cleared": existed, "item_id": item_id}


# ─────────────────────────────────────────────────────────────────────────────
# BAND C — scoring models. Each is a TRANSPARENT composite: every input is a real
# observable, weights are explicit and equal, and the components are returned so
# the score is fully decomposable. These are DEFINED composites, not objective
# truths — the weights are a starting point the operator can change. All read-only.
# ─────────────────────────────────────────────────────────────────────────────

def maturity_score() -> Dict[str, Any]:
    """Framework maturity (Wave 8 #77) as a transparent composite of three real
    ratios (each 0–1, equal weight): validation coverage (confirmed/total),
    debt cleanliness (1 − open_debt/total), and resolution activity
    (resolutions/total, capped at 1). Score = mean × 100. Fully decomposable."""
    corpus = _corpus()
    total = len(corpus) or 1
    debt = _debt_obj()
    confirmed = sum(1 for e in corpus if e.get("outcome") == "confirmed")
    open_debt = (debt.get("validation", 0) or 0) + (debt.get("correction", 0) or 0)
    resolutions = sum(1 for e in corpus if e.get("resolves"))
    components = {
        "validation_coverage": round(confirmed / total, 3),
        "debt_cleanliness": round(1 - open_debt / total, 3),
        "resolution_activity": round(min(resolutions / total, 1.0), 3),
    }
    score = round(sum(components.values()) / len(components) * 100)
    band = ("mature" if score >= 70 else "developing" if score >= 40 else "nascent")
    return {
        "score": score, "band": band, "components": components,
        "weights": {k: round(1 / len(components), 3) for k in components},
        "inputs": {"corpus_total": total, "confirmed": confirmed,
                   "open_debt": open_debt, "resolutions": resolutions},
        "_note": "DEFINED composite (not an objective measure): mean of three "
                 "equal-weighted ratios × 100. Every input shown above; adjust "
                 "the weights to your judgment. Read-only.",
    }


def complexity_score() -> Dict[str, Any]:
    """Operational complexity (Wave 12 #112): the raw drivers of operational
    burden + a relative index. Higher = more to operate. Drivers are real counts
    (sites, categories, source types, corpus size). The index is a transparent
    normalized sum, NOT an absolute — it's meaningful as a relative/trend figure
    (and most useful once there's history). Read-only."""
    corpus = _corpus()
    sites = len({_site_of_entry(e) for e in corpus if _site_of_entry(e)})
    categories = len({e.get("category") for e in corpus if e.get("category")})
    source_types = 0
    try:
        from bulk_downloader import deep_detect as dd
        source_types = len(getattr(dd, "SOURCE_TYPES", []) or [])
    except Exception:
        pass
    drivers = {
        "sites": sites,
        "categories": categories,
        "source_types": source_types,
        "corpus_entries": len(corpus),
    }
    # relative index: each driver normalized against a soft reference, summed.
    # the references are documented, coarse, and adjustable — not magic.
    refs = {"sites": 20, "categories": 10, "source_types": 30, "corpus_entries": 200}
    index = round(sum(min(drivers[k] / refs[k], 1.0) for k in drivers)
                  / len(drivers) * 100)
    return {
        "complexity_index": index, "drivers": drivers, "references": refs,
        "_note": "DEFINED relative index: each driver normalized against a "
                 "documented soft reference, averaged × 100. It is a relative/"
                 "trend indicator, not an absolute complexity. Adjust references "
                 "to your environment. Read-only.",
    }


def org_health_index() -> Dict[str, Any]:
    """Organizational health (Wave 12 #120): a composite of maturity + evidence
    freshness + concern-freedom. Combines the maturity score with two more real
    ratios. Transparent and decomposable; read-only.

    Honesty note: 'freshness' uses recency of the newest corpus entry, which is a
    weak signal with only a few days of data — it sharpens as history grows."""
    mat = maturity_score()
    corpus = _corpus()
    debt = _debt_obj()
    total = len(corpus) or 1
    debt_ids = set(debt.get("validation_items", []) + debt.get("correction_items", []))
    concern_freedom = round(1 - len(debt_ids) / total, 3)
    # freshness: 1.0 if newest entry within 7 days, decaying to 0 by 90 days
    import datetime as _dt
    dates = [e.get("date", "")[:10] for e in corpus if e.get("date")]
    freshness = 0.0
    if dates:
        try:
            newest = max(_dt.date.fromisoformat(x) for x in dates if x)
            age = (_dt.date.today() - newest).days
            freshness = round(max(0.0, min(1.0, 1 - (age - 7) / 83)) if age > 7 else 1.0, 3)
        except Exception:
            freshness = 0.0
    components = {
        "maturity": round(mat["score"] / 100, 3),
        "concern_freedom": concern_freedom,
        "evidence_freshness": freshness,
    }
    score = round(sum(components.values()) / len(components) * 100)
    band = ("healthy" if score >= 70 else "watch" if score >= 40 else "at_risk")
    return {
        "score": score, "band": band, "components": components,
        "weights": {k: round(1 / len(components), 3) for k in components},
        "_note": "DEFINED composite: mean of maturity + concern-freedom + "
                 "evidence-freshness × 100. Freshness is a weak signal on few "
                 "days of data and sharpens with history. Read-only.",
    }


def portfolio_opportunity() -> Dict[str, Any]:
    """Per-site capture-opportunity rollup (Wave 13 #127). The one Band E item
    that adds real computation: capture_opportunity returns a flat gap list; this
    rolls it up BY SITE into a site-level opportunity score, so you can see which
    sites have the most missing evidence. Read-only."""
    co = capture_opportunity()
    by_site: Dict[str, Dict[str, Any]] = {}
    for o in co["opportunities"]:
        s = o["site"]
        d = by_site.setdefault(s, {"site": s, "p1": 0, "p2": 0, "p3": 0, "total": 0})
        d[f"p{o['priority']}"] += 1
        d["total"] += 1
    # opportunity score: P1 weighted heaviest (validation debt is the best target)
    rows = []
    for d in by_site.values():
        d["opportunity_score"] = d["p1"] * 3 + d["p2"] * 2 + d["p3"] * 1
        rows.append(d)
    rows.sort(key=lambda r: -r["opportunity_score"])
    return {"by_site": rows, "n_sites": len(rows),
            "_note": "Per-site rollup of capture opportunities (P1 validation "
                     "debt weighted heaviest). Read-only; recommends where the "
                     "most evidence is missing, takes no action."}


def operational_narrative() -> Dict[str, Any]:
    """Operational narrative (Wave 8 #79): deterministic prose summary assembled
    from the current state — NOT an LLM call, just honest templated prose from
    the real numbers (debt, drift, coverage, recent activity, maturity). Read-
    only; the text restates figures already on the dashboards."""
    mat = maturity_score()
    debt = _debt_obj()
    cov = coverage_heatmap()["support"]
    drift = drift_ops()
    corpus = _corpus()
    paras = []
    # 1) overall posture
    c = debt.get("correction", 0) or 0
    v = debt.get("validation", 0) or 0
    paras.append(
        f"The framework is at a {mat['band']} maturity ({mat['score']}/100), "
        f"with {len(corpus)} corpus entries and {cov['well_supported']} "
        f"well-supported result(s). "
        + ("There is no open correction debt." if not c
           else f"There are {c} correction-debt item(s) needing attention before release.")
        + (f" {v} validation-debt item(s) await evidence." if v else ""))
    # 2) coverage / scarcity
    paras.append(
        f"Evidence coverage shows {cov['well_supported']} confirmed, "
        f"{cov['weakly_supported']} partial, and {cov['untested']} untested area(s). "
        + ("Coverage is solid." if cov['untested'] == 0
           else f"The {cov['untested']} untested area(s) are the main gap."))
    # 3) drift
    nd = drift.get("n", 0)
    paras.append(
        f"Drift activity: {nd} verdict(s) recorded. "
        + ("No drift of concern." if nd == 0
           else "Recent drift has been recorded and resolved where evidence allowed."))
    return {"paragraphs": paras, "as_of": _now_iso(),
            "_note": "Deterministic prose from current figures (no model call). "
                     "Restates dashboard numbers in narrative form. Read-only."}


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now().isoformat(timespec="seconds")


# ─────────────────────────────────────────────────────────────────────────────
# Band F — Forecasting & Trends (v3.66.109). DATA-GATED BY DESIGN.
#
# Every metric in this band is longitudinal: it needs a real time-series to be
# honest. The corpus currently has only a few distinct days. Rather than emit a
# slope from 3 points — confident-looking output with no predictive validity,
# exactly the overconfidence the posture forbids — each metric COMPUTES ITS
# STRUCTURE but WITHHOLDS the projection until there is enough history, and says
# so plainly. The real computation is written and ready; it lights up
# automatically the day the corpus crosses the threshold. This is how Band F is
# "closed out": every remaining roadmap feature has an honest home, none of them
# fabricate. Read-only; NO model or network call anywhere in this band.
# ─────────────────────────────────────────────────────────────────────────────

_MIN_TREND_DAYS = 14         # minimum distinct days before any trend/forecast is honest
_MIN_CORRELATION_DAYS = 21   # cross-site correlation needs more before it is meaningful
_MIN_CALIBRATION_RECORDS = 20  # calibration needs logged forecast→outcome pairs


def _distinct_days() -> int:
    """Number of distinct calendar days represented in the corpus. The single
    fact that gates this whole band."""
    days = {(e.get("date") or "")[:10] for e in _corpus()}
    days.discard("")
    return len(days)


def _history_gate(min_days: int = _MIN_TREND_DAYS) -> Dict[str, Any]:
    """The shared data-sufficiency gate: is there enough distinct-day history for
    a longitudinal metric to be honest? Read-only."""
    have = _distinct_days()
    return {"sufficient": have >= min_days, "distinct_days": have,
            "required_days": min_days, "shortfall": max(0, min_days - have)}


def _series_by_day(pred=None) -> Dict[str, int]:
    """Corpus entry counts per calendar day (optionally filtered) — the raw
    material every forecast/trend would consume. Read-only."""
    out: Dict[str, int] = {}
    for e in _corpus():
        d = (e.get("date") or "")[:10]
        if not d or (pred and not pred(e)):
            continue
        out[d] = out.get(d, 0) + 1
    return dict(sorted(out.items()))


def _linear_projection(series: Dict[str, int]) -> Dict[str, Any]:
    """A deliberately modest, transparent projection used ONLY once the gate is
    open: trailing simple-moving-average + a least-squares slope over the daily
    counts. Clearly labelled as a naive linear estimate, not a model. No RNG."""
    pts = list(series.values())
    n = len(pts)
    if n < 2:
        return {"sma": pts[-1] if pts else 0, "slope_per_day": 0.0, "next_estimate": pts[-1] if pts else 0}
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(pts) / n
    denom = sum((x - mx) ** 2 for x in xs) or 1
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, pts)) / denom
    sma = round(sum(pts[-7:]) / len(pts[-7:]), 2)
    nxt = round(my + slope * (n + 1 - mx), 2)
    return {"sma_7": sma, "slope_per_day": round(slope, 3),
            "next_estimate": nxt,
            "_method": "naive least-squares slope over daily counts + 7-pt SMA; "
                       "a transparent baseline, not a trained forecaster"}


def _gated(feature: str, name: str, definition: str, gate: Dict[str, Any],
           computed=None) -> Dict[str, Any]:
    """Uniform envelope for a Band F metric. Gate open → 'value' carries the real
    computation; gate closed → value withheld with an honest reason. NEVER
    fabricates a number when the gate is closed."""
    out = {"feature": feature, "name": name, "definition": definition, "gate": gate}
    if gate.get("sufficient"):
        out["status"] = "available"
        try:
            out["value"] = computed() if callable(computed) else computed
        except Exception as e:  # pragma: no cover - defensive
            out["status"] = "error"
            out["value"] = None
            out["error"] = str(e)
    else:
        out["status"] = "insufficient_history"
        out["value"] = None
        have = gate.get("distinct_days", gate.get("records", 0))
        need = gate.get("required_days", gate.get("required_records", 0))
        unit = "distinct days" if "required_days" in gate else "logged forecast→outcome pairs"
        out["_note"] = (
            f"Withheld: longitudinal metric — needs \u2265{need} {unit} to be honest; "
            f"currently {have}. Projecting from {have} would be a slope with no "
            f"predictive validity, which the posture forbids. Populates automatically "
            f"at threshold. Read-only.")
    return out


def forecast_panel() -> Dict[str, Any]:
    """Forecasting metrics (#39 review, #51 risk, #52 drift, #53 stability,
    #54 capacity, #59 health, #75 strategic outlook). Each projects a corpus
    time-series forward — gated until there is real history. Read-only."""
    g = _history_gate(_MIN_TREND_DAYS)
    metrics = [
        _gated("51", "Risk forecast", "Project the count of open-risk / unconfirmed "
               "entries forward from its daily series.", g,
               lambda: _linear_projection(_series_by_day(
                   lambda e: e.get("outcome") not in ("confirmed",)))),
        _gated("52", "Drift forecast", "Project drift-verdict volume forward.", g,
               lambda: _linear_projection(_series_by_day(
                   lambda e: e.get("category") == "drift_verdict"))),
        _gated("53", "Stability forecast", "Project confirmed/total stability forward.", g),
        _gated("54", "Capacity forecast", "Project queue / capture demand forward.", g),
        _gated("59", "Health forecast", "Project the org-health index forward.", g),
        _gated("39", "Review forecast", "Project review volume forward.", g),
        _gated("75", "Strategic outlook", "Forward-looking narrative from the trend set.", g),
    ]
    return {"panel": "forecasting", "gate": g, "metrics": metrics,
            "_note": "Forecasting needs a real time-series. All metrics gate "
                     "together on distinct corpus days. Read-only; no model call."}


def trend_panel() -> Dict[str, Any]:
    """Trend / evolution metrics (#45 portfolio stability, #98 assumption-stability
    view, #99 trust evolution, #106 assumption-stability index, #107 knowledge
    decay, #108 framework trust trends, #128 long-term trend). Gated until
    history exists. Read-only."""
    g = _history_gate(_MIN_TREND_DAYS)
    metrics = [
        _gated("99", "Trust evolution", "Confidence/outcome mix over time.", g),
        _gated("108", "Framework trust trend", "Trust composite trajectory.", g),
        _gated("107", "Knowledge decay", "Age-out of unrefreshed confirmations.", g),
        _gated("106", "Assumption stability index", "Assumption churn over time.", g),
        _gated("98", "Assumption stability view", "Per-assumption stability series.", g),
        _gated("45", "Portfolio stability", "Per-site metric stability over time.", g),
        _gated("128", "Long-term trend", "Aggregate corpus trajectory.", g),
    ]
    return {"panel": "trends", "gate": g, "metrics": metrics,
            "_note": "Trends need history; gated on distinct corpus days. Read-only."}


def velocity_panel() -> Dict[str, Any]:
    """Rate-over-time metrics (#100 validation velocity, #113 maintenance burden,
    #114 review fatigue). Gated until history exists. Read-only."""
    g = _history_gate(_MIN_TREND_DAYS)
    metrics = [
        _gated("100", "Validation velocity", "Confirmations per day over time.", g,
               lambda: _linear_projection(_series_by_day(
                   lambda e: e.get("outcome") == "confirmed"))),
        _gated("113", "Maintenance burden", "Correction/drift work rate over time.", g),
        _gated("114", "Review fatigue", "Review throughput decline over time.", g),
    ]
    return {"panel": "velocity", "gate": g, "metrics": metrics,
            "_note": "Rates need history; gated on distinct corpus days. Read-only."}


def calibration_panel() -> Dict[str, Any]:
    """Calibration metrics (#101 confidence calibration, #102 forecast accuracy).
    These need LOGGED forecast→outcome pairs to score, which do not exist yet, so
    they gate on a forecast-record count rather than days. Read-only."""
    records = 0  # no forecast-logging subsystem yet; honest zero
    g = {"sufficient": records >= _MIN_CALIBRATION_RECORDS, "records": records,
         "required_records": _MIN_CALIBRATION_RECORDS,
         "shortfall": max(0, _MIN_CALIBRATION_RECORDS - records)}
    metrics = [
        _gated("101", "Confidence calibration", "Predicted vs. realised confidence "
               "(reliability curve).", g),
        _gated("102", "Forecast accuracy", "Scored error of past forecasts.", g),
    ]
    return {"panel": "calibration", "gate": g, "metrics": metrics,
            "_note": "Calibration needs logged forecast\u2192outcome pairs (none recorded "
                     "yet). It activates once forecasts are logged AND their outcomes "
                     "are known. Read-only."}


def sustainability_panel() -> Dict[str, Any]:
    """Sustainability / scaling / resilience metrics (#66 sustainability tracking,
    #70 framework resilience, #111 sustainability dashboard, #117 scaling risk,
    #118 sustainability stress test). Inherently longitudinal; gated. Read-only."""
    g = _history_gate(_MIN_TREND_DAYS)
    metrics = [
        _gated("111", "Sustainability dashboard", "Operating-load trajectory.", g),
        _gated("66", "Sustainability tracking", "Burden vs. throughput over time.", g),
        _gated("70", "Framework resilience", "Recovery from drift/failure over time.", g),
        _gated("117", "Scaling risk", "Load growth vs. capacity headroom.", g),
        _gated("118", "Sustainability stress test", "Projected load under growth "
               "scenarios (needs a real baseline trajectory).", g),
    ]
    return {"panel": "sustainability", "gate": g, "metrics": metrics,
            "_note": "Sustainability/scaling are longitudinal; gated on distinct "
                     "corpus days. Read-only."}


def correlation_panel() -> Dict[str, Any]:
    """Cross-site correlation (#50). Needs enough sites AND days to be statistically
    meaningful; gated on a higher day threshold. Read-only."""
    g = _history_gate(_MIN_CORRELATION_DAYS)
    sites = len({_site_of_entry(e) for e in _corpus() if _site_of_entry(e)})
    g["sites"] = sites
    metrics = [
        _gated("50", "Cross-site correlation", "Correlate per-site drift/health "
               "series across sites.", g),
    ]
    return {"panel": "correlation", "gate": g, "metrics": metrics,
            "_note": f"Correlation over {sites} site(s) needs \u2265{_MIN_CORRELATION_DAYS} "
                     "distinct days to be meaningful; gated. Read-only."}


def forecasting_overview() -> Dict[str, Any]:
    """Band F aggregate (v3.66.109): every data-blocked forecasting/trend/velocity/
    calibration/sustainability/correlation metric, each gated and honest. This is
    the single page that closes out Band F. Read-only; no model/network call."""
    panels = [forecast_panel(), trend_panel(), velocity_panel(),
              calibration_panel(), sustainability_panel(), correlation_panel()]
    total = sum(len(p["metrics"]) for p in panels)
    gated = sum(1 for p in panels for m in p["metrics"]
                if m["status"] != "available")
    return {
        "distinct_days": _distinct_days(),
        "min_trend_days": _MIN_TREND_DAYS,
        "panels": panels,
        "metric_count": total,
        "gated_count": gated,
        "_note": "Band F — forecasting/trend/sustainability features. These are "
                 "DATA-BLOCKED, not effort-blocked: each computes its structure but "
                 "withholds output until the corpus has a real time-series, instead "
                 "of projecting from a handful of points. They populate automatically "
                 "as history accrues. Read-only; no model or network call.",
    }


def debug_log(lines: int = 200) -> Dict[str, Any]:
    """Read-only tail of the application log (Wave: debug console). Runs every
    line through redact() — the same redaction the warehouse viewer and
    get_task_log use — and withholds any line that still trips posture_scan, so a
    token/cookie that landed in a log is never surfaced. Read-only; never writes
    or executes anything."""
    try:
        n = max(1, min(int(lines), 2000))
    except (TypeError, ValueError):
        n = 200
    # candidate log locations: explicit env, then the app's RotatingFileHandler
    candidates = []
    env = os.environ.get("BD_LOG_FILE")
    if env:
        candidates.append(Path(env))
    candidates += [Path("logs/bulk_downloader.log"),
                   Path(os.environ.get("BD_HOME", ".")) / "logs" / "bulk_downloader.log"]
    log_path = next((p for p in candidates if p.is_file()), None)
    if not log_path:
        return {"present": False, "lines": [],
                "searched": [str(p) for p in candidates],
                "_note": "No application log found. Set BD_LOG_FILE to point at "
                         "the running log. Read-only."}
    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return {"present": False, "lines": [], "error": str(e), "_note": "Read-only."}
    tail = raw[-n:]
    out = []
    withheld = 0
    for ln in tail:
        red = redact(ln)
        try:
            from bulk_downloader.capture_ingest import posture_scan
            leaks = posture_scan(red)
        except Exception:
            leaks = []
        if leaks:
            out.append("[line withheld — posture scan flagged a potential leak]")
            withheld += 1
        else:
            out.append(red)
    return {"present": True, "path": str(log_path), "lines": out,
            "shown": len(out), "withheld": withheld, "as_of": _now_iso(),
            "_note": "Read-only, redacted tail of the application log. Lines that "
                     "trip the posture scan are withheld."}
