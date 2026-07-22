from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INSTALL = (ROOT / "install_service.sh").read_text(encoding="utf-8")
UNINSTALL = (ROOT / "uninstall_service.sh").read_text(encoding="utf-8")


def test_companion_unit_is_non_blocking_and_retries_forever():
    assert 'AI_SERVICE_NAME="bulkdownloader-ai-ready"' in INSTALL
    assert "ExecStart=${PYEXE} -m bulk_downloader.ai_boot_readiness" in INSTALL
    assert "Restart=on-failure" in INSTALL
    assert "RestartSec=60" in INSTALL
    assert "StartLimitIntervalSec=0" in INSTALL
    main_unit = INSTALL.split("Description=BulkDownloader (Flask", 1)[1].split("UNIT", 1)[0]
    assert "bulkdownloader-ai-ready" not in main_unit
    assert "ollama.service" not in main_unit


def test_companion_uses_same_user_directory_python_and_env():
    assert "User=${RUN_USER}" in INSTALL
    assert "WorkingDirectory=${APP_DIR}" in INSTALL
    assert "EnvironmentFile=-${APP_DIR}/.env" in INSTALL
    assert "ExecStart=${PYEXE} -m bulk_downloader.ai_boot_readiness" in INSTALL


def test_installer_enables_companion_but_start_failure_is_warning():
    assert 'systemctl enable "${AI_SERVICE_NAME}"' in INSTALL
    assert 'systemctl restart "${AI_SERVICE_NAME}"' in INSTALL
    assert "AI readiness will retry after boot" in INSTALL


def test_uninstaller_stops_disables_removes_and_resets_companion():
    for expected in (
        'AI_SERVICE_NAME="bulkdownloader-ai-ready"',
        'systemctl stop "$AI_SERVICE_NAME"',
        'systemctl disable "$AI_SERVICE_NAME"',
        'rm -f "$AI_UNIT_PATH"',
        'systemctl reset-failed "$AI_SERVICE_NAME"',
    ):
        assert expected in UNINSTALL


def test_uninstaller_dry_run_summary_includes_systemd_cleanup():
    for expected in (
        'ACTIONS+=("sudo systemctl daemon-reload")',
        'ACTIONS+=("sudo systemctl reset-failed $SERVICE_NAME")',
        'ACTIONS+=("sudo systemctl reset-failed $AI_SERVICE_NAME")',
    ):
        assert expected in UNINSTALL
