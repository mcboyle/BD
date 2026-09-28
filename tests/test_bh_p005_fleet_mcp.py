"""Regression tests for plugins/bd-fleet-mcp harness cut bh-p005-bd-agy-worker-2.

Findings covered:
  001 (HIGH) say hook option-argument hides say (sudo -u, env -u, bash --rcfile)
  002 (HIGH) say hook quoted dollar false positive ('cost $0' literal allowed, "$VAR" refused)
  005 (MED)  invoke_subagent ledger written before spawn failure
  006 (MED)  say hook non-utf8 stdin causes UnicodeDecodeError exit 2
  007 (HIGH) say hook unlisted wrappers (setsid, flock, stdbuf, ionice) hide say
  008 (HIGH) premise_verify harness OWNS missing file in existing dir proceeds
  009 (MED)  premise_verify harness OWNS trailing semicolon causes IndexError
  010 (MED)  premise_verify harness check_tests causes UnboundLocalError

Opt-in env:
  BD_BH_P005_FLEET_MCP_CANDIDATE=<directory containing server.py and cli.py>
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH_P005_FLEET_MCP_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _cand_dir() -> Path:
    p = Path(CANDIDATE)
    assert p.is_dir(), f"candidate directory missing: {CANDIDATE}"
    assert (p / "server.py").is_file(), f"server.py missing in {CANDIDATE}"
    assert (p / "cli.py").is_file(), f"cli.py missing in {CANDIDATE}"
    return p


def _import_server(cand_dir: Path, env_vars: dict[str, str]) -> Any:
    # Isolated module load with explicit environment
    for k, v in env_vars.items():
        os.environ[k] = v
    server_path = cand_dir / "server.py"
    spec = importlib.util.spec_from_file_location("cand_server", server_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_cli_hook(
    cand_dir: Path, stdin_bytes: bytes, env_vars: dict[str, str]
) -> subprocess.CompletedProcess[bytes]:
    cli_py = cand_dir / "cli.py"
    full_env = {**os.environ, "PYTHONWARNINGS": "ignore", **env_vars}
    return subprocess.run(
        [sys.executable, "-W", "ignore", str(cli_py), "hook"],
        input=stdin_bytes,
        capture_output=True,
        text=False,
        env=full_env,
        timeout=15,
        check=False,
    )


# --- Finding 006: non-utf8 stdin hook ---------------------------------------


def test_006_non_utf8_stdin_quietly_passes() -> None:
    cand = _cand_dir()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        res = _run_cli_hook(
            cand, b"\xff\xfe", {"BD_FLEET_HOME": str(p), "BD_PERSIST": str(p)}
        )
        assert res.returncode == 0
        assert not res.stdout
        assert not res.stderr


# --- Finding 001 & 007: wrapper options & commands --------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "sudo -u mboyle bd-say.sh seat-A",
        "env -u PATH bd-say.sh seat-A",
        "bash --rcfile /dev/null -c 'bd-say.sh seat-A'",
        "setsid bd-say.sh seat-A",
        "flock /tmp bd-say.sh seat-A",
        "stdbuf -oL bd-say.sh seat-A",
        "ionice bd-say.sh seat-A",
        "sudo bd-say.sh seat-A",
    ],
)
def test_001_007_wrappers_and_option_args_remain_guarded(cmd: str) -> None:
    cand = _cand_dir()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        res = _run_cli_hook(
            cand, cmd.encode("utf-8"), {"BD_FLEET_HOME": str(p), "BD_PERSIST": str(p)}
        )
        assert res.returncode == 2
        assert b"REFUSED" in res.stderr
        assert b"bd-say.sh" in res.stderr


def test_007_mention_only_quietly_passes() -> None:
    cand = _cand_dir()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        res = _run_cli_hook(
            cand, b"grep bd-say f", {"BD_FLEET_HOME": str(p), "BD_PERSIST": str(p)}
        )
        assert res.returncode == 0
        assert not res.stdout
        assert not res.stderr


# --- Finding 002: quoted dollar literal vs dynamic --------------------------


def test_002_single_quoted_dollar_allowed() -> None:
    cand = _cand_dir()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        reg = p / "registry"
        reg.write_text("pm\tseat-A\nworker\tseat-A\n", encoding="utf-8")
        claim = p / "claim.sh"
        claim.write_text('#!/bin/sh\ncat "$REGISTRY"\n', encoding="utf-8")
        claim.chmod(0o755)
        env = {
            "BD_FLEET_HOME": str(p),
            "BD_PERSIST": str(p),
            "REGISTRY": str(reg),
            "BD_ROLE_CLAIM_HELPER": str(claim),
            "BD_SEAT": "fixture",
        }
        res = _run_cli_hook(cand, b"bd-say.sh seat-A 'cost $0'", env)
        assert res.returncode == 0, res.stderr.decode("utf-8", errors="replace")


def test_002_double_quoted_dollar_refused_as_dynamic() -> None:
    cand = _cand_dir()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        res = _run_cli_hook(
            cand,
            b'bd-say.sh seat-A "$NOTSET"',
            {"BD_FLEET_HOME": str(p), "BD_PERSIST": str(p)},
        )
        assert res.returncode == 2
        assert b"dynamic argument" in res.stderr


# --- Finding 005: invoke_subagent ledger on spawn failure -------------------


def test_005_spawn_failure_writes_no_dispatched_ledger_row() -> None:
    cand = _cand_dir()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        env = {"BD_FLEET_HOME": str(p), "BD_PERSIST": str(p), "BD_SEAT": "fixture"}
        mod = _import_server(cand, env)
        res = mod.invoke_subagent("task fail", "slug_fail")
        assert res["status"] == "error"
        ledger = p / "DISPATCH-LEDGER.tsv"
        assert not ledger.exists() or "slug_fail" not in ledger.read_text(
            encoding="utf-8"
        )


def test_005_successful_spawn_writes_dispatched_ledger_row() -> None:
    cand = _cand_dir()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        bin_dir = p / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        spawn_bin = bin_dir / "bd-agy-lean-spawn"
        spawn_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        spawn_bin.chmod(0o755)
        env = {"BD_FLEET_HOME": str(p), "BD_PERSIST": str(p), "BD_SEAT": "fixture"}
        mod = _import_server(cand, env)
        res = mod.invoke_subagent("task pass", "slug_pass")
        assert res["status"] == "dispatched"
        assert res.get("pid", -1) > 0
        ledger = p / "DISPATCH-LEDGER.tsv"
        assert ledger.exists()
        content = ledger.read_text(encoding="utf-8")
        assert "slug_pass" in content
        assert "dispatched" in content


# --- Finding 008, 009, 010: premise_verify harness -------------------------


def test_008_harness_owns_missing_file_in_existing_dir_refutes() -> None:
    cand = _cand_dir()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        sub = p / "sub"
        sub.mkdir()
        have_file = sub / "have.py"
        have_file.write_text("h=1\n", encoding="utf-8")
        have_sha = hashlib.sha256(have_file.read_bytes()).hexdigest()

        brief = p / "brief.md"
        brief.write_text(
            f"KIND: harness\nBASE: sub/have.py {have_sha}\nOWNS: sub/missing.py\n",
            encoding="utf-8",
        )

        env = {"BD_FLEET_HOME": str(p), "BD_PERSIST": str(p), "BD_SEAT": "fixture"}
        mod = _import_server(cand, env)
        res = mod.premise_verify(str(brief))
        assert res["verdict"] == "REFUTE"
        assert any("OWNS absent: sub/missing.py" in err for err in res["violations"])


def test_009_harness_owns_trailing_semicolon_does_not_crash() -> None:
    cand = _cand_dir()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        sub = p / "sub"
        sub.mkdir()
        have_file = sub / "have.py"
        have_file.write_text("h=1\n", encoding="utf-8")
        have_sha = hashlib.sha256(have_file.read_bytes()).hexdigest()

        brief = p / "brief.md"
        brief.write_text(
            f"KIND: harness\nBASE: sub/have.py {have_sha}\nOWNS: sub/have.py;\n",
            encoding="utf-8",
        )

        env = {"BD_FLEET_HOME": str(p), "BD_PERSIST": str(p), "BD_SEAT": "fixture"}
        mod = _import_server(cand, env)
        res = mod.premise_verify(str(brief))
        assert res["verdict"] == "PROCEED"


def test_010_harness_check_tests_resolves_without_unbound_error() -> None:
    cand = _cand_dir()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        sub = p / "sub"
        sub.mkdir()
        have_file = sub / "have.py"
        have_file.write_text("h=1\n", encoding="utf-8")
        have_sha = hashlib.sha256(have_file.read_bytes()).hexdigest()

        test_file = p / "tests" / "test_main.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test(): pass\n", encoding="utf-8")

        brief = p / "brief.md"
        brief.write_text(
            f"KIND: harness\nBASE: sub/have.py {have_sha}\nOWNS: sub/have.py\n",
            encoding="utf-8",
        )

        env = {"BD_FLEET_HOME": str(p), "BD_PERSIST": str(p), "BD_SEAT": "fixture"}
        mod = _import_server(cand, env)

        # Existing test -> PROCEED
        res_pass = mod.premise_verify(str(brief), check_tests=[str(test_file)])
        assert res_pass["verdict"] == "PROCEED"
        assert res_pass["tests"][str(test_file)]["exists"] is True

        # Absent test -> REFUTE
        res_refute = mod.premise_verify(str(brief), check_tests=["tests/nope.py"])
        assert res_refute["verdict"] == "REFUTE"
        assert res_refute["tests"]["tests/nope.py"]["exists"] is False
        assert any(
            "test absent: tests/nope.py" in err for err in res_refute["violations"]
        )
