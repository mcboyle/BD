"""Row 718: a canonical but unwritable tree must have no install effects."""
import os

import pytest

from test_row715_installer_refuses_a_foreign_directory import InstallRun

BD_GATE_SCOPE = "repo-wide"


def test_unwritable_install_dir_refuses_before_effects(tmp_path):
    if os.geteuid() == 0:
        assert os.geteuid() == 0, "only root bypasses this mode-bit experiment"
        pytest.skip("root ignores directory mode bits; no writability measurement")
    run = InstallRun(tmp_path)
    run.app.chmod(0o500)
    assert not os.access(run.app, os.W_OK)
    # A chmod failure is separately injected: owning a helper still permits
    # chmod through an unwritable parent, so mode 500 alone cannot prove it.
    run.env["CHMOD_FAIL"] = "1"
    try:
        run.run().refused("INSTALL-DIR-NOT-WRITABLE")
    finally:
        run.app.chmod(0o700)


def test_writability_refusal_independent_of_helper_chmod(tmp_path):
    if os.geteuid() == 0:
        assert os.geteuid() == 0
        pytest.skip("root ignores directory mode bits")
    run = InstallRun(tmp_path)
    run.app.chmod(0o500)
    assert not os.access(run.app, os.W_OK)
    try:
        run.run().refused("INSTALL-DIR-NOT-WRITABLE")
    finally:
        run.app.chmod(0o700)


@pytest.mark.parametrize("failure", ["chmod", "helper", "missing"])
def test_helper_failure_is_deliberate(tmp_path, failure):
    run = InstallRun(tmp_path)
    assert os.access(run.app, os.W_OK)
    if failure == "chmod":
        run.env["CHMOD_FAIL"] = "1"
    elif failure == "helper":
        run.env["HELPER_RC"] = "1"
    else:
        run.helper.unlink()
        assert not run.helper.exists()
    run.run()
    if failure == "chmod":
        run.refused("HELPER-CHMOD-REFUSED")
        return
    # Version stamping is diagnostic: ExecStartPre has an explicit '-' and
    # serving does not require the stamp. Writability itself is mandatory.
    assert run.result.returncode == 0, run.out
    assert "WARNING" in run.out
    assert run.unit.is_file() and run.ai_unit.is_file()
    assert len([line for line in run.log.read_text().splitlines() if line.startswith("restart ")]) == 2
