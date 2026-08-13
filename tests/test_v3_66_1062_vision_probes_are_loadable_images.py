"""A vision probe must be an image the backend will actually load.

@1062. Three places in this tree send a hardcoded PNG to a vision model, and
all three sent a 1x1 pixel. ollama 0.32.9 REJECTS a 1x1 with HTTP 400
"Failed to load image or audio file"; 0.32.4 accepted it. So the probes worked
until the backend got stricter, and then reported the vision model broken while
it was fine.

MEASURED on test5 (ollama 0.32.9, qwen2.5vl:7b), one variable -- image size --
with the host, model and request shape held fixed:

    1x1   -> HTTP 400 Failed to load image or audio file
    2x2   -> 200, valid completion
    8x8   -> 200
    32x32 -> 200

and the same 1x1 against test4 (ollama 0.32.4) -> 200. So this is not a BD
defect in the vision path; it is a degenerate payload that a stricter backend
stopped accepting.

WHY IT MATTERS BEYOND THE LIVE CHECK. One of the three sites is PRODUCT code:
`llm_readiness.py` probes capability="vision" with it, so BD's own readiness
report tells an operator the vision model is broken when it works. The live
check L18 is the visible symptom; the readiness probe is the consequence.

WHAT THE OLD TEST COULD NOT SEE. `test_u39_vision_live_test.test_embedded_png_
is_valid` asserts PNG magic and len > 0 -- it certifies the payload is
WELL-FORMED and never asks whether it is USABLE. A 1x1 passes both. The
denominator excluded the property that mattered, which is section 0's subject.

THE THRESHOLD. The measured boundary sits between 1 and 2, but pinning the rule
at 2 leaves no margin for a backend that tightens again. _MIN_DIM is 8: still a
trivial payload (a few hundred bytes) and comfortably clear of the edge.
"""

import base64
import pathlib
import re
import struct
import subprocess

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent

# Its PNG-literal census enumerates `git ls-files -- '*.py'`, so a probe added
# anywhere in the tree is its subject.
BD_GATE_SCOPE = "repo-wide"

# Measured: 1 fails on ollama 0.32.9, 2 passes. 8 is margin, not superstition.
_MIN_DIM = 8

_B64_START = "iVBORw0KGgo"


_SELF = pathlib.Path(__file__).resolve().relative_to(_REPO).as_posix()


def _excluded(rel):
    """Which files the scan skips -- a NAMED predicate so it can be tested.

    Exactly two exclusions, and both are narrow on purpose:

      * vendored third-party code, which this repo does not author;
      * THIS FILE, which carries a 1x1 literal DELIBERATELY as synthetic input
        proving the scanner can see one. Without the exemption the gate fails
        on its own fixture -- it did, on first run. Section 0's
        comments-are-inside-the-denominator trap, in data form.

    It is a function rather than an inline `if` because a mutant widening it to
    `rel.startswith("tests/")` ESCAPED the battery: no probe lives under tests/
    today, so nothing could notice the denominator shrinking. An exemption that
    cannot be tested is a blind spot waiting to be widened by someone who does
    not know why it is there.
    """
    return "/vendor/" in rel or rel == _SELF


def _scan_lines(lines):
    """The line-scan, factored out so it can be tested on SYNTHETIC input.

    It has to be: the two defects fixed here -- a double-quote-only regex and a
    per-file `break` -- are both invisible against the real tree, because no
    single-quoted probe and no second-literal-in-one-file exist in it today. A
    fix whose failing branch nothing can reach is not verified, it is asserted.
    """
    out = []
    for i, line in enumerate(lines):
        if _B64_START not in line:
            continue
        buf = []
        for nxt in lines[i:i + 8]:
            # BOTH QUOTE STYLES. The first version matched double quotes only,
            # so a probe written with single quotes was invisible and every
            # assertion below would have reported the tree clean -- section 0's
            # exact shape, inside the gate written to stop it.
            buf += re.findall(r'"([A-Za-z0-9+/=]*)"', nxt)
            buf += re.findall(r"'([A-Za-z0-9+/=]*)'", nxt)
            if nxt.rstrip().endswith(")") and buf:
                break
        try:
            data = base64.b64decode("".join(buf))
            if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
                continue
            w, h = struct.unpack(">II", data[16:24])
        except Exception:
            continue              # not decodable here; not a probe payload
        out.append((i + 1, len(data), w, h))
        # NO `break`. The first version stopped at the FIRST literal per file,
        # so a second probe appended below a good one was outside the
        # denominator -- and live_tests/checks.py, 1900+ lines with its probe
        # near the end, is exactly where the next one gets appended.
    return out


def _png_literals():
    """Every embedded PNG literal in tracked python, with its dimensions.

    Repo-wide rather than a hardcoded list of the three known sites: a fourth
    probe added later must be caught too, and a check that enumerates only what
    it already knows about cannot see the thing it is for.

    DECLARED BLIND SPOT (CLAUDE.md section 1 -- an instrument states what it
    cannot see, in its own output): this enumerates `git ls-files -- '*.py'`,
    which does NOT reach the tracked extensionless python-shebang bd-* scripts
    under toolchain/bin, nor python embedded in shell heredocs. A probe added
    there is outside this denominator. Measured at v3.66.1062: no probe
    currently lives outside *.py.
    """
    files = subprocess.run(["git", "ls-files", "--", "*.py"],
                           capture_output=True, text=True,
                           cwd=_REPO, timeout=120).stdout.split()
    out = []
    for rel in files:
        if _excluded(rel):
            continue
        try:
            lines = (_REPO / rel).read_text(encoding="utf-8",
                                            errors="replace").splitlines()
        except OSError:
            continue
        for ln, nb, w, h in _scan_lines(lines):
            out.append((rel, ln, nb, w, h))
    return out


@pytest.fixture(scope="module")
def literals():
    found = _png_literals()
    # NON-EMPTY DENOMINATOR. A scanner that matched nothing would make every
    # assertion below vacuously true and report the tree clean -- the exact
    # failure this file exists to prevent, one level up.
    assert found, (
        "BD-GATE-UNRUNNABLE: no embedded PNG literal found in any tracked "
        "python file. The scanner is broken, not the tree."
    )
    return found


def test_the_scanner_sees_the_known_probe_sites(literals):
    """Name the sites it must reach, so a regex that silently stops matching
    is a failure rather than a clean report."""
    seen = {rel for rel, _, _, _, _ in literals}
    for required in ("bulk_downloader/llm_readiness.py",
                     "bulk_downloader/dev_suite/integrations_diag.py",
                     "live_tests/checks.py"):
        assert required in seen, (
            f"the scanner did not reach {required}, which is known to embed a "
            f"vision probe -- its denominator has stopped containing its subject"
        )


def test_no_embedded_png_is_a_single_pixel(literals):
    """THE DEFECT, stated as the rule that would have caught it."""
    tiny = [(rel, ln, w, h) for rel, ln, _, w, h in literals if w < 2 or h < 2]
    assert not tiny, (
        "single-pixel PNG payload(s) still embedded: "
        + "; ".join(f"{rel}:{ln} ({w}x{h})" for rel, ln, w, h in tiny)
        + ". ollama 0.32.9 rejects a 1x1 with HTTP 400 'Failed to load image "
          "or audio file' while 0.32.4 accepted it, so this reports the vision "
          "model broken when it works."
    )


def test_vision_probes_clear_the_size_floor(literals):
    """Margin, not just the measured edge."""
    probes = [(rel, ln, w, h) for rel, ln, _, w, h in literals
              if rel in ("bulk_downloader/llm_readiness.py",
                         "bulk_downloader/dev_suite/integrations_diag.py",
                         "live_tests/checks.py")]
    assert probes, "BD-GATE-UNRUNNABLE: no vision probe payloads located"
    small = [(rel, ln, w, h) for rel, ln, w, h in probes
             if w < _MIN_DIM or h < _MIN_DIM]
    assert not small, (
        f"vision probe(s) below the {_MIN_DIM}x{_MIN_DIM} floor: "
        + "; ".join(f"{rel}:{ln} ({w}x{h})" for rel, ln, w, h in small)
        + f". The measured boundary is between 1 and 2 on ollama 0.32.9; the "
          f"floor carries margin so the next tightening does not reproduce this."
    )


def test_the_floor_itself_sits_above_the_measured_failure():
    """The constant encodes a measurement, so assert the measurement.

    THIS TEST EXISTS BECAUSE A MUTANT ESCAPED. Setting _MIN_DIM to 1 broke
    nothing observable -- the payloads are 16x16, so the floor was not
    load-bearing at that moment and every other assertion stayed green. A floor
    that can be quietly lowered to the value known to FAIL is not a floor; it is
    a number waiting to be edited by someone who does not know why it is there.
    """
    assert _MIN_DIM >= 8, (
        f"_MIN_DIM is {_MIN_DIM}. The docstring argues for MARGIN above the "
        f"measured boundary (1 fails, 2 passes on ollama 0.32.9) and 8x8 was "
        f"measured green -- so the floor is 8. Asserting only >= 2 let a mutant "
        f"lower it to any of 2..7 while the claim of margin went unenforced: "
        f"the argument and the check diverged."
    )


def test_the_payloads_are_still_small(literals):
    """The over-sensitivity control, and it is not decorative.

    A fix for "too small" that reaches for a realistic screenshot would put
    kilobytes of base64 into three source files and add real latency to a probe
    whose whole point is to be cheap. Bound both directions.
    """
    probes = [(rel, ln, nb) for rel, ln, nb, _, _ in literals
              if rel in ("bulk_downloader/llm_readiness.py",
                         "bulk_downloader/dev_suite/integrations_diag.py",
                         "live_tests/checks.py")]
    fat = [(rel, ln, nb) for rel, ln, nb in probes if nb > 20_000]
    assert not fat, (
        "vision probe payload(s) over 20KB: "
        + "; ".join(f"{rel}:{ln} ({nb}B)" for rel, ln, nb in fat)
        + ". A probe is meant to be cheap; embed a small synthetic image, not "
          "a real screenshot."
    )


# ── the scanner's own failing branches, on synthetic input ──────────────
#
# Both of these were REAL defects found in adversarial review, and neither can
# fail against the real tree: there is no single-quoted probe in it, and no
# file holds two literals. Without synthetic input the fixes are unverified.

_SIXTEEN = ("iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAHUlEQVR42m"
            "OQwwEe4gAMoxpoogGXBC6DRjXQRAMA2Vd+kIR10J8AAAAASUVORK5CYII=")
_ONE_PX = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQ"
           "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def test_the_scanner_reads_single_quoted_literals():
    """A probe written with ' instead of " must not be invisible."""
    half = len(_ONE_PX) // 2
    lines = ["PROBE = ('%s'" % _ONE_PX[:half], "         '%s')" % _ONE_PX[half:]]
    found = _scan_lines(lines)
    assert found, (
        "the scanner found nothing in a single-quoted probe -- it would report "
        "a tree carrying one as clean"
    )
    assert found[0][2] == 1 and found[0][3] == 1, found


def test_the_scanner_does_not_stop_at_the_first_literal():
    """A second probe appended below a good one must still be seen."""
    def block(name, b64):
        half = len(b64) // 2
        return ['%s = ("%s"' % (name, b64[:half]), '    "%s")' % b64[half:]]

    lines = block("GOOD", _SIXTEEN) + ["", "# something else", ""] + \
        block("SNEAKY", _ONE_PX)
    found = _scan_lines(lines)
    assert len(found) == 2, (
        f"scanner returned {len(found)} literal(s), expected 2 -- it stops at "
        f"the first, so anything appended below an existing probe is outside "
        f"its denominator: {found}"
    )
    assert any(w == 1 and h == 1 for _, _, w, h in found), (
        f"the appended 1x1 was not among the results: {found}"
    )


def test_the_exemption_is_exactly_this_file():
    """THIS TEST EXISTS BECAUSE A MUTANT ESCAPED.

    Widening the self-exclusion to `tests/` changed nothing observable -- no
    probe lives under tests/ today -- so the gate's denominator could shrink
    silently. Assert the predicate directly.
    """
    assert _excluded(_SELF), "the gate must skip its own synthetic fixtures"
    assert _excluded("bulk_downloader/vendor/snapdom/snapdom.js")
    for must_scan in ("tests/test_some_other_file.py",
                      "tests/test_v3_66_1031_socket_recorder_stages.py",
                      "bulk_downloader/llm_readiness.py",
                      "live_tests/checks.py"):
        assert not _excluded(must_scan), (
            f"{must_scan} is excluded from the scan -- the exemption has been "
            f"widened beyond this file and the denominator no longer contains "
            f"the places a probe can be added"
        )
