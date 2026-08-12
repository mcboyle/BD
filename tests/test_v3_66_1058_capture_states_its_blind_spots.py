"""capture.sh must say what its verdict does NOT cover.

@1058, backlog row 54. `capture.sh` is the box gate, and its last line is the
only line anybody reads -- so a PASS that names nothing it could not look at is
the gate-reports-OK-while-blind shape CLAUDE.md section 0 exists to fight. The
socket recorder is the model: it prints its own blind spots on every run rather
than burying them in a README.

THE MEASURED BLIND SPOT. capture.sh runs two lanes -- a parallel lane
(`-m capture_parallel -n N --dist loadfile`) and a serial lane (`-n 0`). Neither
reproduces the co-batching of a whole-suite `pytest tests/` run, so a test that
wipes `bulk_downloader.*` from `sys.modules` and orphans a later module's
import-time binding cannot fire. At v3.66.1034 test6 passed capture at
15547 pass / 0 fail while the tree carried all 14 known leakers, and a plain
`pytest tests/` on the same commit failed between 5 and 35.

WHY THE ASSERTION READS COMMENT-STRIPPED CODE. The deliverable here is OUTPUT,
not a note. A bare grep over the file would be satisfied by the comment written
to explain the feature -- the exact shape `shell_source` was built for after it
escaped a mutant twice. `shell_code_only` fixes that denominator.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

from shell_source import shell_code_only

_REPO = Path(__file__).resolve().parent.parent
_CAPTURE = _REPO / "capture.sh"

# The distinctive words, not a generic marker. CLAUDE.md section 10: when every
# refusal shares a token, assert the REASON. A test that only looked for the
# string "blind" would pass on a block that named the wrong blind spot.
_MUST_NAME = (
    "loadfile",        # the lane that cannot co-batch
    "sys.modules",     # the mechanism that leaks
    "15547",           # the measurement, so the claim is checkable
)


@pytest.fixture(scope="module")
def code() -> str:
    return shell_code_only(_CAPTURE)


def test_the_instrument_is_not_empty(code):
    """Denominator first: a stripped file that came back empty would pass
    every 'not in' assertion below and fail every 'in' one for the wrong
    reason."""
    assert _CAPTURE.exists(), "BD-GATE-UNRUNNABLE: capture.sh is missing"
    raw = _CAPTURE.read_text(encoding="utf-8")
    assert len(code) > 10_000, f"stripped code is only {len(code)} chars"
    assert len(code) < len(raw), (
        "shell_code_only removed nothing -- either capture.sh has no comments "
        "(it has many) or the helper is not stripping, and every assertion "
        "below would then be readable from prose"
    )


def test_the_stripper_actually_removes_a_comment(code):
    """The over-sensitivity control, in the direction that matters.

    Without this, a helper that silently returned the raw text would make the
    whole file green while proving nothing about CODE.
    """
    raw = _CAPTURE.read_text(encoding="utf-8")
    comment_lines = [
        l for l in raw.splitlines()
        if l.lstrip().startswith("#") and len(l.strip()) > 40
    ]
    assert comment_lines, "BD-GATE-UNRUNNABLE: no comment to test the stripper"
    survivors = [l for l in comment_lines if l in code.splitlines()]
    assert not survivors, f"{len(survivors)} whole-line comment(s) survived"


def test_capture_emits_a_blind_spot_block(code):
    """It must be EXECUTABLE output, not a comment."""
    assert "[blind spots]" in code, (
        "capture.sh emits no blind-spot block in its executable text. A verdict "
        "that does not say what it could not look at reports OK truthfully and "
        "uselessly."
    )


def _block(code: str) -> str:
    """The blind-spot region only.

    SCOPED DELIBERATELY. The first version of this test asserted each needle
    against the WHOLE file, and `loadfile` passed before the feature existed --
    `--dist loadfile` already appears in the lane invocation 700 lines above.
    A needle that cannot fail is not a test, so the denominator is narrowed to
    the block under test.
    """
    i = code.find("[blind spots]")
    assert i != -1, "no blind-spot block to scope to"
    return code[i:i + 2500]


@pytest.mark.parametrize("needle", _MUST_NAME)
def test_the_block_names_the_measured_cross_file_blind_spot(code, needle):
    assert needle in _block(code), (
        f"the blind-spot block does not name {needle!r}. State the mechanism "
        f"and the measurement, not a vague caveat -- an unspecific warning is "
        f"the one a reader skips."
    )


def test_the_needles_are_scoped_and_could_fail(code):
    """The control for the scoping above: prove the region is a strict subset.

    If `_block` ever returned the whole file, every needle assertion would go
    trivially green again and nothing would say so.
    """
    assert len(_block(code)) < len(code) / 2, (
        "the blind-spot region is not a strict subset of the file -- the "
        "needle assertions are back to being satisfiable from anywhere"
    )


def test_the_block_is_emitted_after_the_verdict(code):
    """Adjacency is the whole point: it must sit where the verdict is read."""
    verdict = code.find("10_VERDICT.txt")
    blind = code.find("[blind spots]")
    assert verdict != -1, "BD-GATE-UNRUNNABLE: verdict artifact not found"
    assert blind != -1, "no blind-spot block to position"
    assert blind > verdict, (
        "the blind-spot block is emitted BEFORE the verdict, so the last thing "
        "printed is still an unqualified PASS"
    )


def _extract_block(raw: str) -> str:
    """The emitting construct, cut on STRUCTURE rather than a fixed width.

    CLAUDE.md section 2a: a harness that slices a shell construct by line count
    swallows its closing delimiter and produces a bash syntax error presenting
    as a subject failure. Cut from the assignment to its own closing line.
    """
    lines = raw.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith('BLIND_SPOTS="$OUT'))
    end = next(i for i in range(start, len(lines))
               if lines[i].startswith('} | tee "$BLIND_SPOTS"'))
    return "\n".join(lines[start:end + 1]) + "\n"


def test_the_block_actually_prints_when_executed(tmp_path):
    """RUN IT, do not read it.

    THIS TEST EXISTS BECAUSE A MUTANT ESCAPED. Every other assertion in this
    file reads capture.sh's TEXT, so a mutant that replaced one `echo` with
    `: # <the same words as a comment>` left all the needles present in the
    source and broke the output -- band stayed green, behaviour gone. Source
    text is not output; the only way to tell them apart is to execute.
    """
    raw = _CAPTURE.read_text(encoding="utf-8")
    block = _extract_block(raw)

    # Precondition before verdict (section 6): assert the harness built the
    # shape. A block that failed to extract would make every assertion below
    # test an empty string.
    assert block.count("echo") >= 8, f"extracted only {block.count('echo')} echoes"
    syntax = subprocess.run(["bash", "-n"], input=block, text=True,
                            capture_output=True)
    assert syntax.returncode == 0, f"extract does not parse: {syntax.stderr}"

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    r = subprocess.run(["bash", "-s"], input=block, text=True,
                       capture_output=True, timeout=60,
                       env={**os.environ, "OUT": str(out_dir)})
    assert r.returncode == 0, f"block exited {r.returncode}: {r.stderr[:400]}"

    for needle in ("[blind spots]", "CROSS-FILE STATE LEAKS", "sys.modules",
                   "15547", "TIMEZONE-DEPENDENT"):
        assert needle in r.stdout, (
            f"{needle!r} is in capture.sh's source but never reaches its "
            f"OUTPUT -- the line is present but not executed"
        )

    artifact = out_dir / "11_BLIND_SPOTS.txt"
    assert artifact.exists(), "no artifact written; the bundle carries nothing"
    assert artifact.read_text() == r.stdout, (
        "the archived text differs from what the operator saw on screen"
    )


def test_the_block_lands_in_the_evidence_bundle(code):
    """Printed and archived. A caveat that exists only in a terminal that has
    since scrolled away is not evidence anybody can re-read."""
    m = re.search(r'BLIND(?:_SPOTS)?="?\$OUT/([^"\s]+)', code)
    assert m, (
        "the blind-spot text is not written under $OUT, so it is absent from "
        "the tarball the operator uploads -- the artifact that outlives the run"
    )
    assert m.group(1).endswith(".txt"), m.group(1)
    # DECLARING THE PATH IS NOT WRITING TO IT. The first version of this
    # assertion stopped at the line above, and a mutant replacing
    # `| tee "$BLIND_SPOTS"` with `> /dev/null` escaped it -- the variable was
    # still assigned, so the check still passed while the artifact was never
    # created. Assert the redirection, not the declaration.
    assert re.search(r'(\|\s*tee\s+"\$BLIND_SPOTS"|>\s*"\$BLIND_SPOTS")', code), (
        "$BLIND_SPOTS is assigned but nothing writes to it -- the block prints "
        "to the terminal and leaves no artifact in the bundle"
    )
