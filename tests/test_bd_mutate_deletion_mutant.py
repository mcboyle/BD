"""H377 -- a DELETION mutant (``"new": ""``) is expressible.

The canonical first mutant for any new guard is "delete the guard".  Written the
obvious way -- the guard lines as ``old``, ``""`` as ``new`` -- bd-mutate used to
refuse the whole spec before running anything, so mutants 2..n of the same
battery never ran either (tool-defect filing
bd-persist/tool-defects/bd-mutate-cannot-express-deletion-20260914.md).

The apply path was never the problem: ``_apply`` asserts the anchor is unique,
that the replacement changed something, and that
``len(after) == len(src) - len(old) + len(new)``, all of which are exact for
``len(new) == 0``.  Only the up-front field validator forbade it.  These tests
pin the relaxation to exactly that: an empty ``new`` runs and deletes exactly
the named span, an ABSENT ``new`` still refuses, and the anchor-uniqueness
refusals are untouched.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_BAND = "tests/test_m.py"
_CATCHER = f"{_BAND}::test_guarded"

# A multi-line span, because a one-line deletion is the easy case and the
# filing's example was two guard lines.
_SPAN = "    if VALUE == 1:\n        return 'guarded'\n"
_SUBJECT = (
    "VALUE = 1\n"
    "def guarded():\n"
    + _SPAN
    + "    return 'open'\n"
)


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "m.py").write_text(_SUBJECT, encoding="utf-8")
    # The catcher records the sha256 of the subject AS THE RUN SEES IT before
    # asserting.  bd-mutate restores the file when the mutant is done, so this
    # sidecar is the only way to observe the mutated bytes from outside.
    (tmp_path / _BAND).write_text(
        "import hashlib\n"
        "import importlib\n"
        "import pathlib\n"
        "import m\n"
        "def test_guarded():\n"
        "    work = pathlib.Path(__file__).resolve().parent.parent\n"
        "    digest = hashlib.sha256((work / 'm.py').read_bytes()).hexdigest()\n"
        "    (work / 'observed.sha256').write_text(digest, encoding='utf-8')\n"
        "    assert importlib.reload(m).guarded() == 'guarded'\n",
        encoding="utf-8",
    )
    return tmp_path


def _run(work: Path, mutants: list[dict], *extra: str) -> subprocess.CompletedProcess[str]:
    spec = work / "spec.json"
    spec.write_text(
        json.dumps({
            "schema": "bd-mutate-spec/1",
            "subject": "a deletion mutant is expressible",
            "band": [_BAND],
            "mutants": mutants,
        }),
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(_TOOL), "--spec", str(spec), "--work", str(work), "--json", *extra],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _payload(run: subprocess.CompletedProcess[str]) -> dict:
    start = run.stdout.find("{")
    assert start >= 0, run.stdout + run.stderr
    return json.loads(run.stdout[start:])


def _deletion(*, old: str = _SPAN, new: object = "", drop_new: bool = False) -> dict:
    mutant = {
        "label": "delete the guard",
        "file": "m.py",
        "old": old,
        "direction": "regression",
        "catcher": _CATCHER,
    }
    if not drop_new:
        mutant["new"] = new
    return mutant


def test_a_deletion_mutant_runs_and_removes_exactly_the_named_span(tmp_path):
    """Acceptance bullet 1: it RUNS, and the mutated file is the by-hand deletion."""
    work = _tree(tmp_path)
    run = _run(work, [_deletion()])
    assert run.returncode == 0, run.stdout + run.stderr
    row = _payload(run)["rows"][0]
    assert row["verdict"] == "CAUGHT", row

    by_hand = _SUBJECT.replace(_SPAN, "", 1)
    assert by_hand != _SUBJECT, "the fixture span does not occur in the fixture subject"
    expected = hashlib.sha256(by_hand.encode("utf-8")).hexdigest()
    observed = (work / "observed.sha256").read_text(encoding="utf-8").strip()
    assert observed == expected, (
        f"mutated bytes {observed} are not the by-hand deletion {expected}"
    )
    # ... and the run put the original back.
    assert (work / "m.py").read_text(encoding="utf-8") == _SUBJECT


def test_a_deletion_mutant_whose_anchor_is_absent_still_refuses(tmp_path):
    """Acceptance bullet 2, half one: the empty `new` does not excuse a bad anchor."""
    work = _tree(tmp_path)
    run = _run(work, [_deletion(old="    if VALUE == 99:\n        return 'nowhere'\n")])
    assert run.returncode == 2, run.stdout + run.stderr
    row = _payload(run)["rows"][0]
    assert row["verdict"] in {"INVALID", "UNKNOWN", "ERROR"}, row
    assert "anchor occurs 0 times" in row["why"], row


def test_a_deletion_mutant_whose_anchor_occurs_twice_still_refuses(tmp_path):
    """Acceptance bullet 2, half two: uniqueness is unchanged."""
    work = _tree(tmp_path)
    (work / "m.py").write_text(_SUBJECT + "\n" + _SPAN, encoding="utf-8")
    run = _run(work, [_deletion()])
    assert run.returncode == 2, run.stdout + run.stderr
    row = _payload(run)["rows"][0]
    assert "anchor occurs 2 times" in row["why"], row


def test_an_absent_new_key_still_refuses_because_absent_is_not_empty(tmp_path):
    """Acceptance bullet 3: the relaxation is 'may be empty', not 'may be missing'."""
    work = _tree(tmp_path)
    run = _run(work, [_deletion(drop_new=True)])
    assert run.returncode == 2, run.stdout + run.stderr
    assert "field 'new' is required" in run.stderr, run.stderr
    assert "baseline GREEN" not in run.stdout, run.stdout


def test_a_non_string_new_still_refuses(tmp_path):
    """The type check survives the relaxation: None is not an empty string."""
    work = _tree(tmp_path)
    run = _run(work, [_deletion(new=None)])
    assert run.returncode == 2, run.stdout + run.stderr
    assert "field 'new' must be a string" in run.stderr, run.stderr


def test_emit_spec_can_publish_a_deletion_mutant(tmp_path):
    """The durable-spec writer must accept what the runner accepts.

    --emit-spec is the documented way to promote a measured scratch battery into
    tests/mutants/.  A relaxation that stopped at the run validator would let a
    seat RUN a deletion and then refuse to let them keep it.
    """
    work = _tree(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "--", "m.py", _BAND], cwd=work, check=True)
    destination = work / "tests" / "mutants" / "v3_66_9999_deletion.json"
    run = _run(
        work,
        [_deletion()],
        "--emit-spec", destination.name,
        "--subject", "a deletion mutant survives emission",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert destination.exists(), run.stdout + run.stderr
    emitted = json.loads(destination.read_text(encoding="utf-8"))
    assert emitted["mutants"][0]["new"] == "", emitted


def test_the_contract_tells_a_spec_author_that_new_may_be_empty():
    """The filing's root cause: the docstring said `new required what to replace
    it with` and nothing more, so an author could not know deletion was
    available (or, before H377, unavailable) without spending a pool slot."""
    contract = _TOOL.read_text(encoding="utf-8")
    line = next(
        (raw for raw in contract.splitlines()
         if raw.strip().startswith("new ") and "required" in raw),
        None,
    )
    assert line is not None, "the contract no longer documents the 'new' field"
    assert "may be EMPTY = delete the span" in line, line
