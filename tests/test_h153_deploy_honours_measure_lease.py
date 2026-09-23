"""H153: a process is not a lease -- deploy.sh must wait on an UNREAD measurement.

MEASURED 2026-09-07: the step-0 guard refused while the canonical suite ran, the
suite exited at 15:39:27Z, and the deploy's reset landed at 15:39:52Z with the
log still unread. The run's CONSUMER holds a lease (bd-measure-lease.sh take /
release) in $BD_LEASE_DIR; deploy.sh must refuse at step 0 while a fresh lease
is held, and proceed when none is (a stale one is reported, not honoured).

Every run below uses a fake install dir with no .git, so a deploy that gets
past the lease guard stops at the next step-0 refusal ("not a git work tree")
before anything is mutated -- that refusal is the proof it proceeded.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

BD_GATE_SCOPE = "module"
ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy.sh"
PROCEEDED = "not a git work tree"


def _lease(lease_dir: Path, label: str, age: int, *, epoch: bool = True) -> None:
    lease_dir.mkdir(exist_ok=True)
    body = f"label={label}\nhead=0123456789ab\npid=1\nsession=test\ntaken=t\n"
    if epoch:
        body += f"epoch={int(time.time()) - age}\n"
    (lease_dir / f"{label}.lease").write_text(body)


def _deploy(tmp_path: Path, lease_dir: Path) -> subprocess.CompletedProcess[str]:
    install = tmp_path / "install"
    install.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("BD_")}
    env.update(BD_LEASE_DIR=str(lease_dir), HOME=str(tmp_path))
    env["LC_ALL"] = "C"
    return subprocess.run(
        ["bash", str(DEPLOY), "--dir", str(install)],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_no_lease_proceeds_past_the_guard(tmp_path: Path) -> None:
    (tmp_path / "leases").mkdir()
    result = _deploy(tmp_path, tmp_path / "leases")
    assert result.returncode == 2
    assert PROCEEDED in result.stderr, result.stderr
    assert "measurement lease" not in result.stderr


def test_absent_lease_dir_proceeds(tmp_path: Path) -> None:
    result = _deploy(tmp_path, tmp_path / "no-such-dir")
    assert PROCEEDED in result.stderr, result.stderr


def test_a_held_lease_refuses_at_step_0_before_anything_else(tmp_path: Path) -> None:
    _lease(tmp_path / "leases", "canonical-suite", age=60)
    result = _deploy(tmp_path, tmp_path / "leases")
    assert result.returncode == 2
    assert "REFUSED [step 0]" in result.stderr
    assert "measurement lease" in result.stderr and "canonical-suite" in result.stderr, result.stderr
    assert PROCEEDED not in result.stderr


def test_a_stale_lease_is_not_honoured(tmp_path: Path) -> None:
    _lease(tmp_path / "leases", "forgotten", age=5401)
    result = _deploy(tmp_path, tmp_path / "leases")
    assert PROCEEDED in result.stderr, result.stderr


def test_an_unreadable_lease_is_honoured_not_ignored(tmp_path: Path) -> None:
    _lease(tmp_path / "leases", "torn", age=0, epoch=False)
    result = _deploy(tmp_path, tmp_path / "leases")
    assert result.returncode == 2
    assert "torn" in result.stderr and PROCEEDED not in result.stderr, result.stderr


def test_a_non_canonical_epoch_is_honoured_not_ignored(tmp_path: Path) -> None:
    # as1 REFUTE: 'epoch=08' matched a digits-only parse, bash arithmetic rejected it as
    # octal, and the guard fell through as clear. Every non-canonical epoch must refuse.
    for bad in ("08", "09", "0123"):
        leases = tmp_path / f"leases-{bad}"
        leases.mkdir()
        (leases / "octal.lease").write_text(f"label=octal-{bad}\nepoch={bad}\n")
        result = _deploy(tmp_path, leases)
        assert result.returncode == 2, (bad, result.stderr)
        assert f"octal-{bad}" in result.stderr and PROCEEDED not in result.stderr, (bad, result.stderr)


def test_the_lease_guard_precedes_the_reset() -> None:
    code = DEPLOY.read_text(encoding="utf-8")
    assert "_held_measure_lease" in code
    assert code.index("if _held_measure_lease") < code.index('git reset --hard "$NEW"')
