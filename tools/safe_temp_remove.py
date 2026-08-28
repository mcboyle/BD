"""Fail-closed removal for stale shared-temp entries not owned at creation."""
from __future__ import annotations

import ctypes
import errno
import os
import shutil
import stat
from pathlib import Path

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1


def _private_name(name: str) -> str:
    suffix = ".bdrm-" + os.urandom(8).hex()
    raw = os.fsencode(name)[:255 - len(os.fsencode(suffix))]
    return os.fsdecode(raw) + suffix


def _rename_noreplace(parent_fd: int, old: str, private: str) -> None:
    """renameat2(RENAME_NOREPLACE), or refuse; never emulate with clobber."""
    libc = ctypes.CDLL(None, use_errno=True)
    fn = getattr(libc, "renameat2", None)
    if fn is None:
        raise OSError(errno.EOPNOTSUPP, "renameat2 unavailable")
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                   ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    if fn(parent_fd, os.fsencode(old), parent_fd, os.fsencode(private),
          _RENAME_NOREPLACE) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), old)


def _destroy_private(path: Path) -> None:
    st = os.lstat(path)
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def rename_verify_destroy_at(
        parent_fd: int,
        name: str,
        expected_identity: tuple[int, int] | None = None,
) -> tuple[bool, str | None]:
    """Remove one direct child through an already-proven parent descriptor.

    The caller owns the parent capability and the policy that made ``name`` a
    candidate. This function binds the removal itself to that capability, so a
    renamed or replaced parent pathname cannot redirect destruction elsewhere.
    """
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        return False, f"unsafe direct-child name: {name!r}"
    held = None
    try:
        try:
            before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            held = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent_fd)
        except FileNotFoundError:
            if expected_identity is not None:
                return False, ("expected identity %r disappeared before acquisition"
                               % (expected_identity,))
            return True, None
        observed = (before.st_dev, before.st_ino)
        if expected_identity is not None and observed != expected_identity:
            return False, ("creation identity mismatch: expected %r, found %r"
                           % (expected_identity, observed))
        expected = observed
        acquired = os.fstat(held)
        if (acquired.st_dev, acquired.st_ino) != expected:
            return False, "identity changed during acquisition"
        private = _private_name(name)
        _rename_noreplace(parent_fd, name, private)
        moved = os.stat(private, dir_fd=parent_fd, follow_symlinks=False)
        if (moved.st_dev, moved.st_ino) != expected:
            return False, ("private-name identity mismatch; foreign entry left "
                           f"untouched at {private}")
        if stat.S_ISDIR(moved.st_mode):
            _destroy_private(Path(f"/proc/self/fd/{parent_fd}") / private)
        else:
            os.unlink(private, dir_fd=parent_fd)
        try:
            os.stat(private, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            return False, f"private entry still exists after removal: {private}"
        remaining_links = os.fstat(held).st_nlink
        expected_links = (0 if stat.S_ISDIR(before.st_mode)
                          else max(0, before.st_nlink - 1))
        if remaining_links != expected_links:
            return False, ("held identity link count did not decrease by one "
                           f"({before.st_nlink} -> {remaining_links})")
        return True, None
    except OSError as exc:
        if exc.errno in {errno.EINVAL, errno.EOPNOTSUPP, errno.ENOSYS}:
            return False, ("renameat2(RENAME_NOREPLACE) unsupported: "
                           f"{type(exc).__name__}: {exc}")
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if held is not None:
            os.close(held)


def rename_verify_destroy(
        path: str | os.PathLike[str],
        expected_identity: tuple[int, int] | None = None,
) -> tuple[bool, str | None]:
    """Move the observed inode to an unguessable name, verify, then destroy.

    These janitors did not create the object, so creation-time identity is not
    available. The reviewed bundle remains the authority for this sequence:
    never issue a destructive call against the public name, and never report a
    failed or unverified removal as success.
    """
    target = Path(path)
    parent = target.parent
    name = target.name
    parent_fd = None
    try:
        try:
            parent_fd = os.open(
                parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return True, None
        return rename_verify_destroy_at(
            parent_fd, name, expected_identity=expected_identity)
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
