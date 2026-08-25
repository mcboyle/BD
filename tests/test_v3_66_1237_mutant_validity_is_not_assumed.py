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

import importlib.machinery
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

    A type this tool cannot parse must not be recorded as valid. `.md` has no
    parser here, so the honest answer is neither True nor False -- and the
    caller turns that into an UNKNOWN verdict rather than running the mutant
    and believing whatever came back.
    """
    path = tmp_path / "notes.md"
    path.write_text("# hello\n", encoding="utf-8")
    ok, _why = mutate._validate(path, "# hello there\n")
    assert ok is not False, "markdown was refused, which is over-correction"


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
