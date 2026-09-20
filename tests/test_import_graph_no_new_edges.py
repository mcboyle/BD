"""DECOMP-R0 — import-graph regression gate.

The surface invariants (`route_map` snapshot, the `*_surface_lock` tests,
`runner_api_snapshot`) prove that a decomposition cut removed *nothing* — no
route, method, or public name vanished. They are blind to the opposite failure:
a cut that quietly *adds* an inter-module import edge it was not supposed to —
the accidental-coupling / lazy-accessor-sprawl class (hazard H-14).

This test is that complement. It freezes the intended internal import-edge set
(`tools/decomp/import_graph_baseline.json`) and asserts the live graph adds no
edge outside it. When a cut *intends* a new edge, the baseline is re-frozen ONCE
on MERGED MAIN -- rebase first, then run
`./venv/bin/python tools/decomp/import_graph_gate.py --update` there. Never
inside the cut that adds the edge: the baseline is one shared file, so parallel
cuts collide on it and a baseline frozen against an unmerged tree bakes in edges
from work that has not landed. Declared, never silent.

Conventions: the custom `run_tests.py` runner chdirs to a temp dir, so the gate
is loaded by absolute path off this file's location (not via `tools.decomp.*`,
which also keeps the gate out of the product import graph it measures). Uses
`assert ..., msg` (the runner's pytest stub has no `pytest.fail`); zero-arg test
functions; no pytest builtins.
"""
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest  # noqa: F401  (harmless under real pytest + the custom runner)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GATE = _REPO_ROOT / "tools" / "decomp" / "import_graph_gate.py"


def _load_gate():
    assert _GATE.exists(), (
        f"{_GATE.relative_to(_REPO_ROOT)} is missing — the DECOMP-R0 import-graph "
        f"gate was not landed."
    )
    spec = importlib.util.spec_from_file_location("_r0_import_graph_gate", _GATE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_baseline_present_and_well_formed():
    gate = _load_gate()
    base = gate.load_baseline(_REPO_ROOT)
    assert base["edges"], "frozen baseline has no edges — it was never populated."
    assert base["edge_count"] == sum(len(v) for v in base["edges"].values()), (
        "baseline edge_count disagrees with its own edge map — regenerate with "
        "`./venv/bin/python tools/decomp/import_graph_gate.py --update`."
    )


def test_no_new_edges():
    """The gate proper: the live graph introduces no edge outside the frozen set."""
    gate = _load_gate()
    new, removed = gate.check(_REPO_ROOT)
    assert not new, (
        "NEW import edge(s) not in the frozen baseline — a cut coupled modules it "
        "should not have (or an intended edge was not declared). Review, then if "
        "intended: rebase onto merged main and re-freeze ONCE there with "
        "`./venv/bin/python tools/decomp/import_graph_gate.py --update` — never "
        "inside the cut that adds the edge:\n  " + "\n  ".join(f"{s} -> {d}" for s, d in new)
    )


def test_gate_detects_an_injected_edge():
    """Teeth: prove the comparison is not vacuous — an edge absent from the
    baseline must be reported as NEW. Pure in-memory; mutates no tree."""
    gate = _load_gate()
    base = gate.load_baseline(_REPO_ROOT)
    fake = ("bulk_downloader/__r0_probe_src.py", "bulk_downloader/__r0_probe_dst.py")
    injected = {(s, d) for s, lst in base["edges"].items() for d in lst}
    injected.add(fake)
    new, _ = gate.compare_edges(_baseline_edge_set(base), injected)
    assert fake in set(new), (
        "the gate failed to flag a synthetic edge absent from the baseline — the "
        "regression check would pass vacuously."
    )


def _baseline_edge_set(base):
    return {(s, d) for s, lst in base["edges"].items() for d in lst}


# --------------------------------------------------------------------------
# N7. The gate's own REMEDY was wrong in two ways at once, and a wrong remedy
# is worse than none: it is the line an agent copies.
#   1. "re-freeze in the SAME cut" -- parallel cuts each re-freezing the
#      baseline collide, and a baseline frozen against an unmerged tree bakes
#      in edges from work that has not landed. Rebase, then re-freeze ONCE on
#      merged main.
#   2. a bare `python3` -- the TOOL PIN LAW: repo tools are invoked through
#      this checkout's interpreter, never a PATH binary that may be another
#      build entirely.
# These assert the string the gate EMITS on its executed paths, so the next
# edit to the wording cannot silently un-fix it.
# --------------------------------------------------------------------------
import contextlib  # noqa: E402
import io  # noqa: E402

_TOOL_PIN = "./venv/bin/python tools/decomp/import_graph_gate.py --update"
_BARE_PYTHON3 = "python3 tools/decomp/import_graph_gate.py"


def _emit_new_edge_failure():
    """Drive the real main() down its NEW-edge branch and capture what it says."""
    gate = _load_gate()
    fake = [("bulk_downloader/__n7_probe_src.py", "bulk_downloader/__n7_probe_dst.py")]
    original = gate.check
    buf = io.StringIO()
    try:
        gate.check = lambda _root=None: (fake, [])
        with contextlib.redirect_stdout(buf):
            rc = gate.main(["--check"])
    finally:
        gate.check = original
    return rc, buf.getvalue()


def test_the_new_edge_remedy_is_emitted_and_is_the_pinned_interpreter():
    rc, out = _emit_new_edge_failure()
    # PRECONDITION: this is the failure branch, and it really printed the edge.
    assert rc == 1, f"expected the NEW-edge branch to fail, got rc={rc!r}"
    assert "__n7_probe_src.py" in out, (
        "the probe never reached the NEW-edge branch; the assertions below "
        f"would be vacuous. Output was: {out!r}"
    )
    assert _TOOL_PIN in out, (
        "the gate's remedy does not name the pinned interpreter "
        f"({_TOOL_PIN!r}). Emitted: {out!r}"
    )
    assert _BARE_PYTHON3 not in out, (
        "the gate's remedy still tells the reader to run a bare `python3` "
        f"repo tool, which the TOOL PIN LAW forbids. Emitted: {out!r}"
    )


def test_the_new_edge_remedy_says_rebase_then_refreeze_once_not_same_cut():
    _rc, out = _emit_new_edge_failure()
    lowered = out.lower()
    assert "same cut" not in lowered, (
        "the gate still tells a worker to re-freeze the baseline IN THEIR OWN "
        "CUT. Parallel cuts collide on that file and a baseline frozen against "
        f"an unmerged tree is wrong. Emitted: {out!r}"
    )
    assert "rebase" in lowered, (
        f"the remedy does not tell the reader to rebase first. Emitted: {out!r}"
    )
    assert "once" in lowered, (
        "the remedy does not say the re-freeze happens ONCE, on merged main. "
        f"Emitted: {out!r}"
    )


def test_the_missing_baseline_remedy_is_also_the_pinned_interpreter():
    """The gate's OTHER emitted remedy, on its own executed path."""
    import tempfile
    gate = _load_gate()
    with tempfile.TemporaryDirectory() as empty:
        try:
            gate.load_baseline(Path(empty))
        except FileNotFoundError as exc:
            message = str(exc)
        else:
            raise AssertionError(
                "load_baseline did not raise on a root with no baseline, so "
                "its remedy string was never emitted."
            )
    assert "--update" in message, (
        f"precondition: this is not the remedy-bearing message: {message!r}"
    )
    assert _TOOL_PIN in message, (
        f"the missing-baseline remedy is not the pinned interpreter: {message!r}"
    )
    assert _BARE_PYTHON3 not in message, (
        f"the missing-baseline remedy still says bare `python3`: {message!r}"
    )


def test_no_remedy_anywhere_in_the_gate_source_says_bare_python3():
    """The complete population: every --update invocation in the gate file.

    Derived from the file itself, not from a list. The scan is proven able to
    say YES against a known-positive line before its NO is believed.
    """
    source = _GATE.read_text(encoding="utf-8")
    invocations = [line.strip() for line in source.splitlines()
                   if "import_graph_gate.py --update" in line]
    assert len(invocations) >= 2, (
        f"expected the gate to carry its remedy, found "
        f"{len(invocations)}: {invocations!r}"
    )
    # The two EMITTED remedies must resolve through the single producer rather
    # than re-spelling the command, or one of them drifts back on the next edit.
    emitters = [line.strip() for line in source.splitlines()
                if "UPDATE_COMMAND" in line]
    assert len(emitters) >= 3, (
        f"expected UPDATE_COMMAND to be defined and used by both emitted "
        f"remedies, found {len(emitters)}: {emitters!r}"
    )
    # POSITIVE CONTROL: the same probe, run against a line that IS bare python3.
    # Built by concatenation so this line is not itself an offender in the
    # tree-wide sweep below -- the control must not become the thing it detects.
    control = ["  " + _BARE_PYTHON3 + " --update"]
    assert [ln for ln in control if _BARE_PYTHON3 in ln] == control, (
        "the probe cannot detect a bare python3 invocation, so its NO below "
        "would be measuring the probe, not the file."
    )
    offenders = [ln for ln in invocations if _BARE_PYTHON3 in ln]
    assert offenders == [], (
        f"{len(offenders)} of {len(invocations)} remedy line(s) in "
        f"{_GATE.name} still invoke a bare `python3`: {offenders!r}"
    )


# --------------------------------------------------------------------------
# N7 round 2. ONE emitter with a test and two without is the same defect with a
# smaller blast radius: bd-decomp prints its remedy while an agent is PLANNING
# the extraction -- BEFORE the edge exists -- so it is the FIRST remedy read and
# the gate's is the second. The sweep below DERIVES its population from the tree
# rather than from a handed-over list, but the emitters it can FAIL on are frozen
# to the four this cut corrects -- see _FROZEN_EMITTERS and the note above it.
# --------------------------------------------------------------------------
import subprocess  # noqa: E402

# Executable emitters only. Excluded, each with its reason:
#   *.md under project-knowledge/ and CHANGELOG.md -- register and append-only
#     history; a worker does not rewrite either. Findings are reported, not edited.
#   tests/mutants/ -- mutation payloads carry the hazard BY DESIGN; a spec that
#     restores the wrong remedy is the proof the gate has teeth, not a violation.
_SWEEP_ROOTS = ("tools/", "toolchain/", "tests/")
_SWEEP_FILES = ("FOOTGUNS.json",)
_SWEEP_SKIP = ("tests/mutants/",)


def _tracked_files():
    """Every tracked path, from git. None means COULD NOT LOOK (never OK)."""
    try:
        done = subprocess.run(
            ["git", "ls-files", "-z"], cwd=str(_REPO_ROOT),
            capture_output=True, text=True, timeout=120)
    except Exception:
        return None
    if done.returncode != 0:
        return None
    return [p for p in done.stdout.split("\0") if p]


def _is_import_edge_remedy(line):
    """A line that tells a reader how to settle the import-graph baseline."""
    low = line.lower()
    if "import_graph_gate.py --update" in line:
        return True
    return "re-freeze" in low and "baseline" in low and "edge" in low


def _remedy_lines():
    """(path, lineno, text) for every executable import-edge remedy in the tree."""
    files = _tracked_files()
    assert files is not None, (
        "COULD NOT LOOK: `git ls-files` did not run, so this test measured "
        "nothing. That is UNKNOWN, which is never permission."
    )
    assert len(files) > 1000, (
        f"COULD NOT LOOK: git listed only {len(files)} tracked files; the sweep "
        f"is not seeing the repository."
    )
    found = []
    for rel in files:
        if any(rel.startswith(s) for s in _SWEEP_SKIP):
            continue
        if not (rel in _SWEEP_FILES or any(rel.startswith(r) for r in _SWEEP_ROOTS)):
            continue
        try:
            text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if _is_import_edge_remedy(line):
                found.append((rel, number, line.strip()))
    return found


# THE FROZEN SUBJECT OF THIS GATE. It is the four emitters this cut corrects and
# NOTHING ELSE. A fourth emitter appearing later is a DIFFERENT problem -- a
# tree-wide enumeration of remedy emitters -- and it is its own row, not a
# failure of this test: an acceptance that grows after the fact manufactures
# refusals against an object that already met the bar it was given. The sweep
# below still runs over the WHOLE tree, because the denominator must be derived
# from the filesystem, but only these four paths can FAIL it.
_FROZEN_EMITTERS = (
    "tools/decomp/import_graph_gate.py",
    "toolchain/bin/bd-decomp",
    "toolchain/bin/bd-band-derive",
    "FOOTGUNS.json",
)


def test_every_emitted_import_edge_remedy_is_correct():
    """The population is derived from the tree, not from a list someone handed me."""
    swept = _remedy_lines()

    # PRECONDITION / DENOMINATOR: the tree-derived sweep really reaches each of
    # the four frozen emitters. If it cannot see them it is measuring its own
    # extraction and every assertion below would be vacuously green.
    emitting_files = {rel for rel, _n, _t in swept}
    for expected in _FROZEN_EMITTERS:
        assert expected in emitting_files, (
            f"{expected} emits an import-edge remedy but the sweep did not see "
            f"it; the probe is too narrow. Saw: {sorted(emitting_files)!r}"
        )

    lines = [(r, n, t) for r, n, t in swept if r in _FROZEN_EMITTERS]
    assert len(lines) >= 6, (
        f"the sweep found only {len(lines)} import-edge remedy line(s) across "
        f"the four frozen emitters; it is measuring its own extraction, not the "
        f"tree: {lines!r}"
    )

    # POSITIVE CONTROL: the same two checks, run against lines that ARE wrong.
    # Concatenated so the control is not itself swept as an offender.
    bad_same_cut = "re-freeze the baseline for a new edge in the SAME" + " cut"
    bad_bare = "  " + _BARE_PYTHON3 + " --update"
    assert "same cut" in bad_same_cut.lower(), "the SAME-cut probe cannot say yes"
    assert "python3 tools/" in bad_bare, "the bare-python3 probe cannot say yes"

    same_cut = [(r, n, t) for r, n, t in lines if "same cut" in t.lower()]
    assert same_cut == [], (
        f"{len(same_cut)} emitted remedy line(s) still tell a reader to re-freeze "
        f"the import-graph baseline IN THE SAME CUT. The baseline is one shared "
        f"file: parallel cuts collide on it and a baseline frozen against an "
        f"unmerged tree bakes in unlanded edges. Offenders: {same_cut!r}"
    )
    bare = [(r, n, t) for r, n, t in lines
            if "python3 tools/" in t or "python3 toolchain/" in t]
    assert bare == [], (
        f"{len(bare)} emitted remedy line(s) invoke a repo tool through a bare "
        f"`python3`, which the TOOL PIN LAW forbids -- a PATH interpreter can be "
        f"an entirely different build. Offenders: {bare!r}"
    )


def test_the_bd_decomp_planning_remedy_is_correct_where_it_is_emitted():
    """bd-decomp prints this BEFORE the edge exists, so it is read first."""
    import importlib.util
    path = _REPO_ROOT / "toolchain" / "bin" / "bd-decomp"
    assert path.exists(), f"{path} is missing"
    spec = importlib.util.spec_from_loader(
        "_n7_bd_decomp",
        importlib.machinery.SourceFileLoader("_n7_bd_decomp", str(path)))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    # bd-decomp imports its sibling bdtools_sec, which lives beside it and is
    # normally found because the script runs from toolchain/bin.
    added = str(path.parent)
    sys.path.insert(0, added)
    try:
        spec.loader.exec_module(mod)
    finally:
        if sys.path and sys.path[0] == added:
            sys.path.pop(0)

    gates = mod.gates_for(_REPO_ROOT, "bulk_downloader/probe.py", False)
    entry = [g for g in gates if g[0] == "FG-IMPORT-EDGE-BASELINE"]
    assert len(entry) == 1, (
        f"precondition: expected exactly one FG-IMPORT-EDGE-BASELINE gate, got "
        f"{[g[0] for g in gates]!r}"
    )
    remedy = entry[0][2]
    assert _TOOL_PIN in remedy, (
        f"bd-decomp's planning remedy is not the pinned interpreter: {remedy!r}"
    )
    assert "same cut" not in remedy.lower(), (
        f"bd-decomp still tells a planner to re-freeze in the SAME cut: {remedy!r}"
    )
    assert "rebase" in remedy.lower() and "once" in remedy.lower(), (
        f"bd-decomp's remedy does not say rebase-then-re-freeze-ONCE: {remedy!r}"
    )


def test_the_footguns_declaration_carries_the_corrected_rule_and_fix():
    import json
    data = json.loads((_REPO_ROOT / "FOOTGUNS.json").read_text(encoding="utf-8"))
    entries = [f for f in data["footguns"] if f["id"] == "FG-IMPORT-EDGE-BASELINE"]
    assert len(entries) == 1, (
        f"precondition: expected exactly one FG-IMPORT-EDGE-BASELINE entry, got "
        f"{len(entries)}"
    )
    entry = entries[0]
    for field in ("rule", "fix"):
        value = entry[field]
        assert "same cut" not in value.lower(), (
            f"FOOTGUNS.json {field!r} still says SAME cut: {value!r}"
        )
        assert "python3 tools/" not in value, (
            f"FOOTGUNS.json {field!r} still invokes a bare python3: {value!r}"
        )
    assert _TOOL_PIN in entry["fix"], (
        f"FOOTGUNS.json fix is not the pinned interpreter: {entry['fix']!r}"
    )
    assert "merged main" in entry["rule"].lower(), (
        f"FOOTGUNS.json rule does not say where the re-freeze happens: "
        f"{entry['rule']!r}"
    )
