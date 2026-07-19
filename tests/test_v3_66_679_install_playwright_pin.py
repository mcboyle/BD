"""v3.66.679 -- MOD-6 completeness: unify the install-script Playwright pins.

678 raised requirements.txt to playwright>=1.61 but MISSED the hardcoded fallback
pins in install_linux.sh and install_windows.bat (the "no requirements.txt ->
install pinned core packages" branch), which still said >=1.45 -- a fresh install
with no requirements.txt could resolve back to 1.60, re-opening the drift 678
closed. This test guards EVERY playwright pin site in the repo at the unified floor.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PIN_SITES = ["requirements.txt", "install_linux.sh", "install_windows.bat"]
# match e.g. playwright>=1.61,<2.0  (in requirements or a quoted pip arg)
_FLOOR_RE = re.compile(r"playwright[\"']?\s*>=\s*(\d+)\.(\d+)")


def test_all_playwright_pins_floor_at_1_61():
    checked = 0
    for rel in _PIN_SITES:
        p = _ROOT / rel
        assert p.is_file(), f"expected {rel} in the repo"
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in _FLOOR_RE.finditer(text):
            checked += 1
            major, minor = int(m.group(1)), int(m.group(2))
            assert (major, minor) >= (1, 61), (
                f"{rel}: playwright floor >=1.61 required (unified); "
                f"found >={m.group(1)}.{m.group(2)}")
    assert checked >= 3, (
        f"expected a playwright pin in each of {_PIN_SITES}; only saw {checked}")
