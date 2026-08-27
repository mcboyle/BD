"""Row 175 -- full captures own their service, ports, and verdict routes."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
CAPTURE = REPO / "capture.sh"
INSTANCE_LIB = REPO / "scripts" / "lib" / "capture_instance.sh"
SERVICE_INSTALLER = REPO / "scripts" / "install_capture_service.sh"
LIVE_SEED = REPO / "tools" / "live_seed.py"
HEARTBEAT = REPO / "scripts" / "lib" / "heartbeat.sh"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _wait_claim(proc: subprocess.Popen[str]) -> tuple[int, int]:
    assert proc.stdout is not None
    line = proc.stdout.readline().strip()
    assert line.startswith("CLAIMED:"), (
        f"port claimant exited before its barrier: {line!r}; "
        f"rc={proc.poll()}"
    )
    ports = tuple(int(value) for value in line.split(":", 1)[1].split(","))
    assert len(ports) == 2 and ports[0] != ports[1]
    assert all(1024 <= port <= 65535 for port in ports)
    return ports


def _start_claim(root: Path, run_id: str) -> subprocess.Popen[str]:
    script = (
        'source "$INSTANCE_LIB"\n'
        'bd_capture_instance_init || exit $?\n'
        'printf "CLAIMED:%s,%s\\n" "$CAPTURE_APP_PORT" '
        '"$CAPTURE_FIXTURE_PORT"\n'
        'sleep 30\n'
    )
    env = {
        **os.environ,
        "INSTANCE_LIB": str(INSTANCE_LIB),
        "CAPTURE_RUN_ID": run_id,
        "CAPTURE_PORT_LOCK_ROOT": str(root),
        "CAPTURE_PORT_PROBE_PYTHON": sys.executable,
        "CAPTURE_PORT_SEED": "731",
    }
    env.pop("CAPTURE_APP_PORT", None)
    env.pop("CAPTURE_FIXTURE_PORT", None)
    return subprocess.Popen(
        ["bash", "-c", script], cwd=REPO, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_parallel_capture_runtime_exists_as_an_executable_contract() -> None:
    """The implementation must be a runnable seam, not comments in capture.sh."""
    assert CAPTURE.is_file(), "the capture entry point itself is missing"
    assert CAPTURE.stat().st_size > 0, "capture.sh is empty"

    missing = [
        str(path.relative_to(REPO))
        for path in (INSTANCE_LIB, SERVICE_INSTALLER)
        if not path.is_file()
    ]
    assert not missing, (
        "parallel capture has no executable instance boundary; missing "
        f"{missing}. The current capture still serializes one systemd unit, "
        "one drop-in, app port 5555, and fixture port 8899."
    )


def test_parallel_is_opt_in_and_the_default_stays_serial() -> None:
    """The new instance path must not silently replace the proven default."""
    driver = (
        'source "$INSTANCE_LIB"\n'
        'mode="$(bd_capture_mode "$@")" || exit $?\n'
        'printf "MODE=%s\\n" "$mode"\n'
    )

    modes = []
    for args in ((), ("--workers=2",), ("--parallel",)):
        result = subprocess.run(
            ["bash", "-c", driver, "capture-mode", *args],
            cwd=REPO,
            env={**os.environ, "INSTANCE_LIB": str(INSTANCE_LIB)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"mode selection failed for {args!r}: "
            f"{result.stdout}{result.stderr}"
        )
        lines = [line for line in result.stdout.splitlines()
                 if line.startswith("MODE=")]
        assert len(lines) == 1, (
            f"mode selection for {args!r} produced {len(lines)} verdicts: "
            f"{result.stdout!r}"
        )
        modes.append(lines[0].split("=", 1)[1])

    assert modes == ["serial", "serial", "parallel"], (
        "no flag and a workers-only invocation must retain the serial path; "
        f"only --parallel may select instance services: {modes}"
    )
    assert modes.count("serial") == 2 and modes.count("parallel") == 1

    code = _code_only(CAPTURE)
    wiring = 'CAPTURE_MODE="$(bd_capture_mode "$@")"'
    assert code.count(wiring) == 1, (
        "capture.sh must derive its mode exactly once from the complete argv; "
        f"found {code.count(wiring)} wiring sites"
    )


def test_two_port_pairs_are_live_concurrently_and_a_shared_pair_refuses(
    tmp_path: Path,
) -> None:
    lock_root = tmp_path / "port-locks"
    lock_root.mkdir(mode=0o700)
    first = _start_claim(lock_root, "parallel-a")
    second = None
    try:
        first_ports = _wait_claim(first)
        second = _start_claim(lock_root, "parallel-b")
        second_ports = _wait_claim(second)

        assert first.poll() is None and second.poll() is None, (
            "both claimants must still be executing at the same time"
        )
        assert set(first_ports).isdisjoint(second_ports), (
            f"concurrent captures shared a port: {first_ports}, {second_ports}"
        )
        lock_files = sorted(lock_root.glob("*.lock"))
        assert len(lock_files) == 4, (
            f"two pairs should hold exactly four nonempty resource claims: "
            f"{lock_files}"
        )
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in lock_files)

        conflict = subprocess.run(
            ["bash", "-c", 'source "$INSTANCE_LIB"; bd_capture_instance_init'],
            cwd=REPO,
            env={
                **os.environ,
                "INSTANCE_LIB": str(INSTANCE_LIB),
                "CAPTURE_RUN_ID": "parallel-negative-control",
                "CAPTURE_PORT_LOCK_ROOT": str(lock_root),
                "CAPTURE_PORT_PROBE_PYTHON": sys.executable,
                "CAPTURE_APP_PORT": str(first_ports[0]),
                "CAPTURE_FIXTURE_PORT": str(first_ports[1]),
            },
            capture_output=True, text=True, timeout=10,
        )
        assert conflict.returncode == 73
        assert conflict.stderr.count("CAPTURE-INSTANCE-PORT-REFUSED") == 1
        assert "owned or busy" in conflict.stderr
    finally:
        for proc in (first, second):
            if proc is not None and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=10)


def test_an_occupied_port_and_a_same_port_pair_fail_for_distinct_reasons(
    tmp_path: Path,
) -> None:
    lock_root = tmp_path / "port-locks"
    lock_root.mkdir(mode=0o700)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        busy_port = occupied.getsockname()[1]
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            free_port = candidate.getsockname()[1]

        base_env = {
            **os.environ,
            "INSTANCE_LIB": str(INSTANCE_LIB),
            "CAPTURE_RUN_ID": "busy-negative-control",
            "CAPTURE_PORT_LOCK_ROOT": str(lock_root),
            "CAPTURE_PORT_PROBE_PYTHON": sys.executable,
        }
        busy = subprocess.run(
            ["bash", "-c", 'source "$INSTANCE_LIB"; bd_capture_claim_ports'],
            cwd=REPO,
            env={**base_env, "CAPTURE_APP_PORT": str(busy_port),
                 "CAPTURE_FIXTURE_PORT": str(free_port)},
            capture_output=True, text=True, timeout=10,
        )
    assert busy.returncode == 73
    assert busy.stderr.count("owned or busy") == 1

    same = subprocess.run(
        ["bash", "-c", 'source "$INSTANCE_LIB"; bd_capture_claim_ports'],
        cwd=REPO,
        env={**base_env, "CAPTURE_APP_PORT": str(free_port),
             "CAPTURE_FIXTURE_PORT": str(free_port)},
        capture_output=True, text=True, timeout=10,
    )
    assert same.returncode == 73
    assert same.stderr.count("must be distinct integers") == 1


def _installer_env(
    tmp_path: Path,
) -> tuple[dict[str, str], Path, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl_log = tmp_path / "systemctl.jsonl"
    service_state = tmp_path / "service-state"
    _write_executable(fake_bin / "sudo", '#!/usr/bin/env bash\nexec "$@"\n')
    _write_executable(
        fake_bin / "systemctl",
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['SYSTEMCTL_LOG'], 'a') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "action = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "state = os.environ['SERVICE_STATE']\n"
        "if action == 'restart':\n"
        "    open(state, 'w').write('active\\n')\n"
        "elif action == 'stop':\n"
        "    open(state, 'w').write('inactive\\n')\n"
        "elif action == 'is-active':\n"
        "    value = open(state).read().strip() if os.path.exists(state) else 'unknown'\n"
        "    print(value)\n"
        "    raise SystemExit(0 if value == 'active' else 3)\n"
        "raise SystemExit(0)\n",
    )
    _write_executable(fake_bin / "curl", "#!/usr/bin/env python3\nraise SystemExit(0)\n")
    unit = tmp_path / "bulkdownloader-capture@.service"
    runtime = tmp_path / "run"
    scratch_repo = tmp_path / "checkout"
    scratch_scripts = scratch_repo / "scripts"
    scratch_scripts.mkdir(parents=True)
    installer = scratch_scripts / SERVICE_INSTALLER.name
    _write_executable(
        installer, SERVICE_INSTALLER.read_text(encoding="utf-8")
    )
    (scratch_repo / "downloader_ui.py").write_text(
        "# executable subject placeholder\n", encoding="utf-8"
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "SYSTEMCTL_LOG": str(systemctl_log),
        "SERVICE_STATE": str(service_state),
        "CAPTURE_SERVICE_UNIT_PATH": str(unit),
        "CAPTURE_SERVICE_RUNTIME_DIR": str(runtime),
        "CAPTURE_SERVICE_PYTHON": sys.executable,
        "CAPTURE_SERVICE_READY_TRIES": "1",
    }
    assert installer.is_file() and os.access(installer, os.X_OK)
    assert Path(sys.executable).is_absolute() and os.access(sys.executable, os.X_OK)
    assert not (scratch_repo / "venv" / "bin" / "python").exists(), (
        "the fixture accidentally recreated the repo-local interpreter whose "
        "absence is the host-independent subject"
    )
    return env, unit, runtime, installer


def test_template_start_and_teardown_are_bound_to_the_named_instance(
    tmp_path: Path,
) -> None:
    env, unit, runtime, installer = _installer_env(tmp_path)
    for instance, port in (("run-a", 42101), ("run-b", 42103)):
        state = tmp_path / f"state-{instance}"
        state.mkdir()
        result = subprocess.run(
            [str(installer), "start", instance, str(port), str(state)],
            cwd=REPO, env=env, capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    assert unit.is_file() and unit.stat().st_size > 0
    template = unit.read_text(encoding="utf-8")
    assert template.count("EnvironmentFile=") == 1
    assert "/%i.env" in template
    assert "User=" in template and "Restart=on-failure" in template

    env_a = runtime / "run-a.env"
    env_b = runtime / "run-b.env"
    assert env_a.is_file() and env_b.is_file(), (
        "the fixture did not build two simultaneous instance configurations"
    )
    assert "BD_PORT=42101" in env_a.read_text(encoding="utf-8")
    assert "BD_PORT=42103" in env_b.read_text(encoding="utf-8")
    assert f"BD_INSTALL_DIR={tmp_path / 'state-run-a'}" in env_a.read_text(
        encoding="utf-8"
    )
    assert f"BD_INSTALL_DIR={tmp_path / 'state-run-b'}" in env_b.read_text(
        encoding="utf-8"
    )
    assert env_a.read_text(encoding="utf-8").count(
        f"BD_CAPTURE_PYTHON={sys.executable}"
    ) == 1
    assert env_b.read_text(encoding="utf-8").count(
        f"BD_CAPTURE_PYTHON={sys.executable}"
    ) == 1
    assert env_a.read_text(encoding="utf-8") != env_b.read_text(encoding="utf-8")

    missing_python = tmp_path / "missing-python"
    rejected = subprocess.run(
        [str(installer), "start", "run-invalid", "42105",
         str(tmp_path / "state-run-a")],
        cwd=REPO,
        env={**env, "CAPTURE_SERVICE_PYTHON": str(missing_python)},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert rejected.returncode == 1
    assert rejected.stderr.count(
        f"capture service: missing interpreter {missing_python}"
    ) == 1
    assert not (runtime / "run-invalid.env").exists(), (
        "the unavailable-interpreter control created instance state before "
        "its distinctive refusal")

    stopped = subprocess.run(
        [str(installer), "stop", "run-a"], cwd=REPO, env=env,
        capture_output=True, text=True, timeout=20,
    )
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert not env_a.exists(), "run-a teardown left its own environment"
    assert env_b.is_file(), "run-a teardown removed run-b's environment"

    calls = [
        json.loads(line)
        for line in (tmp_path / "systemctl.jsonl").read_text().splitlines()
    ]
    restarts = [call for call in calls if call[:1] == ["restart"]]
    stops = [call for call in calls if call[:1] == ["stop"]]
    assert restarts == [
        ["restart", "bulkdownloader-capture@run-a.service"],
        ["restart", "bulkdownloader-capture@run-b.service"],
    ]
    assert stops == [["stop", "bulkdownloader-capture@run-a.service"]]
    assert all("bulkdownloader.service" not in call for call in calls)


def _capture_function(name: str) -> str:
    import re

    source = CAPTURE.read_text(encoding="utf-8")
    matches = re.findall(
        rf"^{name}\(\)\s*\{{\n(.*?)^\}}", source, re.MULTILINE | re.DOTALL,
    )
    assert len(matches) == 1, (
        f"expected exactly one {name} function, found {len(matches)}"
    )
    return f"{name}() {{\n{matches[0]}}}\n"


def test_readiness_probes_the_selected_instance_and_has_a_negative_control(
    tmp_path: Path,
) -> None:
    fired = {"count": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
            fired["count"] += 1
            self.send_response(200 if self.path == "/api/health" else 404)
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        driver = (
            "CAPTURE_READY_TRIES=1\nSERVICE_READY_EXIT=0\n"
            + _capture_function("wait_for_service_ready")
            + 'wait_for_service_ready; rc=$?\nprintf "READY_EXIT=%s\\n" '
              '"$SERVICE_READY_EXIT"\nexit "$rc"\n'
        )
        good = subprocess.run(
            ["bash", "-c", driver],
            env={**os.environ, "CAPTURE_READY_URL": (
                f"http://127.0.0.1:{server.server_port}/api/health")},
            capture_output=True, text=True, timeout=10,
        )
        assert good.returncode == 0, good.stdout + good.stderr
        assert good.stdout.count("READY_EXIT=0") == 1
        assert fired["count"] == 1, (
            "the selected readiness endpoint did not fire exactly once"
        )

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            unavailable = probe.getsockname()[1]
        bad = subprocess.run(
            ["bash", "-c", driver],
            env={**os.environ, "CAPTURE_READY_URL": (
                f"http://127.0.0.1:{unavailable}/api/health")},
            capture_output=True, text=True, timeout=10,
        )
        assert bad.returncode == 1
        assert bad.stderr.count("WARNING: no answer from") == 1
        assert fired["count"] == 1, (
            "the negative control accidentally reached the healthy peer"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _code_only(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def test_capture_routes_seeding_live_artifacts_and_verdicts_by_instance() -> None:
    code = _code_only(CAPTURE)
    required = {
        'CAPTURE_READY_URL="${CAPTURE_APP_ORIGIN}/api/health"': 1,
        'CAPTURE_INSTALL_DIR="/proc/$$/fd/$CAPTURE_VAULT_DIR_FD/state"': 1,
        'CAPTURE_MODE="$(bd_capture_mode "$@")"': 1,
        'CAPTURE_APP_ORIGIN="http://localhost:5555"': 1,
        'CAPTURE_FIXTURE_ORIGIN="http://127.0.0.1:8899"': 1,
        'CAPTURE_UNIT_INSTANCE="bulkdownloader.service"': 1,
        'capture_vault_claim || exit 73': 1,
        '--base-url "$CAPTURE_APP_ORIGIN"': 2,
        '--fixture-origin "$CAPTURE_FIXTURE_ORIGIN"': 2,
        'tools/fixture_site.py --port "$CAPTURE_FIXTURE_PORT"': 1,
        '--url "$CAPTURE_APP_ORIGIN"': 1,
        '--db-path "$CAPTURE_INSTALL_DIR/downloader_history.db"': 1,
        '--results-dir "$OUT/06_live_results_source"': 1,
        'systemctl status "$CAPTURE_UNIT_INSTANCE"': 1,
        '"$CAPTURE_APP_ORIGIN/api/dev/$route"': 1,
        '"$CAPTURE_APP_ORIGIN/api/secrets/unlock"': 1,
        '--stage-exit "capture-instance-teardown=$CAPTURE_INSTANCE_TEARDOWN_EXIT"': 1,
    }
    assert required, "the routing denominator is empty"
    wrong = {
        anchor: (code.count(anchor), count)
        for anchor, count in required.items()
        if code.count(anchor) != count
    }
    assert not wrong, f"instance routing anchors have wrong counts: {wrong}"

    runtime_curls = [
        line.strip() for line in code.splitlines()
        if "curl " in line
    ]
    routed = code.count("$CAPTURE_APP_ORIGIN")
    assert routed >= 10, (
        f"only {routed} executable references use the per-run app origin"
    )
    fixed = [line for line in runtime_curls if ":5555" in line]
    assert not fixed, f"runtime verdict probes still target fixed :5555: {fixed}"

    heartbeat = _code_only(HEARTBEAT)
    for variable in ("CAPTURE_APP_PORT_LOCK_FD", "CAPTURE_FIXTURE_PORT_LOCK_FD"):
        assert heartbeat.count(f'_capture_add_close_fd "${{{variable}:-}}"') == 1, (
            f"detached capture children do not close {variable}; a dead parent "
            "could leave its port claim live in an unrelated descendant"
        )


def test_detached_children_close_both_port_claim_descriptors(
    tmp_path: Path,
) -> None:
    app_lock = tmp_path / "app.lock"
    fixture_lock = tmp_path / "fixture.lock"
    app_lock.touch(mode=0o600)
    fixture_lock.touch(mode=0o600)

    def run_probe(close_capture_fds: bool, log_name: str) -> subprocess.CompletedProcess[str]:
        assignments = ""
        if close_capture_fds:
            assignments = (
                'CAPTURE_APP_PORT_LOCK_FD="$app_fd"\n'
                'CAPTURE_FIXTURE_PORT_LOCK_FD="$fixture_fd"\n'
            )
        driver = (
            'source "$HEARTBEAT"\n'
            'exec {app_fd}<>"$APP_LOCK"; flock -n "$app_fd" || exit 73\n'
            'exec {fixture_fd}<>"$FIXTURE_LOCK"; flock -n "$fixture_fd" || exit 73\n'
            + assignments
            + 'export CHECK_FDS="$app_fd $fixture_fd"\n'
              'printf "PARENT_OPEN=%s\\n" "$(for fd in $CHECK_FDS; do '
              '[ -e /proc/$$/fd/$fd ] && printf x; done | wc -c)"\n'
              "_start_capture_detached \"$LOG\" bash -c '\n"
              '  checked=0; inherited=0\n'
              '  for fd in $CHECK_FDS; do\n'
              '    checked=$((checked + 1))\n'
              '    [ ! -e /proc/$$/fd/$fd ] || inherited=$((inherited + 1))\n'
              '  done\n'
              '  printf "%s,%s\\n" "$checked" "$inherited"\n'
              "'\n"
              'wait "$CAPTURE_DETACHED_PID"\n'
        )
        return subprocess.run(
            ["bash", "-c", driver],
            env={
                **os.environ,
                "HEARTBEAT": str(HEARTBEAT),
                "APP_LOCK": str(app_lock),
                "FIXTURE_LOCK": str(fixture_lock),
                "LOG": str(tmp_path / log_name),
            },
            capture_output=True, text=True, timeout=10,
        )

    closed = run_probe(True, "closed.log")
    assert closed.returncode == 0, closed.stdout + closed.stderr
    assert closed.stdout.count("PARENT_OPEN=2") == 1, (
        "the fixture did not actually open both descriptors in the parent"
    )
    assert (tmp_path / "closed.log").read_text() == "2,0\n", (
        "the detached child inherited one or both capture port claims"
    )

    control = run_probe(False, "inherited.log")
    assert control.returncode == 0, control.stdout + control.stderr
    assert control.stdout.count("PARENT_OPEN=2") == 1
    assert (tmp_path / "inherited.log").read_text() == "2,2\n", (
        "negative control did not prove descriptors are inherited without the "
        "capture-specific close list"
    )


def test_seeder_builds_every_url_from_the_requested_fixture_origin() -> None:
    sys.path.insert(0, str(REPO))
    from tools import live_seed

    origin = "http://127.0.0.1:43127"
    urls = [
        live_seed.seeded_url(index, origin)
        for index in range(len(live_seed._SEED_PATHS))
    ]
    assert len(urls) == 3 and all(url.startswith(origin + "/") for url in urls)
    login = live_seed.login_site_config(origin)
    routed = [login["login_url"], login["success_url"]]
    assert len(routed) == 2 and all(url.startswith(origin + "/") for url in routed)

    control = live_seed.seeded_url(0)
    assert control.startswith(live_seed.FIXTURE_ORIGIN + "/"), (
        "the operator-facing default was lost while adding the capture override"
    )


def test_live_runner_forwards_instance_db_and_results_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from live_tests import run

    seen = {}

    def fake_run_all(base_url, bd_home, **kwargs):
        seen.update(base_url=base_url, bd_home=bd_home, **kwargs)
        return 0

    monkeypatch.setattr(run.harness, "run_all", fake_run_all)
    db_path = tmp_path / "state" / "downloader_history.db"
    results = tmp_path / "results"
    rc = run.main([
        "--url", "http://127.0.0.1:43141",
        "--bd-home", str(REPO),
        "--db-path", str(db_path),
        "--results-dir", str(results),
        "--only", "L1",
    ])
    assert rc == 0
    assert seen["base_url"] == "http://127.0.0.1:43141"
    assert seen["db_path"] == str(db_path)
    assert seen["results_dir"] == str(results)
    assert seen["only"] == ["L1"]


def test_live_context_reads_the_explicit_instance_database(tmp_path: Path) -> None:
    from live_tests.harness import Context

    home = tmp_path / "checkout"
    home.mkdir()
    default_db = home / "downloader_history.db"
    instance_db = tmp_path / "instance" / "downloader_history.db"
    default_db.touch()
    instance_db.parent.mkdir()
    instance_db.touch()
    context = Context("http://127.0.0.1:1", home, db_path=instance_db)
    assert default_db.is_file() and instance_db.is_file(), (
        "the fixture did not build both database candidates"
    )
    assert context.db_path == instance_db
    assert context.db_path != default_db


def test_transform_control_imports_live_seed_without_asserting_fixture_routing() -> None:
    """Used only to prove a transformed module can import while its defect escapes."""
    from tools import live_seed

    assert live_seed.__name__ == "tools.live_seed"
