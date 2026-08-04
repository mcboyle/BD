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

_PROSE_ONLY_BASELINE = 166


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
                exec_files.append(fh.read())
        except OSError:
            continue
    assert exec_files, ("no executable-context file was readable -- this check "
                        "cannot see its subject, which is a FAILURE and not a pass.")
    return sorted(t for t in tools if not any(t in blob for blob in exec_files))


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
