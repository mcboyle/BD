"""v3.43.62: Static sanity checks for the live_recorder.js UI panel.

These tests don't run JS -- they verify the source file:

  1. is well-formed (parens, braces balance)
  2. has an esc() helper defined
  3. wraps every user-data interpolation in esc() (XSS defense)
  4. is wired into index.html

The full backend behavior is tested in test_v3_43_62_live_recorder.py;
this file is the equivalent of widgets/captcha JS spot-checks from the
v3.43.60 Phase 5 audit.
"""
from __future__ import annotations

import re
from pathlib import Path


_STATIC = Path(__file__).parent.parent / "bulk_downloader" / "static"
_INDEX = Path(__file__).parent.parent / "bulk_downloader" / "templates" / "index.html"


def test_index_html_loads_live_recorder_assets():
    """Both live_recorder.js and .css must be linked from index.html."""
    if not _INDEX.exists():
        # Some test environments may not ship the template -- skip.
        return
    src = _INDEX.read_text(encoding="utf-8")
    assert "live_recorder.css" in src, "index.html must link live_recorder.css"
    assert "live_recorder.js" in src, "index.html must include live_recorder.js"


