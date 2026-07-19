"""v3.66.805: plugin quarantine state honours BD_HOME.

Before this cut, ``plugins._quarantine_state_path()`` anchored the quarantine
state file at ``_plugin_dir()/.plugin_state.json`` -- i.e. INSIDE the install
tree (``INSTALL_DIR/plugins/``), regardless of ``BD_HOME``. That is runtime
state written into the deployed code tree: it leaked into the first 797 build
(the @798 manifest exclusion papered over the symptom), and an overlay deploy
could clobber or resurrect it.

This cut moves the STATE file (not the plugin CODE dir) to BD_HOME, mirroring
the established convention in ``interop_registry._registry_path`` and
``backup_verify`` (``os.environ.get("BD_HOME") or "."``). Plugin code still
loads from ``_plugin_dir()`` -- only the persisted quarantine map relocates.

Backward-compat: with BD_HOME UNSET the path stays under the plugin dir, so the
485 persistence contract and existing stash behaviour are unchanged.

Runner-safe: zero-arg fns, module globals restored in try/finally, paths from
tempfile, no pytest builtins.
"""
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def test_state_path_honours_bd_home():
    """RED on pristine: with BD_HOME set, the state path must land under it,
    NOT inside the install tree."""
    orig = os.environ.get("BD_HOME")
    home = tempfile.mkdtemp()
    try:
        os.environ["BD_HOME"] = home
        sp = P._quarantine_state_path()
        assert str(sp).startswith(str(Path(home).resolve())), (
            f"quarantine state must live under BD_HOME ({home}); "
            f"resolved to {sp} (still inside the install tree)")
        assert sp.name == ".plugin_state.json", sp
    finally:
        if orig is None:
            os.environ.pop("BD_HOME", None)
        else:
            os.environ["BD_HOME"] = orig


def test_state_path_falls_back_to_plugin_dir_when_bd_home_unset():
    """With BD_HOME UNSET, the path stays under _plugin_dir() -- preserving the
    485 persistence contract and current stash behaviour (no silent move)."""
    orig = os.environ.get("BD_HOME")
    orig_pd = P._plugin_dir
    tmp = tempfile.mkdtemp()
    try:
        os.environ.pop("BD_HOME", None)
        P._plugin_dir = lambda: Path(tmp)
        sp = P._quarantine_state_path()
        assert str(sp) == str(Path(tmp) / ".plugin_state.json"), sp
    finally:
        P._plugin_dir = orig_pd
        if orig is not None:
            os.environ["BD_HOME"] = orig


def test_explicit_plugin_dir_override_keeps_isolated_state_even_with_bd_home():
    """An EXPLICIT _plugin_dir override (external plugin dirs; the py_bridge
    tests) must keep its OWN co-located state file even when BD_HOME is set --
    otherwise every plugin dir shares one BD_HOME ledger and a quarantine in one
    set bleeds into another (this exact bleed broke test_v3_66_482 in dev)."""
    orig = os.environ.get("BD_HOME")
    orig_pd = P._plugin_dir
    home = tempfile.mkdtemp()
    override = tempfile.mkdtemp()
    try:
        os.environ["BD_HOME"] = home
        P._plugin_dir = lambda: Path(override)
        sp = P._quarantine_state_path()
        assert str(sp) == str(Path(override) / ".plugin_state.json"), (
            f"an explicit plugin-dir override must own its state ({override}); "
            f"resolved to {sp} -- shared-BD_HOME state bleeds across plugin sets")
    finally:
        P._plugin_dir = orig_pd
        if orig is None:
            os.environ.pop("BD_HOME", None)
        else:
            os.environ["BD_HOME"] = orig


def test_state_no_longer_inside_install_tree_under_bd_home():
    """The whole point: under BD_HOME the state path shares NO prefix with the
    install tree's plugin dir."""
    orig = os.environ.get("BD_HOME")
    home = tempfile.mkdtemp()
    try:
        os.environ["BD_HOME"] = home
        sp = P._quarantine_state_path()
        pdir = P._plugin_dir().resolve()
        assert not str(sp).startswith(str(pdir)), (
            f"state {sp} must not be under the install plugin dir {pdir}")
    finally:
        if orig is None:
            os.environ.pop("BD_HOME", None)
        else:
            os.environ["BD_HOME"] = orig
