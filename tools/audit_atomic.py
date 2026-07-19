"""Audit: every `tmp.write_text(...)` (or equivalent staging write) must
be followed by `os.replace(tmp, final)` or `tmp.replace(final)` (Path
method) within a small window. Anything else is a non-atomic write —
a power loss mid-write leaves the canonical file truncated.

Usage:  python tools/audit_atomic.py [repo_root]
Exits 0 if clean, 1 if violations found. Designed to be wired into
pre-commit hooks so non-atomic writes can't sneak in unnoticed.

Added: v3.47.8 (#43) — closes the atomic-write audit item from the
phase 0 hardening pass."""
import re, sys
from pathlib import Path

# Default to repo root inferred from this script's location (tools/.)
if len(sys.argv) > 1:
    ROOT = Path(sys.argv[1]) / "bulk_downloader"
else:
    ROOT = Path(__file__).resolve().parent.parent / "bulk_downloader"
if not ROOT.is_dir():
    sys.stderr.write(f"error: {ROOT} does not exist\n")
    sys.exit(2)
PATTERN_STAGE = re.compile(r"(tmp|_tmp\w*|staging\w*)\s*\.\s*write_(text|bytes)\s*\(")
PATTERN_REPLACE = re.compile(
    r"(?:os|_os)\.replace\s*\("       # os.replace(tmp, dst)
    r"|"
    r"\.replace\s*\("                  # tmp.replace(dst) — Path.replace
)
LOOKAHEAD = 25  # lines after the staging write to look for the replace

violations = []
for py in sorted(ROOT.rglob("*.py")):
    lines = py.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines, 1):
        if not PATTERN_STAGE.search(line):
            continue
        # Look ahead for os.replace within window
        window = "\n".join(lines[i:i + LOOKAHEAD])
        if not PATTERN_REPLACE.search(window):
            # Sometimes the write is the tmp dump from `shutil.move(tmp, ...)`
            # or `Path.rename()` — accept those too
            if re.search(r"\.rename\s*\(|shutil\.(?:move|copy)\s*\(", window):
                continue
            # Some writes are to a logfile that's intended to be append-only.
            # We only flag stagings whose name looks like ".tmp" or whose
            # var name starts with `tmp`/`_tmp`/`staging`.
            violations.append((py, i, line.strip()))

if not violations:
    print("CLEAN — every staging write is paired with an atomic replacement.")
else:
    print(f"FOUND {len(violations)} unpaired staging writes:")
    for path, lineno, content in violations:
        print(f"  {path.relative_to(ROOT.parent)}:{lineno}  {content[:120]}")
sys.exit(1 if violations else 0)
