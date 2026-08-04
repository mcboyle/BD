#!/usr/bin/env python3
"""test_toolchain_534.py -- RED-first guards for the v3.66.534 toolchain additions:
  B  capture.sh runs the graph content-hash gate (P2) when a graph db is present
  E  bd-scan.py has a TS/TSX-aware scan path (semgrep-backed) for frontend/src

run_tests.py harness conventions: zero-arg test_* functions, plain asserts, no
pytest builtins, layout-flexible file discovery. Stdlib only.

RED-first map (fail on pristine 533 -> pass after 534):
  B test_capture_sh_has_graph_checkhash_gate  -> capture.sh had no graph --check-hash
       step. RED: the P2 pin is toothless on stash.
  B test_capture_sh_graph_gate_is_conditional -> the gate must be guarded by a
       db-present check (graceful skip when the db isn't deployed). RED: no gate.
  E test_bd_scan_has_ts_scan_path             -> bd-scan.py had no TS/TSX scan
       (graph is grep-level for frontend). RED: no --ts / scan_ts.
  E test_bd_scan_ts_uses_semgrep             -> the TS path must invoke semgrep
       (the uploaded kit) rather than re-grep. RED: no semgrep reference.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    p = _REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


# --------------------------------------------------------------------------- #
# B -- capture.sh graph content-hash gate                                      #
# --------------------------------------------------------------------------- #
def test_capture_sh_has_graph_checkhash_gate():
    txt = _read("capture.sh")
    assert txt, "capture.sh not found"
    assert "--check-hash" in txt or "check_hash" in txt, \
        "capture.sh has no graph --check-hash gate -- P2 pin is toothless on stash (B not wired)"


def test_capture_sh_graph_gate_uses_external_pin_with_required_mode():
    txt = _read("capture.sh")
    assert txt, "capture.sh not found"
    assert "BD_GRAPH_HASH_PIN" in txt
    assert "BD_REQUIRE_GRAPH_HASH" in txt
    assert re.search(r'if \[ ! -f "\$graph_pin" \]', txt)
    assert "UNKNOWN -- optional check not armed" in txt


# --------------------------------------------------------------------------- #
# E -- bd-scan TS/TSX scan path                                                #
# --------------------------------------------------------------------------- #
def test_bd_scan_has_ts_scan_path():
    txt = _read("tools/bd-scan.py")
    assert txt, "tools/bd-scan.py not found"
    # bd-scan already runs semgrep over the PYTHON tree; the E gap is a scan
    # that TARGETS frontend/src with TS/TSX awareness. Require an explicit
    # frontend/src TS scan path (a function or a targeted invocation).
    has_ts = ("frontend/src" in txt and
              ("def from_semgrep_ts" in txt or "scan_ts" in txt
               or "--ts" in txt or "p/typescript" in txt or "tsx" in txt.lower()))
    assert has_ts, \
        "bd-scan.py has no frontend/src TS/TSX scan path -- the frontend stays " \
        "grep-level (E not wired)"


def test_bd_scan_ts_targets_frontend_with_ts_rules():
    txt = _read("tools/bd-scan.py")
    assert txt, "tools/bd-scan.py not found"
    # the TS path must (a) target frontend/src and (b) use a TS-aware semgrep
    # config (p/typescript or p/react), not just re-run the auto Python config.
    targets_fe = "frontend/src" in txt
    ts_config = ("p/typescript" in txt or "p/react" in txt or "typescript" in txt.lower())
    assert targets_fe and ts_config, \
        "bd-scan.py TS path must target frontend/src with a TS-aware semgrep " \
        "config (p/typescript / p/react) -- E must add a real TS scan, not re-grep"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
            p += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
            f += 1
    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)


# ─────────────────────────────────────────────────────────────────────────────
# @849 -- THE PROSE-ONLY RATCHET.
#
# MEASURED 2026-08-03: of 243 bd-* tools, 167 were described somewhere and
# INVOKED BY NOTHING -- no test, no script, no CI. Among them was bd-bandcheck,
# which validates a band list against exactly the three footguns CLAUDE.md
# section 4 spends paragraphs on, and which nothing had ever called. A checker
# nobody runs is a checker that does not exist.
#
# This does NOT demand that the 166 be wired -- that is a real backlog and
# retiring or wiring them is its own work. It ratchets: the number must not
# GROW. Adding a tool that nothing invokes now costs a red test, and the fix is
# to wire it, to give it a selftest a test runs, or to bump the baseline in the
# same cut with a stated reason.
#
# THE PREDICATE'S OWN DENOMINATOR BIT ME WRITING IT, which is the joke this
# whole cut is about: the first draft counted an "executable context" as
# tests/, .github/, or a .sh suffix -- and `.githooks/pre-commit` is
# EXTENSIONLESS, so the hook that invokes bd-claim was invisible and bd-claim
# read as unwired. Same shape as CLAUDE.md section 1's `*.py` glob missing 469
# extensionless bd-* scripts. Type on the SHEBANG as well as the suffix.

# @849: 166 -> 180. THE POPULATION DID NOT GROW; THE MEASUREMENT GOT HONEST.
# _invokes() below stopped counting a mention inside a file that cannot execute
# anything as "wired". 14 tools were revealed, 7 of them named only as keys in
# scripts/emit_toolchain_ledger.py's hand-correction dict -- which records four
# of those same tools as broken. Raising a ratchet baseline is normally a smell;
# here the old number was the artifact and this one is the measurement. Each of
# the 14 was read individually and is a genuine prose mention (a docstring
# aside, an error-message string, a parity-inventory tuple), not a call.
_PROSE_ONLY_BASELINE = 180


def _bd_tools(root):
    d = os.path.join(root, "toolchain", "bin")
    return sorted(n for n in os.listdir(d) if n.startswith("bd-"))


def _is_executable_context(root, rel):
    """Would a reference here actually RUN the tool, or merely describe it?"""
    if rel.startswith(("project-knowledge/", "docs/")):
        return False                      # prose, by definition
    if rel.startswith((".github/", "tests/", ".githooks/")):
        return True
    if rel.endswith((".sh", ".py")):
        return True
    if rel.endswith((".md", ".txt", ".json", ".html", ".css")):
        return False
    # Extensionless: read the shebang rather than guessing from the name.
    try:
        with open(os.path.join(root, rel), "r", errors="replace") as fh:
            return fh.readline(200).startswith("#!")
    except OSError:
        return False


# @849 -- an execution primitive. A .py holding none of these cannot run
# anything, so a tool named in it is a RECORD ABOUT the tool, not a call to it.
# This is CLAUDE.md section 0's fix pattern applied literally: DERIVE
# reachability rather than assert it.
_EXEC_PRIMITIVE = re.compile(
    r"subprocess|os\.system|os\.exec|Popen|runpy|exec_module|pytest\.main"
    r"|check_call|check_output|SourceFileLoader|__import__|importlib")


def _invokes(tool, rel, body):
    """Does this file plausibly RUN `tool`, or does it merely NAME it?

    Both directions were measured at v3.66.849 before this predicate was
    settled, because each obvious form of it is wrong in one direction:
      - `tool in body` alone (the original) counts a data dict as wiring: all 7
        tools keyed in scripts/emit_toolchain_ledger.py read as wired.
      - requiring a literal `toolchain/bin/<tool>` path drops 52 of 80 genuinely
        wired tools, bd-band-derive and bd-mutate among them, because most
        wiring names the bare stem and joins the path at runtime.
      - the execution-primitive test ALONE wrongly flags the four bd-*fuzz*
        tools: tools/code_intelligence/fuzz_adapters.py names their paths but
        shells out through an imported `_run_bounded`, so the primitive is in
        another module entirely.
    Hence two positive signals, not one.
    """
    if tool not in body:
        return False
    # Naming an executable's PATH is only ever done in order to run it, and the
    # runner is often in an imported helper this file does not contain.
    if "toolchain/bin/%s" % tool in body:
        return True
    if not rel.endswith(".py"):
        return True          # shell / yaml / hooks: any bare word is a command
    return bool(_EXEC_PRIMITIVE.search(body))


def _prose_only(root):
    tools = _bd_tools(root)
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                             capture_output=True, text=True).stdout.split("\0")
    tracked = [f for f in tracked if f and not f.startswith("toolchain/bin/")]
    assert len(tracked) > 100, (
        "git ls-files returned %d files -- the denominator collapsed and a pass "
        "below would mean nothing." % len(tracked))
    exec_files = []
    for rel in tracked:
        p = os.path.join(root, rel)
        if not os.path.isfile(p) or not _is_executable_context(root, rel):
            continue
        try:
            with open(p, "r", errors="replace") as fh:
                exec_files.append((rel, fh.read()))
        except OSError:
            continue
    assert exec_files, ("no executable-context file was readable -- this check "
                        "cannot see its subject, which is a FAILURE and not a pass.")
    return sorted(t for t in tools
                  if not any(_invokes(t, rel, body) for rel, body in exec_files))


def test_unwired_bd_tools_do_not_multiply():
    root = str(_REPO_ROOT)
    tools = _bd_tools(root)
    assert len(tools) > 100, (
        "found only %d bd-* tools -- the subject collapsed." % len(tools))
    prose = _prose_only(root)
    assert len(prose) <= _PROSE_ONLY_BASELINE, (
        "%d bd-* tools are invoked by nothing, up from the %d baseline. The new "
        "ones are somewhere in:\n  %s\nWire it (a test that runs its --selftest "
        "counts), or raise _PROSE_ONLY_BASELINE in this cut and say why. A tool "
        "nothing invokes is a tool that does not run."
        % (len(prose), _PROSE_ONLY_BASELINE, ", ".join(prose[:12])))


def _fixture_repo(td):
    """A minimal tracked repo carrying two fake tools and two mentioning files.

    >100 filler files because _prose_only asserts its denominator did not
    collapse -- the fixture has to clear the same bar real callers do.
    """
    root = Path(td)
    (root / "toolchain" / "bin").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "filler").mkdir()
    for name in ("bd-zzz-ledger-only", "bd-zzz-invoked", "bd-zzz-pathonly"):
        (root / "toolchain" / "bin" / name).write_text("#!/usr/bin/env python3\n")
    # INERT: a pure data dict, the emit_toolchain_ledger.py shape. Naming a tool
    # here is a RECORD ABOUT it, never a call to it -- this file cannot execute.
    (root / "scripts" / "ledger.py").write_text(
        'VERDICT = {\n "bd-zzz-ledger-only": ("RUNS-DEGRADED", "scanned the wrong place"),\n}\n')
    # LIVE: really shells out.
    (root / "scripts" / "runner.py").write_text(
        'import subprocess\nsubprocess.run(["bd-zzz-invoked", "--selftest"])\n')
    # LIVE BY PATH: no execution primitive in THIS file -- it hands the path to a
    # helper imported from elsewhere. tools/code_intelligence/fuzz_adapters.py.
    (root / "scripts" / "dispatch.py").write_text(
        'from .util import _run_bounded\nADAPTERS = {"pg": "toolchain/bin/bd-zzz-pathonly"}\n')
    for i in range(110):
        (root / "filler" / f"f{i}.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True,
                   capture_output=True)
    return root


def test_a_data_dict_mention_is_not_wiring():
    """@849 -- naming a tool in a data structure is not invoking it.

    THE DEFECT: _prose_only asked `tool in blob` over any executable-CONTEXT
    file. scripts/emit_toolchain_ledger.py is a .py holding a hand-correction
    dict keyed by tool name, so all 7 tools it records read as WIRED -- four of
    them recorded verbatim as broken ("scanned the wrong place", "all zeros ...
    scanned an empty denominator"). The ratchet counted a tool as wired BECAUSE
    a generator wrote down that it does not work. CLAUDE.md section 0, in the
    gate written to police section 0.

    BOTH DIRECTIONS ARE ASSERTED, because the obvious fix is over-sensitive and
    over-sensitivity is an equal soundness bug. Measured while building this:
    requiring a literal `toolchain/bin/<tool>` path would have dropped 52 of 80
    wired tools including bd-band-derive and bd-mutate; and a reachability check
    alone wrongly flagged the four bd-*fuzz* tools, whose dispatch file names
    their PATH but executes through an imported helper.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = _fixture_repo(td)
        prose = _prose_only(str(root))

        assert "bd-zzz-ledger-only" in prose, (
            "a tool named ONLY as a key in an inert data dict was counted as "
            "wired. That is the emit_toolchain_ledger.py defect: the predicate "
            "cannot tell a record ABOUT a tool from a call TO it.")
        assert "bd-zzz-invoked" not in prose, (
            "a tool genuinely invoked via subprocess was reported unwired -- "
            "the predicate went over-sensitive, which section 0 counts as an "
            "equal soundness bug to a false clean.")
        assert "bd-zzz-pathonly" not in prose, (
            "a tool named by its toolchain/bin path was reported unwired. "
            "Naming an executable's path is done in order to run it, and the "
            "runner often lives in an imported helper this file cannot see.")


def test_the_tools_this_cut_added_are_wired_and_selftest_clean():
    """The ratchet is a floor; these three must be genuinely exercised.

    Each ships a --selftest that asserts its own failure modes. Running them
    here is what makes them WIRED rather than described -- and it is the
    difference between the 76 tools that are invoked and the 166 that are not.
    """
    root = str(_REPO_ROOT)
    for tool in ("bd-mutate", "bd-claim", "bd-bandcheck", "bd-freshcheck"):
        path = os.path.join(root, "toolchain", "bin", tool)
        assert os.path.isfile(path), "%s is missing" % tool
        r = subprocess.run([sys.executable, path, "--selftest"],
                           cwd=root, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, (
            "%s --selftest exited %d:\n%s" % (tool, r.returncode,
                                              (r.stdout + r.stderr)[-1500:]))
        assert "SELFTEST PASS" in (r.stdout + r.stderr), (
            "%s --selftest exited 0 without saying SELFTEST PASS -- an exit code "
            "with no verdict behind it is the shape this repo keeps finding."
            % tool)


_RUNNERS = ("bd-band", "bd-parband", "bd-fullsuite", "bd-retest")


def test_no_runner_hardcodes_the_wrong_interpreter():
    """@851 -- the band tools drove `python3`, which here is 3.11 without pytest.

    CLAUDE.md section 5 records the incident this causes verbatim: "a full test
    band was measured on 3.11 and reported seven failures that did not exist."
    Four runners hardcoded ["python3", "run_tests.py", ...] -- bd-band among
    them, which is the tool section 4 tells you to derive a band with. Measured
    at v3.66.850: `python3 -V` -> 3.11.15, `python3 -c "import pytest"` ->
    ModuleNotFoundError, while venv/bin/python is 3.12.3 with pytest 8.4.2.

    A run under the wrong interpreter does not error out -- it reports FAILURES,
    which read as real. That is why this is a hardcode ban and not a preference.
    """
    root = str(_REPO_ROOT)
    offenders = []
    for name in _RUNNERS:
        p = os.path.join(root, "toolchain", "bin", name)
        if not os.path.isfile(p):
            continue
        src = open(p, errors="replace").read()
        for m in re.finditer(r'\[\s*["\']python3["\']\s*,', src):
            line = src[:m.start()].count("\n") + 1
            offenders.append("%s:%d" % (name, line))
    assert not offenders, (
        "these runners build an argv starting with a literal \"python3\", which "
        "is 3.11 without the project deps here:\n  %s\nUse "
        "bdtools_sec.resolve_test_interpreter(work) -- and note bare "
        "sys.executable is NOT the fix, because `python3 <tool>` reintroduces it."
        % "\n  ".join(offenders))


def test_interpreter_resolver_proves_pytest_and_refuses_when_it_cannot():
    """Both directions. A resolver that always answers is not a check."""
    root = str(_REPO_ROOT)
    sys.path.insert(0, os.path.join(root, "toolchain", "bin"))
    import importlib
    sec = importlib.import_module("bdtools_sec")

    assert hasattr(sec, "resolve_test_interpreter"), (
        "bdtools_sec has no resolve_test_interpreter -- the shared library is "
        "where this belongs, so four runners cannot drift apart again.")

    exe = sec.resolve_test_interpreter(root)
    assert exe, "no interpreter resolved on a tree that has venv/bin/python"
    r = subprocess.run([exe, "-c", "import pytest; print(pytest.__version__)"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (
        "resolve_test_interpreter returned %s, which cannot import pytest. "
        "Returning an interpreter is a claim that it can run the suite." % exe)

    # NEG: nothing viable -> None, so the caller can exit CANNOT-EVALUATE.
    # Injected probe, because the real environment always has a good one.
    assert sec.resolve_test_interpreter(root, _probe=lambda e: False) is None, (
        "with no viable interpreter the resolver must return None. Falling back "
        "to a broken one is how the band reported failures that did not exist.")
    # OVER-SENSITIVITY: it must not return None when one IS viable.
    assert sec.resolve_test_interpreter(root, _probe=lambda e: True) is not None


def _doc_truth(args, cwd):
    tool = os.path.join(str(_REPO_ROOT), "toolchain", "bin", "bd-doc-truth")
    return subprocess.run([sys.executable, tool] + args, cwd=cwd,
                          capture_output=True, text=True, timeout=120)


def test_doc_truth_mints_a_verdict_instead_of_always_zero():
    """@850 -- bd-doc-truth is WIRED into CI and scanned nothing.

    THE DEFECT, two independent halves, each sufficient to blind it:
      1. `--docs` defaulted to /mnt/project, a path from the retired sandbox
         that does not exist in any git checkout. bd-freshcheck:193 invokes it
         as `_run([sys.executable, str(p)], root)` -- NO ARGUMENTS -- so the
         wired path scanned ZERO documents and printed "0 stale doc claim(s)".
      2. main() returned 0 unconditionally, and the --json branch returned 0
         before even reaching that. check_delegate maps rc0->OK, so even aimed
         at the real docs it could never report STALE.
    Fixing either alone leaves it blind, which is why both are asserted here.

    THE EMPTY DENOMINATOR IS THE POINT. A scan of zero documents is UNKNOWN,
    not clean -- CLAUDE.md section 0's third state. bd-freshcheck:297 already
    grades 2 as UNKNOWN and fails on it; only the producer was missing.
    """
    import tempfile
    root = str(_REPO_ROOT)

    # (1) THE WIRED INVOCATION. bd-freshcheck:193 runs this tool with no
    # arguments at all, so the default --docs IS the shipped behaviour. Assert
    # the property that matters -- it scanned a real corpus -- not the absence
    # of a string. The first draft of this checked `"/mnt/project" not in
    # --help`, which argparse never prints, so it passed on the broken tool:
    # a vacuous assertion inside the test for vacuous assertions.
    r = _doc_truth([], root)
    assert r.returncode != 2, (
        "the no-argument invocation (exactly what bd-freshcheck runs) reported "
        "an empty/absent corpus. Its --docs default must resolve into the work "
        "tree.\n%s" % (r.stdout + r.stderr)[-400:])
    assert "document(s) scanned" in (r.stdout + r.stderr), (
        "the tool does not say how many documents it scanned. A count without "
        "its denominator is the reason this defect survived: '0 stale claims' "
        "read as clean when it meant nothing was looked at.")

    with tempfile.TemporaryDirectory() as td:
        # (2) absent docs dir -> UNKNOWN(2), never a clean 0.
        r = _doc_truth(["--docs", os.path.join(td, "nope"), "--work", root], root)
        assert r.returncode == 2, (
            "scanning an ABSENT docs dir exited %d; an absent corpus is "
            "UNKNOWN (2), and unknown fails." % r.returncode)

        # (3) present but empty -> still UNKNOWN. This is the exact shape the
        # /mnt/project default produced: a real call over nothing.
        empty = os.path.join(td, "empty")
        os.makedirs(empty)
        r = _doc_truth(["--docs", empty, "--work", root], root)
        assert r.returncode == 2, (
            "scanning an EMPTY docs dir exited %d -- zero documents scanned is "
            "not zero problems found." % r.returncode)

        # (4) a real stale claim -> 1, so check_delegate grades it STALE.
        bad = os.path.join(td, "bad")
        os.makedirs(bad)
        with open(os.path.join(bad, "x.md"), "w") as fh:
            fh.write("See `bulk_downloader/does_not_exist_at_all.py`.\n")
        r = _doc_truth(["--docs", bad, "--work", root], root)
        assert r.returncode == 1, (
            "a stale file-path claim exited %d; it must be 1 or bd-freshcheck "
            "grades it OK." % r.returncode)
        # and --json must agree -- it used to return 0 before the verdict.
        rj = _doc_truth(["--docs", bad, "--work", root, "--json"], root)
        assert rj.returncode == 1, (
            "--json exited %d on a stale corpus. emit() returning True made "
            "main() return 0 before the verdict was ever computed."
            % rj.returncode)

        # (5) OVER-SENSITIVITY CONTROL: a non-empty corpus with nothing wrong
        # must still be 0. A gate that only ever fails is as useless as one
        # that only ever passes (section 0 counts both as soundness bugs).
        good = os.path.join(td, "good")
        os.makedirs(good)
        with open(os.path.join(good, "y.md"), "w") as fh:
            fh.write("See `bulk_downloader/__init__.py`, which exists.\n")
        r = _doc_truth(["--docs", good, "--work", root], root)
        assert r.returncode == 0, (
            "a clean NON-EMPTY corpus exited %d; only an empty or absent one "
            "is UNKNOWN." % r.returncode)


def test_the_derivable_half_of_staleness_is_clean():
    """bd-freshcheck --repo-only must pass: doc anchors resolve, the register's
    close section names a commit in this history, and doc claims match source.

    --repo-only is deliberate. The full run also checks .claude-env-report.md,
    a GITIGNORED per-machine provisioning artifact that does not exist in CI at
    all -- gating on it would return UNKNOWN on every run forever and the gate
    would be switched off inside a week. A check whose subject is absent from
    the place it runs is CLAUDE.md section 0 wearing a scheduler.

    What this does NOT cover, stated so a pass is not over-read: whether a cited
    line still SAYS what the doc claims. In-range is necessary, not sufficient,
    and that half needs a reader.
    """
    root = str(_REPO_ROOT)
    tool = os.path.join(root, "toolchain", "bin", "bd-freshcheck")
    assert os.path.isfile(tool), "bd-freshcheck is missing"
    r = subprocess.run([sys.executable, tool, "--root", root, "--repo-only"],
                       cwd=root, capture_output=True, text=True, timeout=300)
    out = r.stdout + r.stderr
    assert "doc file:line anchors" in out, (
        "bd-freshcheck ran but reported no anchor check -- it saw nothing, which "
        "is not the same as finding nothing wrong:\n%s" % out[-800:])
    assert r.returncode == 0, (
        "bd-freshcheck --repo-only exited %d. 1 = something is STALE, 2 = a check "
        "could not RUN (UNKNOWN, which is not a softer 1):\n%s"
        % (r.returncode, out[-1500:]))
