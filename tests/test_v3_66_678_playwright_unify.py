"""v3.66.678 -- MOD-6: unify the Playwright pin.

The service venv resolved playwright to 1.61.0 while the sandbox prestage sat at
1.60.0 -- both satisfied the loose >=1.45,<2.0 range, so the drift was invisible
to the range but real across environments. MOD-6 raises the floor to the unified
version (1.61) so a fresh install / prestage rebuild can't drift back below it;
the dep_freshness advisory scanner then flags any environment under the floor.
The <2.0 ceiling stays (the PyInstaller .spec hard-codes playwright._impl paths a
2.x reorg could move).

This test guards the pin so it can't silently loosen again.
"""
from __future__ import annotations

import re
from pathlib import Path

_REQ = Path(__file__).resolve().parent.parent / "requirements.txt"


def _playwright_line():
    for ln in _REQ.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s.startswith("playwright") and "stealth" not in s:
            return s
    return ""


def test_playwright_floor_is_unified_at_1_61():
    line = _playwright_line()
    assert line, "no playwright pin found in requirements.txt"
    m = re.search(r">=\s*(\d+)\.(\d+)", line)
    assert m, f"playwright pin must declare a >= floor; got {line!r}"
    major, minor = int(m.group(1)), int(m.group(2))
    assert (major, minor) >= (1, 61), (
        f"playwright floor must be >=1.61 (the unified venv version); got {line!r}")


def test_playwright_ceiling_kept_below_2():
    line = _playwright_line()
    assert "<2.0" in line or "<2" in line, (
        f"the <2.0 ceiling must stay (.spec hard-codes _impl paths); got {line!r}")
