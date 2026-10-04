"""O1826 BRIEF-36: app data that ignored its configured home.

M007 app.py _oi_default_download_dir: the global-config fallback was guarded by
`"_load_global_config" in globals()`. No such name exists, so the guard was
always False and a global download_dir was never consulted.

M008 app.py live recorder wiring: `DATA_DIR if "DATA_DIR" in globals() else "."`
-- DATA_DIR is undefined too, so live_recordings/ always landed in the CWD,
whatever BD_HOME said.

M033 capture_schedules._adaptive_cfg_for read Path("sites_config.json") -- the
CWD -- while the app reads BD_SITES_CONFIG_PATH / BD_INSTALL_DIR first.

M038 community_scrapers._cache_dir and M153 selector_library._store_path wrote
under the CWD instead of BD_HOME, the root the rest of the app's stores use
(cross_site_selectors, daily_digest, interop_registry, template_canary).

Every case separates the configured root from the CWD (conftest makes them the
same tmp_path), so a CWD-relative resolver is told apart from the right one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def home_and_cwd(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("BD_HOME", str(home))
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    monkeypatch.delenv("BD_SITES_CONFIG_PATH", raising=False)
    monkeypatch.chdir(cwd)
    return home, cwd


# ── M007: global config download_dir ────────────────────────────────────────

def test_m007_global_download_dir_is_the_fallback(monkeypatch, tmp_path):
    from bulk_downloader import app, global_config
    want = str(tmp_path / "global-dl")
    monkeypatch.delenv("BD_DOWNLOAD_DIR", raising=False)
    monkeypatch.setattr(global_config, "get_config",
                        lambda: {"download_dir": want})
    got = app._oi_default_download_dir()
    assert got == want, (
        f"M007: global config download_dir {want!r} ignored; got {got!r}")


def test_m007_neg_env_still_wins_over_global(monkeypatch, tmp_path):
    from bulk_downloader import app, global_config
    monkeypatch.setenv("BD_DOWNLOAD_DIR", str(tmp_path / "env-dl"))
    monkeypatch.setattr(global_config, "get_config",
                        lambda: {"download_dir": str(tmp_path / "global-dl")})
    assert app._oi_default_download_dir() == str(tmp_path / "env-dl")


def test_m007_neg_no_global_value_keeps_home_downloads(monkeypatch):
    from bulk_downloader import app, global_config
    monkeypatch.delenv("BD_DOWNLOAD_DIR", raising=False)
    monkeypatch.setattr(global_config, "get_config", lambda: {})
    assert app._oi_default_download_dir() == os.path.expanduser("~/Downloads")


# ── M008: live_recordings state dir (bound at app import -> subprocess) ─────

_LIVE_PROBE = (
    "import json,sys;sys.path.insert(0,%r);"
    "import bulk_downloader.app;"
    "from bulk_downloader import live_recorder as L;"
    "d=L._state_dir;"
    "print('LIVE_STATE_DIR='+json.dumps(None if d is None else str(d.resolve())))"
)


def _live_state_dir(home: Path, cwd: Path) -> str | None:
    env = {k: v for k, v in os.environ.items()
           if k not in ("BD_INSTALL_DIR", "BD_SITES_CONFIG_PATH")}
    env.update(BD_HOME=str(home), BD_DISABLE_KEEPALIVE="1", LC_ALL="C",
               PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-c", _LIVE_PROBE % str(REPO)],
                       cwd=str(cwd), env=env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("LIVE_STATE_DIR=")]
    assert lines, "probe printed no LIVE_STATE_DIR line:\n" + r.stdout[-2000:]
    return json.loads(lines[-1].split("=", 1)[1])


def test_m008_live_recordings_land_under_bd_home(tmp_path):
    home, cwd = tmp_path / "home", tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    got = _live_state_dir(home, cwd)
    # Positive control: the probe can see the wiring at all.
    assert got is not None, "live recorder was not wired; probe cannot judge M008"
    assert got == str((home / "live_recordings").resolve()), (
        f"M008: live recorder state dir is {got!r}, not under BD_HOME {home}")
    assert not (cwd / "live_recordings").exists(), (
        "M008: live_recordings/ was created in the CWD")


# ── M033: capture_schedules reads the app's sites_config.json ───────────────

def _write_sites(path: Path, min_h: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sites": {"s1": {
        "adaptive_cadence": True, "cadence_min_h": min_h, "cadence_max_h": 48}}}),
        encoding="utf-8")


def test_m033_sites_config_path_env_is_read(home_and_cwd, monkeypatch, tmp_path):
    from bulk_downloader import capture_schedules as cs
    cfg = tmp_path / "elsewhere" / "sites.json"
    _write_sites(cfg, 3)
    monkeypatch.setenv("BD_SITES_CONFIG_PATH", str(cfg))
    got = cs._adaptive_cfg_for("s1")
    assert got == {"adaptive": True, "min_h": 3, "max_h": 48}, (
        f"M033: BD_SITES_CONFIG_PATH ignored; got {got}")


def test_m033_install_dir_is_read(home_and_cwd, monkeypatch, tmp_path):
    from bulk_downloader import capture_schedules as cs
    inst = tmp_path / "install"
    _write_sites(inst / "sites_config.json", 4)
    monkeypatch.setenv("BD_INSTALL_DIR", str(inst))
    got = cs._adaptive_cfg_for("s1")
    assert got == {"adaptive": True, "min_h": 4, "max_h": 48}, (
        f"M033: BD_INSTALL_DIR ignored; got {got}")


def test_m033_explicit_path_beats_install_dir(home_and_cwd, monkeypatch, tmp_path):
    from bulk_downloader import capture_schedules as cs
    cfg = tmp_path / "elsewhere" / "sites.json"
    _write_sites(cfg, 5)
    _write_sites(tmp_path / "install" / "sites_config.json", 6)
    monkeypatch.setenv("BD_SITES_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path / "install"))
    assert cs._adaptive_cfg_for("s1")["min_h"] == 5


def test_m033_neg_unset_env_still_reads_cwd(home_and_cwd):
    from bulk_downloader import capture_schedules as cs
    _home, cwd = home_and_cwd
    _write_sites(cwd / "sites_config.json", 7)
    assert cs._adaptive_cfg_for("s1") == {"adaptive": True, "min_h": 7, "max_h": 48}


# ── M038: community scrapers cache ──────────────────────────────────────────

def test_m038_scraper_cache_lands_under_bd_home(home_and_cwd):
    from bulk_downloader import community_scrapers as cs
    home, cwd = home_and_cwd
    got = cs._cache_dir()
    assert got.resolve() == (home / "community_scrapers_cache").resolve(), (
        f"M038: scraper cache dir is {got}, not under BD_HOME {home}")
    assert not (cwd / "community_scrapers_cache").exists(), (
        "M038: community_scrapers_cache/ was created in the CWD")


def test_m038_neg_unset_home_keeps_cwd(home_and_cwd, monkeypatch):
    from bulk_downloader import community_scrapers as cs
    _home, cwd = home_and_cwd
    monkeypatch.delenv("BD_HOME")
    assert cs._cache_dir().resolve() == (cwd / "community_scrapers_cache").resolve()


# ── M153: selector library store ────────────────────────────────────────────

def test_m153_selector_library_lands_under_bd_home(home_and_cwd):
    from bulk_downloader import selector_library as sl
    home, cwd = home_and_cwd
    ok, msg = sl.add_named("dl", "button.download")
    assert ok, msg
    assert (home / sl.LIBRARY_FILE).is_file(), (
        f"M153: {sl.LIBRARY_FILE} not written under BD_HOME {home}")
    assert not (cwd / sl.LIBRARY_FILE).exists(), (
        f"M153: {sl.LIBRARY_FILE} was written in the CWD")
    assert sl.get_named("dl")["selector"] == "button.download"


def test_m153_neg_explicit_base_dir_wins(home_and_cwd, tmp_path):
    from bulk_downloader import selector_library as sl
    home, _cwd = home_and_cwd
    base = tmp_path / "explicit"
    base.mkdir()
    ok, msg = sl.add_named("dl", "a.x", base_dir=base)
    assert ok, msg
    assert (base / sl.LIBRARY_FILE).is_file()
    assert not (home / sl.LIBRARY_FILE).exists()
