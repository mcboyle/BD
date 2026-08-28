"""Row 336: absent audit/scan evidence is UNKNOWN, never clean.

The untouched v3.66.1313 sources produced these wrong-green verdicts:

* ``verify_audit`` printed ``ACCEPT -- schema+sha+witness+emit+completeness
  clean (1 advisory warning(s))`` and exited 0 with no witness suite.
* ``bd-scan`` printed ``bd-scan: 0 findings``, wrote ``total: 0`` with empty
  source counts, and exited 0 while all three default analyzer executables were
  absent.

The measured-empty controls below are load-bearing: they distinguish zero
findings reported by three analyzers from zero evidence produced by none.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_VERIFY = _REPO / "tools" / "verify_audit.py"
_SCAN = _REPO / "tools" / "bd-scan.py"


def _audit_fixture(
    tmp_path: Path, *, files=True, findings=True, witness="W-ROW336-1"
) -> Path:
    source = tmp_path / "measured.py"
    source.write_text("MEASURED = True\n", encoding="ascii")
    document = {
        "batch": "ROW336",
        "version": "3.66.1313",
        "files": ([{
            "path": source.name,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "rubric": {"measured": True},
        }] if files else []),
        "findings": ([{
            "id": "ROW336-FIXTURE-1",
            "file": source.name,
            "severity": "high",
            "witness": witness,
            "repro_test": "tests/test_row336_audit_and_scan_evidence.py",
        }] if findings else []),
        "guard_touch": False,
        "tracker_write": False,
        "tree_reverified_byte_identical": True,
    }
    audit = tmp_path / "AUDIT_ROW336.json"
    audit.write_text(json.dumps(document), encoding="ascii")
    advanced = tmp_path / "ROW336_advanced.json"
    advanced.write_text(
        json.dumps({"constraints": [], "exceptions": [], "beliefs": []}),
        encoding="ascii",
    )
    return audit


def _witness_suite(tmp_path: Path, witness_id: str) -> Path:
    suite = tmp_path / f"witness_{witness_id}.py"
    suite.write_text(
        "RESULTS = "
        + repr([{
            "id": witness_id,
            "kind": "finding",
            "ok": True,
            "detail": "measured",
        }])
        + "\n",
        encoding="ascii",
    )
    return suite


def _verify(audit: Path, witness: Path | None = None) -> subprocess.CompletedProcess:
    command = [
        sys.executable,
        str(_VERIFY),
        "--audit",
        str(audit),
        "--root",
        str(audit.parent),
        "--advanced",
        str(audit.parent / "ROW336_advanced.json"),
    ]
    if witness is not None:
        command.extend(("--witnesses", str(witness)))
    return subprocess.run(command, capture_output=True, text=True, check=False)


def test_a_live_sha_and_finding_without_a_witness_suite_is_rejected(tmp_path):
    result = _verify(_audit_fixture(tmp_path))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL  witness: no witness suite supplied" in result.stdout
    assert "REJECT --" in result.stdout
    assert "ACCEPT --" not in result.stdout


def test_a_finding_not_matched_by_the_loaded_witness_suite_is_rejected(tmp_path):
    audit = _audit_fixture(tmp_path)
    unrelated_suite = _witness_suite(tmp_path, "W-SOMEONE-ELSE")

    result = _verify(audit, unrelated_suite)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL  finding ROW336-FIXTURE-1 witness ref 'W-ROW336-1' not matched" in result.stdout
    assert "REJECT --" in result.stdout


def test_a_finding_id_in_reference_text_does_not_replace_a_suite_result(tmp_path):
    audit = _audit_fixture(tmp_path, witness="ROW336-FIXTURE-1")
    unrelated_suite = _witness_suite(tmp_path, "W-SOMEONE-ELSE")

    result = _verify(audit, unrelated_suite)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL  finding ROW336-FIXTURE-1 witness ref 'ROW336-FIXTURE-1' not matched" in result.stdout
    assert "REJECT --" in result.stdout


@pytest.mark.parametrize("missing_population", ["files", "findings"])
def test_an_empty_required_audit_population_is_rejected(tmp_path, missing_population):
    audit = _audit_fixture(
        tmp_path,
        files=missing_population != "files",
        findings=missing_population != "findings",
    )
    suite = _witness_suite(tmp_path, "ROW336-FIXTURE-1")

    result = _verify(audit, suite)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"FAIL  schema: '{missing_population}' must be a non-empty list" in result.stdout


def test_a_live_sha_and_finding_with_a_matching_witness_suite_is_accepted(tmp_path):
    audit = _audit_fixture(tmp_path)
    suite = _witness_suite(tmp_path, "ROW336-FIXTURE-1")

    result = _verify(audit, suite)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ACCEPT -- schema+sha+witness+emit+completeness clean" in result.stdout
    assert "REJECT --" not in result.stdout


def _scan_root(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "bulk_downloader").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "project-knowledge").mkdir()
    (root / "project-knowledge" / "DEFECT_PATTERN_SUPPRESSIONS.json").write_text(
        json.dumps({"schema": "bd-defect-suppressions/v1", "entries": []}),
        encoding="ascii",
    )
    (root / "bulk_downloader" / "measured.py").write_text(
        "MEASURED = True\n", encoding="ascii"
    )
    return root


def _run_scan(root: Path, venv: Path) -> tuple[subprocess.CompletedProcess, dict]:
    report = root.parent / "SCAN_FINDINGS.json"
    result = subprocess.run(
        [
            sys.executable,
            str(_SCAN),
            "--root",
            str(root),
            "--venv",
            str(venv),
            "--out",
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert report.is_file(), result.stdout + result.stderr
    return result, json.loads(report.read_text(encoding="utf-8"))


def test_zero_findings_from_three_unavailable_analyzers_is_unknown(tmp_path):
    root = _scan_root(tmp_path)
    missing_venv = tmp_path / "all-three-analyzers-are-absent"

    result, report = _run_scan(root, missing_venv)

    assert result.returncode == 2, result.stdout + result.stderr
    assert report["status"] == "UNKNOWN"
    assert report["total"] == 0
    assert report["by_source"] == {}
    assert report["analyzers"] == {
        "bandit": {"findings": 0, "status": "UNKNOWN"},
        "defect_patterns": {"findings": 0, "status": "UNKNOWN"},
        "vulture": {"findings": 0, "status": "UNKNOWN"},
    }
    assert "bd-scan: UNKNOWN" in result.stdout
    assert "bd-scan: CLEAN" not in result.stdout


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755)


def _measured_empty_venv(tmp_path: Path) -> Path:
    venv = tmp_path / "measured-empty-venv"
    binaries = venv / "bin"
    binaries.mkdir(parents=True)
    (binaries / "python").symlink_to(sys.executable)
    _write_executable(
        binaries / "bandit",
        "import json\nprint(json.dumps({'results': []}))\n",
    )
    _write_executable(binaries / "vulture", "# measured: no dead code\n")
    return venv


def test_three_measured_healthy_empty_analyzers_report_clean(tmp_path):
    root = _scan_root(tmp_path)
    measured_venv = _measured_empty_venv(tmp_path)

    result, report = _run_scan(root, measured_venv)

    assert result.returncode == 0, result.stdout + result.stderr
    assert report["status"] == "CLEAN"
    assert report["total"] == 0
    assert report["by_source"] == {}
    assert report["analyzers"] == {
        "bandit": {"findings": 0, "status": "MEASURED"},
        "defect_patterns": {"findings": 0, "status": "MEASURED"},
        "vulture": {"findings": 0, "status": "MEASURED"},
    }
    assert "bd-scan: CLEAN -- 0 findings" in result.stdout
    assert "UNKNOWN" not in result.stdout
