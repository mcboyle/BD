"""v3.66.778 -- Plugin-v3 W6 (re-scoped): operator-forced isolation.

The plan's W6 ("subprocess backend for net/fs") was stale two ways: the
subprocess substrate ALREADY exists (node @468, exec @483, py-bridge @482),
and the shipped gated caps (lifecycle/page_access) receive live Playwright
objects that cannot cross a JSON stdin/stdout boundary. The honest gap:
isolation for ``.py`` plugins is plugin-AUTHOR opt-in (the ``# bd:bridge``
sentinel) -- the OPERATOR cannot force a hand-dropped plugin out-of-process.

This cut adds plugins.json ``force_isolated: ["file.py", ...]`` (or
``["*"]``), GUI-writable via _CONFIG_KEYS:

  * a listed ``.py`` file NEVER takes the in-proc importlib path;
  * if it speaks the bridge contract (sentinel) it loads via the subprocess
    bridge, and the operator key OVERRIDES a manifest ``"inproc": true``;
  * if it lacks the contract it is SKIPPED with a named reason -- deny by
    default, never silently degrade to in-proc (a forced plugin that cannot
    be isolated must not run at all, even when its capabilities are granted);
  * surfaced in status() so the grant-UI panel shows the isolation state.

RED on pristine v3.66.777: the key is unknown (dropped by write_config,
ignored by the loader, absent from status). GREEN after.

run_tests.py conventions: zero-arg test functions.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402

_BRIDGE_BODY = (
    "# bd:bridge\n"
    "import json, sys\n"
    "MAN = {'api_version': 2, 'kind': 'processor', 'name': '%s'%s}\n"
    "if __name__ == '__main__':\n"
    "    if '--manifest' in sys.argv:\n"
    "        print(json.dumps(MAN))\n"
    "    else:\n"
    "        print(json.dumps({'ok': True, 'result': {}}))\n"
)


def _with_dir(tmp):
    orig = P._plugin_dir
    P._plugin_dir = lambda: Path(tmp)
    return orig


def _write(tmp, name, body):
    (Path(tmp) / name).write_text(body, "utf-8")


def _cfg(tmp, **kw):
    (Path(tmp) / "plugins.json").write_text(json.dumps(kw), "utf-8")


def _entry(res, name):
    return {x["filename"]: x for x in res["plugins"]}[name]


def test_config_round_trips_force_isolated():
    """The GUI write path persists force_isolated (the 775 lesson: a key the
    write path silently drops is hand-edit-only). RED on 777."""
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        cfg = P.write_config({"force_isolated": ["a.py", "*"]})
        assert cfg.get("force_isolated") == ["a.py", "*"], cfg
        assert P.read_config().get("force_isolated") == ["a.py", "*"]
    finally:
        P._plugin_dir = orig


def test_forced_nonbridge_plugin_skips_with_named_reason():
    """RED anchor: a listed plain in-proc plugin must NOT load in-process;
    it lacks the bridge contract, so it is skipped with a reason naming both
    the operator key and the missing contract."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "plain.py", "PLUGIN={'name':'plain','api_version':2}\n")
        _cfg(tmp, force_isolated=["plain.py"])
        e = _entry(P.load_all(), "plain.py")
        assert e["ok"] is False, e
        assert "force_isolated" in e["skipped_reason"], e["skipped_reason"]
        assert "bd:bridge" in e["skipped_reason"], e["skipped_reason"]
    finally:
        P._plugin_dir = orig
        P.reset()


def test_forced_bridge_plugin_loads_via_subprocess():
    """Invariant guard: a listed bridge-contract plugin still loads -- via
    the subprocess bridge."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "br.py", _BRIDGE_BODY % ("br", ""))
        _cfg(tmp, force_isolated=["br.py"])
        e = _entry(P.load_all(), "br.py")
        assert e["ok"] is True, e
        assert e.get("py_bridge") is True, e
        assert e.get("inproc") is False, e
    finally:
        P._plugin_dir = orig
        P.reset()


def test_forced_overrides_manifest_inproc():
    """RED anchor: a bridge-contract plugin that opts back in-process via
    manifest ``"inproc": true`` LOSES to the operator key -- it runs via the
    subprocess shim."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "opt.py", _BRIDGE_BODY % ("opt", ", 'inproc': True"))
        _cfg(tmp, force_isolated=["opt.py"])
        e = _entry(P.load_all(), "opt.py")
        assert e["ok"] is True, e
        assert e.get("inproc") is False, e
    finally:
        P._plugin_dir = orig
        P.reset()


def test_star_isolates_all():
    """RED anchor: ``"*"`` applies the policy to every .py plugin."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "one.py", "PLUGIN={'name':'one','api_version':2}\n")
        _write(tmp, "two.py", "PLUGIN={'name':'two','api_version':2}\n")
        _cfg(tmp, force_isolated=["*"])
        res = P.load_all()
        assert _entry(res, "one.py")["ok"] is False
        assert _entry(res, "two.py")["ok"] is False
        assert "force_isolated" in _entry(res, "one.py")["skipped_reason"]
    finally:
        P._plugin_dir = orig
        P.reset()


def test_granted_caps_do_not_weaken_isolation_refusal():
    """RED anchor: precedence -- a granted capability does NOT readmit a
    forced, non-bridge plugin to the in-proc path. Deny, never degrade."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "life.py",
               "PLUGIN={'name':'life','api_version':2,"
               "'capabilities':['lifecycle']}\n")
        _cfg(tmp, force_isolated=["life.py"],
             granted_capabilities=["lifecycle"])
        e = _entry(P.load_all(), "life.py")
        assert e["ok"] is False, e
        assert "force_isolated" in e["skipped_reason"], e["skipped_reason"]
    finally:
        P._plugin_dir = orig
        P.reset()


def test_unlisted_behavior_byte_identical():
    """Invariant guard: no force_isolated key -> plain plugins load in-proc
    exactly as before; bridge plugins via the bridge."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "plain.py", "PLUGIN={'name':'plain','api_version':2}\n")
        e = _entry(P.load_all(), "plain.py")
        assert e["ok"] is True, e
    finally:
        P._plugin_dir = orig
        P.reset()


def test_status_exposes_force_isolated():
    """RED anchor: the grant-UI panel derives the isolation state from
    status() (the 775 derive-don't-mirror rule)."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _cfg(tmp, force_isolated=["x.py"])
        P.load_all()
        s = P.status()
        assert s.get("force_isolated") == ["x.py"], s.get("force_isolated")
    finally:
        P._plugin_dir = orig
        P.reset()
