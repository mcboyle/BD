"""v3.66.646 -- S2.2 / CAP-4: export-import integrity via a checksum manifest.

create_backup() already writes a _manifest.json but with NO per-file checksums, and
restore_backup() does no integrity check. This cut:

  * emits a per-file sha256 map in the manifest ("checksums" + "checksum_algo") on
    create_backup(), and
  * verifies those checksums on restore_backup() -- a dry_run restore over a backup
    with checksums becomes an INTEGRITY CHECK (read each member, hash, compare) that
    reports checksum_failures + integrity_ok without touching the filesystem, so a
    corrupted/truncated backup is caught BEFORE a real restore clobbers live state.

(The roadmap named app_export/app_import as the hook, but those are a CSV/JSON dump
and a URL-list import -- not a matched round-trip. backup.py create/restore IS the
matched round-trip with a manifest, so the integrity layer belongs here.)

Sandbox-safe: temp dirs, zero-arg tests, no pytest builtins.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

from bulk_downloader import backup as bk


def _seed_base():
    """A base_dir with a couple of real BACKUP_TARGETS files."""
    d = tempfile.mkdtemp(prefix="cap4_base_")
    (Path(d) / "sites_config.json").write_text('{"sites": {"a": 1}}')
    (Path(d) / "app_config.json").write_text('{"theme": "dark"}')
    return d


def test_manifest_carries_sha256_per_file():
    base = _seed_base()
    out = os.path.join(tempfile.mkdtemp(prefix="cap4_out_"), "b.zip")
    res = bk.create_backup(out, base_dir=base, include_db=False)
    assert res["ok"], res
    with zipfile.ZipFile(out) as zf:
        man = json.loads(zf.read("_manifest.json").decode("utf-8"))
        assert man.get("checksum_algo") == "sha256", man
        sums = man.get("checksums") or {}
        assert "sites_config.json" in sums, f"manifest must checksum each file; got {sums}"
        # the recorded hash matches the real file bytes
        real = hashlib.sha256((Path(base) / "sites_config.json").read_bytes()).hexdigest()
        assert sums["sites_config.json"] == real, "recorded checksum must match the file"


def test_dry_run_restore_verifies_clean_backup():
    base = _seed_base()
    out = os.path.join(tempfile.mkdtemp(prefix="cap4_out_"), "b.zip")
    bk.create_backup(out, base_dir=base, include_db=False)
    tgt = tempfile.mkdtemp(prefix="cap4_tgt_")
    res = bk.restore_backup(out, target_dir=tgt, dry_run=True)
    assert res.get("integrity_ok") is True, f"a clean backup should verify OK; got {res}"
    assert res.get("checksum_failures") == [], res


def test_dry_run_restore_detects_corruption():
    base = _seed_base()
    out = os.path.join(tempfile.mkdtemp(prefix="cap4_out_"), "b.zip")
    bk.create_backup(out, base_dir=base, include_db=False)

    # Corrupt one member's bytes while keeping the manifest's original checksum.
    corrupt = out + ".corrupt.zip"
    with zipfile.ZipFile(out) as zin:
        names = zin.namelist()
        with zipfile.ZipFile(corrupt, "w", zipfile.ZIP_DEFLATED) as zout:
            for n in names:
                data = zin.read(n)
                if n == "sites_config.json":
                    data = b'{"sites": {"a": 999}}TAMPERED'  # different bytes
                zout.writestr(n, data)

    res = bk.restore_backup(corrupt, target_dir=tempfile.mkdtemp(prefix="cap4_c_"),
                            dry_run=True)
    assert res.get("integrity_ok") is False, f"corruption must be detected; got {res}"
    assert "sites_config.json" in (res.get("checksum_failures") or []), res


def test_backup_without_checksums_still_restores():
    """Back-compat: a pre-CAP-4 backup (no checksums in manifest) must still
    restore, reporting integrity_ok True (nothing to verify) rather than failing."""
    # Build a minimal legacy zip: manifest with no checksums + one file.
    legacy = os.path.join(tempfile.mkdtemp(prefix="cap4_legacy_"), "old.zip")
    with zipfile.ZipFile(legacy, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("_manifest.json", json.dumps({"format_version": 1}))
        zf.writestr("sites_config.json", '{"sites": {}}')
    res = bk.restore_backup(legacy, target_dir=tempfile.mkdtemp(prefix="cap4_lt_"),
                            dry_run=True)
    assert res.get("integrity_ok") is True, f"legacy backup should not fail integrity; got {res}"
    assert res.get("checksum_failures") == [], res
