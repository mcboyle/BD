"""deep-backup-restore residual (bughunt deep-backup-restore FINDINGS F2 F3 F5-F9
F11-F14; F1 F10 belong to drb-fix-r2, F4 is app.py and is reported, not fixed).

One RED test per finding, each with its control:
  F2  sensitive-scope members are ciphertext; a lost or emptied manifest must
      not let that ciphertext be written over the live secrets.
  F3  an unreadable member must not raise out of restore_backup, and must not
      leave the members before it written (target unchanged).
  F5  verify_tarball must read member data, not only the directory.
  F6  a member that cannot be written makes the restore NOT ok, with no
      *.bdtmp litter left behind.
  F7  an interrupted restore leaves a marker that says so.
  F8  a manifest that is not a UTF-8 JSON object is a warning, never a raise.
  F9  a truncated / bit-flipped .bdbk is not reported as only "incorrect
      passphrase".
  F11 create_backup's temp file is unique per call.
  F12 create_backup of a base_dir that does not exist is not ok.
  F13 restore_capture_corpus_backup of an undecodable member returns ok=False.
  F14 verify_db_dump leaves no -wal/-shm beside the file and warns when a
      WAL-mode copy has no -wal.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import shutil
import sqlite3
import struct
import threading
import zipfile
from pathlib import Path

import pytest

from bulk_downloader import backup as bk
from bulk_downloader import backup_verify as bv
from bulk_downloader import dom_analyzer as da

BD_GATE_SCOPE = "module"

_LEARNED = "learned/x.json"


def _base(root: Path) -> Path:
    base = root / "base"
    (base / "cookies").mkdir(parents=True)
    (base / "learned").mkdir()
    (base / "sites_config.json").write_text('{"sites": ["new"]}')
    (base / "app_config.json").write_text('{"max": 3}')
    (base / "secrets.json").write_text('{"vault": "PLAINTEXT-SECRET"}')
    (base / "cookies" / "a.json").write_text('{"c": "PLAINTEXT-COOKIE"}')
    # incompressible, so its deflate stream is long enough to damage
    (base / _LEARNED).write_bytes(os.urandom(5000))
    return base


def _old_target(root: Path, name: str) -> Path:
    t = root / name
    t.mkdir()
    (t / "sites_config.json").write_text("OLD")
    return t


def _backup(root: Path, **kw) -> bytes:
    out = root / ("b.bdbk" if kw.get("passphrase") and kw.get("encrypt_scope", "all") == "all"
                  else "b.zip")
    r = bk.create_backup(out, base_dir=_base(root), include_db=False, **kw)
    assert r["ok"], r
    return out.read_bytes()


def _rebuild(zbytes: bytes, *, drop=(), replace=None) -> bytes:
    replace = replace or {}
    src = zipfile.ZipFile(io.BytesIO(zbytes))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            if name in drop:
                continue
            dst.writestr(name, replace.get(name, src.read(name)))
    return buf.getvalue()


def _damage_deflate(zbytes: bytes, name: str) -> bytes:
    """Overwrite the first 64 bytes of one member's deflate stream."""
    info = zipfile.ZipFile(io.BytesIO(zbytes)).getinfo(name)
    assert info.compress_size > 64, info
    off = info.header_offset
    nl, el = struct.unpack("<HH", zbytes[off + 26:off + 30])
    start = off + 30 + nl + el
    b = bytearray(zbytes)
    for i in range(start, start + 64):
        b[i] = 0xFF
    return bytes(b)


def _write(root: Path, name: str, data: bytes) -> Path:
    p = root / name
    p.write_bytes(data)
    return p


def _restore(path, target, **kw):
    try:
        return bk.restore_backup(path, target, **kw)
    except Exception as e:  # noqa: BLE001 -- a raise IS the defect for F3/F8
        pytest.fail("drr: restore_backup raised %s: %s" % (type(e).__name__, e))


@pytest.fixture
def no_record(monkeypatch):
    monkeypatch.setattr(bv, "_record", lambda *a, **k: None)


# -- F2 ---------------------------------------------------------------------

@pytest.mark.parametrize("damage", ["manifest-missing", "members-emptied",
                                    "encryption-not-object"])
def test_f2_sensitive_ciphertext_never_written(tmp_path, damage):
    z = _backup(tmp_path, passphrase="pw", encrypt_scope="sensitive")
    if damage == "manifest-missing":
        bad, pw = _rebuild(z, drop={"_manifest.json"}), "pw"
    elif damage == "encryption-not-object":
        man = json.loads(zipfile.ZipFile(io.BytesIO(z)).read("_manifest.json"))
        man["encryption"] = "sensitive"
        bad, pw = _rebuild(z, replace={"_manifest.json": json.dumps(man)}), "pw"
    else:
        man = json.loads(zipfile.ZipFile(io.BytesIO(z)).read("_manifest.json"))
        man["encryption"]["members"] = []
        bad, pw = _rebuild(z, replace={"_manifest.json": json.dumps(man)}), "WRONG"
    t = _old_target(tmp_path, "t")
    r = _restore(_write(tmp_path, "bad.zip", bad), t, passphrase=pw)
    sec = t / "secrets.json"
    if sec.exists() and b"PLAINTEXT-SECRET" not in sec.read_bytes():
        pytest.fail("drr F2: ciphertext written over secrets.json (%s, ok=%s)"
                    % (damage, r.get("ok")))
    assert r["ok"] is False, r
    assert (t / "sites_config.json").read_text() == "OLD"


def test_f2_controls_intact_sensitive_and_legacy_plain_without_manifest(tmp_path):
    z = _backup(tmp_path, passphrase="pw", encrypt_scope="sensitive")
    t = _old_target(tmp_path, "t")
    r = _restore(_write(tmp_path, "ok.zip", z), t, passphrase="pw")
    assert r["ok"] and b"PLAINTEXT-SECRET" in (t / "secrets.json").read_bytes(), r
    plain = bk.create_backup(tmp_path / "p.zip", base_dir=tmp_path / "base",
                             include_db=False)
    assert plain["ok"], plain
    legacy = _rebuild((tmp_path / "p.zip").read_bytes(), drop={"_manifest.json"})
    t2 = _old_target(tmp_path, "t2")
    r2 = _restore(_write(tmp_path, "legacy.zip", legacy), t2)
    assert r2["ok"], r2
    assert b"PLAINTEXT-SECRET" in (t2 / "secrets.json").read_bytes()


# -- F3 ---------------------------------------------------------------------

def test_f3_unreadable_member_is_not_ok_and_writes_nothing(tmp_path):
    z = _damage_deflate(_backup(tmp_path), _LEARNED)
    t = _old_target(tmp_path, "t")
    r = _restore(_write(tmp_path, "bad.zip", z), t)
    assert r["ok"] is False, r
    assert _LEARNED in r.get("error", ""), r
    if (t / "sites_config.json").read_text() != "OLD":
        pytest.fail("drr F3: members before the unreadable one were written")
    assert not list(t.rglob("*.bdtmp"))


# -- F5 ---------------------------------------------------------------------

def test_f5_verify_tarball_reads_member_data(tmp_path, no_record):
    z = _backup(tmp_path)
    good = _write(tmp_path, "good.zip", z)
    assert bv.verify_tarball(str(good))["ok"] is True
    bad = _write(tmp_path, "bad.zip", _damage_deflate(z, _LEARNED))
    r = bv.verify_tarball(str(bad))
    if r["ok"]:
        pytest.fail("drr F5: verify_tarball ok for an undecodable member")
    assert r["state"] == "failed" and _LEARNED in r["error"], r


# -- F6 ---------------------------------------------------------------------

def test_f6_member_that_cannot_be_written_is_not_ok(tmp_path, monkeypatch):
    zp = _write(tmp_path, "b.zip", _backup(tmp_path))
    t = _old_target(tmp_path, "t")
    real = pathlib.Path.write_bytes

    def disk_full(self, data):
        if self.name.startswith("x.json"):
            raise OSError(28, "No space left on device (injected)")
        return real(self, data)

    monkeypatch.setattr(pathlib.Path, "write_bytes", disk_full)
    r = _restore(zp, t)
    monkeypatch.undo()
    if r["ok"]:
        pytest.fail("drr F6: restore ok=True with a member that failed to write")
    assert not list(t.rglob("*.bdtmp")), sorted(map(str, t.rglob("*")))
    assert (t / "sites_config.json").read_text() == "OLD"


# -- F7 ---------------------------------------------------------------------

class _Killed(BaseException):
    pass


def test_f7_interrupted_restore_leaves_incomplete_marker(tmp_path, monkeypatch):
    zp = _write(tmp_path, "b.zip", _backup(tmp_path))
    t = _old_target(tmp_path, "t")
    real = pathlib.Path.replace
    n = {"c": 0}

    def kill_on_second(self, target):
        if self.name.endswith(".bdtmp"):
            n["c"] += 1
            if n["c"] == 2:
                raise _Killed()
        return real(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", kill_on_second)
    with pytest.raises(_Killed):
        bk.restore_backup(zp, t)
    monkeypatch.undo()
    if not list(t.glob(".bd-restore-incomplete*")):
        pytest.fail("drr F7: interrupted restore left no incomplete marker")
    r = _restore(zp, t)
    assert r["ok"], r
    assert any("did not complete" in w for w in r["warnings"]), r
    assert not list(t.glob(".bd-restore-incomplete*"))


def test_f7_duplicate_member_name_restores_last_and_clears_marker(tmp_path):
    # A zip may carry one name twice: the later member wins, as it did before
    # staging, and the restore is ok with no marker left behind.
    buf = io.BytesIO()
    with pytest.warns(UserWarning), zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("sites_config.json", '{"v": 1}')
        zf.writestr("sites_config.json", '{"v": 2}')
    t = _old_target(tmp_path, "t")
    r = _restore(_write(tmp_path, "dup.zip", buf.getvalue()), t)
    if not r["ok"]:
        pytest.fail("drr F7: duplicate member name made the restore not ok: "
                    + str(r.get("error")))
    assert (t / "sites_config.json").read_text() == '{"v": 2}'
    assert not list(t.glob(".bd-restore-incomplete*"))
    assert not list(t.rglob("*.bdtmp"))


# -- F8 ---------------------------------------------------------------------

@pytest.mark.parametrize("manifest", [b'"x"', b"123", b"\xff\xfe\x00", b"[]", b"null"])
def test_f8_non_object_manifest_is_a_warning(tmp_path, manifest):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("sites_config.json", '{"sites": ["new"]}')
        zf.writestr("_manifest.json", manifest)
    t = _old_target(tmp_path, "t")
    try:
        r = bk.restore_backup(_write(tmp_path, "m.zip", buf.getvalue()), t)
    except Exception as e:  # noqa: BLE001
        pytest.fail("drr F8: manifest %r raised %s" % (manifest, type(e).__name__))
    if not any("_manifest.json" in w for w in r["warnings"]):
        pytest.fail("drr F8: manifest %r accepted with no warning" % manifest)
    assert r["ok"] and (t / "sites_config.json").read_text() == '{"sites": ["new"]}'


# -- F9 ---------------------------------------------------------------------

def test_f9_damaged_bdbk_is_not_only_a_passphrase_error(tmp_path):
    eb = _backup(tmp_path, passphrase="pw")
    assert eb.startswith(b"BDBK")
    flipped = bytearray(eb)
    flipped[len(eb) // 2] = ord("A") if flipped[len(eb) // 2] != ord("A") else ord("B")
    for label, data in (("truncated-10", eb[:10]), ("truncated-21", eb[:21]),
                        ("bit-flipped", bytes(flipped))):
        r = _restore(_write(tmp_path, label + ".bdbk", data), tmp_path / ("t-" + label),
                     passphrase="pw")
        assert r["ok"] is False, (label, r)
        # a short file is never a passphrase question; a flipped byte cannot be
        # told from a wrong passphrase by the HMAC, so it must name both.
        wrong_hint = "damaged" not in r["error"] or (
            label.startswith("truncated") and "passphrase" in r["error"])
        if wrong_hint:
            pytest.fail("drr F9: %s .bdbk reported as %r" % (label, r["error"]))
    wrong = _restore(_write(tmp_path, "ok.bdbk", eb), tmp_path / "t-wrong", passphrase="WRONG")
    assert wrong["ok"] is False and "passphrase" in wrong["error"], wrong
    right = _restore(tmp_path / "ok.bdbk", tmp_path / "t-right", passphrase="pw")
    assert right["ok"], right


# -- F11 --------------------------------------------------------------------

def test_f11_temp_name_is_unique_per_create(tmp_path):
    base = _base(tmp_path)
    out = tmp_path / "out" / "b.zip"
    (tmp_path / "out" / "b.zip.tmp").mkdir(parents=True)  # squats the fixed name
    r = bk.create_backup(out, base_dir=base, include_db=False)
    if not r["ok"]:
        pytest.fail("drr F11: create_backup used the fixed temp name: %s" % r.get("error"))
    assert zipfile.ZipFile(out).testzip() is None
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        bk.create_backup(out, base_dir=base, include_db=False))) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert [x["ok"] for x in results] == [True] * 6, [x.get("error") for x in results]
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["b.zip", "b.zip.tmp"]


# -- F12 --------------------------------------------------------------------

def test_f12_missing_base_dir_is_not_ok(tmp_path):
    out = tmp_path / "b.zip"
    r = bk.create_backup(out, base_dir=tmp_path / "no-such-dir", include_db=False)
    if r["ok"]:
        pytest.fail("drr F12: create_backup ok=True for a missing base_dir (files=%s)"
                    % r.get("files"))
    assert "base_dir" in r["error"] and not out.exists(), r


# -- F13 --------------------------------------------------------------------

@pytest.mark.parametrize("which", ["capture-member", "manifest"])
def test_f13_corpus_restore_of_undecodable_member_is_not_ok(tmp_path, which):
    src = tmp_path / "src"
    for d in da._CAPTURE_OUTPUT_DIRS:
        (src / d).mkdir(parents=True)
        (src / d / "capture_0.json").write_bytes(os.urandom(5000))
    cz = tmp_path / "corpus.zip"
    made = bk.create_capture_corpus_backup(cz, source_root=src)
    assert made["ok"], made
    zb = cz.read_bytes()
    if which == "manifest":
        member = bk._CORPUS_MANIFEST
        # pad the manifest so its deflate stream is long enough to damage
        man = json.loads(zipfile.ZipFile(io.BytesIO(zb)).read(member))
        man["pad"] = os.urandom(200).hex()
        zb = _rebuild(zb, replace={member: json.dumps(man)})
    else:
        member = sorted(n for n in zipfile.ZipFile(io.BytesIO(zb)).namelist()
                        if n != bk._CORPUS_MANIFEST)[0]
    bad = _write(tmp_path, "bad.zip", _damage_deflate(zb, member))
    for dry in (True, False):
        t = tmp_path / ("t-dry" if dry else "t")
        t.mkdir()
        try:
            r = bk.restore_capture_corpus_backup(bad, target_root=t, dry_run=dry)
        except Exception as e:  # noqa: BLE001
            pytest.fail("drr F13: corpus restore (dry_run=%s) raised %s" % (dry, type(e).__name__))
        assert r["ok"] is False and sorted(os.listdir(t)) == [], r
    ctl = tmp_path / "t-ctl"
    ctl.mkdir()
    assert bk.restore_capture_corpus_backup(cz, target_root=ctl)["ok"]


# -- F14 --------------------------------------------------------------------

def _wal_db(d: Path):
    d.mkdir()
    p = d / "h.db"
    cx = sqlite3.connect(p)
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA wal_autocheckpoint=0")
    cx.execute("CREATE TABLE t(i INTEGER)")
    cx.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(100)])
    cx.commit()
    cx.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    cx.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(900)])
    cx.commit()
    return cx, p


def test_f14_verify_db_dump_leaves_no_sidecars_and_warns_on_missing_wal(tmp_path, no_record):
    cx, src = _wal_db(tmp_path / "live")
    try:
        short = tmp_path / "short"
        short.mkdir()
        shutil.copy(src, short / "h.db")  # copied WITHOUT its -wal
        full = tmp_path / "full"
        full.mkdir()
        shutil.copy(src, full / "h.db")
        shutil.copy(str(src) + "-wal", full / "h.db-wal")
    finally:
        cx.close()
    r = bv.verify_db_dump(str(short / "h.db"))
    left = sorted(p.name for p in short.iterdir())
    if left != ["h.db"]:
        pytest.fail("drr F14: verify_db_dump created sidecar files %s" % left)
    if not any("WAL" in w for w in r.get("warnings", [])):
        pytest.fail("drr F14: WAL-mode copy without -wal verified with no warning")
    ctl = bv.verify_db_dump(str(full / "h.db"))
    assert ctl["ok"] and ctl["total_rows"] == 1000, ctl
    assert sorted(p.name for p in full.iterdir()) == ["h.db", "h.db-wal"]
