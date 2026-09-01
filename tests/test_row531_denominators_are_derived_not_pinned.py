"""Row 531: a denominator that must be hand-bumped is a chore, not a gate.

Three literals in this tree had to be edited by hand every time the repository
grew in a perfectly ordinary way:

  _EXPECTED_DECLARED_GATE_COUNT            235, and twenty lines of bump comments
  _EXPECTED_CONFIRMED_SAFETY_GATE_COUNT      7
  len(current) == 139 / len(current_docs) == 138   the tracked Markdown corpus

None of them asks a question the surrounding assertions do not already answer.
The gate module already proves a one-to-one declared/executed correspondence
with no duplicates and no strays; the Markdown gate can derive its own corpus
from `git ls-files`. What the literals added was a second CI round-trip, and on
2026-08-31 they cost exactly that twice in one day: a new repo-wide test file
and a new `docs/repo/FLEET_TOPOLOGY.md` each turned a green candidate red for
no defect at all.

Removing a ratchet is a soundness change, so this file holds the line that the
removal must not cross. The properties CLAUDE.md A7 actually requires --
NONZERO, MEMBERSHIP, UNIQUENESS -- are asserted here directly against the live
helpers, each with a negative control proving its intended failure is still
reachable. Only the arbitrary total is gone.

Scope note (rows 566 and 571). This file was first marked `module` on the
argument that "the tree-wide questions it protects are still asked by the
modules themselves". That was false in both directions. The modules kept only
SHRINK-ONLY floors over the Markdown corpus, so the exact bidirectional
denominator -- the one that sees a membership drift preserving the count --
exists nowhere else; and the only refusal of a re-pin lives here too. Marked
`module`, this file sat in no workflow shard, so on a repository that merges
unattended on green CI neither refusal could ever fire. Its subjects are the
tracked Markdown corpus and the declared gate census, both tree-wide, so it is
repo-wide and it reaches a shard.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess

import pytest

BD_GATE_SCOPE = "repo-wide"

REPO = pathlib.Path(__file__).resolve().parents[1]
SELF = pathlib.Path(__file__).resolve()
SELF_REL = f"tests/{SELF.name}"
GATE_MODULE = REPO / "tests" / "test_v3_66_939_ci_gate_shards_cover_every_gate.py"
DOCS_MODULE = REPO / "tests" / "test_v3_66_1172_nested_freshness_and_legacy_retirement.py"

# The scan denominator. Three modules, not two: row 566 is a guard that could
# not see its own shape, and the first thing such a guard must scan is itself.
SCANNED_MODULES = (GATE_MODULE, DOCS_MODULE, SELF)

# A corpus-sized literal. Small integers (a two-element control set, the 14
# historical documents) are not the chore this row is about, so the rule is
# scoped by magnitude as well as by shape.
_CORPUS_SCALE = 20


def _module_int_names(tree: ast.Module) -> dict[str, int]:
    """{name: value} for every module-level `NAME = <int literal>` binding.

    Row 566. The retired rule recognised a name only by its `_COUNT` suffix, so
    the constants row 531 introduced as the REPLACEMENT -- every one of them
    named `..._FLOOR` -- were invisible to it, and a one-character `>=` -> `==`
    edit reinstated the chore silently. Suffix-matching `_FLOOR` as well would
    reproduce that shape at the next rename, which is precisely the A7 warning
    that a fix tends to carry the defect it corrects. So the name is RESOLVED
    against its binding in the same module instead of being pattern-matched.
    """
    bindings: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        value = node.value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, int)
                and not isinstance(value.value, bool)):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = value.value
    return bindings


def _equality_pins(path: pathlib.Path) -> list[tuple[int, str]]:
    """Every `len(...) == <corpus-scale int, or a module int constant>`.

    The rule is about the OPERATOR, not the number. An equality pin breaks every
    time the repository grows in an ordinary way; a `>=` floor never does, and
    still refuses the removal that an equality pin was really there to catch.
    So `>=` is deliberately allowed and `==` is not.

    Three shapes are reported, and the magnitude exemption applies to the FIRST
    one only:

      len(x) == 139            an inline total at corpus scale
      len(x) == _SOME_FLOOR    a module-level int constant, at ANY magnitude,
                               because _CONFIRMED_SAFETY_GATE_FLOOR is 7
      len(x) == _SOME_FLOOR-1  the same name inside arithmetic, which is how the
                               retired `len(current_docs) == 138` was spelled

    Parsed, not grepped. A7: when a gate scans source text its own comments and
    examples fall inside the denominator -- and this module's docstring names all
    three retired literals, so a textual scan would report itself.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bindings = _module_int_names(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.Eq):
            continue
        for a, b in ((node.left, node.comparators[0]),
                     (node.comparators[0], node.left)):
            if not (isinstance(a, ast.Call) and isinstance(a.func, ast.Name)
                    and a.func.id == "len"):
                continue
            if (isinstance(b, ast.Constant) and isinstance(b.value, int)
                    and not isinstance(b.value, bool) and b.value >= _CORPUS_SCALE):
                found.append((node.lineno, f"len(...) == {b.value}"))
                continue
            names = sorted({n.id for n in ast.walk(b) if isinstance(n, ast.Name)
                            and (n.id in bindings or n.id.endswith(("_COUNT", "_FLOOR")))})
            if names:
                found.append((node.lineno, f"len(...) == {'/'.join(names)}"))
    return found


def _module_scope(path: pathlib.Path):
    """The module-level BD_GATE_SCOPE value, or None.

    AST and module scope only, so a docstring, a comment or an assertion
    message naming the marker answers nothing -- which matters here because
    this file names it in prose several times and is pointed at itself.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "BD_GATE_SCOPE" for t in targets):
            value = node.value
            return value.value if isinstance(value, ast.Constant) else None
    return None


# --------------------------------------------------------------------------
# The literals are gone
# --------------------------------------------------------------------------
def test_the_markdown_corpus_is_not_pinned_to_a_hand_bumped_total():
    offenders = _equality_pins(DOCS_MODULE)
    assert not offenders, (
        "the tracked-Markdown gate compares a derived length against a literal "
        f"total at line(s) {offenders}. Adding one ordinary document then turns "
        "a green candidate red for no defect -- twice on 2026-08-31. Derive the "
        "expected corpus independently instead.")


def test_the_gate_denominator_is_not_pinned_to_a_hand_bumped_total():
    offenders = _equality_pins(GATE_MODULE)
    assert not offenders, (
        f"an exact gate-count equality pin survives at {offenders}. The "
        "declared/executed correspondence already proves membership and "
        "uniqueness, and a floor still catches a removal; the exact total only "
        "adds a second CI round-trip on every cut that declares a gate.")


# --------------------------------------------------------------------------
# ... and every property that mattered is still asserted
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def gate_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location("row531_gate_module", GATE_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_coverage_accepts_a_grown_population_with_no_literal(gate_mod):
    """The whole point: three gates, three executions, no expected_count."""
    declared = {"tests/test_a.py", "tests/test_b.py", "tests/test_c.py"}
    shards = {"one": ["tests/test_a.py", "tests/test_b.py"], "two": ["tests/test_c.py"]}
    gate_mod._assert_exact_gate_coverage(declared, shards)


def test_coverage_still_refuses_a_declared_gate_that_no_shard_runs(gate_mod):
    """MEMBERSHIP. This is the failure the file exists for: a gate that is
    declared, is never executed, and leaves CI green."""
    with pytest.raises(AssertionError, match="missing from CI"):
        gate_mod._assert_exact_gate_coverage(
            {"tests/test_a.py", "tests/test_dropped.py"},
            {"one": ["tests/test_a.py"]})


def test_coverage_still_refuses_a_shard_entry_nobody_declared(gate_mod):
    """MEMBERSHIP, the other direction."""
    with pytest.raises(AssertionError, match="undeclared|extra in CI"):
        gate_mod._assert_exact_gate_coverage(
            {"tests/test_a.py"},
            {"one": ["tests/test_a.py", "tests/test_stray.py"]})


def test_coverage_still_refuses_a_repeated_gate_path(gate_mod):
    """UNIQUENESS. Two shards naming one suite would otherwise let the execution
    count match the declaration count while one declared gate never runs."""
    with pytest.raises(AssertionError, match="repeats|duplicate"):
        gate_mod._assert_exact_gate_coverage(
            {"tests/test_a.py", "tests/test_b.py"},
            {"one": ["tests/test_a.py"], "two": ["tests/test_a.py"]})


def test_coverage_still_refuses_an_empty_declaration(gate_mod):
    """NONZERO. A collapsed denominator must never read as full coverage."""
    with pytest.raises(AssertionError, match="nonzero|empty"):
        gate_mod._assert_exact_gate_coverage(set(), {})


# --------------------------------------------------------------------------
# Row 566: the guard is not blind to the shape it introduced
# --------------------------------------------------------------------------
def test_the_scanned_module_denominator_is_nonzero_and_present():
    """A gate over an empty or absent population must fail, not pass.

    The nonzero claim is not merely "three paths exist": the resolver this
    guard now depends on must actually SEE a module-level int constant in each
    of them, or a scan that resolves nothing would report zero offenders for
    the wrong reason.
    """
    assert SCANNED_MODULES, "the anti-re-pin scan has no modules to scan"
    for path in SCANNED_MODULES:
        assert path.is_file(), f"the scan names an absent module: {path}"
        bindings = _module_int_names(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        assert bindings, (
            f"{path.name} binds no module-level integer, so the resolver had "
            f"nothing to resolve and its silence is not evidence")
    assert GATE_MODULE in SCANNED_MODULES and DOCS_MODULE in SCANNED_MODULES
    assert SELF in SCANNED_MODULES, (
        "the anti-re-pin guard must be inside its own denominator; row 566 is "
        "exactly the shape of a guard that cannot see itself")
    assert len(set(SCANNED_MODULES)) == len(SCANNED_MODULES)


def test_the_guard_is_not_pinned_in_its_own_source():
    """Row 566, applied to the guard itself.

    The two assertions above this section scan the modules the guard judges.
    Neither scanned THIS file, so the instrument that refuses a hand-bumped
    denominator was the one place a hand-bumped denominator could hide.
    """
    offenders = _equality_pins(SELF)
    assert not offenders, (
        f"the anti-re-pin guard pins its own denominator at {offenders}. A "
        "guard exempt from its own rule is the shape row 566 names.")


def test_the_guard_sees_a_re_pin_onto_its_own_replacement_constant(tmp_path):
    """Row 566. Removing the three hand-bumped totals left three named
    constants behind, and a one-character `>=` -> `==` edit on any of them
    reinstates the chore the removal retired.

    The retired rule only recognised a name ending `_COUNT`, and only an inline
    integer at corpus scale, so BOTH replacements were invisible:
    `_CONFIRMED_SAFETY_GATE_FLOOR` is 7 -- below the magnitude cutoff -- and
    every one of them ends `_FLOOR`.
    """
    probe = tmp_path / "repinned.py"
    probe.write_text(
        "_SMALL_FLOOR = 7\n"
        "_BIG_FLOOR = 139\n"
        "def check(family, current):\n"
        "    assert len(family) == _SMALL_FLOOR\n"
        "    assert len(current) == _BIG_FLOOR - 1\n",
        encoding="utf-8")
    lines = sorted(line for line, _ in _equality_pins(probe))
    assert lines == [4, 5], (
        "the anti-re-pin guard did not report a `len(...) == <module int>` "
        f"re-pin; it reported lines {lines}. A guard that only knows the names "
        "the last cut happened to use is blind to the next rename, which is "
        "the same shape as the defect it exists to prevent.")


def test_the_guard_still_ignores_a_floor_and_a_small_inline_total(tmp_path):
    """Negative control: the widening did not turn the guard into `refuse all`.

    A `>=` monotonic floor is the sanctioned replacement and must stay legal; a
    small inline total (the 14 historical documents, the 12 retired tools) is
    not the corpus chore this row is about.
    """
    probe = tmp_path / "sound.py"
    probe.write_text(
        "_BIG_FLOOR = 139\n"
        "RETIRED = ()\n"
        "def check(current, historical):\n"
        "    assert len(current) >= _BIG_FLOOR\n"
        "    assert len(historical) == 14\n"
        "    assert len(RETIRED) == 12\n"
        "    assert len(current) == len(set(current))\n",
        encoding="utf-8")
    assert _equality_pins(probe) == [], (
        "the guard now refuses sound code: a monotonic floor, a small inline "
        "total and a self-comparison are all legal.")


def test_the_guard_still_reports_a_corpus_scale_inline_total(tmp_path):
    """The original rule must survive the widening."""
    probe = tmp_path / "inline.py"
    probe.write_text(
        "def check(current):\n"
        "    assert len(current) == 139\n",
        encoding="utf-8")
    assert [line for line, _ in _equality_pins(probe)] == [2]


# --------------------------------------------------------------------------
# Row 571: a gate CI does not run does not exist
# --------------------------------------------------------------------------
def test_this_gate_is_declared_and_reaches_a_ci_shard(gate_mod):
    """Rows 566 and 571. The EXACT Markdown denominator lives in this file --
    the modules themselves kept only shrink-only floors -- and so does the only
    refusal of a re-pin. Both were module-scoped and in no workflow shard, so a
    membership drift that preserved the count, or a `>=` -> `==` re-pin, passed
    every lane this operator merges on.
    """
    scope = _module_scope(SELF)
    assert scope == "repo-wide", (
        f"this file declares BD_GATE_SCOPE = {scope!r}. Its subject is the "
        "tracked Markdown corpus and the declared gate census -- both "
        "tree-wide populations -- so it must be repo-wide and reach a shard.")
    assert SELF_REL in gate_mod._DECLARED, (
        f"{SELF_REL} is repo-wide but absent from the declared gate set, so no "
        "shard runs it.")
    union = {suite for suites in gate_mod._shard_lists().values() for suite in suites}
    assert union, "the shard union is empty, so this assertion proves nothing"
    assert SELF_REL in union, (
        f"{SELF_REL} is declared but named by no CI shard: {sorted(union)[:3]}...")


# --------------------------------------------------------------------------
# The replacement denominator is independent, not the artifact under test
# --------------------------------------------------------------------------
def _independent_markdown_corpus() -> tuple[list[str], list[str]]:
    """Re-derive the corpus here, from git, WITHOUT importing bdtools_sec.

    A7: do not derive an expected set solely from the artifact under test. This
    is a second implementation of the same rule, so a change to either side is
    visible as a disagreement.
    """
    raw = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "*.md"], cwd=str(REPO), text=True, timeout=60)
    tracked = [p for p in raw.split("\0") if p]
    deleted = set(subprocess.check_output(
        ["git", "ls-files", "--deleted"], cwd=str(REPO), text=True, timeout=60).splitlines())
    current, historical = [], []
    for rel in tracked:
        if rel in deleted:
            continue
        parts = pathlib.PurePosixPath(rel).parts
        if len(parts) != 1 and parts[0] not in ("project-knowledge", "docs"):
            continue
        if rel == "CHANGELOG.md" or parts[:2] == ("docs", "archive"):
            historical.append(rel)
        else:
            current.append(rel)
    return sorted(current), sorted(historical)


def test_the_independent_denominator_is_nonzero_and_unique():
    current, historical = _independent_markdown_corpus()
    assert current, "the independent Markdown denominator collapsed to zero"
    assert len(current) == len(set(current))
    assert len(historical) == len(set(historical))
    assert not set(current) & set(historical)


def test_the_two_implementations_agree_exactly():
    """This is what replaces the literal: an EXACT denominator that is still
    independent. The retired `== 139` compared the corpus against a number a
    human typed; this compares it against a second derivation of the same rule,
    so a change to either side shows up as a named disagreement rather than as
    an off-by-one to be bumped away."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "row531_bdtools_sec", REPO / "toolchain" / "bin" / "bdtools_sec.py")
    sec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sec)

    deleted = set(subprocess.check_output(
        ["git", "ls-files", "--deleted"], cwd=str(REPO), text=True, timeout=60).splitlines())
    theirs_current, theirs_historical = sec.tracked_markdown_corpus(REPO)
    theirs_current = [p for p in theirs_current if p not in deleted]
    theirs_historical = [p for p in theirs_historical if p not in deleted]
    mine_current, mine_historical = _independent_markdown_corpus()

    assert set(theirs_current) == set(mine_current), (
        "the two Markdown corpus derivations disagree: only bdtools_sec "
        f"{sorted(set(theirs_current) - set(mine_current))}; only this file "
        f"{sorted(set(mine_current) - set(theirs_current))}")
    assert set(theirs_historical) == set(mine_historical), (
        "the two historical-corpus derivations disagree: only bdtools_sec "
        f"{sorted(set(theirs_historical) - set(mine_historical))}; only this "
        f"file {sorted(set(mine_historical) - set(theirs_historical))}")
    for rel in mine_current:
        assert (REPO / rel).is_file(), f"corpus names an absent file: {rel}"
