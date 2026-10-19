"""o1698-adapter-control-roles-r3 (O1735 R5): integrator + usage lanes as adapter control roles; O1731 pin in one place.

1. operations.py CONTROL_ROLES gains integrator (ROLE-CARDINALITY 2 lanes) and the usage lanes usage-a..d (one per Claude
   pool). Each launches only with an explicit Claude pool; a usage lane only on its own pool (bd-launch-role.sh H571 renames
   usage* to usage-<pool>); a lane is exempt from POOL-STATE-UNKNOWN like `usage`; a lane's holder is read per pool
   (`who usage-<p>` filtered), never `incumbent usage-<p>` (canon() folds usage-a..d into one registry role). council stays
   AGY_ONLY (O1732).
2. Rule 37: the O1731 pool-C Fable pin lived in bd-launch-role.sh AND bd-launch-c.sh. The role launcher's copy never fired: it
   ran before the O366 BD_LAUNCH_MODEL case and the O1388 default line, both of which rewrite MODEL after it. The candidate keeps
   the rewrite only in bd-launch-c.sh and bd-launch-role.sh asks it (`--pin-model`) after the last write to MODEL.

The files are deployed from bd-persist, not this repo: BD_O1698_CONTROL_ROLES_R3_CANDIDATE is a directory holding the candidate
operations.py, bd-launch-role.sh and bd-launch-c.sh (opt-in; skipped without it). Everything runs on tmp fixtures and stub
helpers on a shim PATH (hostname, tmux): nothing launches and no live registry, pool table or launch log is read or written.
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_O1698_CONTROL_ROLES_R3_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

_HDR = "pool\tstate\tsince\tevidence\tpct\tresets_at\tweekly_pct\tfive_hour_remaining_pct\n"
_HUB = "10.254.0.1"
LANES = ("usage-a", "usage-b", "usage-c", "usage-d")
NEW_MEMBERS = ("integrator", *LANES)
# the O1731 rewrite arm: one copy across both launchers (rule 37)
REWRITE_ARM = "claude-*|opus*|sonnet*|haiku*)"


def _cand(name: str) -> Path:
    p = Path(CANDIDATE) / name
    assert p.is_file(), f"candidate missing: {p}"
    return p


def _stub(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\nimport json,os,sys\n" + body)
    path.chmod(0o755)
    return path


def _load(path: Path, modname: str):
    loader = importlib.machinery.SourceFileLoader(modname, str(path))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fx(tmp_path, monkeypatch):
    h = tmp_path / "h"
    h.mkdir()
    calls = tmp_path / "calls.jsonl"
    _stub(
        h / "bd-launch-role.sh",
        "open(os.environ['CALLS'],'a').write(json.dumps({'argv':sys.argv[1:],"
        "'model':os.environ.get('BD_LAUNCH_MODEL'),'allow_dup':os.environ.get('BD_LAUNCH_ALLOW_DUP')})+'\\n')\n"
        "print('LAUNCHED fixture')\n",
    )
    _stub(
        h / "bd-role-claim.sh",
        "open(os.environ['CALLS']+'.claim','a').write(json.dumps(sys.argv[1:])+'\\n')\n"
        "if sys.argv[1]=='incumbent':\n"
        "    rc=int(os.environ.get('INC_RC','2'))\n"
        "    print(os.environ.get('INC_OUT','')) if rc==0 else print('bd-role-claim: no live holder',file=sys.stderr)\n"
        "    sys.exit(rc)\n"
        "rc=int(os.environ.get('WHO_RC','0'))\nsys.exit(rc) if rc else print(os.environ.get('WHO_OUT',''))\n",
    )
    _stub(
        h / "hostname", f"print('{_HUB} 10.254.0.2')\n"
    )  # require_hub reads `hostname -I`
    state = tmp_path / "POOL_STATE.tsv"
    state.write_text(
        _HDR + "".join(f"{p}\tOK\t-\tfresh\t2\t-\t10\t-\n" for p in "ABCD")
    )
    proc = tmp_path / "proc"
    (proc / "1").mkdir(parents=True)
    (proc / "loadavg").write_text("1.00 1.00 1.00 1/100 1\n")
    (proc / "stat").write_text("cpu  100 0 0 9900 0 0 0 0 0 0\n")
    stamps = tmp_path / "stamps"
    stamps.mkdir()
    prompts = tmp_path / "role-prompts"
    prompts.mkdir()
    for r in (
        "pm",
        "dispatch",
        "lensrouter",
        "status",
        "council",
        "worker",
        *NEW_MEMBERS,
    ):
        (prompts / f"{r}.prompt").write_text(f"ROLE: {r}\n")
    brief = tmp_path / "brief.md"
    brief.write_text("Offline parser change\n")
    card = (
        tmp_path / "ROLE-CARDINALITY.tsv"
    )  # shaped like the live table: integrator 2 lanes, dispatch 4, lanes SINGLE
    card.write_text(
        "# role\tcardinality\tnotes\npm\tSINGLE\t-\ndispatch\t4\t-\nlensrouter\tSINGLE\t-\nstatus\tSINGLE\t-\n"
        "integrator\t2\t-\ncouncil\tSINGLE\t-\nusage\tSINGLE\t-\nworker\tMULTI\t-\n"
        + "".join(f"{lane}\tSINGLE\t-\n" for lane in LANES)
    )
    env = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": f"{h}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "BD_HUB_HOST": _HUB,
        "BD_STATE_DIR": str(tmp_path / "state"),
        "BD_HARNESS": str(h),
        "BD_PERSIST": str(tmp_path),
        "BD_LIMIT_STATE": str(state),
        "BD_PROC_ROOT": str(proc),
        "BD_LAUNCH_STAMP": str(stamps / "launch-gate.stamp"),
        "BD_IOWAIT_SAMPLE_S": "0.01",
        "BD_LAUNCH_ROLE_DIR": str(prompts),
        "BD_LAUNCH_HELPER": str(h / "bd-launch-role.sh"),
        "BD_ROLE_CLAIM_HELPER": str(h / "bd-role-claim.sh"),
        "CALLS": str(calls),
        "BD_ROLE_CARDINALITY": str(card),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in (
        "WHO_OUT",
        "WHO_RC",
        "INC_OUT",
        "INC_RC",
        "BD_LAUNCH_MODEL",
        "BD_LAUNCH_ALLOW_DUP",
    ):
        monkeypatch.delenv(k, raising=False)
    ops = _load(_cand("operations.py"), f"o1698_ctl_r3_{abs(hash(str(tmp_path)))}")

    class F:
        pass

    f = F()
    f.ops, f.h, f.prompts, f.brief, f.stamps, f.calls, f.card, f.state = (
        ops,
        h,
        prompts,
        brief,
        stamps,
        calls,
        card,
        state,
    )

    def demand(*args, **extra):
        stat = proc / "stat"
        stat.write_text("cpu  100 0 0 9900 0 0 0 0 0 0\n")

        # the hub iowait gate samples /proc/stat around a sleep: advance idle jiffies from the current value, atomically
        def _advance(_s):
            fields = stat.read_text().split()
            fields[4] = str(int(fields[4]) + 1000)
            tmp = stat.with_suffix(".tmp")
            tmp.write_text(" ".join(fields) + "\n")
            os.replace(tmp, stat)

        monkeypatch.setattr(ops.time, "sleep", _advance)
        for k, v in extra.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(
            sys, "argv", ["operations.py", "bd-launch-role-demand", *args]
        )
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = ops.main()
            except SystemExit as e:  # argparse usage errors
                rc = e.code
        for k in extra:
            monkeypatch.delenv(k, raising=False)
        return rc, out.getvalue(), err.getvalue()

    def launched():
        return (
            [json.loads(x) for x in calls.read_text().splitlines()]
            if calls.exists()
            else []
        )

    def claim_calls():
        p = Path(str(calls) + ".claim")
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    f.demand, f.launched, f.claim_calls = demand, launched, claim_calls
    return f


# ---------------------------------------------------------------- 1. operations.py control roles


def test_membership_names_integrator_and_the_four_usage_lanes(fx):
    assert set(NEW_MEMBERS) <= set(fx.ops.CONTROL_ROLES), fx.ops.CONTROL_ROLES
    assert {"pm", "dispatch", "lensrouter", "status"} <= set(
        fx.ops.CONTROL_ROLES
    )  # r2 members kept
    assert "council" not in fx.ops.CONTROL_ROLES and fx.ops.AGY_ONLY_ROLES == (
        "council",
    )  # O1732
    assert "usage" not in fx.ops.CONTROL_ROLES  # plain usage keeps its own branch


def test_integrator_dry_run_names_role_bootstrap_and_reads_no_incumbent(fx):
    rc, out, err = fx.demand("integrator", "D", "--dry-run")
    assert rc == 0, err
    assert out.startswith("DRY-RUN: demand verified integrator D;"), out
    assert (
        f"route: {fx.h / 'bd-launch-role.sh'} integrator D ; role=integrator " in out
    ), out
    assert f"bootstrap={fx.prompts / 'integrator.prompt'} model=table" in out, out
    assert (
        fx.claim_calls() == []
    )  # 2 lanes, not SINGLE: the launcher's lane guard counts, no handover lookup
    assert fx.launched() == []


@pytest.mark.parametrize("role", NEW_MEMBERS)
@pytest.mark.parametrize("pool", ["codex", "kimi", "agy", "agy-claude", "auto"])
def test_new_members_need_an_explicit_claude_pool(fx, role, pool):
    rc, _, err = fx.demand(role, pool, "--dry-run")
    assert rc == 2, err
    assert (
        f"REFUSED: {role} needs an explicit Claude pool (A|B|C|D), not {pool}" in err
    ), err
    assert fx.launched() == []


def test_every_control_role_is_refused_off_the_claude_pools(fx):
    for role in fx.ops.CONTROL_ROLES:
        rc, _, err = fx.demand(role, "codex", "--dry-run")
        assert rc == 2, (role, err)
        assert f"REFUSED: {role} needs an explicit Claude pool" in err, err


def test_integrator_model_override_reaches_the_launcher(fx):
    rc, out, err = fx.demand("integrator", "B", "--model", "fable")
    assert rc == 0, err
    assert out.startswith("LAUNCH: integrator B;") and "model=fable(override)" in out, (
        out
    )
    assert fx.launched() == [
        {"argv": ["integrator", "B"], "model": "fable", "allow_dup": None}
    ]


def test_model_gate_still_refuses_a_non_control_role(fx):
    rc, _, err = fx.demand(
        "worker", "B", "--brief", str(fx.brief), "--model", "fable", "--dry-run"
    )
    assert rc == 2
    assert "--model applies only to agy pools, not B" in err, err


def test_integrator_successor_without_incumbent_refused(fx):
    rc, _, err = fx.demand("integrator", "A", "--successor", "--dry-run")
    assert rc == 2
    assert "--successor given but integrator has no live incumbent" in err, err


@pytest.mark.parametrize("lane", LANES)
def test_usage_lane_on_its_own_pool_reads_holders_per_pool(fx, lane):
    pool = lane[-1].upper()
    rc, out, err = fx.demand(lane, pool, "--dry-run")
    assert rc == 0, err
    assert f"route: {fx.h / 'bd-launch-role.sh'} {lane} {pool} ; role={lane} " in out, (
        out
    )
    assert f"bootstrap={fx.prompts / f'{lane}.prompt'}" in out, out
    assert fx.claim_calls() == [
        ["who", lane]
    ]  # never `incumbent usage-x` (canon folds a..d)


@pytest.mark.parametrize(
    "lane,pool",
    [("usage-b", "A"), ("usage-a", "D"), ("usage-c", "B"), ("usage-d", "C")],
)
def test_usage_lane_off_its_pool_refused(fx, lane, pool):
    rc, _, err = fx.demand(lane, pool, "--dry-run")
    assert rc == 2
    assert (
        f"REFUSED: {lane} is the usage lane of pool {lane[-1].upper()}, not {pool}"
        in err
    ), err
    assert fx.launched() == [] and fx.claim_calls() == []


def test_usage_lane_held_on_its_pool_refused_by_name(fx):
    rc, _, err = fx.demand("usage-b", "B", "--dry-run", WHO_OUT="bd-usage-b-B")
    assert rc == 2
    assert "ROLE-HELD: usage-b is held by bd-usage-b-B" in err, err


def test_usage_lane_held_only_on_another_pool_is_not_held(fx):
    # canon() folds usage-a..d: `who usage-b` lists pool A's seat too; it is not pool B's holder
    rc, out, err = fx.demand("usage-b", "B", "--dry-run", WHO_OUT="bd-usage-a-A")
    assert rc == 0, err
    assert "successor-to" not in out, out


def test_usage_lane_successor_hands_over_with_allow_dup(fx):
    rc, out, err = fx.demand(
        "usage-c", "C", "--successor", WHO_OUT="bd-usage-c-C bd-usage-a-A"
    )
    assert rc == 0, err
    assert "successor-to=bd-usage-c-C" in out, out
    assert fx.launched() == [
        {"argv": ["usage-c", "C"], "model": None, "allow_dup": "1"}
    ]


def test_usage_lane_registry_failure_is_could_not_look(fx):
    rc, _, err = fx.demand("usage-a", "A", "--dry-run", WHO_RC="1")
    assert rc == 2, err
    assert "REFUSED: helper rc=1" in err, (
        err
    )  # an unreadable registry never reads as "no holder"
    assert fx.claim_calls() == [["who", "usage-a"]]
    assert fx.launched() == []


def _pool_unknown(fx, pool):
    rows = [
        ln
        if not ln.startswith(f"{pool}\t")
        else f"{pool}\tUNKNOWN\t-\tstale\t-\t-\t-\t-"
        for ln in fx.state.read_text().splitlines()
    ]
    fx.state.write_text("\n".join(rows) + "\n")


def test_usage_lane_is_exempt_from_pool_state_unknown(fx):
    _pool_unknown(fx, "C")
    rc, out, err = fx.demand("usage-c", "C", "--dry-run")
    assert rc == 0, err
    rc, _, err = fx.demand(
        "integrator", "C", "--dry-run"
    )  # control: every other control role still refuses
    assert rc == 2 and "POOL-STATE-UNKNOWN: pool C" in err, err


@pytest.mark.parametrize("pool", ["A", "B", "C", "D", "agy-claude"])
def test_council_stays_agy_only(fx, pool):
    rc, _, err = fx.demand("council", pool, "--brief", str(fx.brief), "--dry-run")
    assert rc == 2
    assert f"council runs only on pool agy, not {pool} (O1732)" in err, err


@pytest.mark.parametrize("role", ["integrator-b", "usage-e", "bogus"])
def test_aliases_and_unknown_roles_still_refused(fx, role):
    rc, _, err = fx.demand(role, "A", "--dry-run")
    assert rc == 2
    assert f"unsupported demand role: {role}" in err, err


def test_missing_cardinality_row_refuses_new_member(fx):
    fx.card.write_text(
        "".join(
            ln + "\n"
            for ln in fx.card.read_text().splitlines()
            if not ln.startswith("integrator\t")
        )
    )
    rc, _, err = fx.demand("integrator", "A", "--dry-run")
    assert rc == 2
    assert "NO-CARDINALITY: integrator has no row" in err, err


# ---------------------------------------------------------------- 2. O1731 pool-C pin, one place


def _assignments(lines):
    """Line numbers that write the shell variable MODEL (not CODEX_MODEL / BD_LAUNCH_MODEL / MODEL_SRC)."""
    return [
        i
        for i, ln in enumerate(lines)
        if not ln.lstrip().startswith("#") and re.search(r"(?<![A-Za-z0-9_])MODEL=", ln)
    ]


def _pool_c_block(lines):
    starts = [
        i for i, ln in enumerate(lines) if ln.strip() == 'if [ "$POOL" = C ]; then'
    ]
    assert len(starts) == 1, starts
    end = next(i for i in range(starts[0], len(lines)) if lines[i] == "fi")
    return starts[0], end


def test_rewrite_arm_exists_exactly_once_across_both_launchers():
    role, poolc = (
        _cand("bd-launch-role.sh").read_text(),
        _cand("bd-launch-c.sh").read_text(),
    )
    counts = (role.count(REWRITE_ARM), poolc.count(REWRITE_ARM))
    assert counts == (0, 1), (
        f"O1731 rewrite arm in (bd-launch-role.sh, bd-launch-c.sh) = {counts}; rule 37 wants (0, 1)"
    )


def test_role_launcher_delegates_pool_c_after_the_last_model_write():
    lines = _cand("bd-launch-role.sh").read_text().splitlines()
    start, end = _pool_c_block(lines)
    block = "\n".join(lines[start : end + 1])
    assert 'bd-launch-c.sh}" --pin-model "$MODEL"' in block, block
    late = [i + 1 for i in _assignments(lines) if i > end]
    assert late == [], (
        f"MODEL rewritten after the pool-C pin at lines {late}: the pin would not hold"
    )
    use = next(i for i, ln in enumerate(lines) if 'CMD="$CMD --model $MODEL"' in ln)
    assert end < use, "pool-C pin must run before MODEL reaches the claude command line"


def _run_block(tmp_path, pool, model, launch_c):
    lines = _cand("bd-launch-role.sh").read_text().splitlines()
    start, end = _pool_c_block(lines)
    log = tmp_path / "override.log"
    script = (
        'die(){ echo "DIE $1 $2"; exit "$1"; }\n'
        f"POOL={pool}; NAME=bd-x-{pool}; ROLE=pm; MODEL={model}\n"
        f"BD_LAUNCH_C={launch_c}; BD_LAUNCH_OVERRIDE_LOG={log}\n"
        + "\n".join(lines[start : end + 1])
        + '\necho "MODEL=$MODEL"\n'
    )
    p = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=60, check=False
    )
    return p, (log.read_text() if log.exists() else "")


@pytest.mark.parametrize("model", ["claude-opus-5-5", "opus", "sonnet", "haiku"])
def test_pool_c_block_pins_claude_models_to_fable_and_logs(tmp_path, model):
    p, log = _run_block(tmp_path, "C", model, _cand("bd-launch-c.sh"))
    assert (p.returncode, p.stdout.strip().splitlines()[-1]) == (
        0,
        "MODEL=claude-fable-5-1",
    ), p.stdout + p.stderr
    assert f"O1731 bd-x-C role=pm pool=C model={model} -> claude-fable-5-1" in log, log


def test_pool_c_block_leaves_fable_and_other_pools_alone(tmp_path):
    p, log = _run_block(tmp_path, "C", "claude-fable-5-1", _cand("bd-launch-c.sh"))
    assert (p.returncode, p.stdout.strip(), log) == (0, "MODEL=claude-fable-5-1", ""), (
        p.stderr
    )
    p, log = _run_block(tmp_path, "A", "claude-opus-5-5", _cand("bd-launch-c.sh"))
    assert (p.returncode, p.stdout.strip(), log) == (0, "MODEL=claude-opus-5-5", ""), (
        p.stderr
    )


def test_pool_c_block_fails_closed_without_the_pool_launcher(tmp_path):
    p, log = _run_block(
        tmp_path, "C", "claude-opus-5-5", tmp_path / "absent-bd-launch-c.sh"
    )
    assert p.returncode == 3, p.stdout + p.stderr
    assert "DIE 3 O1731 pool-C model pin unavailable" in p.stdout, p.stdout
    assert "MODEL=" not in p.stdout


@pytest.mark.parametrize(
    "model,want",
    [
        ("claude-opus-5-5", "claude-fable-5-1"),
        ("sonnet", "claude-fable-5-1"),
        ("claude-fable-5-1", "claude-fable-5-1"),
        ("gpt-5", "gpt-5"),
    ],
)
def test_pin_model_mode(model, want):
    p = subprocess.run(
        [str(_cand("bd-launch-c.sh")), "--pin-model", model],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert (p.returncode, p.stdout) == (0, want + "\n"), p.stderr


def test_pin_model_mode_needs_exactly_one_model():
    p = subprocess.run(
        [str(_cand("bd-launch-c.sh")), "--pin-model"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert p.returncode == 2 and "usage: bd-launch-c.sh --pin-model <model>" in p.stderr


def test_pool_launcher_own_session_still_pinned(tmp_path):
    """The manual path (bd-launch-c.sh <name> <model> ...) still rewrites: tmux is a shim that records the typed command."""
    shim = tmp_path / "bin"
    shim.mkdir()
    rec = tmp_path / "tmux.jsonl"
    _stub(
        shim / "tmux",
        f"open({str(rec)!r},'a').write(json.dumps(sys.argv[1:])+'\\n')\n"
        "if sys.argv[1]=='has-session':sys.exit(1)\n"
        "if sys.argv[1]=='capture-pane':print('bypass permissions on')\n",
    )
    _stub(shim / "sleep", "")
    wd = tmp_path / "wd"
    wd.mkdir()
    env = {**os.environ, "PATH": f"{shim}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    p = subprocess.run(
        [
            str(_cand("bd-launch-c.sh")),
            "bd-x-C",
            "claude-opus-5-5",
            "high",
            "200000",
            str(wd),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )
    assert p.returncode == 0 and "UP bd-x-C model=claude-fable-5-1" in p.stdout, (
        p.stdout + p.stderr
    )
    typed = [
        c
        for c in (json.loads(x) for x in rec.read_text().splitlines())
        if c[:2] == ["send-keys", "-t"] and "-l" in c
    ]
    assert len(typed) == 1 and "--model claude-fable-5-1 " in typed[0][-1], typed
