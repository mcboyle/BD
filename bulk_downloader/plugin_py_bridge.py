"""Python-runtime plugin bridge (v3.66.482, R1 -- plugin-v3 keystone).

Runs ``.py`` plugins that opt into subprocess isolation through the SAME
manifest + JSON contract as :mod:`plugin_node`, so the loader's execution model
is uniform across languages and a hung/crashing ``.py`` plugin can be **killed**
rather than leaking a daemon thread (the old ``plugins._call_guarded`` timeout
path could not kill an in-process callback).

A ``.py`` file is a **bridge plugin** iff its FIRST line is exactly
``# bd:bridge`` -- a plain Python comment, so it is invisible to the legacy
in-process importlib loader, cheap to detect (one line, no exec), and never
collides with a ``#!`` shebang. Files without the sentinel keep loading exactly
as before (decorator-based, in-process); this module changes nothing for them.

Contract (mirrors node, with the richer R1 envelope)::

  * ``python <file> --manifest``  -> ONE JSON line:
      {api_version, kind, name, [event|site_id|priority], [capabilities], [inproc]}
  * ``python <file> <event>``     -> reads {"event","payload","ctx"} on stdin,
      writes {"ok", "result"|"error"} on stdout.

Isolation posture. Bridge plugins are **subprocess-isolated by default**: every
fire spawns ``python <file> <event>`` bounded by :data:`_FIRE_TIMEOUT`; a timeout
kills the process (no leaked thread), and a non-zero exit / unparsable stdout /
``ok: false`` raises so the caller's quarantine machinery records the failure.

In-proc fast path. A trusted first-party plugin can opt OUT of the fork cost by
declaring ``"inproc": true`` in its manifest. In-proc mode imports the module
once and calls its ``handle(event, payload, ctx)`` directly -- no subprocess, no
timeout kill (it runs with BD's authority, same as a legacy plugin, which is why
it is opt-in and per-plugin).

The interpreter is always the running ``sys.executable`` -- R1 ships NO
interpreter-override knob (no env var, no config key). Python's interpreter is
unambiguously the one BD runs under; a per-language interpreter table arrives,
fully governed, with X1. Like node, an absent interpreter is a clean skip rather
than a crash (``sys.executable`` is essentially always present, so this is
belt-and-suspenders / parity with the node path).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Tuple

PY_SUFFIXES = (".py",)

# Plain-comment sentinel on line 1 that marks a .py as a bridge plugin.
BRIDGE_SENTINEL = "# bd:bridge"

# Per-invocation wall-clock bound (seconds), mirroring plugin_node. The probe is
# cheaper; the fire is bounded so a runaway .py plugin cannot block the worker.
_PROBE_TIMEOUT = 5.0
_FIRE_TIMEOUT = 30.0

# Cache of imported modules for the in-proc fast path, keyed by absolute path.
_inproc_modules: dict = {}


def py_bin() -> str:
    """Interpreter for bridge plugins.

    Always the running interpreter (``sys.executable``). Unlike node -- whose
    binary location varies and is therefore overridable via ``BD_PLUGINS_NODE_BIN``
    + a plugins.json control -- Python's interpreter is unambiguously the one BD
    is already running under, so R1 intentionally ships NO interpreter-override
    knob (no env var, no config key, nothing for the config-parity surface to
    govern). A language-specific interpreter table arrives, fully governed, with
    X1 (suffix -> interpreter).
    """
    return sys.executable or "python3"


def py_available(binary: Optional[str] = None) -> bool:
    b = binary or py_bin()
    if os.path.sep in b or b.startswith("."):
        return Path(b).is_file() and os.access(b, os.X_OK)
    return bool(shutil.which(b))


def is_bridge_file(path: Path) -> bool:
    """True iff the file's FIRST line is exactly the bridge sentinel.

    Read-only and exec-free: a single line is read so legacy plugins are never
    executed during discrimination.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline().strip()
    except OSError:
        return False
    return first == BRIDGE_SENTINEL


def probe_manifest(path: Path) -> Tuple[Optional[dict], str]:
    """Run ``python <path> --manifest``; return (manifest_dict, error_str)."""
    b = py_bin()
    if not py_available(b):
        return (None, f"python runtime not found ({b!r})")
    try:
        proc = subprocess.run(
            [b, str(path), "--manifest"],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
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


def _load_inproc_module(path: Path):
    """Import (and cache) a bridge .py for the in-proc fast path."""
    key = str(path.resolve())
    mod = _inproc_modules.get(key)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(f"bd_pybridge_{path.stem}", str(path))
    if not spec or not spec.loader:
        raise ImportError(f"could not create spec for {path.name}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    if not callable(getattr(mod, "handle", None)):
        raise AttributeError(f"{path.name}: in-proc bridge needs a handle(event,payload,ctx)")
    _inproc_modules[key] = mod
    return mod


def _make_shim(path: Path, *, inproc: bool = False):
    """Build a Python callable that runs the bridge plugin for one event.

    Errors raise so the caller's guarded-call / quarantine machinery records
    them. The subprocess form is killed at :data:`_FIRE_TIMEOUT` (no leaked
    thread); the in-proc form calls ``handle`` directly with no fork.
    """
    fname = path.name

    def _normalize(payload, _k):
        if not isinstance(payload, dict):
            payload = {"arg": payload}
        return payload, (_k.get("event") or "fire")

    if inproc:
        def _shim(payload, *_a, **_k):
            payload, event = _normalize(payload, _k)
            mod = _load_inproc_module(path)
            return mod.handle(event, payload, _k.get("ctx") or {})
        _shim.__name__ = f"py_{path.stem}"
        return _shim

    def _shim(payload, *_a, **_k):
        payload, event = _normalize(payload, _k)
        req = json.dumps({"event": event, "payload": payload, "ctx": _k.get("ctx") or {}})
        b = py_bin()
        try:
            proc = subprocess.run(
                [b, str(path), str(event)],
                input=req, capture_output=True, text=True,
                timeout=_FIRE_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"py plugin {fname} timed out") from e
        if proc.returncode != 0:
            raise RuntimeError(
                f"py plugin {fname} exit {proc.returncode}: {proc.stderr.strip()[:200]}")
        out = (proc.stdout or "").strip()
        if not out:
            return None
        try:
            env = json.loads(out.splitlines()[-1])
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"py plugin {fname} bad JSON: {e}") from e
        if isinstance(env, dict) and "ok" in env:
            if not env.get("ok"):
                raise RuntimeError(f"py plugin {fname}: {env.get('error') or 'plugin error'}")
            return env.get("result")
        # Tolerate a bare result object (no envelope) for forward-compat.
        return env

    _shim.__name__ = f"py_{path.stem}"
    return _shim


def load_py_plugin(path: Path, *, full_access: bool, gated_caps: set,
                   api_version: int, granted_caps=frozenset(),
                   force_isolated: bool = False) -> dict:
    """Discover + register one bridge ``.py`` plugin. Returns a load-entry dict
    shaped like the node/.py path's entry."""
    from . import plugins as Pl  # local import: registries live in plugins

    entry = {"filename": path.name, "ok": False, "error": "",
             "manifest": {}, "skipped_reason": "", "kind": "", "py_bridge": True}

    man, err = probe_manifest(path)
    if man is None:
        entry["skipped_reason"] = err
        return entry
    entry["manifest"] = man

    ok_api, why = Pl.api_compatible(man)
    if not ok_api:
        entry["skipped_reason"] = why
        return entry

    caps = set(man.get("capabilities") or [])
    ok_gate, gate_why = Pl.capability_gate(caps, gated_caps, full_access, granted_caps)
    if not ok_gate:
        entry["skipped_reason"] = gate_why
        return entry
    ok_req, req_why = Pl.requires_satisfied(man)  # V3-C (777): same SoT as in-proc
    if not ok_req:
        entry["skipped_reason"] = req_why
        return entry

    # W6 (778): the operator's force_isolated key OVERRIDES a manifest
    # "inproc": true -- a forced plugin runs via the subprocess shim.
    inproc = bool(man.get("inproc")) and not force_isolated
    kind = str(man.get("kind") or "").lower()
    name = str(man.get("name") or path.stem)
    entry["kind"] = kind
    entry["inproc"] = inproc

    try:
        shim = _make_shim(path, inproc=inproc)
        if inproc:
            # Surface an in-proc import failure at load (not first fire).
            _load_inproc_module(path)
    except Exception as e:  # noqa: BLE001
        entry["error"] = str(e)[:300]
        return entry

    if kind == "processor":
        prio = man.get("priority", 100)
        try:
            prio = int(prio)
        except (TypeError, ValueError):
            prio = 100
        Pl.register_processor(shim, priority=prio, name=name)
    elif kind == "hook":
        event = str(man.get("event") or "")
        if not event:
            entry["skipped_reason"] = "hook manifest missing 'event'"
            return entry
        Pl.register_hook(event, lambda payload, _s=shim, _e=event: _s(payload, event=_e))
    elif kind == "extractor":
        site_id = str(man.get("site_id") or "")
        if not site_id:
            entry["skipped_reason"] = "extractor manifest missing 'site_id'"
            return entry
        Pl.register_extractor(site_id, lambda *a, _s=shim, _sid=site_id:
                              _s({"site_id": _sid, "args": list(a)}, event="extract"))
    else:
        entry["skipped_reason"] = f"unknown py-bridge plugin kind {kind!r}"
        return entry

    entry["ok"] = True
    return entry
