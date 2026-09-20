"""Row 881 -- AST-BASED-AUTOMATED-FOOTGUN-MUTANT-GENERATOR.

``bd-ast-mutate`` walks a target file's AST and emits a bd-mutate spec
(``bd-mutate-spec/1``) whose mutants target three designated invariant
classes: security boundary comparisons, credential-redactor mask literals,
and process exit codes. Random line deletion (the prior approach) mostly
produces syntax errors; walking the AST and rewriting one node with
``ast.unparse`` guarantees the emitted mutant still parses.

Acceptance (verbatim from the register row):
  1. the generator produces semantically valid mutants targeting the
     designated invariants;
  2. 100% of the produced mutants are caught by a real test suite exercising
     those invariants (run through the existing ``bd-mutate`` runner, never a
     reimplementation of it);
  3. zero invalid-syntax generations.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-ast-mutate"
_MUTATE = _REPO / "toolchain" / "bin" / "bd-mutate"


def _load_bam():
    """Import bd-ast-mutate (extensionless) as a module, for unit-level checks
    on its internal refusal guard -- the CLI-level tests below cannot trigger
    a no-op or an ambiguous-anchor candidate because the detectors never
    produce one from real source, so the guard itself needs a direct call."""
    loader = importlib.machinery.SourceFileLoader("bd_ast_mutate", str(_TOOL))
    spec = importlib.util.spec_from_loader("bd_ast_mutate", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module

_SUBJECT = '''\
import sys


def is_admin(role):
    if role == "admin":
        return True
    return False


def redact_token(token):
    """Mask a credential completely: the caller never sees any of it."""
    return "***"


def main(argv):
    if not argv:
        sys.exit(2)
    return 0
'''

_BAND = "tests/test_fixture_subject.py"

_BAND_SOURCE = '''\
import importlib
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import subject


def test_is_admin_boundary():
    importlib.reload(subject)
    assert subject.is_admin("admin") is True
    assert subject.is_admin("guest") is False


def test_redact_token_masks_exactly():
    importlib.reload(subject)
    assert subject.redact_token("secret") == "***"


def test_main_exit_code_on_empty_argv():
    importlib.reload(subject)
    try:
        subject.main([])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("main([]) did not exit")
'''


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "subject.py").write_text(_SUBJECT, encoding="utf-8")
    (tmp_path / _BAND).write_text(_BAND_SOURCE, encoding="utf-8")
    return tmp_path


def _run_generator(work: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    spec_path = work / "spec.json"
    return subprocess.run(
        [
            sys.executable, str(_TOOL),
            "--target", str(work / "subject.py"),
            "--repo-root", str(work),
            "--emit-spec", str(spec_path),
            "--subject", "row881 fixture: boundary/redactor/exit-code mutants",
            "--band", _BAND,
            *extra,
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_generator_targets_all_three_invariant_classes(tmp_path):
    work = _tree(tmp_path)
    run = _run_generator(work)
    assert run.returncode == 0, run.stdout + run.stderr

    spec = json.loads((work / "spec.json").read_text(encoding="utf-8"))
    assert spec["schema"] == "bd-mutate-spec/1"
    mutants = spec["mutants"]
    assert len(mutants) == 3, mutants        # one per designated site, no duplicates
    assert len({(m["file"], m.get("old_regex", m.get("old")), m["new"]) for m in mutants}) == 3

    labels = {m["label"] for m in mutants}
    kinds = {m["kind"] for m in mutants}
    assert "security_boundary" in kinds, labels
    assert "credential_redactor" in kinds, labels
    assert "exit_code" in kinds, labels

    for mutant in mutants:
        assert mutant["file"] == "subject.py"
        assert mutant["old"] != mutant["new"]
        assert _SUBJECT.count(mutant["old"]) == 1, mutant


def test_zero_invalid_syntax_generations(tmp_path):
    """Every emitted mutant, applied byte for byte, still parses (acceptance 3)."""
    import ast

    work = _tree(tmp_path)
    run = _run_generator(work)
    assert run.returncode == 0, run.stdout + run.stderr
    spec = json.loads((work / "spec.json").read_text(encoding="utf-8"))

    assert len(spec["mutants"]) > 0
    for mutant in spec["mutants"]:
        mutated_source = _SUBJECT.replace(mutant["old"], mutant["new"], 1)
        ast.parse(mutated_source)  # raises SyntaxError on an invalid mutant


def test_all_generated_mutants_are_caught_by_the_real_bd_mutate_runner(tmp_path):
    """Acceptance 2: run the emitted spec through the existing bd-mutate, not a copy of it."""
    work = _tree(tmp_path)
    gen = _run_generator(work)
    assert gen.returncode == 0, gen.stdout + gen.stderr

    spec_path = work / "spec.json"
    emitted = spec_path.read_bytes()
    spec = json.loads(emitted)
    # P1: the EMITTED bytes are what bd-mutate runs -- every mutant already carries its catcher.
    expected = {
        "security_boundary": f"{_BAND}::test_is_admin_boundary",
        "credential_redactor": f"{_BAND}::test_redact_token_masks_exactly",
        "exit_code": f"{_BAND}::test_main_exit_code_on_empty_argv",
    }
    for mutant in spec["mutants"]:
        assert mutant["catcher"] == expected[mutant["kind"]], mutant
        assert "_function" not in mutant
    assert spec_path.read_bytes() == emitted

    battery = subprocess.run(
        [sys.executable, str(_MUTATE), "--spec", str(spec_path), "--work", str(work), "--json"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert battery.returncode == 0, battery.stdout + battery.stderr
    start = battery.stdout.find("{")
    payload = json.loads(battery.stdout[start:])
    verdicts = {row["label"]: row["verdict"] for row in payload["rows"]}
    assert verdicts, payload
    assert all(v == "CAUGHT" for v in verdicts.values()), verdicts


def test_generator_refuses_a_target_outside_repo_root(tmp_path):
    work = _tree(tmp_path)
    outside = tmp_path.parent / "outside_subject.py"
    outside.write_text(_SUBJECT, encoding="utf-8")
    run = subprocess.run(
        [
            sys.executable, str(_TOOL),
            "--target", str(outside),
            "--repo-root", str(work),
            "--emit-spec", str(work / "spec2.json"),
            "--subject", "must refuse",
            "--band", _BAND,
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode != 0
    assert not (work / "spec2.json").exists()


def test_generator_refuses_a_target_with_no_designated_site(tmp_path):
    work = _tree(tmp_path)
    plain = work / "plain.py"
    plain.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    run = subprocess.run(
        [
            sys.executable, str(_TOOL),
            "--target", str(plain),
            "--repo-root", str(work),
            "--emit-spec", str(work / "spec3.json"),
            "--subject", "must refuse: no invariant site",
            "--band", _BAND,
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode != 0, run.stdout + run.stderr
    assert "no designated invariant site" in run.stderr
    assert not (work / "spec3.json").exists()


def test_prove_mutant_refuses_a_no_op_candidate():
    bam = _load_bam()
    try:
        bam._prove_mutant("x == 1", "x == 1", "x == 1", "t")
        raise AssertionError("no-op candidate was accepted")
    except bam.GeneratorRefused as exc:
        assert "no-op" in str(exc)


def test_prove_mutant_refuses_an_ambiguous_anchor():
    bam = _load_bam()
    try:
        bam._prove_mutant("a == 1\na == 1", "a == 1", "a != 1", "t")
        raise AssertionError("ambiguous anchor was accepted")
    except bam.GeneratorRefused as exc:
        assert "occurs 2 times" in str(exc)


def test_prove_mutant_refuses_a_mutant_that_does_not_parse():
    bam = _load_bam()
    try:
        bam._prove_mutant("if True:\n    pass\n", "True", "1 +", "t")
        raise AssertionError("a syntactically invalid mutant was accepted")
    except bam.GeneratorRefused as exc:
        assert "does not parse" in str(exc)


def test_documented_redactor_yields_only_behavioural_mutants(tmp_path):
    """P2: the redactor's docstring is documentation, not a mask literal -- no
    mutant may change only a docstring (nothing observable would differ)."""
    work = _tree(tmp_path)
    run = _run_generator(work)
    assert run.returncode == 0, run.stdout + run.stderr
    spec = json.loads((work / "spec.json").read_text(encoding="utf-8"))
    redactor = [m for m in spec["mutants"] if m["kind"] == "credential_redactor"]
    assert len(redactor) == 1, redactor
    assert redactor[0]["old"] == '"***"'
    assert "Mask a credential" not in redactor[0]["old"]
    # Every emitted mutant changes observable behaviour of the fixture subject.
    import importlib.util as _ilu
    for m in spec["mutants"]:
        mutated = _SUBJECT.replace(m["old"], m["new"], 1)
        ns_before, ns_after = {}, {}
        exec(compile(_SUBJECT, "subject", "exec"), ns_before)
        exec(compile(mutated, "subject", "exec"), ns_after)
        probes = [
            lambda ns: (ns["is_admin"]("admin"), ns["is_admin"]("guest")),
            lambda ns: ns["redact_token"]("secret"),
        ]
        def _exit(ns):
            try:
                ns["main"]([])
            except SystemExit as exc:
                return exc.code
            return None
        probes.append(_exit)
        assert any(pr(ns_before) != pr(ns_after) for pr in probes), m


def test_generator_refuses_when_no_band_test_can_catch(tmp_path):
    """A durable spec with an unbound mutant is not runnable: refuse, name it, and
    accept an explicit --catcher binding instead."""
    work = _tree(tmp_path)
    (work / _BAND).write_text("def test_unrelated():\n    assert True\n", encoding="utf-8")
    run = _run_generator(work)
    assert run.returncode == 2, run.stdout + run.stderr
    assert "no catcher in the band" in run.stderr
    assert not (work / "spec.json").exists()
    run = _run_generator(work, "--catcher", f"security_boundary={_BAND}::test_unrelated",
                         "--catcher", f"credential_redactor={_BAND}::test_unrelated",
                         "--catcher", f"exit_code={_BAND}::test_unrelated")
    assert run.returncode == 0, run.stdout + run.stderr
    spec = json.loads((work / "spec.json").read_text(encoding="utf-8"))
    assert all(m["catcher"] == f"{_BAND}::test_unrelated" for m in spec["mutants"])
    bad = _run_generator(work, "--catcher", "nonsense")
    assert bad.returncode == 2 and "KIND=NODEID" in bad.stderr


# ---- fixer (O928) controls: correctness REFUTE items 1-4 ----

def test_raise_system_exit_yields_exactly_one_exit_code_mutant(tmp_path):
    """Item 1: `raise SystemExit(2)` used to produce two identical mutants
    (ast.Raise and its child ast.Call were both matched)."""
    work = _tree(tmp_path)
    (work / "subject.py").write_text(_SUBJECT.replace("        sys.exit(2)", "        raise SystemExit(2)"),
                                     encoding="utf-8")
    run = _run_generator(work)
    assert run.returncode == 0, run.stdout + run.stderr
    mutants = json.loads((work / "spec.json").read_text(encoding="utf-8"))["mutants"]
    exits = [m for m in mutants if m["kind"] == "exit_code"]
    assert len(exits) == 1, exits


def test_catchers_inside_test_classes_get_class_qualified_nodeids(tmp_path):
    """Item 2: a test method inside `class TestX:` must be addressed as
    file::TestX::test_fn -- the bare file::test_fn does not collect."""
    work = _tree(tmp_path)
    src = _BAND_SOURCE.replace(
        "def test_main_exit_code_on_empty_argv():",
        "class TestExit:\n  def test_main_exit_code_on_empty_argv(self):").replace(
        "    importlib.reload(subject)\n    try:\n        subject.main([])",
        "    importlib.reload(subject)\n    try:\n        subject.main([])")
    (work / _BAND).write_text(src, encoding="utf-8")
    import importlib.machinery, importlib.util
    loader = importlib.machinery.SourceFileLoader("bd_ast_mutate", str(_TOOL))
    spec = importlib.util.spec_from_loader("bd_ast_mutate", loader)
    mod = importlib.util.module_from_spec(spec); loader.exec_module(mod)
    catchers = mod.derive_catchers([_BAND], work, "main")
    assert catchers == [f"{_BAND}::TestExit::test_main_exit_code_on_empty_argv"]
    # the nodeid collects
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "--co", catchers[0]],
                         cwd=work, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr


def test_band_entries_that_are_nodeids_are_honoured_not_skipped(tmp_path):
    """Item 3: `tests/x.py::test_fn` is a pytest nodeid, not a file; the file
    part is parsed and only that node is a candidate."""
    work = _tree(tmp_path)
    import importlib.machinery, importlib.util
    loader = importlib.machinery.SourceFileLoader("bd_ast_mutate", str(_TOOL))
    spec = importlib.util.spec_from_loader("bd_ast_mutate", loader)
    mod = importlib.util.module_from_spec(spec); loader.exec_module(mod)
    assert mod.derive_catchers([f"{_BAND}::test_main_exit_code_on_empty_argv"], work, "main") == [
        f"{_BAND}::test_main_exit_code_on_empty_argv"]
    assert mod.derive_catchers([f"{_BAND}::test_is_admin_boundary"], work, "main") == []
    assert mod.derive_catchers([_BAND, f"{_BAND}::test_main_exit_code_on_empty_argv"], work, "main") == [
        f"{_BAND}::test_main_exit_code_on_empty_argv"]   # no duplicate
