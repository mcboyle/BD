"""Row 797 -- the three real login seams carry durable mutant pins.

Three call sites decide whether a login actually happens, and until this gate
nothing in the tree held them still:

  * ``_login.do_login`` in ``bulk_downloader/app.py`` -- the session keeper's
    authentication callback;
  * ``do_login`` in ``bulk_downloader/runner_auth.py`` -- the runner's own
    login entry point;
  * ``_login._looks_authenticated(cookies)`` in
    ``bulk_downloader/dev_suite/capture_diag.py`` -- the D-29 cookie
    diagnostic, which is the only place the cookie delta is judged.

Each seam already has a test that would notice if it were bypassed, but a
catcher is only a claim until a mutant proves it.  The mutant specs are the
durable half: they say, in tracked files, exactly which byte range is load
bearing and exactly which test is on the hook for it.  A spec whose anchor has
drifted is the failure this gate is for -- the mutation run would then report
``invalid`` and a reader would see a number that is not an escape and not a
catch, so the seam would be silently unpinned.

WHAT IS ASSERTED HERE, AND WHAT IS NOT.  This gate asserts the pins are
present, singular, resolved into their subjects and pointed at defined tests,
and that the family declares a transform control aimed at a test that does NOT
drive the mutated path.  It deliberately does NOT run bd-mutate: the catch and
the control's escape are measured by the mutation lane
(``test_v3_66_1184_mutation_specs_are_tracked.py`` validates the schema,
``test_h89_bd_mutate_declares_a_control.py`` validates the control verdict),
and re-running four mutants here would buy a slower duplicate of work CI
already does.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_SPEC_DIR = _REPO / "tests" / "mutants"

# The three seams named by row 797. Each entry is (spec basename, subject).
_SEAM_SPECS = {
    "test2b_do_login_keeper_seam.json": "bulk_downloader/app.py",
    "test2b_do_login_runner_seam.json": "bulk_downloader/runner_auth.py",
    "test2b_cookie_delta_diagnostic_seam.json":
        "bulk_downloader/dev_suite/capture_diag.py",
}
_CONTROL_SPEC = "test2b_cookie_delta_transform_control.json"
_CONTROL_SUFFIX = "_transform_control.json"


def _tracked() -> set[str]:
    run = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    )
    return {name for name in run.stdout.split("\0") if name}


def _load(basename: str) -> dict:
    path = _SPEC_DIR / basename
    assert path.is_file(), f"row 797 seam spec is absent: tests/mutants/{basename}"
    return json.loads(path.read_text(encoding="utf-8"))


def _anchor_hits(mutant: dict, source: str) -> int:
    """How many times this mutant's single anchor resolves in its subject."""
    fields = {"old", "old_regex"} & set(mutant)
    assert len(fields) == 1, (
        f"{mutant.get('label')!r}: a mutant carries exactly one of old/old_regex, "
        f"got {sorted(fields)}"
    )
    field = next(iter(fields))
    if field == "old":
        return source.count(mutant["old"])
    return len(re.findall(mutant["old_regex"], source))


def _resolve_exactly_once(spec: str, mutant: dict, source: str) -> None:
    hits = _anchor_hits(mutant, source)
    assert hits == 1, (
        f"{spec}::{mutant['label']}: anchor resolves {hits} time(s) in "
        f"{mutant['file']}, expected exactly 1 -- an unresolved anchor makes "
        f"bd-mutate report invalid, which is neither a catch nor an escape"
    )


def _defined_tests(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
    return names


def _catchers(document: dict) -> list[str]:
    return [mutant["catcher"] for mutant in document["mutants"]]


def test_the_spec_corpus_is_a_nonzero_population_before_any_verdict():
    """No assertion below means anything over an empty or missing corpus."""
    assert _SPEC_DIR.is_dir(), f"no mutant spec directory at {_SPEC_DIR}"
    population = sorted(p.name for p in _SPEC_DIR.glob("*.json"))
    assert population, "zero tracked mutant specs -- UNKNOWN, not a clean corpus"
    required = set(_SEAM_SPECS) | {_CONTROL_SPEC}
    missing = sorted(required - set(population))
    assert not missing, (
        "row 797 seam specs absent from a corpus of "
        f"{len(population)} spec(s): {missing}"
    )


def test_each_login_seam_pins_exactly_one_anchor_in_its_named_subject():
    tracked = _tracked()
    resolved = []
    for basename, subject in _SEAM_SPECS.items():
        document = _load(basename)
        assert f"tests/mutants/{basename}" in tracked, (
            f"tests/mutants/{basename} is untracked; an untracked spec does not "
            f"pin anything for anyone else"
        )
        mutants = document["mutants"]
        assert len(mutants) == 1, (
            f"{basename}: row 797 pins one seam per spec, found {len(mutants)}"
        )
        mutant = mutants[0]
        assert mutant["file"] == subject, (
            f"{basename}: subject is {mutant['file']}, expected {subject}"
        )
        assert mutant["direction"] == "regression", (
            f"{basename}: a seam pin is a regression mutant, got "
            f"{mutant['direction']!r}"
        )
        source = (_REPO / subject).read_text(encoding="utf-8")
        _resolve_exactly_once(basename, mutant, source)
        resolved.append(basename)
    # Exact count: three seams, three resolved pins, no fourth and no silent drop.
    assert len(resolved) == 3, (
        f"row 797 names three login seams; {len(resolved)} resolved: {resolved}"
    )


def test_every_seam_names_a_catcher_that_is_a_defined_test():
    tracked = _tracked()
    for basename in _SEAM_SPECS:
        document = _load(basename)
        for nodeid in _catchers(document):
            rel, _, name = nodeid.partition("::")
            assert rel in tracked and (_REPO / rel).is_file(), (
                f"{basename}: catcher path is absent or untracked: {nodeid}"
            )
            assert name, f"{basename}: catcher names no test: {nodeid}"
            defined = _defined_tests(_REPO / rel)
            assert name.split("::")[0] in defined, (
                f"{basename}: catcher is not a defined test: {nodeid}"
            )


def test_the_family_declares_a_transform_control_aimed_away_from_the_seams():
    """A control that shares a seam's catcher would prove nothing."""
    document = _load(_CONTROL_SPEC)
    assert _CONTROL_SPEC.endswith(_CONTROL_SUFFIX) or (
        document.get("control_spec") is True), (
        f"{_CONTROL_SPEC}: a control declares itself by the "
        f"{_CONTROL_SUFFIX} suffix or control_spec: true"
    )
    mutant = document["mutants"][0]
    source = (_REPO / mutant["file"]).read_text(encoding="utf-8")
    _resolve_exactly_once(_CONTROL_SPEC, mutant, source)
    seam_catchers = set()
    for basename in _SEAM_SPECS:
        seam_catchers.update(_catchers(_load(basename)))
    assert seam_catchers, "fixture built no seam catchers to compare against"
    overlap = sorted(set(_catchers(document)) & seam_catchers)
    assert not overlap, (
        f"{_CONTROL_SPEC}: the control names a seam catcher {overlap}; a "
        f"control must be aimed at a test that does not drive the mutated path, "
        f"or its escape measures nothing"
    )


def test_the_anchor_reader_reports_a_drifted_anchor_rather_than_passing():
    """Negative control: the intended failure is 'resolves 0 time(s)'."""
    drifted = {"label": "negative control", "file": "bulk_downloader/app.py",
               "old": "_login.do_login_THIS_TEXT_IS_NOT_IN_THE_TREE"}
    source = (_REPO / "bulk_downloader" / "app.py").read_text(encoding="utf-8")
    assert _anchor_hits({"label": "positive control", "file": "x",
                         "old": "_login.do_login"}, source) == 1, (
        "positive control: the reader cannot see the real seam, so a zero below "
        "would prove nothing"
    )
    with pytest.raises(AssertionError) as caught:
        _resolve_exactly_once("synthetic", drifted, source)
    assert "resolves 0 time(s)" in str(caught.value), str(caught.value)
