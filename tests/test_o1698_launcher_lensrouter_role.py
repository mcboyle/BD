import contextlib
import importlib.util
import io
import os
import re
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_LAUNCHER_LENSROUTER_ROLE_CANDIDATE", "")
pytestmark = pytest.mark.skipif(
    os.environ.get("BD_TEST_O1698_LAUNCHER_LENSROUTER_ROLE") != "1" or not CANDIDATE,
    reason="candidate opt-in required",
)


@pytest.fixture
def scripts():
    candidate = Path(CANDIDATE)
    assert candidate.is_absolute() and candidate.is_file(), f"candidate missing: {CANDIDATE}"
    assert os.access(candidate, os.X_OK), f"candidate not executable: {CANDIDATE}"
    return candidate


def tables(path, tmp_path, pool="C"):
    text = path.read_text()
    functions = []
    for symbol in ("model_for", "effort_for"):
        matches = re.findall(rf"(?ms)^{symbol}\(\) \{{.*?^\}}", text)
        assert len(matches) == 1, f"function census {symbol}: {len(matches)}"
        functions.append(matches[0])
    source = tmp_path / "role-tables.sh"
    source.write_text("\n".join(functions) + "\n")
    result = subprocess.run(
        ["bash", "-c", ('POOL=$2; source "$1"; for role in lensrouter dispatch pm bogus; do '
         'printf "%s|%s|%s\\n" "$role" "$(model_for "$role")" "$(effort_for "$role")"; done'),
         "role-tables", str(source), pool],
        capture_output=True, text=True, timeout=10, check=True,
    )
    return {role: (model, effort) for role, model, effort in
            (line.split("|") for line in result.stdout.splitlines())}


@pytest.mark.parametrize("pool", ["A", "B", "C", "D", "codex"])
def test_lensrouter_uses_dispatch_model_and_high_effort(scripts, tmp_path, pool):
    roles = tables(scripts, tmp_path, pool)
    assert roles["lensrouter"][0] == roles["dispatch"][0] != "", (
        f"O1698-LENSROUTER-MODEL: dispatch={roles['dispatch'][0]!r} lensrouter={roles['lensrouter'][0]!r}"
    )
    assert roles["lensrouter"][1] == "high", (
        f"O1698-LENSROUTER-EFFORT: pool={pool} expected high; got {roles['lensrouter'][1]!r}"
    )
    assert roles["dispatch"][1] == "high", (
        f"O1698-DISPATCH-EFFORT: pool={pool} expected high; got {roles['dispatch'][1]!r}"
    )
    print(f"O1698-LENSROUTER-GREEN: pool={pool} model={roles['lensrouter'][0]} effort=high")


def test_dispatch_and_pm_positive_controls(scripts, tmp_path):
    roles = tables(scripts, tmp_path)
    assert roles["dispatch"] == ("opus", "high")
    assert roles["pm"] == ("claude-opus-5-5", "high")
    print("O1698-POSITIVE-CONTROLS: dispatch/pm retain their model and high effort")


@pytest.mark.parametrize("pool", ["A", "B", "C", "D", "codex"])
def test_unknown_role_does_not_resolve(scripts, tmp_path, pool):
    roles = tables(scripts, tmp_path, pool)
    assert roles["bogus"] == ("", ""), f"O1698-UNKNOWN-ROLE: pool={pool} got {roles['bogus']!r}"
    print(f"O1698-UNKNOWN-ROLE: pool={pool} model='' effort=''")


@pytest.fixture
def adapter(scripts, tmp_path, monkeypatch):
    candidate = scripts
    fixture = candidate.with_name("operations.py.fixture")
    assert fixture.is_file(), f"adapter fixture missing: {fixture}"
    loader = SourceFileLoader("o1698_lensrouter_adapter", str(fixture))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    ops = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops)
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    launcher = helpers / "bd-launch-role.sh"
    launcher.write_text("#!/bin/sh\necho 'REFUSED: dry-run fixture attempted real launch' >&2\nexit 99\n")
    launcher.chmod(0o755)
    claim = helpers / "bd-role-claim.sh"
    claim.write_text("#!/bin/sh\nif [ \"$1\" = incumbent ]; then echo bd-lensrouter-fixture-C; exit 0; fi\nexit 99\n")
    claim.chmod(0o755)
    prompts = tmp_path / "role-prompts"
    prompts.mkdir()
    (prompts / "lensrouter.prompt").write_text("ROLE: lensrouter fixture\n")
    card = tmp_path / "ROLE-CARDINALITY.tsv"
    card.write_text("# role\tcardinality\tnotes\nlensrouter\tSINGLE\tfixture\n")
    state = tmp_path / "POOL_STATE.tsv"
    state.write_text("pool\tstate\tsince\tevidence\tpct\tresets_at\tweekly_pct\tfive_hour_remaining_pct\n"
                     "C\tOK\t-\tfresh\t2\t-\t10\t-\n")
    proc = tmp_path / "proc"
    (proc / "1").mkdir(parents=True)
    (proc / "loadavg").write_text("1.00 1.00 1.00 1/100 1\n")
    stat = proc / "stat"
    stat.write_text("cpu  100 0 0 9900 0 0 0 0 0 0\n")
    monkeypatch.setattr(ops.time, "sleep", lambda _: stat.write_text("cpu  100 0 0 10900 0 0 0 0 0 0\n"))
    for key in tuple(os.environ):
        if key.startswith("BD_"):
            monkeypatch.delenv(key)
    host_ips = subprocess.run(
        ["hostname", "-I"], capture_output=True, text=True, timeout=10, check=True,
    ).stdout.split()
    assert host_ips, "adapter fixture needs an observed host address"
    for key, value in {
        "BD_HUB_HOST": host_ips[0],
        "BD_HARNESS": helpers, "BD_PERSIST": tmp_path, "BD_LIMIT_STATE": state,
        "BD_PROC_ROOT": proc, "BD_LAUNCH_STAMP": tmp_path / "launch-gate.stamp",
        "BD_LAUNCH_ROLE_DIR": prompts, "BD_LAUNCH_HELPER": launcher,
        "BD_ROLE_CLAIM_HELPER": claim, "BD_ROLE_CARDINALITY": card,
    }.items():
        monkeypatch.setenv(key, str(value))

    def demand(*args):
        monkeypatch.setattr(sys, "argv", ["operations.py", "bd-launch-role-demand", *args])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ops.main()
        return rc, out.getvalue(), err.getvalue()

    return demand, launcher, prompts


def test_adapter_lensrouter_successor_dry_run_reaches_launch_route(adapter):
    demand, launcher, prompts = adapter
    rc, out, err = demand("lensrouter", "C", "--successor", "--dry-run")
    assert rc == 0, (out, err)
    assert out.startswith("DRY-RUN: demand verified lensrouter C;"), out
    assert f"route: {launcher} lensrouter C" in out, out
    assert f"bootstrap={prompts / 'lensrouter.prompt'}" in out, out
    assert "successor-to=bd-lensrouter-fixture-C" in out, out
    assert not err, err
    print("O1698-ADAPTER-GREEN: lensrouter C --successor reached launcher route with fixture prompt")


def test_unknown_role_still_refused(adapter):
    demand, _, _ = adapter
    rc, _, err = demand("bogus", "C", "--dry-run")
    assert rc == 2, err
    assert "REFUSED: unsupported demand role: bogus" in err, err
    print(f"O1698-NEGATIVE: rc={rc} diagnostic={err.strip()}")
