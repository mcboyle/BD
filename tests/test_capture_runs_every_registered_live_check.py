"""capture.sh must request every registered live check, and the completeness
gate must not be computed from the request.

THE DEFECT. capture.sh:860-861 reads:

    LIVE_IDS="L1,L2,...,L35"
    EXPECTED_LIVE_TESTS=$(printf '%s\n' "$LIVE_IDS" | awk -F, '{print NF}')

`LIVE_IDS` is passed to `--only`, and `EXPECTED_LIVE_TESTS` is passed to
tools/capture_verdict.py, which fails the capture when the number of checks that
ran differs from it (capture_verdict.py:107-111).

Both sides of that comparison come from the same string. The gate therefore
answers "did everything I asked for run?" while appearing to answer "did
everything run?". It catches an ID that was requested and did not run -- one
dropped from the registry -- and it can never catch an ID that exists in the
registry and was never requested.

Measured on 2026-07-29 against the shipped tree:

    harness.registry()          37
    capture.sh LIVE_IDS         35
    registered, never requested ['L36', 'L37']

L36 (m2-spa-bundle-served) and L37 (deployed-version-coherent) have never run in
any capture. L36's failure branches -- /m2/ non-200 while /api/health answers,
200 carrying a not-built header, an index.html referencing no /m2 asset, a
referenced hashed asset that 404s -- are precisely the stale-or-half-built
frontend/dist/ class that CLAUDE.md section 7 names as the one thing a git
deploy silently does not deliver. capture.sh's own on-disk index.html check has
a different denominator (a file on disk, not what the service serves) and cannot
see it.

WHY THE EXISTING TEST DID NOT CATCH THIS. tests/test_u45_capture_sh_shipped.py
asserts that L3 is present in LIVE_IDS and that the --only flag is wired. Both
are true and both stay true no matter how many registered checks are missing:
its denominator is the selection, not the catalog. This file's denominator is
harness.registry().

WHY THE FIRST TEST BELOW EXISTS. While measuring this defect I ran a probe that
imported live_tests.harness without live_tests.checks. Checks register via a
decorator at import, so registry() returned an empty list and the probe reported
0 registered and [] missing -- it would have refuted a correct finding. An empty
denominator is a failure signal, not a pass. The canary makes that unmissable
here rather than silently vacuous.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SH = REPO_ROOT / "capture.sh"


@pytest.fixture(scope="module")
def capture_body() -> str:
    if not CAPTURE_SH.is_file():
        pytest.fail(f"capture.sh not found at {CAPTURE_SH}; this gate cannot "
                    f"verify what it is asked about")
    return CAPTURE_SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def registered_ids() -> list[str]:
    """Every registered live-check ID, from the registry itself.

    live_tests.checks must be imported: the checks register through the
    @live_test decorator at import time, so importing harness alone yields an
    empty registry -- which would make every assertion in this file vacuous.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from live_tests import checks  # noqa: F401  (import IS the registration)
        from live_tests import harness
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.fail(f"live_tests did not import, so the registry cannot be "
                    f"read and this gate cannot verify its subject: {exc}")
    return [t.id for t in harness.registry()]


def _live_ids(body: str) -> list[str]:
    found = re.search(r'^LIVE_IDS="([^"]*)"', body, re.MULTILINE)
    if not found:
        pytest.fail("no LIVE_IDS= assignment found in capture.sh; the shape "
                    "this gate reads has changed and it cannot answer")
    return [s.strip() for s in found.group(1).split(",") if s.strip()]


def _expected_assignment(body: str) -> str:
    """The whole EXPECTED_LIVE_TESTS assignment, continuations resolved.

    Reading a single line is not enough: a shell assignment may continue across
    lines with a trailing backslash, and the first version of this helper did
    exactly that -- it matched `^EXPECTED_LIVE_TESTS=(.*)$` and so could not see
    the part of its own subject that lived on the next line. Same defect class
    as the one this file exists to catch, one layer up.
    """
    # Join backslash-continued lines first, so the assignment is one string.
    joined = re.sub(r"\\\n\s*", " ", body)
    found = re.search(r'^EXPECTED_LIVE_TESTS=(.*)$', joined, re.MULTILINE)
    if not found:
        pytest.fail("no EXPECTED_LIVE_TESTS= assignment found in capture.sh")
    return found.group(1)


# ── denominator canaries ─────────────────────────────────────────────────────

def test_the_registry_is_not_empty(registered_ids):
    """An empty registry makes every assertion below vacuously true."""
    assert registered_ids, (
        "harness.registry() returned no checks. Every assertion in this file "
        "compares against that list, so all of them would pass over an empty "
        "set. The usual cause is importing live_tests.harness without "
        "live_tests.checks -- registration happens at import of checks."
    )


def test_capture_sh_still_selects_live_checks(capture_body):
    """No selection means nothing to compare the registry against."""
    assert _live_ids(capture_body), (
        "capture.sh's LIVE_IDS is empty; the comparison below would be vacuous"
    )


# ── the defect ───────────────────────────────────────────────────────────────

def test_every_registered_check_is_requested_by_capture_sh(
        capture_body, registered_ids):
    """A check that exists but is never requested has never been run."""
    requested = set(_live_ids(capture_body))
    missing = [cid for cid in registered_ids if cid not in requested]
    assert not missing, (
        f"{len(missing)} live check(s) are registered but never requested by "
        f"capture.sh, so they have never run in any capture: {missing}\n\n"
        f"registry: {len(registered_ids)}  |  LIVE_IDS: {len(requested)}\n\n"
        f"Add them to LIVE_IDS. If one must NOT run on a capture host, that is "
        f"a deliberate exclusion and belongs in an explicit skip list with a "
        f"stated reason -- not in the gap between two numbers nobody compares."
    )


def test_the_expected_count_is_not_derived_from_the_selection(capture_body):
    """The completeness gate must not compute its expectation from the request.

    While EXPECTED_LIVE_TESTS is `awk NF` over LIVE_IDS, the verdict compares
    the selection against itself. It is a real gate against a check that was
    requested and did not run; it is structurally incapable of noticing a check
    that was never requested. Deriving the count from the registry makes the
    denominator contain the subject.
    """
    rhs = _expected_assignment(capture_body)
    assert "LIVE_IDS" not in rhs, (
        f"EXPECTED_LIVE_TESTS is computed from LIVE_IDS:\n\n    {rhs.strip()}\n\n"
        f"That is the same string passed to --only, so the capture verdict "
        f"compares the selection with itself and can never detect a registered "
        f"check that was never selected. Derive the count from "
        f"harness.registry() instead."
    )


def test_the_expected_count_comes_from_the_registry(capture_body):
    """State the positive form too, so the fix cannot be a bare deletion."""
    rhs = _expected_assignment(capture_body)
    assert "registry" in rhs, (
        f"EXPECTED_LIVE_TESTS does not read the live-check registry:\n\n"
        f"    {rhs.strip()}\n\n"
        f"Removing the LIVE_IDS reference is not sufficient -- the count has to "
        f"come from harness.registry(), or the verdict has no independent "
        f"denominator to compare what ran against."
    )
