"""bd-precut must RUN the gates bd-band-derive cannot derive.

THE DEFECT, and it is one defect rather than four. Between v3.66.1223 and
v3.66.1238 the same integrator shipped four separate mistakes that a gate
ALREADY IN THIS TREE would have caught:

  * a mutation anchor resolving zero or two times -- FOUR times over
    (v3.66.1223, 1228, 1231, 1237), each caught late by hand
  * a subprocess budget of 600s inside a 240s bound (v3.66.1229)
  * a subprocess inheriting the ambient LC_ALL (v3.66.1235)

NONE was a missing gate. `test_v3_66_1184` checks anchor uniqueness,
`test_v3_66_1222` checks budgets, `test_v3_66_1197` checks LC_ALL. Every one of
them EXISTS and did not RUN, because `bd-band-derive` derives an affected band
from CHANGED PATHS and these gates judge the TREE -- so no diff ever selects
them. Two were caught by GitHub CI after a push, each costing a re-run and a
force-push.

A floor is not a ceiling (CLAUDE.md A5). This is the mechanical part of the
ceiling, so it belongs in a tool rather than in a habit that four cuts proved
unreliable.
"""
from __future__ import annotations

import ast
import pathlib
import re
import subprocess

BD_GATE_SCOPE = "repo-wide"

REPO = pathlib.Path(__file__).resolve().parents[1]
PRECUT = REPO / "toolchain" / "bin" / "bd-precut"

#: Pinned HERE and not read out of bd-precut, so that deleting one there is a
#: FAILURE rather than a silently smaller expectation. Deriving the expected set
#: from the artifact under test is how a dropped entry passes (CLAUDE.md A7).
EXPECTED_GATES = {
    "tests/test_row357_mutant_anchors_are_not_fragile.py",
    "tests/test_row473_register_tree_containment.py",
    "tests/test_v3_66_1184_mutation_specs_are_tracked.py",
    "tests/test_v3_66_1034_guards_survive_a_module_wipe.py",
    "tests/test_v3_66_1222_every_budget_is_subordinate_to_its_bound.py",
    "tests/test_v3_66_1197_ambient_locale_into_subprocess.py",
    "tests/test_import_graph_no_new_edges.py",
}


def _precut_source() -> str:
    return PRECUT.read_text(encoding="utf-8")


def _declared_gates() -> set[str]:
    """The gate list read from the AST, not from the file's text.

    THE FIRST VERSION OF THIS REGEXED THE SOURCE, and a mutation battery caught
    it inside the very cut about textual proxies: commenting an entry out left
    the string sitting in the file, so the regex still found it and the mutant
    ESCAPED. Comments are inside a text scanner's denominator and outside the
    AST's -- the same lesson v3.66.1232 learned for the registrable-domain
    census, arriving here seven cuts later in the tool that is supposed to stop
    me repeating myself.
    """
    tree = ast.parse(_precut_source())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "_UNDERIVED_GATES" not in targets:
            continue
        assert isinstance(node.value, (ast.List, ast.Tuple)), node.value
        out = set()
        for elt in node.value.elts:
            assert isinstance(elt, (ast.Tuple, ast.List)) and elt.elts, elt
            first = elt.elts[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str), first
            out.add(first.value)
        return out
    raise AssertionError("bd-precut no longer declares _UNDERIVED_GATES at all")


def test_every_underived_gate_is_named_by_the_tool():
    """The whole-tree population is RUN, not merely mentioned in a comment."""
    named = _declared_gates()
    assert named == EXPECTED_GATES, (
        "bd-precut runs %r but this contract pins %r. A gate removed from that "
        "list stops running and nothing else would notice -- which is exactly "
        "the failure this cut exists to end."
        % (sorted(named), sorted(EXPECTED_GATES)))


def test_each_named_gate_actually_exists_and_is_tracked():
    """A list naming a file that is not there is a list that runs nothing."""
    tracked = set(subprocess.run(
        ["git", "ls-files"], cwd=str(REPO), capture_output=True, text=True,
        check=True).stdout.split())
    assert _declared_gates() == EXPECTED_GATES, "declaration drifted"
    for rel in sorted(EXPECTED_GATES):
        assert rel in tracked, "%s is named by bd-precut but not tracked" % rel
        assert (REPO / rel).is_file(), rel


def test_none_of_them_is_derivable_from_a_changed_path():
    """THE PRECONDITION FOR THE WHOLE CUT, asserted rather than assumed.

    If bd-band-derive DID select these, running them here would be redundant.
    It does not: they judge the tree, so no set of changed paths reaches them.
    This drives the real deriver over each gate's own path -- the most generous
    possible input -- and asserts it still does not select its peers.
    """
    derive = REPO / "toolchain" / "bin" / "bd-band-derive"
    assert derive.is_file(), derive
    import json
    import sys
    unreachable = []
    for rel in sorted(EXPECTED_GATES):
        r = subprocess.run(
            [sys.executable, str(derive), "--files", rel, "--json"],
            # BOUNDED BELOW THE BOUND GOVERNING THIS ITEM. The first draft
            # of this very file carried timeout=300 inside the 240s pytest
            # bound -- the fifth instance of that defect in one session, and
            # the check this cut ADDS is what caught it, before the push.
            # MEASURED: one bd-band-derive call takes ~6s on an idle test5.
            # max(30, 6 x 7) = 42; 60 leaves room for all seven calls under load
            # and clears the 240 - 30 ceiling with margin.
            cwd=str(REPO), capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            continue
        try:
            band = set(json.loads(r.stdout).get("band", []))
        except (ValueError, TypeError):
            continue
        missed = EXPECTED_GATES - band - {rel}
        if missed:
            unreachable.append((rel, sorted(missed)))
    assert unreachable, (
        "bd-band-derive now selects every one of these from any of their own "
        "paths, which would make this check redundant. If that is a real "
        "change, retire this cut rather than leaving a tool doing nothing."
    )


def test_the_tool_treats_a_failing_underived_gate_as_BLOCKING():
    """Not advisory. Two of the four defects reached GitHub before anyone saw
    them, and advisory output is what got skimmed past."""
    src = _precut_source()
    site = re.search(r"if rc != 0:\s*\n\s*blocking\.append\(\s*\n?\s*"
                     r'"underived gate\(s\) FAILED', src)
    assert site, (
        "a failing underived gate no longer appends to `blocking`. If it only "
        "warns, this whole cut is decoration.")


def test_an_absent_population_is_UNKNOWN_and_not_OK():
    """The fail-open this repository keeps meeting. If none of the population is
    present, the honest answer is UNKNOWN -- not a clean bill of health."""
    src = _precut_source()
    assert re.search(r"if not present:\s*\n\s*unknown\.append\(", src), (
        "bd-precut no longer reports an absent gate population as UNKNOWN; a "
        "zero-length list would then read as nothing to report")
    assert "len(present) < len(_UNDERIVED_GATES)" in src, (
        "a PARTIALLY present population is no longer reported, so part of the "
        "population vanishing would still print a green line")
