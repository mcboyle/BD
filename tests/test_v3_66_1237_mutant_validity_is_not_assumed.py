"""bd-mutate must not record a mutant it cannot parse as VALID.

THE DEFECT (backlog row 239), and it was a fail-open in the instrument that
validates every other cut. `_validate()` ran `ast.parse` for `.py` and `bash -n`
for `.sh`, then fell through to an unconditional `return True` for everything
else. A `.tsx` mutant that did not parse was therefore recorded VALID; its
catcher then failed -- because the whole spec file could no longer be
transformed, not because any assertion fired -- and `_grade_mutant` scored it
CAUGHT on `named catcher failed`. The battery reported proof it did not have.

The comment at that fall-through was honest: "unknown type: not our place to
guess". Declining to guess is right. Recording the refusal to judge AS a passing
judgement is the bug.

MEASURED at v3.66.1237: esbuild 0.25.12 already ships in frontend/node_modules
for vitest, and reading from STDIN with an explicit --loader accepts a valid
.tsx and refuses a broken one. Passing a FILE with --loader is refused outright
("loader without extension only applies when reading from stdin"), which is why
the validator pipes.
"""
from __future__ import annotations

import collections
import importlib.machinery
import json
import pathlib
import subprocess

import pytest

BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "toolchain" / "bin" / "bd-mutate"
ESBUILD = REPO / "frontend" / "node_modules" / ".bin" / "esbuild"

mutate = importlib.machinery.SourceFileLoader(
    "bd_mutate_1237", str(TOOL)).load_module()

_GOOD_TSX = "export const f = (x: string): string => `${x}!`;\n"
_BROKEN_TSX = "export const f = (x: string): string => `${x}\n"


def _write_battery_fixture(tmp_path, subject_name, subject_text, test_text):
    subject = tmp_path / subject_name
    subject.write_text(subject_text, encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    test_path = tests / "test_subject.py"
    test_path.write_text(test_text, encoding="utf-8")
    assert subject.is_file(), "precondition: mutation subject was not created"
    assert test_path.is_file(), "precondition: named catcher was not created"
    return subject, "tests/test_subject.py::test_subject_value"


def _run_counted_battery(tmp_path, monkeypatch, mutant, nodeid):
    grade_calls = []
    original_grade = mutate._grade_mutant

    def counted_grade(candidate, result):
        grade_calls.append(candidate["label"])
        return original_grade(candidate, result)

    monkeypatch.setattr(mutate, "_grade_mutant", counted_grade)
    rc, rows = mutate.run_battery(
        [mutant], [nodeid], tmp_path, verbose=False, timeout=30)
    assert len(rows) == 1, (
        "precondition: one supplied mutant must produce exactly one result row")
    return rc, rows[0], grade_calls


def test_the_parser_this_gate_depends_on_is_present_and_discriminates():
    """PRECONDITION. Without it every assertion below would pass vacuously."""
    assert ESBUILD.is_file(), (
        "no esbuild at %s -- run `npm ci` in frontend/. This gate cannot judge "
        "the validator without the parser the validator uses." % ESBUILD)
    good = subprocess.run([str(ESBUILD), "--loader=tsx", "--log-level=silent"],
                          input=_GOOD_TSX, capture_output=True, text=True,
                          timeout=60)
    bad = subprocess.run([str(ESBUILD), "--loader=tsx", "--log-level=silent"],
                         input=_BROKEN_TSX, capture_output=True, text=True,
                         timeout=60)
    assert good.returncode == 0, good.stderr[-300:]
    assert bad.returncode != 0, (
        "the fixture the rest of this file calls BROKEN parses cleanly, so "
        "every refusal asserted below would be about the wrong thing")


def test_an_unparseable_tsx_mutant_is_refused_rather_than_called_valid(tmp_path):
    """THE RED CASE. Before v3.66.1237 this returned (True, ''), and a mutant
    that only broke the transform went on to score CAUGHT."""
    path = tmp_path / "Thing.tsx"
    path.write_text(_GOOD_TSX, encoding="utf-8")
    ok, why = mutate._validate(path, _BROKEN_TSX)
    assert ok is False, (
        "bd-mutate recorded an unparseable .tsx mutant as %r. A mutant that "
        "cannot be transformed proves nothing about the behaviour its catcher "
        "names: the catcher fails because the FILE is gone, and that reads as "
        "CAUGHT." % (ok,))
    assert "esbuild" in why, why


def test_a_valid_tsx_mutant_is_still_accepted(tmp_path):
    """OVER-SENSITIVITY CONTROL. A validator that refused every .tsx would
    satisfy the test above and make every frontend battery unrunnable."""
    path = tmp_path / "Thing.tsx"
    path.write_text(_GOOD_TSX, encoding="utf-8")
    ok, why = mutate._validate(path, _GOOD_TSX.replace("!", "?"))
    assert ok is True, (ok, why)
    assert why == "", why


@pytest.mark.parametrize("name", ["a.ts", "b.jsx", "c.mts", "d.cjs"])
def test_the_whole_typescript_family_is_judged_not_just_tsx(tmp_path, name):
    """The population, not one extension. `.tsx` was the one that bit us; a
    validator that special-cased it would leave the same hole beside it."""
    path = tmp_path / name
    path.write_text(_GOOD_TSX, encoding="utf-8")
    ok, _ = mutate._validate(path, _BROKEN_TSX)
    assert ok is False, "%s was not parse-checked" % name


def test_a_missing_parser_is_UNKNOWN_rather_than_valid(tmp_path, monkeypatch):
    """The refusal that could never fire, driven.

    esbuild IS installed on this host, so nothing exercised the branch that
    handles its absence -- and a mutation battery said so: removing that branch
    entirely ESCAPED at v3.66.1237. An untested refusal reads as working
    precisely because the condition it guards never occurs here.

    A provisioning gap must not be reported as a clean bill of health. If the
    parser is missing, the mutant's validity is UNKNOWN, and UNKNOWN is not
    valid.
    """
    monkeypatch.setattr(mutate, "_INVOKED_REPOSITORY", tmp_path / "nowhere")
    path = tmp_path / "Thing.tsx"
    path.write_text(_GOOD_TSX, encoding="utf-8")
    for parent in list(path.resolve().parents):
        assert not (parent / "frontend" / "node_modules" / ".bin" / "esbuild").is_file(), (
            "the fixture sits under a tree that HAS esbuild (%s), so this test "
            "would measure the happy path" % parent)

    ok, why = mutate._validate(path, _BROKEN_TSX)
    assert ok is None, (
        "with no parser available bd-mutate answered %r. Declining to judge "
        "must not be recorded as passing judgement -- that is row 239 itself." % (ok,))
    assert "UNKNOWN" in why and "npm ci" in why, why


def test_an_unjudgeable_type_is_UNKNOWN_and_not_valid(tmp_path):
    """The third state, and the actual lesson.

    A type this tool cannot parse must not be recorded as valid: the honest
    answer is neither True nor False, and the caller turns that into an UNKNOWN
    verdict rather than running the mutant and believing whatever came back.

    THE SUBJECT MOVED, AND THAT IS THE POINT. This example used to be `.md`,
    which was unjudgeable only because nothing had registered it -- while the
    tracked corpus was mutating 17 `.md` mutants and grading every one. `.md`
    is now a REGISTERED text family (see the test below); the unregistered
    population it used to stand for is what this asserts, so the assertion is
    about the DEFAULT rather than about one suffix that happened to be missing.
    """
    path = tmp_path / "notes.row711unregistered"
    path.write_text("hello\n", encoding="utf-8")
    assert path.suffix not in _corpus_suffix_representatives()[2], (
        "precondition: this example suffix is in the tracked corpus, so it "
        "ought to be registered and cannot stand for the unregistered default")
    ok, why = mutate._validate(path, "hello there\n")
    assert ok is None, (
        "bd-mutate recorded an unrecognised suffix as %r; declining to parse "
        "must be UNKNOWN, never VALID" % (ok,))
    assert "UNKNOWN" in why and ".row711unregistered" in why, why


def _corpus_suffix_representatives():
    """Every suffix the TRACKED MUTANT CORPUS actually mutates.

    The population is derived from the specs and their real tracked subjects --
    never from the validator under test, which is the artifact whose
    completeness is in question.
    """
    specs = sorted((REPO / "tests" / "mutants").glob("*.json"))
    counts = collections.Counter()
    representatives = {}
    for spec_path in specs:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for mutant in spec.get("mutants", []):
            subject = REPO / mutant["file"]
            if not subject.is_file():
                continue
            counts[subject.suffix] += 1
            representatives.setdefault(subject.suffix, (subject, spec_path.name))
    return specs, counts, representatives


def _suffixes_the_validator_declines(validate, representatives):
    """Which of those suffixes does the validator refuse to JUDGE (None)?"""
    declined = {}
    for suffix, (subject, spec_name) in sorted(representatives.items()):
        ok, why = validate(subject, subject.read_text(encoding="utf-8"))
        if ok is None:
            declined[suffix] = (spec_name, why)
    return declined


def test_row711_every_suffix_in_the_tracked_mutant_corpus_is_registered():
    """THE COMPLETENESS GATE for row 711's UNKNOWN default.

    UNKNOWN-by-default is right, and it is only safe while the registration is
    complete for the corpus this tool RUNS. Installing the default alone flipped
    35 tracked mutants across 15 specs from graded to UNKNOWN, and an UNKNOWN
    mutant is never graded at all -- so a CAUGHT stopped being CAUGHT and a
    transform control that exists precisely to ESCAPE could no longer escape.
    This fails loudly at gate time the next time a subject type arrives, instead
    of silently at battery time.
    """
    specs, counts, representatives = _corpus_suffix_representatives()
    assert specs, "DENOMINATOR IS ZERO: no tracked mutant specs were found"
    assert sum(counts.values()) > 0, "DENOMINATOR IS ZERO: no tracked mutants"
    assert len(representatives) >= 5, (
        "the corpus resolved to %d suffix families, which is too few for this "
        "gate to be judging a population: %s" % (len(representatives), sorted(representatives)))
    # THE FIXTURE CONTAINS THE HAZARD. These are the three families the UNKNOWN
    # default took out; a corpus without them could not go red for row 711.
    for hazard in (".md", ".txt", ".yml"):
        assert counts[hazard] > 0, (
            "the tracked corpus no longer mutates any %s subject, so this gate "
            "cannot see the regression it exists for" % hazard)

    declined = _suffixes_the_validator_declines(mutate._validate, representatives)
    assert declined == {}, (
        "bd-mutate declines to judge %d suffix families its own tracked corpus "
        "mutates, so every mutant in them records UNKNOWN and is never graded: "
        "%s" % (len(declined), declined))


def test_row711_the_registration_gate_fires_when_a_family_is_deregistered(monkeypatch):
    """NEGATIVE CONTROL, and it reintroduces the exact hazard on the real seam.

    Emptying the two registration tables is what the patch-before-this-one did
    by omission. The gate above must then name all three families -- if it stays
    green under this, it is asserting nothing.
    """
    _specs, counts, representatives = _corpus_suffix_representatives()
    assert {".md", ".txt", ".yml"} <= set(representatives), sorted(representatives)
    monkeypatch.setattr(mutate, "_TEXT_SUFFIXES", frozenset())
    monkeypatch.setattr(mutate, "_YAML_SUFFIXES", frozenset())

    declined = _suffixes_the_validator_declines(mutate._validate, representatives)
    assert set(declined) == {".md", ".txt", ".yml"}, (
        "deregistering the text and yaml families should make exactly those "
        "three corpus suffixes unjudgeable; got %s" % (sorted(declined),))
    # MEASURED at this tree: 35 mutants over 15 specs (.md 17, .yml 13,
    # .txt 5). The number is recorded rather than pinned -- the corpus grows,
    # and a frozen count would refuse unrelated cuts for adding a mutant. What
    # must never be zero is the population itself.
    lost = sum(counts[s] for s in declined)
    assert lost > 0, (
        "deregistering three families cost 0 mutants, so the corpus behind "
        "this control is empty and the gate above proves nothing")


def test_row711_text_subjects_are_judged_valid_rather_than_declined(tmp_path):
    """`.md` and `.txt` have no parse contract for a mutant to break."""
    for name in ("notes.md", "subject.txt"):
        path = tmp_path / name
        path.write_text("hello\n", encoding="utf-8")
        assert mutate._validate(path, "hello there\n") == (True, ""), name


def test_row711_yaml_is_parsed_for_real_and_a_broken_one_is_INVALID_not_UNKNOWN(tmp_path):
    """The distinction that decides whether a mutant is GRADED at all.

    UNKNOWN skips grading; INVALID is a measured refusal. `.yml` can genuinely
    be unparseable, so it is parsed -- and the refusal must be False.
    """
    path = tmp_path / "workflow.yml"
    path.write_text("jobs:\n  a:\n    runs-on: ubuntu\n", encoding="utf-8")
    assert mutate._validate(path, "jobs:\n  b:\n    runs-on: ubuntu\n") == (True, "")

    broken = "jobs:\n  a: [unclosed\n"
    ok, why = mutate._validate(path, broken)
    assert ok is False, (
        "an unparseable .yml answered %r. None means nobody looked and skips "
        "grading; the parser DID look and refused, which is INVALID." % (ok,))
    assert why.startswith("yaml.safe_load:"), why


def test_row711_a_yaml_mutant_still_grades_normally(tmp_path, monkeypatch):
    """THE SEAM, at battery level -- this is what row645's M1 does for real."""
    original = "jobs:\n  shard_a: keep\n"
    subject, nodeid = _write_battery_fixture(
        tmp_path, "ci.yml", original,
        "from pathlib import Path\n"
        "def test_subject_value():\n"
        "    assert 'shard_a' in Path('ci.yml').read_text()\n")

    valid, why = mutate._validate(subject, "jobs:\n  shard_b: keep\n")
    rc, row, grade_calls = _run_counted_battery(
        tmp_path, monkeypatch,
        {"label": "a CI shard leaves the denominator", "file": "ci.yml",
         "old": "shard_a", "new": "shard_b", "catcher": nodeid},
        nodeid)

    assert (valid, why, row["verdict"], len(grade_calls), rc) == (
        True, "", mutate.CAUGHT, 1, 0), (
        "a well-formed .yml mutant must be graded, not declined; got valid=%r "
        "why=%r verdict=%s grade_calls=%d rc=%d"
        % (valid, why, row["verdict"], len(grade_calls), rc))
    assert row["why"] == "named catcher failed: " + nodeid


def test_row711_corrupt_json_is_unknown_and_never_graded(tmp_path, monkeypatch):
    original = '{"enabled": true}\n'
    corrupt = '{"enabled": \n'
    subject, nodeid = _write_battery_fixture(
        tmp_path, "payload.json", original,
        "import json\n"
        "from pathlib import Path\n"
        "def test_subject_value():\n"
        "    assert json.loads(Path('payload.json').read_text()) == "
        "{'enabled': True}\n")
    assert json.loads(subject.read_text(encoding="utf-8")) == {"enabled": True}
    with pytest.raises(json.JSONDecodeError):
        json.loads(corrupt)
    assert original.replace("true}", "") == corrupt, (
        "precondition: the mutant does not build the independently checked "
        "corrupt JSON fixture")

    valid, why = mutate._validate(subject, corrupt)
    rc, row, grade_calls = _run_counted_battery(
        tmp_path, monkeypatch,
        {"label": "corrupt JSON", "file": "payload.json",
         "old": "true}", "new": "", "catcher": nodeid},
        nodeid)

    assert (valid, row["verdict"], len(grade_calls), rc) == (
        None, mutate.UNKNOWN, 0, 2), (
        "corrupt .json must be recorded UNKNOWN before grading; got "
        "valid=%r verdict=%s grade_calls=%d rc=%d" %
        (valid, row["verdict"], len(grade_calls), rc))
    assert why.startswith("json.loads:"), why
    assert row["why"] == why


def test_row711_unknown_suffix_is_unknown_and_never_graded(tmp_path, monkeypatch):
    subject, nodeid = _write_battery_fixture(
        tmp_path, "setting.row711", "enabled\n",
        "from pathlib import Path\n"
        "def test_subject_value():\n"
        "    assert Path('setting.row711').read_text() == 'enabled\\n'\n")
    assert subject.suffix == ".row711" and subject.read_text() == "enabled\n"

    valid, why = mutate._validate(subject, "disabled\n")
    rc, row, grade_calls = _run_counted_battery(
        tmp_path, monkeypatch,
        {"label": "unknown suffix", "file": "setting.row711",
         "old": "enabled", "new": "disabled", "catcher": nodeid},
        nodeid)

    assert (valid, row["verdict"], len(grade_calls), rc) == (
        None, mutate.UNKNOWN, 0, 2), (
        "an unrecognised suffix must stop before grading; got valid=%r "
        "verdict=%s grade_calls=%d rc=%d" %
        (valid, row["verdict"], len(grade_calls), rc))
    assert "UNKNOWN" in why and ".row711" in why, why


def test_row711_well_formed_json_still_grades_normally(tmp_path, monkeypatch):
    original = '{"enabled": true}\n'
    mutated = '{"enabled": false}\n'
    subject, nodeid = _write_battery_fixture(
        tmp_path, "payload.json", original,
        "import json\n"
        "from pathlib import Path\n"
        "def test_subject_value():\n"
        "    assert json.loads(Path('payload.json').read_text()) == "
        "{'enabled': True}\n")
    assert json.loads(subject.read_text(encoding="utf-8")) == {"enabled": True}
    assert json.loads(mutated) == {"enabled": False}

    valid, why = mutate._validate(subject, mutated)
    rc, row, grade_calls = _run_counted_battery(
        tmp_path, monkeypatch,
        {"label": "well-formed JSON", "file": "payload.json",
         "old": "true", "new": "false", "catcher": nodeid},
        nodeid)

    assert (valid, why, row["verdict"], len(grade_calls), rc) == (
        True, "", mutate.CAUGHT, 1, 0)
    assert row["why"] == "named catcher failed: " + nodeid


def test_row711_transform_control_imports_without_asserting_suffix_behavior():
    assert TOOL.is_file(), "precondition: bd-mutate subject is absent"
    assert callable(mutate._validate), "precondition: validator import failed"


def test_the_python_and_shell_validators_still_work(tmp_path):
    """REGRESSION GUARD. This cut edits the function those two depend on."""
    py = tmp_path / "m.py"
    py.write_text("x = 1\n", encoding="utf-8")
    assert mutate._validate(py, "x = 1\n")[0] is True
    assert mutate._validate(py, "def broken(:\n")[0] is False

    sh = tmp_path / "m.sh"
    sh.write_text("echo hi\n", encoding="utf-8")
    assert mutate._validate(sh, "echo hi\n")[0] is True
    assert mutate._validate(sh, "if then fi done\n")[0] is False
