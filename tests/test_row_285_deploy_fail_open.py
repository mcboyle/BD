"""Row 285: deployment readiness must never collapse UNKNOWN into success.

This gate executes the five confirmed seams.  In particular, F04 invokes a
deploy script from inside a real git clone, then lets ``git reset --hard``
replace that same pathname.  The test therefore crosses the real open-inode
boundary; it is not a source-text approximation of self replacement.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import test_deploy_script as deploy_support


BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
CAPTURE_INSTALLER = REPO / "scripts" / "install_capture_service.sh"
DEV_CAPABILITIES = REPO / "scripts" / "lib" / "dev_capabilities.sh"
DOWNLOAD_DIRS = REPO / "scripts" / "lib" / "download_dirs.sh"


def _write_executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _combined(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def _installed_deploy(fx) -> subprocess.CompletedProcess[str]:
    installed = Path(fx.clone) / "scripts" / "deploy.sh"
    argv = [
        deploy_support.BASH,
        str(installed),
        "--dir",
        fx.clone,
        "--health-url",
        "http://deploy-test.invalid/api/health",
        "--timeout",
        "5",
        "--interval",
        "1",
    ]
    return subprocess.run(
        argv,
        env=fx.env,
        cwd=fx.work,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_f03_bare_failure_after_stop_attempts_exactly_one_recovery() -> None:
    """The already-confirmed F03 defense remains in this row's denominator."""
    fx = deploy_support._setup()
    deploy_support._bundle_current(fx)
    trigger = deploy_support._force_sweep_errexit(fx)

    result = deploy_support._deploy(fx)

    service_calls = deploy_support._lines(fx.logs["systemctl"])
    rm_log = deploy_support._read(fx.env["RM_LOG"])
    assert trigger.name in rm_log, "fixture did not fire the forced step-9 failure"
    assert service_calls.count("stop bulkdownloader") == 1, service_calls
    assert service_calls.count("start bulkdownloader") == 1, service_calls
    assert result.returncode != 0, _combined(result)
    assert _combined(result).count("RESTARTED-PARTIAL-DEPLOY") == 1


def test_f04_reset_hands_off_to_the_new_script_body_exactly_once() -> None:
    fx = deploy_support._setup()
    installed = Path(fx.clone) / "scripts" / "deploy.sh"
    incoming = Path(fx.seed) / "scripts" / "deploy.sh"
    old_body = installed.read_text(encoding="utf-8")
    anchor = 'note "tree version is $TREE_VERSION"'
    marker = 'note "ROW285-INCOMING-POST-RESET-BODY $TREE_VERSION"'
    assert old_body.count(anchor) == 1, "fixture anchor is not unique"
    assert old_body.count(marker) == 0, "old body already contains incoming marker"
    incoming_body = incoming.read_text(encoding="utf-8").replace(anchor, marker)
    assert incoming_body.count(marker) == 1, "incoming body lacks its marker"

    target = deploy_support._advance_origin(
        fx,
        "row285 incoming deploy body",
        rel="scripts/deploy.sh",
        text=incoming_body,
    )
    deploy_support._bundle_current(fx)
    before_inode = installed.stat().st_ino

    result = _installed_deploy(fx)

    after_body = installed.read_text(encoding="utf-8")
    assert deploy_support._head(fx.clone) == target, (
        "fixture never reset to the incoming commit"
    )
    assert installed.stat().st_ino != before_inode, (
        "git reset did not replace the invoked script's inode; this run cannot "
        "measure F04"
    )
    assert after_body.count(marker) == 1, (
        "the path does not name the incoming script after reset"
    )
    assert result.returncode == 0, _combined(result)
    fired = _combined(result).count("ROW285-INCOMING-POST-RESET-BODY")
    assert fired == 1, (
        f"incoming post-reset script body fired {fired} times, expected exactly "
        "1; the deploy reported success from the pre-reset inode\n"
        + _combined(result)
    )
    assert _combined(result).count("tree version is 3.66.848") == 0, (
        "the new script was called but the old inode resumed after it returned; "
        "the handoff must replace the old shell with exec\n" + _combined(result)
    )
    service_calls = deploy_support._lines(fx.logs["systemctl"])
    assert service_calls.count("stop bulkdownloader") == 1, service_calls
    assert service_calls.count("start bulkdownloader") == 1, service_calls

    # Negative control: no tree movement means no handoff claim.
    unchanged = deploy_support._setup()
    deploy_support._bundle_current(unchanged)
    unchanged_result = _installed_deploy(unchanged)
    assert unchanged_result.returncode == 0, _combined(unchanged_result)
    assert _combined(unchanged_result).count("post-reset handoff") == 0


def test_f04_bootstrap_delivery_fails_loudly_then_new_inode_succeeds() -> None:
    """The release delivering the handoff begins in a body that lacks it.

    A helper already sourced after reset must recognize that one-time stale
    inode, fail inside the protected stopped window, and let EXIT recovery make
    the host available. The next invocation starts from the delivered body and
    must complete normally.
    """
    current = deploy_support.SCRIPT.read_text(encoding="utf-8")
    handoff_anchor = (
        'if [ "$SAME" -eq 0 ]; then\n'
        '  note "post-reset handoff: executing deploy logic from $NEW before step 5"'
    )
    disabled = (
        'if false; then\n'
        '  note "post-reset handoff: executing deploy logic from $NEW before step 5"'
    )
    assert current.count(handoff_anchor) == 1, "handoff fixture anchor is not unique"
    pre_handoff = current.replace(handoff_anchor, disabled)
    assert pre_handoff.count(disabled) == 1

    fx = deploy_support._setup(deploy_source=pre_handoff)
    installed = Path(fx.clone) / "scripts" / "deploy.sh"
    assert installed.read_text(encoding="utf-8").count(disabled) == 1
    target = deploy_support._advance_origin(
        fx,
        "row285 delivers the inode handoff",
        rel="scripts/deploy.sh",
        text=current,
    )
    deploy_support._bundle_current(fx)
    before_inode = installed.stat().st_ino

    delivery = _installed_deploy(fx)

    service_calls = deploy_support._lines(fx.logs["systemctl"])
    assert deploy_support._head(fx.clone) == target
    assert installed.stat().st_ino != before_inode, (
        "fixture did not replace the invoked pre-handoff inode"
    )
    assert service_calls.count("stop bulkdownloader") == 1, service_calls
    assert delivery.returncode != 0, (
        "the invocation delivering the handoff reported success from the old "
        "inode\n" + _combined(delivery)
    )
    assert _combined(delivery).count("STALE-DEPLOY-SCRIPT-INODE") == 1, (
        "bootstrap failure did not name the stale inode boundary\n"
        + _combined(delivery)
    )
    assert service_calls.count("start bulkdownloader") == 1, service_calls
    assert _combined(delivery).count("RESTARTED-PARTIAL-DEPLOY") == 1
    assert "DEPLOY OK" not in _combined(delivery)

    # The path now names the delivered body. A second invocation therefore has
    # no stale descriptor and must run that body to a verified success.
    second = _installed_deploy(fx)
    assert second.returncode == 0, _combined(second)
    assert _combined(second).count("ALREADY CURRENT -- verified") == 1


def _capture_fixture(
    root: Path,
    *,
    unit_state: str = "active",
    unit_state_rc: int = 0,
    stop_rc: int = 0,
    curl_rc: int = 0,
) -> tuple[dict[str, str], Path, Path, Path, Path]:
    checkout = root / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    installer = scripts / CAPTURE_INSTALLER.name
    _write_executable(installer, CAPTURE_INSTALLER.read_text(encoding="utf-8"))
    (checkout / "downloader_ui.py").write_text("# fixture app\n", encoding="utf-8")

    fake_bin = root / "bin"
    fake_bin.mkdir()
    systemctl_log = root / "systemctl.jsonl"
    curl_log = root / "curl.jsonl"
    _write_executable(fake_bin / "sudo", '#!/bin/sh\nexec "$@"\n')
    _write_executable(
        fake_bin / "systemctl",
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['SYSTEMCTL_LOG'], 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "action = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "if action == 'stop':\n"
        "    raise SystemExit(int(os.environ['STOP_RC']))\n"
        "if action == 'is-active':\n"
        "    print(os.environ['UNIT_STATE'])\n"
        "    raise SystemExit(int(os.environ['UNIT_STATE_RC']))\n"
        "raise SystemExit(0)\n",
    )
    _write_executable(
        fake_bin / "curl",
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['CURL_LOG'], 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "raise SystemExit(int(os.environ['CURL_RC']))\n",
    )

    runtime = root / "run"
    unit = root / "bulkdownloader-capture@.service"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "SYSTEMCTL_LOG": str(systemctl_log),
        "CURL_LOG": str(curl_log),
        "STOP_RC": str(stop_rc),
        "UNIT_STATE": unit_state,
        "UNIT_STATE_RC": str(unit_state_rc),
        "CURL_RC": str(curl_rc),
        "CAPTURE_SERVICE_UNIT_PATH": str(unit),
        "CAPTURE_SERVICE_RUNTIME_DIR": str(runtime),
        "CAPTURE_SERVICE_PYTHON": sys.executable,
        "CAPTURE_SERVICE_READY_TRIES": "1",
        "CAPTURE_SERVICE_READY_INTERVAL": "0",
    }
    return env, runtime, installer, systemctl_log, curl_log


def _json_lines(path: Path) -> list[list[str]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_f05_start_requires_active_and_health_evidence(tmp_path: Path) -> None:
    bad_root = tmp_path / "bad"
    env, runtime, installer, systemctl_log, curl_log = _capture_fixture(
        bad_root, curl_rc=7
    )
    state = bad_root / "state"
    state.mkdir()

    bad = subprocess.run(
        [str(installer), "start", "run-bad", "42121", str(state)],
        env=env,
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=20,
    )

    calls = _json_lines(systemctl_log)
    restarts = [call for call in calls if call[:1] == ["restart"]]
    probes = _json_lines(curl_log)
    assert len(restarts) == 1, f"fixture fired {len(restarts)} restarts: {calls}"
    assert (runtime / "run-bad.env").is_file(), (
        "fixture never built the instance environment"
    )
    assert len(probes) == 1, (
        f"capture-service readiness probe fired {len(probes)} times, expected "
        "exactly 1"
    )
    assert "/api/health" in " ".join(probes[0]), probes
    assert bad.returncode != 0, _combined(bad)
    assert "started" not in bad.stdout.lower(), _combined(bad)

    good_root = tmp_path / "good"
    good_env, _, good_installer, good_systemctl, good_curl = _capture_fixture(
        good_root, curl_rc=0
    )
    good_state = good_root / "state"
    good_state.mkdir()
    good = subprocess.run(
        [str(good_installer), "start", "run-good", "42123", str(good_state)],
        env=good_env,
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert len([c for c in _json_lines(good_systemctl) if c[:1] == ["restart"]]) == 1
    assert len(_json_lines(good_curl)) == 1
    assert good.returncode == 0, _combined(good)
    assert good.stdout.lower().count("started") == 1


def test_f06_stop_preserves_state_when_unit_state_is_unknown(tmp_path: Path) -> None:
    unknown_root = tmp_path / "unknown"
    env, runtime, installer, systemctl_log, _ = _capture_fixture(
        unknown_root,
        unit_state="unknown",
        unit_state_rc=4,
        stop_rc=1,
    )
    runtime.mkdir()
    env_path = runtime / "run-unknown.env"
    env_path.write_text("fixture=owned\n", encoding="utf-8")

    unknown = subprocess.run(
        [str(installer), "stop", "run-unknown"],
        env=env,
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=20,
    )

    calls = _json_lines(systemctl_log)
    assert len([c for c in calls if c[:1] == ["stop"]]) == 1, calls
    assert len([c for c in calls if c[:1] == ["is-active"]]) == 1, calls
    assert unknown.returncode != 0, (
        "an unknown unit state was converted to inactive/success\n" + _combined(unknown)
    )
    assert "unknown" in _combined(unknown).lower(), _combined(unknown)
    assert env_path.is_file(), "unknown teardown deleted the recovery environment"

    inactive_root = tmp_path / "inactive"
    inactive_env, inactive_runtime, inactive_installer, inactive_log, _ = (
        _capture_fixture(
            inactive_root,
            unit_state="inactive",
            unit_state_rc=3,
            stop_rc=1,
        )
    )
    inactive_runtime.mkdir()
    inactive_path = inactive_runtime / "run-inactive.env"
    inactive_path.write_text("fixture=owned\n", encoding="utf-8")
    inactive = subprocess.run(
        [str(inactive_installer), "stop", "run-inactive"],
        env=inactive_env,
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert len([c for c in _json_lines(inactive_log) if c[:1] == ["is-active"]]) == 1
    assert inactive.returncode == 0, _combined(inactive)
    assert not inactive_path.exists()


def _psql_stub(root: Path) -> tuple[Path, Path]:
    fake_bin = root / "bin"
    fake_bin.mkdir(parents=True)
    log = root / "psql.log"
    _write_executable(
        fake_bin / "psql",
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$PSQL_LOG"\nexit 0\n',
    )
    return fake_bin, log


def _run_pg(fake_bin: Path, log: Path, home: Path) -> subprocess.CompletedProcess[str]:
    script = f'. "{DEV_CAPABILITIES}"\nSUDO=""\nbd_mod3_pg_provision\n'
    return subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "PSQL_LOG": str(log),
            "HOME": str(home),
        },
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_f07_pg_success_requires_dsn_persistence(tmp_path: Path) -> None:
    bad_bin, bad_log = _psql_stub(tmp_path / "bad-stub")
    bad_home = tmp_path / "home-is-a-file"
    bad_home.write_text("not a directory\n", encoding="utf-8")

    bad = _run_pg(bad_bin, bad_log, bad_home)

    psql_calls = bad_log.read_text(encoding="utf-8").splitlines()
    assert len(psql_calls) == 1, (
        f"fixture fired {len(psql_calls)} DSN checks, expected exactly 1"
    )
    assert "SELECT 1" in psql_calls[0]
    assert bad.returncode != 0, (
        "PostgreSQL provisioning reported success after DSN persistence failed\n"
        + _combined(bad)
    )
    assert "persist" in _combined(bad).lower(), _combined(bad)

    good_bin, good_log = _psql_stub(tmp_path / "good-stub")
    good_home = tmp_path / "good-home"
    good_home.mkdir()
    good = _run_pg(good_bin, good_log, good_home)
    persisted = good_home / ".config" / "bd" / "mod3.env"
    assert len(good_log.read_text(encoding="utf-8").splitlines()) == 1
    assert good.returncode == 0, _combined(good)
    assert persisted.read_text(encoding="utf-8") == (
        "export MOD3_PG_TEST_DSN="
        "postgresql://mod3_ci:mod3_ci_password@127.0.0.1:5432/mod3_ci\n"
    )


def test_transform_control_sources_dev_capabilities_without_testing_persistence() -> None:
    """Mutation transform control: loading definitions alone judges no effect."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'. "{DEV_CAPABILITIES}"; declare -F bd_mod3_pg_provision >/dev/null',
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, _combined(result)


def _run_download_dirs(
    config: Path,
    python: Path,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    script = f'. "{DOWNLOAD_DIRS}"; bd_ensure_download_dirs "$1" "$2"'
    return subprocess.run(
        ["bash", "-c", script, "row285-download-dirs", str(python), str(config)],
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_f08_unavailable_config_measurement_is_not_ready(tmp_path: Path) -> None:
    target = tmp_path / "downloads"
    config = tmp_path / "sites_config.json"
    config.write_text(
        json.dumps([{"name": "one", "download_dir": str(target)}]),
        encoding="utf-8",
    )
    population = json.loads(config.read_text(encoding="utf-8"))
    assert len(population) == 1 and population[0]["download_dir"] == str(target)
    assert not target.exists()

    parser_log = tmp_path / "parser.log"
    unavailable_python = _write_executable(
        tmp_path / "unavailable-python",
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$PARSER_LOG"\nexit 71\n',
    )
    result = _run_download_dirs(
        config,
        unavailable_python,
        env={"PARSER_LOG": str(parser_log)},
    )

    parser_calls = parser_log.read_text(encoding="utf-8").splitlines()
    assert len(parser_calls) == 1, (
        f"fixture fired {len(parser_calls)} config parses, expected exactly 1"
    )
    assert result.returncode != 0, (
        "unavailable config measurement reported download readiness\n"
        + _combined(result)
    )
    assert "unknown" in _combined(result).lower(), _combined(result)
    assert not target.exists()


def test_f08_unwritable_configured_directory_is_not_ready(tmp_path: Path) -> None:
    target = tmp_path / "downloads"
    target.mkdir()
    target.chmod(0o500)
    config = tmp_path / "sites_config.json"
    config.write_text(
        json.dumps([{"name": "one", "download_dir": str(target)}]),
        encoding="utf-8",
    )
    assert len(json.loads(config.read_text(encoding="utf-8"))) == 1
    assert target.stat().st_mode & 0o222 == 0, "fixture path still has write bits"
    try:
        result = _run_download_dirs(config, Path(sys.executable))
    finally:
        target.chmod(0o700)

    assert result.returncode != 0, (
        "an unwritable configured directory reported download readiness\n"
        + _combined(result)
    )
    assert "not writable" in _combined(result).lower(), _combined(result)
    assert list(target.iterdir()) == [], "write probe left an artifact behind"


def test_f08_writable_directory_is_a_real_negative_control(tmp_path: Path) -> None:
    target = tmp_path / "downloads" / "nested"
    config = tmp_path / "sites_config.json"
    config.write_text(
        json.dumps([{"name": "one", "download_dir": str(target)}]),
        encoding="utf-8",
    )
    assert not target.exists()

    result = _run_download_dirs(config, Path(sys.executable))

    assert result.returncode == 0, _combined(result)
    assert target.is_dir(), "configured directory was not created"
    probe = target / "row285-negative-control"
    probe.write_text("writable\n", encoding="utf-8")
    assert probe.read_text(encoding="utf-8") == "writable\n"
