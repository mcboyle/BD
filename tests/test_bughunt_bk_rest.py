"""BK-REST (bughunt backup-replication, FINDINGS F8-F14; finder bd-worker-B2-B).

Each test is the FINDINGS repro (probe_mod P6/P8-P12, probe_app A4/A5) turned into
an assertion, with the control FINDINGS names beside it:

  F8  db_replication.restore_store: ok=True when the restored file is absent
      (verified=False only as a side field). Control: a restore that writes a
      healthy SQLite db is ok + verified.
  F9  backup_verify.smoke_restore: sandboxes left behind (rehearsal + API), and
      extraction with no per-member cap. Control: restore_backup's 256 MB cap;
      a member under the cap still extracts.
  F10 backup.restore_backup: ok=True for an EMPTY zip. Control: verify_tarball
      already refuses the same zip; a one-member zip still restores.
  F11 backup._iter_files follows symlinks out of the data dir. Control: the real
      file beside the link is archived.
  F12 /api/backup/{verify,smoke_restore,drift} take any server path (table
      names outside BD_HOME, existence oracle). Control: the same db inside
      BD_HOME still verifies.
  F13 /api/backup/history?limit=abc -> 500, limit=-1 unbounded; verify
      {"path": 123} -> 500. Control: limit=5 still answers 200.
  F14 create_backup hashed each member with a SECOND read after zf.write; a file
      changing between the reads gets a manifest checksum its own archive fails.

Hermetic: tmp_path data dirs, Flask test app with only the backup blueprint,
backup_verify._record stubbed (no live DB), litestream is a PATH stub.
"""

import builtins
import glob
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import pytest
from flask import Flask

from bulk_downloader import app_backup
from bulk_downloader import backup as bk
from bulk_downloader import backup_verify as bv
from bulk_downloader import db_replication as R

BD_GATE_SCOPE = "module"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setenv("BD_HOME", str(home))
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    monkeypatch.setattr(bv, "_record", lambda *a, **k: None)
    monkeypatch.setattr(app_backup, "_check_csrf", lambda *a, **k: None)
    return home, tmp


def _client():
    app = Flask(__name__)
    app.register_blueprint(app_backup.backup_bp)
    return app.test_client()


def _zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _sqlite(path, table="secret_table"):
    cx = sqlite3.connect(path)
    cx.execute(f"CREATE TABLE {table}(x)")
    cx.execute(f"INSERT INTO {table} VALUES(1)")
    cx.commit()
    cx.close()
    return path


def _sandboxes(tmp):
    return glob.glob(str(tmp / "bd-restore-test-*"))


# ── F8 ──────────────────────────────────────────────────────────────────

def _stub_litestream(tmp_path, monkeypatch, body):
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "litestream").write_text("#!/bin/sh\n" + body + "exit 0\n")
    os.chmod(stub / "litestream", 0o755)
    monkeypatch.setenv("PATH", str(stub) + os.pathsep + os.environ.get("PATH", ""))


def test_f8_restore_store_that_restored_nothing_is_not_ok(env, tmp_path, monkeypatch):
    _stub_litestream(tmp_path, monkeypatch, "")  # exits 0, writes nothing (P6)
    base = tmp_path / "p6base"
    base.mkdir()
    dest = base / "restored" / "queue.db"
    r = R.restore_store("queue.db", str(dest), base_dir=base)
    assert not dest.exists()
    assert r.get("ok") is False, f"BK-REST F8: nothing restored but ok=True: {r}"
    assert r.get("verified") is False, r
    assert "verification" in r.get("reason", ""), r


def test_f8_control_restore_that_writes_a_healthy_db_is_ok(env, tmp_path, monkeypatch):
    src = _sqlite(tmp_path / "replica-src.db")
    _stub_litestream(tmp_path, monkeypatch, f'cp "{src}" "$3"\n')  # argv: restore -o <dest> ...
    base = tmp_path / "p6base"
    base.mkdir()
    dest = base / "restored" / "queue.db"
    r = R.restore_store("queue.db", str(dest), base_dir=base)
    assert r.get("ok") is True and r.get("verified") is True, r
    assert dest.exists()


# ── F9 ──────────────────────────────────────────────────────────────────

def test_f9_rehearsal_leaves_no_sandbox(env, tmp_path):
    _home, tmp = env
    good = _zip(tmp_path / "good.zip", {"sites_config.json": "{}"})
    before = len(_sandboxes(tmp))
    for _ in range(5):
        assert bv.rehearse(backup_path=str(good))["ok"] is True
    left = _sandboxes(tmp)
    assert len(left) == before, f"BK-REST F9: rehearsal leaked {len(left) - before} sandboxes: {left}"


def test_f9_api_smoke_restore_leaves_no_sandbox(env):
    home, tmp = env
    good = _zip(home / "good.zip", {"sites_config.json": "{}"})
    resp = _client().post("/api/backup/smoke_restore", json={"path": str(good)})
    body = resp.get_json()
    assert resp.status_code == 200 and body["ok"] is True, body
    assert _sandboxes(tmp) == [], f"BK-REST F9: API smoke_restore leaked {_sandboxes(tmp)}"


def test_f9_failed_smoke_restore_removes_its_sandbox(env, tmp_path):
    _home, tmp = env
    for path in (tmp_path / "x.unknown", _zip(tmp_path / "empty.zip", {})):
        Path(path).touch()
        r = bv.smoke_restore(str(path))
        assert r["ok"] is False and "restored_to" not in r, r
    assert _sandboxes(tmp) == [], f"BK-REST F9: failed smoke_restore leaked {_sandboxes(tmp)}"


def test_f9_smoke_restore_cap_is_the_restore_cap():
    assert getattr(bv, "_SMOKE_MEMBER_CAP", None) == bk._RESTORE_MEMBER_CAP, (
        "BK-REST F9: smoke_restore has no per-member cap equal to restore_backup's")


def test_f9_smoke_restore_caps_member_size_like_restore_backup(env, tmp_path, monkeypatch):
    _home, tmp = env
    monkeypatch.setattr(bv, "_SMOKE_MEMBER_CAP", 1024, raising=False)
    bomb = _zip(tmp_path / "bomb.zip", {"big.bin": b"\0" * 4096})
    r = bv.smoke_restore(str(bomb))
    assert r["ok"] is False and r.get("state") == "failed", f"BK-REST F9: no size cap: {r}"
    assert "restore cap" in r.get("error", ""), r
    assert not glob.glob(str(tmp / "bd-restore-test-*" / "big.bin")), "oversized member extracted"


def test_f9_control_member_under_cap_still_extracts(env, tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "_SMOKE_MEMBER_CAP", 1024, raising=False)
    small = _zip(tmp_path / "small.zip", {"sites_config.json": b"x" * 100})
    r = bv.smoke_restore(str(small))
    try:
        assert r["ok"] is True, r
        assert os.path.getsize(os.path.join(r["restored_to"], "sites_config.json")) == 100
    finally:
        import shutil
        shutil.rmtree(r.get("restored_to") or "/nonexistent", ignore_errors=True)


# ── F10 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dry_run", [False, True])
def test_f10_empty_zip_restore_is_not_ok(tmp_path, dry_run):
    empty = _zip(tmp_path / "empty.zip", {})
    target = tmp_path / "p10"
    r = bk.restore_backup(empty, target_dir=target, dry_run=dry_run)
    assert r["ok"] is False, f"BK-REST F10: empty zip restored ok: {r}"
    assert "empty" in r.get("error", ""), r
    assert r["files_restored"] == 0
    assert not target.exists()


def test_f10_manifest_only_zip_is_not_ok(tmp_path):
    z = _zip(tmp_path / "m.zip", {"_manifest.json": "{}", "cookies/": ""})
    assert bk.restore_backup(z, target_dir=tmp_path / "t")["ok"] is False


def test_f10_control_sibling_refuses_and_one_member_restores(env, tmp_path):
    empty = _zip(tmp_path / "empty.zip", {})
    assert bv.verify_tarball(str(empty))["ok"] is False
    one = _zip(tmp_path / "one.zip", {"sites_config.json": "{}"})
    r = bk.restore_backup(one, target_dir=tmp_path / "t")
    assert r["ok"] is True and r["files_restored"] == 1, r


# ── F11 ─────────────────────────────────────────────────────────────────

def test_f11_create_backup_does_not_follow_symlinks(tmp_path):
    src = tmp_path / "p12src"
    (src / "cookies").mkdir(parents=True)
    (src / "cookies" / "real.json").write_text("REAL")
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("OUTSIDE-SECRET")
    os.symlink(secret, src / "cookies" / "x.json")
    outdir = tmp_path / "outside-dir"
    outdir.mkdir()
    (outdir / "y.json").write_text("OUTSIDE-SECRET")
    os.symlink(outdir, src / "cookies" / "linkdir")
    z = tmp_path / "p12.zip"
    assert bk.create_backup(z, base_dir=src, include_db=False)["ok"]
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        leaked = [n for n in names if b"OUTSIDE-SECRET" in zf.read(n)]
        assert leaked == [], f"BK-REST F11: symlink target archived: {leaked}"
        assert zf.read("cookies/real.json") == b"REAL"  # control
    assert "cookies/x.json" not in names and "cookies/linkdir/y.json" not in names


# ── F12 / F13 ───────────────────────────────────────────────────────────

def test_f12_verify_refuses_path_outside_bd_home(env, tmp_path):
    other = _sqlite(tmp_path / "elsewhere.db")
    resp = _client().post("/api/backup/verify", json={"path": str(other)})
    body = resp.get_json()
    assert resp.status_code == 400, f"BK-REST F12: outside path verified: {body}"
    assert "secret_table" not in json.dumps(body)


def test_f12_no_existence_oracle_outside_bd_home(env, tmp_path):
    other = _sqlite(tmp_path / "elsewhere.db")
    c = _client()
    for route in ("/api/backup/verify", "/api/backup/smoke_restore"):
        a = c.post(route, json={"path": str(other)})
        b = c.post(route, json={"path": str(tmp_path / "absent.db")})
        assert (a.status_code, a.get_json()) == (b.status_code, b.get_json()), (
            f"BK-REST F12: {route} tells existing from absent: {a.get_json()} vs {b.get_json()}")
        assert a.status_code == 400


def test_f12_drift_refuses_path_outside_bd_home(env, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(bv, "verify_against_live", lambda p: calls.append(p) or {"ok": True})
    other = _sqlite(tmp_path / "elsewhere.db")
    resp = _client().post("/api/backup/drift", json={"path": str(other)})
    assert resp.status_code == 400 and calls == [], f"BK-REST F12: drift read {calls}"


def test_f12_control_db_inside_bd_home_still_verifies(env):
    home, _tmp = env
    inside = _sqlite(home / "backup-copy.db")
    resp = _client().post("/api/backup/verify", json={"path": str(inside)})
    body = resp.get_json()
    assert resp.status_code == 200 and body["ok"] is True, body
    assert body["tables"] == ["secret_table"]


@pytest.mark.parametrize("route", ["/api/backup/verify", "/api/backup/smoke_restore",
                                   "/api/backup/drift"])
def test_f13_non_string_path_is_400(env, route):
    resp = _client().post(route, json={"path": 123})
    assert resp.status_code == 400, f"BK-REST F13: {route} path=123 -> {resp.status_code} {resp.get_json()}"


@pytest.mark.parametrize("limit", ["abc", "-1", "0"])
def test_f13_history_bad_limit_is_400(env, monkeypatch, limit):
    calls = []
    monkeypatch.setattr(bv, "recent_verifications", lambda limit=20: calls.append(limit) or [])
    resp = _client().get("/api/backup/history?limit=" + limit)
    assert resp.status_code == 400 and calls == [], (
        f"BK-REST F13: limit={limit} -> {resp.status_code}, recent_verifications{calls}")


def test_f13_control_history_valid_limit(env, monkeypatch):
    calls = []
    monkeypatch.setattr(bv, "recent_verifications", lambda limit=20: calls.append(limit) or [])
    resp = _client().get("/api/backup/history?limit=5")
    assert resp.status_code == 200 and calls == [5], (resp.status_code, calls)


# ── F14 ─────────────────────────────────────────────────────────────────

def _manifest_matches_members(z):
    with zipfile.ZipFile(z) as zf:
        sums = json.loads(zf.read("_manifest.json"))["checksums"]
        return {n: hashlib.sha256(zf.read(n)).hexdigest() == h for n, h in sums.items()}


def test_f14_checksum_is_of_the_archived_bytes(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    target = src / "sites_config.json"
    target.write_text('{"v": "ORIGINAL"}')
    real_open = builtins.open
    opens = []

    def racing_open(file, mode="r", *a, **k):
        # a writer replaces the file after the FIRST read of it
        if "r" in mode and "b" in mode and os.path.realpath(str(file)) == str(target.resolve()):
            opens.append(file)
            if len(opens) > 1:
                return io.BytesIO(b'{"v": "CHANGED-BETWEEN-READS"}')
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", racing_open)
    z = tmp_path / "f14.zip"
    assert bk.create_backup(z, base_dir=src, include_db=False)["ok"]
    monkeypatch.setattr(builtins, "open", real_open)
    match = _manifest_matches_members(z)
    assert match.get("sites_config.json") is True, (
        f"BK-REST F14: manifest checksum is not of the archived bytes ({len(opens)} reads): {match}")
    r = bk.restore_backup(z, target_dir=tmp_path / "t")
    assert r["ok"] is True and r["integrity_ok"] is True, r


def test_f14_control_unraced_backup_round_trips(tmp_path):
    src = tmp_path / "src"
    (src / "cookies").mkdir(parents=True)
    (src / "sites_config.json").write_text("{}")
    (src / "cookies" / "a.json").write_text("[]")
    z = tmp_path / "ok.zip"
    assert bk.create_backup(z, base_dir=src, include_db=False)["ok"]
    assert _manifest_matches_members(z) == {"sites_config.json": True, "cookies/a.json": True}
