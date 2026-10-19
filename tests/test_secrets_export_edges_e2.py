"""secrets-export-edges-e2 (O1789 deep-secrets-export-rest OPEN EDGE 2; E2-only re-cut).

eol_export.full_export wrote every file with write_bytes / write_text /
tarfile.open, so the mode followed the umask (0664 under umask 002); the export
holds history, provenance, account health and optionally the cookie jar. E1
(scheme-less proxy masking in egress_withhold) rides proxy-cred-schemeless.

The values below are fixed test strings, not credentials.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import stat

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

SITE_URL = "https://see.example/"


def _cfg(proxy):
    return {"name": "see", "base_url": SITE_URL, "proxy": proxy}


# ── EDGE 2: EOL export file modes ───────────────────────────────────


@pytest.fixture
def eol_env(monkeypatch, tmp_path):
    from bulk_downloader import constants as C
    from bulk_downloader import db as DB

    dbfile = tmp_path / "see.db"
    cx = sqlite3.connect(dbfile)
    cx.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, site_id, site_name, url,"
               " status, filename, file_size, ts, message)")
    cx.execute("INSERT INTO history (site_id, site_name, url, status, filename, file_size,"
               " ts, message) VALUES ('see','see','https://see.example/a','ok','a',1,"
               " datetime('now'),'m')")
    cx.commit()
    cx.close()

    @contextlib.contextmanager
    def _conn():
        c = sqlite3.connect(dbfile)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    install = tmp_path / "install"
    (install / "cookies").mkdir(parents=True)
    (install / "cookies" / "see.txt").write_text("cookie-see\n")
    monkeypatch.setattr(DB, "db_conn", _conn)
    monkeypatch.setattr(C, "INSTALL_DIR", install)
    old = os.umask(0o002)  # the RED is vacuous under a 077 umask
    try:
        yield tmp_path / "eol-out"
    finally:
        os.umask(old)


EXPECTED_FILES = {"README.txt", "history.csv", "provenance.csv", "bd-state.json",
                  "cookies.tar", "templates/see.bdtpl"}


def _modes(dest):
    out = {}
    for p in dest.rglob("*"):
        if p.is_file():
            rel = p.relative_to(dest).as_posix()
            if rel.startswith("templates/"):
                rel = "templates/see.bdtpl"
            out[rel] = stat.S_IMODE(p.stat().st_mode)
    return out


def test_edge2_every_eol_export_file_is_0600(eol_env):
    from bulk_downloader import eol_export as EE

    r = EE.full_export(str(eol_env), s_cfg={"see": _cfg("")}, include_cookies=True)
    modes = _modes(eol_env)
    # denominator from disk: all six writers ran (history row exported, tar made)
    assert set(modes) == EXPECTED_FILES, (sorted(modes), r.get("warnings"))
    assert r.get("history_rows") == 1, r
    wide = {k: oct(m) for k, m in modes.items() if m != 0o600}
    assert not wide, f"EOL export files not 0600: {wide}"


def test_edge2_reexport_over_existing_files_tightens_them(eol_env):
    from bulk_downloader import eol_export as EE

    eol_env.mkdir()
    for name in ("README.txt", "history.csv", "bd-state.json", "cookies.tar"):
        (eol_env / name).write_text("old")
        os.chmod(eol_env / name, 0o644)
    EE.full_export(str(eol_env), s_cfg={"see": _cfg("")}, include_cookies=True)
    modes = _modes(eol_env)
    assert set(modes) == EXPECTED_FILES
    wide = {k: oct(m) for k, m in modes.items() if m != 0o600}
    assert not wide, f"re-export kept a wider mode: {wide}"
    assert (eol_env / "README.txt").read_text().startswith("BulkDownloader EOL Export")
