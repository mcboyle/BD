"""v3.66.748 — the forbidden-artifact gate must contain what is present.

AUDIT ROUND 2, finding R18. 27 `.hypothesis/` entries ship in the release zip.
The gate that exists to catch test-runner/cache junk in a release reports
CLEAN — because `.hypothesis/` is not in its list:

    FORBIDDEN_SEGMENT = ('__pycache__/', 'node_modules/', '/captures/',
                         'captures/', '/screenshots/', 'screenshots/')
    FORBIDDEN_SUFFIX  = ('.pyc', '.pyo', '.wacz')

The program's signature failure, one more time: a check whose denominator
structurally excludes the thing being asked about reports clean, truthfully and
uselessly. 0 of the 27 present entries were flagged.

`.hypothesis/` is Hypothesis's example database + constants cache — pure test-run
state, regenerated on demand, never source. It has no business in a release.

NOTE ON SCOPE (the audit's one wrong assumption): R18 said this needed a
guard-SHA re-declaration because `build_release.py` is guard #7. It does not —
the forbidden lists live in `tools/diff_release_zips.py`, which is NOT
guard-pinned. Verified against bd-guardcheck's 7-file list before editing.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "tools"))


def _drz():
    import diff_release_zips as D
    return D


def test_hypothesis_cache_is_a_forbidden_release_artifact():
    """The concrete R18 finding: a .hypothesis/ path must be FLAGGED."""
    D = _drz()
    names = [
        ".hypothesis/.gitignore",
        ".hypothesis/constants/0132d3afdfb4d0b2",
        ".hypothesis/examples/aabbcc",
        "bulk_downloader/app.py",          # real source — must NOT be flagged
    ]
    flagged = D.forbidden_artifacts(names)

    hyp = [n for n in flagged if ".hypothesis" in n]
    assert len(hyp) == 3, (
        f"the forbidden-artifact gate flagged {len(hyp)} of 3 .hypothesis "
        "entries — its denominator does not contain the thing being asked "
        "about, so it reports clean truthfully and uselessly"
    )
    assert "bulk_downloader/app.py" not in flagged, (
        "the gate flagged real source — the fix must not over-reach"
    )


def test_the_existing_forbidden_classes_still_flag():
    """A widened denominator must not lose what it already caught."""
    D = _drz()
    names = [
        "bulk_downloader/__pycache__/app.cpython-312.pyc",
        "frontend/node_modules/x/index.js",
        "captures/session.wacz",
        "reports/screenshots/x.png",
        "bulk_downloader/app.py",
    ]
    flagged = D.forbidden_artifacts(names)
    assert len(flagged) == 4, f"regressed on the existing classes: {flagged}"
    assert "bulk_downloader/app.py" not in flagged
