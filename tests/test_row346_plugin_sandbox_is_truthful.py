"""Row 346: plugin launches and their checker share one truthful sandbox contract.

The runtime cases execute both launch shapes for all three subprocess bridges.
The checker case supplies the exact former shape (``timeout=`` plus
``capture_output=True``, but inherited environment/cwd and unbounded capture)
and requires it to be distinguishable from the corrected real tree.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_CHECKER = _REPO / "toolchain" / "bin" / "bd-plugin-sandbox-check"
_SENTINEL = "H16_SENTINEL"
_SECRET = "production-secret-row346"

_PLUGIN = '''\
# bd:bridge
import json
import os
import sys

def observation():
    return {
        "sentinel": os.environ.get("H16_SENTINEL"),
        "cwd": os.getcwd(),
        "path_present": bool(os.environ.get("PATH")),
    }

if sys.argv[1] == "--manifest":
    result = {
        "api_version": 2,
        "kind": "processor",
        "name": "row346_probe",
        **observation(),
    }
else:
    sys.stdin.read()
    result = observation()
sys.stdout.write(json.dumps(result))
'''

_FLOOD_PLUGIN = '''\
# bd:bridge
import json
import sys

sys.stdout.write("x" * (512 * 1024) + "\\n")
if sys.argv[1] == "--manifest":
    result = {"api_version": 2, "kind": "processor", "name": "row346_flood"}
else:
    sys.stdin.read()
    result = {"ok": True}
sys.stdout.write(json.dumps(result))
'''


def _plugin(tmp_path: Path, body: str = _PLUGIN) -> Path:
    plugin_dir = tmp_path / "plugin-owned"
    plugin_dir.mkdir()
    path = plugin_dir / "probe.py"
    path.write_text(body, encoding="utf-8")
    return path


def _bridge(name: str, monkeypatch: pytest.MonkeyPatch):
    if name == "py":
        from bulk_downloader import plugin_py_bridge as bridge

        return bridge, None
    if name == "node":
        from bulk_downloader import plugin_node as bridge

        monkeypatch.setattr(bridge, "node_bin", lambda: sys.executable)
        monkeypatch.setattr(bridge, "node_available", lambda _binary=None: True)
        return bridge, None
    if name == "exec":
        from bulk_downloader import plugin_exec as bridge

        return bridge, [sys.executable]
    raise AssertionError(f"unmeasured bridge {name!r}")


def _probe(bridge, interp, path: Path):
    if interp is None:
        return bridge.probe_manifest(path)
    return bridge.probe_manifest(path, interp)


def _fire(bridge, interp, path: Path):
    if interp is None:
        options = (
            {"inproc": False}
            if bridge.__name__.endswith("plugin_py_bridge")
            else {}
        )
        shim = bridge._make_shim(path, **options)
    else:
        shim = bridge._make_shim(path, interp)
    return shim({"subject": "row346"}, event="probe")


@pytest.mark.parametrize("bridge_name", ("py", "node", "exec"))
def test_manifest_launch_hides_ambient_environment_and_uses_plugin_cwd(
    bridge_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = _plugin(tmp_path)
    ambient = tmp_path / "ambient-cwd"
    ambient.mkdir()
    monkeypatch.chdir(ambient)
    monkeypatch.setenv(_SENTINEL, _SECRET)
    bridge, interp = _bridge(bridge_name, monkeypatch)

    manifest, error = _probe(bridge, interp, path)

    assert error == "" and manifest is not None, error
    assert manifest["cwd"] == str(path.parent.resolve()), manifest
    assert manifest["sentinel"] is None, manifest
    assert manifest["path_present"] is True, manifest


@pytest.mark.parametrize("bridge_name", ("py", "node", "exec"))
def test_fire_launch_hides_ambient_environment_and_uses_plugin_cwd(
    bridge_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = _plugin(tmp_path)
    ambient = tmp_path / "ambient-cwd"
    ambient.mkdir()
    monkeypatch.chdir(ambient)
    monkeypatch.setenv(_SENTINEL, _SECRET)
    bridge, interp = _bridge(bridge_name, monkeypatch)

    result = _fire(bridge, interp, path)

    assert result["cwd"] == str(path.parent.resolve()), result
    assert result["sentinel"] is None, result
    assert result["path_present"] is True, result


@pytest.mark.parametrize("bridge_name", ("py", "node", "exec"))
def test_plugin_output_overflow_is_refused_while_normal_output_passes(
    bridge_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    normal = _plugin(tmp_path)
    bridge, interp = _bridge(bridge_name, monkeypatch)
    manifest, error = _probe(bridge, interp, normal)
    assert error == "" and manifest and manifest["name"] == "row346_probe"

    flood_dir = tmp_path / "flood"
    flood_dir.mkdir()
    flood = flood_dir / "flood.py"
    flood.write_text(_FLOOD_PLUGIN, encoding="utf-8")
    flooded_manifest, flooded_error = _probe(bridge, interp, flood)
    assert flooded_manifest is None
    assert "output limit" in flooded_error.lower(), flooded_error
    with pytest.raises(subprocess.SubprocessError, match="output limit"):
        _fire(bridge, interp, flood)


def _run_checker(work: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
    run = subprocess.run(
        [sys.executable, str(_CHECKER), "--work", str(work), "--json"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        document = json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"checker did not emit JSON: rc={run.returncode}\n{run.stdout}{run.stderr}"
        ) from exc
    return run, document


def _leaky_tree(tmp_path: Path) -> Path:
    package = tmp_path / "leaky-tree" / "bulk_downloader"
    package.mkdir(parents=True)
    former_shape = '''\
import subprocess

def probe_manifest(path):
    return subprocess.run(["runtime", str(path), "--manifest"],
                          capture_output=True, text=True, timeout=5)

def _make_shim(path):
    def _shim(payload):
        return subprocess.run(["runtime", str(path), "fire"], input=payload,
                              capture_output=True, text=True, timeout=30)
    return _shim
'''
    for name in ("plugin_node.py", "plugin_py_bridge.py", "plugin_exec.py"):
        (package / name).write_text(former_shape, encoding="utf-8")
    return package.parent


def test_checker_distinguishes_the_six_leaky_launches_from_the_secure_tree(tmp_path: Path):
    leaky_run, leaky = _run_checker(_leaky_tree(tmp_path))
    assert leaky_run.returncode == 1, leaky
    assert leaky["summary"]["bridges"] == 3, leaky
    assert leaky["summary"]["launches"] == 6, leaky
    assert leaky["summary"]["weak"] > 0, leaky
    controls = {finding.get("control") for finding in leaky["findings"]}
    assert {"environment", "cwd", "output"} <= controls, leaky

    secure_run, secure = _run_checker(_REPO)
    assert secure_run.returncode == 0, secure
    assert secure["summary"] == {"bridges": 3, "launches": 6, "weak": 0}, secure
    assert secure["findings"] == [], secure


def test_transform_control_imports_the_sandbox_without_launching_a_plugin():
    """Import-only control for the durable bd-mutate transform-control spec."""
    from bulk_downloader import plugin_sandbox

    assert callable(plugin_sandbox.run_plugin_process)
