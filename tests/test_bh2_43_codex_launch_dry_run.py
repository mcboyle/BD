"""harness/bd-launch-codex-role.sh --dry-run must not write into the codex home or the seat cwd (BH2-43, LOW).

The effort-profile derivation (BD_CX_EFFORT) and the per-seat prompt write ran BEFORE the DRY exit, so a dry run left
bd-<role>-<effort>.config.toml in ~/.codex and .bd-role-prompt-<seat>.md in /var/tmp/bd-seats/codex-<role>. The harness
is deployed from bd-persist, not this repo: BD_BH2_43_CANDIDATE is the absolute path of the candidate launcher. Every run
is hermetic: temp CODEX_HOME / seat cwd / TMPDIR, stub role-claim and say tools, the prefix gate forced off.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH2_43_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

ROLE = "bh243probe"
SEAT = "bd-cx-bh243probe9"


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def _fixture(tmp_path: Path) -> dict:
    cand = Path(CANDIDATE)
    assert cand.is_file(), f"BD_BH2_43_CANDIDATE supplied but not a file: {cand}"
    home = tmp_path / "codex-home"
    (home / "agents").mkdir(parents=True)
    (home / "agents" / f"bd-{ROLE}.toml").write_text(
        'developer_instructions = "YOU ARE bd-<role>. fixture role text."\n',
        encoding="utf-8",
    )
    (home / f"bd-{ROLE}.config.toml").write_text(
        'model = "fixture-model"\nmodel_reasoning_effort = "medium"\n', encoding="utf-8"
    )
    card = tmp_path / "card.tsv"
    card.write_text(f"{ROLE}\tMULTI\n", encoding="utf-8")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name in ("claim", "say"):
        stub = stubs / name
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    (
        tmp_path / "seat-cwd"
    ).mkdir()  # an explicit BD_CX_WORKDIR is used as given, never created
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("BD_CX_", "BD_CODEX", "CODEX_", "BD_LAUNCH_"))
    }
    env.update(
        LC_ALL="C",
        CODEX_HOME=str(home),
        BD_ROLE_CARDINALITY=str(card),
        BD_LAUNCH_ROLE_CLAIM=str(stubs / "claim"),
        BD_CX_SAY=str(stubs / "say"),
        BD_CX_WORKDIR=str(tmp_path / "seat-cwd"),
        BD_CX_PREFIX_GATE="off",
        BD_CX_EFFORT="high",
        BD_CODEX_APPSERVER_SOCK=str(tmp_path / "no.sock"),
        BD_CODEX=str(tmp_path / "no-codex"),
        TMPDIR=str(tmpdir),
    )
    return {"home": home, "cwd": tmp_path / "seat-cwd", "tmpdir": tmpdir, "env": env}


def _run(fx: dict, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", CANDIDATE, ROLE, SEAT, *extra],
        env=fx["env"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    before = _tree(fx["home"])
    proc = _run(fx, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    # the dry run still derives and shows the effort variant, at its real path/name
    assert f"profile=bd-{ROLE}-high " in proc.stdout, proc.stdout
    assert "effort=high" in proc.stdout, proc.stdout
    assert f"prompt_file={fx['cwd']}/.bd-role-prompt-{SEAT}.md" in proc.stdout, (
        proc.stdout
    )
    leaked = sorted(_tree(fx["home"]) - before)
    assert not leaked, f"BH2_43_DRY_RUN_WROTE codex home: {leaked}"
    assert not list(fx["cwd"].iterdir()), (
        f"BH2_43_DRY_RUN_WROTE seat cwd: {sorted(_tree(fx['cwd']))}"
    )
    assert not list(fx["tmpdir"].iterdir()), (
        f"dry-run temp dir not removed: {list(fx['tmpdir'].iterdir())}"
    )


def test_real_path_still_writes_profile_and_prompt(tmp_path: Path) -> None:
    """Control: without --dry-run the same fixture writes both files where the seat reads them, then stops at the
    absent codex binary (rc 6) before any tmux session is touched."""
    fx = _fixture(tmp_path)
    proc = _run(fx)
    assert proc.returncode == 6, (proc.returncode, proc.stdout, proc.stderr)
    assert "not executable" in proc.stderr or "tmux not on PATH" in proc.stderr, (
        proc.stderr
    )
    prof = fx["home"] / f"bd-{ROLE}-high.config.toml"
    assert prof.is_file() and 'model_reasoning_effort = "high"' in prof.read_text(
        encoding="utf-8"
    )
    prompt = fx["cwd"] / f".bd-role-prompt-{SEAT}.md"
    assert prompt.is_file() and f"YOU ARE {SEAT}" in prompt.read_text(encoding="utf-8")


def test_dry_run_bad_effort_still_refused(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    fx["env"]["BD_CX_EFFORT"] = "turbo"
    proc = _run(fx, "--dry-run")
    assert proc.returncode == 2 and "BD_CX_EFFORT=turbo" in proc.stderr, (
        proc.returncode,
        proc.stderr,
    )
