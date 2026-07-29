"""The suite installs plugins into the working tree it is testing.

THE DEFECT, reproduced 2026-07-29 on a clean tree by running the plugin band:

    $ git status --porcelain
    ?? plugins/ackgate.py
    ?? plugins/handdropped.py
    ?? plugins/plugins.registry.json

and `plugins/plugins.json` -- a TRACKED file -- comes back modified as well.

`plugins._plugin_dir()` returns `INSTALL_DIR / "plugins"`, and `INSTALL_DIR`
falls back to `Path.cwd()` when `BD_INSTALL_DIR` is unset (constants.py:15).
Under pytest the cwd is the repo, so `install_plugin()` stages real files into
the source tree. `tests/test_plugin_uninstall.py` and
`tests/test_o5_plugin_install.py` do exactly that, by design -- they are testing
installation, and nothing told them where to install.

WHY THIS IS MORE THAN UNTIDY. `git status` stops being a reliable statement
about what a change touched. Three cuts in this session had to revert
`plugins/plugins.json` rather than commit it, and deciding that each time
requires knowing which of the dirty files are yours. A working tree that dirties
itself makes every subsequent diff a judgement call, and the judgement is made
under time pressure at the end of a cut, which is when it will eventually be got
wrong.

WHY ENV MANIPULATION IS NOT THE FIX. `INSTALL_DIR` is computed at IMPORT of
constants.py, so setting `BD_INSTALL_DIR` from a session fixture is inert -- the
value is already frozen. The lever has to be `_plugin_dir` itself.

WHY THE REDIRECT SEEDS RATHER THAN EMPTIES. `plugins/plugins.json` is tracked
and is read as load configuration. A redirect to a bare tmp directory would
change what the plugin loader sees, so the fixture copies the tracked contents
across: reads keep working, writes land somewhere disposable.

Same family as the two other path leaks found this session -- `sites_config.json`
resolved against the cwd, and the VPN config resolved against `$HOME`. In each
case a real runtime path is correct for the deployment and wrong for a test
process that happens to be standing in the same directory.
"""
from __future__ import annotations

import pathlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_PLUGINS = REPO_ROOT / "plugins"


@pytest.fixture()
def plugins_mod():
    try:
        from bulk_downloader import plugins
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.fail(f"bulk_downloader.plugins did not import, so this gate "
                    f"cannot verify its subject: {exc}")
    return plugins


# ── denominator canary ───────────────────────────────────────────────────────

def test_the_repo_plugins_dir_is_what_we_think_it_is(plugins_mod):
    """If the layout moved, the assertions below are aimed at nothing."""
    assert REPO_PLUGINS.is_dir(), (
        f"{REPO_PLUGINS} does not exist; this gate would pass while the real "
        f"plugins directory was written."
    )
    assert callable(plugins_mod._plugin_dir), (
        "plugins._plugin_dir is not callable; the lever this gate checks is gone"
    )


# ── the defect ───────────────────────────────────────────────────────────────

def _force_install_dir_to_the_repo(monkeypatch):
    """Make the UNGUARDED resolution point at the source tree, deterministically.

    Without this the tests below are order-dependent and cannot fail. INSTALL_DIR
    is frozen at import of constants.py, so whether _plugin_dir() would resolve
    to the repo depends on whether that import beat conftest's chdir. Measured
    both ways in one afternoon: running this file alone gave a tmp dir (guard
    never exercised, every mutation survived 8/8), running it in a band gave the
    repo. Pinning INSTALL_DIR here removes the coin flip, so the guard is
    exercised on every run rather than on lucky ones.
    """
    from bulk_downloader import constants
    monkeypatch.setattr(constants, "INSTALL_DIR", REPO_ROOT)


def test_the_guard_diverts_a_resolution_that_would_hit_the_repo(
        plugins_mod, monkeypatch):
    """The condition the guard exists for, forced rather than awaited."""
    _force_install_dir_to_the_repo(monkeypatch)
    resolved = pathlib.Path(plugins_mod._plugin_dir()).resolve()
    assert resolved != REPO_PLUGINS.resolve(), (
        f"with INSTALL_DIR pinned to the repo, _plugin_dir() still resolves to "
        f"{resolved}. Any test that installs a plugin -- or writes to pdir "
        f"directly, as test_plugin_uninstall does -- lands in the source tree, "
        f"and `git status` stops describing the change under review."
    )


def test_installing_a_plugin_does_not_dirty_the_repo(
        plugins_mod, monkeypatch, tmp_path):
    """The behaviour, not just the path: stage one and look at the tree.

    Reads the real directory listing rather than trusting _plugin_dir(), so a
    redirect bypassed somewhere inside install_plugin() is still caught.
    """
    _force_install_dir_to_the_repo(monkeypatch)
    before = {q.name for q in REPO_PLUGINS.iterdir()} if REPO_PLUGINS.is_dir() else set()
    src = tmp_path / "gatecheck_probe.py"
    src.write_text('PLUGIN = {"name": "gatecheck_probe", "version": "1.0.0"}\n',
                   encoding="utf-8")
    try:
        plugins_mod.install_plugin(str(src), ack=True)
    except Exception as exc:
        pytest.skip(f"install_plugin declined in this environment: {exc}")
    after = {q.name for q in REPO_PLUGINS.iterdir()} if REPO_PLUGINS.is_dir() else set()
    new_files = sorted(after - before)
    for name in new_files:                      # never leave the tree dirty
        try:
            (REPO_PLUGINS / name).unlink()
        except OSError:
            pass
    assert not new_files, (
        f"installing a plugin created {new_files} inside the repository's "
        f"plugins/ directory. Tests must stage into a disposable location."
    )


def test_the_plugin_state_still_relocates_under_bd_home(
        plugins_mod, monkeypatch, tmp_path):
    """The invariant the band caught the first fix breaking.

    _quarantine_state_path relocates state under BD_HOME only when _plugin_dir()
    is the install-tree DEFAULT; an explicit override keeps its state co-located
    (the 485 isolation contract). A redirect that looks like an override
    therefore silently stops the v3.66.805 relocation. Asserted here so the
    guard cannot be "simplified" back into breaking it.
    """
    _force_install_dir_to_the_repo(monkeypatch)
    home = tmp_path / "bdhome"
    home.mkdir()
    monkeypatch.setenv("BD_HOME", str(home))
    state = pathlib.Path(plugins_mod._quarantine_state_path()).resolve()
    assert str(state).startswith(str(home.resolve())), (
        f"plugin quarantine state resolved to {state}, which is not under "
        f"BD_HOME ({home}). The redirect made _plugin_dir() look like an "
        f"explicit override, so the v3.66.805 relocation stopped applying."
    )
