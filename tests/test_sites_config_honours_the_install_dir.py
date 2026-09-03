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


def _empty_site_registries(monkeypatch, app_mod):
    monkeypatch.setattr(app_mod, "runners", {})
    monkeypatch.setattr(app_mod, "s_cfg", {})
    monkeypatch.setattr(app_mod, "s_meta", {})


def _prepare_real_boot(monkeypatch, app_mod):
    db_mod = importlib.import_module("bulk_downloader.db")
    migrations = importlib.import_module("bulk_downloader.migrations")
    run_history = importlib.import_module("bulk_downloader.run_history")

    _empty_site_registries(monkeypatch, app_mod)
    monkeypatch.setattr(app_mod, "_BOOTED_PATHS", set())
    monkeypatch.setattr(app_mod, "_SITE_RUNTIME_PATH", None)
    monkeypatch.setattr(app_mod, "_SITE_RUNTIME_READY", False)
    monkeypatch.setattr(app_mod, "_SITE_RUNTIME_ROLLBACK_PENDING", False)
    monkeypatch.setattr(app_mod, "_SITES_CONFIG_REACHABILITY", None, raising=False)
    monkeypatch.setattr(app_mod, "_APP_CFG_SEED_PENDING", None)
    monkeypatch.setattr(app_mod, "db_init", lambda: None)
    monkeypatch.setattr(app_mod, "db_integrity_check", lambda: (True, []))
    monkeypatch.setattr(db_mod, "db_fts_optimize", lambda: (False, "not run"))
    monkeypatch.setattr(db_mod, "db_queue_recovery_summary", lambda: {"total": 0})
    monkeypatch.setattr(db_mod, "run_integrity_check", lambda: None)
    monkeypatch.setattr(migrations, "apply_pending", lambda: {"applied": 0, "errors": 0})
    monkeypatch.setattr(run_history, "init", lambda: None)
    monkeypatch.setattr(app_mod, "_init_vpn_runtime", lambda: {"ok": True})
    monkeypatch.setattr(app_mod, "_start_session_keepers", lambda: None)
    monkeypatch.setattr(app_mod, "_start_watch_folder_threads", lambda: None)
    monkeypatch.setattr(app_mod, "_start_watcher", lambda: None)
    monkeypatch.setattr(app_mod, "_start_background_services", lambda: None)


_UNSUPPORTED_OBSERVATION = object()


def _assert_optional_observation(app_mod, name, expected):
    actual = getattr(app_mod, name, _UNSUPPORTED_OBSERVATION)
    if actual is not _UNSUPPORTED_OBSERVATION:
        assert actual == expected


def _assert_optional_reachability(app_mod, expected):
    reporter = getattr(app_mod, "_sites_config_reachability", None)
    if reporter is not None:
        assert reporter() == expected


@pytest.mark.parametrize("configured_install_dir", [False, True])
def test_a_fresh_install_is_healthy_after_the_real_boot_publication_order(
    tmp_path, monkeypatch, capsys, configured_install_dir
):
    from bulk_downloader import app as app_mod
    app_health = sys.modules["bulk_downloader.app_health"]

    install_dir = tmp_path / "fresh-install"
    install_dir.mkdir()
    monkeypatch.chdir(install_dir)
    if configured_install_dir:
        monkeypatch.setenv("BD_INSTALL_DIR", str(install_dir))
    else:
        monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    pristine = Path("sites_config.json")
    monkeypatch.setattr(app_mod, "SITES_FILE", pristine)
    monkeypatch.setattr(app_mod, "_SITES_FILE_LAST_AUTO_OBJECT", pristine)
    monkeypatch.setattr(
        app_mod, "_SITES_FILE_RUNTIME_PUBLISHED_OBJECT", None, raising=False
    )
    _prepare_real_boot(monkeypatch, app_mod)
    expected_path = app_mod._resolved_site_runtime_path()
    assert Path(expected_path).is_absolute()
    assert Path(expected_path).parent == install_dir.resolve()
    assert Path(expected_path).exists() is False
    real_publish = app_mod._publish_sites_file_for_runtime
    published = []

    def record_publish(config_path):
        published.append(Path(config_path))
        real_publish(config_path)

    monkeypatch.setattr(app_mod, "_publish_sites_file_for_runtime", record_publish)
    capsys.readouterr()

    assert app_mod.boot_once() is True

    assert published == [Path(expected_path), Path(expected_path)]
    _assert_optional_observation(
        app_mod, "_SITES_FILE_EXISTED_AT_PUBLICATION", False
    )
    assert app_mod.SITES_FILE == Path(expected_path)
    assert app_mod.SITES_FILE.exists() is False
    assert capsys.readouterr().err == ""
    _assert_optional_reachability(app_mod, {
        "ok": True,
        "path": str(expected_path),
        "state": "first_run",
    })
    assert (len(app_mod.runners), len(app_mod.s_cfg), len(app_mod.s_meta)) == (0, 0, 0)

    class _HealthyDb:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _sql):
            return self

        def fetchone(self):
            return (1,)

    monkeypatch.setattr(app_health, "_app_runners", lambda: app_mod.runners)
    monkeypatch.setattr(app_health, "_app_s_cfg", lambda: app_mod.s_cfg)
    monkeypatch.setattr(app_health, "_app__app_boot_time", lambda: 0.0)
    monkeypatch.setattr(app_health, "app_test_mode", lambda: False)
    monkeypatch.setattr(app_health, "db_conn", _HealthyDb)
    monkeypatch.setattr(app_health, "_attach_credential_health", lambda *_args: None)
    monkeypatch.setattr(app_health, "_attach_download_hold", lambda *_args: None)
    monkeypatch.setattr(app_health, "build_identity", lambda _root: {})
    response = app_mod.app.test_client().get("/api/health")
    payload = response.get_json()
    assert response.status_code == 200
    assert published == [Path(expected_path)] * 3
    assert payload["ok"] is True
    if "sites_config" in payload:
        assert payload["sites_config"]["state"] == "first_run"
    assert payload["sites_loaded"] == 0


def test_a_published_missing_pin_is_unknown_and_visible_on_health(
    tmp_path, monkeypatch, capsys
):
    from bulk_downloader import app as app_mod
    app_health = sys.modules["bulk_downloader.app_health"]

    _empty_site_registries(monkeypatch, app_mod)
    monkeypatch.setattr(
        app_mod, "_SITES_FILE_RUNTIME_PUBLISHED_OBJECT", None, raising=False
    )
    if hasattr(app_mod, "_SITES_FILE_EXISTED_AT_PUBLICATION"):
        monkeypatch.setattr(app_mod, "_SITES_FILE_EXISTED_AT_PUBLICATION", None)
    import_time_sites_file = app_mod.SITES_FILE
    assert import_time_sites_file is app_mod._SITES_FILE_LAST_AUTO_OBJECT
    monkeypatch.setattr(app_mod, "SITES_FILE", import_time_sites_file)
    monkeypatch.setattr(
        app_mod, "_SITES_FILE_LAST_AUTO_OBJECT", import_time_sites_file
    )

    removed_parent = tmp_path / "published-install"
    removed_parent.mkdir()
    monkeypatch.setenv("BD_INSTALL_DIR", str(removed_parent))
    unreachable = (removed_parent / "sites_config.json").resolve()
    unreachable.write_text("{}", encoding="utf-8")
    assert unreachable.is_file()
    assert unreachable.stat().st_size == 2
    _prepare_real_boot(monkeypatch, app_mod)
    real_publish = app_mod._publish_sites_file_for_runtime
    published = []

    def publish_then_remove(config_path):
        published.append(Path(config_path))
        real_publish(config_path)
        if len(published) == 1:
            assert app_mod.SITES_FILE.is_file()
            _assert_optional_observation(
                app_mod, "_SITES_FILE_EXISTED_AT_PUBLICATION", True
            )
            app_mod.SITES_FILE.unlink()
            app_mod.SITES_FILE.parent.rmdir()

    monkeypatch.setattr(
        app_mod, "_publish_sites_file_for_runtime", publish_then_remove
    )
    capsys.readouterr()  # discard import-time route-registration diagnostics

    assert app_mod.boot_once() is True

    assert published == [unreachable, unreachable]
    assert app_mod.SITES_FILE is not import_time_sites_file
    assert app_mod.SITES_FILE is app_mod._SITES_FILE_LAST_AUTO_OBJECT
    assert app_mod.SITES_FILE.is_absolute() is True
    assert app_mod.SITES_FILE.exists() is False

    diagnostics = capsys.readouterr().err.splitlines()
    assert len(diagnostics) == 1, diagnostics
    assert diagnostics[0].count("sites_config path is UNKNOWN") == 1
    assert diagnostics[0].count(str(unreachable)) == 1
    assert len(app_mod.runners) == 0
    assert len(app_mod.s_cfg) == 0
    assert len(app_mod.s_meta) == 0
    expected = {
        "ok": False,
        "path": str(unreachable),
        "state": "unknown",
    }
    _assert_optional_reachability(app_mod, expected)

    class _HealthyDb:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _sql):
            return self

        def fetchone(self):
            return (1,)

    monkeypatch.setattr(app_health, "_app_runners", lambda: app_mod.runners)
    monkeypatch.setattr(app_health, "_app_s_cfg", lambda: app_mod.s_cfg)
    monkeypatch.setattr(app_health, "_app__app_boot_time", lambda: 0.0)
    monkeypatch.setattr(app_health, "app_test_mode", lambda: False)
    monkeypatch.setattr(app_health, "db_conn", _HealthyDb)
    monkeypatch.setattr(app_health, "_attach_credential_health", lambda *_args: None)
    monkeypatch.setattr(app_health, "_attach_download_hold", lambda *_args: None)
    monkeypatch.setattr(app_health, "build_identity", lambda _root: {})
    response = app_mod.app.test_client().get("/api/health")
    payload = response.get_json()
    assert response.status_code == 503
    assert published == [unreachable] * 3
    assert payload["ok"] is False
    assert payload["degraded"] == "sites_config_unknown"
    assert payload["sites_config"] == expected
    assert payload["sites_loaded"] == 0


def test_a_published_missing_file_is_unknown_while_its_parent_still_exists(
    tmp_path, monkeypatch, capsys
):
    from bulk_downloader import app as app_mod

    _empty_site_registries(monkeypatch, app_mod)
    monkeypatch.setattr(
        app_mod, "_SITES_FILE_RUNTIME_PUBLISHED_OBJECT", None, raising=False
    )
    if hasattr(app_mod, "_SITES_FILE_EXISTED_AT_PUBLICATION"):
        monkeypatch.setattr(app_mod, "_SITES_FILE_EXISTED_AT_PUBLICATION", None)
    import_time_sites_file = app_mod.SITES_FILE
    assert import_time_sites_file is app_mod._SITES_FILE_LAST_AUTO_OBJECT
    monkeypatch.setattr(app_mod, "SITES_FILE", import_time_sites_file)
    monkeypatch.setattr(
        app_mod, "_SITES_FILE_LAST_AUTO_OBJECT", import_time_sites_file
    )

    live_parent = tmp_path / "published-install-without-config"
    live_parent.mkdir()
    monkeypatch.setenv("BD_INSTALL_DIR", str(live_parent))
    missing = (live_parent / "sites_config.json").resolve()
    missing.write_text("{}", encoding="utf-8")
    assert missing.is_file()
    assert missing.stat().st_size == 2
    _prepare_real_boot(monkeypatch, app_mod)
    real_publish = app_mod._publish_sites_file_for_runtime
    published = []

    def publish_then_remove(config_path):
        published.append(Path(config_path))
        real_publish(config_path)
        if len(published) == 1:
            assert app_mod.SITES_FILE.is_file()
            _assert_optional_observation(
                app_mod, "_SITES_FILE_EXISTED_AT_PUBLICATION", True
            )
            app_mod.SITES_FILE.unlink()

    monkeypatch.setattr(
        app_mod, "_publish_sites_file_for_runtime", publish_then_remove
    )
    capsys.readouterr()

    assert app_mod.boot_once() is True

    assert published == [missing, missing]
    assert app_mod.SITES_FILE is not import_time_sites_file
    assert app_mod.SITES_FILE.is_absolute() is True
    assert app_mod.SITES_FILE.exists() is False
    assert app_mod.SITES_FILE.parent.is_dir() is True

    diagnostics = capsys.readouterr().err.splitlines()
    assert len(diagnostics) == 1, diagnostics
    assert diagnostics[0].count("sites_config path is UNKNOWN") == 1
    assert diagnostics[0].count(str(missing)) == 1
    _assert_optional_reachability(app_mod, {
        "ok": False,
        "path": str(missing),
        "state": "unknown",
    })
    assert (len(app_mod.runners), len(app_mod.s_cfg), len(app_mod.s_meta)) == (0, 0, 0)


def test_a_published_unreadable_file_is_unknown_not_malformed(
    tmp_path, monkeypatch, capsys
):
    from bulk_downloader import app as app_mod

    pristine = Path("sites_config.json")
    monkeypatch.setattr(app_mod, "SITES_FILE", pristine)
    monkeypatch.setattr(app_mod, "_SITES_FILE_LAST_AUTO_OBJECT", pristine)
    monkeypatch.setattr(
        app_mod, "_SITES_FILE_RUNTIME_PUBLISHED_OBJECT", None, raising=False
    )
    if hasattr(app_mod, "_SITES_FILE_EXISTED_AT_PUBLICATION"):
        monkeypatch.setattr(app_mod, "_SITES_FILE_EXISTED_AT_PUBLICATION", None)
    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))
    _prepare_real_boot(monkeypatch, app_mod)
    unreadable = (tmp_path / "sites_config.json").resolve()
    unreadable.write_text("{}", encoding="utf-8")
    assert unreadable.is_file()
    assert unreadable.stat().st_size == 2
    real_read_text = Path.read_text
    refused_reads = []

    def refuse_config_read(path, *args, **kwargs):
        if path == unreadable:
            refused_reads.append(path)
            raise PermissionError("test-only unreadable sites config")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse_config_read)
    capsys.readouterr()

    assert app_mod.boot_once() is True

    assert refused_reads == [unreadable]
    diagnostics = capsys.readouterr().err.splitlines()
    assert len(diagnostics) == 1, diagnostics
    assert diagnostics[0].count("sites_config path is UNKNOWN") == 1
    assert diagnostics[0].count(str(unreadable)) == 1
    assert diagnostics[0].count("malformed") == 0
    _assert_optional_reachability(app_mod, {
        "ok": False,
        "path": str(unreadable),
        "state": "unknown",
    })
    assert (len(app_mod.runners), len(app_mod.s_cfg), len(app_mod.s_meta)) == (0, 0, 0)


def test_a_pristine_relative_missing_config_remains_a_silent_first_run(
    tmp_path, monkeypatch, capsys
):
    from bulk_downloader import app as app_mod

    _empty_site_registries(monkeypatch, app_mod)
    monkeypatch.chdir(tmp_path)
    pristine = Path("sites_config.json")
    monkeypatch.setattr(app_mod, "SITES_FILE", pristine)
    monkeypatch.setattr(app_mod, "_SITES_FILE_LAST_AUTO_OBJECT", pristine)
    monkeypatch.setattr(
        app_mod, "_SITES_FILE_RUNTIME_PUBLISHED_OBJECT", None, raising=False
    )
    monkeypatch.setattr(app_mod, "_SITES_CONFIG_REACHABILITY", None, raising=False)
    assert app_mod.SITES_FILE.is_absolute() is False
    assert app_mod.SITES_FILE.exists() is False

    assert app_mod._load_sites_config() is None

    assert capsys.readouterr().err == ""
    _assert_optional_reachability(app_mod, {
        "ok": True,
        "path": "sites_config.json",
        "state": "first_run",
    })
    assert (len(app_mod.runners), len(app_mod.s_cfg), len(app_mod.s_meta)) == (0, 0, 0)


def test_a_present_malformed_config_keeps_its_distinctive_single_diagnostic(
    tmp_path, monkeypatch, capsys
):
    from bulk_downloader import app as app_mod

    _empty_site_registries(monkeypatch, app_mod)
    malformed = tmp_path / "sites_config.json"
    malformed.write_text("{", encoding="utf-8")
    monkeypatch.setattr(app_mod, "SITES_FILE", malformed)
    monkeypatch.setattr(app_mod, "_SITES_FILE_LAST_AUTO_OBJECT", malformed)
    monkeypatch.setattr(
        app_mod, "_SITES_FILE_RUNTIME_PUBLISHED_OBJECT", None, raising=False
    )
    monkeypatch.setattr(app_mod, "_SITES_CONFIG_REACHABILITY", None, raising=False)
    assert malformed.is_file()

    assert app_mod._load_sites_config() is None

    diagnostics = capsys.readouterr().err.splitlines()
    assert len(diagnostics) == 1, diagnostics
    assert diagnostics[0].count("sites_config.json malformed, ignoring:") == 1
    assert diagnostics[0].count("sites_config path is UNKNOWN") == 0
    _assert_optional_reachability(app_mod, {
        "ok": True,
        "path": str(malformed),
        "state": "reachable",
    })
    assert (len(app_mod.runners), len(app_mod.s_cfg), len(app_mod.s_meta)) == (0, 0, 0)


def test_row516_transform_control_only_imports_the_sites_loader():
    from bulk_downloader import app as app_mod

    assert callable(app_mod._load_sites_config)
