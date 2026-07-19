"""v3.66.321 — redacted-fixture exemption in the release-diff forbidden gate.

``tools/diff_release_zips.forbidden_artifacts`` hard-failed on EVERY ``.wacz`` in a
candidate zip, which forced baseline-less builds (dropping the diff/regression
hygiene gates) whenever the operator-confirmed recognizer fixtures shipped. The
exemption: redacted recognizer fixtures under ``tests/fixtures/`` (F2-scrubbed,
names/shapes only) are allowed; a real (non-``.redacted``) ``.wacz``, or any
``.wacz`` outside ``tests/fixtures/``, stays forbidden, and every other forbidden
class (.pyc, captures/, secrets, history DB) is unchanged.

Sandbox: pure stdlib, zero-arg tests.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
for _p in (str(_REPO), str(_REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import diff_release_zips as drz  # noqa: E402


def test_redacted_fixtures_are_exempt():
    names = [
        "tests/fixtures/vidstack/miruro.redacted.wacz",
        "tests/fixtures/vidstack/mirurow.redacted.wacz",
    ]
    assert drz.forbidden_artifacts(names) == []


def test_real_wacz_still_forbidden_everywhere():
    # non-redacted .wacz anywhere, and a .wacz OUTSIDE tests/fixtures, stay forbidden
    names = [
        "tests/fixtures/vidstack/raw_capture.wacz",          # not .redacted
        "captures/some_session.redacted.wacz",                # right suffix, wrong dir
        "bulk_downloader/leaked.redacted.wacz",               # right suffix, wrong dir
    ]
    assert sorted(drz.forbidden_artifacts(names)) == sorted(names)


def test_other_forbidden_classes_unaffected():
    names = [
        "bulk_downloader/app.pyc",
        "tests/fixtures/vidstack/__pycache__/x.pyc",
        "data/captures/a.json",
        "downloader_history.db",
        "secrets/.env",
        "tests/fixtures/vidstack/ok.redacted.wacz",  # the one allowed item
    ]
    bad = drz.forbidden_artifacts(names)
    assert "tests/fixtures/vidstack/ok.redacted.wacz" not in bad
    for p in names[:-1]:
        assert p in bad, p
