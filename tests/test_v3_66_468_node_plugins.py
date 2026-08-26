"""v3.66.468 WS1: node-runtime plugins.

The loader is extended to also discover ``*.js`` / ``*.mjs`` plugins in the
plugin dir and run them via a node subprocess bridge:

  * probe ``node <file> --manifest`` -> one JSON line
    ``{api_version, kind, name, [event|site_id|priority]}``
  * fire ``node <file> <event>`` with the event payload as JSON on stdin;
    the result is read back as JSON from stdout.

A thin Python shim is registered into the SAME registries as ``.py`` plugins
(processor / hook / extractor), so the rest of BD is runtime-agnostic. Node
plugins honor the same full-access gate as ``.py`` (a manifest declaring a
gated capability is skipped unless allow_full_access). ``BD_PLUGINS_NODE_BIN``
overrides the node binary. Product discovery cleanly skips an absent runtime;
the positive lifecycle gates report that absence as UNKNOWN and fail loudly.

Runner-safe: zero-arg test fns, no pytest builtins, paths from __file__,
tempfile.mkdtemp, module globals restored in try/finally.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402

_NODE = os.environ.get("BD_PLUGINS_NODE_BIN", "node")
_HAVE_NODE = bool(shutil.which(_NODE))


def _require_node_runtime():
    assert _HAVE_NODE, (
        f"UNKNOWN: Node runtime {_NODE!r} is unavailable, so the plugin "
        "lifecycle was not executed"
    )


def _with_plugin_dir(tmp):
    orig = P._plugin_dir
    P._plugin_dir = lambda: Path(tmp)
    return orig


def _write(tmp, name, body):
    fp = Path(tmp) / name
    fp.write_text(body, "utf-8")
    return fp


_PROC_MJS = """\
const kind = process.argv[2];
if (kind === '--manifest') {
  process.stdout.write(JSON.stringify(
    {api_version: 2, kind: 'processor', name: 'node_tagger', priority: 50}));
  process.exit(0);
}
let buf = '';
process.stdin.on('data', d => buf += d);
process.stdin.on('end', () => {
  const payload = buf.trim() ? JSON.parse(buf) : {};
  process.stdout.write(JSON.stringify({tagged: true, site: payload.site_id || null}));
});
"""

_HOOK_MJS = """\
const kind = process.argv[2];
if (kind === '--manifest') {
  process.stdout.write(JSON.stringify(
    {api_version: 2, kind: 'hook', name: 'node_logger', event: 'download.done'}));
  process.exit(0);
}
let buf = '';
process.stdin.on('data', d => buf += d);
process.stdin.on('end', () => { process.stdout.write(JSON.stringify({ok: true})); });
"""


def test_node_module_importable():
    # The bridge lives in its own module so the import edge is declarable.
    from bulk_downloader import plugin_node  # noqa: F401
    # NO ASSERTION ON THE PACKAGE ATTRIBUTE. MEASURED at v3.66.1097: it holds
    # in a bare interpreter and is FALSE under this suite, because
    # tests/conftest.py's _canonicalize_package_children reconciles
    # module-valued package attributes against sys.modules. So an assertion on
    # it would test the conftest, not the import edge, and that is what the
    # `or True` was quietly absorbing.
    # The import above IS the check: it raises ImportError if the bridge module
    # is missing, which is the whole point of the test's name.


def test_node_processor_discovered_and_runs():
    _require_node_runtime()
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        _write(tmp, "tagger.mjs", _PROC_MJS)
        res = P.load_all()
        assert res["loaded"] == 1, res
        # registered as a processor in the shared registry
        names = [p["name"] for p in P.list_processors()]
        assert "node_tagger" in names, names
        out = P.run_processors({"site_id": "demo"})
        got = [r for r in out if r["name"] == "node_tagger"]
        assert got and got[0]["ok"], out
        assert got[0]["result"] == {"tagged": True, "site": "demo"}, got
    finally:
        P._plugin_dir = orig
        P.reset()


def test_node_runtime_gate_is_unknown_when_node_is_absent():
    """The positive lifecycle gate must not return normally over no runtime."""
    global _HAVE_NODE
    saved = _HAVE_NODE
    try:
        _HAVE_NODE = False
        raised = None
        try:
            test_node_processor_discovered_and_runs()
        except AssertionError as exc:
            raised = str(exc)
        assert raised is not None and "UNKNOWN" in raised, (
            "the Node lifecycle gate returned OK without executing a plugin"
        )
    finally:
        _HAVE_NODE = saved


def test_node_plugin_selftest_reports_absence_as_unknown():
    """Selftest exit 0 is reserved for a completed lifecycle round-trip."""
    tool = _REPO / "toolchain" / "bin" / "bd-node-plugin-check"
    env = dict(os.environ)
    env["BD_PLUGINS_NODE_BIN"] = "/definitely-missing/node-row-host-shape"
    proc = subprocess.run(
        [sys.executable, str(tool), "--selftest"],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2 and "UNKNOWN" in (proc.stdout + proc.stderr), (
        "the selftest reported healthy without a Node runtime: "
        f"rc={proc.returncode}\n{proc.stdout}{proc.stderr}"
    )


def test_node_plugin_selftest_runs_the_healthy_lifecycle():
    """The state opposite UNKNOWN executes every advertised selftest subject."""
    _require_node_runtime()
    tool = _REPO / "toolchain" / "bin" / "bd-node-plugin-check"
    env = dict(os.environ)
    env["BD_PLUGINS_NODE_BIN"] = str(shutil.which(_NODE))
    proc = subprocess.run(
        [sys.executable, str(tool), "--selftest"],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"healthy Node selftest failed:\n{output}"
    for measured in (
        "node available",
        "--manifest contract round-trips",
        "event JSON payload round-trips",
        "tree plugin_node.probe_manifest agrees",
    ):
        assert measured in output, f"selftest did not report {measured!r}:\n{output}"
    assert "UNKNOWN" not in output


def test_node_hook_discovered_and_fires():
    _require_node_runtime()
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        _write(tmp, "logger.mjs", _HOOK_MJS)
        res = P.load_all()
        assert res["loaded"] == 1, res
        hooks = P.list_hooks()
        assert "download.done" in hooks and len(hooks["download.done"]) >= 1, hooks
        # firing must not raise
        P.fire_hook("download.done", {"site_id": "demo", "url": "u"})
    finally:
        P._plugin_dir = orig
        P.reset()


def test_node_absent_is_clean_skip():
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    old_bin = os.environ.get("BD_PLUGINS_NODE_BIN")
    try:
        P.reset()
        os.environ["BD_PLUGINS_NODE_BIN"] = "/nonexistent/node-xyz"
        _write(tmp, "tagger.mjs", _PROC_MJS)
        res = P.load_all()
        # node missing -> the .mjs is skipped, not an error, and nothing crashes
        assert res["errors"] == 0, res
        assert res["loaded"] == 0, res
        assert res["skipped"] >= 1, res
    finally:
        if old_bin is None:
            os.environ.pop("BD_PLUGINS_NODE_BIN", None)
        else:
            os.environ["BD_PLUGINS_NODE_BIN"] = old_bin
        P._plugin_dir = orig
        P.reset()


def test_node_gated_capability_skipped_when_gate_off():
    _require_node_runtime()
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        P.set_full_access(False)
        gated = _PROC_MJS.replace(
            "name: 'node_tagger', priority: 50",
            "name: 'node_priv', priority: 50, capabilities: ['page_access']")
        _write(tmp, "priv.mjs", gated)
        res = P.load_all()
        assert res["loaded"] == 0, res
        assert res["skipped"] >= 1, res
        assert "node_priv" not in [p["name"] for p in P.list_processors()]
    finally:
        P._plugin_dir = orig
        P.reset()
