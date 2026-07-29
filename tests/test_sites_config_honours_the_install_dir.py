"""The suite writes a permanent site into the operator's real sites_config.json.

THE DEFECT. `bulk_downloader/app.py` defines

    SITES_FILE = Path("sites_config.json")

which is RELATIVE, so it resolves against the process cwd at use time.
`tests/test_e2e_smoke.py::_RealE2ESmoke.setUpClass` POSTs `/api/sites` to create
"E2E Test Site", and `tearDownClass` never deletes it.

The harness around it is not careless -- it sets `BD_INSTALL_DIR` to a temp dir,
clears every `bulk_downloader` module from `sys.modules` so the new value takes
effect, and re-imports. `constants.INSTALL_DIR` is genuinely isolated. The site
config escapes anyway, because a relative path never consults INSTALL_DIR at
all: it follows the cwd, which is wherever pytest happens to be standing.

Measured 2026-07-29: the deploy box and this sandbox each hold **7** entries, all
named "E2E Test Site". At four or more sites the SPA collapses idle sites by
default, so this is already changing what the operator sees, and every
site-count denominator in a capture bundle is inflated by it -- the selftest
line reads "7 site(s)" and L8 reads "9 site(s) configured".

WHY NOT `Path(INSTALL_DIR) / "sites_config.json"`, WHICH LOOKS RIGHT.
INSTALL_DIR is frozen at import of constants.py and falls back to `Path.cwd()`.
Whether that import beats conftest's chdir decides its value for the whole
session -- the trap that made the plugins-directory leak survive so long, where
one file gave a tmp dir and a band gave the repo. Binding SITES_FILE to a
possibly-repo-frozen INSTALL_DIR would PIN the leak to the source tree for every
ordinary test, where today the relative path at least follows conftest's chdir
into a tmp dir. The fix has to key off the env var directly, which the harness
sets and which nothing freezes.

So: absolute under BD_INSTALL_DIR when it is set, and the existing relative
behaviour when it is not. On the box BD_INSTALL_DIR is unset and the service's
cwd is its install root, so nothing about the deployment changes and no
migration is needed.

IT MUST REMAIN A MODULE ATTRIBUTE. Ten test files reference SITES_FILE and at
least one monkeypatches it (`monkeypatch.setattr(app_mod, "SITES_FILE", ...)`).
Turning it into a call-time resolver would leave those patches inert -- the
same shape as the secrets_store cut, where seven files patched the module
attribute and a resolver would have silently ignored all of them.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SITES = REPO_ROOT / "sites_config.json"


def _reimport_app_with(install_dir: str | None):
    """Re-import app the way the e2e harness does, and return its SITES_FILE.

    Deterministic by construction: it does not care what INSTALL_DIR happened to
    freeze to earlier in the session, which is exactly the order-dependence that
    made two earlier gates in this session unable to fail.
    """
    saved_env = os.environ.get("BD_INSTALL_DIR")
    saved_mods = {k: v for k, v in sys.modules.items()
                  if k.startswith("bulk_downloader")}
    try:
        if install_dir is None:
            os.environ.pop("BD_INSTALL_DIR", None)
        else:
            os.environ["BD_INSTALL_DIR"] = install_dir
        for name in list(sys.modules):
            if name.startswith("bulk_downloader"):
                del sys.modules[name]
        app_mod = importlib.import_module("bulk_downloader.app")
        return Path(app_mod.SITES_FILE)
    finally:
        for name in list(sys.modules):
            if name.startswith("bulk_downloader"):
                del sys.modules[name]
        sys.modules.update(saved_mods)
        if saved_env is None:
            os.environ.pop("BD_INSTALL_DIR", None)
        else:
            os.environ["BD_INSTALL_DIR"] = saved_env


# ── denominator canary ───────────────────────────────────────────────────────

def test_sites_file_is_still_a_module_attribute():
    """Ten test files reference it and at least one monkeypatches it.

    If it stops being an attribute, every one of those patches goes inert and
    the tests keep passing while pointing at the wrong file.
    """
    from bulk_downloader import app as app_mod
    assert isinstance(getattr(app_mod, "SITES_FILE", None), Path), (
        "app.SITES_FILE is not a Path attribute. Tests monkeypatch it with "
        "setattr; a call-time resolver would silently ignore all of them."
    )


# ── the defect ───────────────────────────────────────────────────────────────

def test_sites_config_lands_under_bd_install_dir(tmp_path):
    """The isolation the e2e harness already asks for, actually honoured."""
    isolated = tmp_path / "install"
    isolated.mkdir()
    resolved = _reimport_app_with(str(isolated))
    assert str(resolved).startswith(str(isolated.resolve())), (
        f"with BD_INSTALL_DIR={isolated}, SITES_FILE resolved to {resolved}. "
        f"The e2e harness sets that variable, clears sys.modules and re-imports "
        f"specifically to isolate this -- but a relative path never consults it, "
        f"so 'E2E Test Site' is written into whatever directory pytest is "
        f"standing in. Seven of them are on the deploy box."
    )


def test_an_unset_install_dir_keeps_the_existing_behaviour():
    """The box has BD_INSTALL_DIR unset; its layout must not move.

    Guards against a fix that helpfully relocates the operator's live config and
    orphans the sites they already have.
    """
    resolved = _reimport_app_with(None)
    assert not resolved.is_absolute() or resolved.parent == Path.cwd(), (
        f"with BD_INSTALL_DIR unset, SITES_FILE resolved to {resolved}, which "
        f"is neither the historical relative path nor the current working "
        f"directory. On the box that would orphan the operator's existing "
        f"sites_config.json and their sites would appear to vanish."
    )


def test_the_repo_copy_is_not_the_target_when_isolation_is_requested(tmp_path):
    """The specific bad outcome: pinning to the source tree.

    Binding SITES_FILE to constants.INSTALL_DIR -- which is frozen at import and
    falls back to Path.cwd() -- would resolve to the repository whenever that
    import beat conftest's chdir. That is worse than today, where the relative
    path at least follows the chdir into a tmp dir.
    """
    isolated = tmp_path / "install2"
    isolated.mkdir()
    resolved = _reimport_app_with(str(isolated)).resolve()
    assert resolved != REPO_SITES.resolve(), (
        f"SITES_FILE resolved to the repository's own {REPO_SITES.name} while "
        f"BD_INSTALL_DIR asked for {isolated}. The fix must key off the env var, "
        f"not off a cwd-frozen INSTALL_DIR."
    )


def test_a_frozen_install_dir_does_not_capture_the_config(monkeypatch):
    """The mutation the other tests could not see.

    Binding SITES_FILE to constants.INSTALL_DIR passes every test above, because
    the re-import helper sets BD_INSTALL_DIR and clears sys.modules, so
    INSTALL_DIR recomputes to the isolated directory and both forms agree.

    They diverge in the case that actually bites: BD_INSTALL_DIR UNSET, with
    INSTALL_DIR already frozen to the repository because its import beat the
    suite's chdir. The env-keyed form returns the relative path, which follows
    the chdir into a tmp dir. The INSTALL_DIR-bound form returns the source
    tree's own sites_config.json and pins every ordinary test to it.

    Found by mutation, not by reading -- the same way Cut B's guard was found to
    be unfalsifiable earlier in this session.
    """
    from bulk_downloader import app as app_mod, constants
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    monkeypatch.setattr(constants, "INSTALL_DIR", REPO_ROOT)
    resolved = Path(app_mod._resolve_sites_file())
    assert resolved.resolve() != REPO_SITES.resolve(), (
        f"with BD_INSTALL_DIR unset and INSTALL_DIR frozen to the repo, "
        f"SITES_FILE resolved to {resolved} -- the source tree's own config. "
        f"Every ordinary test would then write there, which is worse than the "
        f"relative path it replaced."
    )
