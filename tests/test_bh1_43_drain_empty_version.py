"""BH1-43 (findings/FINDING-BH-bd-kimi-audit-2-042): bd-drain.sh turned an unreadable main version into a v3.66.1 claim.

The drain reads origin/main:bulk_downloader/__init__.py with every stderr discarded; when that read fails, V is empty
and bash arithmetic makes $((V+1)) == 1, so the drain announced "v3.66.1 (main )" and handed bd-row-chain.sh a
version-1 claim for a real row. Fix: an unreadable/unparseable main version SKIPs the row as UNKNOWN (git's error
kept in the log) and never reaches bd-row-chain.sh.

The harness is deployed from bd-persist, not this repo: BD_BH1_43_CANDIDATE is the absolute path of the candidate
bd-drain.sh. Hermetic: tmp git repos, tmp artifacts dir, stub row-audit and row-chain.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH1_43_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={**os.environ, **GIT_ENV},
    )


def _repo_with_version(tmp: Path, version: str | None) -> Path:
    """A clone whose origin/main carries bulk_downloader/__init__.py (or no such file when version is None)."""
    up = tmp / "upstream"
    (up / "bulk_downloader").mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=up)
    if version is None:
        (up / "README").write_text("x\n")
    else:
        (up / "bulk_downloader" / "__init__.py").write_text(
            f'__version__ = "{version}"\n'
        )
    _git("add", "-A", cwd=up)
    _git("commit", "-q", "-m", "c", cwd=up)
    clone = tmp / "repo"
    _git("clone", "-q", str(up), str(clone), cwd=tmp)
    return clone


def _drain(tmp: Path, repo: Path) -> tuple[subprocess.CompletedProcess[str], Path]:
    script = Path(CANDIDATE)
    assert script.is_file(), f"candidate missing: {CANDIDATE}"
    audit = tmp / "audit.py"
    audit.write_text("import sys\nsys.exit(0)\n")
    chain_log = tmp / "chain-args.log"
    chain = tmp / "chain.sh"
    chain.write_text(f'#!/bin/bash\necho "$*" >> "{chain_log}"\n')
    chain.chmod(0o755)
    env = {
        **os.environ,
        **GIT_ENV,
        "BD_DRAIN_ARTIFACTS": str(tmp / "art"),
        "BD_DRAIN_REPO": str(repo),
        "BD_HARNESS_DIR": str(tmp / "no-harness"),
        "BD_ROW_AUDIT_PY": str(audit),
        "BD_ROW_CHAIN_SH": str(chain),
    }
    res = subprocess.run(
        ["bash", str(script), "901|slug-a|Title A", "902|slug-b|Title B"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    return res, chain_log


@pytest.mark.parametrize(
    "version", [None, "4.0.1"], ids=["no-init-file", "format-drift"]
)
def test_unreadable_main_version_never_claims_v1(
    tmp_path: Path, version: str | None
) -> None:
    res, chain_log = _drain(tmp_path, _repo_with_version(tmp_path, version))
    chained = chain_log.read_text() if chain_log.exists() else ""
    assert chained == "", (
        f"DRAIN_EMPTY_VERSION_CLAIM: bd-row-chain.sh was called with {chained!r}"
    )
    assert "v3.66.1 (main )" not in res.stdout, res.stdout
    assert res.stdout.count("UNKNOWN main version") == 2, res.stdout
    assert "DRAIN COMPLETE: 0 merged, 2 skipped" in res.stdout, res.stdout


def test_git_show_failure_is_kept_in_the_log(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    res, chain_log = _drain(tmp_path, not_a_repo)
    assert not chain_log.exists()
    assert "rc=128" in res.stdout and "not a git repository" in res.stdout.lower(), (
        res.stdout
    )


def test_control_real_version_chains_next(tmp_path: Path) -> None:
    res, chain_log = _drain(tmp_path, _repo_with_version(tmp_path, "3.66.1703"))
    assert chain_log.read_text().splitlines() == [
        "901 1704 slug-a Title A",
        "902 1704 slug-b Title B",
    ]
    assert "--- 901 -> v3.66.1704 (main 1703) ---" in res.stdout
    assert "DRAIN COMPLETE: 2 merged, 0 skipped" in res.stdout
