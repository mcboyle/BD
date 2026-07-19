"""v3.66.749 -- secret_scan's skip set is DERIVED from the manifest
exclusion canon, not re-typed.

The disease is KB_JUDGMENT (f) living inside a walk: _SECRET_SKIP_DIRS
was a hand-copied literal of the "not source" set. The canon
(_MANIFEST_EXCLUDE_DIRS) has since grown -- screenshots, .pytest_cache,
results, profiles, .mypy_cache, then .hypothesis and state @748 -- and
the scanner's copy never learned any of it. On stash the install dir
accretes exactly those dirs, and secret_scan regex-reads every
.json/.txt line inside them: an unpruned walk over runtime accretion,
same shape 742 fixed for bat_lint/sh_lint.

A mirror also fails silently in the dangerous direction: any FUTURE dir
added to the manifest canon would be silently walked by the scanner
forever. Deriving (canon | scanner-specific extras) makes drift
structurally impossible.

Scanner-specific extras stay: tests/ (fake creds by design),
sast_results/dast_results (scanner output trees).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bulk_downloader.dev_suite.audit_security as asec
import bulk_downloader.dev_suite.release_lint as rl


def _mk(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


# Secret-SHAPED string, assembled so no raw credential shape lives in
# this file (the toolchain's own secret gates scan tests/ too).
_FAKE_AWS = "AKIA" + "EXAMPLEKEY0" + "12345"          # AKIA + 16 [0-9A-Z]
_PLANT = "aws_key = " + _FAKE_AWS + "\n"


def test_secret_skip_dirs_derived_from_manifest_canon():
    """Every dir the manifest verifier declares 'not source' must be
    outside the scanner's denominator. RED on pristine: the re-typed
    copy is missing screenshots, .pytest_cache, results, profiles,
    .mypy_cache, .hypothesis, state."""
    missing = set(rl._MANIFEST_EXCLUDE_DIRS) - set(asec._SECRET_SKIP_DIRS)
    assert not missing, (
        f"secret_scan's skip set drifted from the manifest canon; it will "
        f"walk {sorted(missing)} on stash -- the re-typed mirror never "
        f"learns what the canon learns (KB_JUDGMENT f)"
    )


def test_secret_skip_dirs_keep_scanner_extras():
    """Deriving from the canon must not drop the scanner's own
    exclusions -- tests/ carries fake creds by design."""
    extras = {"tests", "sast_results", "dast_results"}
    missing = extras - set(asec._SECRET_SKIP_DIRS)
    assert not missing, f"scanner-specific exclusions lost: {sorted(missing)}"


def test_secret_scan_does_not_descend_into_manifest_excluded_dirs(
        monkeypatch, tmp_path):
    """Behavioral proof at descend time: a secret-shaped string under
    results/ or .hypothesis/ is runtime accretion, not source -- it must
    never be read, let alone flagged. RED on pristine for the dirs the
    mirror is missing."""
    root = str(tmp_path)
    _mk(root, "real_src.py", _PLANT)                 # the one true subject
    for d in ("results", "profiles", "screenshots", ".pytest_cache",
              ".mypy_cache", ".hypothesis", "state",
              "venv", "node_modules", "tests"):
        _mk(root, f"{d}/bait.txt", _PLANT)

    monkeypatch.setattr(asec, "_repo_root", lambda: tmp_path)

    r = asec.secret_scan()

    assert r["ok"] is True
    flagged = {f["file"] for f in r["findings"]}
    assert flagged == {"real_src.py"}, (
        f"scanner read outside its subject: {sorted(flagged)} -- these dirs "
        "are exactly what accretes on stash and why /api/dev/secret_scan "
        "sits on L34's advisory slow-list"
    )
    assert r["files_scanned"] == 1, (
        f"files_scanned={r['files_scanned']}: the walk PAID for excluded "
        "dirs (post-hoc filtering is not pruning)"
    )
