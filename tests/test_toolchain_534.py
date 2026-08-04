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
_TOOL_BUDGET = 246


def test_the_toolchain_does_not_grow_unbudgeted():
    root = str(_REPO_ROOT)
    n = len(_bd_tools(root))
    assert n > 100, "only %d tools found -- the subject collapsed" % n
    assert n <= _TOOL_BUDGET, (
        "toolchain/bin holds %d bd-* tools, over the %d budget. Adding a tool "
        "owes retiring one: wire it, retire another, or raise _TOOL_BUDGET in "
        "this cut and say why." % (n, _TOOL_BUDGET))
