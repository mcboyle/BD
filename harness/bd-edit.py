#!/usr/bin/env python3
"""Atomically replace a script: write a sibling temp file, then rename over it.

Python's write_text() TRUNCATES AND REWRITES THE SAME INODE. A bash script that
is currently executing keeps reading from that inode at its saved byte offset,
so an in-place rewrite makes it resume in the middle of different text -- which
is how bd-integrate-row.sh reported `line 138: THIS: command not found` while
`bash -n` on the same file passed cleanly. rename() swaps the directory entry
and leaves the old inode intact for the running process, so the edit takes
effect on the NEXT invocation and never corrupts the current one. Same hazard
CLAUDE.md A6 documents for scripts/deploy.sh.
"""
import os, pathlib, sys, tempfile
path = pathlib.Path(sys.argv[1])
data = sys.stdin.read()
d = path.parent
fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{path.name}.", suffix=".tmp")
with os.fdopen(fd, "w") as f:
    f.write(data)
os.chmod(tmp, path.stat().st_mode if path.exists() else 0o755)
os.replace(tmp, path)
print(f"{path.name}: replaced atomically ({len(data)} bytes)")
