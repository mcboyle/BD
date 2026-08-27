"""Row 290 -- default capture reaches its optional fixture and every later step.

The production entry point is executed byte-for-byte from an isolated fake
install.  Only process boundaries (systemd, curl, and the install's Python)
are replaced; argument parsing, serial/parallel selection, capture variables,
step ordering, artifact creation, and the fixture launch are capture.sh's own.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
CAPTURE = REPO / "capture.sh"


@dataclass(frozen=True)
class CaptureRun:
    completed: subprocess.CompletedProcess[str]
    python_calls: tuple[tuple[str, ...], ...]
    installer_calls: tuple[str, ...]
    artifacts: frozenset[str]
    port_lock_names: tuple[str, ...]


def _write_executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _stage_fake_python(path: Path) -> None:
    source = f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with open(os.environ["CAPTURE_FAKE_PYTHON_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")

if args == ["--version"]:
    print("Python capture-probe")
elif args[:2] == ["-m", "pytest"]:
    junit = next(
        value.split("=", 1)[1]
        for value in args
        if value.startswith("--junitxml=")
    )
    Path(junit).write_text(
        '<testsuites tests="1"><testsuite tests="1">'
        '<testcase classname="probe" name="ran"/>'
        '</testsuite></testsuites>\\n',
        encoding="utf-8",
    )
elif args[:1] == ["tools/gui_parity_inventory.py"]:
    target = Path("reports/gui_parity_inventory.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{{"route_source": "live url_map"}}\\n', encoding="utf-8")
elif args[:1] == ["tools/pytest_capture_results.py"]:
    output = Path(args[args.index("--json") + 1])
    summary = Path(args[args.index("--summary") + 1])
    output.write_text('{{"tests": 2}}\\n', encoding="utf-8")
    summary.write_text("2 passed\\n", encoding="utf-8")
elif args[:2] == ["-m", "bulk_downloader.ai_boot_observation"]:
    output = Path(args[args.index("--output") + 1])
    output.write_text('{{"state": "probe"}}\\n', encoding="utf-8")
elif args[:3] == ["-u", "-m", "live_tests.run"]:
    results = Path(args[args.index("--results-dir") + 1])
    results.mkdir(parents=True, exist_ok=True)
    (results / "L1.log").write_text("probe live result\\n", encoding="utf-8")
elif args[:1] == ["-c"] and "len(harness.registry())" in args[1]:
    print("37")
elif args[:1] == ["tools/capture_verdict.py"]:
    print("CAPTURE VERDICT: PASS")
"""
    _write_executable(path, source)


def _stage_capture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    runner = tmp_path / "runner"
    fake_home = tmp_path / "BulkDownloader"
    fake_bin = tmp_path / "bin"
    python_log = tmp_path / "python.jsonl"

    (runner / "scripts").mkdir(parents=True)
    shutil.copy2(CAPTURE, runner / "capture.sh")
    shutil.copytree(REPO / "scripts" / "lib", runner / "scripts" / "lib")
    _stage_fake_python(runner / "venv" / "bin" / "python")

    (fake_home / "bulk_downloader").mkdir(parents=True)
    (fake_home / "frontend" / "dist").mkdir(parents=True)
    (fake_home / "tools").mkdir(parents=True)
    (fake_home / "scripts").mkdir(parents=True)
    (fake_home / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "capture-probe"\n', encoding="utf-8"
    )
    (fake_home / "frontend" / "dist" / "index.html").write_text(
        "<!doctype html><title>capture probe</title>\n", encoding="utf-8"
    )
    (fake_home / "CHANGELOG.md").write_text(
        "# capture probe\n", encoding="utf-8"
    )
    for name in ("live_seed.py", "fixture_site.py"):
        (fake_home / "tools" / name).write_text(
            "# Presence makes capture.sh execute its real step-5a branch.\n",
            encoding="utf-8",
        )
    assert sum((fake_home / "tools" / name).is_file() for name in (
        "live_seed.py", "fixture_site.py"
    )) == 2, "the fixture must make capture.sh's step-5a predicate true"

    _stage_fake_python(fake_home / "venv" / "bin" / "python")
    _write_executable(
        fake_home / "venv" / "bin" / "pip",
        "#!/usr/bin/env bash\nexit 0\n",
    )
    _write_executable(
        fake_home / "install_service.sh",
        """#!/usr/bin/env bash
printf 'legacy %s\n' "$*" >> "${CAPTURE_INSTALLER_LOG:?}"
exit 0
""",
    )
    _write_executable(
        fake_home / "scripts" / "install_capture_service.sh",
        """#!/usr/bin/env bash
printf '%s\n' "$*" >> "${CAPTURE_INSTALLER_LOG:?}"
exit 0
""",
    )

    fake_bin.mkdir()
    _write_executable(
        fake_bin / "sudo",
        """#!/usr/bin/env bash
exec "$@"
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        """#!/usr/bin/env bash
if [ "${1:-}" = "is-active" ]; then
  if [ "${2:-}" = "--quiet" ]; then exit 3; fi
  printf 'active\n'
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env bash
case "$*" in
  *api/dev/routes*) printf '{"routes": []}\n' ;;
  *-w*) printf '200' ;;
  *) printf '{}\n' ;;
esac
exit 0
""",
    )
    for name in ("journalctl", "ollama", "sleep"):
        _write_executable(fake_bin / name, "#!/usr/bin/env bash\nexit 0\n")

    return runner / "capture.sh", fake_home, fake_bin, python_log


def _output_path(stdout: str) -> Path:
    match = re.search(r"^  output  : (/tmp/bd_capture-[^/]+)/  -> ", stdout, re.M)
    assert match, f"capture did not publish its owned output path:\n{stdout}"
    output = Path(match.group(1))
    assert output.name.startswith("bd_capture-"), output
    return output


def _run_capture(
    tmp_path: Path,
    *arguments: str,
    remove_serial_fixture_port: bool = False,
    ports: tuple[int, int] | None = None,
) -> CaptureRun:
    capture, fake_home, fake_bin, python_log = _stage_capture(tmp_path)
    if remove_serial_fixture_port:
        source = capture.read_text(encoding="utf-8")
        anchor = "  CAPTURE_FIXTURE_PORT=8899\n"
        assert source.count(anchor) == 1, (
            "the negative control requires exactly one serial fixture-port "
            f"assignment, found {source.count(anchor)}"
        )
        capture.write_text(source.replace(anchor, "", 1), encoding="utf-8")
    installer_log = tmp_path / "installer.log"
    lock_root = tmp_path / "capture-lock"
    lock_root.mkdir(mode=0o700)
    port_lock_root = tmp_path / "port-locks"
    port_lock_root.mkdir(mode=0o700)
    env = dict(os.environ)
    for name in (
        "BD_INSTALL_DIR",
        "CAPTURE_APP_PORT",
        "CAPTURE_FIXTURE_PORT",
        "CAPTURE_MODE",
    ):
        env.pop(name, None)
    env.update(
        {
            "BD_CAPTURE_SIGNAL_REEXEC": "1",
            "BD_HOME": str(fake_home),
            "CAPTURE_FAKE_PYTHON_LOG": str(python_log),
            "CAPTURE_INSTALLER_LOG": str(installer_log),
            "CAPTURE_KEEP": "999999999",
            "CAPTURE_PORT_LOCK_ROOT": str(port_lock_root),
            "CAPTURE_PORT_PROBE_PYTHON": sys.executable,
            "CAPTURE_STAGE_CAP": "30",
            "CAPTURE_VAULT_GLOBAL_LOCK": str(lock_root / "capture-vault.lock"),
            "HOME": str(tmp_path / "operator-home"),
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "TMPDIR": str(tmp_path),
        }
    )
    if ports is not None:
        assert len(ports) == 2 and ports[0] != ports[1]
        assert all(1024 <= port <= 65535 for port in ports)
        env["CAPTURE_APP_PORT"] = str(ports[0])
        env["CAPTURE_FIXTURE_PORT"] = str(ports[1])
    completed = subprocess.run(
        ["bash", str(capture), *arguments],
        cwd=capture.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = _output_path(completed.stdout)
    try:
        artifacts = frozenset(
            str(path.relative_to(output))
            for path in output.rglob("*")
            if path.is_file()
        )
        python_calls = tuple(
            tuple(json.loads(line))
            for line in python_log.read_text(encoding="utf-8").splitlines()
        )
        installer_calls = tuple(
            installer_log.read_text(encoding="utf-8").splitlines()
            if installer_log.is_file()
            else ()
        )
        port_lock_names = tuple(
            sorted(path.name for path in port_lock_root.glob("*.lock"))
        )
        return CaptureRun(
            completed,
            python_calls,
            installer_calls,
            artifacts,
            port_lock_names,
        )
    finally:
        archive = Path(f"{output}.tar.gz")
        instance = Path("/tmp") / (
            f"bd_capture_vault-{output.name.removeprefix('bd_capture-')}"
        )
        if archive.is_file():
            archive.unlink()
        if output.is_dir():
            shutil.rmtree(output)
        if instance.is_dir():
            shutil.rmtree(instance)


def _two_bindable_ports() -> tuple[int, int]:
    sockets = []
    try:
        while len(sockets) < 2:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            if sock.getsockname()[1] == 8899:
                sock.close()
                continue
            sockets.append(sock)
        ports = tuple(sock.getsockname()[1] for sock in sockets)
        assert len(ports) == 2 and ports[0] != ports[1]
        assert all(port > 0 and port != 8899 for port in ports)
        return ports
    finally:
        for sock in sockets:
            sock.close()


def test_default_capture_reaches_fixture_seed_and_steps_six_through_nine(
    tmp_path: Path,
) -> None:
    run = _run_capture(tmp_path)
    combined = run.completed.stdout + run.completed.stderr

    # Preconditions first: no argument selected parallel mode, both fixture
    # tools existed, and the real script reached the exact historical boundary.
    assert run.completed.args == ["bash", str(tmp_path / "runner" / "capture.sh")]
    assert run.completed.stdout.count(
        "=== [5a/9] Seed synthetic live-check input ==="
    ) == 1
    assert "05_ollama.log" in run.artifacts

    assert run.completed.returncode == 0, combined
    assert "CAPTURE_FIXTURE_PORT: unbound variable" not in combined
    for heading in (
        "=== [6/9] Live-test suite ===",
        "=== [7/9] Dev-tool routes ===",
        "=== [8/9] T51 regenerate_goldens dry-run ===",
        "=== [9/9] HTTP smoke ===",
    ):
        assert run.completed.stdout.count(heading) == 1, (
            f"default capture did not execute {heading!r}:\n{combined}"
        )

    fixture_calls = [
        call for call in run.python_calls
        if call[:1] == ("tools/fixture_site.py",)
    ]
    assert fixture_calls == [("tools/fixture_site.py", "--port", "8899")]
    assert "05a_fixture_site.log" in run.artifacts
    assert "05a_live_seed.log" in run.artifacts
    assert "08_t51_dryrun.log" in run.artifacts
    assert "09_http_smoke.log" in run.artifacts


def test_removing_the_serial_default_reproduces_the_exact_step_five_failure(
    tmp_path: Path,
) -> None:
    run = _run_capture(tmp_path, remove_serial_fixture_port=True)
    combined = run.completed.stdout + run.completed.stderr

    assert run.completed.stdout.count(
        "=== [5a/9] Seed synthetic live-check input ==="
    ) == 1
    assert "05_ollama.log" in run.artifacts
    assert "05a_fixture_site.log" not in run.artifacts
    assert run.completed.returncode == 1
    assert re.search(
        r"capture\.sh: line [0-9]+: CAPTURE_FIXTURE_PORT: unbound variable",
        run.completed.stderr,
    ), combined
    for heading in (
        "=== [6/9] Live-test suite ===",
        "=== [7/9] Dev-tool routes ===",
        "=== [8/9] T51 regenerate_goldens dry-run ===",
        "=== [9/9] HTTP smoke ===",
    ):
        assert heading not in run.completed.stdout


def test_parallel_capture_claims_and_routes_a_real_distinct_port_pair(
    tmp_path: Path,
) -> None:
    ports = _two_bindable_ports()
    run = _run_capture(tmp_path, "--parallel", ports=ports)
    combined = run.completed.stdout + run.completed.stderr

    assert run.completed.returncode == 0, combined
    assert run.completed.stdout.count(f"  app     : http://127.0.0.1:{ports[0]}") == 1
    assert run.completed.stdout.count(
        f"  fixture : http://127.0.0.1:{ports[1]}"
    ) == 1
    assert set(run.port_lock_names) == {
        f"{ports[0]}.lock",
        f"{ports[1]}.lock",
    }
    assert [
        call for call in run.python_calls
        if call[:1] == ("tools/fixture_site.py",)
    ] == [("tools/fixture_site.py", "--port", str(ports[1]))]
    starts = [call for call in run.installer_calls if call.startswith("start ")]
    stops = [call for call in run.installer_calls if call.startswith("stop ")]
    assert len(starts) == 1 and len(stops) == 1, run.installer_calls
    assert f" {ports[0]} " in starts[0]
    live_calls = [
        call for call in run.python_calls
        if call[:3] == ("-u", "-m", "live_tests.run")
    ]
    assert len(live_calls) == 1, run.python_calls
    live = live_calls[0]
    assert live[live.index("--url") + 1] == f"http://127.0.0.1:{ports[0]}"
    for heading in (
        "=== [5a/9] Seed synthetic live-check input ===",
        "=== [6/9] Live-test suite ===",
        "=== [7/9] Dev-tool routes ===",
        "=== [8/9] T51 regenerate_goldens dry-run ===",
        "=== [9/9] HTTP smoke ===",
    ):
        assert run.completed.stdout.count(heading) == 1


def test_transform_control_capture_parses_without_asserting_fixture_routing() -> None:
    """A syntax-only consumer must not catch a behavior-preserving shell parse."""
    completed = subprocess.run(
        ["bash", "-n", str(CAPTURE)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
