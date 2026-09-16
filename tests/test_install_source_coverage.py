"""Linux installation sources: live launch targets, tracking and branch rules."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]


def census():
    path = ROOT / "tools/install_source_coverage.py"
    assert path.is_file(), "Linux install source census is missing"
    spec = importlib.util.spec_from_file_location("install_source_coverage", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(root, path, content=""):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def test_supported_population_is_nonzero_and_complete():
    result = census().audit(ROOT)
    assert len(result["entries"]) == 7
    assert len(result["sources"]) == 34
    assert sum(source["required"] for source in result["sources"]) == 26
    assert len({source["target"] for source in result["sources"]}) == 18
    assert not result["errors"], result["errors"]


@pytest.mark.parametrize("mode", ["missing", "untracked", "unresolved", "escape"])
def test_required_source_failure_names_owner_and_target(tmp_path, mode):
    target = {"unresolved": "$UNBOUND/helper.sh", "escape": "../helper.sh"}.get(mode, "helper.sh")
    write(tmp_path, "entry.sh", '. "' + target + '"\n')
    if mode != "missing":
        write(tmp_path, "helper.sh")
    tracked = {"entry.sh"} | ({"helper.sh"} if mode != "untracked" else set())
    result = census().audit(tmp_path, entries=["entry.sh"], tracked=tracked)
    assert len(result["errors"]) == 1, result
    assert "entry.sh" in result["errors"][0] and "helper.sh" in result["errors"][0]


def test_empty_supported_population_fails(tmp_path):
    result = census().audit(tmp_path, entries=[], tracked=set())
    assert result["errors"] == ["supported Linux entry set is empty"]


def test_new_required_launch_target_is_discovered(tmp_path):
    write(tmp_path, "entry.sh", 'bash ./child.sh\npython3 "$ROOT/missing.py"\n')
    write(tmp_path, "child.sh", '. "fragment.sh"\n')
    write(tmp_path, "fragment.sh")
    result = census().audit(tmp_path, entries=["entry.sh"],
                            tracked={"entry.sh", "child.sh", "fragment.sh"})
    assert len(result["sources"]) == 3
    assert len(result["errors"]) == 1 and "missing.py" in result["errors"][0]


def test_installer_optional_fragment_is_provisioner_required(tmp_path):
    for name in ("install_linux.sh", "scripts/provision_test_host.sh"):
        write(tmp_path, name, (ROOT / name).read_text())
    tool = census()
    installer = tool.audit(tmp_path, entries=["install_linux.sh"], tracked={"install_linux.sh"})
    provisioner = tool.audit(tmp_path, entries=["scripts/provision_test_host.sh"],
                             tracked={"scripts/provision_test_host.sh"})
    assert not any("system_deps.sh" in error for error in installer["errors"]), installer
    assert sum("system_deps.sh" in error for error in provisioner["errors"]) == 1
    assert sum("dev_capabilities.sh" in error for error in provisioner["errors"]) == 1


def test_changed_optional_absence_branch_is_not_silently_trusted(tmp_path):
    text = (ROOT / "install_linux.sh").read_text()
    old = '    echo "  (system packages skipped: scripts/lib/system_deps.sh not present)"'
    assert text.count(old) == 1
    write(tmp_path, "install_linux.sh", text.replace(old, "    exit 99"))
    result = census().audit(tmp_path, entries=["install_linux.sh"], tracked={"install_linux.sh"})
    assert any("optional branch changed" in error and "system_deps.sh" in error
               for error in result["errors"]), result


def test_complete_required_graph_passes_and_ignores_generated_external_windows(tmp_path):
    write(tmp_path, "entry.sh", 'python3 app.py\n# install_dev.bat\n# install_windows.bat\n'
          'python3 -m venv venv\nprintf "%s" "$HOSTS_FILE"\n')
    write(tmp_path, "app.py")
    result = census().audit(tmp_path, entries=["entry.sh"], tracked={"entry.sh", "app.py"})
    assert result["errors"] == []
    assert len(result["sources"]) == 1


def test_invoked_ai_bootstrap_is_included(tmp_path):
    write(tmp_path, "entry.sh", "bash install_ai_ollama.sh\n")
    result = census().audit(tmp_path, entries=["entry.sh"], tracked={"entry.sh"})
    assert len(result["sources"]) == 1
    assert "install_ai_ollama.sh" in result["errors"][0]


def test_tracked_symlink_cannot_resolve_to_untracked_source(tmp_path):
    write(tmp_path, "entry.sh", 'python3 app.py\n')
    write(tmp_path, "private.py")
    (tmp_path / "app.py").symlink_to("private.py")
    result = census().audit(tmp_path, entries=["entry.sh"], tracked={"entry.sh", "app.py"})
    assert len(result["errors"]) == 1
    assert "resolves outside tracked files" in result["errors"][0]


def test_diagnostics_and_interpreter_arguments_are_not_launches(tmp_path):
    write(tmp_path, "entry.sh", 'python3 app.py\n'
          'echo "bash missing.sh"\n'
          'bd_ensure_download_dirs "$VENV_PY" "$DIR/sites_config.json"\n'
          'if [ -x "venv/bin/python" ]; then :; fi\n')
    write(tmp_path, "app.py")
    result = census().audit(tmp_path, entries=["entry.sh"], tracked={"entry.sh", "app.py"})
    assert result["errors"] == [] and len(result["sources"]) == 1


def test_optional_missing_source_does_not_exempt_different_required_entry(tmp_path):
    write(tmp_path, "install_linux.sh", (ROOT / "install_linux.sh").read_text())
    write(tmp_path, "entry.sh", '. "scripts/lib/system_deps.sh"\n')
    result = census().audit(tmp_path, entries=["install_linux.sh", "entry.sh"],
                            tracked={"install_linux.sh", "entry.sh"})
    assert len(result["errors"]) == 1
    assert "entry.sh" in result["errors"][0] and "system_deps.sh" in result["errors"][0]


def test_root_variable_name_does_not_override_its_actual_owner(tmp_path):
    write(tmp_path, "entry.sh", 'REPO=/outside\n. "$REPO/helper.sh"\n')
    write(tmp_path, "helper.sh")
    result = census().audit(tmp_path, entries=["entry.sh"], tracked={"entry.sh", "helper.sh"})
    assert len(result["errors"]) == 1
    assert "escapes checkout" in result["errors"][0]


@pytest.mark.parametrize("command", [".", "bash"])
def test_same_owner_later_required_launch_cannot_borrow_optional_branch(tmp_path, command):
    text = (ROOT / "install_linux.sh").read_text()
    write(tmp_path, "install_linux.sh", text + '\n' + command +
          ' "$INSTALL_DIR/scripts/lib/system_deps.sh"\n')
    result = census().audit(tmp_path, entries=["install_linux.sh"], tracked={"install_linux.sh"})
    assert len(result["errors"]) == 1, result
    assert "system_deps.sh" in result["errors"][0]


def test_live_entry_reference_must_resolve(tmp_path):
    tool = census()
    policy = json.loads(tool.POLICY.read_text())
    policy["entries"]["install_linux.sh"]["anchor"] = "missing-live-install-entry-fixture"
    tool.POLICY = tmp_path / "policy.json"
    tool.POLICY.write_text(json.dumps(policy))
    result = tool.audit(ROOT)
    assert result["errors"] == ["install_linux.sh: supported entry reference is unresolved"]


@pytest.mark.parametrize("valid_root", [False, True])
def test_cli_reports_measurement_and_failure_exit_code(tmp_path, valid_root):
    root = ROOT if valid_root else tmp_path
    process = subprocess.run([sys.executable, str(ROOT / "tools/install_source_coverage.py"),
                              "--root", str(root)], text=True, capture_output=True, check=False)
    assert process.returncode == (0 if valid_root else 1), process.stderr
    report = json.loads(process.stdout)
    assert bool(report["errors"]) is not valid_root
    assert len(report["sources"]) == (34 if valid_root else 0)
