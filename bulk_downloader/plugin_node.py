"""Node-runtime plugin bridge (v3.66.468, WS1).

Runs ``*.js`` / ``*.mjs`` plugins via a node subprocess so the loader is
runtime-agnostic. Contract:

  * ``node <file> --manifest`` prints ONE JSON line describing the plugin:
    ``{api_version, kind, name, [event|site_id|priority], [capabilities]}``
    where ``kind`` is one of ``processor`` / ``hook`` / ``extractor``.
  * ``node <file> <event>`` receives the event payload as JSON on **stdin**
    and writes its result as JSON to **stdout**.

A thin Python shim is registered into the SAME registries as ``.py`` plugins
(:func:`plugins.register_processor` / ``register_hook`` / ``register_extractor``),
so processors/hooks/extractors fire identically regardless of language.

Node plugins honor the same governance as ``.py``: a manifest declaring a
gated capability is skipped unless full-access is enabled. The node binary is
``BD_PLUGINS_NODE_BIN`` (default ``node``); an absent runtime is a clean skip,
never a crash. Per-fire timeout is bounded so a hung node process can't wedge
the registry.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

from .plugin_sandbox import run_plugin_process

NODE_SUFFIXES = (".js", ".mjs")

# Per-invocation wall-clock bound (seconds). Probe is cheaper; fire is bounded
# so a runaway node plugin cannot block the worker.
_PROBE_TIMEOUT = 5.0
_FIRE_TIMEOUT = 30.0


def node_bin() -> str:
    # Resolution order: BD_PLUGINS_NODE_BIN env override > plugins.json
    # `node_bin` (the GUI-settable value) > "node". The plugins.json source is
    # what makes this a live GUI control (read on every probe/fire, no restart).
    env = (os.environ.get("BD_PLUGINS_NODE_BIN", "") or "").strip()
    if env:
        return env
    try:
        from . import plugins as _pl
        cfg_val = (_pl.read_config().get("node_bin") or "").strip()
        if cfg_val:
            return cfg_val
    except Exception:  # noqa: BLE001
        pass
    return "node"


def node_available(binary: Optional[str] = None) -> bool:
    b = binary or node_bin()
    # absolute/explicit path: check directly; bare name: PATH lookup
    if os.path.sep in b or b.startswith("."):
        return Path(b).is_file() and os.access(b, os.X_OK)
    return bool(shutil.which(b))


def probe_manifest(path: Path) -> Tuple[Optional[dict], str]:
    """Run ``node <path> --manifest``; return (manifest_dict, error_str)."""
    path = path.resolve()
    b = node_bin()
    if not node_available(b):
        return (None, f"node runtime not found ({b!r})")
    try:
        proc = run_plugin_process(
            [b, str(path), "--manifest"],
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


def _make_shim(path: Path):
    """Build a Python callable that runs the node plugin for one event.

    Payload (a dict) -> JSON on stdin; stdout JSON -> returned. Errors raise so
    the caller's guarded-call / quarantine machinery records them.
    """
    path = path.resolve()
    b = node_bin()
    fname = path.name

    def _shim(payload, *_a, **_k):
        # extractors are called fn(site_id) by BD; normalize to a dict payload
        if not isinstance(payload, dict):
            payload = {"arg": payload}
        event = _k.get("event") or "fire"
        try:
            proc = run_plugin_process(
                [b, str(path), str(event)],
                input_text=json.dumps(payload), plugin_path=path,
                timeout=_FIRE_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"node plugin {fname} timed out") from e
        if proc.returncode != 0:
            raise RuntimeError(
                f"node plugin {fname} exit {proc.returncode}: "
                f"{proc.stderr.strip()[:200]}")
        out = (proc.stdout or "").strip()
        if not out:
            return None
        try:
            return json.loads(out.splitlines()[-1])
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"node plugin {fname} bad JSON: {e}") from e

    _shim.__name__ = f"node_{path.stem}"
    return _shim


def load_node_plugin(path: Path, *, full_access: bool, gated_caps: set,
                     api_version: int, granted_caps=frozenset()) -> dict:
    """Discover + register one node plugin. Returns a load-entry dict shaped
    like the ``.py`` path's entry: {filename, ok, error, manifest,
    skipped_reason, kind}."""
    from . import plugins as P  # local import: registries live in plugins

    entry = {"filename": path.name, "ok": False, "error": "",
             "manifest": {}, "skipped_reason": "", "kind": "", "node": True}

    man, err = probe_manifest(path)
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
    shim = _make_shim(path)
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
        # bind the event name into the shim call
        P.register_hook(event, lambda payload, _s=shim, _e=event: _s(payload, event=_e))
    elif kind == "extractor":
        site_id = str(man.get("site_id") or "")
        if not site_id:
            entry["skipped_reason"] = "extractor manifest missing 'site_id'"
            return entry
        P.register_extractor(site_id, lambda *a, _s=shim, _sid=site_id:
                             _s({"site_id": _sid, "args": list(a)}, event="extract"))
    else:
        entry["skipped_reason"] = f"unknown node plugin kind {kind!r}"
        return entry

    entry["ok"] = True
    return entry
