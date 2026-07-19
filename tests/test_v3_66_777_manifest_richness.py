"""v3.66.777 -- Plugin-v3 V3-C: manifest richness (requires + enable/disable).

Completes the V3-C track (config_schema shipped @498). Two additive manifest
features:

  * ``requires`` -- ``{"bd_min": "3.66.700", "plugins": ["dep.py"]}``.
    Enforced as ONE SoT (plugins.requires_satisfied), called from the in-proc
    validator AND all three bridge runtimes (node / exec / py_bridge) at the
    same seam as capability_gate -- the @774 consolidation lesson applied on
    day one, so the admission decision cannot drift per-runtime. Fail-closed
    on malformed input with a NAMED reason (api_compatible's posture).
    Plugin-deps are checked against already-loaded entries: load order is the
    operator's plugins.json ``order``; the skip reason says so.
  * ``on_enable`` / ``on_disable`` -- a setup/teardown lifecycle pair distinct
    from the gated browser lifecycle. This cut wires the IN-PROC py loader
    (manifest declares the hook; the module provides the module-level
    callable; declared-but-missing is a fail-closed skip). on_disable fires
    from reset() BEFORE registries clear (so reload/unload tears down).
    Bridge-runtime enable/disable events are DEFERRED (documented) -- a
    convenience, not an admission gate, so per-runtime rollout is honest.

Cross-wave invariant: a manifest-less plugin loads byte-identically.

RED on pristine v3.66.776: "requires" is an unknown key (unsatisfied
requirements still LOAD); no enable/disable firing. GREEN after.

run_tests.py conventions: zero-arg test functions.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def _with_dir(tmp):
    orig = P._plugin_dir
    P._plugin_dir = lambda: Path(tmp)
    return orig


def _write(tmp, name, body):
    (Path(tmp) / name).write_text(body, "utf-8")


def _entry(res, name):
    return {x["filename"]: x for x in res["plugins"]}[name]


# ───────────────────────── requires: bd_min ─────────────────────────

def test_requires_bd_min_satisfied_loads():
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "low.py",
               "PLUGIN={'name':'low','api_version':2,"
               "'requires':{'bd_min':'3.0.0'}}\n")
        e = _entry(P.load_all(), "low.py")
        assert e["ok"] is True, e
    finally:
        P._plugin_dir = orig
        P.reset()


def test_requires_bd_min_too_high_skips_with_named_reason():
    """RED anchor: on 776 'requires' is ignored and this LOADS."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "hi.py",
               "PLUGIN={'name':'hi','api_version':2,"
               "'requires':{'bd_min':'99.0.0'}}\n")
        e = _entry(P.load_all(), "hi.py")
        assert e["ok"] is False and e["skipped_reason"], e
        assert "99.0.0" in e["skipped_reason"], e["skipped_reason"]
        assert "requires BD" in e["skipped_reason"], e["skipped_reason"]
    finally:
        P._plugin_dir = orig
        P.reset()


def test_requires_malformed_is_fail_closed():
    """A requirement that cannot be parsed cannot be VERIFIED -- fail closed
    with a named reason (api_compatible's malformed posture)."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "bad.py",
               "PLUGIN={'name':'bad','api_version':2,"
               "'requires':{'bd_min':'not-a-version'}}\n")
        e = _entry(P.load_all(), "bad.py")
        assert e["ok"] is False and e["skipped_reason"], e
        assert "bd_min" in e["skipped_reason"], e["skipped_reason"]
        # a non-dict requires is equally fail-closed
        _write(tmp, "bad2.py",
               "PLUGIN={'name':'bad2','api_version':2,'requires':'nope'}\n")
        e2 = _entry(P.load_all(), "bad2.py")
        assert e2["ok"] is False and "requires" in e2["skipped_reason"], e2
    finally:
        P._plugin_dir = orig
        P.reset()


# ───────────────────────── requires: plugin deps ─────────────────────────

def test_requires_plugin_dep_loaded_first_admits():
    """Dep sorted earlier (alphabetical default order) -> dependent loads."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "a_dep.py", "PLUGIN={'name':'a_dep','api_version':2}\n")
        _write(tmp, "b_needs.py",
               "PLUGIN={'name':'b_needs','api_version':2,"
               "'requires':{'plugins':['a_dep.py']}}\n")
        res = P.load_all()
        assert _entry(res, "a_dep.py")["ok"] is True
        assert _entry(res, "b_needs.py")["ok"] is True, _entry(res, "b_needs.py")
    finally:
        P._plugin_dir = orig
        P.reset()


def test_requires_missing_plugin_dep_skips_and_names_it():
    """RED anchor: names the missing dep AND the order remedy."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "needs.py",
               "PLUGIN={'name':'needs','api_version':2,"
               "'requires':{'plugins':['ghost.py']}}\n")
        e = _entry(P.load_all(), "needs.py")
        assert e["ok"] is False and e["skipped_reason"], e
        assert "ghost.py" in e["skipped_reason"], e["skipped_reason"]
        assert "order" in e["skipped_reason"], e["skipped_reason"]
    finally:
        P._plugin_dir = orig
        P.reset()


def test_requires_enforced_in_bridge_runtime():
    """The SoT reaches the subprocess runtimes: a # bd:bridge plugin whose
    manifest requires an impossible bd_min is SKIPPED by the py-bridge loader
    with the same named reason. RED anchor on 776 (bridge ignores requires)."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "br.py",
               "# bd:bridge\n"
               "import json, sys\n"
               "MAN = {'api_version': 2, 'kind': 'processor', 'name': 'br',\n"
               "       'requires': {'bd_min': '99.0.0'}}\n"
               "if __name__ == '__main__':\n"
               "    if '--manifest' in sys.argv:\n"
               "        print(json.dumps(MAN))\n"
               "    else:\n"
               "        print(json.dumps({'ok': True, 'result': {}}))\n")
        e = _entry(P.load_all(), "br.py")
        assert e["ok"] is False and e.get("skipped_reason"), e
        assert "99.0.0" in e["skipped_reason"], e["skipped_reason"]
    finally:
        P._plugin_dir = orig
        P.reset()


# ───────────────────── on_enable / on_disable ─────────────────────

def test_on_enable_fires_on_load():
    """RED anchor: manifest-declared on_enable runs after a successful load."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    marker = Path(tmp) / "enabled.marker"
    try:
        _write(tmp, "en.py",
               "from pathlib import Path\n"
               "PLUGIN={'name':'en','api_version':2,'on_enable':True}\n"
               f"def on_enable():\n    Path({str(marker)!r}).write_text('x')\n")
        e = _entry(P.load_all(), "en.py")
        assert e["ok"] is True, e
        assert marker.exists(), "on_enable did not fire"
    finally:
        P._plugin_dir = orig
        P.reset()


def test_on_disable_fires_on_reset_before_clear():
    """RED anchor: manifest-declared on_disable runs when reset() unloads."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    marker = Path(tmp) / "disabled.marker"
    try:
        _write(tmp, "dis.py",
               "from pathlib import Path\n"
               "PLUGIN={'name':'dis','api_version':2,'on_disable':True}\n"
               f"def on_disable():\n    Path({str(marker)!r}).write_text('x')\n")
        e = _entry(P.load_all(), "dis.py")
        assert e["ok"] is True, e
        assert not marker.exists()
        P.reset()
        assert marker.exists(), "on_disable did not fire on reset()"
    finally:
        P._plugin_dir = orig
        P.reset()


def test_declared_on_enable_missing_callable_is_fail_closed():
    """Declaring a hook the module does not provide is a manifest defect --
    fail-closed skip with a named reason. RED anchor on 776 (loads fine)."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "liar.py",
               "PLUGIN={'name':'liar','api_version':2,'on_enable':True}\n")
        e = _entry(P.load_all(), "liar.py")
        assert e["ok"] is False and "on_enable" in e["skipped_reason"], e
    finally:
        P._plugin_dir = orig
        P.reset()


def test_on_enable_failure_does_not_break_other_plugins():
    """A crashing on_enable is isolated: the OTHER plugin still loads and
    load_all completes (the guarded-call posture)."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "boom.py",
               "PLUGIN={'name':'boom','api_version':2,'on_enable':True}\n"
               "def on_enable():\n    raise RuntimeError('setup exploded')\n")
        _write(tmp, "ok.py", "PLUGIN={'name':'ok','api_version':2}\n")
        res = P.load_all()
        assert _entry(res, "ok.py")["ok"] is True
    finally:
        P._plugin_dir = orig
        P.reset()


# ───────────────────────── invariant ─────────────────────────

def test_manifest_less_plugin_unaffected():
    """Cross-wave invariant: no manifest -> loads exactly as before."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        _write(tmp, "plain.py", "X = 1\n")
        e = _entry(P.load_all(), "plain.py")
        assert e["ok"] is True, e
    finally:
        P._plugin_dir = orig
        P.reset()
