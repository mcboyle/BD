"""F-SWEEP-N1 -- backup smoke_restore must confine archive extraction.

bulk_downloader/backup_verify.py::smoke_restore mkdtemp's a sandbox then
tf.extractall(sandbox) / zf.extractall(sandbox) with no member vetting, so a
tar/zip member with a '../' traversal or an absolute path escapes the sandbox
(tar-slip / zip-slip). These tests drive smoke_restore through a *controlled*
sandbox (tempfile.mkdtemp patched to a dir inside the test tree) so any escape
lands where we can assert on it and clean it up.

RED on pristine 3.66.568 (extractall writes the escape file + returns ok=True);
GREEN after the confine fix (escape rejected -> ok=False, nothing written out).

Runs under the custom run_tests.py harness: zero-arg tests, tempfile.mkdtemp
(no tmp_path), manual save/restore of the patched global in try/finally.
"""

import io
import os
import shutil
import tarfile
import tempfile
import zipfile

import bulk_downloader.backup_verify as bv


def _write_tar_member(tar_path, member_name, data=b"pwned"):
    """Build a tar whose single regular-file member is `member_name`."""
    with tarfile.open(tar_path, "w") as tf:
        ti = tarfile.TarInfo(name=member_name)
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))


def _write_zip_member(zip_path, member_name, data=b"pwned"):
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(member_name, data)


def _run_smoke_restore_in(controlled_sandbox, archive_path):
    """Call bv.smoke_restore with its mkdtemp forced to controlled_sandbox."""
    orig_mkdtemp = tempfile.mkdtemp
    tempfile.mkdtemp = lambda *a, **k: controlled_sandbox
    try:
        return bv.smoke_restore(archive_path)
    finally:
        tempfile.mkdtemp = orig_mkdtemp


def test_smoke_restore_rejects_tar_dotdot_traversal():
    work = tempfile.mkdtemp(prefix="n1-tar-dotdot-")
    try:
        sbx = os.path.join(work, "sbx")
        os.makedirs(sbx, exist_ok=True)
        marker = "n1_tar_escape.txt"
        # '../marker' from inside sbx lands in `work` (one level up) -- outside.
        tar_path = os.path.join(work, "evil.tar")
        _write_tar_member(tar_path, "../" + marker)

        res = _run_smoke_restore_in(sbx, tar_path)

        escaped = os.path.join(work, marker)  # parent of sbx
        assert not os.path.exists(escaped), (
            f"tar '../' member escaped the sandbox: {escaped} was written")
        assert res.get("ok") is False, (
            f"smoke_restore must reject a traversal tar, got ok={res.get('ok')!r}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_smoke_restore_rejects_tar_absolute_member():
    work = tempfile.mkdtemp(prefix="n1-tar-abs-")
    try:
        sbx = os.path.join(work, "sbx")
        os.makedirs(sbx, exist_ok=True)
        # An ABSOLUTE member path (still inside `work` so we can clean it up, but
        # os.path.join(sbx, abs) == abs -> outside sbx). os.path.join discards the
        # sandbox when the member is absolute, so this escapes on pristine.
        abs_target = os.path.join(work, "abs_escape.txt")
        tar_path = os.path.join(work, "evil_abs.tar")
        _write_tar_member(tar_path, abs_target)

        res = _run_smoke_restore_in(sbx, tar_path)

        assert not os.path.exists(abs_target), (
            f"absolute tar member escaped the sandbox: {abs_target} was written")
        assert res.get("ok") is False, (
            f"smoke_restore must reject an absolute-path tar, got ok={res.get('ok')!r}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_smoke_restore_rejects_zip_dotdot_traversal():
    work = tempfile.mkdtemp(prefix="n1-zip-dotdot-")
    try:
        sbx = os.path.join(work, "sbx")
        os.makedirs(sbx, exist_ok=True)
        marker = "n1_zip_escape.txt"
        zip_path = os.path.join(work, "evil.zip")
        _write_zip_member(zip_path, "../" + marker)

        res = _run_smoke_restore_in(sbx, zip_path)

        escaped = os.path.join(work, marker)
        assert not os.path.exists(escaped), (
            f"zip '../' member escaped the sandbox: {escaped} was written")
        assert res.get("ok") is False, (
            f"smoke_restore must reject a zip-slip archive, got ok={res.get('ok')!r}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_smoke_restore_rejects_tar_symlink_escape():
    """A tar symlink member that redirects a later write outside the sandbox
    must be refused. Path-name confinement alone cannot catch this: the link
    target only resolves at extraction time, so the member NAMES all look
    in-bounds -- the fix must reject link members outright."""
    work = tempfile.mkdtemp(prefix="n1-tar-symlink-")
    try:
        sbx = os.path.join(work, "sbx")
        os.makedirs(sbx, exist_ok=True)
        marker = "symlink_escape.txt"
        tar_path = os.path.join(work, "evil_link.tar")
        with tarfile.open(tar_path, "w") as tf:
            link = tarfile.TarInfo("sneaky")
            link.type = tarfile.SYMTYPE
            link.linkname = ".."          # sbx/sneaky -> work (parent of sbx)
            tf.addfile(link)
            data = b"pwned"
            through = tarfile.TarInfo("sneaky/" + marker)  # write through the link
            through.size = len(data)
            tf.addfile(through, io.BytesIO(data))

        res = _run_smoke_restore_in(sbx, tar_path)

        escaped = os.path.join(work, marker)   # would land in work via the symlink
        assert not os.path.exists(escaped), (
            f"tar symlink member redirected a write outside the sandbox: {escaped}")
        assert res.get("ok") is False, (
            f"smoke_restore must reject a tar with a link member, got ok={res.get('ok')!r}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_smoke_restore_benign_tar_still_extracts():
    """Regression guard: a normal tarball still restores ok (no over-blocking)."""
    work = tempfile.mkdtemp(prefix="n1-benign-tar-")
    try:
        sbx = os.path.join(work, "sbx")
        os.makedirs(sbx, exist_ok=True)
        tar_path = os.path.join(work, "good.tar.gz")
        with tarfile.open(tar_path, "w:gz") as tf:
            for name, data in (("meta.json", b'{"v":1}'),
                               ("data/rows.csv", b"a,b\n1,2\n")):
                ti = tarfile.TarInfo(name=name)
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))

        res = _run_smoke_restore_in(sbx, tar_path)

        assert res.get("ok") is True, (
            f"benign tarball should restore ok, got {res!r}")
        extracted = res.get("extracted_files") or []
        assert any("meta.json" in e for e in extracted), (
            f"expected extracted files listed, got {extracted!r}")
        assert os.path.exists(os.path.join(sbx, "meta.json")), (
            "benign member should be present inside the sandbox")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_smoke_restore_benign_zip_still_extracts():
    work = tempfile.mkdtemp(prefix="n1-benign-zip-")
    try:
        sbx = os.path.join(work, "sbx")
        os.makedirs(sbx, exist_ok=True)
        zip_path = os.path.join(work, "good.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("meta.json", b'{"v":1}')
            zf.writestr("nested/f.txt", b"ok")

        res = _run_smoke_restore_in(sbx, zip_path)

        assert res.get("ok") is True, (
            f"benign zip should restore ok, got {res!r}")
        assert os.path.exists(os.path.join(sbx, "meta.json")), (
            "benign zip member should be present inside the sandbox")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    for fn in sorted(k for k in dict(globals()) if k.startswith("test_")):
        globals()[fn]()
        print("PASS", fn)
