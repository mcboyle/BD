"""ROW 752: the cited display gate must own its claim, not borrow a global one.

MECHANISM (measured on c9dd4176, not assumed).  The cited gate
``tests/test_row300_parallel_display_cleanup_owns_process.py::
test_cited_cleanup_leaves_a_foreign_display_alive`` replaces
``test_capture_provides_a_display.subprocess`` with a boundary that intercepts
the helper's ``bash -c``, so the display number ``_claim_unused_display``
returns is never used: the display actually under test is allocated by Xvfb
``-displayfd`` inside ``_start_exclusively_allocated_xvfb``.  The claim is pure
scaffolding -- and yet it reaches into ``/tmp/bd-display-test-X<n>.claim``, a
HOST-GLOBAL namespace shared by every concurrent xdist worker and by every
earlier suite run that was killed before releasing its claim.

Once Xvfb is present, ``_claim_unused_display`` is the only step in
``_owned_bd_start_display`` that can raise BEFORE that first ``bash -c``.  So
when the global namespace yields nothing free, the foreign display is never
started, ``holder`` stays empty, and the cited gate fails on
"precondition: expected one foreign display, observed 0" -- while the
AssertionError naming the contended resource is captured into
``target_failure`` and then discarded by that very precondition.  Scheduling,
not the subject, decides the verdict, and the diagnosis is erased.

Both halves are gated here: the gate must survive an exhausted global claim
namespace, and its precondition must report the failure it swallowed.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path
from typing import Iterator

import pytest

import test_capture_provides_a_display as display_test
import test_row300_parallel_display_cleanup_owns_process as row300


BD_GATE_SCOPE = "repo-wide"

# The width of the exhaustion fixture.  Four is enough to make the refusal
# unambiguous and small enough that the fixture's own denominator is exact.
_EXHAUSTION_CANDIDATE_COUNT = 4

# A private candidate window, far above the real _DISPLAY_CANDIDATES range, so
# occupying it cannot block a concurrent worker that is claiming for real.
_PRIVATE_WINDOW = range(7520, 8520)

_XVFB_REQUIRED = pytest.mark.skipif(
    shutil.which("Xvfb") is None or shutil.which("xdpyinfo") is None,
    reason="Xvfb and xdpyinfo are required for the display gate",
)


class _NoXvfbOnPath:
    """Stand-in for ``shutil`` that reports Xvfb missing, nothing else."""

    @staticmethod
    def which(name: str) -> str | None:
        return None


@contextlib.contextmanager
def _an_exhausted_claim_namespace() -> Iterator[tuple[int, ...]]:
    """Hold every claim file of a private candidate window, O_EXCL, for real."""
    held: list[tuple[int, Path]] = []
    numbers: list[int] = []
    try:
        for candidate in _PRIVATE_WINDOW:
            claim_path = Path(f"/tmp/bd-display-test-X{candidate}.claim")
            try:
                fd = os.open(
                    claim_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                    0o600,
                )
            except FileExistsError:
                continue
            os.write(fd, b"row752-exhaustion-fixture\n")
            os.fsync(fd)
            held.append((fd, claim_path))
            numbers.append(candidate)
            if len(numbers) == _EXHAUSTION_CANDIDATE_COUNT:
                break
        assert len(held) == _EXHAUSTION_CANDIDATE_COUNT, (
            "precondition: the fixture occupied "
            f"{len(held)} of {_EXHAUSTION_CANDIDATE_COUNT} private claim "
            f"candidates in {_PRIVATE_WINDOW}"
        )
        for _, claim_path in held:
            assert claim_path.is_file(), (
                f"precondition: the fixture built no claim file at {claim_path}"
            )
        yield tuple(numbers)
    finally:
        for fd, claim_path in held:
            os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                claim_path.unlink()


@_XVFB_REQUIRED
def test_the_cited_gate_survives_an_exhausted_global_claim_namespace(
    tmp_path: Path,
) -> None:
    """The contended resource is named, and it must not decide the verdict."""
    with _an_exhausted_claim_namespace() as candidates:
        assert len(candidates) == _EXHAUSTION_CANDIDATE_COUNT
        workspace = tmp_path / "cited"
        workspace.mkdir()
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(display_test, "_DISPLAY_CANDIDATES", candidates)
            try:
                row300.test_cited_cleanup_leaves_a_foreign_display_alive(
                    patch, workspace
                )
            except AssertionError as exc:
                raise AssertionError(
                    "the cited display gate does not own its claim: a contended "
                    f"{len(candidates)}-candidate global "
                    "/tmp/bd-display-test-X<n>.claim namespace decided its "
                    f"outcome: {exc}"
                ) from exc


@_XVFB_REQUIRED
def test_the_exhaustion_fixture_really_refuses_and_then_really_yields() -> None:
    """Negative control with its positive control.

    The fixture must make ``_claim_unused_display`` refuse FOR THE INTENDED
    REASON -- every candidate already claimed -- and the same call on the same
    window must succeed the moment one candidate is released, so a green
    verdict above cannot come from a probe that can only say no.
    """
    with _an_exhausted_claim_namespace() as candidates:
        released = Path(f"/tmp/bd-display-test-X{candidates[0]}.claim")
        refusal = ""
        refused: object = None
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(display_test, "_DISPLAY_CANDIDATES", candidates)
            try:
                refused = display_test._claim_unused_display()
            except AssertionError as exc:
                refusal = str(exc)
            assert refused is None, (
                "a claim was acquired although every candidate was held"
            )
            assert refusal == (
                f"UNKNOWN: none of {_EXHAUSTION_CANDIDATE_COUNT} atomically "
                "claimed display candidates was free"
            ), refusal

            # Positive control: release exactly one candidate and claim again.
            released.unlink()
            granted = display_test._claim_unused_display()
            try:
                assert granted.number in candidates, granted.number
                assert granted.path == released, granted.path
            finally:
                display_test._release_display_claim(granted)


def test_the_cited_precondition_names_the_failure_it_swallowed(
    tmp_path: Path,
) -> None:
    """A future occurrence must diagnose itself instead of reporting only a count.

    Driven through a second pre-helper failure -- Xvfb absent from the helper's
    own lookup -- so the assertion is about the REPORT, not about the claim.
    """
    workspace = tmp_path / "cited"
    workspace.mkdir()
    observed = ""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(display_test, "shutil", _NoXvfbOnPath)
        try:
            row300.test_cited_cleanup_leaves_a_foreign_display_alive(
                patch, workspace
            )
        except AssertionError as exc:
            observed = str(exc)
    assert "precondition: expected one foreign display, observed 0" in observed, (
        "the fixture did not reach the cited precondition at all: "
        f"{observed!r}"
    )
    assert "precondition: Xvfb is required" in observed, (
        "the cited precondition discards the failure that stopped the helper "
        "before it fired, so a contended-resource refusal is reported only as "
        f"a count: {observed!r}"
    )
