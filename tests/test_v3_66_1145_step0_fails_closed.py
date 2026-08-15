"""bd-cut's step-0 release gate must fail CLOSED: only exit 0 authorizes a cut.

WHY, MEASURED 2026-08-15 at 8cea48c by three independent harnesses, one of them
from a clean-room checkout that never read the others:

    checker outcome   0   1   2   3   4  127  crash  missing  exception  timeout
    bd-cut result    go  go  go  STOP go   go     go       go         go       go

Only exit 3 blocked. Everything else -- including "I could not evaluate" --
authorized the cut. `toolchain/bin/bd-cut:959-985`:

    :974-975   if not os.path.isfile(_exe): continue        # silent skip
    :978-979   except Exception as _e: ...; _rc = 0         # TimeoutExpired too
    :980       if _rc == 3:                                 # the ONLY block
    :964       skipped entirely on --resume / --resume-zip / --no-build

THE SHARPEST INSTANCE, and the reason this is a contract violation rather than a
missing feature. `bdtools_sec.EXIT_CANNOT_EVALUATE = 2`. `bd-footguns` returns it
when zero detectors reached a verdict, under its own comment: "UNKNOWN IS A THIRD
STATE AND IT FAILS (CLAUDE.md s0) ... Refuse instead of certifying."
`bd-ratchet` returns it at three sites. Step 0 converted that deliberate refusal
into authorization -- the checker one level down refuses to certify blindness and
the consumer certifies it anyway.

AND IT WAS LIVE, NOT THEORETICAL: `bd-ratchet --check` exits 2 on every fleet
host right now, because its baseline is `~/.bd_metrics_baseline.json` -- untracked,
$HOME-relative, absent. Half of step 0 has been a measured no-op.

NO TEST PINNED ANY OF THIS. Confirmed three independent ways over tracked files:
nothing asserted the ABORT message, `_rc == 3`, `--no-gate`, or bd-cut's return
under `--no-build`/`--resume`. The defect was invisible to every gate for its
whole life, which is why this file exists rather than an edit to an existing one.

WHY STUB EXECUTABLES AND NOT MONKEYPATCHING. The checker is located by SIBLING
PATH of the resolved bd-cut (`os.path.join(dirname(realpath(__file__)), name)`),
never PATH and never an env var -- so a PATH stub proves nothing while looking
green, and monkeypatching `subprocess.run` would replace the very call under
test, letting a mutant that drops the `timeout=` kwarg or the `isfile` guard
escape. Copying bd-cut into a tmp bin/ beside stub checkers exercises the real
seam end to end. The ONE exception is the generic-exception case: no stub can
make the PARENT raise, so that case patches `subprocess.run` and says so.

EVERY CASE ASSERTS ITS OWN WORDS. All six blocking causes share exit 3, so a
test asserting the code alone passes when any of them fires -- CLAUDE.md section
10 records four mutants escaping exactly that way in bd-jobs and bd-ab.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest

# Its subject is one tool's gate, not the tree.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
BDCUT = REPO / "toolchain" / "bin" / "bd-cut"
SEC = REPO / "toolchain" / "bin" / "bdtools_sec.py"

# bd-cut proceeds past step 0 and dies later for an unrelated, deterministic
# reason. That death is the PROCEEDED signal; exit 3 + our text is BLOCKED.
_PROCEED_RC = 1

STUB = """#!/usr/bin/env python3
import json, os, sys, time
mark = os.environ["BD_STEP0_MARKER"]
with open(mark, "a") as fh:
    fh.write(json.dumps({{"tool": os.path.basename(__file__), "argv": sys.argv[1:]}}) + "\\n")
{body}
"""


def _bin_with(tmp: pathlib.Path, stubs: dict, timeout_override=None) -> pathlib.Path:
    """A tmp bin/ holding a copy of bd-cut, its sec module, and chosen stubs.

    `stubs` maps checker name -> python body. A name ABSENT from the mapping is
    the "missing checker" case: the file is simply never written, so no tracked
    file is deleted.
    """
    b = tmp / "bin"
    b.mkdir(parents=True, exist_ok=True)
    src = BDCUT.read_text(encoding="utf-8")
    if timeout_override is not None:
        # Applied-check per CLAUDE.md section 6: unique anchor, then length
        # arithmetic. An injected clock is NOT usable here -- subprocess's
        # timeout is enforced in C/select, not through a patchable `time`, so a
        # fake clock raises nothing and the test would certify a branch it never
        # entered.
        # The bound now lives in the module constant, not at the call site.
        anchor = "STEP0_TIMEOUT = 900"
        assert src.count(anchor) == 1, f"timeout anchor count {src.count(anchor)}"
        new = f"STEP0_TIMEOUT = {timeout_override}"
        after = src.replace(anchor, new, 1)
        assert after != src and len(after) == len(src) - len(anchor) + len(new)
        src = after
    (b / "bd-cut").write_text(src, encoding="utf-8")
    (b / "bd-cut").chmod(0o755)
    shutil.copy(SEC, b / "bdtools_sec.py")
    for name, body in stubs.items():
        p = b / name
        p.write_text(STUB.format(body=body), encoding="utf-8")
        p.chmod(0o755)
    return b


def _work(tmp: pathlib.Path) -> pathlib.Path:
    """Minimal work tree: sec.require_corpus wants a .py; --resume reads
    bulk_downloader/__init__.py."""
    w = tmp / "work"
    (w / "bulk_downloader").mkdir(parents=True, exist_ok=True)
    (w / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')
    (w / "anything.py").write_text("x = 1\n")
    return w


def _run(tmp: pathlib.Path, b: pathlib.Path, w: pathlib.Path, extra=()):
    marker = tmp / "marker.jsonl"
    env = {
        "PATH": "/usr/bin:/bin",          # keep precut's shutil.which off the real tools
        "HOME": str(tmp),
        "BD_STEP0_MARKER": str(marker),
        "BD_DISABLE_KEEPALIVE": "1",
    }
    r = subprocess.run(
        [sys.executable, str(b / "bd-cut"), "--work", str(w),
         "--out", str(tmp / "out"), "--skip-fe", *extra],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(tmp))
    calls = []
    if marker.exists():
        calls = [json.loads(l) for l in marker.read_text().splitlines() if l.strip()]
    return r, calls


def _ran(calls, tool):
    return any(c["tool"] == tool for c in calls)


EXIT = {
    0: "sys.exit(0)",
    1: "sys.exit(1)",
    2: "sys.exit(2)",
    3: "sys.exit(3)",
}
HANG = "time.sleep(30)"


# ------------------------------------------------------------- preconditions

def test_the_harness_drives_the_real_seam():
    """PRECONDITION. Without this every assertion below is vacuous.

    Proves the stub we wrote is what step 0 actually invoked, with step 0's own
    argv -- CLAUDE.md section 6: assert the shape before the verdict.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        b = _bin_with(tmp, {"bd-footguns": EXIT[0], "bd-ratchet": EXIT[0]})
        r, calls = _run(tmp, b, _work(tmp))
        assert _ran(calls, "bd-footguns"), (
            f"step 0 never invoked our stub; the harness proves nothing.\n{r.stdout[-800:]}")
        fg = [c for c in calls if c["tool"] == "bd-footguns"][0]
        assert fg["argv"][:2] == ["--check", "--tree"], fg["argv"]


# ------------------------------------------------------------- the RED matrix

@pytest.mark.parametrize("code,needle", [
    (1, "reported a VIOLATION"),
    (2, "could not evaluate"),
    (3, "reported a VIOLATION"),
])
def test_a_nonzero_checker_blocks_the_cut(code, needle):
    """Only 0 authorizes. 1, 2 and 3 must each block, each naming its reason."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        b = _bin_with(tmp, {"bd-footguns": EXIT[code], "bd-ratchet": EXIT[0]})
        r, calls = _run(tmp, b, _work(tmp))
        assert _ran(calls, "bd-footguns"), "fixture precondition: the stub ran"
        out = r.stdout + r.stderr
        assert r.returncode == 3, (
            f"exit {code} did not block (rc={r.returncode})\n{out[-1200:]}")
        assert "NO-CUT: step-0" in out, out[-800:]
        assert needle in out, f"the refusal does not name its cause: {out[-800:]}"
        assert "bd-footguns" in out


def test_a_missing_checker_blocks_and_says_so():
    """A gate whose INPUT is unavailable must FAIL, not SKIP -- a skip reads as
    green. FOOTGUNS.json's FG-GATE-DEGRADES-TO-SKIP says exactly this, is
    severity=blocking and status=active, and its detector.kind is "none", so
    bd-footguns itself can never fire it."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        b = _bin_with(tmp, {"bd-ratchet": EXIT[0]})     # bd-footguns never written
        assert not (b / "bd-footguns").exists(), "fixture precondition"
        r, calls = _run(tmp, b, _work(tmp))
        assert not _ran(calls, "bd-footguns")
        out = r.stdout + r.stderr
        assert r.returncode == 3, f"a missing checker did not block\n{out[-1200:]}"
        assert "is MISSING" in out and "bd-footguns" in out, out[-800:]


def test_a_timeout_blocks_and_is_not_reported_as_a_pass():
    """TimeoutExpired is an Exception, so it landed in the handler that set
    _rc = 0. A bound that fires must never be indistinguishable from a clean
    result."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        b = _bin_with(tmp, {"bd-footguns": HANG, "bd-ratchet": EXIT[0]},
                      timeout_override=2)
        r, calls = _run(tmp, b, _work(tmp))
        assert _ran(calls, "bd-footguns"), "fixture precondition: the child started"
        out = r.stdout + r.stderr
        assert r.returncode == 3, f"a timeout did not block\n{out[-1200:]}"
        assert "TIMED OUT" in out and "bd-footguns" in out, out[-800:]


def _load_bdcut():
    """Load the real bd-cut as a module. Its __main__ guard runs nothing."""
    import importlib.machinery, importlib.util
    loader = importlib.machinery.SourceFileLoader("bd_cut_uut", str(BDCUT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BDCUT.parent))
    try:
        loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def test_an_exception_in_the_gate_blocks(monkeypatch, tmp_path):
    """The ONE case a stub cannot produce: the PARENT raising.

    No stub can make bd-cut's own subprocess call raise, and adding an env
    backdoor to production code so a test can reach a branch would be a defect,
    not a test. So this drives the REAL step0_gate() with subprocess.run
    patched -- declared here rather than pretended, per CLAUDE.md section 6.
    """
    m = _load_bdcut()
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise OSError(12, "Cannot allocate memory")

    monkeypatch.setattr(m.subprocess, "run", boom)
    # Precondition: the checkers must EXIST, or we would be testing the missing
    # branch instead of the exception branch.
    here = str(BDCUT.parent)
    for name in m.STEP0_CHECKERS:
        assert os.path.isfile(os.path.join(here, name)), f"{name} absent"

    refusals = m.step0_gate(str(tmp_path), checker_dir=here)
    assert calls["n"] >= 1, "subprocess.run was never reached; vacuous"
    assert refusals, "an exception produced no refusal"
    assert any("RAISED" in r and "OSError" in r for r in refusals), refusals


def test_a_timeout_is_distinguishable_from_every_other_refusal(monkeypatch, tmp_path):
    """TimeoutExpired must not share wording with a violation or an exception."""
    m = _load_bdcut()

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=k.get("timeout", 900))

    monkeypatch.setattr(m.subprocess, "run", slow)
    refusals = m.step0_gate(str(tmp_path), checker_dir=str(BDCUT.parent))
    assert refusals and all("TIMED OUT" in r for r in refusals), refusals
    assert not any("RAISED" in r for r in refusals), refusals


def test_only_exit_zero_authorizes(monkeypatch, tmp_path):
    """The whole contract, at the helper, across every code in one place."""
    m = _load_bdcut()

    class R:
        def __init__(self, rc): self.returncode = rc

    for rc in (0, 1, 2, 3, 4, 127, -9):
        monkeypatch.setattr(m.subprocess, "run", lambda *a, rc=rc, **k: R(rc))
        refusals = m.step0_gate(str(tmp_path), checker_dir=str(BDCUT.parent))
        if rc == 0:
            assert refusals == [], f"exit 0 was refused: {refusals}"
        else:
            assert refusals, f"exit {rc} authorized the cut"
            assert all(r.startswith("NO-CUT: step-0") for r in refusals), refusals


def test_an_unrecognised_code_refuses_rather_than_guessing(monkeypatch, tmp_path):
    m = _load_bdcut()

    class R:
        returncode = 42

    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: R())
    refusals = m.step0_gate(str(tmp_path), checker_dir=str(BDCUT.parent))
    assert any("not a recognised verdict" in r for r in refusals), refusals


def test_the_ratchet_refusal_names_its_remedy(monkeypatch, tmp_path):
    """bd-ratchet exits 2 on every fleet host today (no baseline). Blocking
    without naming the fix is how a gate gets switched off."""
    m = _load_bdcut()

    class R:
        returncode = 2

    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: R())
    refusals = m.step0_gate(str(tmp_path), checker_dir=str(BDCUT.parent))
    ratchet = [r for r in refusals if "bd-ratchet" in r]
    assert ratchet, refusals
    assert "bd-ratchet --baseline" in ratchet[0], ratchet[0]


# ------------------------------------------------- every entry path is gated

@pytest.mark.parametrize("flag", ["--resume", "--no-build"])
def test_the_gate_runs_on_resume_and_no_build(flag):
    """Both continue to a real build, so neither is a defensible exemption.

    Measured before the fix: the checker was never invoked on these paths --
    stub_ran was False in every cell, so even an exit-3 checker proceeded.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        b = _bin_with(tmp, {"bd-footguns": EXIT[3], "bd-ratchet": EXIT[0]})
        r, calls = _run(tmp, b, _work(tmp), extra=(flag,))
        out = r.stdout + r.stderr
        assert _ran(calls, "bd-footguns"), (
            f"{flag} skipped the gate entirely; the checker never ran\n{out[-800:]}")
        assert r.returncode == 3, f"{flag} did not block on a violation\n{out[-1200:]}"


# ------------------------------------------------------ over-sensitivity

def test_a_clean_checker_still_proceeds():
    """THE CONTROL THAT MATTERS. A gate that blocks everything passes every
    assertion above and is useless -- CLAUDE.md section 0 counts an
    over-sensitive gate as a soundness bug equal to a false clean."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        b = _bin_with(tmp, {"bd-footguns": EXIT[0], "bd-ratchet": EXIT[0]})
        r, calls = _run(tmp, b, _work(tmp))
        out = r.stdout + r.stderr
        assert _ran(calls, "bd-footguns") and _ran(calls, "bd-ratchet")
        assert r.returncode != 3, f"a clean gate blocked the cut\n{out[-1200:]}"
        assert "NO-CUT: step-0" not in out


def test_no_gate_still_overrides_and_is_LOUD():
    """The override must survive, and must be impossible to miss afterwards.

    An override you cannot later prove happened is indistinguishable from a gate
    that ran. Before this cut --no-gate printed NOTHING: measured over
    comment-stripped source, the token appeared in exactly three code sites and
    none was an announcement.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        b = _bin_with(tmp, {"bd-footguns": EXIT[3], "bd-ratchet": EXIT[0]})
        r, calls = _run(tmp, b, _work(tmp), extra=("--no-gate",))
        out = r.stdout + r.stderr
        assert not _ran(calls, "bd-footguns"), "--no-gate still ran the gate"
        assert r.returncode != 3, "--no-gate failed to override a violation"
        assert "STEP-0 GATE SKIPPED BY OPERATOR" in r.stderr, (
            f"--no-gate is not loud on stderr:\n{r.stderr[-800:]}")


def test_the_help_text_no_longer_names_a_tool_the_gate_does_not_run():
    """--help said "skip the step-0 bd-precut --gate pre-flight
    (footguns/ratchet/stale-doc)". The gate invokes bd-footguns and bd-ratchet
    directly, and there is no stale-doc check. A false help string is how an
    operator learns the wrong model of the gate they are overriding."""
    src = BDCUT.read_text(encoding="utf-8")
    import ast
    tree = ast.parse(src)
    helps = [n.value for n in ast.walk(tree)
             if isinstance(n, ast.keyword) and n.arg == "help"
             and isinstance(n.value, ast.Constant)]
    texts = [h.value for h in helps if isinstance(h.value, str)]
    nogate = [t for t in texts if "no-gate" in t or "step-0" in t]
    assert nogate, "no --no-gate help text found at all"
    joined = " ".join(nogate)
    assert "bd-precut" not in joined, (
        f"--help still names bd-precut, which step 0 does not invoke: {joined!r}")
    assert "stale-doc" not in joined, (
        f"--help still claims a stale-doc check that does not exist: {joined!r}")


# ===================== --detach must not launch an unauthorized cut =========

JOB_STUB = '''#!/usr/bin/env python3
import json, os, sys
mark = os.environ["BD_STEP0_MARKER"]
with open(mark, "a") as fh:
    fh.write(json.dumps({"tool": "bd-job", "argv": sys.argv[1:]}) + "\\n")
sys.exit(0)
'''


def _bin_with_job(tmp, stubs, **kw):
    b = _bin_with(tmp, stubs, **kw)
    p = b / "bd-job"
    p.write_text(JOB_STUB, encoding="utf-8")
    p.chmod(0o755)
    return b


def test_a_blocked_gate_never_launches_a_detached_cut():
    """THE FINDING. --detach used to run BEFORE step 0 and return 0 as soon as
    bd-job accepted the job.

    That 0 meant only "child launched", but nothing in the exit code said so, so
    a cut whose gate would have BLOCKED still launched and a caller reading the
    parent's status could not tell it from an authorized cut. Gating first means
    there is no unauthorized child to misrepresent.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        b = _bin_with_job(tmp, {"bd-footguns": EXIT[3], "bd-ratchet": EXIT[0]})
        r, calls = _run(tmp, b, _work(tmp), extra=("--detach",))
        out = r.stdout + r.stderr
        assert _ran(calls, "bd-footguns"), (
            f"the gate did not run before detaching\n{out[-800:]}")
        assert r.returncode == 3, f"a blocked cut still detached\n{out[-1200:]}"
        assert not _ran(calls, "bd-job"), (
            "bd-job was invoked despite a blocking gate -- an unauthorized cut "
            "was launched")


def test_an_unknown_gate_never_launches_a_detached_cut():
    """exit 2 is UNKNOWN, and UNKNOWN must not authorize a detached cut either."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        b = _bin_with_job(tmp, {"bd-footguns": EXIT[2], "bd-ratchet": EXIT[0]})
        r, calls = _run(tmp, b, _work(tmp), extra=("--detach",))
        assert r.returncode == 3
        assert not _ran(calls, "bd-job"), "an UNKNOWN gate launched a detached cut"


def test_a_clean_gate_launches_the_detached_cut_with_the_right_argv(capfd):
    """OVER-SENSITIVITY CONTROL, and it must be specific.

    "any bd-job call" is NOT sufficient: production calls `bd-job kill cut`
    FIRST, so a run that killed the previous job and then refused to start a new
    one would satisfy a naive check. This asserts a recorded invocation that
    actually STARTS, with the exact leading argv, and proves the child command
    does not carry --detach (it would re-detach forever).
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        b = _bin_with_job(tmp, {"bd-footguns": EXIT[0], "bd-ratchet": EXIT[0]})
        r, calls = _run(tmp, b, _work(tmp), extra=("--detach",))
        out = r.stdout + r.stderr
        assert r.returncode == 0, out[-1200:]

        jobs = [c["argv"] for c in calls if c["tool"] == "bd-job"]
        assert jobs, f"bd-job was never invoked\n{out[-1200:]}"
        # The kill call is expected and is NOT a start.
        kills = [j for j in jobs if j[:1] == ["kill"]]
        starts = [j for j in jobs if j[:4] == ["start", "--name", "cut", "--"]]
        assert kills, f"production kills the previous job first; none seen: {jobs}"
        assert len(starts) == 1, (
            f"expected exactly one `start --name cut --` invocation, got: {jobs}")

        child = starts[0][4:]
        assert child, f"the start call carried no child command: {starts[0]}"
        assert "--detach" not in child, (
            f"the child command still carries --detach and would re-detach "
            f"forever: {child}")
        assert any(x.endswith("bd-cut") for x in child), (
            f"the child does not re-exec bd-cut: {child}")
        assert "--work" in child, f"the child lost --work: {child}"


def test_the_detached_parent_never_claims_the_cut_succeeded():
    """Exit 0 here means "launched". The output must say so in words, because a
    caller that reads only the status has no other way to learn it."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        b = _bin_with_job(tmp, {"bd-footguns": EXIT[0], "bd-ratchet": EXIT[0]})
        r, _ = _run(tmp, b, _work(tmp), extra=("--detach",))
        out = r.stdout + r.stderr
        assert "NOT A CUT VERDICT" in out, (
            f"the detached parent does not disclaim a verdict:\n{out[-900:]}")
        assert "bd-job status cut" in out
        # It must not read as a completed cut.
        for phrase in ("cut complete", "CUT-ready", "cut succeeded"):
            assert phrase not in out, f"the parent claims {phrase!r}: {out[-600:]}"


# ============ bd-footguns: a declared block_on_exit must beat the shortcut ====

def _load_footguns():
    import importlib.machinery, importlib.util
    tool = REPO / "toolchain" / "bin" / "bd-footguns"
    loader = importlib.machinery.SourceFileLoader("bd_footguns_uut", str(tool))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(tool.parent))
    try:
        loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def test_a_real_detector_declaring_block_on_exit_2_blocks_on_unknown(monkeypatch):
    """THE ORDERING DEFECT, on the REAL registry entry.

    FG-GUARD-SHA-BYTE-IDENTICAL declares block_on_exit [1, 2] precisely so that a
    bd-guardcheck BD-GATE-UNRUNNABLE blocks -- CLAUDE.md section 2 says an
    unverified guard pin must not proceed. The cannot-evaluate shortcut ran
    first, so the declared 2 was unreachable and became "skip". If any other
    detector then decided, bd-footguns printed OK and exited 0, and bd-cut's
    step 0 received 0 while a configured blocking detector had returned UNKNOWN.
    """
    m = _load_footguns()
    fg = next((f for f in m.SEED if f["id"] == "FG-GUARD-SHA-BYTE-IDENTICAL"), None)
    # PRECONDITIONS -- assert the shape before the verdict.
    assert fg is not None, "FG-GUARD-SHA-BYTE-IDENTICAL is not in the registry"
    assert fg["detector"]["kind"] == "tool", fg["detector"]
    assert 2 in fg["detector"]["block_on_exit"], (
        "this test is vacuous unless the detector really declares 2: "
        f"{fg['detector']}")
    assert m.sec.EXIT_CANNOT_EVALUATE == 2

    monkeypatch.setattr(m, "_run_tool", lambda cmd, tree: (2, ""))
    verdict, why = m._check_one(fg, str(REPO))
    assert verdict == "violation", (
        f"a delegate returning CANNOT-EVALUATE was recorded as {verdict!r} "
        f"({why!r}) even though the detector declares block_on_exit=[1, 2]")
    assert "2" in why


def test_a_detector_not_declaring_2_still_treats_unknown_as_skip(monkeypatch):
    """OVER-SENSITIVITY CONTROL. The fix must not turn every cannot-evaluate
    into a violation -- only the ones a detector explicitly declares."""
    m = _load_footguns()
    det = {"id": "SYNTHETIC", "severity": "blocking", "status": "active",
           "rule": "r", "fix": "f",
           "detector": {"kind": "tool", "cmd": ["x"], "block_on_exit": [1]}}
    monkeypatch.setattr(m, "_run_tool", lambda cmd, tree: (2, ""))
    verdict, why = m._check_one(det, str(REPO))
    assert verdict == "skip", (
        f"an undeclared cannot-evaluate became {verdict!r}; the fix is "
        "over-sensitive and would block on every unavailable delegate")


def test_a_clean_delegate_still_passes(monkeypatch):
    m = _load_footguns()
    fg = next(f for f in m.SEED if f["id"] == "FG-GUARD-SHA-BYTE-IDENTICAL")
    monkeypatch.setattr(m, "_run_tool", lambda cmd, tree: (0, ""))
    verdict, _ = m._check_one(fg, str(REPO))
    assert verdict == "pass", verdict


def test_a_declared_violation_code_still_blocks(monkeypatch):
    m = _load_footguns()
    fg = next(f for f in m.SEED if f["id"] == "FG-GUARD-SHA-BYTE-IDENTICAL")
    monkeypatch.setattr(m, "_run_tool", lambda cmd, tree: (1, ""))
    verdict, _ = m._check_one(fg, str(REPO))
    assert verdict == "violation", verdict


# ============ the gate must not mutate the tree it is standing in ===========

def test_the_gate_leaves_no_artifact_in_the_callers_cwd(tmp_path):
    """MEASURED DEFECT, v3.66.1147.

    bd-footguns --check with BD_INSTALL_DIR unset and cwd inside the repo wrote
    a 7,467,008-byte downloader_history.db into the WORKING TREE:
    db._resolve_db_path falls back to a relative path resolved against CWD, and
    step 0 inherits whatever cwd the operator ran bd-cut from. The same run with
    a neutral cwd produced zero artifacts and an identical verdict.

    A gate must not modify the thing it judges, nor the tree the operator is
    standing in. This drives the REAL step0_gate with a checker that writes into
    its own cwd, and asserts the caller's directory is untouched.
    """
    m = _load_bdcut()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in m.STEP0_CHECKERS:
        (bin_dir / name).write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "open(os.path.join(os.getcwd(), 'downloader_history.db'),'w').write('x')\n"
            "sys.exit(0)\n")
        (bin_dir / name).chmod(0o755)

    caller = tmp_path / "caller"
    caller.mkdir()
    before = sorted(os.listdir(caller))
    old = os.getcwd()
    os.chdir(caller)
    try:
        refusals = m.step0_gate(str(tmp_path), checker_dir=str(bin_dir))
    finally:
        os.chdir(old)

    assert refusals == [], f"the clean stubs were refused: {refusals}"
    after = sorted(os.listdir(caller))
    assert after == before, (
        f"the gate wrote into the caller's cwd: {set(after) - set(before)}")
    assert not (caller / "downloader_history.db").exists()


def test_the_gate_sandbox_is_removed(tmp_path):
    """The isolation directory is itself a path, and a path is a promise."""
    m = _load_bdcut()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seen = tmp_path / "seen.txt"
    for name in m.STEP0_CHECKERS:
        (bin_dir / name).write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            f"open({str(seen)!r},'a').write(os.getcwd()+chr(10))\n"
            "sys.exit(0)\n")
        (bin_dir / name).chmod(0o755)
    assert m.step0_gate(str(tmp_path), checker_dir=str(bin_dir)) == []
    sandboxes = [l.strip() for l in seen.read_text().splitlines() if l.strip()]
    assert sandboxes, "the checkers never reported a cwd"
    for d in sandboxes:
        assert "bdcut_gate_" in d, f"the checker did not run in a sandbox: {d}"
        assert not os.path.exists(d), f"gate sandbox leaked: {d}"


# ================= --resume-zip: ONE attested subject ======================

def _make_zip(path, files):
    import zipfile
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return path


def test_extract_and_attest_returns_a_verified_directory(tmp_path):
    m = _load_bdcut()
    z = _make_zip(tmp_path / "r.zip", {"a.py": "x = 1\n", "pkg/b.txt": "hello"})
    d = m.extract_and_attest(str(z))
    try:
        assert os.path.isfile(os.path.join(d, "a.py"))
        assert (pathlib.Path(d) / "pkg" / "b.txt").read_text() == "hello"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_attestation_fails_closed_when_the_extract_differs(tmp_path, monkeypatch):
    """THE NEGATIVE. If what lands on disk is not what the archive holds, the
    subject is not vouchable and the cut must not proceed."""
    m = _load_bdcut()
    z = _make_zip(tmp_path / "r.zip", {"a.py": "x = 1\n"})
    import zipfile as _zf
    real = _zf.ZipFile.extractall

    def tamper(self, path=None, *a, **k):
        real(self, path, *a, **k)
        pathlib.Path(path, "a.py").write_text("x = 999   # tampered\n")

    monkeypatch.setattr(_zf.ZipFile, "extractall", tamper)
    with pytest.raises(RuntimeError) as ei:
        m.extract_and_attest(str(z))
    assert "differ from the archive" in str(ei.value)
    leaked = [p for p in pathlib.Path(tempfile.gettempdir()).glob("bdcut_subject_*")]
    assert not leaked, f"a failed attestation leaked its directory: {leaked}"


def test_resume_zip_gate_and_band_share_one_subject(tmp_path, monkeypatch):
    """THE REQUIREMENT. The checker subject path must EQUAL the band subject
    path, and differ from the worktree -- one extraction, one object."""
    m = _load_bdcut()
    z = _make_zip(tmp_path / "r.zip", {"a.py": "x = 1\n"})
    work = tmp_path / "work"
    (work / "bulk_downloader").mkdir(parents=True)
    (work / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')

    seen = {}
    monkeypatch.setattr(m, "step0_gate",
                        lambda subject, **k: seen.setdefault("gate", subject) and [])
    monkeypatch.setattr(m, "band",
                        lambda zp, su, wk, extracted=None: seen.setdefault("band", extracted))
    monkeypatch.setattr(m, "verify", lambda *a, **k: None)
    monkeypatch.setattr(m, "max_summary", lambda *a, **k: None)

    rc = m.main(["--work", str(work), "--out", str(tmp_path / "out"),
                 "--resume-zip", str(z)])
    assert rc == 0, rc
    assert seen.get("gate"), "the gate was never given a subject"
    assert seen.get("band"), "band was never given the extracted subject"
    assert seen["gate"] == seen["band"], (
        f"the certified subject is not the tested subject:\n"
        f"  gate: {seen['gate']}\n  band: {seen['band']}")
    assert seen["gate"] != str(work), "the gate was bound to the WORKTREE"
    assert "bdcut_subject_" in seen["gate"]


def test_the_resume_zip_subject_is_removed_on_every_exit_path(tmp_path, monkeypatch):
    """Cleanup is in main()'s finally, so it survives return, die and raise."""
    m = _load_bdcut()
    z = _make_zip(tmp_path / "r.zip", {"a.py": "x = 1\n"})
    work = tmp_path / "work"
    (work / "bulk_downloader").mkdir(parents=True)
    (work / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')
    seen = {}
    monkeypatch.setattr(m, "step0_gate",
                        lambda subject, **k: seen.setdefault("d", subject) and [])
    monkeypatch.setattr(m, "verify", lambda *a, **k: None)
    monkeypatch.setattr(m, "max_summary", lambda *a, **k: None)

    # normal return
    monkeypatch.setattr(m, "band", lambda *a, **k: None)
    m.main(["--work", str(work), "--out", str(tmp_path / "o1"),
            "--resume-zip", str(z)])
    assert not os.path.exists(seen["d"]), f"subject leaked on return: {seen['d']}"

    # exception mid-band
    seen.clear()
    def boom(*a, **k):
        raise RuntimeError("band exploded")
    monkeypatch.setattr(m, "band", boom)
    with pytest.raises(RuntimeError):
        m.main(["--work", str(work), "--out", str(tmp_path / "o2"),
                "--resume-zip", str(z)])
    assert not os.path.exists(seen["d"]), f"subject leaked on exception: {seen['d']}"


def test_band_does_not_re_extract_over_a_supplied_subject():
    """Structural: the second extraction is gone, not merely unused."""
    src = BDCUT.read_text(encoding="utf-8")
    import ast
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "band")
    body = ast.unparse(fn)
    assert "extracted" in fn.args.args[-1].arg or any(
        a.arg == "extracted" for a in fn.args.args), "band() takes no extracted="
    assert "_preextracted" in body, "band() does not honour a supplied subject"
    assert "if not _preextracted" in body.replace("\n", " ") or \
           "not _preextracted" in body, "extractall is unconditional"


# ============ v3.66.1148: subject integrity, leaks, isolation proof =========

def _multi_zip(path):
    """A MULTI-MEMBER archive. A single-member fixture cannot show whether the
    whole denominator is attested or only the first entry."""
    import zipfile
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(6):
            zf.writestr(f"pkg/mod{i}.py", f"VALUE = {i}\n" * 20)
        zf.writestr("run_tests.py", "print('ok')\n")
    return path


def test_resume_zip_with_detach_is_refused_before_any_extraction(tmp_path, capsys):
    """Two subjects again: the parent would extract/gate A, the child B.

    ASSERTS THE REASON, not just rc 3. At 61e3c4cf this returned 3 anyway --
    because the REAL checkers refused the synthetic extract -- so a
    returncode-only assertion passed for entirely the wrong reason and would
    have certified a fix that did not exist. All step-0 refusals share exit 3;
    the words are the only thing that discriminates (CLAUDE.md section 10).
    """
    m = _load_bdcut()
    z = _multi_zip(tmp_path / "r.zip")
    work = tmp_path / "work"
    (work / "bulk_downloader").mkdir(parents=True)
    (work / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')
    before = set(pathlib.Path(tempfile.gettempdir()).glob("bdcut_subject_*"))
    rc = m.main(["--work", str(work), "--out", str(tmp_path / "o"),
                 "--resume-zip", str(z), "--detach"])
    err = capsys.readouterr().err
    assert rc == 3, f"--resume-zip --detach was accepted (rc={rc})"
    assert "--resume-zip cannot be combined with --detach" in err, (
        f"refused, but not for the detach conflict -- so this proves nothing "
        f"about the fix:\n{err[-700:]}")
    after = set(pathlib.Path(tempfile.gettempdir()).glob("bdcut_subject_*"))
    assert after == before, f"it extracted before refusing: {after - before}"


def test_a_stale_zip_check_that_cannot_evaluate_fails_closed(tmp_path, monkeypatch):
    """'stale-zip check skipped' converted UNKNOWN into continuation."""
    m = _load_bdcut()
    z = _multi_zip(tmp_path / "r.zip")
    d = m.extract_and_attest(str(z))
    try:
        monkeypatch.setattr(m, "_tree_vs_zip_source_hash",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
        with pytest.raises(RuntimeError) as ei:
            m.band(str(z), ["pkg/mod0.py"], str(tmp_path), extracted=d)
        assert "could not evaluate" in str(ei.value)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_real_band_extracts_exactly_once(tmp_path, monkeypatch):
    """Drives the REAL band(), not a lambda, and COUNTS extractall calls."""
    import zipfile as _zf
    m = _load_bdcut()
    z = _multi_zip(tmp_path / "r.zip")
    calls = {"n": 0}
    real = _zf.ZipFile.extractall

    def counted(self, path=None, *a, **k):
        calls["n"] += 1
        return real(self, path, *a, **k)

    monkeypatch.setattr(_zf.ZipFile, "extractall", counted)
    subject = m.extract_and_attest(str(z))          # extraction #1
    assert calls["n"] == 1, calls

    monkeypatch.setattr(m, "_tree_vs_zip_source_hash",
                        lambda *a, **k: ("same", "same", []))
    monkeypatch.setattr(m, "python_for", lambda w: sys.executable)

    class _R:
        # band() takes the LAST line containing "Total:" as its summary and
        # requires "Failed: 0" in it. A fake without "Total:" makes band die for
        # a reason unrelated to the question under test.
        returncode = 0
        stdout = "Total: 1  Passed: 1  Failed: 0\n"
        stderr = ""

    monkeypatch.setattr(m, "run", lambda *a, **k: _R())
    try:
        # A suite that is really IN the archive -- band refuses an empty suite
        # set, and a fixture that produces nothing to band would prove nothing.
        assert (pathlib.Path(subject) / "pkg" / "mod0.py").is_file()
        m.band(str(z), ["pkg/mod0.py"], str(tmp_path), extracted=subject)
    finally:
        shutil.rmtree(subject, ignore_errors=True)
    assert calls["n"] == 1, (
        f"band re-extracted the archive: {calls['n']} extractall calls, want 1")


def test_attestation_covers_a_LATER_member(tmp_path, monkeypatch):
    """Tamper member 5 of 7 -- a first-entry-only check would pass this."""
    import zipfile as _zf
    m = _load_bdcut()
    z = _multi_zip(tmp_path / "r.zip")
    real = _zf.ZipFile.extractall

    def tamper(self, path=None, *a, **k):
        real(self, path, *a, **k)
        victim = pathlib.Path(path, "pkg", "mod4.py")
        assert victim.exists(), "fixture precondition: the later member exists"
        victim.write_text("VALUE = 999  # tampered\n")

    monkeypatch.setattr(_zf.ZipFile, "extractall", tamper)
    with pytest.raises(RuntimeError) as ei:
        m.extract_and_attest(str(z))
    assert "mod4.py" in str(ei.value), (
        f"attestation missed a later member: {ei.value}")


def test_a_swapped_archive_is_caught_before_verify(tmp_path, monkeypatch):
    """The band tests A; verify must not then report on B."""
    m = _load_bdcut()
    z = tmp_path / "r.zip"
    _multi_zip(z)
    work = tmp_path / "work"
    (work / "bulk_downloader").mkdir(parents=True)
    (work / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')

    monkeypatch.setattr(m, "step0_gate", lambda subject, **k: [])

    handed = {}

    def swap(zp, su, wk, extracted=None):
        import zipfile
        handed["zp"] = zp
        # Asserted HERE, not after main() returns: the snapshot is removed by
        # main()'s finally, so the only moment it can be inspected is while the
        # band holds it.
        handed["writable"] = bool(os.stat(zp).st_mode & 0o222)
        # SWAP THE OPERATOR'S ARCHIVE -- the external path, `z`.
        #
        # This used to write to `zp`, the path the band was handed, because
        # before v3.66.1149 they were the same file. They are not any more: the
        # band now receives an OWNED, READ-ONLY snapshot, and this line raised
        # PermissionError when the two came apart. That failure was the fix
        # working -- the test could no longer reach the object it was trying to
        # corrupt. The intent (band judged A, verify must not report on B) is
        # unchanged and is what the assertions below still measure.
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("different.py", "x = 2\n")

    monkeypatch.setattr(m, "band", swap)
    called = {"verify": False}
    monkeypatch.setattr(m, "verify", lambda *a, **k: called.__setitem__("verify", True))
    monkeypatch.setattr(m, "max_summary", lambda *a, **k: None)

    rc = m.main(["--work", str(work), "--out", str(tmp_path / "o"),
                 "--resume-zip", str(z)])
    assert rc == 3, f"a swapped archive was not caught (rc={rc})"
    assert not called["verify"], "verify ran against the replacement archive"
    # The band was never handed the mutable external path in the first place.
    assert os.path.realpath(handed["zp"]) != os.path.realpath(str(z))
    assert handed["writable"] is False, (
        "the band's archive is writable -- the snapshot is not immutable")
    assert not os.path.exists(handed["zp"]), "the snapshot outlived the run"


# --------------------------------------------------------------- leak tests

def _tmp_snapshot():
    t = pathlib.Path(tempfile.gettempdir())
    return set(t.glob("bdcut_*")) | set(t.glob("bdfg_*"))


@pytest.mark.parametrize("mode", ["success", "refusal", "exception"])
def test_no_temporary_directory_survives_a_run(tmp_path, monkeypatch, mode):
    """Success, refusal and exception all end with nothing left behind.

    The snapshot covers bdcut_* AND bdfg_* -- the BD_HOME directories used the
    DEFAULT /tmp/tmp* prefix before this cut, so a bdcut_* glob could not see
    them and "zero leaks" from that glob would have been a gate blind to its
    own subject.
    """
    m = _load_bdcut()
    z = _multi_zip(tmp_path / "r.zip")
    work = tmp_path / "work"
    (work / "bulk_downloader").mkdir(parents=True)
    (work / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')
    monkeypatch.setattr(m, "verify", lambda *a, **k: None)
    monkeypatch.setattr(m, "max_summary", lambda *a, **k: None)
    if mode == "success":
        monkeypatch.setattr(m, "step0_gate", lambda s, **k: [])
        monkeypatch.setattr(m, "band", lambda *a, **k: None)
    elif mode == "refusal":
        monkeypatch.setattr(m, "step0_gate", lambda s, **k: ["NO-CUT: step-0 synthetic"])
    else:
        monkeypatch.setattr(m, "step0_gate", lambda s, **k: [])
        def boom(*a, **k):
            raise RuntimeError("band exploded")
        monkeypatch.setattr(m, "band", boom)

    before = _tmp_snapshot()
    try:
        m.main(["--work", str(work), "--out", str(tmp_path / "o"),
                "--resume-zip", str(z)])
    except RuntimeError:
        assert mode == "exception"
    leaked = _tmp_snapshot() - before
    assert not leaked, f"{mode}: leaked {sorted(str(p) for p in leaked)}"


def test_an_interrupted_extraction_leaks_nothing(tmp_path, monkeypatch):
    """KeyboardInterrupt is not an Exception subclass, and the directory is not
    yet registered with main() when extraction runs."""
    import zipfile as _zf
    m = _load_bdcut()
    z = _multi_zip(tmp_path / "r.zip")
    real = _zf.ZipFile.extractall

    def interrupt(self, path=None, *a, **k):
        real(self, path, *a, **k)
        raise KeyboardInterrupt("operator ^C")

    monkeypatch.setattr(_zf.ZipFile, "extractall", interrupt)
    before = _tmp_snapshot()
    with pytest.raises(KeyboardInterrupt):
        m.extract_and_attest(str(z))
    leaked = _tmp_snapshot() - before
    assert not leaked, f"an interrupt leaked: {sorted(str(p) for p in leaked)}"


def test_cleanup_failures_are_reported_not_swallowed(tmp_path, monkeypatch, capsys):
    """A cleanup that did not happen must never be silent."""
    m = _load_bdcut()
    z = _multi_zip(tmp_path / "r.zip")
    work = tmp_path / "work"
    (work / "bulk_downloader").mkdir(parents=True)
    (work / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')
    monkeypatch.setattr(m, "step0_gate", lambda s, **k: [])
    monkeypatch.setattr(m, "band", lambda *a, **k: None)
    monkeypatch.setattr(m, "verify", lambda *a, **k: None)
    monkeypatch.setattr(m, "max_summary", lambda *a, **k: None)
    monkeypatch.setattr(m.shutil, "rmtree",
                        lambda *a, **k: (_ for _ in ()).throw(OSError(13, "Permission denied")))
    m.main(["--work", str(work), "--out", str(tmp_path / "o"), "--resume-zip", str(z)])
    err = capsys.readouterr().err
    assert "TEMPORARY DIRECTORIES NOT REMOVED" in err, err[-600:]


# ------------------------------------------------- isolation, proven by bytes

def test_the_gate_cannot_overwrite_an_existing_ignored_database(tmp_path):
    """FILENAME COMPARISON CANNOT SEE THIS -- and this was the measured defect.

    The real bd-footguns overwrote an EXISTING gitignored downloader_history.db
    in the caller's cwd. A directory-listing check finds the same filename
    before and after and reports clean. Only the BYTES answer it.
    """
    m = _load_bdcut()
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    for name in m.STEP0_CHECKERS:
        (bin_dir / name).write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "open(os.path.join(os.getcwd(), 'downloader_history.db'),'wb')"
            ".write(b'CLOBBERED')\n"
            "sys.exit(0)\n")
        (bin_dir / name).chmod(0o755)

    caller = tmp_path / "caller"; caller.mkdir()
    sentinel = caller / "downloader_history.db"
    payload = b"SENTINEL-PRODUCTION-DATA" * 64
    sentinel.write_bytes(payload)
    import hashlib
    before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    before_size = sentinel.stat().st_size

    old = os.getcwd(); os.chdir(caller)
    try:
        assert m.step0_gate(str(tmp_path), checker_dir=str(bin_dir)) == []
    finally:
        os.chdir(old)

    after = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    assert after == before, (
        "the gate OVERWROTE an existing database in the caller's cwd; "
        f"sha {before[:12]} -> {after[:12]}")
    assert sentinel.stat().st_size == before_size


def test_the_checker_runs_inside_the_owned_sandbox(tmp_path):
    """cwd, BD_INSTALL_DIR and BD_HOME must all be inside the sandbox."""
    m = _load_bdcut()
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    rec = tmp_path / "rec.txt"
    for name in m.STEP0_CHECKERS:
        (bin_dir / name).write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            f"open({str(rec)!r},'a').write(json.dumps({{'cwd': os.getcwd(),"
            "'install': os.environ.get('BD_INSTALL_DIR'),"
            "'home': os.environ.get('BD_HOME')})+chr(10))\n"
            "sys.exit(0)\n")
        (bin_dir / name).chmod(0o755)
    assert m.step0_gate(str(tmp_path), checker_dir=str(bin_dir)) == []
    rows = [json.loads(l) for l in rec.read_text().splitlines() if l.strip()]
    assert len(rows) == len(m.STEP0_CHECKERS), rows
    for r in rows:
        assert "bdcut_gate_" in r["cwd"], r
        assert r["install"] == r["cwd"], f"BD_INSTALL_DIR outside the sandbox: {r}"
        assert r["home"] == r["cwd"], f"BD_HOME outside the sandbox: {r}"
        assert not os.path.exists(r["cwd"]), f"sandbox leaked: {r['cwd']}"


@pytest.mark.parametrize("body,expect", [
    ("import time; time.sleep(30)", "TIMED OUT"),
    ("import sys; sys.exit(2)", "could not evaluate"),
])
def test_the_sandbox_is_removed_after_timeout_and_refusal(tmp_path, body, expect):
    m = _load_bdcut()
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    rec = tmp_path / "cwds.txt"
    for name in m.STEP0_CHECKERS:
        (bin_dir / name).write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            f"open({str(rec)!r},'a').write(os.getcwd()+chr(10))\n"
            f"{body}\n")
        (bin_dir / name).chmod(0o755)
    refusals = m.step0_gate(str(tmp_path), checker_dir=str(bin_dir), timeout=2)
    assert refusals and any(expect in r for r in refusals), refusals
    for d in {l.strip() for l in rec.read_text().splitlines() if l.strip()}:
        assert not os.path.exists(d), f"sandbox leaked after {expect}: {d}"
