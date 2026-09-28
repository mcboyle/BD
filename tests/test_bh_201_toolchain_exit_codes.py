"""BH-201 -- three toolchain tools printed a failure and exited 0.

001 bd-vpn-proof printed "DIRECT LEAK" for a vpn_required site and exited 0
    (text and --json). Contract (bdtools_sec EXIT_*): leak -> 1, resolver could
    not be consulted -> 2, clean or fail-closed (VPNRequiredError) -> 0.
    BH1-19: bd-egress-proof shares prove() and had the same exit-0 leak; both run here.
002 bd-sbcap --check printed XX lines for missing capabilities and exited 0.
003 bd-reindex printed "ERR tools/<gen>" for a failed generator and exited 0.

Each probe runs the real tool against a scratch tree; nothing is installed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "toolchain" / "bin"


# ---- 001 bd-vpn-proof / BH1-19 bd-egress-proof ---------------------------------------

_LEAK_TOOLS = ["bd-vpn-proof", "bd-egress-proof"]

_VPN_RUNTIME = '''
class VPNRequiredError(Exception):
    pass

MODE = {mode!r}

def is_vpn_required_for_site(site):
    return MODE != "not-required"

def get_socks_url_for_site(site):
    if MODE == "proxied":
        return "socks5://127.0.0.1:1080"
    if MODE == "fail-closed":
        raise VPNRequiredError("tunnel down")
    if MODE == "crash":
        raise RuntimeError("resolver bug")
    return None
'''


def _vpn_proof(tmp_path: Path, tool: str, mode: str | None, *extra: str) -> subprocess.CompletedProcess[str]:
    work = tmp_path / "work"
    pkg = work / "bulk_downloader"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    if mode is not None:
        (pkg / "vpn_runtime.py").write_text(_VPN_RUNTIME.format(mode=mode), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(BIN / tool), "--work", str(work), "--site", "s", *extra],
        capture_output=True, text=True, timeout=60, check=False, env=dict(os.environ, LC_ALL="C"),
    )


@pytest.mark.parametrize("tool", _LEAK_TOOLS)
@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
@pytest.mark.parametrize(("mode", "want_rc", "want_leak"), [
    ("direct", 1, True),          # vpn_required, no proxy: the leak
    ("crash", 1, True),           # resolver raised something other than VPNRequiredError
    ("proxied", 0, False),        # negative control: routed through the tunnel
    ("fail-closed", 0, False),    # negative control: refused, which is the correct outcome
    ("not-required", 0, False),   # negative control: direct is allowed
])
def test_vpn_proof_exit_code_follows_leak(tmp_path: Path, mode: str, want_rc: int,
                                          want_leak: bool, as_json: bool, tool: str) -> None:
    proc = _vpn_proof(tmp_path, tool, mode, *(["--json"] if as_json else []))
    assert proc.returncode == want_rc, (mode, proc.stdout, proc.stderr)
    if as_json:
        assert json.loads(proc.stdout)["direct_leak"] is want_leak
    else:
        assert ("DIRECT LEAK" in proc.stdout) is want_leak, proc.stdout


@pytest.mark.parametrize("tool", _LEAK_TOOLS)
@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
def test_vpn_proof_unresolvable_is_cannot_evaluate(tmp_path: Path, as_json: bool, tool: str) -> None:
    proc = _vpn_proof(tmp_path, tool, None, *(["--json"] if as_json else []))
    assert proc.returncode == 2, (proc.stdout, proc.stderr)


# ---- 002 bd-sbcap --check ------------------------------------------------------------

def test_sbcap_check_fails_when_it_reports_missing(tmp_path: Path) -> None:
    # A fresh BD_SBCAP_ROOT has no axe.min.js, so at least one XX line is certain.
    env = dict(os.environ, BD_SBCAP_ROOT=str(tmp_path / "sbcap"), HOME=str(tmp_path), LC_ALL="C")
    proc = subprocess.run([str(BIN / "bd-sbcap"), "--check"], capture_output=True,
                          text=True, env=env, timeout=180, check=False)
    xx = [ln for ln in proc.stdout.splitlines() if "XX" in ln]
    assert any("axe-core absent" in ln for ln in xx), proc.stdout
    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    m = re.search(r"== check done: (\d+) problem\(s\) ==", proc.stdout)
    assert m and int(m.group(1)) == len(xx), proc.stdout


def test_sbcap_usage_exits_unchanged() -> None:
    # Negative control: --help and a bad argument keep their exit codes.
    env = dict(os.environ, LC_ALL="C")
    helped = subprocess.run([str(BIN / "bd-sbcap"), "--help"], capture_output=True,
                            text=True, env=env, timeout=30, check=False)
    assert helped.returncode == 0
    bad = subprocess.run([str(BIN / "bd-sbcap"), "--nope"], capture_output=True,
                         text=True, env=env, timeout=30, check=False)
    assert bad.returncode == 2


# ---- 003 bd-reindex ----------------------------------------------------------------------

_GENERATORS = ("build_pin_index.py", "build_route_index.py", "gui_parity_inventory.py",
               "build_function_index.py", "dependency_graph.py")


def _reindex(tmp_path: Path, failing: str | None) -> subprocess.CompletedProcess[str]:
    work = tmp_path / "work"
    (work / "tools").mkdir(parents=True)
    for name in _GENERATORS:
        body = "import sys; sys.exit(3)\n" if name == failing else "pass\n"
        (work / "tools" / name).write_text(body, encoding="utf-8")
    git_env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, LC_ALL="C")
    subprocess.run(["git", "init", "-q", str(work)], env=git_env, check=True, timeout=30)
    env = dict(git_env, BD_VENV_PY=sys.executable)
    return subprocess.run([str(BIN / "bd-reindex"), "--work", str(work)], capture_output=True,
                          text=True, env=env, cwd=tmp_path, timeout=120, check=False)


def test_reindex_fails_when_a_generator_fails(tmp_path: Path) -> None:
    proc = _reindex(tmp_path, "build_route_index.py")
    assert "ERR tools/build_route_index.py" in proc.stdout, proc.stdout
    # The loop still ran every generator; only the verdict changed.
    assert proc.stdout.count("  OK  tools/") == 4, proc.stdout
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert "1 generator(s) failed" in proc.stderr and "build_route_index.py" in proc.stderr


def test_reindex_all_generators_ok_exits_zero(tmp_path: Path) -> None:
    # Negative control: a clean regeneration is still exit 0.
    proc = _reindex(tmp_path, None)
    assert proc.stdout.count("  OK  tools/") == 5, proc.stdout
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
