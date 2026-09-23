"""H360: the underived precut run's wall is an operator knob that names itself.

THE DEFECT. `bd-precut --gate` runs every repo-wide gate file in one pytest
subprocess under a fixed 1800 s wall. On 2026-09-14 five of five remote precuts
of one cut (row 648) hit that wall on 48-core pool hosts at load 36-42 and the
tool reported, correctly, a HANG naming no gate -- the run was simply not
finished, at [60%]..[98%]. There was no way to give the run more time short of
editing the tool, so every full band on a loaded host became COULD NOT LOOK.

THE CONTRACT. `BD_PRECUT_BUDGET_S` sets the wall, as an integer clamped to
[1800, 7200]. A non-integer or a value below the floor is IGNORED -- the default
applies -- and the tool says so in ONE bounded stderr line, because a silently
ignored knob is a trap. The timeout message quotes the budget actually used, so
a log names its own wall. Unset means 1800, silently: that is the negative
control, and it is what every existing caller relies on.

Module-scoped: the subject is one function of one tool and the two call sites
that consume it, not a property of the tree.
"""
from __future__ import annotations

import ast
import importlib.machinery
import io
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-precut"

DEFAULT_S = 1800
CEILING_S = 7200
ENV = "BD_PRECUT_BUDGET_S"

#: A nonzero population for the run site: the tool formats "over %d gate file(s)".
_PRESENT = [("tests/test_alpha_gate.py", "why-alpha"),
            ("tests/test_beta_gate.py", "why-beta")]


def _load():
    return importlib.machinery.SourceFileLoader(
        "bd_precut_h360_under_test", str(TOOL)).load_module()


def _budget(mod, environ):
    err = io.StringIO()
    got = mod._underived_budget_s(environ=environ, err=err)
    return got, err.getvalue()


@pytest.fixture()
def timing_out_run(monkeypatch):
    """subprocess.run that always exceeds its wall, recording the wall it was given."""
    mod = _load()
    seen = []

    def fake_run(argv, **kw):
        seen.append(kw.get("timeout"))
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout"))

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return mod, seen


def test_the_timeout_message_names_the_budget_actually_used(timing_out_run, monkeypatch, tmp_path):
    """RED on the fixed constant: the run is given 1800 and says 1800 whatever the operator set."""
    mod, seen = timing_out_run
    monkeypatch.setenv(ENV, "3600")
    assert len(_PRESENT) == 2, "precondition: the gate population must be nonzero"
    rc, detail = mod._run_underived_gates(str(tmp_path), _PRESENT, env={})
    assert rc == 124, (rc, detail)
    assert seen == [3600], (
        f"{ENV}=3600 was set but subprocess.run was given timeout={seen}; "
        "the wall is not the operator's budget")
    assert "did NOT finish within 3600s over 2 gate file(s)" in detail, (
        "the hang message must quote the budget the run was actually given, "
        "so a log names its wall: " + detail)


def test_unset_means_the_default_and_says_nothing(timing_out_run, monkeypatch, tmp_path):
    """Negative control: no variable -> 1800, no stderr, message says 1800."""
    mod, seen = timing_out_run
    monkeypatch.delenv(ENV, raising=False)
    got, said = _budget(mod, {})
    assert got == DEFAULT_S and said == "", (got, said)
    rc, detail = mod._run_underived_gates(str(tmp_path), _PRESENT, env={})
    assert rc == 124 and seen == [DEFAULT_S], (rc, seen)
    assert f"did NOT finish within {DEFAULT_S}s over 2 gate file(s)" in detail, detail


@pytest.mark.parametrize("raw, expected", [
    ("1800", 1800),      # the floor itself is a legal value
    ("3600", 3600),
    ("7200", 7200),      # the ceiling itself is a legal value
    (" 2400 ", 2400),    # surrounding whitespace is not a reason to ignore an int
])
def test_an_in_range_override_is_used_silently(raw, expected):
    mod = _load()
    got, said = _budget(mod, {ENV: raw})
    assert got == expected, (raw, got)
    assert said == "", f"an accepted value must not be narrated: {said!r}"


def test_above_the_ceiling_is_clamped_and_says_so():
    mod = _load()
    for raw in ("7201", "99999", "100000000"):
        got, said = _budget(mod, {ENV: raw})
        assert got == CEILING_S, (raw, got)
        assert said.count("\n") == 1 and "clamped" in said and ENV in said and raw in said, (raw, said)


@pytest.mark.parametrize("raw", ["1799", "0", "-1", "-1800"])
def test_below_the_floor_is_ignored_with_one_line_saying_so(raw):
    mod = _load()
    got, said = _budget(mod, {ENV: raw})
    assert got == DEFAULT_S, (raw, got)
    assert said.count("\n") == 1, f"exactly one stderr line, got: {said!r}"
    assert "ignored" in said and "floor" in said and ENV in said and raw in said, said
    assert f"{DEFAULT_S}s" in said, "the line must say which budget applies instead: " + said


@pytest.mark.parametrize("raw", ["soon", "", "1e3", "36 00", "3600s", "1800.0", "0x708"])
def test_a_non_integer_is_ignored_with_one_line_saying_so(raw):
    mod = _load()
    got, said = _budget(mod, {ENV: raw})
    assert got == DEFAULT_S, (raw, got)
    assert said.count("\n") == 1, f"exactly one stderr line, got: {said!r}"
    assert "ignored" in said and "integer" in said and ENV in said and repr(raw) in said, said
    assert f"{DEFAULT_S}s" in said, said


def test_the_ignored_line_is_bounded_even_for_a_huge_value():
    mod = _load()
    raw = "x" * 10_000
    got, said = _budget(mod, {ENV: raw})
    assert got == DEFAULT_S
    assert said.count("\n") == 1 and len(said) <= 300, len(said)
    assert "truncated" in said, "a cut line must SAY it was cut: " + said[:120]


def test_the_two_refusal_reasons_do_not_share_one_message():
    """Row 615's rule: two causes, two messages -- else a log cannot tell them apart."""
    mod = _load()
    _, below = _budget(mod, {ENV: "5"})
    _, nonint = _budget(mod, {ENV: "five"})
    assert below != nonint and below and nonint, (below, nonint)


@pytest.fixture()
def gate_tree(tmp_path):
    """The smallest tree main() judges to the underived run: a consistent release
    trio, ONE floor gate file present, no git, no tools/precut_check.py."""
    (tmp_path / "bulk_downloader").mkdir()
    (tmp_path / "bulk_downloader" / "__init__.py").write_text('__version__ = "9.9.9"\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_settings_center_slice4.py").write_text(
        'def test_pin():\n    assert __version__ == "9.9.9"\n')
    (tmp_path / "tests" / "test_row357_mutant_anchors_are_not_fragile.py").write_text(
        'BD_GATE_SCOPE = "repo-wide"\n')
    (tmp_path / "CHANGELOG.md").write_text("## v9.9.9\n- ascii only\n")
    return tmp_path


@pytest.fixture()
def children_that_time_out_in_pytest(monkeypatch):
    """Every child main() spawns answers rc 0 with empty output -- except the
    pytest child, which exceeds whatever wall it was given. Records the walls."""
    mod = _load()
    walls = []

    def fake_run(argv, **kw):
        if "pytest" in argv:
            walls.append(kw.get("timeout"))
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout"))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return mod, walls


_GATE_ARGV = ["--gate", "--no-insync", "--no-envscan", "--no-coretest", "--root"]


def test_the_gate_verdict_itself_names_the_operator_budget(gate_tree, children_that_time_out_in_pytest,
                                                           monkeypatch, capsys):
    """END TO END through main(): the seam the self-mutation battery found. The
    unit tests above call _run_underived_gates directly, so a mutant that deletes
    the CALL in main() (rc, detail = None) escaped them. This test drives the
    verdict the operator reads, so that mutant now fails loudly here."""
    mod, walls = children_that_time_out_in_pytest
    monkeypatch.setenv(ENV, "3600")
    rc = mod.main(_GATE_ARGV + [str(gate_tree)])
    out = capsys.readouterr().out
    assert rc == 3, (rc, out)
    assert walls == [3600], f"the pytest child was given timeout={walls}, not the operator's 3600"
    assert "RESULT: NOT CUT-READY" in out and "underived gate(s) FAILED" in out, out
    assert "did NOT finish within 3600s over 1 gate file(s)" in out, (
        "the verdict the operator reads must name the wall actually used: " + out)


def test_the_gate_verdict_names_1800s_when_the_variable_is_unset(gate_tree, children_that_time_out_in_pytest,
                                                                 monkeypatch, capsys):
    """Negative control for the end-to-end path: unset -> the default, said."""
    mod, walls = children_that_time_out_in_pytest
    monkeypatch.delenv(ENV, raising=False)
    rc = mod.main(_GATE_ARGV + [str(gate_tree)])
    out = capsys.readouterr().out
    assert rc == 3 and walls == [DEFAULT_S], (rc, walls)
    assert f"did NOT finish within {DEFAULT_S}s over 1 gate file(s)" in out, out


def _run_site(tree):
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_run_underived_gates")


def _is_budget_call(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "_underived_budget_s")


def _run_site_violations(tree):
    """The run-site property over a parsed bd-precut; [] when it holds.

    In _run_underived_gates, _underived_budget_s() is called exactly once and
    its result is bound to ONE local name, and every timeout= (subprocess.run)
    and timed_out_after= (_underived_detail) passes exactly that name. The
    local's SPELLING is not part of the property (H415): a pure rename holds,
    and the fixed constant at either site, or at both, does not.
    """
    fn = _run_site(tree)
    calls = [n for n in ast.walk(fn) if _is_budget_call(n)]
    if len(calls) != 1:
        return [(f"_run_underived_gates must derive its wall from _underived_budget_s() "
                 f"exactly once, found {len(calls)}")]
    bound = [a.targets[0].id for a in ast.walk(fn)
             if isinstance(a, ast.Assign) and a.value is calls[0]
             and len(a.targets) == 1 and isinstance(a.targets[0], ast.Name)]
    if not bound:
        return [("the one _underived_budget_s() call must be the whole value of a plain "
                 "`<local> = _underived_budget_s()` assignment")]
    bindings = sum(1 for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == bound[0]
                   and not isinstance(n.ctx, ast.Load))
    if bindings != 1:
        return [(f"the local {bound[0]!r} must be bound once, to _underived_budget_s(), and "
                 f"never rebound: the walls carry the budget, not the fixed constant or "
                 f"another value ({bindings} bindings)")]
    walls = [(kw.arg, ast.unparse(kw.value)) for n in ast.walk(fn) if isinstance(n, ast.Call)
             for kw in n.keywords if kw.arg in ("timeout", "timed_out_after")]
    violations = []
    if {arg for arg, _ in walls} != {"timeout", "timed_out_after"}:
        violations.append("both walls, timeout= and timed_out_after=, must be passed: "
                          + repr(sorted(walls)))
    stray = sorted((arg, got) for arg, got in walls if got != bound[0])
    if stray:
        violations.append(f"every wall must be the one local bound to _underived_budget_s() "
                          f"({bound[0]!r}), not the fixed constant or another value: {stray!r}")
    return violations


def test_the_run_site_consumes_the_budget_exactly_once():
    """Exact count over the tool's AST: in _run_underived_gates, subprocess.run's
    timeout= and _underived_detail's timed_out_after= are both the ONE local
    bound to _underived_budget_s(), whatever it is named, and the fixed constant
    is no longer passed at either site."""
    violations = _run_site_violations(ast.parse(TOOL.read_text(encoding="utf-8"), str(TOOL)))
    assert violations == [], violations


def _rename_the_budget_local(fn):
    """R-NEG-3: the local bound to _underived_budget_s() renamed at every site."""
    (assign,) = [a for a in ast.walk(fn) if isinstance(a, ast.Assign) and _is_budget_call(a.value)]
    old = assign.targets[0].id
    uses = [n for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == old]
    for node in uses:
        node.id = old + "_renamed"
    return len(uses)


def _the_constant_at(*walls):
    """The fixed constant passed at the named wall(s) instead of the local."""
    def mutate(fn):
        sites = [kw for n in ast.walk(fn) if isinstance(n, ast.Call)
                 for kw in n.keywords if kw.arg in walls]
        for kw in sites:
            kw.value = ast.Name(id="_UNDERIVED_BUDGET_S", ctx=ast.Load())
        return len(sites)
    return mutate


def _rebind_the_budget_local(fn):
    """The local rebound to the fixed constant after the budget call: every wall
    still names the local, but the value it carries is no longer the budget."""
    for holder in ast.walk(fn):
        body = getattr(holder, "body", None)
        if not isinstance(body, list):
            continue
        for index, stmt in enumerate(body):
            if isinstance(stmt, ast.Assign) and _is_budget_call(stmt.value):
                name = stmt.targets[0].id
                body.insert(index + 1, ast.Assign(
                    targets=[ast.Name(id=name, ctx=ast.Store())],
                    value=ast.Name(id="_UNDERIVED_BUDGET_S", ctx=ast.Load()), lineno=0))
                return 1
    return 0


@pytest.mark.parametrize("mutate, holds", [
    pytest.param(_rename_the_budget_local, True, id="R-NEG-3-pure-rename"),
    pytest.param(_the_constant_at("timeout"), False, id="constant-at-timeout"),
    pytest.param(_the_constant_at("timed_out_after"), False, id="constant-at-timed_out_after"),
    pytest.param(_the_constant_at("timeout", "timed_out_after"), False, id="constant-at-both"),
    pytest.param(_rebind_the_budget_local, False, id="local-rebound-to-constant"),
])
def test_the_run_site_check_follows_the_property_not_the_spelling(mutate, holds):
    """H415 (lens rowh360b NOTE 2): the check above states the property, so a
    pure rename of the local holds -- it used to turn the gate red with a message
    denying what was true of the tree -- while the fixed constant at either site,
    or at both, is still refused. Each mutant edits the real tool's AST."""
    tree = ast.parse(TOOL.read_text(encoding="utf-8"), str(TOOL))
    assert mutate(_run_site(tree)) >= 1, "the mutant changed nothing, so it proves nothing"
    violations = _run_site_violations(tree)
    if holds:
        assert violations == [], violations
    else:
        assert any("not the fixed constant" in v for v in violations), violations
