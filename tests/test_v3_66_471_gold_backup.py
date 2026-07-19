"""A0 KEYSTONE -- generational gold-backup-with-restore (v3.66.471).

The existing template_keystone.snapshot_gold keeps exactly ONE gold per host
("first snapshot wins"), so a sequence of writes loses every known-good state
but the first. A0 generalizes profile_sync's timestamped move-aside into a
*generational* template backup under ``templates/.gold_backups/<host>/<ts>/``
with a manifest (sha256 / version / reason) and a guaranteed one-call restore.

Contract proven here (RED-first -- template_backup does not exist yet):
  1. backup -> mutate live -> restore yields a BYTE-IDENTICAL original.
  2. the manifest records sha256 + reason + version for the backed-up bytes.
  3. multiple writes retain multiple generations (history, not last-only).
  4. restore can target a specific timestamp (not just latest).
  5. a backup FAILURE is reported ok=False so the caller can ABORT the write
     (the keystone's safety guarantee: never auto-write without a restore point).
  6. safe_overwrite ABORTS (live untouched) when the generational backup fails.

Zero-arg test functions; no pytest builtins; temp dirs via tempfile.
"""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from bulk_downloader import template_backup as tb
from bulk_downloader import template_keystone as tk


_SUFFIX = ".template.json"


def _seed(reviewed: Path, host: str, payload: dict) -> Path:
    reviewed.mkdir(parents=True, exist_ok=True)
    fp = reviewed / f"{host}{_SUFFIX}"
    fp.write_text(json.dumps(payload, indent=2), "utf-8")
    return fp


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_backup_mutate_restore_is_byte_identical():
    d = Path(tempfile.mkdtemp())
    reviewed = d / "templates" / "reviewed"
    host = "example.com"
    fp = _seed(reviewed, host, {"host": host, "version": "3.66.470", "n": 1})
    original_sha = _sha(fp)

    bk = tb.backup_template(host, reviewed_dir=reviewed, reason="unit")
    assert bk["ok"] is True, bk
    assert bk.get("backed_up") is True, bk

    # Corrupt the live template.
    fp.write_text('{"host":"example.com","n":999,"CORRUPT":true}', "utf-8")
    assert _sha(fp) != original_sha

    rb = tb.restore_template(host, reviewed_dir=reviewed)
    assert rb["ok"] is True, rb
    assert _sha(fp) == original_sha, "restore must be byte-identical to the backup"
    shutil.rmtree(d, ignore_errors=True)


def test_manifest_records_sha_reason_version():
    d = Path(tempfile.mkdtemp())
    reviewed = d / "templates" / "reviewed"
    host = "site.test"
    fp = _seed(reviewed, host, {"host": host, "version": "3.66.470"})
    expect_sha = _sha(fp)

    bk = tb.backup_template(host, reviewed_dir=reviewed, reason="drift-quarantine")
    assert bk["ok"] is True, bk
    man_path = Path(bk["dir"]) / "manifest.json"
    assert man_path.is_file(), bk
    man = json.loads(man_path.read_text("utf-8"))
    assert man["sha256"] == expect_sha, man
    assert man["reason"] == "drift-quarantine", man
    assert man.get("version") == "3.66.470", man
    assert man["host"] == host, man
    shutil.rmtree(d, ignore_errors=True)


def test_multiple_generations_retained():
    d = Path(tempfile.mkdtemp())
    reviewed = d / "templates" / "reviewed"
    host = "gen.test"
    _seed(reviewed, host, {"host": host, "v": 1})

    b1 = tb.backup_template(host, reviewed_dir=reviewed, reason="first")
    (reviewed / f"{host}{_SUFFIX}").write_text('{"host":"gen.test","v":2}', "utf-8")
    b2 = tb.backup_template(host, reviewed_dir=reviewed, reason="second")
    assert b1["ok"] and b2["ok"]
    assert b1["ts"] != b2["ts"], "two backups must occupy distinct generations"

    gens = tb.list_backups(host, reviewed_dir=reviewed)
    assert len(gens) == 2, gens
    shutil.rmtree(d, ignore_errors=True)


def test_restore_specific_timestamp():
    d = Path(tempfile.mkdtemp())
    reviewed = d / "templates" / "reviewed"
    host = "ts.test"
    fp = _seed(reviewed, host, {"host": host, "v": 1})
    sha_v1 = _sha(fp)
    b1 = tb.backup_template(host, reviewed_dir=reviewed, reason="v1")

    fp.write_text('{"host":"ts.test","v":2}', "utf-8")
    tb.backup_template(host, reviewed_dir=reviewed, reason="v2")

    fp.write_text('{"host":"ts.test","v":3,"LIVE":true}', "utf-8")

    rb = tb.restore_template(host, ts=b1["ts"], reviewed_dir=reviewed)
    assert rb["ok"] is True, rb
    assert _sha(fp) == sha_v1, "restoring an explicit ts must recover that generation"
    shutil.rmtree(d, ignore_errors=True)


def test_backup_failure_is_reported_not_swallowed():
    d = Path(tempfile.mkdtemp())
    reviewed = d / "templates" / "reviewed"
    host = "fail.test"
    _seed(reviewed, host, {"host": host})

    # Point the backup root at a path that cannot be created (a *file* where the
    # backups dir must be a directory) -> backup must report ok=False, not raise
    # past the caller and not silently "succeed".
    blocker = d / "templates" / ".gold_backups"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not a directory", "utf-8")  # occupy the dir name with a file

    bk = tb.backup_template(host, reviewed_dir=reviewed, reason="x")
    assert bk["ok"] is False, bk
    shutil.rmtree(d, ignore_errors=True)


def test_no_live_template_is_ok_but_not_backed_up():
    d = Path(tempfile.mkdtemp())
    reviewed = d / "templates" / "reviewed"
    reviewed.mkdir(parents=True, exist_ok=True)
    bk = tb.backup_template("absent.com", reviewed_dir=reviewed, reason="x")
    assert bk["ok"] is True, bk
    assert bk.get("backed_up") is False, bk
    shutil.rmtree(d, ignore_errors=True)


def test_safe_overwrite_aborts_when_backup_fails():
    """The keystone guarantee: an auto-write must NOT touch live if the
    generational backup could not be taken."""
    d = Path(tempfile.mkdtemp())
    reviewed = d / "templates" / "reviewed"
    host = "abort.test"
    fp = _seed(reviewed, host, {"host": host, "v": "gold"})
    live_sha = _sha(fp)

    # Block the generational backup dir as in the failure test.
    blocker = d / "templates" / ".gold_backups"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("blocked", "utf-8")

    res = tk.safe_overwrite(host, {"host": host, "v": "NEW"}, reviewed_dir=reviewed)
    assert res["ok"] is False, res
    assert "backup" in (res.get("error") or "").lower(), res
    assert _sha(fp) == live_sha, "live must be UNTOUCHED when backup fails"
    shutil.rmtree(d, ignore_errors=True)
