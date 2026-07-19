"""O5: plugin registry + atomic install + version-range gate + at-your-own-risk ack.

RED-first (v3.66.501). Pure filesystem + ast -- no network, no browser, no Flask.
Sandbox conventions: derive repo root from __file__; zero-arg test fns; no pytest
builtins (tempfile.mkdtemp, not tmp_path); no monkeypatch (override module globals
in try/finally and restore).

The install path NEVER executes plugin code: it ast-reads the PLUGIN manifest,
gates on version-range (api_compatible / R5) + an at-your-own-risk acknowledgment,
stages atomically (os.replace), and records the install in plugins.registry.json.
Loading stays the separate, already-gated load_all() concern.
"""
import os
import sys
import json
import shutil
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bulk_downloader import plugins  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────
def _with_tmp_plugin_dir():
    """Point plugins._plugin_dir at a fresh temp dir. Returns (pdir, restore).

    Internal callers all look up the module-global _plugin_dir at call time, so
    rebinding the attribute redirects the whole subsystem (incl. the new install
    functions). restore() puts it back and removes the temp tree.
    """
    tmp = tempfile.mkdtemp()
    pdir = Path(tmp) / "plugins"
    pdir.mkdir(parents=True, exist_ok=True)
    orig = plugins._plugin_dir
    plugins._plugin_dir = lambda: pdir

    def restore():
        plugins._plugin_dir = orig
        shutil.rmtree(tmp, ignore_errors=True)

    return pdir, restore


def _candidate(text, name="plug.py"):
    """Write a plugin SOURCE file (the thing we install FROM) to its own temp
    dir under `name`, so two candidates can share a basename without colliding."""
    d = tempfile.mkdtemp()
    p = Path(d) / name
    p.write_text(text, "utf-8")
    return p


def _man(version="1.0.0", **extra):
    keys = {"name": "x", "version": version, "api_version": 2}
    keys.update(extra)
    return "PLUGIN = " + json.dumps(keys) + "\n"


# ── a) ack gate (replaces the dropped signing invariant) ───────────────
def test_install_refused_without_ack_then_allowed():
    pdir, restore = _with_tmp_plugin_dir()
    try:
        src = _candidate(_man(name="good"), name="good.py")

        res = plugins.install_plugin(str(src), ack=False)
        assert res.get("installed") is False
        reason = (res.get("reason") or "").lower()
        assert "risk" in reason or "acknowled" in reason
        assert not (pdir / "good.py").exists()

        # acknowledge via the persisted plugins.json flag, then retry
        plugins.write_config({"risk_acknowledged": True})
        res2 = plugins.install_plugin(str(src), ack=False)
        assert res2.get("installed") is True
        assert (pdir / "good.py").exists()
    finally:
        restore()


def test_per_call_ack_flag_allows_install():
    pdir, restore = _with_tmp_plugin_dir()
    try:
        src = _candidate(_man(name="g2"), name="g2.py")
        res = plugins.install_plugin(str(src), ack=True)  # flag unset, ack passed
        assert res.get("installed") is True
        assert (pdir / "g2.py").exists()
    finally:
        restore()


# ── b) version-range gate at install (reuses api_compatible / R5) ──────
def test_install_refused_on_incompatible_api():
    pdir, restore = _with_tmp_plugin_dir()
    try:
        bad = _candidate(_man(name="hi", min_api=99), name="hi.py")
        res = plugins.install_plugin(str(bad), ack=True)
        assert res.get("installed") is False
        assert not (pdir / "hi.py").exists()

        good = _candidate(_man(name="ok", api_version=2), name="ok.py")
        assert plugins.install_plugin(str(good), ack=True).get("installed") is True

        # manifest is optional -> a file with no PLUGIN still installs
        nomani = _candidate("# no manifest here\nX = 1\n", name="bare.py")
        assert plugins.install_plugin(str(nomani), ack=True).get("installed") is True
    finally:
        restore()


# ── c) atomic stage: a failed commit leaves no loadable partial ────────
def test_install_atomic_no_partial_on_failure():
    pdir, restore = _with_tmp_plugin_dir()
    orig_replace = os.replace
    try:
        plugins.write_config({"risk_acknowledged": True})
        src = _candidate(_man(name="z"), name="z.py")

        def _boom(a, b):
            raise OSError("simulated replace failure")

        os.replace = _boom
        try:
            plugins.install_plugin(str(src), ack=True)
        except OSError:
            pass  # raise-or-return both acceptable; invariants below are the gate

        assert not (pdir / "z.py").exists()
        assert not any(p.name.endswith(".incoming") for p in pdir.iterdir())
        assert "z.py" not in [p.name for p in plugins._plugin_files(pdir)]
    finally:
        os.replace = orig_replace
        restore()

    # a clean install IS discoverable
    pdir2, restore2 = _with_tmp_plugin_dir()
    try:
        plugins.write_config({"risk_acknowledged": True})
        src = _candidate(_man(name="z2"), name="z2.py")
        plugins.install_plugin(str(src), ack=True)
        assert "z2.py" in [p.name for p in plugins._plugin_files(pdir2)]
    finally:
        restore2()


# ── d) registry record + in-place upgrade ─────────────────────────────
def test_registry_records_and_upgrades():
    pdir, restore = _with_tmp_plugin_dir()
    try:
        plugins.write_config({"risk_acknowledged": True})
        plugins.install_plugin(str(_candidate(_man("1.0.0", name="plex"), "plex.py")), ack=True)

        rec = [r for r in plugins.installed_registry() if r.get("file") == "plex.py"]
        assert rec, "install must record a registry entry"
        assert rec[0].get("version") == "1.0.0"
        assert (rec[0].get("source") or "").startswith("local:")
        assert rec[0].get("installed_at")

        plugins.install_plugin(str(_candidate(_man("1.2.0", name="plex"), "plex.py")), ack=True)
        rec2 = [r for r in plugins.installed_registry() if r.get("file") == "plex.py"][0]
        assert rec2.get("version") == "1.2.0"
        assert rec2.get("previous_version") == "1.0.0"
    finally:
        restore()


def test_force_required_to_overwrite_unregistered_file():
    pdir, restore = _with_tmp_plugin_dir()
    try:
        plugins.write_config({"risk_acknowledged": True})
        # a hand-dropped file the registry never recorded
        (pdir / "manual.py").write_text(_man(name="manual"), "utf-8")
        src = _candidate(_man("2.0.0", name="manual"), "manual.py")
        res = plugins.install_plugin(str(src), ack=True)        # force defaults False
        assert res.get("installed") is False
        forced = plugins.install_plugin(str(src), ack=True, force=True)
        assert forced.get("installed") is True
    finally:
        restore()


# ── e) install must not execute the candidate module ──────────────────
def test_install_does_not_execute_module():
    pdir, restore = _with_tmp_plugin_dir()
    sentinel = Path(tempfile.mkdtemp()) / "ran.flag"
    try:
        plugins.write_config({"risk_acknowledged": True})
        text = _man(name="se") + "open(r'%s', 'w').write('x')\n" % sentinel
        res = plugins.install_plugin(str(_candidate(text, "se.py")), ack=True)
        assert res.get("installed") is True
        assert not sentinel.exists(), "install must ast-read, never exec the module"
    finally:
        restore()
        shutil.rmtree(sentinel.parent, ignore_errors=True)


# ── f) backward-compat: hand-dropped loads, just no registry entry ────
def test_handdropped_loads_without_registry_entry():
    pdir, restore = _with_tmp_plugin_dir()
    try:
        (pdir / "hd.py").write_text(
            "from bulk_downloader import plugins\n"
            "@plugins.hook('download.done')\n"
            "def _h(payload):\n    return None\n",
            "utf-8",
        )
        plugins.load_all()
        assert "hd.py" in [e.get("filename") for e in plugins._loaded]
        assert "hd.py" not in [r.get("file") for r in plugins.installed_registry()]
    finally:
        restore()
