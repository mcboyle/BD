"""Row 377: an installed verifier distinguishes defect from unavailable instrument."""
from __future__ import annotations

import os
import re
import json
import shutil
import subprocess
import sys
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-template-verify"
_RESOLVER = _REPO / "toolchain" / "bin" / "_bd_work_tree.py"
_SWEEP = _REPO / "toolchain" / "bin" / "bd-sweep"
_DEPENDENCY = "playwright"
_BAD_TEMPLATE = "gamma_kosmos"
_GOOD_SELECTOR = "a[class*='DownloadOption'][href*='/movieaction/download/']"
_BAD_SELECTOR = "a[href*='.row377'"


def _installed_layout(tmp_path: Path) -> tuple[Path, Path]:
    """Copy the real package and installed command without ambient venv luck."""
    assert _TOOL.is_file(), f"precondition: tracked verifier is absent: {_TOOL}"
    assert _RESOLVER.is_file(), (
        f"precondition: checkout resolver is absent: {_RESOLVER}"
    )
    checkout = tmp_path / "checkout"
    package = checkout / "bulk_downloader"
    shutil.copytree(
        _REPO / "bulk_downloader",
        package,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    initialized = subprocess.run(
        ["git", "init", "-q"],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    assert not (checkout / "venv").exists(), (
        "precondition: isolated checkout unexpectedly carries an interpreter; "
        "the chosen subprocess would be replaced"
    )

    installed = tmp_path / "installed-suite"
    installed.mkdir()
    tool = installed / _TOOL.name
    shutil.copy2(_TOOL, tool)
    shutil.copy2(_RESOLVER, installed / _RESOLVER.name)
    pointer = installed / ".bd-work-tree"
    pointer.write_text(f"{checkout}\n", encoding="utf-8")
    pointer.chmod(0o600)
    assert tool.is_file() and os.access(tool, os.X_OK), (
        f"precondition: installed command is not executable: {tool}"
    )
    return tool, checkout


def _run_selftest(interpreter: Path, tool: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for inherited in ("BD_INSTALL_DIR", "BD_WORK_TREE", "PYTHONPATH"):
        env.pop(inherited, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [str(interpreter), str(tool), "--selftest"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _probe_import(interpreter: Path) -> str:
    program = (
        "import importlib,sys\n"
        "try:\n"
        f"    importlib.import_module({_DEPENDENCY!r})\n"
        "except Exception as exc:\n"
        f"    print(f'interpreter={{sys.executable}} import={_DEPENDENCY} "
        "available=False error={type(exc).__name__}: {exc}')\n"
        "else:\n"
        f"    print(f'interpreter={{sys.executable}} import={_DEPENDENCY} available=True')\n"
    )
    probe = subprocess.run(
        [str(interpreter), "-I", "-c", program],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    return probe.stdout.strip()


def _terminal_verdict(run: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    output = run.stdout + run.stderr
    lines = [line for line in output.splitlines() if line.startswith("SELFTEST ")]
    assert len(lines) == 1, (
        f"expected one machine-readable terminal verdict, found {lines!r}:\n{output}"
    )
    match = re.fullmatch(r"SELFTEST (PASS|FAIL|UNKNOWN): (.+)", lines[0])
    assert match is not None, f"malformed terminal verdict: {lines[0]!r}"
    return match.group(1), match.group(2)


def _ci_shard_accepts(run: subprocess.CompletedProcess[str]) -> bool:
    """The template-selectors shard certifies only a reasoned zero-status PASS."""
    try:
        verdict, _diagnostic = _terminal_verdict(run)
    except AssertionError:
        return False
    return run.returncode == 0 and verdict == "PASS"


def test_installed_selftest_passes_on_the_good_tree_with_a_nonzero_denominator(tmp_path: Path):
    tool, _checkout = _installed_layout(tmp_path)
    interpreter = Path(sys.executable)
    dependency = _probe_import(interpreter)
    assert f"interpreter={interpreter}" in dependency, dependency
    assert f"import={_DEPENDENCY} available=True" in dependency, (
        "precondition: good-tree interpreter cannot run the verifier: " + dependency
    )

    run = _run_selftest(interpreter, tool, tmp_path)

    verdict, diagnostic = _terminal_verdict(run)
    assert run.returncode == 0 and verdict == "PASS", run.stdout + run.stderr
    counts = re.search(r"templates=(\d+) selectors=(\d+)", diagnostic)
    assert counts is not None, diagnostic
    assert int(counts.group(1)) > 0 and int(counts.group(2)) > 0, diagnostic
    assert f"interpreter={interpreter}" in diagnostic, diagnostic
    assert "Traceback" not in run.stdout + run.stderr
    assert _ci_shard_accepts(run) is True


def test_installed_selftest_fails_and_names_the_seeded_bad_template(tmp_path: Path):
    tool, checkout = _installed_layout(tmp_path)
    source = checkout / "bulk_downloader" / "site_templates" / "_data_players.py"
    before = source.read_text(encoding="utf-8")
    assert before.count(_GOOD_SELECTOR) == 1, (
        "precondition: bad-template mutation anchor must occur exactly once"
    )
    after = before.replace(_GOOD_SELECTOR, _BAD_SELECTOR)
    assert after != before and after.count(_BAD_SELECTOR) == 1, (
        "precondition: seeded template bytes did not change exactly once"
    )
    source.write_text(after, encoding="utf-8")

    interpreter = Path(sys.executable)
    dependency = _probe_import(interpreter)
    assert f"interpreter={interpreter}" in dependency, dependency
    assert f"import={_DEPENDENCY} available=True" in dependency, (
        "precondition: bad-template control interpreter cannot run the verifier: "
        + dependency
    )
    run = _run_selftest(interpreter, tool, tmp_path)

    verdict, diagnostic = _terminal_verdict(run)
    assert run.returncode == 1 and verdict == "FAIL", run.stdout + run.stderr
    assert f"template={_BAD_TEMPLATE}" in diagnostic, diagnostic
    assert _BAD_SELECTOR in diagnostic, diagnostic
    assert "Traceback" not in run.stdout + run.stderr
    assert _ci_shard_accepts(run) is False


def test_installed_selftest_reports_unknown_when_its_interpreter_lacks_an_import(tmp_path: Path):
    tool, checkout = _installed_layout(tmp_path)
    bare_python = Path("/usr/bin/python3").resolve()
    assert bare_python.is_file() and os.access(bare_python, os.X_OK), (
        f"precondition: bare interpreter is unavailable: {bare_python}"
    )
    assert not (checkout / "venv" / "bin" / "python").exists(), (
        "precondition: verifier would re-exec instead of using the bare interpreter"
    )
    dependency = _probe_import(bare_python)
    assert f"interpreter={bare_python}" in dependency, dependency
    assert f"import={_DEPENDENCY} available=False" in dependency, (
        "precondition: chosen UNKNOWN interpreter unexpectedly satisfies the import: "
        + dependency
    )

    run = _run_selftest(bare_python, tool, tmp_path)

    verdict, diagnostic = _terminal_verdict(run)
    assert run.returncode == 2 and verdict == "UNKNOWN", run.stdout + run.stderr
    assert f"interpreter={bare_python}" in diagnostic, diagnostic
    assert f"import={_DEPENDENCY}" in diagnostic, diagnostic
    assert "ModuleNotFoundError" in diagnostic, diagnostic
    assert "Traceback" not in run.stdout + run.stderr
    assert _ci_shard_accepts(run) is False, (
        "template-selectors shard converted an unavailable verifier into success"
    )


def test_real_selftest_sweep_can_select_the_template_verifier():
    assert _SWEEP.is_file(), f"precondition: selftest consumer is absent: {_SWEEP}"
    env = os.environ.copy()
    for inherited in ("BD_INSTALL_DIR", "BD_WORK_TREE", "PYTHONPATH"):
        env.pop(inherited, None)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    run = subprocess.run(
        [
            sys.executable,
            str(_SWEEP),
            "--selftests",
            "--bin",
            str(_TOOL.parent),
            "--only",
            _TOOL.name,
            "--timeout",
            "30",
            "--json",
        ],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    payload = json.loads(run.stdout)
    assert payload["runnable"] == 1 and payload["cases"] == 1, payload
    assert payload["counts"]["PASS"] == 1, payload
    assert payload["counts"]["UNKNOWN"] == 0, payload


def test_real_selftest_sweep_preserves_unknown_as_a_distinct_non_success(tmp_path: Path):
    assert _SWEEP.is_file(), f"precondition: selftest consumer is absent: {_SWEEP}"
    tool = tmp_path / _TOOL.name
    tool.write_text(
        "#!/usr/bin/python3\n"
        "# accepts --selftest\n"
        "import sys\n"
        "sys.stdout.write('probe-prefix-without-newline')\n"
        "sys.stderr.write(\"SELFTEST UNKNOWN: interpreter=/usr/bin/python3 \"\n"
        "                 \"import=playwright reason=ModuleNotFoundError: \"\n"
        "                 \"No module named 'playwright'\\n\")\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    run = subprocess.run(
        [
            sys.executable,
            str(_SWEEP),
            "--selftests",
            "--bin",
            str(tmp_path),
            "--only",
            tool.name,
            "--timeout",
            "10",
            "--json",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert run.returncode != 0, run.stdout + run.stderr
    payload = json.loads(run.stdout)
    assert payload["runnable"] == 1 and payload["cases"] == 1, payload
    assert payload["counts"]["UNKNOWN"] == 1, payload
    assert payload["counts"]["PASS"] == 0, payload
    assert payload["failures"][0]["status"] == "UNKNOWN", payload
    assert "SELFTEST UNKNOWN" in payload["failures"][0]["why"], payload
