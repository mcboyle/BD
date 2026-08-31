"""Row 423 -- an EMPTY backup archive must never verify or restore ok.

bulk_downloader/backup_verify.py:139 set ``ok = True`` unconditionally once the
archive opened. The only downgrade was the ``expected_members`` substring check
(lines 144-149), which the DEFAULT call skips, and ``member_count == 0`` did not
fail. ``smoke_restore`` calls ``verify_tarball(path)`` with no expected_members
(line 201), so it inherited that vacuous pass and attested "BD could start from
this backup" over ZERO content.

RED on the defective parent (55a3de38): a header-only tar -- the exact residue of
a backup job that died after writing the tar header -- produced
``{'ok': True, 'size_bytes': 10240, 'member_count': 0}`` and a smoke_restore with
``ok=True, extracted_files=[]``.

The contract this module pins:

  * a zero-member archive is a DISTINCT refusal that NAMES the emptiness, and
    smoke_restore does not attest restorability over it;
  * an UNAVAILABLE measurement (unsupported type, unreadable/truncated archive)
    reports UNKNOWN rather than OK, per CLAUDE.md A7 -- and still not-ok, so the
    tristate never fails open;
  * NEGATIVE CONTROL: a real archive with an exact nonzero member count (3) still
    verifies ok and still restores exactly those 3 files;
  * the existing ``expected_members`` downgrade is preserved, and it is a MEASURED
    miss (failed), not an unavailable measurement (unknown).

Every test stubs ``backup_verify._record`` so no repository/live DB state is
touched, and the capture doubles as a seam assertion: the audit row an operator
would read back must carry the same refusal. Repo convention: zero-arg tests,
tempfile.mkdtemp (no tmp_path), manual save/restore of patched globals in
try/finally.
"""
from __future__ import annotations

import io
import os
import shutil
import tarfile
import tempfile
import zipfile

import bulk_downloader.backup_verify as bv


# ── fixtures ────────────────────────────────────────────────────────────────

def _stub_record():
    """Replace bv._record with a capturing stub. Returns (records, restore)."""
    records: list = []
    orig = bv._record

    def _cap(path, kind, result, started):
        records.append({"path": path, "kind": kind, "result": dict(result)})

    bv._record = _cap

    def _restore():
        bv._record = orig

    return records, _restore


def _empty_tar():
    """A header-only tar: opened for write and closed with no members added --
    exactly what a backup job that died after creating the archive leaves."""
    d = tempfile.mkdtemp(prefix="row423_empty_tar_")
    p = os.path.join(d, "backup.tar")
    with tarfile.open(p, "w"):
        pass
    return d, p


def _empty_zip():
    d = tempfile.mkdtemp(prefix="row423_empty_zip_")
    p = os.path.join(d, "backup.zip")
    with zipfile.ZipFile(p, "w"):
        pass
    return d, p


def _tar_with(names):
    """A tar carrying exactly `names` as regular file members."""
    d = tempfile.mkdtemp(prefix="row423_full_tar_")
    p = os.path.join(d, "backup.tar")
    with tarfile.open(p, "w") as tf:
        for n in names:
            data = f"contents of {n}".encode()
            ti = tarfile.TarInfo(name=n)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return d, p


def _names_the_emptiness(result):
    """The refusal must be DISTINCTIVE: a file-not-found or an unreadable archive
    is also falsy, so ok=False alone would let either launder this verdict."""
    blob = f"{result.get('error', '')}".lower()
    return ("empty" in blob or "0 member" in blob or "no member" in blob
            or "zero member" in blob)


# ── precondition: the fixture really is a readable, zero-member archive ─────

def test_precondition_header_only_tar_opens_and_yields_exactly_zero_members():
    d, p = _empty_tar()
    try:
        assert os.path.isfile(p), "fixture did not create the tar"
        assert os.path.getsize(p) > 0, "a header-only tar is not a zero-byte file"
        with tarfile.open(p, "r:*") as tf:
            members = tf.getmembers()
        assert len(members) == 0, f"expected exactly 0 members, got {len(members)}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_precondition_empty_zip_opens_and_yields_exactly_zero_members():
    d, p = _empty_zip()
    try:
        assert os.path.isfile(p)
        with zipfile.ZipFile(p) as zf:
            assert len(zf.namelist()) == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── the defect: an empty archive must be a distinct refusal ─────────────────

def test_verify_tarball_refuses_an_empty_tar_and_names_the_emptiness():
    d, p = _empty_tar()
    records, restore = _stub_record()
    try:
        r = bv.verify_tarball(p)
        assert r.get("member_count") == 0, (
            f"precondition: verify_tarball must have SEEN zero members, got {r!r}")
        assert r.get("ok") is False, (
            f"an archive with zero members must never verify ok: {r!r}")
        assert r.get("state") == "failed", (
            f"a measured-empty archive is FAILED, not unknown/ok: {r!r}")
        assert _names_the_emptiness(r), (
            f"the refusal must NAME the emptiness, not just be falsy: {r!r}")
        # seam: the audit row an operator reads back carries the same refusal.
        assert len(records) == 1, f"expected exactly one audit record, got {records!r}"
        assert records[0]["kind"] == "tarball"
        assert records[0]["result"]["ok"] is False
    finally:
        restore()
        shutil.rmtree(d, ignore_errors=True)


def test_verify_tarball_refuses_an_empty_zip():
    d, p = _empty_zip()
    records, restore = _stub_record()
    try:
        r = bv.verify_tarball(p)
        assert r.get("member_count") == 0, f"precondition: {r!r}"
        assert r.get("ok") is False, f"empty zip must not verify ok: {r!r}"
        assert r.get("state") == "failed", r
        assert _names_the_emptiness(r), r
    finally:
        restore()
        shutil.rmtree(d, ignore_errors=True)


def test_smoke_restore_does_not_attest_restorability_of_an_empty_tar():
    d, p = _empty_tar()
    records, restore = _stub_record()
    r = {}
    try:
        r = bv.smoke_restore(p)
        assert r.get("member_count") == 0, f"precondition: {r!r}"
        assert r.get("ok") is False, (
            f"smoke_restore must not attest BD could start from zero content: {r!r}")
        assert r.get("state") == "failed", r
        assert _names_the_emptiness(r), r
        # An empty extracted_files list is exactly the vacuous attestation this
        # cut exists to remove: the key must not be present at all.
        assert not r.get("extracted_files"), (
            f"smoke_restore must not report an extraction for a refused archive: {r!r}")
        assert records, "smoke_restore recorded nothing"
        assert records[-1]["result"]["ok"] is False
    finally:
        restore()
        shutil.rmtree(d, ignore_errors=True)
        if r.get("restored_to"):
            shutil.rmtree(r["restored_to"], ignore_errors=True)


# ── A7: an unavailable measurement is UNKNOWN, never OK ─────────────────────

def test_unsupported_archive_type_is_unknown_not_ok():
    d = tempfile.mkdtemp(prefix="row423_unsupported_")
    records, restore = _stub_record()
    try:
        p = os.path.join(d, "backup.bin")
        with open(p, "wb") as fh:
            fh.write(b"not an archive at all")
        r = bv.verify_tarball(p)
        assert r.get("ok") is False, r
        assert r.get("state") == "unknown", (
            f"a type we cannot open is an UNAVAILABLE measurement, not a "
            f"measured failure: {r!r}")
        assert "unsupported" in f"{r.get('error', '')}".lower(), r
    finally:
        restore()
        shutil.rmtree(d, ignore_errors=True)


def test_truncated_archive_is_unknown_not_ok():
    d = tempfile.mkdtemp(prefix="row423_truncated_")
    records, restore = _stub_record()
    try:
        p = os.path.join(d, "backup.tar.gz")
        with open(p, "wb") as fh:
            fh.write(b"\x1f\x8b\x08\x00 truncated garbage, not a real gzip member")
        # precondition: this really is unreadable as an archive.
        raised = False
        try:
            with tarfile.open(p, "r:*"):
                pass
        except Exception:
            raised = True
        assert raised, "fixture is readable; it cannot exercise the unknown path"
        r = bv.verify_tarball(p)
        assert r.get("ok") is False, r
        assert r.get("state") == "unknown", (
            f"an unreadable archive is UNKNOWN, never ok: {r!r}")
        assert r.get("error"), r
    finally:
        restore()
        shutil.rmtree(d, ignore_errors=True)


def test_smoke_restore_unknown_backup_format_is_unknown_not_ok():
    d = tempfile.mkdtemp(prefix="row423_smoke_unknown_")
    records, restore = _stub_record()
    try:
        p = os.path.join(d, "backup.bin")
        with open(p, "wb") as fh:
            fh.write(b"not a backup")
        r = bv.smoke_restore(p)
        assert r.get("ok") is False, r
        assert r.get("state") == "unknown", (
            f"a format smoke_restore cannot verify is UNKNOWN: {r!r}")
    finally:
        restore()
        shutil.rmtree(d, ignore_errors=True)


def test_missing_file_is_a_measured_failure_not_unknown():
    """A backup that is NOT THERE is measured, and it is the loudest failure --
    it must not be laundered into the softer 'could not measure' state."""
    d = tempfile.mkdtemp(prefix="row423_absent_")
    records, restore = _stub_record()
    try:
        p = os.path.join(d, "no-such-backup.tar")
        assert not os.path.exists(p)
        r = bv.verify_tarball(p)
        assert r.get("ok") is False, r
        assert r.get("state") == "failed", r
    finally:
        restore()
        shutil.rmtree(d, ignore_errors=True)


# ── NEGATIVE CONTROL: a real archive still verifies and still restores ──────

def test_negative_control_three_member_tar_verifies_ok_with_exact_count():
    names = ["history.csv", "app_config.json", "README.txt"]
    d, p = _tar_with(names)
    records, restore = _stub_record()
    try:
        r = bv.verify_tarball(p)
        assert r.get("ok") is True, f"a real 3-member archive must verify ok: {r!r}"
        assert r.get("state") == "ok", r
        assert r.get("member_count") == 3, r
        assert sorted(r.get("sample_members", [])) == sorted(names), r
        assert not r.get("error"), r
    finally:
        restore()
        shutil.rmtree(d, ignore_errors=True)


def test_negative_control_three_member_tar_restores_exactly_three_files():
    names = ["history.csv", "app_config.json", "README.txt"]
    d, p = _tar_with(names)
    records, restore = _stub_record()
    r = {}
    try:
        r = bv.smoke_restore(p)
        assert r.get("ok") is True, f"a real archive must still restore: {r!r}"
        assert r.get("state") == "ok", r
        assert r.get("member_count") == 3, r
        assert sorted(r.get("extracted_files", [])) == sorted(names), r
        sandbox = r.get("restored_to")
        assert sandbox and os.path.isdir(sandbox), r
        on_disk = sorted(os.listdir(sandbox))
        assert on_disk == sorted(names), (
            f"expected exactly 3 files on disk, got {on_disk!r}")
        for n in names:
            with open(os.path.join(sandbox, n)) as fh:
                assert fh.read() == f"contents of {n}"
    finally:
        restore()
        shutil.rmtree(d, ignore_errors=True)
        if r.get("restored_to"):
            shutil.rmtree(r["restored_to"], ignore_errors=True)


def test_negative_control_three_member_zip_verifies_ok():
    d = tempfile.mkdtemp(prefix="row423_full_zip_")
    records, restore = _stub_record()
    try:
        p = os.path.join(d, "backup.zip")
        names = ["a.txt", "b.txt", "c.txt"]
        with zipfile.ZipFile(p, "w") as zf:
            for n in names:
                zf.writestr(n, "x")
        r = bv.verify_tarball(p)
        assert r.get("ok") is True, r
        assert r.get("state") == "ok", r
        assert r.get("member_count") == 3, r
    finally:
        restore()
        shutil.rmtree(d, ignore_errors=True)


# ── the existing expected_members downgrade is preserved ────────────────────

def test_expected_members_downgrade_is_preserved_and_is_a_measured_failure():
    names = ["history.csv", "app_config.json", "README.txt"]
    d, p = _tar_with(names)
    records, restore = _stub_record()
    try:
        # present -> still ok (the downgrade must not fire on a satisfied ask)
        good = bv.verify_tarball(p, expected_members=["history.csv"])
        assert good.get("ok") is True, good
        assert good.get("state") == "ok", good
        assert good.get("missing_expected") == [], good
        # absent -> refused, and MEASURED (failed), not unknown
        bad = bv.verify_tarball(p, expected_members=["history.csv", "nope.txt"])
        assert bad.get("ok") is False, bad
        assert bad.get("state") == "failed", bad
        assert bad.get("missing_expected") == ["nope.txt"], bad
        assert bad.get("member_count") == 3, bad
    finally:
        restore()
        shutil.rmtree(d, ignore_errors=True)


def test_empty_archive_refuses_even_when_expected_members_are_satisfiable_none():
    """An empty archive with expected_members=[] must not sneak through: the
    falsy list skips the downgrade branch, which is exactly the shape that let
    the default call pass."""
    d, p = _empty_tar()
    records, restore = _stub_record()
    try:
        r = bv.verify_tarball(p, expected_members=[])
        assert r.get("ok") is False, r
        assert r.get("state") == "failed", r
        assert _names_the_emptiness(r), r
    finally:
        restore()
        shutil.rmtree(d, ignore_errors=True)


# ── the operator surface: the scheduled rehearsal must inherit the refusal ──

def test_rehearsal_verdict_reports_the_empty_backup_as_not_ok():
    """rehearse() is what writes the verdict the digest reads as
    'Restore rehearsal: OK'. It sits on smoke_restore, so an empty backup must
    reach the operator as a failure that names the emptiness -- BD_HOME is
    redirected so the verdict file lands in a sandbox, and the inherited value
    is REMOVED rather than merely not set."""
    d, p = _empty_tar()
    home = tempfile.mkdtemp(prefix="row423_bdhome_")
    records, restore = _stub_record()
    had_home = "BD_HOME" in os.environ
    prev_home = os.environ.get("BD_HOME")
    os.environ["BD_HOME"] = home
    try:
        out = bv.rehearse(backup_path=p)
        assert out.get("ok") is False, (
            f"the rehearsal must not attest an empty backup restored: {out!r}")
        assert _names_the_emptiness(out), (
            f"the operator-visible error must name the emptiness: {out!r}")
        # precondition: the verdict really was persisted where the digest reads it
        persisted = bv.last_rehearsal()
        assert isinstance(persisted, dict), persisted
        assert persisted.get("ok") is False, persisted
    finally:
        restore()
        if had_home:
            os.environ["BD_HOME"] = prev_home
        else:
            os.environ.pop("BD_HOME", None)
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)


BD_GATE_SCOPE = "module"
