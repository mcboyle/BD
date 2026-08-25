"""The --work default census must judge what a tool RESOLVES, not what it says.

BACKLOG ROW 196. `tests/test_desandbox_tool_verifiers.py` decided whether a
tracked tool defaults `--work` to the retired sandbox tree by asking whether the
file's BYTES contained one exact spelling of that assignment. A textual proxy
for a runtime property is wrong in both directions at once, and this file proves
both directions against the real gate function rather than describing them:

  FALSE NEGATIVE  five spellings that are byte-different and runtime-identical.
                  Single quotes, a module constant, an imported module
                  attribute, a line-broken keyword, and a subdirectory of the
                  retired tree. The imported-attribute shape is not a
                  hypothetical: it is how the overwhelming majority of this
                  repo's real tools spell the same default.
  FALSE POSITIVE  a tool whose docstring and comment quote the retired
                  assignment while its executable default is a real directory.
                  Over-sensitivity is a soundness bug too -- CLAUDE.md A7 says
                  so explicitly, and a gate that fires on prose gets switched
                  off.

HOW THIS DRIVES THE REAL SUBJECT. The gate under test scans a whole repository,
so it is driven with `REPO` repointed at `tmp_path`, its tracked-file
enumeration stubbed to exactly one fixture, and its carrier classifier replaced
by a spy. The spy is the seam: it records the denominator the gate measured and
the carrier set the gate concluded, so the verdict here is the gate's OWN
conclusion and not a re-implementation of it.

WHY THIS IS A REPLAY AND NOT A BATTERY. Every name touched above -- `REPO`,
`_python_typed_tracked`, `_classify_sandbox_carriers` and the gate function
itself -- exists on the defective parent as well as on the fix, and the gate is
invoked through a signature-adaptive call so the same harness runs on both. This
file was executed against the pre-fix tree and every assertion below failed for
the intended reason; the exact texts are in the cut's evidence.

INDEPENDENT PRECONDITION. Each fixture's runtime default is measured by RUNNING
the fixture with the repository interpreter and reading its parsed value back,
which is a different mechanism from the one the fix uses. "The census saw
nothing" can therefore only mean the census is blind; it can never mean the
fixture was inert.

The retired path is never written literally here. It is read back out of the
subject module at run time, so this file does not join the carrier population
that `tests/test_sandbox_home_stays_retired.py` freezes.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

SUBJECT = Path(__file__).resolve().parent / "test_desandbox_tool_verifiers.py"

# One fixture tool. `build()` is only called under `__main__`, which is exactly
# how the census execs it and how a direct run behaves, so the two measurements
# below observe the same code path.
_TOOL_TEMPLATE = '''\
"""%(doc)s"""
import argparse
import json

%(preamble)s


def build():
    parser = argparse.ArgumentParser(prog="fixture-tool")
%(declaration)s
    return parser


if __name__ == "__main__":
    print(json.dumps(vars(build().parse_args([]))))
'''

_SIBLING = "bd_fixture_shared_defaults.py"


def _load_subject():
    """Load the gate module by path, under a private name.

    By path rather than by `import`, so this file adds no tests->tests import
    edge, and under a private name so the monkeypatching below cannot leak into
    the copy pytest collects when both files run in one process.
    """
    assert SUBJECT.is_file(), (
        "the subject gate module is missing at %s, so nothing below is a "
        "measurement of it" % SUBJECT)
    spec = importlib.util.spec_from_file_location("_row196_subject", SUBJECT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _retired_work(module) -> str:
    """The retired sandbox work path, read out of the subject at run time.

    Reads the post-fix constant when it exists and falls back to parsing the
    pre-fix textual needle, so one harness measures both trees. Writing the
    path as a literal here would enrol this file in the frozen carrier
    population that tests/test_sandbox_home_stays_retired.py pins.
    """
    value = getattr(module, "_RETIRED_SANDBOX_WORK", None)
    if isinstance(value, str) and value:
        return value
    legacy = getattr(module, "_SANDBOX_WORK_DEFAULT", None)
    assert isinstance(legacy, str) and legacy, (
        "the subject declares neither _RETIRED_SANDBOX_WORK nor "
        "_SANDBOX_WORK_DEFAULT, so this harness cannot name the path it is "
        "supposed to hunt and every verdict below would be vacuous")
    match = re.search(r'default="([^"]+)"', legacy)
    assert match, (
        "could not read a path out of the legacy needle %r" % legacy)
    return match.group(1)


def _write_tool(directory: Path, name: str, *, doc="fixture tool", preamble="",
                declaration="") -> Path:
    path = directory / name
    path.write_text(
        _TOOL_TEMPLATE % {"doc": doc, "preamble": preamble,
                          "declaration": declaration},
        encoding="utf-8")
    assert path.is_file() and path.stat().st_size > 0, (
        "the fixture tool %s was not written, so the census would be "
        "certifying an absent file" % name)
    return path


def _runtime_default(path: Path, env=None) -> str:
    """What the fixture's own parser resolves --work to, measured by RUNNING it.

    Deliberately NOT the mechanism the fix uses. A7 forbids deriving the
    expected set solely from the artifact under test.

    Reads the parsed namespace rather than one attribute name: argparse names
    the destination after the FIRST long option, and 15 of this repo's real
    tools spell the declaration `add_argument("--tree", "--work", ...)`, so an
    attribute lookup would work only for the spelling that happens to be
    convenient here.
    """
    result = subprocess.run([sys.executable, str(path)], capture_output=True,
                            text=True, timeout=60, cwd=str(path.parent), env=env)
    assert result.returncode == 0, (
        "the fixture tool %s did not run (rc=%d), so it is not a valid "
        "subject:\n%s" % (path.name, result.returncode, result.stderr[-800:]))
    values = list(json.loads(result.stdout).values())
    assert len(values) == 1, (
        "fixture %s parsed to %d destinations, not one, so 'the' default is "
        "ambiguous: %r" % (path.name, len(values), values))
    return values[0]


def _drive_gate(module, tmp_path, rels):
    """Run the real gate over exactly these fixtures and return its conclusion.

    Returns the spy's record: {"calls", "n_files", "carriers"}.
    """
    rels = list(rels)
    assert rels, "no fixture was handed to the gate, so the verdict is vacuous"
    seen = {"calls": 0}

    def spy(n_files, carriers, known, floor=2000):
        seen["calls"] += 1
        seen["n_files"] = n_files
        seen["carriers"] = set(carriers)
        seen["known"] = set(known)
        return []

    gate = getattr(module, "test_the_bare_work_default_is_not_a_sandbox_path", None)
    assert callable(gate), (
        "the gate function this row is about is gone from the subject module; "
        "renaming it silently would leave CI running nothing")

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(module, "REPO", tmp_path)
        monkey.setattr(module, "_python_typed_tracked", lambda: list(rels))
        # The post-fix gate refuses a collapsed candidate population. That
        # floor is a real guard against a whole-tree scan silently emptying;
        # here the population is deliberately tiny. raising=False so the same
        # harness runs on the parent, where the name does not exist.
        monkey.setattr(module, "_WORK_CANDIDATE_FLOOR", 1, raising=False)
        monkey.setattr(module, "_classify_sandbox_carriers", spy)
        assert module.REPO == tmp_path, (
            "REPO was not repointed at the fixture tree, so this test would be "
            "measuring the real repository")
        params = inspect.signature(gate).parameters
        gate(**({"tmp_path": tmp_path} if "tmp_path" in params else {}))
    finally:
        monkey.undo()

    assert seen["calls"] == 1, (
        "the gate consulted its carrier classifier %d time(s), not once, so "
        "the record below is not the gate's verdict" % seen["calls"])
    assert seen["n_files"] == len(rels), (
        "the gate measured a denominator of %r rather than the %d fixture(s) "
        "it was given; a scan over nothing reports no carriers and passes "
        "forever" % (seen["n_files"], len(rels)))
    return seen


# ── the five runtime-identical evasions ─────────────────────────────────────

def _evasion_cases(retired: str, sibling_written: dict):
    """id -> (kwargs for _write_tool, expected runtime default)."""
    return {
        "single-quoted": (
            {"declaration": "    parser.add_argument('--work', default='%s')"
                            % retired},
            retired),
        "named-constant": (
            {"preamble": 'WORK = "%s"' % retired,
             "declaration": '    parser.add_argument("--work", default=WORK)'},
            retired),
        # The real-world shape, twice over: 129 of this repo's 146 declarers
        # resolve the default through an imported module attribute, and 15 of
        # them put --work SECOND, after --tree. A census that recognised only a
        # first-position option would miss all 15.
        "imported-constant": (
            {"preamble": "import %s as sec" % _SIBLING[:-3],
             "declaration": '    parser.add_argument("--tree", "--work",\n'
                            "                        default=sec.DEFAULT_WORK)"},
            retired),
        "line-broken": (
            {"declaration": "    parser.add_argument(\n"
                            '        "--work",\n'
                            "        default =\n"
                            '        "%s",\n'
                            "    )" % retired},
            retired),
        "retired-subdirectory": (
            {"preamble": 'WORK_ROOT = "%s"' % retired,
             "declaration": '    parser.add_argument("--work",\n'
                            '                        default=WORK_ROOT + "/checkout")'},
            retired + "/checkout"),
    }


@pytest.mark.parametrize("case_id", sorted(_evasion_cases("x", {})))
def test_a_runtime_work_default_evasion_is_seen_by_the_census(tmp_path, case_id):
    """RED on the parent, for all five ids, with carriers=set().

    Each fixture defaults --work to the retired tree at RUNTIME while spelling
    it in a way the byte scan cannot match.
    """
    module = _load_subject()
    retired = _retired_work(module)
    (tmp_path / _SIBLING).write_text(
        'DEFAULT_WORK = "%s"\n' % retired, encoding="utf-8")

    kwargs, expected = _evasion_cases(retired, {})[case_id]
    name = "tool_%s.py" % case_id.replace("-", "_")
    path = _write_tool(tmp_path, name, **kwargs)
    rel = name

    # PRECONDITION 1 -- the evasion is real: the byte needle the retired gate
    # matched on is genuinely absent from this source.
    needle = 'default="%s"' % retired
    source = path.read_text(encoding="utf-8")
    assert needle not in source, (
        "fixture %s still contains the literal byte needle, so a green verdict "
        "could be manufactured by the textual path this row is removing" % name)

    # PRECONDITION 2 -- measured independently of the census: the fixture's own
    # parser really resolves --work to the retired tree.
    observed = _runtime_default(path)
    assert observed == expected, (
        "fixture %s resolves --work to %r, not the intended %r, so it is not "
        "an evasion and the verdict below would prove nothing"
        % (name, observed, expected))

    seen = _drive_gate(module, tmp_path, [rel])

    assert seen["carriers"] == {rel}, (
        "RUNTIME --WORK DEFAULT EVASION IS INVISIBLE TO THE CENSUS: running "
        "%s proved it defaults --work to %r, which is inside the retired "
        "sandbox tree, but the census reported carriers=%r. A gate that reads "
        "source text certifies a spelling, not a behaviour."
        % (name, observed, sorted(seen["carriers"])))


def test_inert_retired_prose_is_not_a_runtime_work_default(tmp_path):
    """OVER-SENSITIVITY CONTROL, and RED on the parent in the other direction.

    The parent reported carriers={this file} for a tool whose only contact with
    the retired path is a docstring and a comment. A textual gate cannot satisfy
    this and the five evasions at the same time, which is the whole argument for
    behaviour.
    """
    module = _load_subject()
    retired = _retired_work(module)
    good_dir = tmp_path / "real_tree"
    good_dir.mkdir()

    name = "inert_prose_tool.py"
    path = _write_tool(
        tmp_path, name,
        doc='historical note: default="%s" was this tool once, and is not now'
            % retired,
        preamble='# retired shape, kept as documentation: default="%s"' % retired,
        declaration='    parser.add_argument("--work", default=%r)' % str(good_dir))

    # PRECONDITION 1 -- the prose really is there, so a pass cannot come from
    # the fixture having quietly lost its comment.
    needle = 'default="%s"' % retired
    assert path.read_text(encoding="utf-8").count(needle) == 2, (
        "fixture %s should carry the retired assignment exactly twice as inert "
        "prose; without it this control proves nothing" % name)

    # PRECONDITION 2 -- measured by running it: the executable default is a
    # real directory on this host.
    observed = _runtime_default(path)
    assert observed == str(good_dir) and good_dir.is_dir(), (
        "fixture %s resolves --work to %r rather than the real directory %s"
        % (name, observed, good_dir))

    seen = _drive_gate(module, tmp_path, [name])

    assert seen["carriers"] == set(), (
        "INERT RETIRED PROSE WAS COUNTED AS A RUNTIME CONFIGURATION: running "
        "%s proved it defaults --work to %r, a directory that exists on this "
        "host, but the census reported carriers=%r. Firing on a comment is a "
        "soundness bug in the other direction and is how a gate gets switched "
        "off." % (name, observed, sorted(seen["carriers"])))


def test_the_census_attributes_each_verdict_to_the_tool_it_measured(tmp_path):
    """PER-FILE ATTRIBUTION, which a single-fixture population cannot prove.

    A census that probes N targets in N children has to reconcile results back
    to names. Get that wrong -- an off-by-one, or every row reading the first
    result -- and the census still returns a plausible set: on a healthy tree
    every record looks alike, so nothing fires, and on a dirty one it accuses
    the wrong file. Two fixtures, one clean and one retired, with the clean one
    FIRST in sorted order, is the smallest population where that is visible.

    This is also the second RED direction against the parent: the retired
    fixture spells its default through a module constant, which the byte scan
    could not see either.
    """
    module = _load_subject()
    retired = _retired_work(module)
    good_dir = tmp_path / "real_tree"
    good_dir.mkdir()

    clean = "tool_a_clean.py"
    dirty = "tool_b_retired.py"
    clean_path = _write_tool(
        tmp_path, clean,
        declaration='    parser.add_argument("--work", default=%r)' % str(good_dir))
    dirty_path = _write_tool(
        tmp_path, dirty,
        preamble='WORK = "%s"' % retired,
        declaration='    parser.add_argument("--work", default=WORK)')

    # PRECONDITION 1 -- ordering, which is what makes the attribution visible.
    assert sorted([clean, dirty]) == [clean, dirty], (
        "the clean fixture must sort FIRST, or a census that always reads the "
        "first result would accidentally give the right answer here")

    # PRECONDITION 2 -- neither fixture can be judged by the retired byte
    # needle, so a verdict cannot come from the textual path.
    needle = 'default="%s"' % retired
    for path in (clean_path, dirty_path):
        assert needle not in path.read_text(encoding="utf-8"), (
            "fixture %s contains the retired byte needle" % path.name)

    # PRECONDITION 3 -- measured by running each one.
    assert _runtime_default(clean_path) == str(good_dir), clean
    assert _runtime_default(dirty_path) == retired, dirty

    seen = _drive_gate(module, tmp_path, [clean, dirty])

    assert seen["carriers"] == {dirty}, (
        "THE CENSUS DID NOT ATTRIBUTE ITS VERDICT TO THE TOOL IT MEASURED: "
        "running the fixtures proved %s defaults --work to a real directory "
        "and %s defaults it to the retired sandbox tree, but the census "
        "reported carriers=%r. A result read back under the wrong name is not "
        "a measurement of anything." % (clean, dirty, sorted(seen["carriers"])))


def test_a_second_option_declaration_is_refused_rather_than_half_measured(tmp_path):
    """The probe stops at the FIRST declaration, so a second one is UNKNOWN.

    ADDED AFTER ADVERSARIAL PROBING. The census refuses a file that declares the
    option twice, but no tracked file does today, so that refusal was a rule
    nothing exercised: blunting it to `if False` changed no result anywhere. A
    guard that only fires in a state the tree is not in is a guard no mutant can
    reach, and this file already makes that argument twice about the subject.

    The refusal matters because the failure it prevents is silent: the probe
    would report the FIRST declaration's default and the second subparser's
    retired default would be certified without ever being measured.
    """
    module = _load_subject()
    retired = _retired_work(module)
    good_dir = tmp_path / "real_tree"
    good_dir.mkdir()

    name = "tool_two_declarations.py"
    path = _write_tool(
        tmp_path, name,
        preamble='WORK = "%s"' % retired,
        declaration='    parser.add_argument("--work", default=%r)\n'
                    '    sub = parser.add_subparsers().add_parser("again")\n'
                    '    sub.add_argument("--work", default=WORK)' % str(good_dir))

    # PRECONDITION 1 -- the fixture really declares the option twice. Parsed
    # here rather than by calling the subject's own enumerator, so this is an
    # independent count and not the artifact under test grading itself.
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=name)
    declarations = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "add_argument"
                    and any(isinstance(a, ast.Constant) and a.value == "--work"
                            for a in n.args)]
    assert len(declarations) == 2, (
        "the fixture should declare the option exactly twice; it declares it "
        "%d time(s)" % len(declarations))

    # PRECONDITION 2 -- measured by running it: a bare invocation resolves the
    # FIRST declaration, so the retired second one is invisible to any census
    # that stops there.
    assert _runtime_default(path) == str(good_dir), (
        "the fixture's bare invocation should resolve the first, harmless "
        "declaration")

    with pytest.raises(AssertionError) as excinfo:
        _drive_gate(module, tmp_path, [name])

    text = str(excinfo.value)
    assert "more than once" in text and name in text, (
        "A SECOND OPTION DECLARATION WAS NOT REFUSED: the census stops at the "
        "first declaration, so a file declaring the option twice has one "
        "measured default and one unmeasured one -- and an unmeasured default "
        "is UNKNOWN, not OK. The refusal did not name the file or the reason; "
        "it said: %s" % text[:400])


def test_an_ambient_bd_repo_cannot_steer_the_census(tmp_path, monkeypatch):
    """The census measures the CODE, not the shell it happens to run in.

    ADDED AFTER ADVERSARIAL PROBING. `_probe_work_defaults` pops BD_ROOT,
    BD_REPO, BD_INSTALL_DIR and BD_WORK_TREE, but none of those is set on this
    host or in CI, so deleting the pop changed no result and the isolation was
    prose rather than a measured property. An operator with BD_REPO exported
    would otherwise get a different verdict from the same tree, which is the
    definition of an unreproducible gate.

    Both directions are asserted: the fixture is PROVED sensitive to the
    variable first, so the clean verdict below cannot come from a fixture that
    ignores it.
    """
    module = _load_subject()
    retired = _retired_work(module)
    good_dir = tmp_path / "real_tree"
    good_dir.mkdir()

    name = "tool_env_steered.py"
    path = _write_tool(
        tmp_path, name,
        preamble="import os",
        declaration='    parser.add_argument(\n'
                    '        "--work",\n'
                    '        default=os.environ.get("BD_REPO", %r))' % str(good_dir))

    # PRECONDITION 1 -- the fixture really is steered by the variable. Without
    # this the "census ignored it" verdict below would be vacuous.
    # ROW 178 / v3.66.1197: a subprocess that INHERITS os.environ inherits the
    # ambient LC_ALL, and locale collation then decides sort order inside the
    # child -- so the host's language can change a verdict. Pinned to C, which
    # is what the tree-wide gate at test_v3_66_1197 requires and what caught
    # this at v3.66.1235 in CI rather than locally.
    steered_env = dict(os.environ)
    steered_env["LC_ALL"] = "C"
    steered_env["BD_REPO"] = retired
    assert _runtime_default(path, env=steered_env) == retired, (
        "the fixture does not honour BD_REPO, so it cannot demonstrate that "
        "the census ignores it")

    # PRECONDITION 2 -- and it falls back to the harmless default without it.
    clean_env = {k: v for k, v in os.environ.items() if k != "BD_REPO"}
    clean_env["LC_ALL"] = "C"
    assert _runtime_default(path, env=clean_env) == str(good_dir)

    # The variable is now genuinely present in THIS process's environment --
    # removed rather than merely not set, then set, so an inherited value
    # cannot be what is measured.
    monkeypatch.delenv("BD_REPO", raising=False)
    monkeypatch.setenv("BD_REPO", retired)
    assert os.environ["BD_REPO"] == retired

    seen = _drive_gate(module, tmp_path, [name])

    assert seen["carriers"] == set(), (
        "AN AMBIENT BD_REPO STEERED THE CENSUS: the fixture's own code defaults "
        "--work to a real directory and only reaches the retired tree when "
        "BD_REPO is exported, yet with that variable set in the census "
        "process the census reported carriers=%r. A verdict that depends on "
        "the operator's shell is not a property of the tree."
        % (sorted(seen["carriers"]),))
