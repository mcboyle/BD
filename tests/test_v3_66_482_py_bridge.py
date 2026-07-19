"""v3.66.482 R1 (plugin-v3 keystone): subprocess-isolate ``.py`` plugins.

``.py`` plugins may now opt into the SAME subprocess + JSON contract as node
(``plugin_node.py``), closing the daemon-thread leak in ``_call_guarded``'s
timeout path and unifying the execution model every later plugin kind plugs
into.

A ``.py`` file is a **bridge plugin** iff its FIRST line is exactly
``# bd:bridge`` (a Python comment -- invisible to the legacy in-proc import,
cheap to detect, never collides with a ``#!`` shebang). Bridge plugins speak:

  * ``python <file> --manifest``  -> one JSON line
    ``{api_version, kind, name, [event|site_id|priority], [capabilities], [inproc]}``
  * ``python <file> <event>``     -> reads ``{"event","payload","ctx"}`` on
    stdin, writes ``{"ok", "result"|"error"}`` on stdout.

Bridge plugins are **subprocess-isolated by default** (killed at the fire
timeout -- NO leaked thread). They opt into the **in-proc fast path** via a
manifest ``"inproc": true`` (imports the module and
calls ``handle(event, payload, ctx)`` directly, no fork).

``.py`` files WITHOUT the sentinel stay LEGACY (in-proc import, decorator-based)
-- byte-identical to pre-482 behavior.

Runner-safe: zero-arg test fns, no pytest builtins, paths from __file__,
tempfile.mkdtemp, env/module globals restored in try/finally.
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def _with_plugin_dir(tmp):
    orig = P._plugin_dir
    P._plugin_dir = lambda: Path(tmp)
    return orig


def _write(tmp, name, body):
    fp = Path(tmp) / name
    fp.write_text(body, "utf-8")
    return fp


# ── Bridge plugin fixtures (pure-stdlib .py speaking the contract) ─────

# A processor that echoes the site_id back, both subprocess and in-proc.
_PROC_PY = """\
# bd:bridge
import json, sys

PLUGIN = {"api_version": 2, "kind": "processor", "name": "py_tagger",
          "priority": 50}


def handle(event, payload, ctx):
    return {"tagged": True, "site": (payload or {}).get("site_id")}


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--manifest":
        sys.stdout.write(json.dumps(PLUGIN))
        sys.exit(0)
    raw = sys.stdin.read()
    req = json.loads(raw) if raw.strip() else {}
    try:
        r = handle(req.get("event") or arg, req.get("payload") or {},
                   req.get("ctx") or {})
        sys.stdout.write(json.dumps({"ok": True, "result": r}))
    except Exception as e:  # noqa: BLE001
        sys.stdout.write(json.dumps({"ok": False, "error": str(e)}))
"""

# A processor that hangs forever when fired (but answers --manifest fast).
_HANG_PY = """\
# bd:bridge
import json, sys, time

PLUGIN = {"api_version": 2, "kind": "processor", "name": "py_hang",
          "priority": 50}

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--manifest":
        sys.stdout.write(json.dumps(PLUGIN)); sys.exit(0)
    time.sleep(3600)
"""

# A processor that always crashes when fired.
_CRASH_PY = """\
# bd:bridge
import json, sys

PLUGIN = {"api_version": 2, "kind": "processor", "name": "py_crash",
          "priority": 50}

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--manifest":
        sys.stdout.write(json.dumps(PLUGIN)); sys.exit(0)
    raise SystemExit(7)
"""

# Same logic as _PROC_PY but manifest opts into the in-proc fast path.
_INPROC_PY = """\
# bd:bridge
import json, sys

PLUGIN = {"api_version": 2, "kind": "processor", "name": "py_fast",
          "priority": 50, "inproc": True}


def handle(event, payload, ctx):
    return {"tagged": True, "site": (payload or {}).get("site_id"), "fast": True}


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--manifest":
        sys.stdout.write(json.dumps(PLUGIN)); sys.exit(0)
    raw = sys.stdin.read()
    req = json.loads(raw) if raw.strip() else {}
    r = handle(req.get("event") or arg, req.get("payload") or {}, req.get("ctx") or {})
    sys.stdout.write(json.dumps({"ok": True, "result": r}))
"""

# A gated-capability bridge plugin (page_access) -- skipped with the gate off.
_GATED_PY = """\
# bd:bridge
import json, sys

PLUGIN = {"api_version": 2, "kind": "processor", "name": "py_priv",
          "priority": 50, "capabilities": ["page_access"]}


def handle(event, payload, ctx):
    return {"ok": True}


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--manifest":
        sys.stdout.write(json.dumps(PLUGIN)); sys.exit(0)
    sys.stdout.write(json.dumps({"ok": True, "result": {}}))
"""

# A LEGACY decorator plugin (no sentinel) -- must keep importing in-proc.
_LEGACY_PY = """\
from bulk_downloader import plugins as P


@P.processor(priority=70, name="legacy_proc")
def go(payload):
    return {"legacy": True}
"""


def test_py_bridge_module_importable():
    # The bridge lives in its own module so the import edge is declarable
    # (import-graph baseline gate), mirroring plugin_node.
    from bulk_downloader import plugin_py_bridge  # noqa: F401
    assert hasattr(plugin_py_bridge, "load_py_plugin")
    assert hasattr(plugin_py_bridge, "probe_manifest")


def test_bridge_processor_discovered_and_runs_subprocess():
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        _write(tmp, "tagger.py", _PROC_PY)
        res = P.load_all()
        assert res["loaded"] == 1, res
        names = [p["name"] for p in P.list_processors()]
        assert "py_tagger" in names, names
        out = P.run_processors({"site_id": "demo"})
        got = [r for r in out if r["name"] == "py_tagger"]
        assert got and got[0]["ok"], out
        assert got[0]["result"] == {"tagged": True, "site": "demo"}, got
    finally:
        P._plugin_dir = orig
        P.reset()


def test_bridge_hung_plugin_killed_no_thread_leak():
    """(a) A hung bridge .py is killed at the fire timeout and leaks NO thread.

    The whole point of R1: the old daemon-thread timeout path could not kill a
    hung in-proc plugin, leaking a thread per fire. The subprocess bridge kills
    the process, so thread count stays flat across N fires.
    """
    from bulk_downloader import plugin_py_bridge as B
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    old_to = B._FIRE_TIMEOUT
    try:
        P.reset()
        B._FIRE_TIMEOUT = 0.75  # keep the test fast
        _write(tmp, "hang.py", _HANG_PY)
        res = P.load_all()
        assert res["loaded"] == 1, res
        base = threading.active_count()
        for _ in range(3):
            out = P.run_processors({"site_id": "x"})
            got = [r for r in out if r["name"] == "py_hang"]
            # a hung plugin is a failure (killed), never ok
            assert got and got[0]["ok"] is False, out
        # give any (wrongly) leaked threads a beat to register, then assert flat
        time.sleep(0.2)
        assert threading.active_count() <= base + 1, (
            f"thread leak: base={base} now={threading.active_count()}")
    finally:
        B._FIRE_TIMEOUT = old_to
        P._plugin_dir = orig
        P.reset()


def test_bridge_crashing_plugin_quarantines():
    """(b) A crashing bridge .py -> (False, None) + quarantine fail count rises."""
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        _write(tmp, "crash.py", _CRASH_PY)
        res = P.load_all()
        assert res["loaded"] == 1, res
        # fire enough times to trip the fail budget
        for _ in range(P._FAIL_BUDGET):
            P.run_processors({"site_id": "x"})
        q = P.list_quarantine()
        assert any("py_crash" in entry["key"] or entry["fails"] >= P._FAIL_BUDGET
                   for entry in q), q
    finally:
        P._plugin_dir = orig
        P.reset()


def test_bridge_result_parity_inproc_vs_subprocess():
    """(c) The SAME bridge plugin returns an identical result in-proc vs subprocess."""
    from bulk_downloader import plugin_py_bridge as B
    tmp = tempfile.mkdtemp()
    payload = {"site_id": "parity"}

    # subprocess path
    sub = _write(tmp, "p_sub.py", _PROC_PY)
    shim_sub = B._make_shim(Path(sub), inproc=False)
    r_sub = shim_sub(payload, event="download.done")

    # in-proc path
    shim_in = B._make_shim(Path(sub), inproc=True)
    r_in = shim_in(payload, event="download.done")

    assert r_sub == r_in == {"tagged": True, "site": "parity"}, (r_sub, r_in)


def test_bridge_gated_capability_skipped_when_gate_off():
    """(d) A gated-cap bridge .py is skipped at load with the gate off."""
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        P.set_full_access(False)
        _write(tmp, "priv.py", _GATED_PY)
        res = P.load_all()
        assert res["loaded"] == 0, res
        assert res["skipped"] >= 1, res
        assert "py_priv" not in [p["name"] for p in P.list_processors()]
    finally:
        P._plugin_dir = orig
        P.reset()


def test_bridge_inproc_fast_path_bypasses_subprocess():
    """(e) inproc manifest runs in-proc -- NO subprocess spawned."""
    from bulk_downloader import plugin_py_bridge as B
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    # tripwire: if the fast path forks, this raises and the test fails
    import subprocess
    real_run = subprocess.run
    calls = {"n": 0}

    def _tripwire(*a, **k):
        # allow the --manifest probe at load, count fire-time runs separately
        calls["n"] += 1
        return real_run(*a, **k)

    try:
        P.reset()
        _write(tmp, "fast.py", _INPROC_PY)
        res = P.load_all()
        assert res["loaded"] == 1, res
        # now instrument only the FIRE
        subprocess.run = _tripwire
        calls["n"] = 0
        out = P.run_processors({"site_id": "demo"})
        got = [r for r in out if r["name"] == "py_fast"]
        assert got and got[0]["ok"], out
        assert got[0]["result"] == {"tagged": True, "site": "demo", "fast": True}, got
        assert calls["n"] == 0, f"fast path spawned a subprocess ({calls['n']} run calls)"
    finally:
        subprocess.run = real_run
        P._plugin_dir = orig
        P.reset()


def test_legacy_py_plugin_unchanged_inproc():
    """A .py WITHOUT the sentinel still imports in-proc via decorators (no regression)."""
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        _write(tmp, "legacy.py", _LEGACY_PY)
        res = P.load_all()
        assert res["loaded"] == 1, res
        names = [p["name"] for p in P.list_processors()]
        assert "legacy_proc" in names, names
        out = P.run_processors({"site_id": "demo"})
        got = [r for r in out if r["name"] == "legacy_proc"]
        assert got and got[0]["ok"] and got[0]["result"] == {"legacy": True}, out
    finally:
        P._plugin_dir = orig
        P.reset()
