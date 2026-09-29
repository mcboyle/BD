"""P2 launch-dryrun-writes-prompt: ``bd-launch-role.sh --dry-run`` must not publish the live system prompt.

findings/FINDING-launch-role-dry-run-publishes-live-systemprompt-correctness-B5-B.md: the R2 frozen-prompt block
(mkdir + mktemp + mv of role-systemprompts/<role>.frozen.systemprompt) ran ABOVE the first ``--dry-run`` exit, so a
dry-run rewrote the file every live seat of the role reads (measured: verify.frozen.systemprompt mtime 00:32:09Z).
A dry-run while role-prompts/<role>.prompt is mid-edit would publish the half-edited prompt to every seat.

The harness is deployed from bd-persist, not this repo: ``BD_LAUNCH_DRYRUN_WRITES_PROMPT_CANDIDATE`` is the absolute
path of the candidate script. Hermetic: role prompt, system-prompt dir, frozen-prompt dir and the message tool are
all tmp paths; the cardinality guard is bypassed with its own switch. No seat, tmux session or claim is created.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_LAUNCH_DRYRUN_WRITES_PROMPT_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

ROLE_TEXT = "ROLE PROMPT UNDER EDIT -- must not be published by a dry-run\n"


def _launch(
    tmp: Path, *extra: str, frozen: str | None = None
) -> tuple[subprocess.CompletedProcess[str], Path]:
    script = Path(CANDIDATE)
    assert script.is_file(), f"candidate missing: {CANDIDATE}"
    roles, spdir, frozen_dir = tmp / "roles", tmp / "sp", tmp / "frozen"
    for d in (roles, spdir, frozen_dir):
        d.mkdir(exist_ok=True)
    (roles / "verify.prompt").write_text(ROLE_TEXT)
    if frozen is not None:
        (frozen_dir / "verify.frozen.systemprompt").write_text(frozen)
    say = tmp / "say.sh"
    say.write_text("#!/bin/bash\nexit 0\n")
    say.chmod(0o755)
    env = {
        **os.environ,
        "BD_LAUNCH_ROLE_DIR": str(roles),
        "BD_LAUNCH_SAY": str(say),
        "BD_LAUNCH_SYSTEMPROMPT_DIR": str(spdir),
        "BD_LAUNCH_FROZEN_DIR": str(frozen_dir),
        "BD_LAUNCH_ALLOW_DUP": "1",
        "BD_LAUNCH_OVERRIDE_LOG": str(tmp / "override.log"),
    }
    res = subprocess.run(
        ["bash", str(script), "verify", "B", "bd-verify-B99-B", *extra],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
        cwd=tmp,
    )
    return res, spdir


def test_dry_run_publishes_nothing(tmp_path: Path) -> None:
    res, spdir = _launch(tmp_path, "--dry-run")
    written = sorted(p.name for p in spdir.iterdir())
    assert written == [], (
        f"LAUNCH_DRYRUN_WROTE_PROMPT: --dry-run published {written} into the live system-prompt dir; "
        f"rc={res.returncode}"
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert (
        "DRY systemprompt: " in res.stdout and "(not written: --dry-run)" in res.stdout
    ), res.stdout
    assert "DRY claim: " in res.stdout, res.stdout


def test_dry_run_names_the_frozen_source_when_present(tmp_path: Path) -> None:
    res, spdir = _launch(tmp_path, "--dry-run", frozen="FROZEN PROMPT\n")
    assert list(spdir.iterdir()) == []
    assert res.returncode == 0, res.stdout + res.stderr
    line = next(
        ln for ln in res.stdout.splitlines() if ln.startswith("DRY systemprompt: ")
    )
    assert line.endswith(
        "frozen/verify.frozen.systemprompt (not written: --dry-run)"
    ), line


def test_control_dry_run_contract_is_unchanged(tmp_path: Path) -> None:
    """Passes on BASE and cut: a dry-run still exits 0 and prints the seat, command and claim it would use."""
    res, _spdir = _launch(tmp_path, "--dry-run")
    assert res.returncode == 0, res.stdout + res.stderr
    lines = res.stdout.splitlines()
    assert any(
        ln.startswith("DRY bd-verify-B99-B pool=B role=verify ") for ln in lines
    ), res.stdout
    assert any(
        ln.startswith("DRY cmd: ") and "--append-system-prompt-file" in ln
        for ln in lines
    ), res.stdout
    assert any(ln.startswith("DRY claim: ") for ln in lines), res.stdout
