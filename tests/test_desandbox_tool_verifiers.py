"""bd-mutation-test and bd-band-derive verified nothing, because they were aimed
at a sandbox that does not exist on this tree.

Both tools' ENGINES are sound -- bd-mutation-test's four-state discipline
(CAUGHT / SURVIVED / UNKNOWN / BASELINE-RED) and bd-band-derive's ~19 selftest
controls are real work. What was broken is where they pointed:

  bd-mutation-test  11 registry gates shelled `python3` -- the container's 3.11
                    WITHOUT the project deps, so three of the load-bearing gate
                    rows (route_index, route_map, body_contract) went
                    BASELINE-RED purely on the interpreter and could never
                    report SURVIVED. 4 rows copied src="/home/claude/bin", a
                    path absent here, and crashed in shutil.copytree.
  bd-band-derive    selftest() hardcoded /home/claude/work in TWO places
                    (:750 and :1079), so ~19 controls whose own docstring says
                    they "WOULD HAVE CAUGHT the constant-band bug this tool
                    shipped with" printed `SKIP  no work tree` and PASSED
                    without executing.

THE SHAPE, and why it is this project's recurring one: a verifier that cannot
reach its subject reports success. bd-band-derive's SKIP->PASS is CLAUDE.md
section 0 exactly -- and it is worse than an ordinary blind gate because the
thing it fails to verify is OTHER gates' blast radius.

THREE ANCHORS WERE ALSO ROTTEN, which is the second half of the fix. A mutation
anchor that matches 442 sites and is applied with count=1 mutates whichever site
re.subn reaches first -- not the one the row names -- so the row's CAUGHT verdict
is evidence about a different location. Measured on pristine:

    ROUTE_INDEX.json            '"spa_wired": true'  -> 442 matches, count=1
    tools/BODY_CONTRACT_CALLS   '"/api/'             -> 259 matches, count=1
    live_tests/checks.py        (L34 exceeded)       ->   0 matches, count=1

The third is ROT: it matches nothing, so the row can only ever return UNKNOWN.
A3 below fails on all three and passes only when each anchor resolves to exactly
the count the row declares.

A3 IS AST-BASED ON PURPOSE. Reading row["mutate"].pattern off the loaded closure
would require first patching the shared _sub helper -- which would make this
assertion untestable against pristine source, and couple an anchor-uniqueness
check to an unrelated edit. It parses the REGISTRY literal instead, so it runs
identically before and after the fix.

A3 IS ALSO FAIL-CLOSED. Every registry row must resolve to an existing target
file and a parseable _sub(pattern, ..., count); anything unrecognised FAILS
rather than being skipped. A row pointing at a renamed file must not be silently
certified -- unknown is a third state and it fails.

RED-first: A1, A2, A3, A5, A6 and B1 all fail on pristine source. A4 is a
labelled regression guard that passes today and is not counted as RED.
"""
from __future__ import annotations

import ast
import errno
import hashlib
import inspect
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# MEASURED PER CALL SITE ON THIS HOST, v3.66.1222, one test per process, idle
# test5 (48 cores), load 0.41-0.46 recorded per run:
#
#     mutation-engine-selftest      2s   (budget was 300)
#     real-gate-row-end-to-end     38s   (budget was 600, item bound 240)
#     band-derive-selftest          6s   (budget was 600)
#
# THE END-TO-END ROW MEASURED 201s BEFORE THIS CUT AND 38s AFTER, and the
# difference is not the mutation -- it is what the tool was pointed at. Against
# the LIVE repository it carried whatever else sits in the working directory;
# against a detached clone it carries tracked files only. Fixing the snapshot
# race made the row 5.3x faster as a side effect, so the 201s figure is kept
# here only to explain why the budget is no longer 402s.
#
# THE OLD TABLE WAS KEYED BY TOOL AND THAT IS THE DEFECT. `bd-mutation-test`
# takes 2s as `--selftest` and 201s as `--only route_index/spa_wired`; one key
# cannot describe both, so the ratio printed on failure was wrong by 8x. The
# cost is a property of the INVOCATION, so the baseline is too.
#
# AND THE 25.8s WAS MEASURED IN A CLOUD SANDBOX, from which the previous note
# reasoned that a run exceeding 600s "hung rather than ran slowly". On this box
# the same row takes 201 SECONDS with nothing else running. It is slow, not
# stuck -- and 201s against the sanctioned `--timeout=240` leaves 39s of
# headroom, which is what made it the second failure of the 2026-08-24 wedge.
# See fleet-run-artifacts/2026-08-24/xdist-wedge/FINDING.md.
_MEASURED_S = {
    "mutation-engine-selftest": 2,
    "real-gate-row-end-to-end": 38,
    "band-derive-selftest": 6,
}

# Same arithmetic v3.66.1219 established for the tool-state gate, and for the
# same reason: an inner budget that cannot fire before the bound governing its
# item has a dead `except` clause, and what runs instead is pytest-timeout
# killing the worker.
_CONTENTION_FACTOR = 2.0
_ITEM_RESERVE_S = 30
_MIN_BUDGET_S = 60


def _budget_s(site):
    """The subprocess budget for one call site -- always below its item bound."""
    return max(_MIN_BUDGET_S, int(_MEASURED_S[site] * _CONTENTION_FACTOR))


def _item_timeout_s(site):
    """The pytest-timeout bound governing that site's item, set EXPLICITLY.

    Every site now lands well inside the sanctioned 240s -- the end-to-end row
    needed 432s while it was mutating the live tree and needs 106s against a
    detached copy. Stating each bound explicitly keeps the relationship the
    tests depend on visible instead of inherited, and makes a future regression
    in either direction obvious.
    """
    return _budget_s(site) + _ITEM_RESERVE_S

BIN = REPO / "toolchain" / "bin"
MT = BIN / "bd-mutation-test"
BD = BIN / "bd-band-derive"
_PYTEST_FAILURE = getattr(pytest.fail, "Exception", AssertionError)


def _load_mt():
    """Load bd-mutation-test as a module. Its main() is __main__-guarded, so
    importing has no side effects."""
    import importlib.machinery
    import importlib.util
    spec = importlib.util.spec_from_loader(
        "bd_mutation_test_under_test",
        importlib.machinery.SourceFileLoader(
            "bd_mutation_test_under_test", str(MT)))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BIN))
    try:
        spec.loader.exec_module(mod)
    finally:
        if str(BIN) in sys.path:
            sys.path.remove(str(BIN))
    return mod


def _load_bd_mutate(path=BIN / "bd-mutate"):
    import importlib.machinery
    import importlib.util
    name = "bd_mutate_under_test_%s" % os.getpid()
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(path)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _descriptor_identity(fstat, fd):
    observed = fstat(fd)
    return (fd, observed.st_dev, observed.st_ino,
            stat.S_IFMT(observed.st_mode))


def _close_if_same_owner(original_fstat, original_close, expected):
    try:
        current = _descriptor_identity(original_fstat, expected[0])
    except OSError:
        return
    if current == expected:
        original_close(expected[0])


def _assert_owner_is_settled(original_fstat, expected):
    try:
        current = _descriptor_identity(original_fstat, expected[0])
    except OSError:
        return
    assert current != expected, (
        f"descriptor owner remains open after settlement: {expected!r}")


def _close_call_owners(path):
    """Return the non-empty enclosing-function census for explicit closes."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    owners = []

    class CloseVisitor(ast.NodeVisitor):
        def __init__(self):
            self.functions = []

        def visit_FunctionDef(self, node):
            self.functions.append(node.name)
            self.generic_visit(node)
            self.functions.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            function = node.func
            if (isinstance(function, ast.Attribute) and
                    function.attr == "close" and
                    isinstance(function.value, ast.Name) and
                    function.value.id == "os"):
                assert self.functions, (
                    f"module-level os.close is outside an owner funnel: {path}")
                owners.append(self.functions[-1])
            self.generic_visit(node)

    CloseVisitor().visit(tree)
    assert owners, f"explicit os.close denominator is empty: {path}"
    return owners


def test_mutation_tool_fd_closes_are_centralized_in_one_funnel_per_tool():
    observed = {
        str(path.relative_to(REPO)): _close_call_owners(path)
        for path in (BIN / "bd-mutate", BIN / "bd-mutation-test")
    }

    assert observed == {
        "toolchain/bin/bd-mutate": ["_settle_owned_fds"],
        "toolchain/bin/bd-mutation-test": ["_settle_descriptors"],
    }


# ── the registry must not re-sandbox the interpreter or the tree ─────────────

def test_no_gate_reruns_the_wrong_interpreter():
    """RED. Pristine: 11 rows shell `python3`, which on this container is 3.11
    without the project dependencies -- so the gate fails to import flask and
    the row reports BASELINE-RED regardless of the mutation.

    The `/home/` clause is the cry-wolf half: baking an absolute interpreter
    path into a gate would work on one machine and break every other, so the
    placeholder must survive rather than being resolved at edit time.
    """
    mod = _load_mt()
    bad = []
    for row in mod.REGISTRY:
        gate = row.get("gate") or ""
        if not gate:
            continue
        if gate.split()[0] == "python3":
            bad.append(f"{row['id']}: gate shells python3 -- {gate[:60]}")
        if "/home/" in gate:
            bad.append(f"{row['id']}: gate bakes an absolute path -- {gate[:60]}")
    assert not bad, (
        "these gates cannot execute against this tree's interpreter:\n  "
        + "\n  ".join(bad))


def test_no_row_copies_a_tree_that_does_not_exist():
    """RED. Pristine: 4 rows set src="/home/claude/bin", which is absent here,
    so the row dies in shutil.copytree -- bypassing the tool's own doctrine
    that an UNKNOWN must be reported rather than raised."""
    mod = _load_mt()
    bad = []
    for row in mod.REGISTRY:
        src = row.get("src")
        if not src:
            continue
        # The property is "this directory EXISTS", not "this string does not
        # contain /home/". A first draft used the substring and fired on the
        # CORRECT fixed value, because this checkout lives under /home/user --
        # a gate failing on identity rather than behaviour, which is section 0's
        # inverse and gets gates switched off. The /home/claude clause below is
        # deliberately narrow: it names the one dead sandbox, so it cannot fire
        # on a real tree that happens to sit under some other /home path.
        if str(src).startswith("/home/claude"):
            bad.append(f"{row['id']}: src is the retired sandbox -- {src}")
        elif not os.path.isdir(str(src)):
            bad.append(f"{row['id']}: src does not exist -- {src}")
    assert not bad, (
        "these rows copy a source tree that is not present:\n  "
        + "\n  ".join(bad))


def test_mutation_snapshot_excludes_untracked_runtime_entries(tmp_path):
    """A transient untracked entry cannot enter the mutation subject.

    The exact fleet failure this catches was an untracked repo-root probe that
    disappeared after copytree enumerated it. The gate's baseline requires the
    runtime entry to be absent, so the old unrestricted copy fails without a
    timing race.
    """
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    (source / "gate.py").write_text(
        "from pathlib import Path\n"
        "ok = (Path('feature.txt').read_text() == 'PRESENT\\n' and "
        "not Path('_u27_secret_probe.py').exists())\n"
        "raise SystemExit(0 if ok else 1)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "feature.txt", "gate.py"],
                   cwd=source, check=True)
    transient = source / "_u27_secret_probe.py"
    transient.write_text("runtime-only\n", encoding="utf-8")
    assert transient.exists()
    row = {
        "id": "snapshot/untracked-runtime-entry",
        "why": "fixture",
        "target": "feature.txt",
        "mutate": lambda text: text.replace("PRESENT", "ABSENT"),
        "gate": "{py} gate.py",
    }

    result = mod.check(row, str(source))

    assert result["state"] == "CAUGHT", result
    assert transient.read_text(encoding="utf-8") == "runtime-only\n"
    assert (source / "feature.txt").read_text(encoding="utf-8") == "PRESENT\n"


@pytest.mark.parametrize(
    "dependency,write_phase",
    (("venv", "baseline"), ("venv", "mutant"),
     ("frontend/node_modules", "baseline"),
     ("frontend/node_modules", "mutant")),
    ids=("python-baseline", "python-mutant",
         "node-baseline", "node-mutant"),
)
def test_mutation_dependency_write_never_reaches_source(
        tmp_path, dependency, write_phase):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    dep = source / dependency
    dep.mkdir(parents=True)
    (dep / "identity.txt").write_text("SEALED\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    calls = tmp_path / "gate-calls"
    rel = shlex.quote(dependency)
    value = "PRESENT" if write_phase == "baseline" else "ABSENT"
    gate = (
        f"printf x >> {shlex.quote(str(calls))}; "
        f"if grep -q {value} feature.txt; then "
        f"printf polluted > {rel}/source-polluted; fi; "
        "grep -q PRESENT feature.txt"
    )
    row = {
        "id": f"snapshot/private-{dependency}",
        "why": "fixture",
        "target": "feature.txt",
        "mutate": lambda text: text.replace("PRESENT", "ABSENT"),
        "gate": gate,
    }

    result = mod.check(row, str(source))

    assert result["state"] == "UNKNOWN", result
    assert dependency in result["detail"]
    expected_calls = "x" if write_phase == "baseline" else "xx"
    assert calls.read_text(encoding="utf-8") == expected_calls
    assert not (dep / "source-polluted").exists()
    assert (dep / "identity.txt").read_text(encoding="utf-8") == "SEALED\n"


def test_mutation_baseline_red_cannot_hide_a_dependency_write(tmp_path):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    dep = source / "venv"
    dep.mkdir()
    (dep / "identity.txt").write_text("SEALED\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    row = {
        "id": "snapshot/baseline-red-dependency-write",
        "why": "fixture",
        "target": "feature.txt",
        "mutate": lambda text: text.replace("PRESENT", "ABSENT"),
        "gate": "printf polluted > venv/source-polluted; false",
    }

    result = mod.check(row, str(source))

    assert result["state"] == "UNKNOWN", result
    assert "snapshot integrity failed" in result["detail"]
    assert "venv" in result["detail"]
    assert not (dep / "source-polluted").exists()
    assert (dep / "identity.txt").read_text(encoding="utf-8") == "SEALED\n"


@pytest.mark.parametrize("dependency", ("venv", "frontend/node_modules"))
def test_mutation_dependency_authority_rejects_an_external_symlink(
        tmp_path, dependency):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "identity.txt").write_text("EXTERNAL\n", encoding="utf-8")
    authority = source / dependency
    authority.parent.mkdir(parents=True, exist_ok=True)
    authority.symlink_to(outside, target_is_directory=True)
    calls = []
    row = {
        "id": f"snapshot/external-{dependency}",
        "why": "fixture",
        "target": "feature.txt",
        "mutate": lambda text: text.replace("PRESENT", "ABSENT"),
        "gate": "true",
    }
    original_run = mod._run
    mod._run = lambda *args, **kwargs: calls.append(args) or original_run(
        *args, **kwargs)
    try:
        result = mod.check(row, str(source))
    finally:
        mod._run = original_run

    assert result["state"] == "UNKNOWN", result
    assert dependency in result["detail"]
    assert calls == []
    assert (outside / "identity.txt").read_text(encoding="utf-8") == "EXTERNAL\n"


@pytest.mark.parametrize("write_phase", ("baseline", "mutant"))
def test_mutation_tracked_write_is_unknown_not_caught(tmp_path, write_phase):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    (source / "guard.txt").write_text("CLEAN\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt", "guard.txt"],
                   cwd=source, check=True)
    calls = tmp_path / "gate-calls"
    write_marker = "PRESENT" if write_phase == "baseline" else "ABSENT"
    gate = (
        f"printf x >> {shlex.quote(str(calls))}; "
        f"if grep -q {write_marker} feature.txt; then "
        "printf 'DIRTY\\n' > guard.txt; fi; "
        "grep -q CLEAN guard.txt"
    )
    row = {
        "id": f"snapshot/{write_phase}-tracked-write",
        "why": "fixture",
        "target": "feature.txt",
        "mutate": lambda text: text.replace("PRESENT", "ABSENT"),
        "gate": gate,
    }

    result = mod.check(row, str(source))

    assert result["state"] == "UNKNOWN", result
    assert "snapshot integrity failed" in result["detail"]
    assert "tracked snapshot changed during gate" in result["detail"]
    assert "guard.txt" in result["detail"]
    expected_calls = "x" if write_phase == "baseline" else "xx"
    assert calls.read_text(encoding="utf-8") == expected_calls
    assert (source / "guard.txt").read_text(encoding="utf-8") == "CLEAN\n"


def test_mutation_baseline_red_cannot_hide_a_tracked_write(tmp_path):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    (source / "guard.txt").write_text("CLEAN\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt", "guard.txt"],
                   cwd=source, check=True)
    row = {
        "id": "snapshot/baseline-red-tracked-write",
        "why": "fixture",
        "target": "feature.txt",
        "mutate": lambda text: text.replace("PRESENT", "ABSENT"),
        "gate": "printf 'DIRTY\\n' > guard.txt; false",
    }

    result = mod.check(row, str(source))

    assert result["state"] == "UNKNOWN", result
    assert "tracked snapshot changed during gate" in result["detail"]
    assert "guard.txt" in result["detail"]
    assert (source / "guard.txt").read_text(encoding="utf-8") == "CLEAN\n"


@pytest.mark.parametrize("link_kind", ("absolute", "relative"))
def test_mutation_dependency_nested_escape_is_unknown_without_source_write(
        tmp_path, link_kind):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    dependency = source / "frontend" / "node_modules"
    dependency.mkdir(parents=True)
    outside = source / "outside-owned"
    outside.mkdir()
    target = (str(outside) if link_kind == "absolute" else
              os.path.relpath(outside, dependency))
    (dependency / "escape").symlink_to(target, target_is_directory=True)
    calls = tmp_path / "gate-calls"
    row = {
        "id": f"snapshot/nested-{link_kind}-escape",
        "why": "fixture",
        "target": "feature.txt",
        "mutate": lambda text: text.replace("PRESENT", "ABSENT"),
        "gate": (
            f"printf x >> {shlex.quote(str(calls))}; "
            "printf polluted > frontend/node_modules/escape/source-polluted; "
            "grep -q PRESENT feature.txt"
        ),
    }

    result = mod.check(row, str(source))

    assert result["state"] == "UNKNOWN", result
    assert "external dependency link" in result["detail"]
    assert not calls.exists()
    assert not (outside / "source-polluted").exists()


def test_mutation_snapshot_preserves_a_tracked_worktree_deletion(tmp_path):
    """An already-absent cached path is a legitimate working-tree state."""
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    deleted = source / "deleted.txt"
    deleted.write_text("tracked then removed\n", encoding="utf-8")
    (source / "gate.py").write_text(
        "from pathlib import Path\n"
        "ok = (Path('feature.txt').read_text() == 'PRESENT\\n' and "
        "not Path('deleted.txt').exists())\n"
        "raise SystemExit(0 if ok else 1)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "feature.txt", "deleted.txt", "gate.py"],
                   cwd=source, check=True)
    deleted.unlink()
    assert not deleted.exists()
    row = {
        "id": "snapshot/tracked-deletion",
        "why": "fixture",
        "target": "feature.txt",
        "mutate": lambda text: text.replace("PRESENT", "ABSENT"),
        "gate": "{py} gate.py",
    }

    result = mod.check(row, str(source))

    assert result["state"] == "CAUGHT", result
    assert not deleted.exists()
    assert (source / "feature.txt").read_text(encoding="utf-8") == "PRESENT\n"


def _snapshot_fixture_row(target="feature.txt"):
    return {
        "id": "snapshot/trust-boundary",
        "why": "fixture",
        "target": target,
        "mutate": lambda text: text.replace("PRESENT", "ABSENT"),
        "gate": "true",
    }


def test_mutation_snapshot_rejects_a_regular_target_replaced_by_symlink(
        tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    target = source / "feature.txt"
    target.write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("PRESENT\n", encoding="utf-8")
    target.unlink()
    target.symlink_to(outside)
    gate_calls = []
    monkeypatch.setattr(mod, "_run",
                        lambda *args: gate_calls.append(args) or (0, ""))

    result = mod.check(_snapshot_fixture_row(), str(source))

    assert result["state"] == "UNKNOWN", result
    assert gate_calls == []
    assert outside.read_text(encoding="utf-8") == "PRESENT\n"


def test_mutation_snapshot_rejects_a_symlinked_parent_component(
        tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    tracked_parent = source / "owned"
    tracked_parent.mkdir(parents=True)
    target = tracked_parent / "feature.txt"
    target.write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "owned/feature.txt"], cwd=source, check=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / "feature.txt"
    outside_target.write_text("PRESENT\n", encoding="utf-8")
    target.unlink()
    tracked_parent.rmdir()
    tracked_parent.symlink_to(outside, target_is_directory=True)
    gate_calls = []
    monkeypatch.setattr(mod, "_run",
                        lambda *args: gate_calls.append(args) or (0, ""))

    result = mod.check(_snapshot_fixture_row("owned/feature.txt"), str(source))

    assert result["state"] == "UNKNOWN", result
    assert gate_calls == []
    assert outside_target.read_text(encoding="utf-8") == "PRESENT\n"


def test_mutation_snapshot_rejects_a_tracked_symlink(tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("PRESENT\n", encoding="utf-8")
    (source / "feature.txt").symlink_to(outside)
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    gate_calls = []
    monkeypatch.setattr(mod, "_run",
                        lambda *args: gate_calls.append(args) or (0, ""))

    result = mod.check(_snapshot_fixture_row(), str(source))

    assert result["state"] == "UNKNOWN", result
    assert "unsupported Git index mode '120000'" in result["detail"], result
    assert gate_calls == []
    assert outside.read_text(encoding="utf-8") == "PRESENT\n"


def test_mutation_snapshot_refuses_an_unmarked_non_git_subject(
        tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    gate_calls = []
    monkeypatch.setattr(mod, "_run",
                        lambda *args: gate_calls.append(args) or (0, ""))

    result = mod.check(_snapshot_fixture_row(), str(source))

    assert result["state"] == "UNKNOWN", result
    assert gate_calls == []


def test_snapshot_failure_is_one_structured_unknown_json_result(
        tmp_path, monkeypatch, capsys):
    import json

    mod = _load_mt()
    source = tmp_path / "not-git"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    row = _snapshot_fixture_row()
    monkeypatch.setattr(mod, "REGISTRY", [row])
    monkeypatch.setattr(
        sys, "argv",
        [str(MT), "--only", row["id"], "--work", str(source), "--json"],
    )
    gate_calls = []
    monkeypatch.setattr(mod, "_run",
                        lambda *args: gate_calls.append(args) or (0, ""))

    rc = mod.main()
    streams = capsys.readouterr()
    payload = json.loads(streams.out)

    assert rc == 1
    assert streams.err == ""
    assert payload["total"] == len(payload["results"]) == 1
    assert payload["caught"] == 0
    assert payload["failing"] == [row["id"]]
    assert payload["results"][0]["state"] == "UNKNOWN"
    assert gate_calls == []


@pytest.mark.parametrize(
    "failure_point", ("fstat", "mkdir", "destination_open"))
def test_mutation_snapshot_closes_source_root_when_setup_fails(
        tmp_path, monkeypatch, failure_point):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    destination = tmp_path / f"snapshot-{failure_point}"
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    original_mkdir = os.mkdir
    source_fds = []
    closed_fds = []
    injected_fstat = []

    def recording_open(path, flags, *args, **kwargs):
        if (failure_point == "destination_open" and
                os.fspath(path) == str(destination)):
            raise PermissionError("injected destination open failure")
        fd = original_open(path, flags, *args, **kwargs)
        if os.fspath(path) == str(source):
            source_fds.append(fd)
        return fd

    def failing_fstat(fd):
        if (failure_point == "fstat" and fd in source_fds and
                not injected_fstat):
            injected_fstat.append(fd)
            raise PermissionError("injected source fstat failure")
        return original_fstat(fd)

    def recording_close(fd):
        closed_fds.append(fd)
        return original_close(fd)

    def failing_mkdir(path, *args, **kwargs):
        if failure_point == "mkdir" and os.fspath(path) == str(destination):
            raise PermissionError("injected destination mkdir failure")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(mod.os, "open", recording_open)
    monkeypatch.setattr(mod.os, "close", recording_close)
    monkeypatch.setattr(mod.os, "fstat", failing_fstat)
    monkeypatch.setattr(mod.os, "mkdir", failing_mkdir)

    with pytest.raises(PermissionError):
        mod._snapshot_tree(str(source), str(destination))

    assert len(source_fds) == 1
    assert bool(injected_fstat) == (failure_point == "fstat")
    try:
        assert source_fds[0] in closed_fds
    finally:
        if source_fds[0] not in closed_fds:
            original_close(source_fds[0])


def test_mutation_snapshot_allocates_manifest_before_owning_source_root(
        tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    destination = tmp_path / "snapshot"
    original_open = os.open
    original_close = os.close
    source_fds = []
    lines, first_line = inspect.getsourcelines(mod._snapshot_tree)
    manifest_line = first_line + next(
        index for index, line in enumerate(lines)
        if line.strip() == "manifest = {}"
    )

    def recording_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if os.fspath(path) == str(source):
            source_fds.append(fd)
        return fd

    def fail_at_manifest(frame, event, _arg):
        if (frame.f_code is mod._snapshot_tree.__code__ and event == "line" and
                frame.f_lineno == manifest_line):
            raise RuntimeError("injected manifest setup failure")
        return fail_at_manifest

    monkeypatch.setattr(mod.os, "open", recording_open)
    sys.settrace(fail_at_manifest)
    try:
        with pytest.raises(RuntimeError, match="manifest setup failure"):
            mod._snapshot_tree(str(source), str(destination))
    finally:
        sys.settrace(None)
        for fd in source_fds:
            try:
                original_close(fd)
            except OSError:
                pass

    assert source_fds == []


@pytest.mark.parametrize("layer", ("leaf", "root"))
def test_mutation_snapshot_close_failure_does_not_skip_later_owned_fd(
        tmp_path, monkeypatch, layer):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    destination = tmp_path / f"snapshot-{layer}"
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    owned = {"source_root": [], "destination_root": [],
             "source_leaf": [], "destination_leaf": []}
    close_attempts = []
    injected = []

    def recording_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        opened = original_fstat(fd)
        identity = (fd, opened.st_dev, opened.st_ino,
                    stat.S_IFMT(opened.st_mode))
        raw_path = os.fspath(path)
        if raw_path == str(source):
            owned["source_root"].append(identity)
        elif raw_path == str(destination):
            owned["destination_root"].append(identity)
        elif raw_path == b"feature.txt":
            access = flags & os.O_ACCMODE
            key = ("source_leaf" if access == os.O_RDONLY
                   else "destination_leaf")
            owned[key].append(identity)
        return fd

    def close_first_then_raise(fd):
        current = original_fstat(fd)
        identity = (fd, current.st_dev, current.st_ino,
                    stat.S_IFMT(current.st_mode))
        close_attempts.append(identity)
        target = owned[f"destination_{layer}"]
        if target and identity == target[0] and not injected:
            original_close(fd)
            injected.append(identity)
            raise OSError(f"injected destination {layer} close failure")
        return original_close(fd)

    monkeypatch.setattr(mod.os, "open", recording_open)
    monkeypatch.setattr(mod.os, "close", close_first_then_raise)

    try:
        with pytest.raises(OSError, match=f"destination {layer} close"):
            mod._snapshot_tree(str(source), str(destination))

        assert len(injected) == 1
        assert len(owned[f"source_{layer}"]) == 1
        assert owned[f"source_{layer}"][0] in close_attempts
    finally:
        for descriptors in owned.values():
            for identity in descriptors:
                if identity not in close_attempts:
                    fd = identity[0]
                    try:
                        current = original_fstat(fd)
                    except OSError:
                        continue
                    current_identity = (fd, current.st_dev, current.st_ino,
                                        stat.S_IFMT(current.st_mode))
                    if current_identity == identity:
                        original_close(fd)


@pytest.mark.parametrize("layer", ("leaf", "root"))
def test_mutation_snapshot_cleanup_failure_preserves_primary_error(
        tmp_path, monkeypatch, layer):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    destination = tmp_path / f"snapshot-{layer}"
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    original_read = os.read
    original_git_index = mod._git_index
    owned = {"source_root": [], "destination_root": [],
             "source_leaf": [], "destination_leaf": []}
    close_attempts = []
    primary_injected = []
    close_injected = []
    index_calls = []

    def recording_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        opened = original_fstat(fd)
        identity = (fd, opened.st_dev, opened.st_ino,
                    stat.S_IFMT(opened.st_mode))
        raw_path = os.fspath(path)
        if raw_path == str(source):
            owned["source_root"].append(identity)
        elif raw_path == str(destination):
            owned["destination_root"].append(identity)
        elif raw_path == b"feature.txt":
            access = flags & os.O_ACCMODE
            key = ("source_leaf" if access == os.O_RDONLY
                   else "destination_leaf")
            owned[key].append(identity)
        return fd

    def failing_read(fd, size):
        if layer == "leaf" and owned["source_leaf"] and not primary_injected:
            current = original_fstat(fd)
            identity = (fd, current.st_dev, current.st_ino,
                        stat.S_IFMT(current.st_mode))
            if identity == owned["source_leaf"][0]:
                primary_injected.append(identity)
                raise OSError(errno.EIO, "injected source read failure")
        return original_read(fd, size)

    def failing_second_index(root):
        result = original_git_index(root)
        index_calls.append(result)
        if layer == "root" and len(index_calls) == 2:
            primary_injected.append("index")
            raise OSError(errno.EIO, "injected post-copy index failure")
        return result

    def close_then_raise(fd):
        current = original_fstat(fd)
        identity = (fd, current.st_dev, current.st_ino,
                    stat.S_IFMT(current.st_mode))
        close_attempts.append(identity)
        original_close(fd)
        target = owned[f"destination_{layer}"]
        if target and identity == target[0] and not close_injected:
            close_injected.append(identity)
            raise OSError(errno.EBADF, "injected cleanup close failure")

    monkeypatch.setattr(mod.os, "open", recording_open)
    monkeypatch.setattr(mod.os, "close", close_then_raise)
    monkeypatch.setattr(mod.os, "read", failing_read)
    monkeypatch.setattr(mod, "_git_index", failing_second_index)

    primary = ("source read failure" if layer == "leaf"
               else "post-copy index failure")
    error_type = mod._SnapshotError if layer == "leaf" else OSError
    try:
        with pytest.raises(error_type, match=primary):
            mod._snapshot_tree(str(source), str(destination))

        assert len(primary_injected) == 1
        assert len(close_injected) == 1
        assert owned[f"destination_{layer}"][0] in close_attempts
        assert owned[f"source_{layer}"][0] in close_attempts
    finally:
        for descriptors in owned.values():
            for identity in descriptors:
                try:
                    current = original_fstat(identity[0])
                except OSError:
                    continue
                current_identity = (
                    identity[0], current.st_dev, current.st_ino,
                    stat.S_IFMT(current.st_mode),
                )
                if current_identity == identity:
                    original_close(identity[0])


@pytest.mark.parametrize(
    "helper,nested",
    (("source", False), ("source", True),
     ("destination", False), ("destination", True)),
    ids=("source-leaf", "source-directory",
         "destination-leaf", "destination-directory"),
)
def test_mutation_snapshot_parent_close_failure_closes_unpublished_child(
        tmp_path, monkeypatch, helper, nested):
    mod = _load_mt()
    root = tmp_path / helper
    root.mkdir()
    relative = b"owned/feature.txt" if nested else b"feature.txt"
    if helper == "source":
        target = root / os.fsdecode(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("PRESENT\n", encoding="utf-8")
    original_open = os.open
    original_close = os.close
    original_dup = os.dup
    original_fstat = os.fstat
    owned = {"parent": [], "child": []}
    close_attempts = []
    injected = []

    def identity(fd):
        observed = original_fstat(fd)
        return (fd, observed.st_dev, observed.st_ino,
                stat.S_IFMT(observed.st_mode))

    def recording_dup(fd):
        duplicate = original_dup(fd)
        owned["parent"].append(identity(duplicate))
        return duplicate

    def recording_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        expected_child = b"owned" if nested else b"feature.txt"
        if os.fspath(path) == expected_child:
            owned["child"].append(identity(fd))
        return fd

    def close_parent_then_raise(fd):
        current = identity(fd)
        close_attempts.append(current)
        if owned["parent"] and current == owned["parent"][0] and not injected:
            original_close(fd)
            injected.append(current)
            raise OSError("injected parent close failure")
        return original_close(fd)

    root_fd = original_open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    monkeypatch.setattr(mod.os, "dup", recording_dup)
    monkeypatch.setattr(mod.os, "open", recording_open)
    monkeypatch.setattr(mod.os, "close", close_parent_then_raise)
    try:
        with pytest.raises((OSError, mod._SnapshotError), match="parent close"):
            if helper == "source":
                mod._open_tracked(root_fd, relative)
            else:
                mod._open_destination(root_fd, relative, False)

        assert len(injected) == 1
        assert len(owned["child"]) == 1
        assert owned["child"][0] in close_attempts
    finally:
        original_close(root_fd)
        for descriptor in owned["child"]:
            if descriptor not in close_attempts:
                fd = descriptor[0]
                try:
                    current = identity(fd)
                except OSError:
                    continue
                if current == descriptor:
                    original_close(fd)


def test_mutation_snapshot_close_all_cancellation_drains_later_fds_and_preserves_first(
        tmp_path, monkeypatch):
    mod = _load_mt()
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    identities = []
    for index in range(3):
        path = tmp_path / f"owned-{index}"
        path.write_bytes(bytes([index]))
        fd = original_open(path, os.O_RDONLY)
        identities.append(_descriptor_identity(original_fstat, fd))
    first = KeyboardInterrupt("first owned close cancellation")
    later = SystemExit(73)
    attempts = []

    def close_then_fault(fd):
        identity = _descriptor_identity(original_fstat, fd)
        attempts.append(identity)
        original_close(fd)
        if identity == identities[0]:
            raise first
        if identity == identities[1]:
            raise later

    monkeypatch.setattr(mod.os, "close", close_then_fault)
    try:
        with pytest.raises(BaseException) as caught:
            mod._close_all(*(identity[0] for identity in identities))

        assert caught.value is first
        assert caught.value.args == ("first owned close cancellation",)
        assert attempts == identities
        assert any("SystemExit" in note and "73" in note
                   for note in getattr(first, "__notes__", ()))
        for identity in identities:
            _assert_owner_is_settled(original_fstat, identity)
    finally:
        for identity in identities:
            _close_if_same_owner(original_fstat, original_close, identity)


@pytest.mark.parametrize(
    "evidence_fault", ("primary-add-note", "secondary-str"))
def test_mutation_snapshot_close_evidence_failures_never_interrupt_owner_drain(
        tmp_path, monkeypatch, evidence_fault):
    mod = _load_mt()
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    identities = []
    for index in range(3):
        path = tmp_path / f"hostile-evidence-{index}"
        path.write_bytes(bytes([index]))
        fd = original_open(path, os.O_RDONLY)
        identities.append(_descriptor_identity(original_fstat, fd))

    class RejectingNotePrimary(KeyboardInterrupt):
        def add_note(self, note):
            raise RuntimeError("primary rejected close evidence")

    class UnprintableCloseFailure(SystemExit):
        def __str__(self):
            raise RuntimeError("secondary close text is hostile")

    if evidence_fault == "primary-add-note":
        primary = RejectingNotePrimary("snapshot primary")
        failures = [SystemExit(81 + index) for index in range(3)]
    else:
        primary = KeyboardInterrupt("snapshot primary")
        failures = [UnprintableCloseFailure(81 + index)
                    for index in range(3)]
    attempts = []

    def close_then_fault(fd):
        identity = _descriptor_identity(original_fstat, fd)
        index = identities.index(identity)
        attempts.append(identity)
        original_close(fd)
        raise failures[index]

    monkeypatch.setattr(mod.os, "close", close_then_fault)
    try:
        with pytest.raises(BaseException) as caught:
            try:
                raise primary
            finally:
                mod._close_all_preserving_active_error(
                    *(identity[0] for identity in identities))

        assert caught.value is primary
        assert caught.value.args == ("snapshot primary",)
        assert attempts == identities
        notes = getattr(primary, "__notes__", ())
        assert len(notes) == 3
        for identity, note in zip(identities, notes):
            assert "cleanup" in note
            assert f"fd {identity[0]}" in note
            assert type(failures[0]).__name__ in note
            if evidence_fault == "secondary-str":
                assert "<unprintable>" in note
        for identity in identities:
            _assert_owner_is_settled(original_fstat, identity)
    finally:
        for identity in identities:
            _close_if_same_owner(original_fstat, original_close, identity)


def test_mutation_snapshot_parent_cancellation_settles_child_and_preserves_parent(
        tmp_path, monkeypatch):
    mod = _load_mt()
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    identities = []
    for name in ("parent", "child"):
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        fd = original_open(path, os.O_RDONLY)
        identities.append(_descriptor_identity(original_fstat, fd))
    parent_failure = KeyboardInterrupt("parent descriptor cancellation")
    child_failure = SystemExit(74)
    attempts = []

    def close_then_fault(fd):
        identity = _descriptor_identity(original_fstat, fd)
        attempts.append(identity)
        original_close(fd)
        if identity == identities[0]:
            raise parent_failure
        if identity == identities[1]:
            raise child_failure

    monkeypatch.setattr(mod.os, "close", close_then_fault)
    try:
        with pytest.raises(BaseException) as caught:
            mod._adopt_child_after_parent_close(
                identities[0][0], identities[1][0])

        assert caught.value is parent_failure
        assert caught.value.args == ("parent descriptor cancellation",)
        assert attempts == identities
        assert any("unpublished child" in note and
                   "SystemExit" in note and "74" in note
                   for note in getattr(parent_failure, "__notes__", ()))
        for identity in identities:
            _assert_owner_is_settled(original_fstat, identity)
    finally:
        for identity in identities:
            _close_if_same_owner(original_fstat, original_close, identity)


@pytest.mark.parametrize(
    "funnel",
    ("open-tracked", "open-destination", "read-tracked",
     "snapshot-tree", "observe-snapshot"),
)
def test_mutation_snapshot_active_primary_survives_close_cancellation_at_every_funnel(
        tmp_path, monkeypatch, funnel):
    mod = _load_mt()
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    original_dup = os.dup
    original_mkdir = os.mkdir
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    destination = tmp_path / "destination"
    if funnel == "open-destination":
        destination.mkdir()
    if funnel == "snapshot-tree":
        subprocess.run(["git", "init", "-q"], cwd=source, check=True)
        subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)

    primary = RuntimeError(f"active primary at {funnel}")
    close_failure = SystemExit(75)
    owned = []
    close_attempts = []
    injected_primary = []
    injected_close = []

    def remember(fd):
        identity = _descriptor_identity(original_fstat, fd)
        owned.append(identity)
        return fd

    def recording_dup(fd):
        duplicate = original_dup(fd)
        if funnel in ("open-tracked", "open-destination"):
            remember(duplicate)
        return duplicate

    def recording_open(path, flags, *args, **kwargs):
        raw_path = os.fspath(path)
        if (funnel in ("open-tracked", "open-destination") and
                raw_path == b"feature.txt"):
            injected_primary.append(raw_path)
            raise primary
        fd = original_open(path, flags, *args, **kwargs)
        if funnel == "read-tracked" and raw_path == b"feature.txt":
            remember(fd)
        elif funnel == "snapshot-tree" and raw_path == str(source):
            remember(fd)
        elif funnel == "observe-snapshot" and raw_path == str(source):
            remember(fd)
        return fd

    def failing_mkdir(path, *args, **kwargs):
        if funnel == "snapshot-tree" and os.fspath(path) == str(destination):
            injected_primary.append(os.fspath(path))
            raise primary
        return original_mkdir(path, *args, **kwargs)

    original_sha256 = mod.hashlib.sha256

    def failing_sha256(*args, **kwargs):
        if funnel == "read-tracked" and not injected_primary:
            injected_primary.append("sha256")
            raise primary
        return original_sha256(*args, **kwargs)

    original_read_tracked = mod._read_tracked

    def failing_read_tracked(*args, **kwargs):
        if funnel == "observe-snapshot" and not injected_primary:
            injected_primary.append("read-tracked")
            raise primary
        return original_read_tracked(*args, **kwargs)

    def close_then_cancel(fd):
        identity = _descriptor_identity(original_fstat, fd)
        close_attempts.append(identity)
        original_close(fd)
        if identity in owned and not injected_close:
            injected_close.append(identity)
            raise close_failure

    root_fd = None
    if funnel in ("open-tracked", "read-tracked"):
        root_fd = original_open(
            source, os.O_RDONLY | os.O_DIRECTORY |
            os.O_CLOEXEC | os.O_NOFOLLOW)
    elif funnel == "open-destination":
        root_fd = original_open(
            destination, os.O_RDONLY | os.O_DIRECTORY |
            os.O_CLOEXEC | os.O_NOFOLLOW)

    monkeypatch.setattr(mod.os, "dup", recording_dup)
    monkeypatch.setattr(mod.os, "open", recording_open)
    monkeypatch.setattr(mod.os, "mkdir", failing_mkdir)
    monkeypatch.setattr(mod.os, "close", close_then_cancel)
    monkeypatch.setattr(mod.hashlib, "sha256", failing_sha256)
    monkeypatch.setattr(mod, "_read_tracked", failing_read_tracked)
    try:
        with pytest.raises(BaseException) as caught:
            if funnel == "open-tracked":
                mod._open_tracked(root_fd, b"feature.txt")
            elif funnel == "open-destination":
                mod._open_destination(root_fd, b"feature.txt", False)
            elif funnel == "read-tracked":
                mod._read_tracked(root_fd, b"feature.txt", False)
            elif funnel == "snapshot-tree":
                mod._snapshot_tree(str(source), str(destination))
            else:
                mod._observe_tracked_snapshot(
                    str(source), {b"feature.txt": (False, b"unused")})

        assert injected_primary, f"primary injection did not fire: {funnel}"
        assert len(owned) == 1, (funnel, owned)
        assert injected_close == owned
        assert close_attempts.count(owned[0]) == 1
        assert caught.value is primary
        assert caught.value.args == (f"active primary at {funnel}",)
        assert any("cleanup" in note and "SystemExit" in note and "75" in note
                   for note in getattr(primary, "__notes__", ()))
        _assert_owner_is_settled(original_fstat, owned[0])
    finally:
        if root_fd is not None:
            try:
                original_close(root_fd)
            except OSError:
                pass
        for identity in owned:
            _close_if_same_owner(original_fstat, original_close, identity)


def test_mutation_snapshot_digest_setup_failure_closes_source_fd(
        tmp_path, monkeypatch):
    mod = _load_mt()
    root = tmp_path / "source"
    root.mkdir()
    (root / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    source_leaf = []
    close_attempts = []

    def identity(fd):
        observed = original_fstat(fd)
        return (fd, observed.st_dev, observed.st_ino,
                stat.S_IFMT(observed.st_mode))

    def recording_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if os.fspath(path) == b"feature.txt":
            source_leaf.append(identity(fd))
        return fd

    def recording_close(fd):
        close_attempts.append(identity(fd))
        return original_close(fd)

    def fail_digest_setup():
        raise RuntimeError("injected digest setup failure")

    root_fd = original_open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    monkeypatch.setattr(mod.os, "open", recording_open)
    monkeypatch.setattr(mod.os, "close", recording_close)
    monkeypatch.setattr(mod.hashlib, "sha256", fail_digest_setup)
    try:
        with pytest.raises(RuntimeError, match="digest setup"):
            mod._read_tracked(root_fd, b"feature.txt", False)

        assert len(source_leaf) == 1
        assert source_leaf[0] in close_attempts
    finally:
        original_close(root_fd)
        if source_leaf and source_leaf[0] not in close_attempts:
            fd = source_leaf[0][0]
            try:
                current = identity(fd)
            except OSError:
                pass
            else:
                if current == source_leaf[0]:
                    original_close(fd)


def test_mutation_snapshot_uses_current_tracked_bytes_not_index_bytes(tmp_path):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    feature = source / "feature.txt"
    feature.write_text("INDEX\n", encoding="utf-8")
    (source / "gate.py").write_text(
        "from pathlib import Path\n"
        "raise SystemExit(0 if Path('feature.txt').read_text() == "
        "'WORKING\\n' else 1)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "feature.txt", "gate.py"],
                   cwd=source, check=True)
    feature.write_text("WORKING\n", encoding="utf-8")
    row = _snapshot_fixture_row()
    row["mutate"] = lambda text: text.replace("WORKING", "ABSENT")
    row["gate"] = "{py} gate.py"

    result = mod.check(row, str(source))

    assert result["state"] == "CAUGHT", result
    assert feature.read_text(encoding="utf-8") == "WORKING\n"


def test_mutation_gates_start_from_independent_clean_trees(tmp_path):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    feature = source / "feature.txt"
    feature.write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    calls = tmp_path / "gate-calls"
    row = _snapshot_fixture_row()
    row["gate"] = (
        f"printf '%s\\n' \"$PWD\" >> {shlex.quote(str(calls))}; "
        "test ! -e .gate-seen && touch .gate-seen"
    )

    result = mod.check(row, str(source))

    assert result["state"] == "SURVIVED", result
    gate_trees = calls.read_text(encoding="utf-8").splitlines()
    assert len(gate_trees) == 2 and len(set(gate_trees)) == 2, gate_trees
    assert feature.read_text(encoding="utf-8") == "PRESENT\n"


def test_mutation_gate_cannot_reuse_pristine_python_bytecode(tmp_path):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    feature = source / "feature.py"
    feature.write_text("VALUE = 'PRESENT'\n", encoding="utf-8")
    (source / "gate.py").write_text(
        "import os\n"
        "os.utime('feature.py', (1700000000, 1700000000))\n"
        "import feature\n"
        "raise SystemExit(0 if feature.VALUE == 'PRESENT' else 1)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "feature.py", "gate.py"],
                   cwd=source, check=True)
    row = _snapshot_fixture_row()
    row["target"] = "feature.py"
    row["mutate"] = lambda text: text.replace("PRESENT", "BROK_EN")
    row["gate"] = "{py} gate.py"

    result = mod.check(row, str(source))

    assert result["state"] == "CAUGHT", result
    assert feature.read_text(encoding="utf-8") == "VALUE = 'PRESENT'\n"
    assert not (source / "__pycache__").exists()


def test_mutation_snapshot_uses_only_owner_execute_as_git_mode_authority(
        tmp_path):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    feature = source / "feature.txt"
    feature.write_text("PRESENT\n", encoding="utf-8")
    (source / "gate.py").write_text(
        "from pathlib import Path\n"
        "raise SystemExit(0 if Path('feature.txt').read_text() == "
        "'PRESENT\\n' else 1)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "feature.txt", "gate.py"],
                   cwd=source, check=True)
    feature.chmod(0o645)
    index = subprocess.run(
        ["git", "ls-files", "-s", "feature.txt"], cwd=source, check=True,
        capture_output=True, text=True,
    ).stdout
    assert index.startswith("100644 "), index
    assert stat.S_IMODE(feature.stat().st_mode) == 0o645
    row = _snapshot_fixture_row()
    row["gate"] = "{py} gate.py"

    result = mod.check(row, str(source))

    assert result["state"] == "CAUGHT", result


def test_mutation_snapshot_ignores_ambient_git_redirection(
        tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    (source / "gate.py").write_text(
        "from pathlib import Path\n"
        "raise SystemExit(0 if Path('feature.txt').read_text() == "
        "'PRESENT\\n' else 1)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "feature.txt", "gate.py"],
                   cwd=source, check=True)
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(tmp_path / "forged-objects"))
    row = _snapshot_fixture_row()
    row["gate"] = "{py} gate.py"

    result = mod.check(row, str(source))

    assert result["state"] == "CAUGHT", result


def test_mutation_snapshot_refuses_bytes_changed_during_copy(
        tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    feature = source / "feature.txt"
    feature.write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    original = mod._read_tracked
    changed = []

    def change_after_copy(root_fd, rel, executable, destination=None):
        result = original(root_fd, rel, executable, destination)
        if (os.fsdecode(rel) == "feature.txt" and destination is not None and
                not changed):
            feature.write_text("CHANGED\n", encoding="utf-8")
            changed.append(os.fsdecode(rel))
        return result

    monkeypatch.setattr(mod, "_read_tracked", change_after_copy)
    gate_calls = []
    monkeypatch.setattr(mod, "_run",
                        lambda *args: gate_calls.append(args) or (0, ""))

    result = mod.check(_snapshot_fixture_row(), str(source))

    assert changed == ["feature.txt"]
    assert result["state"] == "UNKNOWN", result
    assert gate_calls == []


def test_mutation_snapshot_refuses_a_tracked_file_disappearing_after_copy(
        tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    feature = source / "feature.txt"
    feature.write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    original = mod._read_tracked
    removed = []

    def remove_after_copy(root_fd, rel, executable, destination=None):
        result = original(root_fd, rel, executable, destination)
        if (os.fsdecode(rel) == "feature.txt" and destination is not None and
                not removed):
            feature.unlink()
            removed.append(os.fsdecode(rel))
        return result

    monkeypatch.setattr(mod, "_read_tracked", remove_after_copy)
    gate_calls = []
    monkeypatch.setattr(mod, "_run",
                        lambda *args: gate_calls.append(args) or (0, ""))

    result = mod.check(_snapshot_fixture_row(), str(source))

    assert removed == ["feature.txt"]
    assert result["state"] == "UNKNOWN", result
    assert "tracked path changed during snapshot" in result["detail"], result
    assert gate_calls == []


def test_mutation_snapshot_refuses_an_index_change_during_copy(
        tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    late = source / "late.txt"
    late.write_text("late index entry\n", encoding="utf-8")
    original = mod._git_index
    changed = []

    def change_index_after_first_listing(src_tree):
        result = original(src_tree)
        if not changed:
            subprocess.run(["git", "add", "late.txt"], cwd=source, check=True)
            changed.append("late.txt")
        return result

    monkeypatch.setattr(mod, "_git_index", change_index_after_first_listing)
    gate_calls = []
    monkeypatch.setattr(mod, "_run",
                        lambda *args: gate_calls.append(args) or (0, ""))

    result = mod.check(_snapshot_fixture_row(), str(source))

    assert changed == ["late.txt"]
    assert result["state"] == "UNKNOWN", result
    assert "Git index changed during snapshot" in result["detail"], result
    assert gate_calls == []


def test_mutation_snapshot_refuses_an_absent_tracked_file_appearing_later(
        tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    feature = source / "feature.txt"
    feature.write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    feature.unlink()
    original = mod._git_index
    listings = []

    def restore_after_first_listing(src_tree):
        result = original(src_tree)
        listings.append(result[0])
        if len(listings) == 2:
            feature.write_text("PRESENT\n", encoding="utf-8")
        return result

    monkeypatch.setattr(mod, "_git_index", restore_after_first_listing)
    gate_calls = []
    monkeypatch.setattr(mod, "_run",
                        lambda *args: gate_calls.append(args) or (0, ""))

    result = mod.check(_snapshot_fixture_row(), str(source))

    assert len(listings) == 2 and listings[0] == listings[1]
    assert feature.read_text(encoding="utf-8") == "PRESENT\n"
    assert result["state"] == "UNKNOWN", result
    assert "tracked path changed during snapshot" in result["detail"], result
    assert gate_calls == []


def test_mutation_snapshot_refuses_an_equal_byte_inode_replacement(
        tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    feature = source / "feature.txt"
    feature.write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    held_fd = os.open(feature, os.O_RDONLY)
    original = mod._read_tracked
    replacements = []

    def replace_after_copy(root_fd, rel, executable, destination=None):
        result = original(root_fd, rel, executable, destination)
        if (os.fsdecode(rel) == "feature.txt" and destination is not None and
                not replacements):
            feature.unlink()
            feature.write_text("PRESENT\n", encoding="utf-8")
            replacements.append((os.fstat(held_fd).st_ino,
                                 feature.stat().st_ino))
        return result

    monkeypatch.setattr(mod, "_read_tracked", replace_after_copy)
    gate_calls = []
    monkeypatch.setattr(mod, "_run",
                        lambda *args: gate_calls.append(args) or (0, ""))
    try:
        result = mod.check(_snapshot_fixture_row(), str(source))
    finally:
        os.close(held_fd)

    assert len(replacements) == 1
    assert replacements[0][0] != replacements[0][1]
    assert result["state"] == "UNKNOWN", result
    assert "tracked path changed during snapshot" in result["detail"], result
    assert gate_calls == []


def test_mutation_snapshot_refuses_mode_only_second_observation_drift(
        tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    original = mod._read_tracked
    changed = []

    def report_mode_drift(root_fd, rel, executable, destination=None):
        result = original(root_fd, rel, executable, destination)
        if (os.fsdecode(rel) == "feature.txt" and destination is None and
                result is not None and not changed):
            identity, digest = result
            changed.append((identity[-1], identity[-1] ^ stat.S_IRGRP))
            return identity[:-1] + (changed[0][1],), digest
        return result

    monkeypatch.setattr(mod, "_read_tracked", report_mode_drift)
    gate_calls = []
    monkeypatch.setattr(mod, "_run",
                        lambda *args: gate_calls.append(args) or (0, ""))

    result = mod.check(_snapshot_fixture_row(), str(source))

    assert len(changed) == 1 and changed[0][0] != changed[0][1]
    assert result["state"] == "UNKNOWN", result
    assert "tracked path changed during snapshot" in result["detail"], result
    assert gate_calls == []


def test_mutation_snapshot_rejects_a_gitlink(tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    (source / "feature.txt").write_text("PRESENT\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=source, check=True)
    tree = subprocess.run(["git", "write-tree"], cwd=source, check=True,
                          capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(
        ["git", "-c", "user.name=fixture", "-c", "user.email=f@example.invalid",
         "commit-tree", tree, "-m", "fixture"],
        cwd=source, check=True, capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(["git", "update-index", "--add", "--cacheinfo",
                    f"160000,{commit},vendor"], cwd=source, check=True)
    gate_calls = []
    monkeypatch.setattr(mod, "_run",
                        lambda *args: gate_calls.append(args) or (0, ""))

    result = mod.check(_snapshot_fixture_row(), str(source))

    assert result["state"] == "UNKNOWN", result
    assert "160000" in result["detail"]
    assert gate_calls == []


def test_mutation_snapshot_rejects_an_empty_git_denominator(
        tmp_path, monkeypatch):
    mod = _load_mt()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    gate_calls = []
    monkeypatch.setattr(mod, "_run",
                        lambda *args: gate_calls.append(args) or (0, ""))

    result = mod.check(_snapshot_fixture_row(), str(source))

    assert result["state"] == "UNKNOWN", result
    assert "denominator is empty" in result["detail"]
    assert gate_calls == []


def _registry_rows_from_ast():
    """Every REGISTRY row as (id, target, has_src, mutate_node), read from the
    SOURCE rather than the loaded closure.

    Reading the compiled closure would require the tool to expose the pattern,
    which pristine source does not -- this assertion has to be runnable on both
    sides of the fix or it proves nothing.
    """
    tree = ast.parse(MT.read_text(encoding="utf-8"))
    reg = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "REGISTRY" for t in node.targets):
            reg = node.value
    assert reg is not None, "REGISTRY assignment not found in bd-mutation-test"
    assert isinstance(reg, (ast.List, ast.Tuple)), "REGISTRY is not a literal list"
    rows = []
    for el in reg.elts:
        # The rows are dict(id=..., target=..., mutate=...) CALLS, not {...}
        # literals. An earlier draft of this helper filtered on ast.Dict and so
        # matched NOTHING -- 16 rows in, 0 rows out, and the assertion below
        # passed over an empty denominator. That is the exact section 0 failure
        # this cut exists to fix, reproduced inside its own test; the length
        # assertion at the end is what makes it impossible to repeat silently.
        if isinstance(el, ast.Dict):
            rows.append({k.value: v for k, v in zip(el.keys, el.values)
                         if isinstance(k, ast.Constant)})
        elif (isinstance(el, ast.Call)
              and getattr(el.func, "id", getattr(el.func, "attr", "")) == "dict"):
            rows.append({kw.arg: kw.value for kw in el.keywords if kw.arg})
    assert len(rows) == len(reg.elts), (
        f"the REGISTRY reader understood {len(rows)} of {len(reg.elts)} rows. "
        f"A row it cannot parse is a row it cannot check, and a shrinking "
        f"denominator here certifies anchors nobody verified.")
    assert rows, "REGISTRY is empty -- nothing to verify"
    return rows


def test_every_mutation_anchor_resolves_to_exactly_its_declared_count():
    """RED, and the assertion that makes every other CAUGHT verdict meaningful.

    An anchor matching 442 sites applied with count=1 mutates whichever site
    re.subn reaches first, so the row proves something about a location it does
    not name. An anchor matching 0 sites can only ever return UNKNOWN.

    FAIL-CLOSED: a row whose target is missing, or whose mutate is not a
    recognisable _sub(...), FAILS here. It is not skipped. Only an explicit
    __PLANT__ row is exempt.
    """
    bad = []
    for d in _registry_rows_from_ast():
        target_node = d.get("target")
        if target_node is None or not isinstance(target_node, ast.Constant):
            bad.append("a row has no literal target")
            continue
        target = target_node.value
        if str(target).startswith("__PLANT__:"):
            continue
        rid = d.get("id")
        rid = rid.value if isinstance(rid, ast.Constant) else "<unknown id>"
        mut = d.get("mutate")
        if not (isinstance(mut, ast.Call)
                and getattr(mut.func, "id", getattr(mut.func, "attr", "")) == "_sub"):
            bad.append(f"{rid}: mutate is not a recognisable _sub(...) call")
            continue
        try:
            pat = ast.literal_eval(mut.args[0])
            cnt = ast.literal_eval(mut.args[2]) if len(mut.args) >= 3 else 1
        except Exception as exc:
            bad.append(f"{rid}: could not read the anchor -- {exc}")
            continue
        base = BIN if "src" in d else REPO
        path = base / target
        if not path.exists():
            bad.append(f"{rid}: target does not exist -- {path}")
            continue
        try:
            found = len(re.findall(pat, path.read_text(errors="replace")))
        except re.error as exc:
            bad.append(f"{rid}: anchor is not a valid regex -- {exc}")
            continue
        if cnt > 0:
            if found != cnt:
                bad.append(f"{rid}: anchor matches {found} site(s) in {target}, "
                           f"but the row applies count={cnt}")
        elif found < 1:
            bad.append(f"{rid}: replace-all anchor matches nothing in {target}")
    assert not bad, (
        "these mutation anchors do not identify the site they claim:\n  "
        + "\n  ".join(bad))


# @877 -- the carriers this gate KNOWS about. Anything outside this set is a
# regression; anything inside it that gets fixed must be REMOVED from the list
# in the same cut, which is why the assertion is set EQUALITY and not "no new
# ones". A one-directional count would let the list rot into a permanent
# amnesty, and this repo already has that failure recorded: a floor of 150 sat
# under a real population of 258 and could not see a narrowing it was written
# to catch (@870).
#
# NONE of these is on a band path. The two that were -- bd-band and
# bd-bandcheck -- were fixed at @876; the rest are operator-invoked
# checkpoint/rollback/snapshot tools and decomp helpers, which is item 8c and
# is deliberately NOT in this cut. Listing them makes them visible; before
# this, a gate reading ONE file certified the whole population.
#
# ROW 196, v3.66.1235 -- THE VERDICT MOVES FROM TEXT TO BEHAVIOUR. The census
# used to ask whether a file's bytes contained one exact spelling of the
# assignment. That is a textual proxy for a runtime property and it was wrong in
# both directions: five byte-different, runtime-identical spellings walked
# straight past it (single quotes, a module constant, an imported module
# attribute, a line-broken keyword, a subdirectory of the retired tree), while a
# docstring quoting the retired assignment was reported as a live configuration.
# The path constant survives because the classifier still has to name what it is
# hunting; nothing reads a file's TEXT for it any more.
_RETIRED_SANDBOX_WORK = "/home/claude/work"
_RETIRED_SANDBOX_ROOT = _RETIRED_SANDBOX_WORK.rsplit("/", 1)[0]
# EMPTY, AND THAT IS THE POINT. The one entry this set ever held was THIS FILE,
# listed because the gate's own literal matched itself -- the A7 self-reference
# the row is about. A runtime probe never reads this file's text, so the
# self-reference is gone rather than renamed. The set stays, with its
# set-EQUALITY discipline in _classify_sandbox_carriers intact, so a future real
# carrier can be recorded and cannot rot into a one-directional amnesty.
_KNOWN_SANDBOX_DEFAULT_CARRIERS = frozenset()

# The option whose resolved default this census judges.
_WORK_OPTION = "--work"

# Floor on the candidate population. 146 tracked python-typed files declare this
# option today (measured at v3.66.1235); a census that suddenly certifies a
# handful of them is certifying almost nothing and must say so rather than pass.
_WORK_CANDIDATE_FLOOR = 120

# Files that carry the option TOKEN and call add_argument, yet declare no
# --work option. Set equality, so a new one is a forced human decision rather
# than a silent hole in the enumeration.
_WORK_OPTION_NON_DECLARERS = {
    "toolchain/bin/bd-coretest": "runs other tools with the option in argv",
    "toolchain/bin/bd-sweep": "prose about the option contract",
    "toolchain/bin/bd-tool-lint": "the option appears inside its selftest fixture",
}

# MEASURED ON THIS HOST, v3.66.1235, idle test5 (48 cores), load 1.06-1.87:
# 0.534s wall for all 146 targets, three consecutive runs, `git status
# --porcelain` byte-identical before and after each. The budget is
# max(_MIN_BUDGET_S, 6 x measured) = max(60, 4) = 60, which is the file's own
# floor and is 180s clear of the 240s bound governing the item. Deliberately NOT
# a _MEASURED_S entry: that table arms _run_tool's `elapsed <= baseline x
# _CONTENTION_FACTOR` self-policing, and a 0.5s baseline would make a perfectly
# healthy run under suite contention fail an assertion about SPEED.
_WORK_CENSUS_MEASURED_S = 0.534
_WORK_CENSUS_BUDGET_S = 60


def _python_typed_tracked():
    """Tracked files that are Python by EXTENSION *or* by SHEBANG.

    `git ls-files -- '*.py'` is NOT "the Python files in this repo" -- a *.py
    glob reaches 2.5% of toolchain/, where the tools are extensionless bd-*
    scripts. Typing on the shebang as well is the difference between a
    denominator of 2110 and one of 2568.
    """
    out = subprocess.run(["git", "ls-files", "-z"], cwd=str(REPO),
                         capture_output=True, text=True, check=True).stdout
    keep = []
    for rel in out.split("\0"):
        if not rel:
            continue
        p = REPO / rel
        if rel.endswith(".py"):
            keep.append(rel)
            continue
        if "." in rel.rsplit("/", 1)[-1]:
            continue
        try:
            head = p.read_bytes()[:80]
        except OSError:
            continue
        if head[:2] == b"#!" and b"python" in head.split(b"\n")[0]:
            keep.append(rel)
    return keep


def _classify_sandbox_carriers(n_files, carriers, known, floor=2000):
    """Every problem with this scan, as a list of messages. PURE on purpose.

    Extracted so the three guards can be driven with constructed input -- see
    test_the_sandbox_carrier_classifier_fires_in_all_three_directions. Left
    inline, all three were unreachable by any mutant on a healthy tree.
    """
    problems = []
    if n_files <= floor:
        problems.append(
            "the python-typed denominator collapsed to %d files (floor %d); "
            "every assertion here would be passing over almost nothing"
            % (n_files, floor))
    new = set(carriers) - set(known)
    if new:
        problems.append(
            "%d file(s) newly default --work to the retired sandbox path, so a "
            "bare invocation measures a tree that is not there:\n  %s"
            % (len(new), "\n  ".join(sorted(new))))
    fixed = set(known) - set(carriers)
    if fixed:
        problems.append(
            "%d file(s) no longer carry the sandbox default -- good, but remove "
            "them from _KNOWN_SANDBOX_DEFAULT_CARRIERS in the SAME cut. A list "
            "that only ever grows stale becomes a permanent amnesty:\n  %s"
            % (len(fixed), "\n  ".join(sorted(fixed))))
    return problems


def _work_option_candidates(files):
    """rel -> [lineno] for every file that DECLARES the option to argparse.

    Parsing, not grepping. An `in` test counts docstrings, comments and argv
    strings; an ast.Call with a Constant option name counts declarations. The
    cheap `in` pre-filter only decides which files are worth parsing -- it can
    only ever over-include, and the AST makes the decision.

    FAIL-CLOSED: no `except SyntaxError`. Every python-typed tracked file parses
    today (measured: 0 SyntaxError over 2536 files at v3.66.1235), so a file
    that stops parsing is a real event and must raise rather than be silently
    dropped from the denominator.
    """
    out = {}
    for rel in files:
        try:
            src = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _WORK_OPTION not in src:
            continue
        tree = ast.parse(src, filename=rel)
        sites = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "add_argument"
                 and any(isinstance(a, ast.Constant) and a.value == _WORK_OPTION
                         for a in n.args)]
        if sites:
            out[rel] = [n.lineno for n in sites]
    return out


# THE PROBE. Runs in a child of a child: one fork per target, so a tool that
# dies, exits, or corrupts interpreter state cannot take the census with it.
#
# It monkeypatches ArgumentParser.add_argument and raises the moment the option
# is declared, which is as far into the tool as the census ever needs to go --
# before parse_args, before main's real work.
#
# THIS IS A STRING LITERAL ON PURPOSE. It is written to a temporary file at run
# time, so its `add_argument` text is invisible to this module's own AST and
# this file can never enumerate itself as a candidate.
#
# sys.path.insert of the target's own directory is LOAD-BEARING, not tidiness:
# without it toolchain/bin/bd-decomp raises ModuleNotFoundError for bdtools_sec,
# because it relies on the implicit sys.path[0] a real invocation gives it.
# MEASURED both ways at v3.66.1235 -- 0 unreached with the insert, exactly
# bd-decomp unreached without it. A harness that manufactures its own failure is
# the defect one level up.
#
# runpy.run_path is NOT usable here: it swaps sys.modules['__main__'], the
# probe's own globals are torn down, and the child produces no output at all
# with rc=0. Anyone "simplifying" this back to runpy silently empties the
# census.
_WORK_PROBE_SOURCE = '''\
import argparse, json, os, sys


def probe_one(option, target):
    class Reached(Exception):
        pass

    seen = {}
    real = argparse.ArgumentParser.add_argument

    def add(self, *args, **kw):
        action = real(self, *args, **kw)
        if option in args:
            seen["default"] = action.default
            raise Reached
        return action

    argparse.ArgumentParser.add_argument = add
    sys.argv = [target]
    sys.path.insert(0, os.path.dirname(os.path.realpath(target)))
    err = None
    try:
        exec(compile(open(target, "rb").read(), target, "exec"),
             {"__name__": "__main__", "__file__": target,
              "__builtins__": __builtins__})
    except Reached:
        pass
    except BaseException as exc:
        err = "%s: %s" % (type(exc).__name__, exc)
    d = seen.get("default")
    return {"target": target, "reached": "default" in seen,
            "default": d if (isinstance(d, str) or d is None) else str(d),
            "kind": type(d).__name__, "error": err}


def main():
    option, outdir, targets = sys.argv[1], sys.argv[2], sys.argv[3:]
    devnull = os.open(os.devnull, os.O_RDONLY)
    for i, t in enumerate(targets):
        pid = os.fork()
        if pid == 0:
            code = 0
            try:
                os.dup2(devnull, 0)
                with open(os.path.join(outdir, "%d.json" % i), "w") as fh:
                    json.dump(probe_one(option, t), fh)
            except BaseException:
                code = 3
            finally:
                os._exit(code)
    for _ in targets:
        os.wait()
    return 0


raise SystemExit(main())
'''


def _probe_work_defaults(rels, tmpdir):
    """rel -> the probe's record of what that tool resolves the option to.

    THE PARENT MUST STAY SINGLE-THREADED. It forks; forking a process that owns
    a thread pool is a real hazard, so a future refactor that adds one here is
    a correctness change and not an optimisation.

    Isolation: HOME and TMPDIR are redirected inside tmpdir, stdin is /dev/null
    per child so no tool can block on it, bytecode writing is off, and the four
    BD_* variables that could inject a default are POPPED rather than merely not
    set -- the census must measure the code, not the operator's shell.

    cwd is REPO deliberately: at least one tool defaults to a cwd-relative ".",
    and a census run from an arbitrary directory would report a different answer
    than a real invocation from the checkout.
    """
    probe = Path(tmpdir) / "work_default_probe.py"
    probe.write_text(_WORK_PROBE_SOURCE, encoding="utf-8")
    outdir = Path(tmpdir) / "probe-out"
    outdir.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("BD_ROOT", "BD_REPO", "BD_INSTALL_DIR", "BD_WORK_TREE")}
    # ROW 178 / v3.66.1197: a subprocess that INHERITS os.environ inherits the
    # ambient LC_ALL, and locale collation then decides sort order inside the
    # child -- so the host's language can change a verdict. Pinned to C, which
    # is what the tree-wide gate at test_v3_66_1197 requires and what caught
    # this at v3.66.1235 in CI rather than locally.
    env["LC_ALL"] = "C"
    home = Path(tmpdir) / "probe-home"
    scratch = Path(tmpdir) / "probe-tmp"
    home.mkdir(exist_ok=True)
    scratch.mkdir(exist_ok=True)
    env.update(PYTHONDONTWRITEBYTECODE="1", HOME=str(home), TMPDIR=str(scratch))
    assert not ({"BD_ROOT", "BD_REPO", "BD_INSTALL_DIR", "BD_WORK_TREE"} & set(env)), (
        "an ambient BD_* variable survived into the census environment, so the "
        "measurement would describe this shell rather than the tools")

    result = _run_tool(
        [sys.executable, str(probe), _WORK_OPTION, str(outdir)]
        + [str(REPO / rel) for rel in rels],
        budget_s=_WORK_CENSUS_BUDGET_S,
        what="the runtime %s default census" % _WORK_OPTION,
        cwd=str(REPO), env=env)
    assert result.returncode == 0, (
        "the census driver itself failed (rc=%d), so no target has a verdict:\n%s"
        % (result.returncode, result.stderr[-800:]))

    probed, missing = {}, []
    for i, rel in enumerate(rels):
        blob = outdir / ("%d.json" % i)
        try:
            probed[rel] = json.loads(blob.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            missing.append("%s: the probe produced no readable result (%s)"
                           % (rel, exc))
    assert not missing, (
        "the census could not measure these targets, so their verdict is "
        "UNKNOWN, which fails:\n  " + "\n  ".join(missing))
    return probed


def _classify_work_defaults(probed):
    """(carriers, problems) from probe records. PURE, so it is drivable.

    The retired-path question ONLY. Matches the retired ROOT and anything under
    it, not one exact string: a default of <retired>/checkout is just as absent
    from this host as <retired> itself.
    """
    carriers, problems = set(), []
    for rel in sorted(probed):
        row = probed[rel]
        if not row.get("reached"):
            problems.append(
                "%s: the census could not build its parser (%s) -- a tool whose "
                "option default cannot be MEASURED is UNKNOWN, not OK, and "
                "UNKNOWN fails" % (rel, row.get("error")))
            continue
        value = row.get("default")
        if value is None:
            continue
        text = str(value)
        if text == _RETIRED_SANDBOX_ROOT or text.startswith(_RETIRED_SANDBOX_ROOT + "/"):
            carriers.add(rel)
    return carriers, problems


@pytest.mark.timeout(_WORK_CENSUS_BUDGET_S + _ITEM_RESERVE_S)
def test_the_bare_work_default_is_not_a_sandbox_path(tmp_path):
    """@877 -- this gate used to read ONE FILE. ROW 196 -- then it read TEXT.

    `MT.read_text()` -- bd-mutation-test, and nothing else -- while 20 other
    tracked files carried the identical `default="/home/claude/work"`. It
    certified a population of 2568 by looking at one member of it, and reported
    OK. CLAUDE.md A7: the denominator excluded the subject.

    @877 widened the denominator to every python-typed tracked file but kept the
    VERDICT textual, and a textual verdict on a runtime property fails in both
    directions at once. It could not see a single-quoted default, a module
    constant, an imported module attribute (which is how 129 of this repo's 146
    real declarers spell it), a line-broken keyword, or a subdirectory of the
    retired tree -- and it DID fire on files that merely mention /home/claude in
    a comment explaining this very class of bug, which the original author
    worked around by narrowing the needle rather than by asking the question
    behaviourally.

    So the AST is demoted to ENUMERATION and the verdict is RUNTIME: each
    candidate's own parser is built in an isolated child and asked what it
    resolves the option to.

    THE HAZARD, STATED RATHER THAN HIDDEN: this executes each candidate's
    module-level code and its main() up to the first declaration of the option.
    Measured at v3.66.1235, all 146 prologues are parser construction only, and
    three full runs left `git status --porcelain` byte-identical. A future tool
    that ACTS before declaring the option would be run by this gate.

    WHAT THIS STILL DOES NOT CONSTRAIN: an option declared from a non-constant
    name in a file that never calls add_argument at all (narrowed, not closed,
    by test_every_work_option_declaration_is_visible_to_the_census); a default
    reachable only with BD_ROOT/BD_REPO/BD_WORK_TREE set, which the probe pops
    on purpose; a second declaration in the same file, which the probe would not
    reach and which the multi-site assertion below refuses; and non-argparse
    CLIs.
    """
    files = _python_typed_tracked()
    candidates = _work_option_candidates(files)
    assert len(candidates) >= _WORK_CANDIDATE_FLOOR, (
        "only %d file(s) declare the option (floor %d); the census would be "
        "certifying almost nothing, which is the shape this gate exists to "
        "refuse" % (len(candidates), _WORK_CANDIDATE_FLOOR))
    multi = {rel: lines for rel, lines in candidates.items() if len(lines) != 1}
    assert not multi, (
        "these files declare the option more than once; the probe stops at the "
        "FIRST declaration and cannot see the rest, so their later declarations "
        "would be certified without being measured: %r" % (multi,))

    probed = _probe_work_defaults(sorted(candidates), tmp_path)
    assert set(probed) == set(candidates), (
        "the probe denominator does not reconcile with the candidate set, so "
        "some file was enumerated and never measured: %r"
        % (sorted(set(candidates) ^ set(probed)),))

    carriers, problems = _classify_work_defaults(probed)
    problems = problems + _classify_sandbox_carriers(
        len(files), carriers, _KNOWN_SANDBOX_DEFAULT_CARRIERS)
    assert not problems, "\n".join(problems)


def test_an_unprobeable_candidate_is_a_failure_not_a_pass():
    """FAIL-CLOSED CONTROL, on constructed input.

    On a healthy tree every candidate reaches its declaration, so this branch is
    unreachable from the real census and no mutant could touch it there. That is
    exactly the "a guard that only fires in a state the tree is not in is a
    guard no mutant can reach" problem its sibling classifier test was written
    for, so it gets the same treatment: drive the pure function directly.
    """
    probed = {
        "toolchain/bin/bd-example": {
            "reached": False, "default": None, "kind": "NoneType",
            "error": "ModuleNotFoundError: No module named 'bdtools_sec'"},
    }
    carriers, problems = _classify_work_defaults(probed)
    assert carriers == set(), (
        "an unmeasurable tool must not be counted as a carrier either -- that "
        "would be a false accusation rather than a fail-closed")
    assert len(problems) == 1, problems
    assert "bd-example" in problems[0] and "UNKNOWN" in problems[0], problems[0]
    assert "bdtools_sec" in problems[0], (
        "the diagnosis must carry the tool's own error, or the operator cannot "
        "tell a broken tool from a broken census: %r" % problems[0])

    # OVER-SENSITIVITY, same function: a measured, healthy default is silent.
    ok = {"toolchain/bin/bd-example": {
        "reached": True, "default": str(REPO), "kind": "str", "error": None}}
    assert _classify_work_defaults(ok) == (set(), [])


def test_every_work_option_declaration_is_visible_to_the_census():
    """THE ENUMERATION'S OWN DENOMINATOR PROOF.

    The census only sees declarations whose option name is a STRING CONSTANT in
    an add_argument call. This asks the complementary question over the whole
    python-typed population: which files carry the option TOKEN and call
    add_argument, yet are not candidates? Set equality against a declared list,
    so a new one is a forced human decision instead of a silent hole.

    RAW text on purpose, not a comment-stripped view: the token lives inside a
    string literal, which comment/string stripping removes. The token boundary
    stops `--workspace` or `--work-tree` from counting.
    """
    files = _python_typed_tracked()
    assert len(files) > 2000, (
        "the python-typed denominator collapsed to %d files; every claim below "
        "would be about nothing" % len(files))
    candidates = _work_option_candidates(files)
    token = re.compile(re.escape(_WORK_OPTION) + r"(?![\w-])")

    unseen = set()
    for rel in files:
        if rel in candidates:
            continue
        try:
            src = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not token.search(src):
            continue
        tree = ast.parse(src, filename=rel)
        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "add_argument" for n in ast.walk(tree)):
            unseen.add(rel)

    assert unseen == set(_WORK_OPTION_NON_DECLARERS), (
        "the set of files that carry the option token AND build an argument "
        "parser but are NOT enumerated as declarers has changed. Every such "
        "file is a place the census could be blind. Added: %r. Gone (remove "
        "them from _WORK_OPTION_NON_DECLARERS in the SAME cut): %r"
        % (sorted(unseen - set(_WORK_OPTION_NON_DECLARERS)),
           sorted(set(_WORK_OPTION_NON_DECLARERS) - unseen)))


def test_the_sandbox_carrier_classifier_fires_in_all_three_directions():
    """@877 -- the assertions above are unconstrained ON A HEALTHY TREE.

    A mutation battery proved it: deleting the empty-denominator canary, or the
    new-carrier check, or the fixed-carrier check, ALL left the suite green,
    because on this tree `new` and `fixed` are both empty and the denominator
    is nowhere near collapsing. Three guards that fire only in a state the tree
    is not in are three guards no mutant can reach -- and a test that passes
    before and after is not a test.

    So the decision is a PURE function and this drives it with constructed
    input. That is the only way to exercise a guard whose real-world trigger is
    an event that has not happened yet.
    """
    known = {"a.py", "b.py"}

    # clean: same set, plausible denominator -> silent
    assert _classify_sandbox_carriers(2500, {"a.py", "b.py"}, known) == []

    # collapsed denominator -> the canary fires even though the sets agree
    out = _classify_sandbox_carriers(3, {"a.py", "b.py"}, known)
    assert out and "denominator" in out[0], out

    # a NEW carrier -> named
    out = _classify_sandbox_carriers(2500, {"a.py", "b.py", "c.py"}, known)
    assert out and "c.py" in out[0] and "newly" in out[0], out

    # a FIXED carrier -> also named, so the list cannot rot into an amnesty
    out = _classify_sandbox_carriers(2500, {"a.py"}, known)
    assert out and "b.py" in out[0], out

    # both at once -> BOTH reported, not just the first
    out = _classify_sandbox_carriers(2500, {"a.py", "c.py"}, known)
    assert len(out) == 2, out


# ── the engines still work (behavioural) ────────────────────────────────────

def test_the_mutation_engine_selftest_still_passes():
    """REGRESSION GUARD -- passes on pristine too. Labelled, NOT counted as RED.

    The engine and its four-state discipline are sound and this cut must not
    disturb them.
    """
    r = _run_tool([sys.executable, str(MT), "--selftest"],
                  budget_s=_budget_s("mutation-engine-selftest"),
                  what="bd-mutation-test", site="mutation-engine-selftest",
                  cwd=REPO)
    assert r.returncode == 0, f"selftest exit={r.returncode}\n{r.stdout[-2000:]}"
    assert "SELFTEST PASS" in r.stdout, r.stdout[-2000:]


def _live_writers_under(root: Path) -> list[str]:
    """Every live process whose CWD is inside ``root``, named exactly.

    Reads /proc/<pid>/cwd rather than matching a pattern over argv: an argv
    probe also matches the shell that WROTE the script it looks for, and the
    ``[b]racket`` trick does not hide it because there the pattern is data
    rather than argv (CLAUDE.md A7).  A readlink cannot match this process by
    accident, and this process is excluded by pid regardless.

    Returns one readable row per writer, so a contended cleanup can NAME the
    process holding the directory instead of collapsing to a bare ENOTEMPTY
    that leads the reader nowhere.
    """
    # AN UNAVAILABLE MEASUREMENT IS UNKNOWN, NOT OK. Returning [] when the
    # probe cannot see /proc would make "no writers" and "no eyes" the same
    # answer, and the caller asserts on the empty list -- the fail-open shape
    # this test exists to close. Report the reason as a writer row instead, so
    # the assertion goes RED and says why.
    rows: list[str] = []
    try:
        root = root.resolve()
        pids = os.listdir("/proc")
    except OSError as exc:
        return ["UNKNOWN: cannot enumerate live writers (%s: %s)"
                % (type(exc).__name__, exc)]
    for entry in pids:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == os.getpid():
            continue
        try:
            cwd = Path(os.readlink("/proc/%d/cwd" % pid))
            cwd.relative_to(root)
        except (OSError, ValueError):
            continue
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as fh:
                argv = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            argv = "<exited>"
        rows.append("pid=%d cwd=%s argv=%s" % (pid, cwd, argv.strip()[:160]))
    return rows


_REAP_RECORD = re.compile(
    r"the orphaned band session is reaped "
    r"\(pgid=(?P<pgid>-?\d+) alive=(?P<alive>\w+) gone=(?P<gone>\w+)\)")


def test_bd_mutate_selftest_retries_a_late_enotempty_cleanup(
        tmp_path, monkeypatch, capsys):
    """RED: removing the retry leaves a known selftest-owned tree behind.

    The real selftest exits its TemporaryDirectory context after its last
    battery.  This one-shot replacement models the only cleanup race at that
    seam: a writer creates a file after the remover's traversal and the first
    removal reports ENOTEMPTY.  A second removal must reclaim that same owned
    directory.  It is deterministic rather than a timed competing thread.

    ROW 638 -- and why "modelled" and "only" are both load-bearing.  This test
    twice failed GitHub CI with ``OSError: [Errno 39] Directory not empty`` on
    candidates touching neither the tool nor this file, and passed on test5.
    That text is the OS's own strerror, not the injection's "injected late
    writer", so the escape is the RETRY's real removal meeting a genuinely
    non-empty tree -- there was a SECOND writer, one this test does not own.

    Measured on test5 2026-09-02 (bd-persist/workers/1451/): selftest section 7
    SIGTERMs a child bd-mutate mid-battery, but ``_run_owned`` starts every
    band with ``start_new_session=True``, so the band pytest is its own session
    leader and the signal never reaches it.  It survives with ppid=1, keeps the
    selftest root as its cwd, and goes on creating ``.pytest_cache`` and its
    ``--junitxml`` directory INSIDE the root -- pid 3817652, ppid=1,
    pgid=sid=3817652, still adding entries 6.3s after ``proc.wait()`` returned.
    Widen that window the way CI load widens it and the cleanup reproduces the
    CI text exactly, at ``os.rmdir('kill')`` -- ``kill`` being the orphan's own
    cwd, so the OS names the writer's directory in the error it raises.

    So the three preconditions below are the point of this test, not decoration.
    A run measured on test5 with the orphan ALIVE at retry time and merely idle
    passed every assertion this test used to carry: "the retry worked" and "the
    directory happened to be empty this time" were the same green.  They are
    now separated -- the retry must be shown a non-empty root containing the
    injected racer, and the root must be shown to contain no writer but ours.
    """
    mutate = _load_bd_mutate()
    real_mkdtemp = tempfile.mkdtemp
    rmtree_globals = tempfile.TemporaryDirectory._rmtree.__globals__
    real_rmtree = rmtree_globals.get("_rmtree", tempfile._shutil.rmtree)
    selftest_roots = []
    removals = []
    fired = []
    observed: dict[str, object] = {}

    def track_selftest_mkdtemp(*args, **kwargs):
        directory = kwargs.get("dir", args[2] if len(args) > 2 else None)
        prefix = kwargs.get("prefix", args[1] if len(args) > 1 else None)
        made = Path(real_mkdtemp(*args, **kwargs))
        if directory is None and prefix is None:
            selftest_roots.append(made)
        return str(made)

    def late_writer_rmtree(path, *args, **kwargs):
        candidate = Path(path)
        if candidate in selftest_roots:
            removals.append(candidate)
            if not fired:
                fired.append(candidate)
                (candidate / "late-writer.txt").write_text(
                    "contended\n", encoding="utf-8")
                raise OSError(errno.ENOTEMPTY, "injected late writer")
            # THE RETRY.  Record what it actually has to reclaim, and who else
            # is writing here, BEFORE the removal destroys the evidence for
            # both.  Without this the verdict cannot tell a working retry from
            # a lucky one.
            observed["entries"] = sorted(p.name for p in candidate.iterdir())
            observed["writers"] = _live_writers_under(candidate)
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(mutate.tempfile, "mkdtemp", track_selftest_mkdtemp)
    if "_rmtree" in rmtree_globals:
        monkeypatch.setattr(tempfile, "_rmtree", late_writer_rmtree)
    else:
        monkeypatch.setattr(tempfile._shutil, "rmtree", late_writer_rmtree)

    try:
        rc = mutate._selftest(tmp_path)
    except OSError as exc:
        # NAME THE STEP AND CARRY THE SYSTEM'S OWN WORDS.  A bare ENOTEMPTY
        # here is indistinguishable from a broken injection, and the two lead
        # to opposite actions.
        pytest.fail(
            "the selftest root could not be reclaimed -- %s: %s\n"
            "  entries at retry: %r\n"
            "  live writers at retry: %r\n"
            "A non-empty writer list is row 638: something outside this test "
            "was creating entries in the root while the retry removed it. An "
            "empty one means the retry itself is broken."
            % (type(exc).__name__, exc,
               observed.get("entries"), observed.get("writers")))
    printed = capsys.readouterr().out

    # PRECONDITION 1 -- the selftest owns every writer in its own root, so the
    # only race left at this seam is the one this test injects.  This is the
    # assertion with RED provenance: the base selftest emits no reap record at
    # all, on every host and every schedule.
    record = _REAP_RECORD.search(printed)
    assert record, (
        "the selftest recorded no reap of the band session it orphans in "
        "section 7, so a writer it does not own may still be inside the root "
        "this cleanup removes -- row 638.\n" + printed[-3000:])
    assert int(record.group("pgid")) > 0, (
        "the selftest never learned the orphaned band's session id, so its "
        "reap had no subject: " + record.group(0))
    assert record.group("alive") == "True", (
        "the selftest found no LIVE band session to reap. A reaper that is "
        "satisfied by an already-dead group is the fail-open shape it exists "
        "to prevent, and it would pass here on exactly the schedules that "
        "make row 638 fire: " + record.group(0))
    assert record.group("gone") == "True", (
        "the orphaned band session outlived its reap: " + record.group(0))

    # PRECONDITION 2 -- the retry had something real to reclaim.
    assert observed.get("entries"), (
        "the root was empty at retry time, so the removal below reclaimed "
        "nothing and its success says nothing about the retry")
    assert "late-writer.txt" in observed["entries"], (
        "the injected racer was not present for the retry to reclaim, so a "
        "green verdict here is about a directory that happened to be clean, "
        "not about the retry: %r" % (observed["entries"],))

    # PRECONDITION 3 -- and no writer but ours.
    assert observed.get("writers") == [], (
        "a process outside this test was writing inside the selftest root "
        "while the retry removed it; that is row 638, and it must be NAMED "
        "here rather than surfacing later as a bare ENOTEMPTY:\n  "
        + "\n  ".join(observed.get("writers") or []))

    assert rc == 0
    assert len(selftest_roots) == 1
    assert fired == selftest_roots, "the late-writer cleanup race never fired"
    assert removals == [selftest_roots[0], selftest_roots[0]], removals
    assert not selftest_roots[0].exists(), "the contended selftest root leaked"


def test_bd_mutate_selftest_cleanup_retries_only_enotempty():
    """The bounded contention retry must not launder an unrelated cleanup fault."""
    mutate = _load_bd_mutate()
    attempts = []

    def late_writer_cleanup():
        attempts.append("cleanup")
        if len(attempts) == 1:
            raise OSError(errno.ENOTEMPTY, "late writer")

    mutate._cleanup_selftest_directory(late_writer_cleanup)
    assert attempts == ["cleanup", "cleanup"]

    def denied_cleanup():
        raise PermissionError(errno.EACCES, "not a contention race")

    with pytest.raises(PermissionError, match="not a contention race"):
        mutate._cleanup_selftest_directory(denied_cleanup)


def _one_mutation_result(process, *, expected_id=None):
    """Validate the complete JSON channel while retaining failure evidence."""
    import json as _json

    diag = (f"exit={process.returncode}\n"
            f"stdout={process.stdout[-3000:]!r}\n"
            f"stderr={process.stderr[-3000:]!r}")
    if process.returncode not in (0, 1):
        pytest.fail(diag)
    try:
        payload = _json.loads(process.stdout)
    except ValueError as exc:
        pytest.fail(f"could not parse the complete JSON payload ({exc}):\n"
                    f"{diag}")
    if not isinstance(payload, dict):
        pytest.fail(f"expected a JSON object: {payload!r}\n{diag}")
    rows = payload.get("results")
    if payload.get("total") != 1 or not isinstance(rows, list) or len(rows) != 1:
        pytest.fail(f"expected exactly one mutation result: {payload!r}\n{diag}")
    if payload.get("failing") != [] or process.returncode != 0:
        pytest.fail(f"the selected mutation was not caught: {payload!r}\n{diag}")
    row = rows[0]
    if not isinstance(row, dict):
        pytest.fail(f"expected a mutation result object: {row!r}\n{diag}")
    if expected_id is not None and row.get("id") != expected_id:
        pytest.fail(f"expected mutation id {expected_id!r}, got "
                    f"{row.get('id')!r}\n{diag}")
    return row


def test_mutation_result_failure_preserves_status_stdout_and_stderr():
    process = subprocess.CompletedProcess(
        args=["bd-mutation-test"], returncode=1, stdout="",
        stderr="sentinel-copytree",
    )

    with pytest.raises(_PYTEST_FAILURE) as caught:
        _one_mutation_result(process)

    message = str(caught.value)
    assert "exit=1" in message
    assert "stdout=''" in message
    assert "stderr='sentinel-copytree'" in message


def test_mutation_result_rejects_a_prefixed_json_stream():
    process = subprocess.CompletedProcess(
        args=["bd-mutation-test"], returncode=0,
        stdout=('warning-before-json\n'
                '{"results":[{"state":"CAUGHT"}],"caught":1,'
                '"total":1,"failing":[]}'),
        stderr="",
    )

    with pytest.raises(_PYTEST_FAILURE, match="complete JSON"):
        _one_mutation_result(process)


def test_mutation_result_rejects_a_non_object_without_losing_diagnostics():
    stdout = '[{"id":"one/row","state":"CAUGHT"}]'
    process = subprocess.CompletedProcess(
        args=["bd-mutation-test"], returncode=0, stdout=stdout,
        stderr="sentinel-list",
    )

    with pytest.raises(_PYTEST_FAILURE) as caught:
        _one_mutation_result(process)

    message = str(caught.value)
    assert "expected a JSON object" in message
    assert "exit=0" in message
    assert f"stdout={stdout!r}" in message
    assert "stderr='sentinel-list'" in message


def test_mutation_result_is_bound_to_the_requested_row():
    process = subprocess.CompletedProcess(
        args=["bd-mutation-test"], returncode=0,
        stdout=('{"results":[{"id":"wrong/row","state":"CAUGHT"}],'
                '"caught":1,"total":1,"failing":[]}'),
        stderr="",
    )

    with pytest.raises(_PYTEST_FAILURE, match="route_index/spa_wired"):
        _one_mutation_result(process, expected_id="route_index/spa_wired")


def _bd_mutate_scratch_runner(root):
    runner = root / "toolchain" / "bin" / "bd-mutate"
    runner.parent.mkdir(parents=True)
    runner.write_bytes((BIN / "bd-mutate").read_bytes())
    return runner


def _bd_mutate_scratch_subject(work, file_name="m.py", gate_marker=None):
    tests = work / "tests"
    tests.mkdir(parents=True)
    gate_effect = (
        "from pathlib import Path\n"
        f"Path({str(gate_marker)!r}).write_text('RAN\\n')\n"
        if gate_marker is not None else "")
    (tests / "test_m.py").write_text(
        gate_effect + "def test_value():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    spec = work.parent / f"{work.name}-spec.json"
    spec.write_text(
        '{"schema":"bd-mutate-spec/1","subject":"canonical refusal",'
        '"band":["tests/test_m.py"],"mutants":[{'
        f'"label":"change value","file":"{file_name}","old":"VALUE = 1",'
        '"new":"VALUE = 2","direction":"regression",'
        '"catcher":"tests/test_m.py::test_value"}]}',
        encoding="utf-8",
    )
    return spec


def _bd_mutate_recovery_record(journal, name, target, original, mutant):
    record = journal / name
    record.write_text(json.dumps({
        "path": target.name,
        "label": "recovery write failure",
        "pid": int(record.stem),
        "original_sha": hashlib.sha256(original.encode()).hexdigest(),
        "mutated_sha": hashlib.sha256(mutant.encode()).hexdigest(),
        "original": original,
    }), encoding="utf-8")
    return record


def _bd_mutate_green_band():
    nodeid = "tests/test_m.py::test_value"
    return {
        "rc": 0,
        "tail": "",
        "collected": [nodeid],
        "outcomes": {nodeid: ["passed"]},
        "measurement_error": None,
        "collection_error": False,
    }


def _bd_mutate_emit_fixture(tmp_path, mutate, name="v3_66_9999_emit.json"):
    work = tmp_path / "candidate"
    (work / "tests").mkdir(parents=True)
    (work / "m.py").write_text("VALUE = 1\n", encoding="utf-8")
    (work / "tests" / "test_m.py").write_text(
        "def test_value():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(
        ["git", "-C", str(work), "add", "m.py", "tests/test_m.py"],
        check=True,
    )
    prepared = mutate._prepare_emitted_spec(
        work, name, "emitted-spec ownership",
        ["tests/test_m.py::test_value"], [{
            "label": "value", "file": "m.py", "old": "VALUE = 1",
            "new": "VALUE = 2", "direction": "regression",
            "catcher": "tests/test_m.py::test_value",
        }])
    return work, prepared


def _bd_mutate_main_json(mutate, monkeypatch, capsys, runner, spec, work):
    monkeypatch.setattr(sys, "argv", [
        str(runner), "--spec", str(spec), "--work", str(work), "--json",
    ])
    rc = mutate.main()
    stdout, stderr = capsys.readouterr()
    return rc, json.loads(stdout), stdout, stderr


@pytest.mark.parametrize("child_close_fault", (False, True),
                         ids=("parent-primary", "child-secondary-fd-reuse"))
def test_bd_mutate_emit_prepare_parent_close_settles_unpublished_child(
        tmp_path, monkeypatch, child_close_fault):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    parent = work / "tests" / "mutants"
    parent.mkdir(parents=True)
    (work / "m.py").write_text("VALUE = 1\n", encoding="utf-8")
    (work / "tests" / "test_m.py").write_text(
        "def test_value():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(
        ["git", "-C", str(work), "add", "m.py", "tests/test_m.py"],
        check=True,
    )
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    real_child = mutate._PinnedDirectory.child.__func__
    real_owner_close = mutate._PinnedDirectory.close
    parent_failure = KeyboardInterrupt("emitter parent close primary")
    child_failure = SystemExit(91)
    parent_identities = []
    child_identities = []
    attempts = []
    transfer_start = []
    replacement = []
    fillers = []
    sentinel = tmp_path / "child-close-reuse-sentinel"

    def capture_child(cls, tests_owner, name, expected):
        child = real_child(cls, tests_owner, name, expected)
        parent_identities[:] = [
            _descriptor_identity(original_fstat, fd)
            for fd in tests_owner._owned.values()
        ]
        child_identities[:] = [
            _descriptor_identity(original_fstat, fd)
            for fd in child._owned.values()
        ]
        return child

    def parent_close_then_raise(owner, *, primary=None):
        if owner.path == work / "tests" and not transfer_start:
            transfer_start.append(len(attempts))
            real_owner_close(owner, primary=primary)
            if child_close_fault:
                target_fd = child_identities[-1][0]
                while True:
                    filler_fd = original_open("/dev/null", os.O_RDONLY)
                    fillers.append(
                        _descriptor_identity(original_fstat, filler_fd))
                    if filler_fd > target_fd:
                        break
            raise parent_failure
        return real_owner_close(owner, primary=primary)

    def close_with_child_secondary(fd):
        identity = _descriptor_identity(original_fstat, fd)
        attempts.append(identity)
        if (child_close_fault and child_identities
                and identity == child_identities[-1] and not replacement):
            original_close(fd)
            replacement_fd = original_open(
                sentinel, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            assert replacement_fd == fd, (
                "fixture did not force immediate numeric descriptor reuse")
            replacement.append(
                _descriptor_identity(original_fstat, replacement_fd))
            raise child_failure
        return original_close(fd)

    monkeypatch.setattr(
        mutate._PinnedDirectory, "child", classmethod(capture_child))
    monkeypatch.setattr(mutate._PinnedDirectory, "close", parent_close_then_raise)
    monkeypatch.setattr(mutate.os, "close", close_with_child_secondary)
    try:
        with pytest.raises(BaseException) as caught:
            mutate._prepare_emitted_spec(
                work, "v3_66_9999_parent_close.json", "parent transfer",
                ["tests/test_m.py::test_value"], [{
                    "label": "value", "file": "m.py", "old": "VALUE = 1",
                    "new": "VALUE = 2", "direction": "regression",
                    "catcher": "tests/test_m.py::test_value",
                }])

        assert caught.value is parent_failure
        assert parent_identities and child_identities and len(transfer_start) == 1
        transfer_attempts = attempts[transfer_start[0]:]
        for identity in parent_identities + child_identities:
            assert transfer_attempts.count(identity) == 1
        for identity in child_identities:
            _assert_owner_is_settled(original_fstat, identity)
        if child_close_fault:
            assert len(replacement) == 1
            assert (_descriptor_identity(original_fstat, replacement[0][0]) ==
                    replacement[0])
            assert any("SystemExit" in note and "91" in note
                       for note in getattr(parent_failure, "__notes__", ()))
    finally:
        for identity in replacement + fillers + parent_identities + child_identities:
            _close_if_same_owner(original_fstat, original_close, identity)


def test_bd_mutate_journal_record_close_is_once_after_effect_and_fd_reuse(
        tmp_path, monkeypatch):
    mutate = _load_bd_mutate()
    work = tmp_path / "candidate"
    work.mkdir()
    sentinel = tmp_path / "replacement-sentinel"
    mutate._bind_invoked_runner_identity()
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    record_name = f"{os.getpid()}.json"
    owned = {}
    attempts = []
    replacement = []
    injected = OSError("record close-after-effect")

    def recording_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        identity = _descriptor_identity(original_fstat, fd)
        raw_path = os.fspath(path)
        if raw_path == record_name:
            owned["record"] = identity
        elif raw_path == ".bd-mutate-inflight":
            owned["journal"] = identity
        return fd

    def close_reuse_then_raise(fd):
        identity = _descriptor_identity(original_fstat, fd)
        attempts.append(identity)
        if identity == owned.get("record") and not replacement:
            original_close(fd)
            replacement_fd = original_open(
                sentinel, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            assert replacement_fd == fd, (
                "fixture did not force immediate numeric descriptor reuse")
            replacement.append(
                _descriptor_identity(original_fstat, replacement_fd))
            raise injected
        return original_close(fd)

    monkeypatch.setattr(mutate.os, "open", recording_open)
    monkeypatch.setattr(mutate.os, "close", close_reuse_then_raise)
    try:
        with pytest.raises(BaseException) as caught:
            mutate._journal_write(
                work, "subject.py", "ORIGINAL\n", "MUTATED\n",
                "record close reuse")

        assert caught.value is injected
        assert set(owned) == {"record", "journal"}
        assert attempts.count(owned["record"]) == 1
        assert owned["journal"] in attempts
        assert len(replacement) == 1
        assert (_descriptor_identity(original_fstat, replacement[0][0]) ==
                replacement[0]), "replacement fd was closed by an ambiguous retry"
    finally:
        for identity in replacement + list(owned.values()):
            _close_if_same_owner(original_fstat, original_close, identity)


@pytest.mark.parametrize(
    "evidence_fault", ("primary-add-note", "secondary-str"))
def test_bd_mutate_close_evidence_failures_never_interrupt_owner_drain(
        tmp_path, monkeypatch, evidence_fault):
    mutate = _load_bd_mutate()
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    identities = []
    for index in range(3):
        path = tmp_path / f"hostile-evidence-{index}"
        path.write_bytes(bytes([index]))
        fd = original_open(path, os.O_RDONLY)
        identities.append(_descriptor_identity(original_fstat, fd))

    class RejectingNotePrimary(KeyboardInterrupt):
        def add_note(self, note):
            raise RuntimeError("primary rejected close evidence")

    class UnprintableCloseFailure(SystemExit):
        def __str__(self):
            raise RuntimeError("secondary close text is hostile")

    if evidence_fault == "primary-add-note":
        primary = RejectingNotePrimary("journal primary")
        failures = [SystemExit(84 + index) for index in range(3)]
    else:
        primary = KeyboardInterrupt("journal primary")
        failures = [UnprintableCloseFailure(84 + index)
                    for index in range(3)]
    attempts = []
    owned = {name: identity[0] for name, identity in zip(
        ("hostile first", "hostile second", "hostile third"), identities)}

    def close_then_fault(fd):
        identity = _descriptor_identity(original_fstat, fd)
        index = identities.index(identity)
        attempts.append(identity)
        original_close(fd)
        raise failures[index]

    monkeypatch.setattr(mutate.os, "close", close_then_fault)
    try:
        with pytest.raises(BaseException) as caught:
            try:
                raise primary
            finally:
                mutate._settle_owned_fds(
                    owned, primary=sys.exc_info()[1])

        assert caught.value is primary
        assert caught.value.args == ("journal primary",)
        assert attempts == identities
        assert owned == {}
        notes = getattr(primary, "__notes__", ())
        assert len(notes) == 3
        for name, identity, note in zip(
                ("hostile first", "hostile second", "hostile third"),
                identities, notes):
            assert name in note
            assert f"fd {identity[0]}" in note
            assert type(failures[0]).__name__ in note
            if evidence_fault == "secondary-str":
                assert "<unprintable>" in note
        for identity in identities:
            _assert_owner_is_settled(original_fstat, identity)
    finally:
        for identity in identities:
            _close_if_same_owner(original_fstat, original_close, identity)


def test_bd_mutate_open_journal_parent_cancellation_settles_unpublished_child(
        tmp_path, monkeypatch):
    mutate = _load_bd_mutate()
    work = tmp_path / "candidate"
    work.mkdir()
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    owned = {}
    attempts = []
    parent_failure = KeyboardInterrupt("journal parent close cancellation")
    child_failure = SystemExit(71)

    def recording_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        raw_path = os.fspath(path)
        if raw_path == str(work):
            owned["parent"] = _descriptor_identity(original_fstat, fd)
        elif raw_path == ".bd-mutate-inflight":
            owned["child"] = _descriptor_identity(original_fstat, fd)
        return fd

    def close_then_cancel(fd):
        identity = _descriptor_identity(original_fstat, fd)
        attempts.append(identity)
        original_close(fd)
        if identity == owned.get("parent"):
            raise parent_failure
        if identity == owned.get("child"):
            raise child_failure

    monkeypatch.setattr(mutate.os, "open", recording_open)
    monkeypatch.setattr(mutate.os, "close", close_then_cancel)
    try:
        with pytest.raises(BaseException) as caught:
            mutate._open_journal_dir(work, create=True)

        assert set(owned) == {"parent", "child"}
        owned_attempts = [identity for identity in attempts
                          if identity in owned.values()]
        assert owned_attempts == [owned["parent"], owned["child"]]
        assert caught.value is parent_failure
        assert caught.value.args == ("journal parent close cancellation",)
        assert any("journal directory" in note and
                   "SystemExit" in note and "71" in note
                   for note in getattr(parent_failure, "__notes__", ()))
        for identity in owned.values():
            _assert_owner_is_settled(original_fstat, identity)
    finally:
        for identity in owned.values():
            _close_if_same_owner(original_fstat, original_close, identity)


def test_bd_mutate_journal_unlink_cleanup_preserves_exact_primary(
        tmp_path, monkeypatch):
    mutate = _load_bd_mutate()
    work = tmp_path / "candidate"
    work.mkdir()
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    record = journal / "123.json"
    record.write_text('{"owned": true}\n', encoding="utf-8")
    directory_stat = journal.stat()
    record_stat = record.stat()
    expected_dir = (directory_stat.st_dev, directory_stat.st_ino,
                    stat.S_IFMT(directory_stat.st_mode))
    expected_record = (record_stat.st_dev, record_stat.st_ino,
                       stat.S_IFMT(record_stat.st_mode))
    mutate._bind_invoked_runner_identity()
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    journal_owner = []
    attempts = []
    unlink_calls = []
    primary = KeyboardInterrupt("journal unlink primary")
    close_failure = SystemExit(72)

    def recording_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if os.fspath(path) == ".bd-mutate-inflight":
            journal_owner.append(_descriptor_identity(original_fstat, fd))
        return fd

    def failing_unlink(path, *args, **kwargs):
        unlink_calls.append((os.fspath(path), kwargs.get("dir_fd")))
        raise primary

    def close_then_cancel(fd):
        identity = _descriptor_identity(original_fstat, fd)
        attempts.append(identity)
        original_close(fd)
        if journal_owner and identity == journal_owner[0]:
            raise close_failure

    monkeypatch.setattr(mutate.os, "open", recording_open)
    monkeypatch.setattr(mutate.os, "unlink", failing_unlink)
    monkeypatch.setattr(mutate.os, "close", close_then_cancel)
    try:
        with pytest.raises(BaseException) as caught:
            mutate._journal_unlink(
                work, record, expected_dir, expected_record)

        assert caught.value is primary
        assert caught.value.args == ("journal unlink primary",)
        assert unlink_calls == [(record.name, journal_owner[0][0])]
        assert attempts.count(journal_owner[0]) == 1
        assert record.exists(), "before-effect unlink fault removed the record"
        assert any("journal directory" in note and
                   "SystemExit" in note and "72" in note
                   for note in getattr(primary, "__notes__", ()))
        _assert_owner_is_settled(original_fstat, journal_owner[0])
    finally:
        for identity in journal_owner:
            _close_if_same_owner(original_fstat, original_close, identity)


def test_bd_mutate_journal_write_cleanup_preserves_primary_and_drains_all(
        tmp_path, monkeypatch):
    mutate = _load_bd_mutate()
    work = tmp_path / "candidate"
    work.mkdir()
    mutate._bind_invoked_runner_identity()
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    original_write = os.write
    record_name = f"{os.getpid()}.json"
    owned = {}
    attempts = []
    writes = []
    primary = KeyboardInterrupt("journal write primary")
    record_close = SystemExit(76)
    journal_close = SystemExit(77)

    def recording_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        identity = _descriptor_identity(original_fstat, fd)
        raw_path = os.fspath(path)
        if raw_path == record_name:
            owned["record"] = identity
        elif raw_path == ".bd-mutate-inflight":
            owned["journal"] = identity
        return fd

    def failing_write(fd, data):
        identity = _descriptor_identity(original_fstat, fd)
        if identity == owned.get("record") and not writes:
            writes.append((identity, len(data)))
            raise primary
        return original_write(fd, data)

    def close_then_cancel(fd):
        identity = _descriptor_identity(original_fstat, fd)
        attempts.append(identity)
        original_close(fd)
        if identity == owned.get("record"):
            raise record_close
        if identity == owned.get("journal"):
            raise journal_close

    monkeypatch.setattr(mutate.os, "open", recording_open)
    monkeypatch.setattr(mutate.os, "write", failing_write)
    monkeypatch.setattr(mutate.os, "close", close_then_cancel)
    try:
        with pytest.raises(BaseException) as caught:
            mutate._journal_write(
                work, "subject.py", "ORIGINAL\n", "MUTATED\n",
                "write primary")

        assert caught.value is primary
        assert caught.value.args == ("journal write primary",)
        assert len(writes) == 1 and writes[0][1] > 0
        assert attempts.index(owned["record"]) < attempts.index(owned["journal"])
        assert attempts.count(owned["record"]) == 1
        assert attempts.count(owned["journal"]) == 1
        notes = getattr(primary, "__notes__", ())
        assert any("journal record" in note and "76" in note for note in notes)
        assert any("journal directory" in note and "77" in note for note in notes)
        for identity in owned.values():
            _assert_owner_is_settled(original_fstat, identity)
    finally:
        for identity in owned.values():
            _close_if_same_owner(original_fstat, original_close, identity)


def test_bd_mutate_journal_records_cleanup_preserves_primary_and_drains_all(
        tmp_path, monkeypatch):
    mutate = _load_bd_mutate()
    work = tmp_path / "candidate"
    work.mkdir()
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    (journal / "123.json").write_text('{"owned": true}\n', encoding="utf-8")
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    original_read = os.read
    owned = {}
    attempts = []
    reads = []
    primary = KeyboardInterrupt("journal record read primary")
    record_close = SystemExit(78)
    journal_close = SystemExit(79)

    def recording_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        identity = _descriptor_identity(original_fstat, fd)
        raw_path = os.fspath(path)
        if raw_path == "123.json":
            owned["record"] = identity
        elif raw_path == ".bd-mutate-inflight":
            owned["journal"] = identity
        return fd

    def failing_read(fd, size):
        identity = _descriptor_identity(original_fstat, fd)
        if identity == owned.get("record") and not reads:
            reads.append((identity, size))
            raise primary
        return original_read(fd, size)

    def close_then_cancel(fd):
        identity = _descriptor_identity(original_fstat, fd)
        attempts.append(identity)
        original_close(fd)
        if identity == owned.get("record"):
            raise record_close
        if identity == owned.get("journal"):
            raise journal_close

    monkeypatch.setattr(mutate.os, "open", recording_open)
    monkeypatch.setattr(mutate.os, "read", failing_read)
    monkeypatch.setattr(mutate.os, "close", close_then_cancel)
    try:
        with pytest.raises(BaseException) as caught:
            mutate._journal_records(work)

        assert caught.value is primary
        assert caught.value.args == ("journal record read primary",)
        assert reads == [(owned["record"], 65536)]
        assert attempts.index(owned["record"]) < attempts.index(owned["journal"])
        assert attempts.count(owned["record"]) == 1
        assert attempts.count(owned["journal"]) == 1
        notes = getattr(primary, "__notes__", ())
        assert any("journal record" in note and "78" in note for note in notes)
        assert any("journal directory" in note and "79" in note for note in notes)
        for identity in owned.values():
            _assert_owner_is_settled(original_fstat, identity)
    finally:
        for identity in owned.values():
            _close_if_same_owner(original_fstat, original_close, identity)


def test_bd_mutate_journal_record_close_primary_survives_outer_close_failure(
        tmp_path, monkeypatch):
    mutate = _load_bd_mutate()
    work = tmp_path / "candidate"
    work.mkdir()
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    (journal / "123.json").write_text('{"owned": true}\n', encoding="utf-8")
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    original_read = os.read
    owned = {}
    attempts = []
    reads = []
    record_close = KeyboardInterrupt("journal record close primary")
    journal_close = SystemExit(80)

    def recording_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        identity = _descriptor_identity(original_fstat, fd)
        raw_path = os.fspath(path)
        if raw_path == "123.json":
            owned["record"] = identity
        elif raw_path == ".bd-mutate-inflight":
            owned["journal"] = identity
        return fd

    def recording_read(fd, size):
        identity = _descriptor_identity(original_fstat, fd)
        if identity == owned.get("record"):
            reads.append((identity, size))
        return original_read(fd, size)

    def close_then_cancel(fd):
        identity = _descriptor_identity(original_fstat, fd)
        attempts.append(identity)
        original_close(fd)
        if identity == owned.get("record"):
            raise record_close
        if identity == owned.get("journal"):
            raise journal_close

    monkeypatch.setattr(mutate.os, "open", recording_open)
    monkeypatch.setattr(mutate.os, "read", recording_read)
    monkeypatch.setattr(mutate.os, "close", close_then_cancel)
    try:
        with pytest.raises(BaseException) as caught:
            mutate._journal_records(work)

        assert caught.value is record_close
        assert caught.value.args == ("journal record close primary",)
        assert len(reads) >= 2 and reads[-1][1] == 65536
        assert attempts.index(owned["record"]) < attempts.index(owned["journal"])
        assert attempts.count(owned["record"]) == 1
        assert attempts.count(owned["journal"]) == 1
        assert any("journal directory" in note and "80" in note
                   for note in getattr(record_close, "__notes__", ()))
        for identity in owned.values():
            _assert_owner_is_settled(original_fstat, identity)
    finally:
        for identity in owned.values():
            _close_if_same_owner(original_fstat, original_close, identity)


@pytest.mark.parametrize("stage", ("hash", "cache"))
def test_bd_mutate_journal_preflight_cancellation_settles_subject_owner(
        tmp_path, monkeypatch, stage):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    subject.write_text(original, encoding="utf-8")
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    record = _bd_mutate_recovery_record(
        journal, "123.json", subject, original, mutant)
    cancellation = KeyboardInterrupt(f"preflight {stage} cancellation")
    real_init = mutate._PinnedSubject.__init__
    real_sha = mutate._PinnedSubject.sha
    acquired = []
    fired = []

    def capture_init(owner, *args, **kwargs):
        real_init(owner, *args, **kwargs)
        acquired.append([
            _descriptor_identity(os.fstat, fd)
            for fd in owner._owned.values()
        ])

    def cancelling_sha(owner):
        if stage == "hash" and not fired:
            fired.append(stage)
            raise cancellation
        return real_sha(owner)

    def cancelling_cache(owner):
        fired.append(stage)
        raise cancellation

    monkeypatch.setattr(mutate._PinnedSubject, "__init__", capture_init)
    monkeypatch.setattr(mutate._PinnedSubject, "sha", cancelling_sha)
    if stage == "cache":
        monkeypatch.setattr(mutate, "_purge_subject_pycache", cancelling_cache)

    with pytest.raises(BaseException) as caught:
        mutate.journal_preflight(work)

    assert caught.value is cancellation and fired == [stage]
    assert len(acquired) == 1 and record.exists()
    for identity in acquired[0]:
        _assert_owner_is_settled(os.fstat, identity)


@pytest.mark.parametrize("stage", ("hash", "restore", "cache"))
def test_bd_mutate_journal_recovery_cancellation_settles_subject_owner(
        tmp_path, monkeypatch, stage):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    subject.write_text(mutant, encoding="utf-8")
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    record = _bd_mutate_recovery_record(
        journal, "123.json", subject, original, mutant)
    cancellation = SystemExit(92)
    real_init = mutate._PinnedSubject.__init__
    real_sha = mutate._PinnedSubject.sha
    real_write = mutate._write_subject_text
    acquired = []
    fired = []

    def capture_init(owner, *args, **kwargs):
        real_init(owner, *args, **kwargs)
        acquired.append([
            _descriptor_identity(os.fstat, fd)
            for fd in owner._owned.values()
        ])

    def cancelling_sha(owner):
        if stage == "hash" and not fired:
            fired.append(stage)
            raise cancellation
        return real_sha(owner)

    def cancelling_write(*args, **kwargs):
        fired.append(stage)
        raise cancellation

    def write_then_cancel_cache(*args, **kwargs):
        return real_write(*args, **kwargs)

    def cancelling_cache(owner):
        fired.append(stage)
        raise cancellation

    monkeypatch.setattr(mutate._PinnedSubject, "__init__", capture_init)
    monkeypatch.setattr(mutate._PinnedSubject, "sha", cancelling_sha)
    if stage == "restore":
        monkeypatch.setattr(mutate, "_write_subject_text", cancelling_write)
    elif stage == "cache":
        monkeypatch.setattr(mutate, "_write_subject_text", write_then_cancel_cache)
        monkeypatch.setattr(mutate, "_purge_subject_pycache", cancelling_cache)

    with pytest.raises(BaseException) as caught:
        mutate.journal_recover(work)

    assert caught.value is cancellation and fired == [stage]
    assert len(acquired) == 1 and record.exists()
    expected = original if stage == "cache" else mutant
    assert subject.read_text(encoding="utf-8") == expected
    for identity in acquired[0]:
        _assert_owner_is_settled(os.fstat, identity)


@pytest.mark.parametrize("mode", ("zero-selected", "battery-cancelled"))
def test_bd_mutate_main_settles_live_prepared_emit_owner(
        tmp_path, monkeypatch, mode):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    emit_parent = work / "tests" / "mutants"
    emit_parent.mkdir(parents=True)
    spec = tmp_path / "spec.json"
    spec.write_text("[]\n", encoding="utf-8")
    owner = mutate._PinnedDirectory(emit_parent)
    identities = [
        _descriptor_identity(os.fstat, fd) for fd in owner._owned.values()
    ]
    prepared = (owner, Path("tests/mutants/v3_66_9999_main_owner.json"), b"{}\n")
    cancellation = KeyboardInterrupt("main battery cancellation")
    selected = [] if mode == "zero-selected" else [{
        "label": "one", "file": "m.py", "old": "A", "new": "B",
        "direction": "regression",
    }]

    monkeypatch.setattr(sys, "argv", [
        str(runner), "--spec", str(spec), "--work", str(work),
        "--emit-spec", "v3_66_9999_main_owner.json", "--subject", "owner",
    ])
    monkeypatch.setattr(mutate, "_normalise_spec",
                        lambda *_args, **_kwargs: (selected, ["tests/test_m.py"]))
    monkeypatch.setattr(mutate, "_prepare_emitted_spec",
                        lambda *_args, **_kwargs: prepared)
    if mode == "battery-cancelled":
        monkeypatch.setattr(
            mutate, "run_battery",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(cancellation))

    if mode == "battery-cancelled":
        with pytest.raises(BaseException) as caught:
            mutate.main()
        assert caught.value is cancellation
    else:
        assert mutate.main() == 2
    for identity in identities:
        _assert_owner_is_settled(os.fstat, identity)


@pytest.mark.parametrize("topology", ("equal", "descendant", "ancestor"))
def test_bd_mutate_refuses_the_invoked_repository_as_work(tmp_path, topology):
    """The runner must never mutate the repository that supplies its own code."""
    root = tmp_path / "runner-repository"
    runner = _bd_mutate_scratch_runner(root)
    if topology == "equal":
        work = root
        subject = work / "m.py"
    elif topology == "descendant":
        work = root / "nested-subject"
        subject = work / "m.py"
    else:
        work = tmp_path
        subject = root / "subject.py"
    work.mkdir(parents=True, exist_ok=True)
    subject.write_text("VALUE = 1\n", encoding="utf-8")
    file_name = str(subject.relative_to(work))
    gate_marker = tmp_path / "gate-ran.marker"
    spec = _bd_mutate_scratch_subject(work, file_name, gate_marker)
    runner_before = runner.read_bytes()
    subject_before = subject.read_bytes()
    if topology == "equal":
        assert work.resolve() == root.resolve()
    elif topology == "descendant":
        assert work.resolve().is_relative_to(root.resolve())
    else:
        assert root.resolve().is_relative_to(work.resolve())

    result = subprocess.run(
        [sys.executable, str(runner), "--spec", str(spec),
         "--work", str(work), "--json"],
        cwd=work, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "refusing work that intersects the repository containing this bd-mutate" in result.stderr
    assert subject.read_bytes() == subject_before
    assert runner.read_bytes() == runner_before
    assert not gate_marker.exists()


@pytest.mark.parametrize("escape", ("traversal", "symlink"))
def test_bd_mutate_refuses_a_subject_outside_work(tmp_path, escape):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    work.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    if escape == "traversal":
        file_name = "../outside.py"
    else:
        file_name = "link.py"
        (work / file_name).symlink_to(outside)
    spec = _bd_mutate_scratch_subject(work, file_name)

    result = subprocess.run(
        [sys.executable, str(runner), "--spec", str(spec),
         "--work", str(work), "--json"],
        cwd=work, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "mutant subject escapes --work" in result.stderr
    assert outside.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_bd_mutate_refuses_a_hardlinked_subject(tmp_path):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    work.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    subject = work / "m.py"
    os.link(outside, subject)
    gate_marker = tmp_path / "gate-ran.marker"
    spec = _bd_mutate_scratch_subject(work, "m.py", gate_marker)
    assert subject.stat().st_nlink > 1

    result = subprocess.run(
        [sys.executable, str(runner), "--spec", str(spec),
         "--work", str(work), "--json"],
        cwd=work, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "mutant subject has multiple hard links" in result.stderr
    assert outside.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not gate_marker.exists()


@pytest.mark.parametrize("escape", ("traversal", "absolute", "symlink"))
def test_bd_mutate_recovery_refuses_a_target_outside_work(tmp_path, escape):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    work.mkdir()
    outside = tmp_path / "outside.py"
    original = "VALUE = 1\n"
    mutant = "VALUE = 2\n"
    outside.write_text(mutant, encoding="utf-8")
    if escape == "traversal":
        rel = "../outside.py"
    elif escape == "absolute":
        rel = str(outside)
    else:
        rel = "link.py"
        (work / rel).symlink_to(outside)
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    (journal / "123.json").write_text(json.dumps({
        "path": rel,
        "label": "legacy escape",
        "pid": 123,
        "original_sha": hashlib.sha256(original.encode()).hexdigest(),
        "mutated_sha": hashlib.sha256(mutant.encode()).hexdigest(),
        "original": original,
    }), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(runner), "--recover", "--work", str(work)],
        cwd=work, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "recovery target escapes --work" in result.stderr
    assert outside.read_text(encoding="utf-8") == mutant
    assert (journal / "123.json").exists()


def test_bd_mutate_symlink_loop_is_unrunnable_not_escaped(tmp_path):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    work.mkdir()
    loop = work / "loop.py"
    loop.symlink_to(loop.name)
    gate_marker = tmp_path / "gate-ran.marker"
    spec = _bd_mutate_scratch_subject(work, "loop.py", gate_marker)

    result = subprocess.run(
        [sys.executable, str(runner), "--spec", str(spec),
         "--work", str(work), "--json"],
        cwd=work, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "mutant subject is unavailable" in result.stderr
    assert "Traceback" not in result.stderr
    assert not gate_marker.exists()


def test_bd_mutate_ancestor_recovery_does_not_purge_runner_caches(tmp_path):
    runner_root = tmp_path / "runner-repository"
    runner = _bd_mutate_scratch_runner(runner_root)
    cache = runner_root / "pkg" / "__pycache__" / "sentinel.pyc"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"UNRELATED CACHE\n")
    cache_before = cache.read_bytes()
    target = tmp_path / "target.py"
    subject_cache = tmp_path / "__pycache__" / "target.cpython-312.pyc"
    subject_cache.parent.mkdir()
    subject_cache.write_bytes(b"STALE MUTANT BYTECODE\n")
    original = "VALUE = 1\n"
    mutant = "VALUE = 2\n"
    target.write_text(mutant, encoding="utf-8")
    journal = tmp_path / ".bd-mutate-inflight"
    journal.mkdir()
    (journal / "123.json").write_text(json.dumps({
        "path": target.name,
        "label": "contained ancestor recovery",
        "pid": 123,
        "original_sha": hashlib.sha256(original.encode()).hexdigest(),
        "mutated_sha": hashlib.sha256(mutant.encode()).hexdigest(),
        "original": original,
    }), encoding="utf-8")
    assert runner_root.resolve().is_relative_to(tmp_path.resolve())

    result = subprocess.run(
        [sys.executable, str(runner), "--recover", "--work", str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_text(encoding="utf-8") == original
    assert not subject_cache.exists()
    assert cache.exists() and cache.read_bytes() == cache_before


def test_bd_mutate_ancestor_recovery_cannot_target_runner_repository(tmp_path):
    runner_root = tmp_path / "runner-repository"
    runner = _bd_mutate_scratch_runner(runner_root)
    subprocess.run(["git", "init", "-q", str(runner_root)], check=True)
    victim = runner_root / "victim.py"
    original = "VALUE = 1\n"
    mutant = "VALUE = 2\n"
    victim.write_text(mutant, encoding="utf-8")
    subprocess.run(["git", "-C", str(runner_root), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(runner_root), "-c", "user.name=Test",
        "-c", "user.email=test@example.invalid", "commit", "-qm", "base",
    ], check=True)
    journal = tmp_path / ".bd-mutate-inflight"
    journal.mkdir()
    record = journal / "123.json"
    record.write_text(json.dumps({
        "path": "runner-repository/victim.py",
        "label": "runner-tree ancestor recovery",
        "pid": 123,
        "original_sha": hashlib.sha256(original.encode()).hexdigest(),
        "mutated_sha": hashlib.sha256(mutant.encode()).hexdigest(),
        "original": original,
    }), encoding="utf-8")
    before = record.read_bytes()

    result = subprocess.run(
        [sys.executable, str(runner), "--recover", "--work", str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "recovery target aliases the invoked runner repository" in result.stderr
    assert victim.read_text(encoding="utf-8") == mutant
    assert record.read_bytes() == before
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("operation", ("ordinary", "recover"))
def test_bd_mutate_work_root_symlink_loop_is_structured_unrunnable(
        tmp_path, operation):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    spec = _bd_mutate_scratch_subject(candidate)
    loop = tmp_path / "loop"
    loop.symlink_to(loop.name)
    argv = [sys.executable, str(runner)]
    if operation == "recover":
        argv += ["--recover"]
    else:
        argv += ["--spec", str(spec)]
    argv += ["--work", str(loop), "--json"]

    result = subprocess.run(
        argv, cwd=tmp_path, capture_output=True, text=True, timeout=120)

    assert result.returncode == 2, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["exit"] == 2 and report["rows"] == []
    assert "work root is unavailable" in result.stderr
    assert "Traceback" not in result.stderr


def test_bd_mutate_recovery_retains_journal_when_subject_cache_is_symlink(
        tmp_path):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    work.mkdir()
    target = work / "target.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    target.write_text(mutant, encoding="utf-8")
    external = tmp_path / "external-cache"
    external.mkdir()
    stale = external / "target.pyc"
    stale.write_bytes(b"STALE MUTANT BYTECODE\n")
    (work / "__pycache__").symlink_to(external, target_is_directory=True)
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    record = journal / "123.json"
    record.write_text(json.dumps({
        "path": target.name,
        "label": "symlink cache recovery",
        "pid": 123,
        "original_sha": hashlib.sha256(original.encode()).hexdigest(),
        "mutated_sha": hashlib.sha256(mutant.encode()).hexdigest(),
        "original": original,
    }), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(runner), "--recover", "--work", str(work)],
        cwd=work, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert target.read_text() == original
    assert record.exists(), "failed cache proof discarded the recovery journal"
    assert stale.read_bytes() == b"STALE MUTANT BYTECODE\n"
    assert (work / "__pycache__").is_symlink()
    assert "recovered target.py" not in result.stdout
    assert "cache" in result.stderr.lower() and "refus" in result.stderr.lower()


def test_bd_mutate_already_clean_recovery_removes_cache_before_journal(
        tmp_path):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    work.mkdir()
    target = work / "target.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    target.write_text(original, encoding="utf-8")
    cache = work / "__pycache__"
    cache.mkdir()
    (cache / "target.pyc").write_bytes(b"STALE MUTANT BYTECODE\n")
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    record = journal / "123.json"
    record.write_text(json.dumps({
        "path": target.name,
        "label": "already clean recovery",
        "pid": 123,
        "original_sha": hashlib.sha256(original.encode()).hexdigest(),
        "mutated_sha": hashlib.sha256(mutant.encode()).hexdigest(),
        "original": original,
    }), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(runner), "--recover", "--work", str(work)],
        cwd=work, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not cache.exists()
    assert not record.exists()
    assert "already clean: target.py" in result.stdout


def test_bd_mutate_ordinary_preflight_retains_clean_journal_on_cache_symlink(
        tmp_path):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    work.mkdir()
    target = work / "m.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    target.write_text(original, encoding="utf-8")
    baseline_marker = tmp_path / "baseline-ran.marker"
    spec = _bd_mutate_scratch_subject(work, "m.py", baseline_marker)
    external = tmp_path / "external-cache"
    external.mkdir()
    stale = external / "m.pyc"
    stale.write_bytes(b"STALE MUTANT BYTECODE\n")
    cache = work / "__pycache__"
    cache.symlink_to(external, target_is_directory=True)
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    record = journal / "123.json"
    record.write_text(json.dumps({
        "path": target.name,
        "label": "already clean ordinary preflight",
        "pid": 123,
        "original_sha": hashlib.sha256(original.encode()).hexdigest(),
        "mutated_sha": hashlib.sha256(mutant.encode()).hexdigest(),
        "original": original,
    }), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(runner), "--spec", str(spec),
         "--work", str(work), "--json"],
        cwd=work, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert record.exists(), "ordinary preflight discarded its recovery proof"
    assert cache.is_symlink() and stale.read_bytes() == b"STALE MUTANT BYTECODE\n"
    assert not baseline_marker.exists(), "baseline ran with stale cache unproved"
    assert "cache cleanup is unproved" in result.stderr
    assert "Traceback" not in result.stderr


def test_bd_mutate_cache_purge_requires_verified_absence(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    cache = work / "pkg" / "__pycache__"
    cache.mkdir(parents=True)
    stale = cache / "m.pyc"
    stale.write_bytes(b"STALE\n")
    calls = []

    def no_effect(path, *args, **kwargs):
        calls.append(Path(path))

    monkeypatch.setattr(mutate.os, "unlink", no_effect)
    with pytest.raises(OSError, match="not empty|still exists after removal"):
        mutate._purge_pycache(work)

    assert len(calls) == 1 and calls[0].name.startswith(".bd-mutate-owned-")
    assert stale.read_bytes() == b"STALE\n"


@pytest.mark.parametrize("location", ("root", "nested-child"))
def test_bd_mutate_cache_rename_after_effect_is_reconciled(
        tmp_path, monkeypatch, location):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    cache = work / "pkg" / "__pycache__"
    cache.mkdir(parents=True)
    stale = cache / "m.pyc"
    stale.write_bytes(b"OWNED CACHE\n")
    real_rename = mutate._rename_noreplace
    primary = OSError(f"{location} rename-after-effect")
    fired = []

    def rename_then_raise(src_fd, src, dst_fd, dst):
        target = (src == "__pycache__" if location == "root" else src == "m.pyc")
        if target and not fired:
            real_rename(src_fd, src, dst_fd, dst)
            fired.append((src, dst))
            raise primary
        return real_rename(src_fd, src, dst_fd, dst)

    monkeypatch.setattr(mutate, "_rename_noreplace", rename_then_raise)
    with pytest.raises(BaseException) as caught:
        mutate._purge_pycache(work)

    assert caught.value is primary and len(fired) == 1
    assert stale.read_bytes() == b"OWNED CACHE\n"
    assert not list(work.rglob(".bd-mutate-owned-*"))


def test_bd_mutate_cache_rename_reconciliation_preserves_a_replacement_race(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    cache = work / "pkg" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "m.pyc").write_bytes(b"OWNED CACHE\n")
    real_rename = mutate._rename_noreplace
    primary = OSError("root rename replacement race")
    fired = []

    def rename_then_replace(src_fd, src, dst_fd, dst):
        if src == "__pycache__" and not fired:
            real_rename(src_fd, src, dst_fd, dst)
            cache.mkdir()
            (cache / "sentinel.pyc").write_bytes(b"FOREIGN CACHE\n")
            fired.append(dst)
            raise primary
        return real_rename(src_fd, src, dst_fd, dst)

    monkeypatch.setattr(mutate, "_rename_noreplace", rename_then_replace)
    with pytest.raises(BaseException) as caught:
        mutate._purge_pycache(work)

    assert caught.value is primary and len(fired) == 1
    assert (cache / "sentinel.pyc").read_bytes() == b"FOREIGN CACHE\n"
    retained = list((work / "pkg").glob(".bd-mutate-owned-cache-*"))
    assert len(retained) == 1
    assert (retained[0] / "m.pyc").read_bytes() == b"OWNED CACHE\n"
    assert any("reconciliation" in note or "replacement" in note
               for note in getattr(primary, "__notes__", ()))


def test_bd_mutate_cache_root_swap_never_deletes_replacement_tree(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    held = tmp_path / "candidate.held"
    cache = work / "pkg" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "m.pyc").write_bytes(b"OWNED CACHE\n")
    external = tmp_path / "external"
    external_cache = external / "pkg" / "__pycache__"
    external_cache.mkdir(parents=True)
    sentinel = external_cache / "sentinel.pyc"
    sentinel.write_bytes(b"EXTERNAL SENTINEL\n")
    real_purge = mutate._purge_cache_descendants
    fired = []

    def swap_root_then_purge(directory_fd, display, *args, **kwargs):
        if not fired:
            work.rename(held)
            work.symlink_to(external, target_is_directory=True)
            fired.append(True)
        return real_purge(directory_fd, display, *args, **kwargs)

    monkeypatch.setattr(mutate, "_purge_cache_descendants", swap_root_then_purge)
    with pytest.raises(ValueError, match="directory detached"):
        mutate._purge_pycache(work)

    assert fired == [True]
    assert sentinel.read_bytes() == b"EXTERNAL SENTINEL\n"
    assert (held / "pkg" / "__pycache__" / "m.pyc").read_bytes() == b"OWNED CACHE\n"


def test_bd_mutate_refuses_symlinked_journal_before_ordinary_run(tmp_path):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    subject.write_text("VALUE = 1\n", encoding="utf-8")
    baseline_marker = tmp_path / "baseline-ran.marker"
    spec = _bd_mutate_scratch_subject(work, "m.py", baseline_marker)
    external = tmp_path / "external-journal"
    external.mkdir()
    sentinel = external / "sentinel.json"
    sentinel.write_text('{"operator":"owned"}\n', encoding="utf-8")
    (work / ".bd-mutate-inflight").symlink_to(
        external, target_is_directory=True)

    result = subprocess.run(
        [sys.executable, str(runner), "--spec", str(spec),
         "--work", str(work), "--json"],
        cwd=work, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["exit"] == 2 and report["rows"] == []
    assert "journal containment" in result.stderr
    assert subject.read_text() == "VALUE = 1\n"
    assert sentinel.read_text() == '{"operator":"owned"}\n'
    assert not baseline_marker.exists()
    assert "Traceback" not in result.stderr


def test_bd_mutate_recovery_refuses_symlinked_external_journal(tmp_path):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    subject.write_text(mutant, encoding="utf-8")
    external = tmp_path / "external-journal"
    external.mkdir()
    record = external / "123.json"
    record_bytes = json.dumps({
        "path": "m.py", "label": "external operator record", "pid": 123,
        "original_sha": hashlib.sha256(original.encode()).hexdigest(),
        "mutated_sha": hashlib.sha256(mutant.encode()).hexdigest(),
        "original": original,
    }).encode()
    record.write_bytes(record_bytes)
    (work / ".bd-mutate-inflight").symlink_to(
        external, target_is_directory=True)

    result = subprocess.run(
        [sys.executable, str(runner), "--recover", "--work", str(work)],
        cwd=work, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "journal containment" in result.stderr
    assert subject.read_text() == mutant
    assert record.read_bytes() == record_bytes
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("operation", ("preflight", "recover"))
@pytest.mark.parametrize("replacement", ("directory", "record"))
def test_bd_mutate_journal_unlink_is_bound_to_read_identity(
        tmp_path, monkeypatch, capsys, operation, replacement):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    subject.write_text(original, encoding="utf-8")
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    record = journal / "123.json"
    valid = json.dumps({
        "path": "m.py", "label": "read identity", "pid": 123,
        "original_sha": hashlib.sha256(original.encode()).hexdigest(),
        "mutated_sha": hashlib.sha256(mutant.encode()).hexdigest(),
        "original": original,
    }).encode()
    record.write_bytes(valid)
    replacement_bytes = b'{"operator":"replacement evidence"}\n'
    original_record = tmp_path / "original-record.json"
    detached_journal = tmp_path / "original-journal"
    real_purge = mutate._purge_subject_pycache
    fired = []

    def swap_after_read(target):
        assert real_purge(target) == 0
        fired.append(replacement)
        if replacement == "directory":
            journal.rename(detached_journal)
            journal.mkdir()
            (journal / record.name).write_bytes(replacement_bytes)
        else:
            record.rename(original_record)
            record.write_bytes(replacement_bytes)
        return 0

    monkeypatch.setattr(mutate, "_purge_subject_pycache", swap_after_read)
    if operation == "preflight":
        rc, messages = mutate.journal_preflight(work)
        diagnostic = "\n".join(messages)
    else:
        rc = mutate.journal_recover(work)
        diagnostic = capsys.readouterr().err

    assert rc == 2 and fired == [replacement]
    assert subject.read_text() == original
    assert (journal / record.name).read_bytes() == replacement_bytes
    if replacement == "directory":
        assert (detached_journal / record.name).read_bytes() == valid
    else:
        assert original_record.read_bytes() == valid
    assert "identity changed" in diagnostic and "unlink" in diagnostic


@pytest.mark.parametrize("operation", ("ordinary", "recover"))
def test_bd_mutate_refuses_linked_worktree_sharing_runner_git_authority(
        tmp_path, operation):
    repository = tmp_path / "runner-repository"
    runner = _bd_mutate_scratch_runner(repository)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(repository), "-c", "user.name=Test",
        "-c", "user.email=test@example.invalid", "commit", "-qm", "base",
    ], check=True)
    linked = tmp_path / "linked-worktree"
    subprocess.run([
        "git", "-C", str(repository), "worktree", "add", "-qb", "linked",
        str(linked),
    ], check=True)
    marker = tmp_path / "baseline-ran.marker"
    spec = _bd_mutate_scratch_subject(linked, "m.py", marker)
    (linked / "m.py").write_text("VALUE = 1\n", encoding="utf-8")

    def manifest():
        return {
            str(path.relative_to(repository)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in repository.rglob("*") if path.is_file()
        }

    before = manifest()
    argv = [sys.executable, str(runner)]
    if operation == "recover":
        argv += ["--recover", "--work", str(linked)]
    else:
        argv += ["--spec", str(spec), "--work", str(linked), "--json"]
    result = subprocess.run(
        argv, cwd=linked, capture_output=True, text=True, timeout=120)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "shares Git authority" in result.stderr
    assert manifest() == before
    assert not marker.exists()
    assert not list((repository / ".git" / "worktrees").rglob(
        "bd-mutate-inflight"))
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("operation", ("ordinary", "recover"))
def test_bd_mutate_refuses_an_external_hard_link_runner_before_effects(
        tmp_path, operation):
    repository = tmp_path / "runner-repository"
    runner = _bd_mutate_scratch_runner(repository)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    victim = repository / "victim.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    victim.write_text(original if operation == "ordinary" else mutant,
                      encoding="utf-8")
    marker = tmp_path / "baseline-ran.marker"
    spec = _bd_mutate_scratch_subject(repository, "victim.py", marker)
    if operation == "recover":
        journal = repository / ".bd-mutate-inflight"
        journal.mkdir()
        (journal / "123.json").write_text(json.dumps({
            "path": victim.name, "label": "hard-link runner recovery",
            "pid": 123,
            "original_sha": hashlib.sha256(original.encode()).hexdigest(),
            "mutated_sha": hashlib.sha256(mutant.encode()).hexdigest(),
            "original": original,
        }), encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    external_dir = tmp_path / "external" / "a" / "b"
    external_dir.mkdir(parents=True)
    external_runner = external_dir / "bd-mutate"
    os.link(runner, external_runner)
    assert runner.stat().st_nlink == external_runner.stat().st_nlink == 2

    def manifest():
        return {
            str(path.relative_to(repository)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in repository.rglob("*") if path.is_file()
        }

    before = manifest()
    argv = [sys.executable, str(external_runner)]
    if operation == "recover":
        argv += ["--recover"]
    else:
        argv += ["--spec", str(spec)]
    argv += ["--work", str(repository), "--json"]
    result = subprocess.run(
        argv, cwd=tmp_path, capture_output=True, text=True, timeout=120)

    assert result.returncode == 2, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["exit"] == 2 and report["rows"] == []
    assert "invoked runner has multiple hard links" in result.stderr
    assert manifest() == before and not marker.exists()
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("operation", ("ordinary", "recover"))
def test_bd_mutate_rechecks_runner_identity_at_each_effect_boundary(
        tmp_path, operation):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    target = work / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    mutate._bind_invoked_runner_identity()
    before = target.read_bytes()
    alias = tmp_path / "late-runner-alias"
    os.link(runner, alias)

    with pytest.raises(ValueError, match="multiple hard links"):
        if operation == "recover":
            mutate._recovery_target(work, target.name)
        else:
            mutate._mutation_subject(work, target.name)

    assert target.read_bytes() == before
    assert runner.stat().st_nlink == alias.stat().st_nlink == 2


@pytest.mark.parametrize("effect", (
    "subject-write", "baseline-cache-purge", "recovery-cache-purge",
))
def test_bd_mutate_runner_identity_drift_blocks_the_next_effect(
        tmp_path, effect):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    target = work / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    cache = work / "__pycache__"
    cache.mkdir()
    cached = cache / "target.pyc"
    cached.write_bytes(b"CACHE\n")
    mutate._bind_invoked_runner_identity()
    subject = (mutate._PinnedSubject(target)
               if effect == "subject-write" else None)
    alias = tmp_path / "late-effect-runner-alias"
    os.link(runner, alias)

    try:
        with pytest.raises(ValueError, match="multiple hard links"):
            if effect == "subject-write":
                mutate._write_subject_text(subject, "VALUE = 2\n")
            elif effect == "baseline-cache-purge":
                mutate._purge_pycache(work)
            else:
                mutate._purge_subject_pycache(target)
    finally:
        if subject is not None:
            subject.close()

    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert cached.read_bytes() == b"CACHE\n"


def test_bd_mutate_recovery_retains_journal_on_cache_deletion_failure(
        tmp_path, monkeypatch, capsys):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    target = work / "target.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    target.write_text(mutant, encoding="utf-8")
    cache = work / "__pycache__"
    cache.mkdir()
    (cache / "target.pyc").write_bytes(b"STALE MUTANT BYTECODE\n")
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    record = journal / "123.json"
    record.write_text(json.dumps({
        "path": target.name,
        "label": "cache removal failure",
        "pid": 123,
        "original_sha": hashlib.sha256(original.encode()).hexdigest(),
        "mutated_sha": hashlib.sha256(mutant.encode()).hexdigest(),
        "original": original,
    }), encoding="utf-8")
    calls = []

    def fail_cache_removal(path, *args, **kwargs):
        calls.append(Path(path))
        raise PermissionError("injected cache removal EPERM")

    monkeypatch.setattr(mutate.os, "rmdir", fail_cache_removal)
    rc = mutate.journal_recover(work)
    out, err = capsys.readouterr()

    assert rc == 2 and len(calls) == 1
    assert calls[0].name.startswith(".bd-mutate-owned-cache-")
    assert target.read_text() == original
    assert cache.exists() and record.exists()
    assert "recovered target.py" not in out
    assert "cache" in err.lower() and "retained" in err.lower()


def test_bd_mutate_recovery_cache_parent_swap_preserves_replacement_and_journal(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    subject_parent = work / "pkg"
    subject_parent.mkdir(parents=True)
    held = work / "pkg.held"
    subject = subject_parent / "target.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    subject.write_text(mutant, encoding="utf-8")
    cache = subject_parent / "__pycache__"
    cache.mkdir()
    (cache / "target.pyc").write_bytes(b"OWNED CACHE\n")
    external = tmp_path / "external-pkg"
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"EXTERNAL SENTINEL\n")
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    record = _bd_mutate_recovery_record(
        journal, "123.json", subject, original, mutant)
    record_document = json.loads(record.read_text(encoding="utf-8"))
    record_document["path"] = "pkg/target.py"
    record.write_text(json.dumps(record_document), encoding="utf-8")
    real_remove = mutate._remove_tree_at
    fired = []

    def swap_parent_then_remove(parent_fd, name, display, *args, **kwargs):
        if not fired:
            subject_parent.rename(held)
            subject_parent.symlink_to(external, target_is_directory=True)
            fired.append(True)
        return real_remove(parent_fd, name, display, *args, **kwargs)

    monkeypatch.setattr(mutate, "_remove_tree_at", swap_parent_then_remove)
    rc = mutate.journal_recover(work)

    assert rc == 2 and record.exists() and fired == [True]
    assert sentinel.read_bytes() == b"EXTERNAL SENTINEL\n"
    assert (held / "target.py").read_text() == original
    assert (held / "__pycache__" / "target.pyc").read_bytes() == b"OWNED CACHE\n"


@pytest.mark.parametrize("failure", (
    PermissionError("injected recovery write EPERM"),
    RuntimeError("injected runner identity drift"),
), ids=("permission-error", "runner-identity-error"))
def test_bd_mutate_recovery_write_failure_is_structured_and_does_not_skip_later_records(
        tmp_path, monkeypatch, capsys, failure):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    first, second = work / "first.py", work / "second.py"
    first.write_text(mutant, encoding="utf-8")
    second.write_text(mutant, encoding="utf-8")
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    first_record = _bd_mutate_recovery_record(
        journal, "101.json", first, original, mutant)
    second_record = _bd_mutate_recovery_record(
        journal, "202.json", second, original, mutant)
    first_record_before = first_record.read_bytes()
    real_write = mutate._write_subject_text
    attempted = []
    mutate._bind_invoked_runner_identity()

    def fail_first_recovery_write(path, text, *args, **kwargs):
        attempted.append(Path(path).name)
        if Path(path) == first:
            raise failure
        return real_write(path, text, *args, **kwargs)

    monkeypatch.setattr(mutate, "_write_subject_text", fail_first_recovery_write)
    rc = mutate.journal_recover(work)
    stdout, stderr = capsys.readouterr()

    assert rc == 2 and attempted == ["first.py", "second.py"]
    assert first.read_text(encoding="utf-8") == mutant
    assert first_record.read_bytes() == first_record_before
    assert second.read_text(encoding="utf-8") == original
    assert not second_record.exists()
    assert "recovered second.py" in stdout and "recovered first.py" not in stdout
    assert "REFUSING first.py: recovery write is unproved" in stderr
    assert type(failure).__name__ in stderr and str(failure) in stderr
    assert "observed recorded mutant" in stderr
    assert "Traceback" not in stderr


def test_bd_mutate_baseline_spawn_failure_emits_exit2_json(
        tmp_path, monkeypatch, capsys):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    subject.write_text("VALUE = 1\n", encoding="utf-8")
    spec = _bd_mutate_scratch_subject(work)
    before = subject.read_bytes()

    def fail_baseline_spawn(*_args, **_kwargs):
        raise PermissionError("injected baseline spawn EPERM")

    monkeypatch.setattr(mutate, "_run_band", fail_baseline_spawn)
    rc, report, raw_stdout, stderr = _bd_mutate_main_json(
        mutate, monkeypatch, capsys, runner, spec, work)

    assert rc == report["exit"] == 2
    assert report["selected"] == report["total"] == 1
    assert report["rows"] == []
    assert json.loads(raw_stdout) == report
    assert subject.read_bytes() == before
    assert "baseline execution is unproved" in stderr
    assert "PermissionError" in stderr and "injected baseline spawn EPERM" in stderr
    assert len(stderr) < 2000 and "Traceback" not in stderr


@pytest.mark.parametrize("failure", (
    PermissionError("injected validator EPERM"),
    KeyboardInterrupt("injected validator cancellation"),
), ids=("ordinary", "cancellation"))
def test_bd_mutate_validation_failure_settles_subject_before_leaving_mutant(
        tmp_path, monkeypatch, failure):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    subject.write_text("VALUE = 1\n", encoding="utf-8")
    spec_path = _bd_mutate_scratch_subject(work)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    before = subject.read_bytes()
    before_info = subject.stat()
    before_identity = (
        before_info.st_dev, before_info.st_ino,
        stat.S_IFMT(before_info.st_mode), before_info.st_nlink,
    )
    original_init = mutate._PinnedSubject.__init__
    original_close = os.close
    original_fstat = os.fstat
    acquired = []
    close_attempts = []
    validation_owner = []
    validation_close_start = []
    validation_calls = []
    band_calls = []

    def capture_init(owner, *args, **kwargs):
        original_init(owner, *args, **kwargs)
        acquired.append([
            _descriptor_identity(original_fstat, fd)
            for fd in owner._owned.values()
        ])

    def recording_close(fd):
        close_attempts.append(_descriptor_identity(original_fstat, fd))
        return original_close(fd)

    def fail_validation(path, text):
        assert Path(path) == subject and text == "VALUE = 2\n"
        assert acquired, "validator ran before a subject owner was acquired"
        validation_owner[:] = acquired[-1]
        validation_close_start.append(len(close_attempts))
        validation_calls.append((Path(path), text))
        for identity in validation_owner:
            assert _descriptor_identity(original_fstat, identity[0]) == identity
        raise failure

    def baseline_only(*_args, **_kwargs):
        band_calls.append(subject.read_text(encoding="utf-8"))
        return _bd_mutate_green_band()

    monkeypatch.setattr(mutate._PinnedSubject, "__init__", capture_init)
    monkeypatch.setattr(mutate.os, "close", recording_close)
    monkeypatch.setattr(mutate, "_validate", fail_validation)
    monkeypatch.setattr(mutate, "_run_band", baseline_only)
    try:
        if isinstance(failure, Exception):
            rc, rows = mutate.run_battery(
                spec["mutants"], spec["band"], work, verbose=False)
            assert rc == 2 and len(rows) == 1
            assert rows[0]["verdict"] in {"UNKNOWN", "ERROR"}
            assert "validation" in rows[0]["why"]
        else:
            with pytest.raises(BaseException) as caught:
                mutate.run_battery(
                    spec["mutants"], spec["band"], work, verbose=False)
            assert caught.value is failure

        assert len(validation_calls) == 1 and band_calls == ["VALUE = 1\n"]
        attempts = close_attempts[validation_close_start[0]:]
        for identity in validation_owner:
            assert attempts.count(identity) == 1
            _assert_owner_is_settled(original_fstat, identity)
        after_info = subject.stat()
        after_identity = (
            after_info.st_dev, after_info.st_ino,
            stat.S_IFMT(after_info.st_mode), after_info.st_nlink,
        )
        assert subject.read_bytes() == before and after_identity == before_identity
        assert not (work / ".bd-mutate-inflight").exists()
    finally:
        for identities in acquired:
            for identity in identities:
                _close_if_same_owner(original_fstat, original_close, identity)


def test_bd_mutate_mutant_band_exception_restores_and_reports_unknown(
        tmp_path, monkeypatch, capsys):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    subject.write_text("VALUE = 1\n", encoding="utf-8")
    spec = _bd_mutate_scratch_subject(work)
    before_bytes = subject.read_bytes()
    before_identity = (subject.stat().st_dev, subject.stat().st_ino,
                       stat.S_IFMT(subject.stat().st_mode), subject.stat().st_nlink)
    calls = []

    def fail_mutant_band(*_args, **_kwargs):
        calls.append(subject.read_text(encoding="utf-8"))
        if len(calls) == 1:
            return _bd_mutate_green_band()
        raise OSError("injected mutant band failure")

    monkeypatch.setattr(mutate, "_run_band", fail_mutant_band)
    rc, report, _raw_stdout, stderr = _bd_mutate_main_json(
        mutate, monkeypatch, capsys, runner, spec, work)

    assert rc == report["exit"] == 2 and len(report["rows"]) == 1
    row = report["rows"][0]
    assert row["label"] == "change value" and row["file"] == "m.py"
    assert row["verdict"] in {"UNKNOWN", "ERROR"}
    assert "mutant band execution is unproved" in row["why"]
    assert calls == ["VALUE = 1\n", "VALUE = 2\n"]
    after_identity = (subject.stat().st_dev, subject.stat().st_ino,
                      stat.S_IFMT(subject.stat().st_mode), subject.stat().st_nlink)
    assert subject.read_bytes() == before_bytes and after_identity == before_identity
    assert not list((work / ".bd-mutate-inflight").glob("*.json"))
    assert "OSError" in stderr and "injected mutant band failure" in stderr
    assert "Traceback" not in stderr


def test_bd_mutate_mutant_write_exception_runs_no_mutant_and_emits_json(
        tmp_path, monkeypatch, capsys):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    subject.write_text(original, encoding="utf-8")
    spec = _bd_mutate_scratch_subject(work)
    before_bytes = subject.read_bytes()
    before_identity = (subject.stat().st_dev, subject.stat().st_ino,
                       stat.S_IFMT(subject.stat().st_mode), subject.stat().st_nlink)
    real_write = mutate._write_subject_text
    band_calls = []
    write_calls = []

    def baseline_only(*_args, **_kwargs):
        band_calls.append(subject.read_text(encoding="utf-8"))
        return _bd_mutate_green_band()

    def fail_mutant_write(path, text, *args, **kwargs):
        write_calls.append(text)
        if text == mutant:
            raise PermissionError("injected mutant write EPERM")
        return real_write(path, text, *args, **kwargs)

    monkeypatch.setattr(mutate, "_run_band", baseline_only)
    monkeypatch.setattr(mutate, "_write_subject_text", fail_mutant_write)
    rc, report, _raw_stdout, stderr = _bd_mutate_main_json(
        mutate, monkeypatch, capsys, runner, spec, work)

    assert rc == report["exit"] == 2 and len(report["rows"]) == 1
    row = report["rows"][0]
    assert row["label"] == "change value" and row["file"] == "m.py"
    assert row["verdict"] in {"UNKNOWN", "ERROR"}
    assert "mutant write is unproved" in row["why"]
    assert band_calls == [original], "a mutant band ran after its write failed"
    assert write_calls == [mutant]
    after_identity = (subject.stat().st_dev, subject.stat().st_ino,
                      stat.S_IFMT(subject.stat().st_mode), subject.stat().st_nlink)
    assert subject.read_bytes() == before_bytes and after_identity == before_identity
    assert not list((work / ".bd-mutate-inflight").glob("*.json"))
    assert "PermissionError" in stderr and "injected mutant write EPERM" in stderr
    assert "Traceback" not in stderr


def test_bd_mutate_subject_close_uncertainty_retains_the_recovery_journal(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    subject.write_text(original, encoding="utf-8")
    subject_inode = subject.stat().st_ino
    spec_path = _bd_mutate_scratch_subject(work)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    real_write = mutate._write_subject_text
    real_close = mutate.os.close
    armed = []
    close_faults = []

    def arm_after_mutant_write(path, text, *args, **kwargs):
        result = real_write(path, text, *args, **kwargs)
        if text == mutant:
            armed.append(True)
        return result

    def close_with_one_post_effect_fault(fd):
        try:
            inode = mutate.os.fstat(fd).st_ino
        except OSError:
            inode = None
        if armed and not close_faults and inode == subject_inode:
            real_close(fd)
            close_faults.append(fd)
            raise OSError("injected post-close subject uncertainty")
        return real_close(fd)

    monkeypatch.setattr(mutate, "_run_band",
                        lambda *_args, **_kwargs: _bd_mutate_green_band())
    monkeypatch.setattr(mutate, "_write_subject_text", arm_after_mutant_write)
    monkeypatch.setattr(mutate.os, "close", close_with_one_post_effect_fault)
    rc, rows = mutate.run_battery(spec["mutants"], spec["band"], work,
                                  verbose=False)

    assert rc == 2 and rows[0]["verdict"] == "ERROR"
    assert "SUBJECT OWNER CLOSE UNKNOWN" in rows[0]["why"]
    assert close_faults and subject.read_text() == original
    assert list((work / ".bd-mutate-inflight").glob("*.json")), (
        "close uncertainty discarded the only recovery authority")


def test_bd_mutate_recovery_subject_close_uncertainty_retains_the_journal(
        tmp_path, monkeypatch, capsys):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    subject.write_text(mutant, encoding="utf-8")
    subject_inode = subject.stat().st_ino
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    record = _bd_mutate_recovery_record(
        journal, "123.json", subject, original, mutant)
    real_write = mutate._write_subject_text
    real_close = mutate.os.close
    armed = []
    close_faults = []

    def arm_after_recovery(path, text, *args, **kwargs):
        result = real_write(path, text, *args, **kwargs)
        if text == original:
            armed.append(True)
        return result

    def close_with_one_post_effect_fault(fd):
        try:
            inode = mutate.os.fstat(fd).st_ino
        except OSError:
            inode = None
        if armed and not close_faults and inode == subject_inode:
            real_close(fd)
            close_faults.append(fd)
            raise OSError("injected post-close recovery uncertainty")
        return real_close(fd)

    monkeypatch.setattr(mutate, "_write_subject_text", arm_after_recovery)
    monkeypatch.setattr(mutate.os, "close", close_with_one_post_effect_fault)
    rc = mutate.journal_recover(work)
    stdout, stderr = capsys.readouterr()

    assert rc == 2 and close_faults and record.exists()
    assert subject.read_text() == original
    assert "recovered m.py" not in stdout
    assert "subject owner close is unproved" in stderr


def test_bd_mutate_subject_name_change_cannot_redirect_the_mutant_write(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    held = work / "m.py.held"
    external = tmp_path / "external.py"
    subject.write_text("VALUE = 1\n", encoding="utf-8")
    external.write_text("EXTERNAL SENTINEL\n", encoding="utf-8")
    spec_path = _bd_mutate_scratch_subject(work)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    real_write = mutate._write_subject_text
    changed = []
    band_calls = []

    def change_name_before_write(path, text, *args, **kwargs):
        if not changed:
            Path(path).rename(held)
            Path(path).symlink_to(external)
            changed.append(text)
        return real_write(path, text, *args, **kwargs)

    def green_band(*_args, **_kwargs):
        band_calls.append(subject.read_text(encoding="utf-8"))
        return _bd_mutate_green_band()

    monkeypatch.setattr(mutate, "_run_band", green_band)
    monkeypatch.setattr(mutate, "_write_subject_text", change_name_before_write)
    rc, rows = mutate.run_battery(spec["mutants"], spec["band"], work,
                                  verbose=False)

    assert rc == 2 and rows[0]["verdict"] in {"UNKNOWN", "ERROR"}
    assert external.read_text(encoding="utf-8") == "EXTERNAL SENTINEL\n"
    assert subject.is_symlink() and held.read_text() == "VALUE = 1\n"
    assert band_calls == ["VALUE = 1\n"], "mutant band ran after detachment"
    assert list((work / ".bd-mutate-inflight").glob("*.json")), (
        "unproved restoration discarded its recovery authority")


def test_bd_mutate_same_byte_replacement_cannot_receive_the_mutant_write(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    held = work / "m.py.held"
    replacement = work / "replacement.py"
    subject.write_text("VALUE = 1\n", encoding="utf-8")
    replacement.write_text("VALUE = 1\n", encoding="utf-8")
    spec_path = _bd_mutate_scratch_subject(work)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    real_write = mutate._write_subject_text
    band_calls = []
    changed = []

    def replace_name_before_write(path, text, *args, **kwargs):
        if not changed:
            Path(path).rename(held)
            replacement.rename(Path(path))
            changed.append(text)
        return real_write(path, text, *args, **kwargs)

    def green_band(*_args, **_kwargs):
        band_calls.append(subject.read_text(encoding="utf-8"))
        return _bd_mutate_green_band()

    monkeypatch.setattr(mutate, "_run_band", green_band)
    monkeypatch.setattr(mutate, "_write_subject_text", replace_name_before_write)
    rc, rows = mutate.run_battery(spec["mutants"], spec["band"], work,
                                  verbose=False)

    assert rc == 2 and rows[0]["verdict"] in {"UNKNOWN", "ERROR"}
    assert subject.read_text() == "VALUE = 1\n"
    assert held.read_text() == "VALUE = 1\n"
    assert band_calls == ["VALUE = 1\n"], "mutant band ran on a replacement"
    assert list((work / ".bd-mutate-inflight").glob("*.json"))


@pytest.mark.parametrize("event", ("leaf-detach", "parent-detach", "late-hard-link"))
def test_bd_mutate_post_write_subject_change_blocks_band_and_unsafe_restore(
        tmp_path, monkeypatch, event):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    parent = work / "pkg"
    parent.mkdir(parents=True)
    subject = parent / "m.py"
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    subject.write_text(original, encoding="utf-8")
    spec_path = _bd_mutate_scratch_subject(work, "pkg/m.py")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    real_replace = mutate._PinnedSubject._replace_bytes
    band_calls = []
    fired = []
    displaced = tmp_path / "displaced"
    alias = tmp_path / "late-link.py"

    def replace_then_change(owner, payload):
        result = real_replace(owner, payload)
        if payload == mutant.encode() and not fired:
            if event == "leaf-detach":
                owner.path.rename(displaced)
                owner.path.write_text(original, encoding="utf-8")
            elif event == "parent-detach":
                owner.path.parent.rename(displaced)
                owner.path.parent.mkdir()
                owner.path.write_text(original, encoding="utf-8")
            else:
                os.link(owner.path, alias)
            fired.append(event)
        return result

    def green_band(*_args, **_kwargs):
        band_calls.append(subject.read_text(encoding="utf-8"))
        return _bd_mutate_green_band()

    monkeypatch.setattr(mutate._PinnedSubject, "_replace_bytes",
                        replace_then_change)
    monkeypatch.setattr(mutate, "_run_band", green_band)
    rc, rows = mutate.run_battery(spec["mutants"], spec["band"], work,
                                  verbose=False)

    assert fired == [event] and rc == 2
    assert rows[0]["verdict"] in {"UNKNOWN", "ERROR"}
    assert band_calls == [original], "mutant band ran after ownership changed"
    assert list((work / ".bd-mutate-inflight").glob("*.json"))
    if event == "leaf-detach":
        assert subject.read_text() == original and displaced.read_text() == mutant
    elif event == "parent-detach":
        assert subject.read_text() == original
        assert (displaced / "m.py").read_text() == mutant
    else:
        assert subject.read_text() == alias.read_text() == mutant


def test_bd_mutate_recovery_name_change_cannot_redirect_the_restore(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    subject = work / "target.py"
    held = work / "target.py.held"
    external = tmp_path / "external.py"
    subject.write_text(mutant, encoding="utf-8")
    external.write_text("EXTERNAL SENTINEL\n", encoding="utf-8")
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    record = _bd_mutate_recovery_record(
        journal, "123.json", subject, original, mutant)
    real_write = mutate._write_subject_text
    changed = []

    def change_name_before_write(path, text, *args, **kwargs):
        if not changed:
            Path(path).rename(held)
            Path(path).symlink_to(external)
            changed.append(text)
        return real_write(path, text, *args, **kwargs)

    monkeypatch.setattr(mutate, "_write_subject_text", change_name_before_write)
    rc = mutate.journal_recover(work)

    assert rc == 2 and record.exists()
    assert external.read_text(encoding="utf-8") == "EXTERNAL SENTINEL\n"
    assert subject.is_symlink() and held.read_text() == mutant


def test_bd_mutate_recovery_same_byte_replacement_is_not_overwritten(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    work.mkdir()
    original, mutant = "VALUE = 1\n", "VALUE = 2\n"
    subject = work / "target.py"
    held = work / "target.py.held"
    replacement = work / "replacement.py"
    subject.write_text(mutant, encoding="utf-8")
    replacement.write_text(mutant, encoding="utf-8")
    journal = work / ".bd-mutate-inflight"
    journal.mkdir()
    record = _bd_mutate_recovery_record(
        journal, "123.json", subject, original, mutant)
    real_write = mutate._write_subject_text
    changed = []

    def replace_name_before_write(path, text, *args, **kwargs):
        if not changed:
            Path(path).rename(held)
            replacement.rename(Path(path))
            changed.append(text)
        return real_write(path, text, *args, **kwargs)

    monkeypatch.setattr(mutate, "_write_subject_text", replace_name_before_write)
    rc = mutate.journal_recover(work)

    assert rc == 2 and record.exists()
    assert subject.read_text() == mutant
    assert held.read_text() == mutant


def test_bd_mutate_emit_stage_replacement_never_publishes_or_deletes_foreign_bytes(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    name = "v3_66_9999_stage_replace.json"
    work, prepared = _bd_mutate_emit_fixture(tmp_path, mutate, name)
    parent = work / "tests" / "mutants"
    final = parent / name
    displaced = tmp_path / "displaced-emitted-stage"
    real_link = mutate.os.link
    fired = []
    foreign_stage = []

    def replace_stage_then_link(*args, **kwargs):
        stages = [path for path in parent.iterdir()
                  if path.name.startswith(f".{name}.") and path.name.endswith(".tmp")]
        assert len(stages) == 1
        stage = stages[0]
        stage.rename(displaced)
        stage.write_bytes(b"FOREIGN STAGE\n")
        foreign_stage.append(stage)
        fired.append(args[0])
        return real_link(*args, **kwargs)

    monkeypatch.setattr(mutate.os, "link", replace_stage_then_link)
    with pytest.raises((OSError, RuntimeError, ValueError)):
        mutate._publish_emitted_spec(*prepared)

    assert len(fired) == 1 and len(foreign_stage) == 1
    assert foreign_stage[0].read_bytes() == b"FOREIGN STAGE\n"
    assert displaced.read_bytes() == prepared[2]
    assert not final.exists()


def test_bd_mutate_emit_stage_file_fsync_failure_leaves_no_artifact(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work, prepared = _bd_mutate_emit_fixture(
        tmp_path, mutate, "v3_66_9999_stage_fsync.json")
    parent = work / "tests" / "mutants"
    real_open = mutate.os.open
    real_fsync = mutate.os.fsync
    stage_fd = []
    primary = OSError("emitted stage fsync uncertainty")

    def capture_stage_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        raw = os.fspath(path)
        if raw.startswith(".v3_66_9999_stage_fsync.json.") and raw.endswith(".tmp"):
            stage_fd.append(fd)
        return fd

    def fail_stage_fsync(fd):
        if stage_fd and fd == stage_fd[0]:
            raise primary
        return real_fsync(fd)

    monkeypatch.setattr(mutate.os, "open", capture_stage_open)
    monkeypatch.setattr(mutate.os, "fsync", fail_stage_fsync)
    with pytest.raises(BaseException) as caught:
        mutate._publish_emitted_spec(*prepared)

    assert caught.value is primary and len(stage_fd) == 1
    assert list(parent.iterdir()) == []


def test_bd_mutate_emit_namespace_fsync_failure_rolls_back_every_artifact(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    name = "v3_66_9999_namespace_fsync.json"
    work, prepared = _bd_mutate_emit_fixture(tmp_path, mutate, name)
    owner, _rel, _payload = prepared
    parent = work / "tests" / "mutants"
    final = parent / name
    real_fsync = mutate.os.fsync
    primary = OSError("emitted namespace fsync uncertainty")
    fired = []

    def fail_post_link_directory_fsync(fd):
        if fd == owner.fd and final.exists() and not fired:
            fired.append(fd)
            raise primary
        return real_fsync(fd)

    monkeypatch.setattr(mutate.os, "fsync", fail_post_link_directory_fsync)
    with pytest.raises(BaseException) as caught:
        mutate._publish_emitted_spec(*prepared)

    assert caught.value is primary and fired
    assert list(parent.iterdir()) == []


def test_bd_mutate_emit_close_uncertainty_preserves_first_error_and_durable_final(
        tmp_path, monkeypatch, capsys):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    name = "v3_66_9999_close_uncertain.json"
    work, prepared = _bd_mutate_emit_fixture(tmp_path, mutate, name)
    owner, _rel, payload = prepared
    parent = work / "tests" / "mutants"
    final = parent / name
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    owner_identities = [
        _descriptor_identity(original_fstat, fd) for fd in owner._owned.values()
    ]
    emitted = {}
    attempts = []
    replacement = []
    primary = KeyboardInterrupt("emitted final close uncertainty")
    secondary = SystemExit(93)
    sentinel = tmp_path / "emitted-close-reuse-sentinel"

    def capture_emitted_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        raw = os.fspath(path)
        if raw == name:
            emitted["final"] = _descriptor_identity(original_fstat, fd)
        elif raw.startswith(f".{name}.") and raw.endswith(".tmp"):
            emitted["stage"] = _descriptor_identity(original_fstat, fd)
        return fd

    def close_then_fault(fd):
        identity = _descriptor_identity(original_fstat, fd)
        attempts.append(identity)
        if identity == emitted.get("final") and not replacement:
            original_close(fd)
            replacement_fd = original_open(
                sentinel, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            assert replacement_fd == fd
            replacement.append(
                _descriptor_identity(original_fstat, replacement_fd))
            raise primary
        if owner_identities and identity == owner_identities[0]:
            original_close(fd)
            raise secondary
        return original_close(fd)

    monkeypatch.setattr(mutate.os, "open", capture_emitted_open)
    monkeypatch.setattr(mutate.os, "close", close_then_fault)
    try:
        with pytest.raises(BaseException) as caught:
            mutate._publish_emitted_spec(*prepared)
        stdout, _stderr = capsys.readouterr()

        assert caught.value is primary and set(emitted) == {"stage", "final"}
        for identity in [*emitted.values(), *owner_identities]:
            assert attempts.count(identity) == 1
        assert len(replacement) == 1
        assert (_descriptor_identity(original_fstat, replacement[0][0]) ==
                replacement[0])
        assert any("SystemExit" in note and "93" in note
                   for note in getattr(primary, "__notes__", ()))
        assert final.read_bytes() == payload
        assert not list(parent.glob(f".{name}.*.tmp"))
        assert "emitted canonical spec" not in stdout
    finally:
        for identity in replacement + owner_identities + list(emitted.values()):
            _close_if_same_owner(original_fstat, original_close, identity)


def test_bd_mutate_emit_parent_identity_change_refuses_publication(tmp_path):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    (work / "tests").mkdir(parents=True)
    (work / "m.py").write_text("VALUE = 1\n", encoding="utf-8")
    (work / "tests" / "test_m.py").write_text(
        "def test_value():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "add", "m.py", "tests/test_m.py"],
                   check=True)
    prepared = mutate._prepare_emitted_spec(
        work, "v3_66_9999_emit_identity.json", "emit identity",
        ["tests/test_m.py::test_value"], [{
            "label": "value", "file": "m.py", "old": "VALUE = 1",
            "new": "VALUE = 2", "direction": "regression",
            "catcher": "tests/test_m.py::test_value",
        }])
    parent = work / "tests" / "mutants"
    held = work / "tests" / "mutants.held"
    external = tmp_path / "external-mutants"
    parent.rename(held)
    external.mkdir()
    parent.symlink_to(external, target_is_directory=True)

    with pytest.raises((OSError, RuntimeError, ValueError)):
        mutate._publish_emitted_spec(*prepared)

    assert list(external.iterdir()) == []
    assert list(held.iterdir()) == []


def test_bd_mutate_emit_parent_swap_during_publication_leaves_no_artifact(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    (work / "tests").mkdir(parents=True)
    (work / "m.py").write_text("VALUE = 1\n", encoding="utf-8")
    (work / "tests" / "test_m.py").write_text(
        "def test_value():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "add", "m.py", "tests/test_m.py"],
                   check=True)
    prepared = mutate._prepare_emitted_spec(
        work, "v3_66_9999_emit_race.json", "emit race",
        ["tests/test_m.py::test_value"], [{
            "label": "value", "file": "m.py", "old": "VALUE = 1",
            "new": "VALUE = 2", "direction": "regression",
            "catcher": "tests/test_m.py::test_value",
        }])
    parent = work / "tests" / "mutants"
    held = work / "tests" / "mutants.held"
    external = tmp_path / "external-mutants"
    external.mkdir()
    real_link = mutate.os.link
    fired = []

    def swap_then_link(*args, **kwargs):
        if not fired:
            parent.rename(held)
            parent.symlink_to(external, target_is_directory=True)
            fired.append(True)
        return real_link(*args, **kwargs)

    monkeypatch.setattr(mutate.os, "link", swap_then_link)
    with pytest.raises((OSError, RuntimeError, ValueError)):
        mutate._publish_emitted_spec(*prepared)

    assert fired == [True]
    assert list(external.iterdir()) == []
    assert list(held.iterdir()) == []


@pytest.mark.parametrize("failure_type", (OSError, FileExistsError),
                         ids=("oserror", "file-exists-error"))
def test_bd_mutate_emit_link_after_effect_failure_is_reconciled(
        tmp_path, monkeypatch, failure_type):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    (work / "tests").mkdir(parents=True)
    (work / "m.py").write_text("VALUE = 1\n", encoding="utf-8")
    (work / "tests" / "test_m.py").write_text(
        "def test_value():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "add", "m.py", "tests/test_m.py"],
                   check=True)
    prepared = mutate._prepare_emitted_spec(
        work, "v3_66_9999_link_effect.json", "link effect",
        ["tests/test_m.py::test_value"], [{
            "label": "value", "file": "m.py", "old": "VALUE = 1",
            "new": "VALUE = 2", "direction": "regression",
            "catcher": "tests/test_m.py::test_value",
        }])
    parent = work / "tests" / "mutants"
    real_link = mutate.os.link

    def link_then_raise(*args, **kwargs):
        real_link(*args, **kwargs)
        raise failure_type("injected link-after-effect uncertainty")

    monkeypatch.setattr(mutate.os, "link", link_then_raise)
    with pytest.raises(failure_type, match="link-after-effect"):
        mutate._publish_emitted_spec(*prepared)

    assert list(parent.iterdir()) == []


def test_bd_mutate_emit_foreign_final_survives_failed_link_reconciliation(
        tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    (work / "tests").mkdir(parents=True)
    (work / "m.py").write_text("VALUE = 1\n", encoding="utf-8")
    (work / "tests" / "test_m.py").write_text(
        "def test_value():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "add", "m.py", "tests/test_m.py"],
                   check=True)
    name = "v3_66_9999_foreign_final.json"
    prepared = mutate._prepare_emitted_spec(
        work, name, "foreign final",
        ["tests/test_m.py::test_value"], [{
            "label": "value", "file": "m.py", "old": "VALUE = 1",
            "new": "VALUE = 2", "direction": "regression",
            "catcher": "tests/test_m.py::test_value",
        }])
    final = work / "tests" / "mutants" / name
    real_link = mutate.os.link

    def replace_final_then_raise(*args, **kwargs):
        real_link(*args, **kwargs)
        final.unlink()
        final.write_bytes(b"FOREIGN FINAL\n")
        raise OSError("injected foreign final after link")

    monkeypatch.setattr(mutate.os, "link", replace_final_then_raise)
    with pytest.raises(OSError, match="foreign final"):
        mutate._publish_emitted_spec(*prepared)

    assert final.read_bytes() == b"FOREIGN FINAL\n"


def test_bd_mutate_emit_same_type_parent_swap_is_rejected(tmp_path, monkeypatch):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    mutate = _load_bd_mutate(runner)
    work = tmp_path / "candidate"
    parent = work / "tests" / "mutants"
    parent.mkdir(parents=True)
    (work / "m.py").write_text("VALUE = 1\n", encoding="utf-8")
    (work / "tests" / "test_m.py").write_text(
        "def test_value():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "add", "m.py", "tests/test_m.py"],
                   check=True)
    held = work / "tests" / "mutants.held"
    replacement = work / "tests" / "mutants.replacement"
    replacement.mkdir()
    sentinel = replacement / "sentinel"
    sentinel.write_bytes(b"REPLACEMENT\n")
    real_child = mutate._PinnedDirectory.child

    def swap_before_child(_cls, tests_owner, name, expected):
        parent.rename(held)
        replacement.rename(parent)
        return real_child(tests_owner, name, expected)

    monkeypatch.setattr(
        mutate._PinnedDirectory, "child", classmethod(swap_before_child))
    with pytest.raises((OSError, RuntimeError, ValueError)):
        mutate._prepare_emitted_spec(
            work, "v3_66_9999_same_type.json", "same type",
            ["tests/test_m.py::test_value"], [{
                "label": "value", "file": "m.py", "old": "VALUE = 1",
                "new": "VALUE = 2", "direction": "regression",
                "catcher": "tests/test_m.py::test_value",
            }])

    assert (parent / "sentinel").read_bytes() == b"REPLACEMENT\n"
    assert list(held.iterdir()) == []
    assert sorted(path.name for path in parent.iterdir()) == ["sentinel"]
    assert not list(parent.glob(".v3_66_9999_same_type.json.*.tmp"))
    assert not (parent / "v3_66_9999_same_type.json").exists()


def test_bd_mutate_post_baseline_identity_drift_is_unrunnable(tmp_path):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    subject.write_text("VALUE = 1\n", encoding="utf-8")
    outside = tmp_path / "outside-alias.py"
    baseline_marker = tmp_path / "baseline-ran.txt"
    mutant_marker = tmp_path / "mutant-ran.txt"
    spec = _bd_mutate_scratch_subject(work, "m.py")
    (work / "tests" / "test_m.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        f"baseline_marker = Path({str(baseline_marker)!r})\n"
        f"mutant_marker = Path({str(mutant_marker)!r})\n"
        "baseline_marker.write_text('baseline reached')\n"
        f"outside = Path({str(outside)!r})\n"
        f"subject = Path({str(subject)!r})\n"
        "if subject.read_text() == 'VALUE = 2\\n':\n"
        "    mutant_marker.write_text('mutant reached')\n"
        "if not outside.exists():\n"
        "    os.link(subject, outside)\n"
        "def test_value():\n"
        "    assert subject.read_text() == 'VALUE = 1\\n'\n",
        encoding="utf-8",
    )
    before = subject.read_bytes()

    result = subprocess.run(
        [sys.executable, str(runner), "--spec", str(spec),
         "--work", str(work), "--json"],
        cwd=work, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "mutant subject has multiple hard links" in result.stderr
    assert "Traceback" not in result.stderr
    assert baseline_marker.read_text(encoding="utf-8") == "baseline reached"
    assert not mutant_marker.exists()
    assert subject.read_bytes() == outside.read_bytes() == before
    report = json.loads(result.stdout)
    assert report["exit"] == 2
    assert len(report["rows"]) == 1
    row = report["rows"][0]
    assert row["label"] == "change value" and row["file"] == "m.py"
    assert row["verdict"] == "UNKNOWN"
    assert "subject identity changed after baseline" in row["why"]


def test_bd_mutate_baseline_byte_drift_is_unknown_and_preserved(tmp_path):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    subject.write_text("VALUE = 1\nOTHER = 1\n", encoding="utf-8")
    baseline_marker = tmp_path / "baseline-ran.txt"
    mutant_marker = tmp_path / "mutant-ran.txt"
    spec = _bd_mutate_scratch_subject(work, "m.py")
    (work / "tests" / "test_m.py").write_text(
        "from pathlib import Path\n"
        f"subject = Path({str(subject)!r})\n"
        f"baseline_marker = Path({str(baseline_marker)!r})\n"
        f"mutant_marker = Path({str(mutant_marker)!r})\n"
        "text = subject.read_text()\n"
        "if 'OTHER = 1\\n' in text:\n"
        "    subject.write_text(text.replace('OTHER = 1\\n', 'OTHER = 2\\n'))\n"
        "baseline_marker.write_text('baseline reached')\n"
        "if 'VALUE = 2\\n' in subject.read_text():\n"
        "    mutant_marker.write_text('mutant reached')\n"
        "def test_value():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(runner), "--spec", str(spec),
         "--work", str(work), "--json"],
        cwd=work, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert baseline_marker.read_text() == "baseline reached"
    assert not mutant_marker.exists()
    assert subject.read_text() == "VALUE = 1\nOTHER = 2\n"
    report = json.loads(result.stdout)
    assert report["exit"] == 2
    assert report["rows"][0]["verdict"] == "UNKNOWN"
    assert "subject identity changed after baseline" in report["rows"][0]["why"]
    assert "bytes" in report["rows"][0]["why"]
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("drift", ("inode", "mode"))
def test_bd_mutate_baseline_inode_or_mode_drift_is_unknown(
        tmp_path, drift):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    work.mkdir()
    subject = work / "m.py"
    subject.write_text("VALUE = 1\n", encoding="utf-8")
    subject.chmod(0o644)
    baseline_marker = tmp_path / "baseline-ran.txt"
    spec = _bd_mutate_scratch_subject(work, "m.py")
    mutation = (
        "replacement = subject.with_suffix('.replacement')\n"
        "replacement.write_bytes(subject.read_bytes())\n"
        "replacement.chmod(0o644)\n"
        "replacement.replace(subject)\n"
        if drift == "inode" else
        "subject.chmod(0o744)\n"
    )
    (work / "tests" / "test_m.py").write_text(
        "from pathlib import Path\n"
        f"subject = Path({str(subject)!r})\n"
        f"marker = Path({str(baseline_marker)!r})\n"
        "if not marker.exists():\n"
        + "".join("    " + line + "\n" for line in mutation.splitlines())
        + "    marker.write_text('baseline reached')\n"
        "def test_value():\n"
        "    assert subject.read_text() == 'VALUE = 1\\n'\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(runner), "--spec", str(spec),
         "--work", str(work), "--json"],
        cwd=work, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["rows"][0]["verdict"] == "UNKNOWN"
    assert drift in report["rows"][0]["why"]
    assert subject.read_text() == "VALUE = 1\n"


def _write_two_subject_mutation_spec(work, path):
    path.write_text(json.dumps({
        "schema": "bd-mutate-spec/1",
        "subject": "two-subject identity completeness",
        "band": ["tests/test_two.py"],
        "mutants": [
            {"label": "first value", "file": "first.py",
             "old": "VALUE = 1", "new": "VALUE = 2",
             "direction": "regression",
             "catcher": "tests/test_two.py::test_first"},
            {"label": "second value", "file": "second.py",
             "old": "VALUE = 1", "new": "VALUE = 2",
             "direction": "regression",
             "catcher": "tests/test_two.py::test_second"},
        ],
    }), encoding="utf-8")


def test_bd_mutate_checks_every_subject_immediately_after_baseline(tmp_path):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    (work / "tests").mkdir(parents=True)
    first, second = work / "first.py", work / "second.py"
    first.write_text("VALUE = 1\n", encoding="utf-8")
    second.write_text("VALUE = 1\nOTHER = 1\n", encoding="utf-8")
    mutant_marker = tmp_path / "mutant-ran"
    (work / "tests" / "test_two.py").write_text(
        "from pathlib import Path\n"
        f"first = Path({str(first)!r})\nsecond = Path({str(second)!r})\n"
        f"marker = Path({str(mutant_marker)!r})\n"
        "text = second.read_text()\n"
        "if 'OTHER = 1\\n' in text:\n"
        "    second.write_text(text.replace('OTHER = 1\\n', 'OTHER = 2\\n'))\n"
        "if 'VALUE = 2\\n' in first.read_text() or 'VALUE = 2\\n' in second.read_text():\n"
        "    marker.write_text('mutant reached')\n"
        "def test_first():\n    assert 'VALUE = 1\\n' in first.read_text()\n"
        "def test_second():\n    assert 'VALUE = 1\\n' in second.read_text()\n",
        encoding="utf-8")
    spec = tmp_path / "two-subjects.json"
    _write_two_subject_mutation_spec(work, spec)

    result = subprocess.run(
        [sys.executable, str(runner), "--spec", str(spec),
         "--work", str(work), "--json"], cwd=work,
        capture_output=True, text=True, timeout=120)

    report = json.loads(result.stdout)
    assert result.returncode == 2 and report["exit"] == 2
    assert report["rows"][0]["label"] == "second value"
    assert report["rows"][0]["verdict"] == "UNKNOWN"
    assert "bytes" in report["rows"][0]["why"]
    assert not mutant_marker.exists(), "first mutant ran before second drift check"


def test_bd_mutate_rechecks_later_subject_after_each_mutant(tmp_path):
    runner = _bd_mutate_scratch_runner(tmp_path / "runner-repository")
    work = tmp_path / "candidate"
    (work / "tests").mkdir(parents=True)
    first, second = work / "first.py", work / "second.py"
    first.write_text("VALUE = 1\n", encoding="utf-8")
    second.write_text("VALUE = 1\nOTHER = 1\n", encoding="utf-8")
    second_mutant_marker = tmp_path / "second-mutant-ran"
    (work / "tests" / "test_two.py").write_text(
        "from pathlib import Path\n"
        f"first = Path({str(first)!r})\nsecond = Path({str(second)!r})\n"
        f"marker = Path({str(second_mutant_marker)!r})\n"
        "if 'VALUE = 2\\n' in first.read_text():\n"
        "    text = second.read_text()\n"
        "    if 'OTHER = 1\\n' in text:\n"
        "        second.write_text(text.replace('OTHER = 1\\n', 'OTHER = 2\\n'))\n"
        "if 'VALUE = 2\\n' in second.read_text():\n"
        "    marker.write_text('second mutant reached')\n"
        "def test_first():\n    assert 'VALUE = 1\\n' in first.read_text()\n"
        "def test_second():\n    assert 'VALUE = 1\\n' in second.read_text()\n",
        encoding="utf-8")
    spec = tmp_path / "two-subjects.json"
    _write_two_subject_mutation_spec(work, spec)

    result = subprocess.run(
        [sys.executable, str(runner), "--spec", str(spec),
         "--work", str(work), "--json"], cwd=work,
        capture_output=True, text=True, timeout=120)

    report = json.loads(result.stdout)
    assert result.returncode == 2 and report["exit"] == 2
    assert [row["verdict"] for row in report["rows"]] == ["CAUGHT", "UNKNOWN"]
    assert "subject identity changed after baseline" in report["rows"][1]["why"]
    assert not second_mutant_marker.exists()


@pytest.mark.slow
@pytest.mark.timeout(_item_timeout_s("real-gate-row-end-to-end"))
def test_a_real_gate_row_runs_end_to_end_and_catches_its_mutation(tmp_path):
    """RED, and the only assertion that exercises the {py} substitution.

    Everything above is structural over the registry text. If the substitution
    in check() were reverted, every gate would be the literal string
    "{py} run_tests.py ..." -- unrunnable -- and no structural assertion would
    notice. This drives one real row all the way through copy -> mutate -> gate
    and requires the four-state engine to return CAUGHT.
    """
    # AGAINST A DETACHED COPY, NOT THE LIVE TREE, and this is a correctness fix
    # rather than tidiness. bd-mutation-test snapshots TRACKED SOURCE, mutates,
    # and compares; pointed at REPO it fails the moment any sibling test in a
    # parallel run touches a tracked file --
    #   _SnapshotError: tracked source changed between pristine and mutant trees
    # -- which is what this test did under -n 12 the moment v3.66.1222 stopped
    # the 240s bound killing it first. The timeout had been MASKING a race.
    #
    # bd-mutate already refuses this by construction: "refusing work that
    # intersects the repository containing this bd-mutate ... Use a detached
    # scratch copy." bd-mutation-test accepting --work REPO is the anomaly, and
    # a test that measures a shared resource from inside a parallel suite
    # measures the suite -- the same rule tests/test_v3_66_1046 states about
    # counting a global directory, and backlog row 231 states about the process
    # table.
    work = _detached_clone(tmp_path / "detached")

    r = _run_tool(
        [sys.executable, str(MT), "--only", "route_index/spa_wired",
         "--work", str(work), "--json"],
        budget_s=_budget_s("real-gate-row-end-to-end"),
        what="bd-mutation-test", site="real-gate-row-end-to-end", cwd=str(work))
    row = _one_mutation_result(r, expected_id="route_index/spa_wired")
    state = row.get("state")
    assert state == "CAUGHT", (
        f"the row reported {state!r}, not CAUGHT. A gate that cannot execute "
        f"reports BASELINE-RED and can never prove a mutation was caught.\n"
        f"{r.stdout[-3000:]}")


@pytest.mark.slow
def test_band_derive_selftest_actually_executes_its_controls():
    """RED. Pristine prints `SKIP  no work tree` and PASSES -- ~19 controls
    whose docstring says they "WOULD HAVE CAUGHT the constant-band bug this
    tool shipped with" never ran.

    The RED signal is CONTENT, not the exit code: pristine exits 0 either way,
    which is precisely why a SKIP that returns success is dangerous.

    INVARIANT: invoke the tool at its REAL path. bdtools_sec.DEFAULT_WORK is
    derived by walking up from bdtools_sec.py's own location, so running a copy
    from anywhere else resolves back to the sandbox default and reproduces the
    false green this test exists to catch.
    """
    env = {**os.environ, "PYTHONPATH": str(BIN)}
    r = _run_tool([sys.executable, str(BD), "--selftest"],
                  budget_s=_budget_s("band-derive-selftest"),
                  what="bd-band-derive", site="band-derive-selftest",
                  cwd=REPO, env=env)
    out = r.stdout + r.stderr
    assert "SKIP  no work tree" not in out, (
        "bd-band-derive still skipped its own controls and reported success:\n"
        + out[-2000:])
    assert "SIGNAL 7" in out, (
        "the controls did not run -- SIGNAL 7 is absent:\n" + out[-2000:])
    assert "SELFTEST PASS" in out, out[-2000:]
    assert r.returncode == 0, f"exit={r.returncode}\n{out[-2000:]}"


# --- C. the two slow rows must fail LOUDLY, and `slow` must be a real mark ----
#
# Measured on the operator's box: this file's own
# test_a_real_gate_row_runs_end_to_end_and_catches_its_mutation blew its 600s
# subprocess budget inside a ten-file run and surfaced as a raw
# subprocess.TimeoutExpired traceback -- no verdict, no next step, and a whole
# capture graded FAIL on `unit failures=1`.
#
# Two separate defects, and the budget is NOT one of them:
#
#   * `@pytest.mark.slow` was applied here but never registered, so pytest
#     warned PytestUnknownMarkWarning and the mark deselected nothing. A marker
#     that reads as a control and controls nothing is exactly the shape this
#     file exists to catch. Registered in tests/conftest.py.
#
#   * A TimeoutExpired escaping the test body gives the reader a stack trace
#     from subprocess.py and nothing about WHICH tool, what budget, or what to
#     run next. `_run_tool` converts it into a verdict that says so.
#
# WHY THE BUDGET IS UNCHANGED. Measured runtimes for the two tools:
# bd-mutation-test 25.8s and bd-band-derive 2.0s in the cloud sandbox; the whole
# file runs in 52.7s on the box. The failing run exceeded 600s -- roughly 13x
# the box's standalone figure -- so it hung rather than ran slowly, and a larger
# number would only make a hang burn more of the capture before failing. The
# budget stays calibrated to measurement and the message names the ratio, so the
# next reader can tell "slow" from "stuck" without re-deriving it.



# A local clone of tracked files only. Measured at a few seconds inside a 38s
# test; 120s is generous and, crucially, BELOW the 240s bound governing the item
# -- the first draft of this helper used 300s, which the ratchet this same cut
# ships caught immediately as a new over-bound site. The gate was right.
_CLONE_BUDGET_S = 120


def _detached_clone(dest):
    """A tracked-files-only copy of this repository, for tools that mutate.

    DELIBERATELY NOT INSIDE THE TEST. `test_the_tool_rows_go_through_the_
    diagnosing_runner` forbids a bare `subprocess.run` in any function that
    drives MT or BD, so that nobody can revert a tool call to an undiagnosed
    subprocess and keep the timeout test green. That gate is right, and this
    clone is SETUP rather than a tool invocation, so it belongs out here where
    the rule does not apply and cannot be weakened to accommodate it.
    """
    result = subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(REPO), str(dest)],
        capture_output=True, text=True, timeout=_CLONE_BUDGET_S)
    assert result.returncode == 0, (
        "could not build a detached copy to mutate:\n%s" % result.stderr[-800:])
    assert (dest / ".git").is_dir() and (dest / "tests").is_dir(), (
        "the detached copy is not a usable repository, so a green result from "
        "anything run against it would prove nothing")
    return dest


def _run_tool(argv, *, budget_s, what, site=None, **kwargs):
    """Run a whole-tree tool, converting a timeout into a DIAGNOSIS.

    Unknown is a third state and it fails -- this never downgrades a timeout to
    a skip. It only replaces an opaque traceback with the verdict, the budget,
    the measured baseline, and the one command that distinguishes a hang from
    ordinary slowness.
    """
    started = time.monotonic()
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=budget_s, **kwargs)
    except subprocess.TimeoutExpired as exc:
        baseline = _MEASURED_S.get(site)
        ratio = f" (~{budget_s / baseline:.0f}x its measured {baseline}s)" if baseline else ""
        printable = " ".join(str(a) for a in argv)
        pytest.fail(
            f"{what} did not finish within {budget_s}s{ratio}, so this row "
            f"produced NO verdict -- it is not a pass and not a fail of the "
            f"thing under test.\n"
            f"  command: {printable}\n"
            f"  next:    run that command by hand. If it completes in about "
            f"{baseline or '<measured>'}s, the tool is fine and this was a "
            f"HANG under suite load, not slowness -- raising the budget would "
            f"only make the next occurrence burn longer before failing.\n"
            f"  partial stdout: {(exc.stdout or b'')[-1500:]!r}"
        )
    # THE BASELINE POLICES ITSELF. Nothing but the run knows how long the run
    # takes, and the table above was wrong by 8x precisely because nobody
    # re-measured it. A site that outgrows its recorded cost says so here, while
    # there is still headroom, rather than by crossing the item bound under load
    # and taking its worker with it.
    elapsed = time.monotonic() - started
    baseline = _MEASURED_S.get(site)
    if baseline is not None:
        assert elapsed <= baseline * _CONTENTION_FACTOR, (
            f"{what} took {elapsed:.1f}s against a recorded baseline of "
            f"{baseline}s for site {site!r}. Re-measure it on an idle host and "
            f"update _MEASURED_S; do not widen _CONTENTION_FACTOR to hide it."
        )
    return result


def test_the_slow_marker_is_registered():
    """RED. `@pytest.mark.slow` sits on two tests in this file, but nothing
    registered it -- pytest warned PytestUnknownMarkWarning and `-m 'not slow'`
    selected nothing, so the mark was decoration.

    Asserted by asking pytest for its OWN registry rather than grepping
    conftest: the question is "can a caller deselect these", and only the
    resolved marker list answers it.
    """
    r = subprocess.run([sys.executable, "-m", "pytest", "--markers"],
                       cwd=REPO, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "@pytest.mark.slow" in r.stdout, (
        "the `slow` marker is not registered, so the @pytest.mark.slow on the "
        "two tool rows in this file deselects nothing and pytest warns on every "
        "run:\n" + r.stdout[-2000:])


def test_a_tool_timeout_reports_a_diagnosis_not_a_bare_traceback():
    """RED. Before _run_tool the timeout propagated as subprocess.TimeoutExpired
    straight out of the test body -- the reader got subprocess.py's stack and no
    statement of which tool, what budget, or what to do next.

    Drives a real timeout with a 1s budget. Asserts the failure is pytest's own
    (a stated verdict) and that it carries the three things the box run lacked:
    the tool name, the budget, and a next step. Also asserts it did NOT become a
    skip -- a timeout is 'could not evaluate', which fails.
    """
    with pytest.raises(_PYTEST_FAILURE) as caught:
        _run_tool([sys.executable, "-c", "import time; time.sleep(30)"],
                  budget_s=1, what="bd-mutation-test")
    message = str(caught.value)
    assert "bd-mutation-test" in message, message
    assert "1s" in message, message
    assert "next:" in message, message
    assert "NO verdict" in message, message


def test_the_tool_rows_go_through_the_diagnosing_runner():
    """The helper is worthless if a row bypasses it.

    Without this, someone could revert a call site to a bare
    subprocess.run(..., timeout=...) and the timeout test above would STILL be
    green -- it exercises _run_tool directly. That is the decoration defect this
    section exists to remove, one level up.

    AST over this module, scoped to the functions that actually shell out to a
    tool (they reference MT or BD): those must contain no direct subprocess.run.
    Structural rather than a substring scan, because `subprocess.run` appears
    legitimately inside _run_tool itself and in the --markers probe.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    offenders = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name != "_run_tool"]:
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        if not ({"MT", "BD"} & names):
            continue
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            f = call.func
            if (isinstance(f, ast.Attribute) and f.attr == "run"
                    and isinstance(f.value, ast.Name) and f.value.id == "subprocess"):
                offenders.append(f"{fn.name}:{call.lineno}")
    assert not offenders, (
        "these tool-invoking tests call subprocess.run directly instead of "
        f"_run_tool, so a timeout there still surfaces as a bare "
        f"TimeoutExpired traceback with no verdict: {offenders}"
    )


BD_GATE_SCOPE = "repo-wide"
