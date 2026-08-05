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
import ast
import functools
import json
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
# @856: 180 -> 184. AGAIN the population did not grow; the measurement got
# honest a third time. _invokes() now (a) matches NAME-EXACTLY, so a tool no
# longer inherits the wiring of a longer tool that starts with its name -- 16 of
# 246 were shadowed that way -- and (b) matches against CODE with comments and
# docstrings stripped, because a `# bd-state cleanly SKIPS` comment is not a
# call. Both were found while using this predicate to decide what is safe to
# DELETE, which is the use that makes a false "wired" expensive: it hides a dead
# tool behind a sentence describing it.
#
# Sanity-checked in both directions at v3.66.856: all 12 known-wired tools
# (bd-mutate, bd-band-derive, bd-freshcheck, bd-claim, bd-bandcheck,
# bd-tool-smoke, bd-doc-truth, bd-docstale, bd-equiv, bd-band, bd-guardcheck,
# bd-regen-order) are still detected -- no false negative, which is the
# direction that would license deleting a tool that IS called.
#
# KNOWN REMAINING IMPRECISION, stated rather than hidden: toolchain/
# install_bdsuite.sh prints an `echo "ready: bd-boot, bd-cut, bd-handoff,
# bd-pack, ..."` banner, and the shell path cannot tell a banner from a command,
# so bd-handoff and bd-pack read as wired off that line. Stripping shell strings
# would break real invocations, so this errs toward "wired" -- the safe
# direction for a deletion gate.
_PROSE_ONLY_BASELINE = 184


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


@functools.lru_cache(maxsize=4096)
def _code_only(body, is_py):
    """`body` with COMMENTS and DOCSTRINGS removed; other string literals kept.

    @856, the fourth denominator bug in this predicate and the subtlest. After
    the prefix fix, six retirement candidates still read as WIRED -- and every
    single hit was prose: a `# bd-state cleanly SKIPS` comment in
    tools/build_release.py, an echo banner in install_bdsuite.sh, and -- best of
    all -- THIS FILE's own docstring, which names bd-rollback and bd-audit as
    examples of the bug and thereby wired them.

    Comments and docstrings cannot execute anything. Other string literals CAN:
    `subprocess.run(["bd-audit", "--json"])` is real wiring and must survive, so
    this deliberately does NOT strip all strings.

    Non-Python (shell/yaml) keeps `#` comments stripped line-wise; that is
    imprecise inside heredocs but errs toward calling a mention prose, and for a
    DELETION gate the safe error is the one that leaves a tool looking unwired
    only when nothing executable names it.
    """
    if not is_py:
        return "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    import io
    import tokenize
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(body).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return body                      # unparseable -> do not silently narrow
    docstrings = set()
    try:
        tree = ast.parse(body)
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef)) and n.body:
                first = n.body[0]
                if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    docstrings.add((first.value.lineno, first.value.col_offset))
    except SyntaxError:
        pass
    out = []
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and tok.start in docstrings:
            continue
        out.append(tok.string)
    return "\n".join(out)


def _names(tool, body, prefix=""):
    """Does `body` mention EXACTLY this tool, not a longer one that starts with it?

    @856. Plain `tool in body` made bd-band read as wired off bd-band-derive.
    `\\b` does not help: it treats "-" as a word boundary, so bd-band\\b still
    matches inside bd-band-derive. The continuation class must therefore include
    the hyphen. The trailing (?!\\.[A-Za-z0-9]) rejects "bd-state.json" -- a file
    whose name begins with a tool's name is not that tool -- while still letting
    the two genuinely .py-suffixed tools (bd-triage.py, bd-audit-gate.py) match
    themselves, because their own name is consumed before the lookahead applies.
    """
    needle = prefix + tool
    # Fast reject FIRST. _prose_only performs ~314k of these (246 tools x 638
    # executable-context files x 2 signals) over whole file bodies. `in` is a
    # C-level substring search; the regex below scans. Running the regex
    # unconditionally took this file from 35s to 6m29s -- and lru_cache did not
    # help, because the cost is the SCAN, not the compile. A gate that slow gets
    # switched off, which section 0 counts as a soundness problem.
    if needle not in body:
        return False
    return _name_re(needle).search(body) is not None


@functools.lru_cache(maxsize=1024)
def _name_re(needle):
    return re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(needle)
                      + r"(?![A-Za-z0-9_-])(?!\.[A-Za-z0-9])")


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

    @856 -- and every match is now NAME-EXACT. The bare `in` test had no word
    boundary, so a tool whose name PREFIXES another's inherited that tool's
    wiring: bd-band from bd-band-derive, bd-rollback from bd-rollback-oracle,
    bd-audit from bd-audit-gate.py, bd-state from "bd-state.json". 16 of 246
    tools are shadowed that way.
    """
    # @856: match against CODE, not prose. Comments and docstrings cannot run
    # anything -- see _code_only, which this file's own docstring motivated.
    code = _code_only(body, rel.endswith(".py"))
    if not _names(tool, code):
        return False
    # Naming an executable's PATH is only ever done in order to run it, and the
    # runner is often in an imported helper this file does not contain.
    if _names(tool, code, prefix="toolchain/bin/"):
        return True
    if not rel.endswith(".py"):
        return True          # shell / yaml / hooks: any bare word is a command
    return bool(_EXEC_PRIMITIVE.search(code))


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


def test_a_tool_name_that_prefixes_another_is_not_wiring():
    """@856 -- the THIRD denominator bug in this one predicate.

    _invokes() opened with `if tool not in body`, a bare substring test with no
    word boundary. So a tool whose NAME IS A PREFIX of another tool's name reads
    as WIRED purely because the LONGER tool is invoked somewhere:
        bd-band     <- bd-band-derive, bd-bandcheck
        bd-rollback <- bd-rollback-oracle, bd-rollback-plan
        bd-audit    <- bd-audit-gate.py
        bd-state    <- bd-state-machine-extract, and "bd-state.json" (a FILE)
    Measured at v3.66.855: 16 of 246 tools are shadowed this way.

    This predicate decides which tools are safe to DELETE. A false "wired" hides
    a dead tool; a false "unwired" licenses deleting one that is still called.
    Both directions are asserted here, and the second is the dangerous one.

    Deliberately NOT a word-boundary regex alone: \\b treats "-" as a boundary,
    so bd-band would still match inside bd-band-derive. The continuation class
    has to include the hyphen, and a following ".ext" has to be excluded too, or
    "bd-state.json" reads as bd-state.
    """
    root = str(_REPO_ROOT)
    sys.path.insert(0, root)
    import importlib
    mod = importlib.import_module("tests.test_toolchain_534")

    # SYNTHETIC names throughout. Using the REAL ones (bd-band, bd-rollback,
    # bd-audit) made this test wire them: its own string literals are code, so
    # _invokes found them and three retirement candidates read as WIRED off
    # nothing but this file. A test must not perturb the population it measures
    # -- the same self-reference that @849 caught in emit_toolchain_ledger.
    longer = 'subprocess.run([sys.executable, "toolchain/bin/bd-zz-tool-long"])'
    assert mod._invokes("bd-zz-tool-long", "x.py", longer) is True, (
        "the tool actually invoked stopped counting -- over-correction")
    assert mod._invokes("bd-zz-tool", "x.py", longer) is False, (
        "a PREFIX read as WIRED off the longer tool's invocation. A prefix is "
        "not a name, and this predicate authorises deletions.")

    # a FILENAME beginning with the tool name is not the tool.
    assert mod._invokes("bd-zz-tool", "x.py",
                        'subprocess.run(["cat", "bd-zz-tool.json"])') is False, (
        "a .json file whose name starts with the tool name is not the tool.")

    # OVER-SENSITIVITY CONTROL -- real wiring must still count, in each of the
    # three shapes it actually takes in this repo.
    assert mod._invokes("bd-zz-tool", "x.py",
                        'subprocess.run([sys.executable, "toolchain/bin/bd-zz-tool"])') is True
    assert mod._invokes("bd-zz-tool", "run.sh",
                        "venv/bin/python toolchain/bin/bd-zz-tool tests/x.py") is True
    assert mod._invokes("bd-zz-tool", "x.py",
                        'subprocess.run(["bd-zz-tool", "--json"])') is True, (
        "an exact-name argv entry must still count -- refusing it would license "
        "deleting a tool that IS called.")

    # PROSE IS NOT A CALL. Asserted directly, because the ratchet cannot catch
    # this: _PROSE_ONLY_BASELINE is a <= ceiling, so losing the comment-stripping
    # LOWERS the count and the ratchet stays green. bd-mutate found exactly that
    # escape, which is the whole argument for a behavioural assertion here.
    commented = ('import subprocess\n'
                 '# bd-zz-tool cleanly SKIPS its check when the zip is absent\n'
                 'subprocess.run(["something-else"])\n')
    assert mod._invokes("bd-zz-tool", "x.py", commented) is False, (
        "a tool named only in a COMMENT read as wired. Real instance: "
        "tools/build_release.py carries `# ... bd-state cleanly SKIPS ...`.")

    docstringed = ('"""Notes: bd-zz-tool is the final cross-check."""\n'
                   'import subprocess\n'
                   'subprocess.run(["something-else"])\n')
    assert mod._invokes("bd-zz-tool", "x.py", docstringed) is False, (
        "a tool named only in a DOCSTRING read as wired -- which is how THIS "
        "file's own prose wired three retirement candidates before @856.")

    # ...but a string literal that is a real argument still counts (above), so
    # the stripping must not reach ordinary strings.
    assert mod._code_only('x = "bd-zz-tool"  # a comment\n', True).count("bd-zz-tool") == 1, (
        "stripping removed a live string literal, not just the comment")


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


def test_tool_smoke_runs_unaided_and_the_toolchain_has_no_undefined_names():
    """@854 -- the only static check that can see toolchain/bin, now wired.

    WHY IT MATTERS: ci.yml's compileall COMPILES without resolving names, and
    its pyflakes step is `|| true` (advisory) AND scoped to bulk_downloader and
    tools -- so the 243 extensionless bd-* scripts had no static checking of any
    kind. bd-tool-smoke found a real one on its first run:
        bd-pack:169  undefined name 'TRACKER_FILES'
    TRACKER_FILES was deleted at v3.66.841 with the TASK_TRACKER retirement and
    that use site was left behind. test_task_tracker_stays_retired.py polices
    the retirement by scanning for TASK_TRACKER references, and 'TRACKER_FILES'
    does not contain that string -- the dangling name sat outside its
    denominator. bd-pack --selftest exits 0 because it drives another path.

    NOT a false-clean gate before this: with BIN hardcoded to /home/claude/bin
    it raised FileNotFoundError and exited 1. It failed CLOSED, which is right;
    it was simply unusable without flags, so nothing ran it.
    """
    root = str(_REPO_ROOT)
    tool = os.path.join(root, "toolchain", "bin", "bd-tool-smoke")
    assert os.path.isfile(tool), "bd-tool-smoke is missing"

    # The wired invocation takes NO arguments -- BIN and WORK must self-resolve.
    r = subprocess.run([sys.executable, tool, "--gate"], cwd=root,
                       capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    m = re.search(r"(\d+)\s+python tools scanned", out)
    assert m, ("bd-tool-smoke --gate did not report a scan count; it must say "
               "what its denominator was:\n%s" % out[-600:])
    assert int(m.group(1)) > 100, (
        "only %s tools scanned -- the denominator collapsed, and a clean verdict "
        "over it would mean nothing." % m.group(1))
    assert r.returncode == 0, (
        "bd-tool-smoke --gate exited %d: a tool in toolchain/bin would crash on "
        "invocation. Nothing else in CI can see this -- compileall does not "
        "resolve names and pyflakes is advisory and scoped elsewhere.\n%s"
        % (r.returncode, out[-1200:]))


def _docstale(args, cwd):
    tool = os.path.join(str(_REPO_ROOT), "toolchain", "bin", "bd-docstale")
    return subprocess.run([sys.executable, tool] + args, cwd=cwd,
                          capture_output=True, text=True, timeout=120)


def _docstale_fixture(td):
    """A minimal tree bd-docstale can resolve: a readable __version__ under
    --work, and an empty docs dir for --dir. Returns the docs path.

    A fixture rather than the live corpus on purpose -- project-knowledge/
    moves whenever anyone edits a header, and a window test asserting over it
    would fail for reasons that have nothing to do with the window.
    """
    os.makedirs(os.path.join(td, "bulk_downloader"), exist_ok=True)
    with open(os.path.join(td, "bulk_downloader", "__init__.py"), "w") as fh:
        fh.write('__version__ = "3.66.900"\n')
    docs = os.path.join(td, "project-knowledge")
    os.makedirs(docs, exist_ok=True)
    return docs


def test_docstale_cannot_report_current_when_it_does_not_know_the_version():
    """@853 -- bd-docstale asserted the OPPOSITE of the truth, three ways.

    It is the complement of bd-doc-truth (fixed @850): that one checks whether a
    cited PATH still resolves, this one checks how far a doc's verified-against
    marker is behind __version__. Measured at v3.66.852:

      1. --dir defaulted to /mnt/project    -> "no verified-against markers
         found", exit 0, while 79 tracked docs carry the marker.
      2. --work defaulted to /home/claude/work, so the current version could not
         be read -- and `d = (cur - p) if cur else 0` then scored EVERY doc as
         0 behind. A doc pinned at 3.66.47 printed in GREEN as "current" and the
         summary read "worst is 0 releases behind". Not blind: INVERTED.
      3. an empty scan returned 0.

    (2) is the one worth the test. A missing minuend silently became zero, so
    the tool's confident output was maximally wrong exactly when its input was
    missing. Unknown is a third state and it FAILS -- it does not become 0.
    """
    import tempfile
    root = str(_REPO_ROOT)

    # (1) the shipped no-argument invocation must scan real docs.
    r = _docstale([], root)
    assert r.returncode != 2, (
        "the no-argument invocation found no corpus; --dir must resolve into "
        "the work tree.\n%s" % (r.stdout + r.stderr)[-300:])
    assert "no verified-against markers found" not in r.stdout, (
        "default --dir still points somewhere with no markers, while the repo "
        "has dozens.")

    # (2) THE INVERSION: an unresolvable current version must refuse, never
    # score every doc as current.
    with tempfile.TemporaryDirectory() as td:
        r = _docstale(["--dir", os.path.join(root, "project-knowledge"),
                       "--work", td], root)
        assert r.returncode == 2, (
            "with no readable __version__ under --work the tool exited %d. It "
            "used to print every doc as 'current' and 'worst is 0 releases "
            "behind' -- a confident inversion, not a gap." % r.returncode)
        # The specific thing that must not happen: a graded table. "worst is
        # N releases behind" is printed only on the grading path, and it read
        # "worst is 0" while docs were 805 behind. (Do not assert on the words
        # "current"/"behind" alone -- the refusal message contains both, which
        # made the first draft of this line fail on a correct tool.)
        assert "worst is" not in r.stdout, (
            "it still emitted a graded summary against an unknown current "
            "version:\n%s" % r.stdout[-300:])

        # (3) empty corpus -> UNKNOWN, not clean.
        empty = os.path.join(td, "empty")
        os.makedirs(empty)
        r = _docstale(["--dir", empty, "--work", root], root)
        assert r.returncode == 2, (
            "an empty docs dir exited %d; zero documents scanned is not zero "
            "problems found." % r.returncode)

    # (4) OVER-SENSITIVITY: a real corpus with a readable version must still
    # produce a report at exit 0 absent --behind. A tool that only ever refuses
    # is not an instrument (section 0 counts that as an equal soundness bug).
    r = _docstale(["--dir", os.path.join(root, "project-knowledge"),
                   "--work", root], root)
    assert r.returncode == 0, (
        "a fully-resolvable run exited %d; without --behind this is a report "
        "tool and must report." % r.returncode)
    assert "behind" in r.stdout, "resolvable run produced no staleness figures"


def test_docstale_reads_three_lines_not_line_one_three_times():
    """@864 -- the scan window opened the file three times and read line 1.

        head = "".join([next(open(f)) for _ in range(3)])

    Three FRESH handles, each yielding its own first line, so `head` was line 1
    repeated -- not lines 1-3. The comment above it and the except branch
    (StopIteration for a file shorter than 3 lines) both describe a three-line
    read that never happened, which is why it survived review.

    MEASURED on this repo at v3.66.863: 65 docs carry the marker in their first
    three lines, 55 have it on line 1, and the five below have it on line 2 --
    invisible to the tool, which then printed "55 marked docs" as its
    denominator and graded staleness over a population 8% smaller than the real
    one. Under-counting the denominator is the same defect as scanning zero
    documents (bd-doc-truth @850), just less obvious.

    A fixture is used rather than the live corpus: the real docs move every
    time someone edits a header, and a test that asserts over them would fail
    for reasons unrelated to the window.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        docs = _docstale_fixture(td)
        # line 1 -- was already visible
        with open(os.path.join(docs, "on_line_one.md"), "w") as fh:
            fh.write("verified-against: v3.66.700\n# Title\nbody\n")
        # line 2 and line 3 -- the blind spots
        with open(os.path.join(docs, "on_line_two.md"), "w") as fh:
            fh.write("# Title\nverified-against: v3.66.701\nbody\n")
        with open(os.path.join(docs, "on_line_three.md"), "w") as fh:
            fh.write("# Title\n\nverified-against: v3.66.702\nbody\n")

        r = _docstale(["--dir", docs, "--work", td], td)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "3 marked docs" in r.stdout, (
            "the scan window still misses a marker below line 1 -- it reported:"
            "\n%s" % r.stdout[-400:])
        for name in ("on_line_one.md", "on_line_two.md", "on_line_three.md"):
            assert name in r.stdout, "%s missing from the report:\n%s" % (
                name, r.stdout[-400:])


def test_docstale_does_not_widen_past_its_window():
    """The over-sensitive direction. A marker on line 4 is out of the stated
    three-line window; widening the read until everything matches would make
    the tool grade prose that merely mentions the string."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        docs = _docstale_fixture(td)
        with open(os.path.join(docs, "on_line_one.md"), "w") as fh:
            fh.write("verified-against: v3.66.700\n# Title\nbody\n")
        with open(os.path.join(docs, "way_down.md"), "w") as fh:
            fh.write("# Title\n\n\nverified-against: v3.66.001\n")
        r = _docstale(["--dir", docs, "--work", td], td)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "1 marked docs" in r.stdout, (
            "the window widened past three lines:\n%s" % r.stdout[-400:])


def test_equiv_refuses_to_certify_over_an_empty_token_set():
    """@852 -- bd-equiv licensed retirement on the strength of measuring nothing.

    It is the tool that AUTHORIZES deleting another tool ("prove one tool can
    safely REPLACE another, before you retire it"). Its verdict is derived by
    diffing two token SETS, and when both sets came back empty -- because the
    default --extract is a test-path regex and most tools emit no test paths --
    every row was `equal: true` and the verdict was EQUAL at exit 0.

    Measured at v3.66.851 on two tools that share nothing:
        bd-equiv --old bd-capture-chaos --new bd-plugin-chaos --inputs $PWD
        -> {"verdict":"EQUAL","old_count":0,"new_count":0}  exit 0

    An empty denominator is CANNOT-EVALUATE, not agreement. The consequence is
    not cosmetic: any retirement previously "proved" with default flags rests on
    a comparison of two empty sets.
    """
    root = str(_REPO_ROOT)
    tgt = os.path.join(root, "toolchain", "bin", "bd-equiv")

    # BEHAVIOURAL RED FIRST, through the real CLI, so this test fails on the
    # shipped defect rather than only on a symbol it introduces (section 2a:
    # discriminate the exception you are hunting -- a missing grade() and a
    # broken verdict are different failures and must not look alike).
    r = subprocess.run(
        [sys.executable, tgt, "--old", "bd-capture-chaos", "--new",
         "bd-plugin-chaos", "--inputs", root, "--json", "--work", root],
        cwd=root, capture_output=True, text=True, timeout=300)
    assert r.returncode == 2, (
        "comparing two unrelated tools whose token sets are both EMPTY exited "
        "%d. Both sets empty is CANNOT-EVALUATE (2), not agreement:\n%s"
        % (r.returncode, (r.stdout + r.stderr)[-600:]))

    sys.path.insert(0, os.path.join(root, "toolchain", "bin"))
    import importlib.util
    import importlib.machinery
    spec = importlib.util.spec_from_loader(
        "_bd_equiv", importlib.machinery.SourceFileLoader("_bd_equiv", tgt))
    eq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eq)

    def rows(pairs):
        return [{"input": "i%d" % i, "old_count": a, "new_count": b,
                 "missing": [], "added": [], "equal": a == b}
                for i, (a, b) in enumerate(pairs)]

    assert hasattr(eq, "grade"), (
        "bd-equiv has no grade() -- the verdict must be derivable from rows "
        "alone so it can be asserted without spawning two subprocesses.")

    # THE DEFECT: every row measured nothing.
    assert eq.grade(rows([(0, 0), (0, 0)]), errored=False) == "CANNOT-EVALUATE", (
        "two empty token sets graded as agreement. That is a licence to retire "
        "a tool based on having measured nothing.")
    # @855 -- the half @852 MISSED. The OLD tool is the subject: if it never
    # emitted a token, "new is a superset of it" is vacuously true and still
    # exits 0 as 'safe to retire'. Caught by the desandbox verification pass
    # against the very fix meant to close this.
    assert eq.grade(rows([(0, 5)]), errored=False) == "CANNOT-EVALUATE", (
        "old emitted NOTHING and the verdict was still a retirement licence. "
        "You cannot prove a replacement covers a tool you never saw produce "
        "output.")
    # ...but NOT symmetric: old=5/new=0 is a real REGRESSION and must survive.
    _r = rows([(5, 0)])
    _r[0]["missing"] = ["tok"]
    assert eq.grade(_r, errored=False) == "REGRESSION", (
        "old=5/new=0 is the replacement losing everything -- the most important "
        "verdict this tool emits. Refusing it would destroy the instrument.")
    # A tool that CRASHED must not yield a substantive verdict either.
    assert eq.grade(rows([(3, 3)]), errored=True) == "CANNOT-EVALUATE", (
        "a tool that errored still produced a verdict; its empty output would "
        "read as agreement or as a regression, both of them artifacts.")
    # OVER-SENSITIVITY: real, non-empty, agreeing measurements must still be
    # EQUAL. A tool that can only ever say CANNOT-EVALUATE is the section 0
    # over-correction and destroys the instrument.
    assert eq.grade(rows([(5, 5)]), errored=False) == "EQUAL"
    # partial emptiness is still evaluable -- one input yielding nothing does
    # not invalidate an input that yielded tokens.
    assert eq.grade(rows([(0, 0), (4, 4)]), errored=False) == "EQUAL"


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


# ─────────────────────────────────────────────────────────────────────────────
# @857 -- SALVAGE. Three capabilities rescued from tools retired this cut. Each
# encodes a defect that recurred more than once, so the code is kept even though
# its original container is gone. These tests are what make the salvage WIRED
# rather than a second copy of the prose-only problem.

def test_salvaged_manifest_canon_reads_the_tree_not_a_fallback():
    """From bd-repin-dist. Announces PROVENANCE, because a silent fallback to
    hand-copied literals is how 6_MANIFEST_EXCLUSION_RULES.md drifted ~600
    releases (fixed @850 by making that doc derive)."""
    root = str(_REPO_ROOT)
    sys.path.insert(0, os.path.join(root, "toolchain", "bin"))
    import importlib
    sec = importlib.import_module("bdtools_sec")

    names, paths, sufs, source = sec.read_manifest_canon(root)
    assert source == "tree", (
        "canon came from the FALLBACK literals on a tree that has "
        "release_lint.py. A silent fallback is the defect this carries.")
    assert len(names) > 10 and sufs, (
        "canon read but nearly empty (%d names, %d suffixes) -- an empty "
        "denominator would make every exclusion check vacuously clean."
        % (len(names), len(sufs)))
    # NEG: a tree without release_lint must SAY fallback, not pretend.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        _, _, _, s2 = sec.read_manifest_canon(td)
        assert s2 == "fallback", "an absent canon reported as if read from tree"


def test_salvaged_stub_detector_separates_a_marker_from_prose_about_one():
    """From bd-pack. The 721 pack shipped 30-byte TODO(author) planning docs and
    the gate said clean because it was os.path.exists(); it RECURRED at 728.
    The false-positive guard matters equally: a 12KB doc that MENTIONS the
    marker is not a stub."""
    root = str(_REPO_ROOT)
    sys.path.insert(0, os.path.join(root, "toolchain", "bin"))
    import importlib
    import tempfile
    sec = importlib.import_module("bdtools_sec")

    with tempfile.TemporaryDirectory() as td:
        stub = os.path.join(td, "stub.md")
        with open(stub, "w") as fh:
            fh.write("# Backlog\n\nTODO(author)\n")
        assert sec.stub_reason(stub), "a TODO(author) placeholder read as content"

        empty = os.path.join(td, "empty.md")
        open(empty, "w").close()
        assert sec.stub_reason(empty) == "empty"

        # THE FALSE-POSITIVE GUARD: real prose that discusses the marker.
        # NOTE the shape: the marker sits on ONE line among many. The guard is
        # LINE-WISE -- it strips marker LINES and judges the remainder -- so a
        # marker buried mid-paragraph on a single very long line takes the whole
        # paragraph with it. That is a real limitation of the salvaged code, and
        # the first draft of this fixture hit it (one long line -> 9 bytes left).
        about = os.path.join(td, "about.md")
        with open(about, "w") as fh:
            fh.write("# Handoff\n\n"
                     "The 721 pack shipped TODO(author) stubs as planning docs.\n\n"
                     + "\n".join(
                         "This line describes the failure in enough detail that "
                         "a reader can actually act on it, which is what makes "
                         "the document real rather than a placeholder."
                         for _ in range(6)) + "\n")
        assert sec.stub_reason(about) is None, (
            "a substantial doc that MENTIONS the marker was flagged as a stub -- "
            "the bare `marker in text` bug that flagged a 12KB document.")

    # a real repo doc must not be flagged (over-sensitivity control)
    assert sec.stub_reason(os.path.join(root, "CHANGELOG.md")) is None


# @857: the net-tool budget, salvaged in SPIRIT from bd-mkbdsuite. Its own
# predicate was `n > budget` -- trivial; the VALUE was the policy it enforced:
# "adding a tool owes retiring one". Re-homed as a ratchet, and unlike the
# prose-only baseline this one is expected to RATCHET DOWN as retirements land.
# @858: 246 -> 239. Seven zero-coupling tools retired. This ratchet is
# meant to move DOWN as retirements land -- unlike _PROSE_ONLY_BASELINE,
# lowering it is the point, and leaving it at 246 would silently re-permit
# the growth the retirement just paid for.
# @882: 239 -> 240. bd-restart-check added, and NOTHING retired to pay for it --
# which this gate is right to make explicit. The reason: it answers a standing
# UNKNOWN that no existing tool can, and that no tool CAN answer by the usual
# means. Whether a mid-session container restart fires SessionStart was assumed
# by the hook's comments and by three test suites and established by none, and a
# hook cannot log the runs it did not make -- so the instrument has to read
# /proc/sys/kernel/random/boot_id from OUTSIDE the hook, on demand, mid-session.
# Folding that into an existing tool would have hidden a single-purpose check
# inside something with a different subject.
# The debt is real and recorded: the retirement pool (SESSION_CARRY 15.30, item
# 7a) is blocked on its over-sensitivity spec being reworked, so this is a
# deliberate raise rather than a deferred retirement. Lower it when 7a lands.
_TOOL_BUDGET = 240


def test_the_toolchain_does_not_grow_unbudgeted():
    root = str(_REPO_ROOT)
    n = len(_bd_tools(root))
    assert n > 100, "only %d tools found -- the subject collapsed" % n
    assert n <= _TOOL_BUDGET, (
        "toolchain/bin holds %d bd-* tools, over the %d budget. Adding a tool "
        "owes retiring one: wire it, retire another, or raise _TOOL_BUDGET in "
        "this cut and say why." % (n, _TOOL_BUDGET))


def test_a_scrubber_decides_text_by_content_not_by_extension():
    """@859 -- the share chain shipped raw session credentials.

    bd-wacz-scrub, bd-scrub-proof and bd-share-safe each carried their OWN
    hand-rolled TEXT_EXT allowlist: three sets, no two identical, and none
    containing ".warc". A WACZ's payload IS .warc, so a capture whose
    archive/data.warc held `Authorization: Bearer ...` and `Cookie:
    bd_session=...` was copied through untouched while the tool printed

        scrubbed WACZ -> ... (1 redactions across 2 text members, VERIFIED CLEAN)

    and exited 0. The VERIFIER shared the blind spot, so nothing downstream
    caught it. Measured: a .txt control in the same archive WAS redacted, so the
    redactor was fine -- the denominator was the defect.

    An allowlist is the wrong SHAPE for a security tool: it fails open on every
    extension nobody thought of, and the cost of that miss is a shipped
    credential. Corpus values below are ZERO-ENTROPY repeats per CLAUDE.md s7 --
    a realistic-looking token would make this file a place the secret lives, and
    gitleaks scans the whole PR range.
    """
    root = str(_REPO_ROOT)
    sys.path.insert(0, os.path.join(root, "toolchain", "bin"))
    import importlib
    sec = importlib.import_module("bdtools_sec")

    # The exact miss: a WARC payload must be scannable.
    assert sec.should_scan("archive/data.warc",
                           b"WARC/1.1\r\nAuthorization: Bearer AAAAAAAAAAAAAAAA\r\n"), (
        "a .warc member read as binary -- this is the WACZ payload, and it is "
        "where the session credentials live.")

    # ...and any extension nobody thought of.
    for name in ("x.cdxj", "x.ndjson", "x.unheard-of", "no_extension_at_all"):
        assert sec.should_scan(name, b"Cookie: bd_session=BBBBBBBBBBBBBBBB"), (
            "%s read as binary; an allowlist fails open on exactly this." % name)

    # OVER-SENSITIVITY CONTROL: real binary must still be skipped, or every
    # scrub run pays to decode video. A tool that scans everything is not the
    # fix -- deciding by CONTENT is.
    assert not sec.should_scan("shot.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00IHDR")
    assert not sec.should_scan("clip.mp4", b"\x00\x00\x00 ftypmp42\x00\x00\x00\x00")

    # Ambiguity resolves to TEXT -- fail closed. Latin-1 bytes are not valid
    # UTF-8 and carry no NUL; a secret in that encoding must still be found.
    assert sec.should_scan("odd.dat", b"caf\xe9 Cookie: bd_session=BBBBBBBBBBBBBBBB")

    # And the three tools must all route through the ONE helper -- three
    # divergent copies is how the same gap survived in all of them at once.
    for tool in ("bd-wacz-scrub", "bd-scrub-proof", "bd-share-safe"):
        src = open(os.path.join(root, "toolchain", "bin", tool),
                   errors="replace").read()
        assert "sec.should_scan(" in src, (
            "%s does not use the shared content check; a private allowlist is "
            "how this defect was born." % tool)
        assert "if ext in TEXT_EXT" not in src, (
            "%s still branches on an extension allowlist." % tool)


def test_a_file_that_collects_nothing_is_not_a_pass():
    """@860 -- RED-first TDD was silently defeatable.

    run_tests_core.py ended `sys.exit(1 if failed > 0 else 0)`. A requested file
    from which pytest collects ZERO tests prints
        Total: 0 | Passed: 0 | Failed: 0 | Skipped: 0
    and exited 0 -- and all three band runners grade on exactly that shape:
    bd-band and bd-parband test ('Failed: 0' in blob and rc == 0), bd-fullsuite
    counts the file green. A battery could be reported as "proven failing" while
    running nothing, and the first honest signal would be the box.

    SKIPS ARE NOT THIS: an all-skipped file has total > 0 and stays green.
    Gating on skips would be the section 0 over-correction -- environment skips
    are legitimate and this repo has many.
    """
    import tempfile
    root = str(_REPO_ROOT)
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "tests"))
        # pytest collects nothing from a non-Test* class
        with open(os.path.join(td, "tests", "test_zero.py"), "w") as fh:
            fh.write("class Helper:\n    def test_would_fail(self):\n        assert False\n")
        for f in ("run_tests_core.py", "run_tests.py"):
            src = os.path.join(root, f)
            if os.path.isfile(src):
                with open(src) as a, open(os.path.join(td, f), "w") as b:
                    b.write(a.read())
        r = subprocess.run([sys.executable, "run_tests.py", "tests/test_zero.py"],
                           cwd=td, capture_output=True, text=True, timeout=300)
        assert r.returncode != 0, (
            "a file collecting ZERO tests exited 0. bd-band grades that PASS, so "
            "a RED-first battery could prove nothing and still look proven.\n%s"
            % (r.stdout + r.stderr)[-500:])
        assert "UNEVALUABLE" in (r.stdout + r.stderr), (
            "it failed without saying WHY; a bare non-zero here reads as a test "
            "failure rather than 'nothing ran'.")

        # OVER-SENSITIVITY CONTROL, and bd-mutate proved it was needed: an
        # ALL-SKIPPED file must stay GREEN. total > 0 there, so it is not the
        # zero-collection case, and this repo skips heavily for environment
        # reasons -- failing on skips would make the guard unusable, which
        # section 0 counts as an equal soundness bug. Without this assertion a
        # mutant broadening the condition to `skipped == total` escaped.
        # pytest.skip() is THIS harness's idiom -- its _PytestStub.skip
        # raises _Skipped, which records SKIP and still counts toward
        # total. (unittest.skip does NOT: the harness collects nothing
        # from a TestCase class, which my first fixture proved the hard
        # way -- and which is why the pytest --collect-only survey I ran
        # first was the wrong instrument for this question.)
        with open(os.path.join(td, "tests", "test_allskip.py"), "w") as fh:
            fh.write("import pytest\n"
                     "def test_a():\n"
                     "    pytest.skip('environment')\n")
        r2 = subprocess.run([sys.executable, "run_tests.py", "tests/test_allskip.py"],
                            cwd=td, capture_output=True, text=True, timeout=300)
        assert r2.returncode == 0, (
            "an ALL-SKIPPED file failed. Skips are legitimate -- the bug is "
            "collecting NOTHING, not skipping everything.\n%s"
            % (r2.stdout + r2.stderr)[-400:])

    # OVER-SENSITIVITY CONTROL: a real suite must still pass.
    # NOT test_pk_mirrors_do_not_drift.py -- it calls pytest.fail(), which the
    # run_tests harness stubs WITHOUT a .fail attribute, so it fails under the
    # runner while passing under pytest. A pre-existing harness incompatibility,
    # unrelated to this guard, and a reminder that "the band is green" and "the
    # tests pass" are answers to different questions.
    r = subprocess.run([sys.executable, "run_tests.py", "tests/test_contracts.py"],
                       cwd=root, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (
        "a real, passing suite now fails -- the guard is over-sensitive:\n%s"
        % (r.stdout + r.stderr)[-500:])


def test_band_derive_finds_the_curated_map_and_says_so_when_it_cannot():
    """@860 -- signal 2 of 4 was dead on every checkout, silently.

    bd-band-derive unions four signals; one is the curated TOUCHED_FILE_TO_TEST
    map. find_map() looked in the work root, docs/, kb/ and the retired
    /mnt/project -- but NOT project-knowledge/, and `git log --follow` shows the
    map has lived there and only there since it was created. Zero rows loaded,
    no notice printed, so every band CLAUDE.md section 4 orders derived through
    this tool was narrower than designed. Measured on bulk_downloader/
    global_config.py: 64 suites before, 69 after.

    The SILENCE is the real defect -- a signal that vanishes without saying so
    still produces an authoritative-looking answer.
    """
    import tempfile
    root = str(_REPO_ROOT)
    tool = os.path.join(root, "toolchain", "bin", "bd-band-derive")

    sys.path.insert(0, os.path.join(root, "toolchain", "bin"))
    import importlib.util
    import importlib.machinery
    spec = importlib.util.spec_from_loader(
        "_bd_bd", importlib.machinery.SourceFileLoader("_bd_bd", tool))
    bd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bd)

    found = bd.find_map(root)
    assert found and os.path.isfile(found), (
        "the curated map was not located on a tree that has it at "
        "project-knowledge/TOUCHED_FILE_TO_TEST.md")
    assert bd.map_rows(found), "the map located but parsed to ZERO rows"

    # ...and a tree WITHOUT the map must SAY so rather than silently narrow.
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "bulk_downloader"))
        os.makedirs(os.path.join(td, "tests"))
        with open(os.path.join(root, "bulk_downloader", "__init__.py")) as a, \
             open(os.path.join(td, "bulk_downloader", "__init__.py"), "w") as b:
            b.write(a.read())
        with open(os.path.join(td, "tests", "test_x.py"), "w") as fh:
            fh.write("def test_x(): pass\n")
        r = subprocess.run([sys.executable, tool, "--work", td,
                            "--file", "bulk_downloader/__init__.py"],
                           cwd=root, capture_output=True, text=True, timeout=300)
        assert "curated map not found" in (r.stdout + r.stderr), (
            "an absent map produced no notice -- the band silently loses one of "
            "its four signals and still reads as authoritative.")


# --------------------------------------------------------------------------- #
# @868 -- a band tool must not mint a verdict for a suite it never ran         #
# --------------------------------------------------------------------------- #
# bd-parband and bd-retest both shell out to `run_tests.py <suite>`.
# run_tests_core.py:1277-1284 prints a WARN for a path it cannot find and then
# `continue`s; with every requested path missing, `filters` is empty, :1288
# takes the else branch and :1303 globs the WHOLE suite. The substituted run's
# verdict is then attributed to the path that was never run -- measured as a
# green PASS with passed=5 for a file that does not exist.
#
# The @860 guard at run_tests_core.py:1605 (`total == 0 and requested`) cannot
# fire here: the substituted full-suite run makes `total` huge. Section 0 --
# the denominator excludes the subject.
#
# EVERY TEST BELOW MUST USE subprocess + BD_LAST_BAND. Both tools resolve
# RESULTS at MODULE IMPORT (bd-parband:58-59, bd-retest:45-46), so an
# in-process import writes .bd_last_band.json into the repo root and the
# "no verdict was minted" assertion stops meaning anything.

def _band_tool(name, args, results_path, timeout=300):
    tool = os.path.join(str(_REPO_ROOT), "toolchain", "bin", name)
    env = dict(os.environ)
    env["BD_LAST_BAND"] = results_path
    return subprocess.run([sys.executable, tool] + args, cwd=str(_REPO_ROOT),
                          capture_output=True, text=True, timeout=timeout,
                          env=env)


def test_parband_refuses_a_suite_path_that_does_not_exist():
    """@868 RED -- bd-parband minted a verdict for a file that is not there.

    Measured on pristine: exit 1, the output carries `TIMEOUT
    tests/test_DOES_NOT_EXIST...` with no refusal, and the results file IS
    written carrying status "timeout" for the nonexistent suite. With a longer
    timeout and a green fallback it mints `PASS ... passed=5` instead, which is
    the worse half of the same defect.

    Assertion (c) is the item's actual claim; (a) and (b) are the mechanism.
    """
    import tempfile
    ghost = "tests/test_DOES_NOT_EXIST_parband_gate.py"
    assert not os.path.exists(os.path.join(str(_REPO_ROOT), ghost)), (
        "the fixture path exists, so this test cannot observe its own subject")

    with tempfile.TemporaryDirectory() as td:
        results = os.path.join(td, "band.json")
        r = _band_tool("bd-parband", [ghost, "--jobs", "1", "--timeout", "1"],
                       results, timeout=120)
        out = r.stdout + r.stderr

        assert r.returncode == 2, (
            "bd-parband exited %d for a suite that does not exist. A missing "
            "subject is UNKNOWN, and unknown is a third state that fails -- "
            "exit 1 says 'the suite failed', which is a verdict about a run "
            "that never happened.\n%s" % (r.returncode, out[-800:]))

        # REQUIRED, not decoration: argparse also exits 2 on a bad argv, so
        # rc==2 alone does not discriminate the condition being hunted.
        assert "BD-PARBAND UNEVALUABLE" in out, (
            "exit 2 with no marker is indistinguishable from an argparse usage "
            "error, so the test would pass on a tool broken a different "
            "way.\n%s" % out[-800:])
        assert "test_DOES_NOT_EXIST_parband_gate.py" in out, (
            "the refusal does not name the offending path; an operator banding "
            "20 suites cannot act on it.\n%s" % out[-800:])

        assert not os.path.exists(results), (
            "a band-results file was written for a suite that was never run. "
            "bd-retest reads this file back and re-grades from it, so a minted "
            "row propagates.")


def test_parband_still_runs_a_suite_that_exists():
    """OVER-SENSITIVITY (a) -- the 'refuse everything' over-correction.

    Section 0 is symmetric: a fix for 'reports clean when blind' that simply
    refuses every input passes the escape's test and destroys the tool.

    The explicit --work with a RELATIVE suite path is the point. A validator
    that resolves against os.getcwd() instead of a.work reproduces the shape of
    the defect one level down -- green from the repo root, refusing everything
    from anywhere else -- and only this input can tell the two apart.
    """
    import tempfile
    suite = "tests/test_capture_dict_shape_tripwire.py"
    assert os.path.isfile(os.path.join(str(_REPO_ROOT), suite)), (
        "%s is gone; pick another fast green suite rather than deleting this "
        "assertion -- it is the only guard on the over-correction." % suite)

    with tempfile.TemporaryDirectory() as td:
        results = os.path.join(td, "band.json")
        r = _band_tool("bd-parband",
                       [suite, "--work", str(_REPO_ROOT), "--jobs", "1",
                        "--timeout", "180"], results, timeout=300)
        out = r.stdout + r.stderr
        assert r.returncode == 0, (
            "a real, green suite was not run cleanly (exit %d). The door check "
            "must narrow the accepted set to 'exists', not to 'nothing'.\n%s"
            % (r.returncode, out[-800:]))
        assert "PASS" in out, out[-800:]
        assert os.path.exists(results), (
            "no results file for a suite that DID run -- bd-retest's input "
            "would be missing.")
        row = json.loads(open(results).read())["results"][0]
        # Deliberately not pinning the pass COUNT: it drifts with the suite.
        assert row["status"] == "pass" and (row.get("passed") or 0) > 0, row


def test_parband_still_accepts_the_leak_pair_it_exists_to_make_safe():
    """OVER-SENSITIVITY (b) -- the delegate-to-bd-bandcheck over-correction.

    bd-bandcheck.check() is the obvious 'single source of truth' move and it
    already has the right MISSING logic. It also enforces LEAK_PAIRS
    (bd-bandcheck:34-37): test_phases_195_199 + test_cut8_schedules must not
    co-band. That pair is bd-parband's WHOLE REASON TO EXIST -- its docstring
    (:5-8) says the per-process BD_HOME means the BD_INSTALL_DIR leak cannot
    cross suites. Delegating would make the tool refuse the exact case it was
    built for, and check() returns one rc, not per-reason, so selective
    delegation is not available without changing its API.

    Green today, RED the moment someone delegates. --timeout 1 because this
    tests the DOOR, not the suites; both are expected to time out.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        results = os.path.join(td, "band.json")
        r = _band_tool("bd-parband",
                       ["tests/test_phases_195_199.py",
                        "tests/test_cut8_schedules.py",
                        "--jobs", "2", "--timeout", "1"], results, timeout=120)
        out = r.stdout + r.stderr
        assert r.returncode != 2, (
            "bd-parband refused the co-band it exists to make safe.\n%s"
            % out[-800:])
        assert "UNEVALUABLE" not in out and "refusing" not in out, (
            "the leak pair was refused. Both files exist; refusing them means "
            "the door is enforcing a co-band policy that belongs to bd-band, "
            "and bd-parband has no reason to exist.\n%s" % out[-800:])


def test_retest_refuses_a_ledger_suite_that_no_longer_exists():
    """@868 RED, the consumer half -- bd-retest has the SAME fall-through.

    bd-retest:83-90 reads the results file, takes every non-pass row and calls
    run_one() on it; run_one (:47-56) drives `run_tests.py <suite>` with no
    existence check. Its grading is WORSE than bd-parband's: if the substituted
    whole-suite run comes back green, `all(o == "pass")` prints FLAKE and the
    tool removes a real failure from the ledger.

    Reachable with no hand-editing: band fails -> the test file is renamed or
    deleted as part of the fix -> bd-retest re-runs the old results file.

    Assertion (c) is the claim -- a suite that cannot be run must not receive a
    GRADE. On pristine the run times out and it prints REAL, which is a verdict
    about a broad run of the whole tree.
    """
    import tempfile
    ghost = "tests/test_DOES_NOT_EXIST_retest_gate.py"
    assert not os.path.exists(os.path.join(str(_REPO_ROOT), ghost))

    with tempfile.TemporaryDirectory() as td:
        results = os.path.join(td, "band.json")
        with open(results, "w") as fh:
            json.dump({"work": str(_REPO_ROOT), "timeout": 1,
                       "results": [{"suite": ghost, "status": "fail",
                                    "failed": 1, "passed": 0, "secs": 0.1,
                                    "tail": ""}]}, fh)
        r = _band_tool("bd-retest",
                       ["--retries", "1", "--timeout", "1",
                        "--work", str(_REPO_ROOT)], results, timeout=120)
        out = r.stdout + r.stderr

        assert r.returncode == 2, (
            "bd-retest exited %d for a ledger row whose suite is gone. It "
            "cannot be retested, so there is no verdict to give.\n%s"
            % (r.returncode, out[-800:]))
        assert "BD-RETEST UNEVALUABLE" in out, (
            "no discriminating marker -- exit 2 is also what an absent results "
            "file produces (bd-retest:78-82), so rc alone cannot tell the two "
            "apart.\n%s" % out[-800:])
        assert "test_DOES_NOT_EXIST_retest_gate.py" in out, out[-800:]
        assert not re.search(r"\b(REAL|FLAKE|FLAKY)\b", out), (
            "bd-retest GRADED a suite it could not run. FLAKE is the dangerous "
            "one: it deletes a real failure from the ledger on the strength of "
            "a whole-tree run that was never asked for.\n%s" % out[-800:])


def test_retest_still_retests_a_suite_that_exists():
    """OVER-SENSITIVITY -- bd-retest must still do its job.

    A green suite in the ledger as 'fail' is exactly the flake case the tool
    exists for; it must come back FLAKE and exit 0, not get refused alongside
    the missing ones.
    """
    import tempfile
    suite = "tests/test_capture_dict_shape_tripwire.py"
    assert os.path.isfile(os.path.join(str(_REPO_ROOT), suite))

    with tempfile.TemporaryDirectory() as td:
        results = os.path.join(td, "band.json")
        with open(results, "w") as fh:
            json.dump({"work": str(_REPO_ROOT), "timeout": 180,
                       "results": [{"suite": suite, "status": "fail",
                                    "failed": 1, "passed": 0, "secs": 0.1,
                                    "tail": ""}]}, fh)
        r = _band_tool("bd-retest",
                       ["--retries", "1", "--timeout", "180",
                        "--work", str(_REPO_ROOT)], results, timeout=300)
        out = r.stdout + r.stderr
        assert r.returncode == 0, (
            "a real suite that passes on retry did not grade clean (exit %d)."
            "\n%s" % (r.returncode, out[-800:]))
        assert "FLAKE" in out, out[-800:]
        assert "UNEVALUABLE" not in out, out[-800:]


def test_the_band_pair_shares_one_existence_check():
    """The pair contract is stated in BOTH headers: bd-parband:57-58 and
    bd-retest:43-44 say 'Edit them together or the pair breaks.'

    Two copies of this predicate is how the consumer half stayed open while
    the producer half was fixed, so the check lives in bdtools_sec and both
    tools call it BY NAME. Asserting the call sites, not just the helper, is
    the point: a helper nobody calls is the section 0 shape again.
    """
    root = str(_REPO_ROOT)
    sys.path.insert(0, os.path.join(root, "toolchain", "bin"))
    import importlib
    sec = importlib.import_module("bdtools_sec")

    assert hasattr(sec, "missing_suite_reason"), (
        "bdtools_sec has no missing_suite_reason -- the shared library is "
        "where this belongs, so the pair cannot drift apart again.")

    # BOTH directions. A predicate that always answers the same way is not a
    # check: one that never refuses is the original defect, one that always
    # refuses is the over-correction.
    assert sec.missing_suite_reason(root, "tests/test_capture_dict_shape_tripwire.py") is None
    assert sec.missing_suite_reason(root, "tests/test_NOT_A_REAL_SUITE_xyz.py")
    # Resolution is against `work`, NOT cwd -- the whole point of the argument.
    assert sec.missing_suite_reason(os.path.join(root, "tests"), "test_toolchain_534.py") is None
    assert sec.missing_suite_reason("/nonexistent-root-xyz", "tests/test_toolchain_534.py")

    for name in ("bd-parband", "bd-retest"):
        src = open(os.path.join(root, "toolchain", "bin", name),
                   errors="replace").read()
        assert "missing_suite_reason" in src, (
            "%s does not call the shared check, so the pair has forked again "
            "-- which is how bd-retest kept the fall-through after bd-parband "
            "lost it." % name)


def _derive(args):
    tool = os.path.join(str(_REPO_ROOT), "toolchain", "bin", "bd-band-derive")
    return subprocess.run([sys.executable, tool, "--work", str(_REPO_ROOT)] + args,
                          cwd=str(_REPO_ROOT), capture_output=True, text=True,
                          timeout=600)


def test_band_derive_reaches_the_pk_mirror_gate():
    """@868 RED -- tests/test_pk_mirrors_do_not_drift.py was in NO band, ever.

    bd-band-derive unions its signals over a file's name, its imports, the
    curated map and declared coupling. The PK-mirror gate answers to none of
    them: it imports only hashlib/pathlib/pytest, its stem matches no source
    file, and `grep -n 'pk_mirror|project-knowledge'
    project-knowledge/TOUCHED_FILE_TO_TEST.md` returns nothing. So for ANY
    mirrored file the deriver returned a band the gate was not in.

    Measured on pristine: `--file toolchain/bin/bdtools_sec.py` -> 13 suites,
    `--file toolchain/bin/bd-scrub-proof` -> 3 suites, gate absent from both.
    Three mirrors sat drifted across v3.66.847-849 with the gate red on main
    the whole time, because every band in between was derived from a changed
    module.
    """
    gate = "tests/test_pk_mirrors_do_not_drift.py"

    def band_of(target):
        """The FULL band, via --json.

        NOT the human listing: bd-band-derive:730 prints `band[:24]`, so a
        membership assertion against stdout is answered by a 24-line window,
        not by the band. The first draft of this test asserted `gate not in
        r.stdout` for app.py -- and a mutant that made SIGNAL 8 match
        everything (band 495) ESCAPED it, because the gate sorted past the
        truncation. A denominator that excludes its subject reports OK; that is
        the defect this whole cut is about, reproduced inside its own test.
        """
        r = _derive(["--file", target, "--json"])
        assert r.returncode == 0, (target, (r.stdout + r.stderr)[-900:])
        return json.loads(r.stdout)["band"]

    for target in ("toolchain/bin/bdtools_sec.py",
                   "toolchain/bin/bd-scrub-proof",
                   "project-knowledge/bd-scrub-proof"):
        assert gate in band_of(target), (
            "%s is a PK-mirrored file and its band omits %s. Editing it drifts "
            "the mirror, the gate is sha-gated, and no other signal can reach "
            "it." % (target, gate))

    # OVER-SENSITIVITY: the signal must not degrade into "band everything".
    # MEASURED at @868: zero of the 278 tracked files sharing a PK top-level
    # basename live under bulk_downloader/, so this assertion is meaningful
    # today and catches a future rule that becomes a prefix match on the repo.
    #
    # MEMBERSHIP, not band SIZE. app.py's real band is 494 suites and the
    # match-everything mutant makes it 495 -- signal 8 contributes exactly one
    # entry, so no size threshold can discriminate here, and pinning 494 would
    # pin a number that drifts with every new test file. The named-suite
    # assertion is the one with the evidence: verified RED under the mutant and
    # green on the real code.
    assert gate not in band_of("bulk_downloader/app.py"), (
        "bulk_downloader/app.py has no PK mirror, so banding the mirror gate "
        "on it means the signal matched something other than mirroring.")

    # The degraded case must SAY so, and must say so in --emit too. The @860
    # curated-map notice is gated on `not a.json and not a.emit`, and --emit is
    # the mode CLAUDE.md section 4 tells you to use -- a signal that vanishes
    # silently there hands back a narrower band that still looks authoritative.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "tests"))
        os.makedirs(os.path.join(td, "bulk_downloader"))
        with open(os.path.join(td, "bulk_downloader", "__init__.py"), "w") as fh:
            fh.write('__version__ = "0.0.0"\n')
        with open(os.path.join(td, "tests", "test_x.py"), "w") as fh:
            fh.write("def test_x(): pass\n")
        tool = os.path.join(str(_REPO_ROOT), "toolchain", "bin", "bd-band-derive")
        r = subprocess.run([sys.executable, tool, "--work", td, "--emit",
                            "--file", "bulk_downloader/__init__.py"],
                           cwd=str(_REPO_ROOT), capture_output=True, text=True,
                           timeout=600)
        assert "project-knowledge/ not found" in (r.stdout + r.stderr), (
            "a tree with no project-knowledge/ dropped signal 8 without a "
            "word, in --emit -- the exact silence @860 exists to "
            "prevent.\n%s" % (r.stdout + r.stderr)[-900:])


# --------------------------------------------------------------------------- #
# @871 -- a unit that could not be evaluated must not land in the success bucket
# --------------------------------------------------------------------------- #
# Four tools, one defect wearing four hats. Each already HAS a correct AGGREGATE
# CANNOT-EVALUATE concept (sec.EXIT_CANNOT_EVALUATE, and three of them mint it
# correctly somewhere). In all four the failure is at the UNIT level: a per-unit
# "I could not evaluate this" is folded into the SUCCESS bucket, and the
# aggregate then reports honestly over a denominator the unevaluable unit has
# quietly left.
#
#   bd-fullsuite   one env-looking LINE excuses every real failure in the FILE
#   bd-opv         a check that RAISED is graded SKIP, the benign bucket
#   bd-equiv       a compared tool that exits non-zero reads as "produced nothing"
#   bd-env-report  version UNKNOWN with a commit present reports FRESH
#
# The invariant is stated once, table-driven, because that is what stops the
# NEXT toolchain addition acquiring the same shape. Four bespoke tests would
# not. bd-env-report-check's rows live in tests/test_env_report_freshness.py
# beside its existing siblings.

def _fullsuite_tree(td, files):
    """A synthetic work tree bd-fullsuite can drive.

    SYNTHETIC ON PURPOSE. The obvious real anchor is test_v3_43_80_modules,
    which CLAUDE.md section 5 says false-fails without GTK typelibs -- but in
    THIS container it passes 49/49, so a control anchored on it would prove
    nothing here and something else on the box.

    `files` maps a test filename to the exact stdout its run should produce.
    The stub run_tests.py replays that text and exits 1 if any FAIL row is in
    it, which is the contract bd-fullsuite actually reads.
    """
    os.makedirs(os.path.join(td, "tests"), exist_ok=True)
    for name, out in files.items():
        with open(os.path.join(td, "tests", name), "w") as fh:
            fh.write("# fixture\n")
        with open(os.path.join(td, "tests", name + ".out"), "w") as fh:
            fh.write(out)
    with open(os.path.join(td, "run_tests.py"), "w") as fh:
        fh.write(
            "import sys, os\n"
            "t = sys.argv[1]\n"
            "p = os.path.join(os.path.dirname(os.path.abspath(__file__)), t + '.out')\n"
            "out = open(p).read()\n"
            "sys.stdout.write(out)\n"
            # Hygiene, not a diagnosis: bd-fullsuite's fork child dup2s fd 1
            # to a file and leaves via os._exit(), which does NOT flush
            # Python's buffers. A one-variable test showed this stub behaves
            # identically here with and without the flush, so it is NOT the
            # reason the end-to-end fork rows failed in CI -- that cause is
            # still unidentified and the rows were removed rather than guessed
            # at. Kept because relying on buffered stdout surviving os._exit
            # is wrong regardless.
            "sys.stdout.flush()\n"
            "sys.exit(1 if '  FAIL  ' in out else 0)\n")
    return td


_REAL_BLOCK = ("  FAIL  test_prune_counts\n"
               "    E   AssertionError: prune should report 1 removed, got 7\n")
_ENV_BLOCK = ("  FAIL  test_tray_imports\n"
              "    E   ValueError: Namespace Gtk not available\n")


def _fullsuite(td, extra, state):
    tool = os.path.join(str(_REPO_ROOT), "toolchain", "bin", "bd-fullsuite")
    env = dict(os.environ)
    env["BD_FULLSUITE_STATE"] = state
    return subprocess.run([sys.executable, tool, "--work", td, "--jobs", "1"] + extra,
                          cwd=str(_REPO_ROOT), capture_output=True, text=True,
                          timeout=300, env=env)


def test_an_env_signal_does_not_excuse_a_real_failure_in_the_same_file():
    """@871 RED -- classify() regexes the WHOLE file output, first-match-wins.

    bd-fullsuite:127-131 searches ENV_PATTERNS across the entire combined
    stdout+stderr of a test FILE; :436 then marks the whole file "env" and :728
    computes the exit code from `real` only. So ONE env-looking line anywhere
    suppresses every genuine failure in that file.

    Measured on pristine, single variable changed (presence of the Gtk string):
      mixed   -> "ENV tests/test_mixed.py (ENV_GTK)" ... "REAL failed 0" ...
                 GREEN, exit=0   -- while the output contained a real
                 AssertionError and "Failed: 2"
      control -> "FAIL tests/test_realonly.py (REAL)" ... exit=1

    BOTH DISPATCH PATHS. run_one (:428/:436, spawn, the default) and
    _parse_worker_line (:225/:226, --fork) derive kind+status independently, so
    a fix landing in one leaves the other defective -- which is the
    fix-reproduces-the-shape trap. The --fork row is what forbids it.
    """
    import tempfile
    mixed = ("Running suite\n" + _REAL_BLOCK + _ENV_BLOCK +
             "Total: 2 | Passed: 0 | Failed: 2 | Skipped: 0\n")
    for extra in ([],):
        with tempfile.TemporaryDirectory() as td, \
             tempfile.TemporaryDirectory() as state:
            _fullsuite_tree(td, {"test_mixed.py": mixed})
            r = _fullsuite(td, ["--only", "mixed"] + extra, state)
            out = r.stdout + r.stderr
            label = "spawn" if not extra else "fork"
            assert r.returncode != 0, (
                "[%s] a file carrying a REAL AssertionError alongside one Gtk "
                "line exited 0. One env-looking line excused the whole file, "
                "and the operator is told GREEN.\n%s" % (label, out[-1200:]))
            assert "AssertionError" in out or "prune should report" in out, (
                "[%s] the real failure never reached the operator; only the "
                "env classification did.\n%s" % (label, out[-1200:]))


def test_a_file_whose_failures_are_all_environmental_is_still_excused():
    """OVER-SENSITIVITY -- the fix must not turn every env file into a failure.

    Without this the tool is unusable in any container lacking GTK typelibs or
    a browser cache, which is most of them. A tool that can only refuse is not
    an instrument. Green before and after by design.
    """
    import tempfile
    env_only = ("Running suite\n" + _ENV_BLOCK +
                "Total: 1 | Passed: 0 | Failed: 1 | Skipped: 0\n")
    passing = ("  PASS  test_ok\n"
               "Total: 1 | Passed: 1 | Failed: 0 | Skipped: 0\n")
    for extra in ([],):
        with tempfile.TemporaryDirectory() as td, \
             tempfile.TemporaryDirectory() as state:
            # a real PASS elsewhere in the run, so the zero-pass guard below is
            # not what is being exercised here
            _fullsuite_tree(td, {"test_envonly.py": env_only,
                                 "test_good.py": passing})
            r = _fullsuite(td, extra, state)
            out = r.stdout + r.stderr
            label = "spawn" if not extra else "fork"
            assert r.returncode == 0, (
                "[%s] a purely environmental failure was reported as real. The "
                "segmentation must narrow what 'env' covers, not delete "
                "it.\n%s" % (label, out[-1200:]))


def test_a_fullsuite_run_in_which_nothing_passed_is_not_green():
    """@871 RED -- bd-fullsuite has no zero-pass guard; bd-opv:1400 has one.

    Measured on pristine: "=== 1 files | passed 0 | REAL failed 0 | ... env 1"
    then "GREEN", exit=0. A run in which every collected file classified env
    verified nothing at all, and exit 0 says otherwise. That bd-opv already
    carries exactly this guard is the proof of the intended semantics.
    """
    import tempfile
    env_only = ("Running suite\n" + _ENV_BLOCK +
                "Total: 1 | Passed: 0 | Failed: 1 | Skipped: 0\n")
    with tempfile.TemporaryDirectory() as td, \
         tempfile.TemporaryDirectory() as state:
        _fullsuite_tree(td, {"test_a.py": env_only, "test_b.py": env_only})
        r = _fullsuite(td, [], state)
        out = r.stdout + r.stderr
        assert r.returncode != 0, (
            "every file classified env, zero tests passed, and the run exited "
            "0 GREEN. Nothing was verified.\n%s" % out[-1200:])
        assert "CANNOT-EVALUATE" in out or "EMPTY" in out, (
            "the refusal must name itself as CANNOT-EVALUATE rather than as a "
            "failure -- nothing failed, nothing was measured.\n%s" % out[-1200:])


def _load_opv():
    import importlib.machinery
    import importlib.util
    os.environ["_BD_OPV_REEXEC"] = "1"   # bd-opv:65-70 re-execs itself otherwise
    tool = os.path.join(str(_REPO_ROOT), "toolchain", "bin", "bd-opv")
    spec = importlib.util.spec_from_loader(
        "_bd_opv", importlib.machinery.SourceFileLoader("_bd_opv", tool))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_an_opv_check_that_crashes_is_not_graded_as_a_missing_precondition():
    """@871 RED -- bd-opv:1368-1370 grades a RAISED check as SKIP.

    SKIP is the benign bucket: the tool's own docstring defines it as
    "precondition missing (e.g. a setting/route absent in this build)". A check
    that died is not that. It feeds nothing but n_skip, so one unrelated PASS
    clears the CANNOT-EVALUATE guard at :1400 and the run exits 0.

    Measured on pristine with a substituted REGISTRY:
      SKIP  OPV-BOOM     check raised: ZeroDivisionError: the check itself is broken
      PASS  OPV-GOOD     a real pass
      1 PASS  0 FAIL  1 SKIP  0 GATED   exit=0

    Indistinguishable in the summary from the genuine OPV-QR row
    ("qr/pyzbar/PIL unavailable"), which is a real absent precondition.
    """
    opv = _load_opv()

    def boom():
        raise ZeroDivisionError("the check itself is broken")

    def good():
        return opv.PASS, "a real pass"

    saved = opv.REGISTRY
    try:
        opv.REGISTRY = [("OPV-BOOM", "core", boom), ("OPV-GOOD", "core", good)]
        rc = opv.main([])
        assert rc == 2, (
            "a check that RAISED left the run at exit %r. A crashed check is "
            "not an absent precondition, and folding it into SKIP means a "
            "broken check reads as a benign one." % (rc,))

        # OVER-SENSITIVITY: a GENUINE precondition skip must stay benign and
        # keep the run at 0. The live anchor is real at this commit -- the full
        # run is 17 PASS / 0 FAIL / 7 SKIP / exit 0. If the fix turns any SKIP
        # into a refusal, bd-opv can only ever refuse in a container.
        def absent():
            return opv.SKIP, "qr/pyzbar/PIL unavailable (No module named 'qrcode')"

        opv.REGISTRY = [("OPV-ABSENT", "core", absent), ("OPV-GOOD", "core", good)]
        rc = opv.main([])
        assert rc == 0, (
            "a genuine precondition SKIP alongside a real PASS exited %r. Only "
            "a check that RAISED may take the new state." % (rc,))
    finally:
        opv.REGISTRY = saved


def test_a_compared_tool_that_exits_non_zero_is_a_crash_not_silence():
    """@871 RED -- bd-equiv:62-68 discards cp.returncode entirely.

    ERROR_SENTINEL (:46, commented "@852: a crash is not agreement") fires only
    on a HARNESS-level exception -- timeout, OSError -- never on the compared
    subprocess exiting non-zero. A tool that dies emits a traceback with no
    extractable tokens, so it reads as "produced nothing", and new >= nothing is
    vacuously true.

    Measured on pristine with an old tool that raises on one of two inputs:
      verdict SUPERSET, "errored": false, exit=0
    printed as "Safe to retire IF 'more' is safe." This is the tool that
    authorizes deletion.
    """
    import tempfile
    tool = os.path.join(str(_REPO_ROOT), "toolchain", "bin", "bd-equiv")
    with tempfile.TemporaryDirectory() as td:
        old = os.path.join(td, "old_t.py")
        new = os.path.join(td, "new_t.py")
        with open(old, "w") as fh:
            fh.write("import sys\n"
                     "if len(sys.argv) > 1 and sys.argv[1] == 'B':\n"
                     "    raise RuntimeError('this tool is broken')\n"
                     "print('tests/test_alpha.py')\nprint('tests/test_beta.py')\n")
        with open(new, "w") as fh:
            fh.write("print('tests/test_alpha.py')\nprint('tests/test_beta.py')\n"
                     "print('tests/test_gamma.py')\n")
        r = subprocess.run([sys.executable, tool, "--old", old, "--new", new,
                            "--inputs", "A", "B", "--work", td, "--json"],
                           cwd=str(_REPO_ROOT), capture_output=True, text=True,
                           timeout=180)
        out = r.stdout + r.stderr
        assert r.returncode == 2, (
            "the OLD tool crashed on one input and bd-equiv exited %d, "
            "certifying the replacement as a SUPERSET of a traceback. This is "
            "the tool that authorizes deletion.\n%s"
            % (r.returncode, out[-1200:]))
        assert "CANNOT-EVALUATE" in out, out[-1200:]

        # OVER-SENSITIVITY: @852 already holds that PARTIAL emptiness stays
        # evaluable -- "a tool that can only refuse is not an instrument". A
        # CLEAN tool that legitimately prints nothing on one input must still
        # grade. The new signal is the EXIT CODE, never an empty token set.
        quiet = os.path.join(td, "quiet_t.py")
        with open(quiet, "w") as fh:
            fh.write("import sys\n"
                     "if len(sys.argv) > 1 and sys.argv[1] == 'B':\n"
                     "    sys.exit(0)\n"
                     "print('tests/test_alpha.py')\n")
        r = subprocess.run([sys.executable, tool, "--old", quiet, "--new", new,
                            "--inputs", "A", "B", "--work", td, "--json"],
                           cwd=str(_REPO_ROOT), capture_output=True, text=True,
                           timeout=180)
        assert r.returncode == 0, (
            "a clean tool that printed nothing on one input was refused. "
            "Silence at exit 0 is data; a non-zero exit is the crash.\n%s"
            % (r.stdout + r.stderr)[-1200:])


def test_an_unattributable_failure_is_unknown_not_excused_as_env():
    """@871 -- the fail-CLOSED direction, which a mutation battery proved was
    asserted in a comment and enforced by nothing.

    classify_run's segmentation is ITSELF a denominator. When a file exits
    non-zero and emits NO per-test markers, there is no failing block to
    classify -- and the tempting answer is to fall back to scanning the whole
    output, which is the original defect. The chosen answer is UNKNOWN: not
    excused, not passed. A mutant returning ("ENV_GTK", "env") on that branch
    ESCAPED the rest of this battery, because every other fixture here produces
    at least one marker and the zero-pass guard covered the rest.

    THE ENV-LOOKING STRING IN THE FIXTURE IS LOAD-BEARING. Without it a
    fall-back-to-whole-output implementation would classify REAL and the run
    would go non-zero anyway -- the test would pass against the defect. It is
    there so the only way to reach a non-zero exit is to refuse to classify.

    Real instance: tests/test_e2e_smoke.py collects ZERO tests and exits 2 with
    "BD-RUNNER UNEVALUABLE", no markers, empty tail. It was reported to the
    operator as a REAL code failure with no explanation attached.
    """
    import tempfile
    # non-zero exit, an env-looking line, and NOT ONE per-test marker
    noattr = ("BD-RUNNER UNEVALUABLE: 1 file(s) requested, ZERO tests collected.\n"
              "  ValueError: Namespace Gtk not available\n"
              "Total: 0 | Passed: 0 | Failed: 0 | Skipped: 0\n")
    passing = ("  PASS  test_ok\n"
               "Total: 1 | Passed: 1 | Failed: 0 | Skipped: 0\n")
    assert "  FAIL  " not in noattr, "the fixture must carry no marker at all"

    for extra in ([],):
        with tempfile.TemporaryDirectory() as td, \
             tempfile.TemporaryDirectory() as state:
            # the passing file keeps P > 0 so the zero-pass guard is NOT what
            # produces the non-zero exit -- otherwise this test would pass for
            # the wrong reason.
            _fullsuite_tree(td, {"test_noattr.py": noattr, "test_good.py": passing})
            # the stub exits 0 when no FAIL row is present, so force non-zero
            with open(os.path.join(td, "run_tests.py"), "w") as fh:
                fh.write(
                    "import sys, os\n"
                    "t = sys.argv[1]\n"
                    "p = os.path.join(os.path.dirname(os.path.abspath(__file__)), t + '.out')\n"
                    "out = open(p).read()\n"
                    "sys.stdout.write(out)\n"
                    "sys.stdout.flush()\n"   # see _fullsuite_tree: os._exit
                    "sys.exit(2 if 'UNEVALUABLE' in out else 0)\n")
            r = _fullsuite(td, extra, state)
            out = r.stdout + r.stderr
            label = "spawn" if not extra else "fork"
            assert r.returncode != 0, (
                "[%s] a file that exited non-zero with NO attributable failing "
                "test was excused and the run went green. Unattributable must "
                "fail CLOSED -- if the marker format ever stops matching, the "
                "fallback must not excuse the whole suite.\n%s"
                % (label, out[-1200:]))
            assert "UNKNOWN" in out, (
                "[%s] the file was not reported as UNKNOWN. It is neither a "
                "code defect nor an environment excuse, and calling it either "
                "misinforms the operator.\n%s" % (label, out[-1200:]))


def test_both_fullsuite_dispatch_paths_share_one_classifier():
    """@871 -- the property the end-to-end --fork rows were there to protect,
    asserted directly instead of through the fork worker.

    THE DEFECT THIS FORBIDS: run_one (spawn, the default) and
    _parse_worker_line (--fork) each derived kind+status independently, so a
    fix landing in one leaves the other defective. --fork is opt-in, so a
    contributor exercising only the default path would never see it.

    WHY NOT END-TO-END. The --fork rows passed here and failed in CI, where the
    fork worker produced no output at all and every file classified UNKNOWN. I
    could not reproduce that environment, and a one-variable test disproved the
    obvious explanation (buffered stdout lost to os._exit -- identical results
    with and without a flush). Rather than guess at a cause, the rows were
    removed and the property is asserted where it actually lives: one function,
    called from both sites. An end-to-end row whose failure mode I cannot
    explain is not evidence about bd-fullsuite -- it is a harness defect
    wearing the subject's shape, which is exactly what CLAUDE.md 2a warns is
    indistinguishable from the real thing.

    The unidentified CI behaviour is recorded in the CHANGELOG as open.
    """
    src = open(os.path.join(str(_REPO_ROOT), "toolchain", "bin", "bd-fullsuite"),
               errors="replace").read()

    # exactly one definition, and both dispatch sites call it
    assert src.count("def classify_run(") == 1, (
        "classify_run must be defined once -- two definitions is the "
        "duplication this replaced.")
    # Anchored on the ASSIGNMENT, not the bare name: `def classify_run(out,
    # rc, failed):` contains the call text too, so counting that substring put
    # the DEFINITION inside the denominator and made the expected count 3.
    # A denominator including its own subject, in the assertion about
    # denominators.
    n = src.count("kind, status = classify_run(out, rc, failed)")
    assert n == 2, (
        "expected exactly two call sites (run_one and _parse_worker_line); "
        "found %d. If a dispatch path stopped calling the shared classifier, "
        "the two paths have forked apart again." % n)

    # and neither site re-derives status locally any more
    assert 'else (\n        "env" if kind.startswith("ENV") else "fail")' not in src, (
        "a dispatch path is deriving status from a whole-file classify() "
        "again -- that is the original defect.")

    # BEHAVIOUR, not just wiring: the classifier itself, all four outcomes.
    import importlib.machinery
    import importlib.util
    spec = importlib.util.spec_from_loader(
        "_bd_fs", importlib.machinery.SourceFileLoader(
            "_bd_fs", os.path.join(str(_REPO_ROOT), "toolchain", "bin",
                                   "bd-fullsuite")))
    fs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fs)

    real = "  FAIL  t_a\n    E   AssertionError: prune should report 1 removed\n"
    env = "  FAIL  t_b\n    E   ValueError: Namespace Gtk not available\n"
    ok = "  PASS  t_c\n"

    assert fs.classify_run(ok, 0, 0) == ("OK", "pass")
    # one env line must NOT excuse the real failure beside it
    assert fs.classify_run(real + env, 1, 2)[1] == "fail", (
        "a real failure alongside an env failure was excused -- the defect.")
    assert fs.classify_run(env + real, 1, 2)[1] == "fail", (
        "order-dependent: first-match-wins is exactly what was removed.")
    # all-environmental is still excused
    assert fs.classify_run(env, 1, 1)[1] == "env"
    assert fs.classify_run(env + env, 1, 2)[1] == "env"
    # rc != 0 with nothing attributable fails CLOSED, even carrying an env string
    assert fs.classify_run("Namespace Gtk not available\n", 2, 0) == (
        "UNKNOWN", "unknown"), (
        "an unattributable non-zero exit was excused as env. If the marker "
        "format ever stops matching, this fallback must not excuse the suite.")


# --------------------------------------------------------------------------- #
# @876 -- the band tool pinned its suites at a browser pool that does not exist #
# --------------------------------------------------------------------------- #

def test_no_band_tool_pins_the_browser_pool_or_pythonpath():
    """@876 -- bd-band's band_env() hardcoded a zip-era cache path.

    MEASURED, one variable at a time, on tests/test_v3_66_252_dom_excerpt.py:

        baseline                    Total: 4 | Passed: 4 | Skipped: 0
        +PLAYWRIGHT_BROWSERS_PATH   Total: 4 | Passed: 1 | Skipped: 3
        +PYTHONPATH                 Total: 4 | Passed: 4 | Skipped: 0

    Three of four assertions vanish, and because they SELF-SKIP on an
    unlaunchable browser the summary still says `Failed: 0` with rc 0 -- which
    is exactly bd-band's own pass predicate (`ok = "Failed: 0" in blob and
    r.returncode == 0`). So the tool CLAUDE.md section 4 mandates for deriving
    every band reported a suite green over assertions that never executed.
    Measured blast radius: 8 of 1212 tracked test files self-skip that way.

    Only the browser path causes it; PYTHONPATH is inert here. Both are removed
    anyway -- replacing PYTHONPATH strips the work tree from sys.path, which is
    the documented route_map_snapshot.py footgun -- but the CHANGELOG says which
    of the two was measured to bite, rather than claiming both.

    THE FIX ALREADY EXISTED IN A SIBLING. toolchain/bin/bd-cut carries the
    ported version WITH its rationale ("hardcoding it pointed the band at a
    browser pool that does not exist"); bd-cut was ported at v3.66.855 and
    bd-band was not. This asserts over BOTH so they cannot drift apart again.
    """
    root = str(_REPO_ROOT)
    offenders = []
    for name in ("bd-band", "bd-cut", "bd-bandcheck", "bd-parband", "bd-fullsuite"):
        p = os.path.join(root, "toolchain", "bin", name)
        if not os.path.isfile(p):
            continue
        src = open(p, errors="replace").read()
        for m in re.finditer(
                r'^\s*(?:env\.update\(|\s+)?(PLAYWRIGHT_BROWSERS_PATH|PYTHONPATH)\s*=\s*["\'][^"\']+["\']',
                src, re.M):
            line = src[:m.start()].count("\n") + 1
            offenders.append("%s:%d  %s" % (name, line, m.group(0).strip()))
    assert not offenders, (
        "these band tools ASSIGN a literal browser pool or PYTHONPATH into the "
        "suite env. Both must be INHERITED: an absent PLAYWRIGHT_BROWSERS_PATH "
        "is playwright's own default, which is correct, while a wrong one makes "
        "browser suites self-skip and still report Failed: 0.\n  %s"
        % "\n  ".join(offenders))


def test_bandcheck_defaults_to_a_work_root_that_exists():
    """@876 -- `--tree/--work` defaulted to the retired /home/claude/work.

    A bare `bd-bandcheck <targets>` therefore reported EVERY target MISSING.
    It fails CLOSED (exit 1), not clean -- I previously reported this as
    "exits 0 anyway" and that was WRONG: the measurement piped through `tail`,
    so the exit code read was tail's. CLAUDE.md section 5's pipe trap.

    Failing closed is much better than failing open, but the DIAGNOSIS is still
    wrong: the operator is told "Typo?" for a path that is fine, so they fix
    spelling that is not broken.
    """
    tool = os.path.join(str(_REPO_ROOT), "toolchain", "bin", "bd-bandcheck")
    src = open(tool, errors="replace").read()
    # Assert over CODE, not over the file's text. The first draft of this
    # forbade the literal anywhere and failed on the COMMENT that explains the
    # removal -- the same prose-vs-code conflation that made the queue entry
    # say bd-band had "3 occurrences" when two were docstring lines and only
    # one was a code position. A denominator that includes its own explanation
    # is not the subject.
    assert not re.search(r'default\s*=\s*["\']/home/claude', src), (
        "bd-bandcheck still DEFAULTS its work root to the retired sandbox path")
    r = subprocess.run([sys.executable, tool, "tests/test_toolchain_534.py"],
                       cwd=str(_REPO_ROOT), capture_output=True, text=True,
                       timeout=120)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "MISSING" not in out, (
        "a bare bd-bandcheck on a real, present band file still could not find "
        "it (exit %d):\n%s" % (r.returncode, out[-600:]))


def test_bandcheck_still_reports_a_leak_pair_it_could_not_resolve():
    """@876 -- the MISSING branch `continue`d before `names.add(base)`.

    So the leak-pair detector ran over an incomplete set, and a bare invocation
    of the test_phases_195_199 + test_cut8_schedules pair reported two MISSING
    lines and NO leak warning. Same exit code, different diagnosis -- and the
    diagnosis is the whole product here. The operator fixes the paths and then
    bands the pair that leaks BD_INSTALL_DIR.

    Asserted against a NONEXISTENT tree on purpose, so the resolution failure is
    guaranteed and the only thing under test is whether the leak survives it.
    """
    tool = os.path.join(str(_REPO_ROOT), "toolchain", "bin", "bd-bandcheck")
    r = subprocess.run([sys.executable, tool, "--work", "/nonexistent-tree-xyz",
                        "tests/test_phases_195_199.py", "tests/test_cut8_schedules.py"],
                       cwd=str(_REPO_ROOT), capture_output=True, text=True,
                       timeout=120)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "LEAK CO-BAND" in out, (
        "two unresolvable paths suppressed the leak-pair warning entirely. The "
        "pair is a hazard about what you are ABOUT TO BAND, not about what "
        "resolved.\n%s" % out[-600:])


# --------------------------------------------------------------------------- #
# @878 -- the operator shell tools ran against a dead sandbox and exited 0      #
# --------------------------------------------------------------------------- #
# Item 8c. None of these is on an automated lane -- measured, invocation-shaped
# rather than by substring: zero hits across capture.sh, scripts/, .github/ and
# install_linux.sh for bd, bd-status, bd-reindex, bd-freshest, bd-since. (A bare
# `bd` grep returns 660 files because it is a substring of everything, which is
# the denominator trap in miniature.) So the cost is an operator misled, not a
# gate fooled -- but bd-reindex WRITES the artifacts the gates then check.

def _sh(tool, *args, env_extra=None, cwd=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    p = os.path.join(str(_REPO_ROOT), "toolchain", "bin", tool)
    return subprocess.run(["bash", p, *args], cwd=cwd or str(_REPO_ROOT),
                          capture_output=True, text=True, timeout=120, env=env)


def test_bd_reindex_refuses_an_interpreter_that_cannot_import_the_deps():
    """@878 -- the sharpest of the four, because it WRITES gate subjects.

    bd-reindex resolved its interpreter as
        VENV_PY="${BD_VENV_PY:-/home/claude/work/venv/bin/python}"
        [ -x "$VENV_PY" ] || VENV_PY="$(command -v python3)"
    On any git checkout the default does not exist, so it fell through to
    `python3` -- which CLAUDE.md section 5 records as 3.11 WITHOUT the project
    dependencies. Confirmed at this commit: 3.11.15, `import pytest` ->
    ModuleNotFoundError.

    It then regenerates PIN_INDEX, ROUTE_INDEX, gui_parity_inventory,
    FUNCTION_INDEX and the dependency graph under that interpreter -- the exact
    artifacts test_pin_index_in_sync and friends check. Section 5 names this
    precise failure: "a full test band was measured on 3.11 and reported seven
    failures that did not exist."

    Refusing is the only safe answer. An interpreter that cannot import the
    deps cannot regenerate an artifact, and writing one anyway is worse than
    writing none.
    """
    src = open(os.path.join(str(_REPO_ROOT), "toolchain", "bin", "bd-reindex"),
               errors="replace").read()
    assert "/home/claude/work/venv/bin/python" not in src, (
        "bd-reindex still defaults its interpreter to a sandbox path that does "
        "not exist on a checkout, so it falls through to bare python3")
    assert 'VENV_PY="$(command -v python3)"' not in src, (
        "bd-reindex still falls back to bare python3, which is 3.11 without "
        "the project deps here. Regenerating a gate's subject under it is how "
        "an artifact becomes wrong in a way nothing reports.")
    # and it must SAY SO rather than proceeding. TWO cases, and the second is
    # the one that matters: an interpreter that is ABSENT is caught by any
    # existence check, while one that is PRESENT AND EXECUTABLE but missing the
    # deps is the actual failure -- bare python3 here is 3.11.15 and cannot
    # import pytest. A mutation battery proved the point: a mutant reduced to
    # `[ -x "$VENV_PY" ]` escaped a test that only tried the absent path.
    import shutil
    r = _sh("bd-reindex", str(_REPO_ROOT),
            env_extra={"BD_VENV_PY": "/nonexistent-python-xyz"})
    out = r.stdout + r.stderr
    assert r.returncode != 0, (
        "bd-reindex ran with an ABSENT interpreter and exited 0:\n%s"
        % out[-800:])
    assert "UNEVALUABLE" in out or "cannot import" in out, out[-800:]

    bare = shutil.which("python3")
    assert bare, "no python3 on PATH; this case cannot be exercised"
    deps_ok = subprocess.run([bare, "-c", "import pytest"],
                             capture_output=True, timeout=60).returncode == 0
    if not deps_ok:
        r = _sh("bd-reindex", str(_REPO_ROOT), env_extra={"BD_VENV_PY": bare})
        out = r.stdout + r.stderr
        assert r.returncode != 0, (
            "bd-reindex accepted %s -- executable, but it cannot import the "
            "project deps. This is the REAL case: section 5 records a full band "
            "measured on 3.11 reporting seven failures that did not exist, and "
            "here it would WRITE the artifacts the gates check.\n%s"
            % (bare, out[-800:]))
        assert "UNEVALUABLE" in out or "cannot import" in out, out[-800:]


def test_bd_reindex_does_not_pin_a_dead_browser_pool():
    """Same class as @876's bd-band fix, in a tool that also writes artifacts."""
    src = open(os.path.join(str(_REPO_ROOT), "toolchain", "bin", "bd-reindex"),
               errors="replace").read()
    assert "/home/claude/.cache/ms-playwright" not in src, (
        "bd-reindex still injects a zip-era browser pool over the real one")


def test_bd_and_bd_status_fail_closed_when_the_env_file_is_absent():
    """@878 -- both sourced /home/claude/bdenv.sh and carried on regardless.

    `bd` had no `set -e` and `exec`ed the command anyway, so `bd <cmd>` ran with
    NONE of the environment its entire purpose is to load -- and exited 0.
    `bd-status` silenced the same failure with `>/dev/null 2>&1`, then printed a
    health report (21/21 kits missing, "BulkDownloader source missing") and
    exited 0. A health check that never loaded the environment it is reporting
    on is the section 0 shape: it cannot see its subject, and says OK.

    Failing closed is right here BECAUSE these are operator tools -- the whole
    output is a verdict a human acts on. An override exists so the refusal
    cannot become a wall.
    """
    for tool in ("bd", "bd-status"):
        r = _sh(tool, "--version", env_extra={"BD_ENV_FILE": "/nonexistent-env-xyz.sh"})
        out = r.stdout + r.stderr
        assert r.returncode != 0, (
            "%s ran with its env file absent and exited 0. Its output is a "
            "verdict an operator acts on.\n%s" % (tool, out[-800:]))
        assert "BD_SKIP_ENV_CHECK" in out, (
            "%s refused without naming the override, so the refusal is a wall "
            "rather than a gate.\n%s" % (tool, out[-800:]))


def test_the_env_refusal_has_an_override_and_a_present_env_is_silent():
    """OVER-SENSITIVITY, both directions.

    A tool that can only refuse is not an instrument. The override must work,
    and a tool whose env file IS present must not mention any of this.
    """
    import tempfile
    r = _sh("bd", "true", env_extra={"BD_ENV_FILE": "/nonexistent-env-xyz.sh",
                                     "BD_SKIP_ENV_CHECK": "1"})
    assert r.returncode == 0, (
        "the documented override did not work:\n%s" % (r.stdout + r.stderr)[-600:])

    with tempfile.TemporaryDirectory() as td:
        envf = os.path.join(td, "bdenv.sh")
        with open(envf, "w") as fh:
            fh.write("export BD_PROBE_MARKER=present\n")
        r = _sh("bd", "true", env_extra={"BD_ENV_FILE": envf})
        out = r.stdout + r.stderr
        assert r.returncode == 0, out[-600:]
        assert "UNEVALUABLE" not in out and "BD_SKIP_ENV_CHECK" not in out, (
            "the tool complained even though its env file was present -- a "
            "guard that speaks when nothing is wrong gets switched off:\n%s"
            % out[-600:])
