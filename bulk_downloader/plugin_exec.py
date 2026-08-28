"""Generic interpreter-based plugin bridge (v3.66.483, X1).

Generalizes the node bridge (:mod:`plugin_node`) and the ``.py`` bridge
(:mod:`plugin_py_bridge`, R1) into ONE interpreter-keyed exec bridge: a plugin
written in any language that speaks the existing manifest + JSON contract loads
with ~zero new bridge code. The contract is exactly node's::

  * ``<interp> <file> --manifest``  -> ONE JSON line:
      {api_version, kind, name, [event|site_id|priority], [capabilities]}
  * ``<interp> <file> <event>``     -> reads the JSON request on **stdin**,
      writes the result on **stdout** (a bare object, or the richer R1
      ``{"ok", "result"|"error"}`` envelope -- both are tolerated).

Interpreter resolution. The default per-suffix interpreter is
:data:`INTERPRETER_BY_SUFFIX` (``.rb`` -> ruby, ``.sh`` -> sh, ``.php`` -> php).
A **no-suffix** executable (a shebang script or compiled binary with the exec
bit set) is run **directly** -- the shebang / loader chooses the interpreter.
The per-suffix default is overridable via the ``plugins.json`` ``interpreters``
map (``{".rb": "/opt/ruby"}``); this is a plugins.json config key, exactly like
``node_bin``, so X1 introduces **NO new ``BD_*`` env var** (the v3.66.482
config-parity governance lesson: a runtime-tunable env var is a config-parity
obligation -- a plugins.json key is the already-governed override surface).

``.js``/``.mjs`` stay with :mod:`plugin_node` (it keeps the
``BD_PLUGINS_NODE_BIN`` env override + ``node_bin`` control) and ``.py`` stays
with :mod:`plugin_py_bridge` / the legacy in-proc loader; :func:`interpreter_for`
returns ``None`` for those so they are never routed here. An absent interpreter
is a CLEAN SKIP (never a crash), mirroring node's ``node_available``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from .plugin_sandbox import run_plugin_process

# Default suffix -> interpreter argv-prefix. ``.js``/``.mjs`` (node) and ``.py``
# (py-bridge / legacy) are deliberately ABSENT -- they have dedicated paths.
INTERPRETER_BY_SUFFIX = {
    ".rb": ("ruby",),
    ".sh": ("sh",),
    ".php": ("php",),
}

# Suffixes owned by other bridges -- never handled here.
_RESERVED_SUFFIXES = (".js", ".mjs", ".py")

# Per-invocation wall-clock bounds (seconds), mirroring plugin_node.
_PROBE_TIMEOUT = 5.0
_FIRE_TIMEOUT = 30.0


def interpreter_for(path: Path, lcfg: Optional[dict] = None):
    """Resolve the interpreter argv-prefix for ``path``.

    Returns a ``list`` argv-prefix (``["sh"]``), an empty list ``[]`` for a
    no-suffix executable run directly (shebang/binary), or ``None`` when this
    bridge does not own the file (node/.py suffix, or an unknown suffix).

    Resolution order for a known suffix: ``plugins.json`` ``interpreters[suffix]``
    override > the :data:`INTERPRETER_BY_SUFFIX` default. (No env var -- the
    plugins.json map is the governed override surface, like ``node_bin``.)
    """
    suf = path.suffix.lower()
    if suf in _RESERVED_SUFFIXES:
        return None
    if suf == "":
        # No suffix -> direct execution (the shebang or the binary loader picks
        # the interpreter). Availability is the exec bit (checked separately).
        return []
    default = INTERPRETER_BY_SUFFIX.get(suf)
    if default is None:
        return None
    overrides = (lcfg or {}).get("interpreters") or {}
    ov = overrides.get(suf)
    if isinstance(ov, str) and ov.strip():
        return [ov.strip()]
    return list(default)


def interpreter_available(argv_prefix, path: Path) -> bool:
    """True iff the resolved interpreter (or, for direct-exec, the file) can run."""
    if not argv_prefix:
        # direct-exec: the plugin file itself must be executable
        return os.access(str(path), os.X_OK)
    b = argv_prefix[0]
    if os.path.sep in b or b.startswith("."):
        return Path(b).is_file() and os.access(b, os.X_OK)
    return bool(shutil.which(b))


def _argv(argv_prefix, path: Path, *rest) -> list:
    return [*argv_prefix, str(path), *rest]


def probe_manifest(path: Path, argv_prefix) -> Tuple[Optional[dict], str]:
    """Run ``<interp> <path> --manifest``; return (manifest_dict, error_str)."""
    path = path.resolve()
    if not interpreter_available(argv_prefix, path):
        who = argv_prefix[0] if argv_prefix else str(path)
        return (None, f"interpreter not found ({who!r})")
    try:
        proc = run_plugin_process(
            _argv(argv_prefix, path, "--manifest"),
            plugin_path=path, timeout=_PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return (None, "manifest probe timed out")
    except Exception as e:  # noqa: BLE001
        return (None, f"manifest probe failed: {e}")
    if proc.returncode != 0:
        return (None, f"manifest probe exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    out = (proc.stdout or "").strip()
    if not out:
        return (None, "manifest probe produced no output")
    try:
        man = json.loads(out.splitlines()[-1])
    except Exception as e:  # noqa: BLE001
        return (None, f"manifest not JSON: {e}")
    if not isinstance(man, dict):
        return (None, "manifest is not an object")
    return (man, "")


def _make_shim(path: Path, argv_prefix):
    """Build a Python callable that runs the plugin for one event.

    Payload dict -> JSON request on stdin; stdout JSON -> returned. A bare
    result object OR the R1 ``{"ok","result"|"error"}`` envelope is accepted.
    Errors raise so the caller's guarded-call / quarantine machinery records
    them; the subprocess is killed at :data:`_FIRE_TIMEOUT` (no leaked thread).
    """
    path = path.resolve()
    fname = path.name

    def _shim(payload, *_a, **_k):
        if not isinstance(payload, dict):
            payload = {"arg": payload}
        event = _k.get("event") or "fire"
        req = json.dumps({"event": event, "payload": payload, "ctx": _k.get("ctx") or {}})
        try:
            proc = run_plugin_process(
                _argv(argv_prefix, path, str(event)),
                input_text=req, plugin_path=path, timeout=_FIRE_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"plugin {fname} timed out") from e
        if proc.returncode != 0:
            raise RuntimeError(
                f"plugin {fname} exit {proc.returncode}: {proc.stderr.strip()[:200]}")
        out = (proc.stdout or "").strip()
        if not out:
            return None
        try:
            env = json.loads(out.splitlines()[-1])
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"plugin {fname} bad JSON: {e}") from e
        if isinstance(env, dict) and "ok" in env:
            if not env.get("ok"):
                raise RuntimeError(f"plugin {fname}: {env.get('error') or 'plugin error'}")
            return env.get("result")
        return env  # bare result (forward-compat)

    _shim.__name__ = f"exec_{path.stem}"
    return _shim


def load_exec_plugin(path: Path, *, interp, full_access: bool, gated_caps: set,
                     api_version: int, granted_caps=frozenset()) -> dict:
    """Discover + register one interpreter-based plugin. Returns a load-entry
    dict shaped like the node/.py path's entry."""
    from . import plugins as P  # local import: registries live in plugins

    entry = {"filename": path.name, "ok": False, "error": "",
             "manifest": {}, "skipped_reason": "", "kind": "", "exec": True,
             "interpreter": list(interp)}

    man, err = probe_manifest(path, interp)
    if man is None:
        entry["skipped_reason"] = err
        return entry
    entry["manifest"] = man

    ok_api, why = P.api_compatible(man)
    if not ok_api:
        entry["skipped_reason"] = why
        return entry

    caps = set(man.get("capabilities") or [])
    ok_gate, gate_why = P.capability_gate(caps, gated_caps, full_access, granted_caps)
    if not ok_gate:
        entry["skipped_reason"] = gate_why
        return entry
    ok_req, req_why = P.requires_satisfied(man)   # V3-C (777): same SoT as in-proc
    if not ok_req:
        entry["skipped_reason"] = req_why
        return entry

    kind = str(man.get("kind") or "").lower()
    name = str(man.get("name") or path.stem)
    shim = _make_shim(path, interp)
    entry["kind"] = kind

    if kind == "processor":
        prio = man.get("priority", 100)
        try:
            prio = int(prio)
        except (TypeError, ValueError):
            prio = 100
        P.register_processor(shim, priority=prio, name=name)
    elif kind == "hook":
        event = str(man.get("event") or "")
        if not event:
            entry["skipped_reason"] = "hook manifest missing 'event'"
            return entry
        P.register_hook(event, lambda payload, _s=shim, _e=event: _s(payload, event=_e))
    elif kind == "extractor":
        site_id = str(man.get("site_id") or "")
        if not site_id:
            entry["skipped_reason"] = "extractor manifest missing 'site_id'"
            return entry
        P.register_extractor(site_id, lambda *a, _s=shim, _sid=site_id:
                             _s({"site_id": _sid, "args": list(a)}, event="extract"))
    else:
        entry["skipped_reason"] = f"unknown exec plugin kind {kind!r}"
        return entry

    entry["ok"] = True
    return entry
