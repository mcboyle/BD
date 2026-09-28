"""harness/bd-output-bound-hook.py: the default allowlist must be the file the harness ships (BH2-17, HIGH).

The hook's default ALLOWLIST was <HERE>/allowlist.tsv, but the harness ships bd-output-bound-allowlist.tsv beside it and
no launcher sets BD_OUTPUT_BOUND_ALLOWLIST, so every exemption (role prompts, FLEET_RULE*.md, DISPATCH-*.md) was
silently dropped and those reads were clamped. The harness is deployed from bd-persist, not this repo:
BD_BH2_17_CANDIDATE is the absolute path of the candidate hook. Each run copies it into a temp "harness" dir beside a
fixture bd-output-bound-allowlist.tsv, so the default is resolved exactly as deployed (relative to the script's dir).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH2_17_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

SHIPPED_NAME = "bd-output-bound-allowlist.tsv"
BIG = 30_000  # above the hook's 20000-byte READ_BYTES default


def _setup(
    tmp_path: Path, allowlist_name: str | None = SHIPPED_NAME
) -> tuple[Path, Path, Path]:
    cand = Path(CANDIDATE)
    assert cand.is_file(), f"BD_BH2_17_CANDIDATE supplied but not a file: {cand}"
    harness = tmp_path / "harness"
    harness.mkdir()
    hook = harness / "bd-output-bound-hook.py"
    shutil.copy2(cand, hook)
    exempt = tmp_path / "law" / "FLEET_RULE-FIXTURE.md"
    other = tmp_path / "other" / "notes.md"
    for p in (exempt, other):
        p.parent.mkdir()
        p.write_text("x" * (BIG - 1) + "\n", encoding="utf-8")
    if allowlist_name:
        (harness / allowlist_name).write_text(
            f"# fixture\n{tmp_path}/law/*.md\t60000\tfixture law is read whole\n",
            encoding="utf-8",
        )
    return hook, exempt, other


def _read(
    hook: Path, tmp_path: Path, path: Path, extra_env: dict | None = None
) -> tuple[int, dict | None, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("BD_OUTPUT_BOUND", "BD_BIG_READ_OK"))
    }
    env.update(
        LC_ALL="C",
        BD_OUTPUT_BOUND="1",
        BD_OUTPUT_BOUND_DIR=str(tmp_path / "metrics"),
        BD_SEAT="bh2-17-test",
    )
    env.update(extra_env or {})
    event = {
        "session_id": "s",
        "tool_name": "Read",
        "tool_input": {"file_path": str(path)},
    }
    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    hits = tmp_path / "metrics" / "hits.log"
    return (
        proc.returncode,
        json.loads(proc.stdout) if proc.stdout.strip() else None,
        (hits.read_text(encoding="utf-8") if hits.exists() else ""),
    )


def _limit(out: dict | None) -> int | None:
    return (
        None if out is None else out["hookSpecificOutput"]["updatedInput"].get("limit")
    )


def test_shipped_allowlist_exempts_by_default(tmp_path: Path) -> None:
    hook, exempt, _ = _setup(tmp_path)
    rc, out, hits = _read(hook, tmp_path, exempt)
    assert rc == 0
    assert out is None, f"BH2_17_ALLOWLIST_DROPPED: exempt read was clamped: {out}"
    assert "\tbh2-17-test\tallowlist\tRead " in hits and "glob=" in hits, hits


def test_unlisted_big_read_still_clamped(tmp_path: Path) -> None:
    """Negative control: same fixture, a path the allowlist does not cover is bounded to 200 lines."""
    hook, _, other = _setup(tmp_path)
    rc, out, hits = _read(hook, tmp_path, other)
    assert rc == 0 and _limit(out) == 200, (out, hits)
    assert "\tbh2-17-test\tread-bound\tRead " in hits, hits


def test_legacy_name_is_not_the_default(tmp_path: Path) -> None:
    """Negative control: an allowlist only under the old name allowlist.tsv exempts nothing (the fixture is live)."""
    hook, exempt, _ = _setup(tmp_path, allowlist_name="allowlist.tsv")
    rc, out, _ = _read(hook, tmp_path, exempt)
    assert rc == 0 and _limit(out) == 200, out


def test_env_override_still_wins(tmp_path: Path) -> None:
    hook, exempt, _ = _setup(tmp_path, allowlist_name=None)
    alt = tmp_path / "alt.tsv"
    alt.write_text(f"{tmp_path}/law/*.md\t60000\toverride\n", encoding="utf-8")
    rc, out, _ = _read(hook, tmp_path, exempt, {"BD_OUTPUT_BOUND_ALLOWLIST": str(alt)})
    assert rc == 0 and out is None, out


def test_escape_hint_names_shipped_file(tmp_path: Path) -> None:
    hook, _, other = _setup(tmp_path)
    _, out, _ = _read(hook, tmp_path, other)
    assert SHIPPED_NAME in out["hookSpecificOutput"]["permissionDecisionReason"], out
