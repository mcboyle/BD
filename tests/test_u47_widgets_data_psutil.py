"""U47 — psutil pinned + cpu_ram widget null-guard.

Stash diagnosis 2026-05-21 found three causes for the misleading
"0% / 0%" CPU/RAM widget on the live deployment:

  1. psutil was not installed in the venv.
  2. psutil was documented as optional in doctor.py but not pinned
     in requirements.txt — so a fresh install always missed it.
  3. The frontend pct() helper returned 0 for null/undefined input,
     so the missing keys rendered as a misleading "0%" instead of
     a clear "—" no-data state.

v3.63.6 unit #4 fixes (2) and (3):

  • requirements.txt now pins psutil (>=5.9,<7.0). It is a runtime
    dependency, not an optional extra — the widget code that uses
    it has been shipping since v3.43.60.
  • widgets.js's cpu_ram renderer null-guards cpu_pct/ram_pct
    before formatting, matching the lib_watched_pct /
    success_rate_24h pattern already used elsewhere in the file.

These tests pin both:

  • psutil with the correct version pin range is in
    requirements.txt (not requirements-test.txt or
    requirements-dev.txt — those are not installed by the runtime
    `pip install -r requirements.txt` path).
  • The cpu_ram render function uses a null-guard. Source-grep the
    JS for the `d.cpu_pct != null` clause inside the cpu_ram entry.
  • The doctor.py psutil entry is still useful (it explains *why*
    psutil exists), but if any future cleanup drops psutil from
    requirements.txt back to "optional," this test catches it.

Functional verification (the live widget shows real numbers after
psutil is installed) is operator-side.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_REQS = _REPO_ROOT / "requirements.txt"


# ── requirements.txt pins ──────────────────────────────────────────


def test_requirements_txt_exists():
    assert _REQS.is_file(), f"requirements.txt missing at {_REQS}"


def test_psutil_pinned_in_requirements_txt():
    """psutil must be in the *runtime* requirements file.

    The runtime install path is `pip install -r requirements.txt` —
    the -optional and -dev variants are deliberately separate. psutil
    powers cpu_ram, which is in DEFAULT_WIDGETS, so it has to be
    installed on every deployment.
    """
    body = _REQS.read_text(encoding="utf-8")
    # Match `psutil` at the start of a logical line (not inside a
    # comment). The `^psutil` anchor with MULTILINE handles either
    # `psutil>=5.9` or `psutil==X.Y`. Comment lines start with `#`.
    pins = [
        line for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
        and re.match(r"^psutil\b", line.strip())
    ]
    assert pins, (
        "psutil not pinned in requirements.txt — pre-v3.63.6 it was "
        "documented as optional but never installed, so the cpu_ram "
        "widget always showed misleading 0% / 0%."
    )
    assert len(pins) == 1, f"psutil pinned more than once: {pins}"


def test_psutil_pin_has_version_range():
    """The pin must have a >= floor. An unversioned `psutil` line
    works but invites the future-Python-3.13-broke-psutil pattern
    we hit with curl_cffi.
    """
    body = _REQS.read_text(encoding="utf-8")
    for line in body.splitlines():
        if line.strip().startswith("psutil"):
            assert ">=" in line, (
                f"psutil pin missing >= floor: {line!r}"
            )
            return


# ── Cross-check: doctor.py still mentions psutil ───────────────────


def test_doctor_py_still_references_psutil():
    """doctor.py's dependency catalog should keep psutil — even
    though it is now required, doctor's purpose is to *report* what
    each dependency does, and 'CPU/RAM dashboard widgets' is still
    the right description.
    """
    doctor_py = _REPO_ROOT / "bulk_downloader" / "doctor.py"
    if not doctor_py.is_file():
        return  # tolerable if the file moves
    body = doctor_py.read_text(encoding="utf-8")
    assert "psutil" in body, (
        "doctor.py no longer references psutil; the dependency "
        "catalog must keep it so a `python -m bulk_downloader.doctor` "
        "run still reports what psutil provides."
    )
