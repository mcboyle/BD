#!/usr/bin/env python3
"""Atomically exchange two existing paths with Linux renameat2."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys


AT_FDCWD = -100
RENAME_EXCHANGE = 2


def exchange(left: str | os.PathLike[str], right: str | os.PathLike[str]) -> None:
    a = os.fsencode(Path(left))
    b = os.fsencode(Path(right))
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError("renameat2(RENAME_EXCHANGE) is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(AT_FDCWD, a, AT_FDCWD, b, RENAME_EXCHANGE) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), f"{left} <-> {right}")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: install_exchange.py LEFT RIGHT", file=sys.stderr)
        return 2
    try:
        exchange(args[0], args[1])
    except OSError as exc:
        print(f"BD-INSTALL-UNRUNNABLE: atomic exchange failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
