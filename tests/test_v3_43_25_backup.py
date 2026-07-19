"""v3.43.25 regression tests — one-click backup.

Round-trip tests: create a backup, restore it elsewhere, verify
the files match. Plus the security-critical path-traversal rejection,
encryption-with-passphrase, and the manifest format.
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import io
import json
# [SAST 3:13pm 13 may] removed unused: import os
import tempfile
import zipfile
from pathlib import Path


def _make_test_environment(base: Path):
    """Build a minimal app working directory with the key files
    present. Returns the dict of {filename: contents} we wrote so
    tests can assert exact match after restore."""
    contents = {
        "sites_config.json": '{"abc123": {"name": "Test Site"}}',
        "app_config.json": '{"global_max_concurrent": 4}',
        "secrets.json": '{"encrypted": "blob"}',
        "user_templates.json": '[]',
    }
    for name, body in contents.items():
        (base / name).write_text(body, encoding="utf-8")
    # Nested dir: cookies/
    (base / "cookies").mkdir()
    (base / "cookies" / "abc123.json").write_text(
        '[{"name": "session", "value": "xyz"}]', encoding="utf-8")
    contents["cookies/abc123.json"] = '[{"name": "session", "value": "xyz"}]'
    return contents


def test_backup_round_trip_plain():
    """Create a plain (unencrypted) backup, restore to a different dir,
    verify all files match byte-for-byte."""
    from bulk_downloader import backup as bd_backup
    with tempfile.TemporaryDirectory() as src_d, \
            tempfile.TemporaryDirectory() as dst_d, \
            tempfile.TemporaryDirectory() as backup_d:
        src = Path(src_d)
        dst = Path(dst_d)
        original = _make_test_environment(src)
        # Backup
        backup_path = Path(backup_d) / "test.zip"
        result = bd_backup.create_backup(
            backup_path, base_dir=src, include_db=False, passphrase=None,
        )
        assert result["ok"], result.get("error")
        assert result["files"] > 0
        assert not result["encrypted"]
        assert backup_path.exists()
        # Restore
        rresult = bd_backup.restore_backup(
            backup_path, target_dir=dst, passphrase=None,
        )
        assert rresult["ok"], rresult.get("error")
        assert rresult["files_restored"] > 0
        # Verify
        for name, expected in original.items():
            got = (dst / name).read_text(encoding="utf-8")
            assert got == expected, f"{name} round-trip mismatch"


def test_backup_round_trip_encrypted():
    """With a passphrase, the backup file must be unreadable without it
    and round-trip cleanly with it."""
    from bulk_downloader import backup as bd_backup
    with tempfile.TemporaryDirectory() as src_d, \
            tempfile.TemporaryDirectory() as dst_d, \
            tempfile.TemporaryDirectory() as backup_d:
        src = Path(src_d)
        dst = Path(dst_d)
        original = _make_test_environment(src)
        backup_path = Path(backup_d) / "encrypted.bdbk"
        # Create encrypted
        result = bd_backup.create_backup(
            backup_path, base_dir=src, include_db=False,
            passphrase="correct horse battery staple",
        )
        assert result["ok"], result.get("error")
        assert result["encrypted"]
        # File MUST start with BDBK magic to identify as encrypted
        with open(backup_path, "rb") as f:
            head = f.read(4)
        assert head == b"BDBK", "encrypted backup missing magic header"
        # Restoring without passphrase must fail clean
        r_no = bd_backup.restore_backup(backup_path, target_dir=dst,
                                          passphrase=None)
        assert not r_no["ok"]
        assert "passphrase" in r_no["error"].lower()
        # Restoring with wrong passphrase must also fail
        r_wrong = bd_backup.restore_backup(backup_path, target_dir=dst,
                                             passphrase="wrong")
        assert not r_wrong["ok"]
        assert "passphrase" in r_wrong["error"].lower()
        # Restoring with right passphrase works
        r_ok = bd_backup.restore_backup(
            backup_path, target_dir=dst,
            passphrase="correct horse battery staple",
        )
        assert r_ok["ok"], r_ok.get("error")
        # Verify content
        for name, expected in original.items():
            got = (dst / name).read_text(encoding="utf-8")
            assert got == expected


def test_backup_includes_manifest():
    """Every backup zip must have a _manifest.json at the root with
    version + timestamp. Restore uses this to detect format changes."""
    from bulk_downloader import backup as bd_backup
    with tempfile.TemporaryDirectory() as src_d, \
            tempfile.TemporaryDirectory() as backup_d:
        src = Path(src_d)
        _make_test_environment(src)
        backup_path = Path(backup_d) / "test.zip"
        bd_backup.create_backup(backup_path, base_dir=src,
                                  include_db=False, passphrase=None)
        with zipfile.ZipFile(backup_path, "r") as zf:
            assert "_manifest.json" in zf.namelist()
            m = json.loads(zf.read("_manifest.json").decode("utf-8"))
            assert m["format_version"] == 1
            assert m["bd_version"] == "3.43.25"
            assert "created_at_iso" in m
            assert m["includes_db"] is False  # we passed include_db=False


def test_restore_rejects_path_traversal():
    """A malicious backup with `../etc/passwd` entries must be rejected,
    not extracted. This is the standard zip-slip attack."""
    from bulk_downloader import backup as bd_backup
    with tempfile.TemporaryDirectory() as src_d, \
            tempfile.TemporaryDirectory() as dst_d, \
            tempfile.TemporaryDirectory() as bk_d:
        # Hand-craft an evil zip
        backup_path = Path(bk_d) / "evil.zip"
        with zipfile.ZipFile(backup_path, "w") as zf:
            zf.writestr("_manifest.json", '{"format_version": 1}')
            zf.writestr("../../../etc/passwd_pwned", "ohno")
            zf.writestr("good_file.txt", "I am safe")
        result = bd_backup.restore_backup(
            backup_path, target_dir=dst_d, passphrase=None,
        )
        assert result["ok"]
        # The good file must restore; the bad one must be skipped
        assert (Path(dst_d) / "good_file.txt").exists()
        # The traversal target must NOT exist anywhere outside dst_d
        assert not (Path(dst_d).parent.parent.parent / "etc" / "passwd_pwned").exists()
        # And the warnings should mention the rejection
        assert any("unsafe" in w.lower() or "path-escape" in w.lower()
                    for w in result["warnings"]), (
            f"path traversal not flagged in warnings: {result['warnings']}"
        )


def test_dry_run_doesnt_write():
    """dry_run=True must NOT create or modify any files in target_dir."""
    from bulk_downloader import backup as bd_backup
    with tempfile.TemporaryDirectory() as src_d, \
            tempfile.TemporaryDirectory() as dst_d, \
            tempfile.TemporaryDirectory() as bk_d:
        src = Path(src_d)
        _make_test_environment(src)
        backup_path = Path(bk_d) / "test.zip"
        bd_backup.create_backup(backup_path, base_dir=src,
                                  include_db=False, passphrase=None)
        # Note what's in dst BEFORE
        before = set(Path(dst_d).rglob("*"))
        result = bd_backup.restore_backup(
            backup_path, target_dir=dst_d, dry_run=True,
        )
        assert result["ok"]
        assert result["dry_run"] is True
        assert result["files_restored"] > 0
        # AFTER must be identical to BEFORE
        after = set(Path(dst_d).rglob("*"))
        assert before == after, "dry_run modified the filesystem"


def test_default_backup_filename_format():
    """The filename must be sortable lexically (year-month-day order)
    so backups list chronologically in a directory."""
    from bulk_downloader.backup import default_backup_filename
    name = default_backup_filename()
    assert name.startswith("bd_backup_")
    assert name.endswith(".zip")
    # YYYY-MM-DD_HHMM format
    import re
    assert re.match(r"bd_backup_\d{4}-\d{2}-\d{2}_\d{4}\.zip", name), name


def test_skipped_files_reported():
    """Missing optional files should be reported in `skipped`, not crash."""
    from bulk_downloader import backup as bd_backup
    with tempfile.TemporaryDirectory() as src_d, \
            tempfile.TemporaryDirectory() as bk_d:
        # src is empty — nothing to back up
        result = bd_backup.create_backup(
            Path(bk_d) / "empty.zip", base_dir=src_d,
            include_db=False, passphrase=None,
        )
        assert result["ok"]
        # All expected files should be listed as skipped
        assert "sites_config.json" in result["skipped"]
        # But the manifest still got written — `files` >= 1
        assert result["files"] == 0  # zero non-manifest files
        # The zip is still valid
        backup_path = Path(bk_d) / "empty.zip"
        with zipfile.ZipFile(backup_path) as zf:
            assert "_manifest.json" in zf.namelist()


# ── Endpoints registered ───────────────────────────────────────────────

def test_backup_endpoints_registered():
    """All three endpoints must be defined in app.py."""
    _pkg = Path(__file__).resolve().parent.parent / "bulk_downloader"
    src = "\n".join([(_pkg / "app.py").read_text(encoding="utf-8")]
                    + [p.read_text(encoding="utf-8")
                       for p in sorted(_pkg.glob("app_*.py"))])
    assert "/api/backup/create" in src
    assert "def api_backup_create" in src
    assert "/api/backup/restore" in src
    assert "def api_backup_restore" in src
    assert "/api/backup/preview" in src
    assert "def api_backup_preview" in src
