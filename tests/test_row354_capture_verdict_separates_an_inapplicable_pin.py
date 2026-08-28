"""Row 354: graph-pin applicability is distinct from graph-content failure.

BD_GATE_SCOPE = "repo-wide"

The graph pin is deployment-local evidence.  These tests run capture.sh's real
graph-gate function and the real final-verdict assessor against isolated Git
trees, pins, and deploy records.  No test consults the host's /var/lib state.
"""
from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

from tools.capture_verdict import CaptureVerdict, assess_capture


BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "capture.sh"
_BLOCK_BEGIN = "# bd_graph_gate_function_begin"
_BLOCK_END = "# bd_graph_gate_function_end"
_REAL_PIN = Path(
    "/var/lib/bulkdownloader/validation/KNOWLEDGE_GRAPH.content.sha256"
)
_REAL_DEPLOY_RECORD = Path(f"{_REAL_PIN}.deploy-tree")
_NOT_APPLICABLE_EXIT = 4


def _git(source: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _make_source_tree(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "bulk_downloader").mkdir(parents=True)
    (source / "frontend" / "src").mkdir(parents=True)
    (source / "tools").mkdir(parents=True)
    (source / "tools" / "code_intelligence").mkdir()
    (source / "venv" / "bin").mkdir(parents=True)
    (source / "bulk_downloader" / "sample.py").write_text(
        "def answer():\n    return 42\n", encoding="utf-8"
    )
    (source / "frontend" / "src" / "sample.ts").write_text(
        "export const answer = 42;\n", encoding="utf-8"
    )
    shutil.copy2(ROOT / "tools" / "l0_extract.py", source / "tools")
    shutil.copy2(ROOT / "tools" / "graph_build.py", source / "tools")
    for name in (
        "__init__.py",
        "artifacts.py",
        "paths.py",
        "schemas.py",
        "snapshot.py",
    ):
        shutil.copy2(
            ROOT / "tools" / "code_intelligence" / name,
            source / "tools" / "code_intelligence" / name,
        )
    os.symlink(sys.executable, source / "venv" / "bin" / "python")
    _git(source, "init", "-q")
    _git(source, "config", "user.name", "Row 354 Test")
    _git(source, "config", "user.email", "row354@example.invalid")
    _git(
        source,
        "add",
        "bulk_downloader/sample.py",
        "frontend/src/sample.ts",
        "tools/l0_extract.py",
        "tools/graph_build.py",
        "tools/code_intelligence/__init__.py",
        "tools/code_intelligence/artifacts.py",
        "tools/code_intelligence/paths.py",
        "tools/code_intelligence/schemas.py",
        "tools/code_intelligence/snapshot.py",
    )
    _git(source, "commit", "-q", "-m", "row 354 fixture")
    return source


def _deploy_tree(source: Path) -> str:
    return _git(source, "rev-parse", "HEAD^{tree}")


def _deploy_record(pin: Path) -> Path:
    return Path(f"{pin}.deploy-tree")


def _assert_isolated(pin: Path, tmp_path: Path) -> Path:
    record = _deploy_record(pin)
    assert pin.is_relative_to(tmp_path)
    assert record.is_relative_to(tmp_path)
    assert pin != _REAL_PIN
    assert record != _REAL_DEPLOY_RECORD
    return record


def _write_current_pin(source: Path, pin: Path, scratch: Path) -> str:
    database = scratch / "pin-source.db"
    extracted = subprocess.run(
        [
            sys.executable,
            "tools/l0_extract.py",
            "--root",
            str(source),
            "--db",
            str(database),
        ],
        cwd=source,
        env={**os.environ, "PYTHONPATH": str(source)},
        capture_output=True,
        text=True,
    )
    assert extracted.returncode == 0, extracted.stdout + extracted.stderr
    written = subprocess.run(
        [
            sys.executable,
            "tools/graph_build.py",
            "--db",
            str(database),
            "--hash-pin",
            str(pin),
            "--write-hash",
        ],
        cwd=source,
        capture_output=True,
        text=True,
    )
    assert written.returncode == 0, written.stdout + written.stderr
    digest = pin.read_text(encoding="ascii").strip()
    assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
    return digest


def _graph_function() -> str:
    text = CAPTURE.read_text(encoding="utf-8")
    assert text.count(_BLOCK_BEGIN) == 1
    assert text.count(_BLOCK_END) == 1
    return text.split(_BLOCK_BEGIN, 1)[1].split(_BLOCK_END, 1)[0]


def _run_graph_gate(
    source: Path, pin: Path, temp_parent: Path
) -> subprocess.CompletedProcess[str]:
    script = (
        _graph_function()
        + "\nrun_graph_hash_gate\nrc=$?\necho GRAPH_RC=$rc\nexit $rc\n"
    )
    env = os.environ.copy()
    env.update(
        {
            "BD_GRAPH_HASH_PIN": str(pin),
            # Applicability comes from the deploy record, never this caller flag.
            "BD_REQUIRE_GRAPH_HASH": "1",
            "HOME": str(temp_parent.parent / "home"),
            "PYTHONPATH": str(source),
            "TMPDIR": str(temp_parent),
        }
    )
    return subprocess.run(
        ["bash", "-c", script],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
    )


def _clean_artifacts(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    unit = tmp_path / "unit.json"
    live = tmp_path / "live.log"
    unit.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "total": 1,
                "passed": 1,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
                "ok": True,
                "tests": [{"status": "pass"}],
            }
        ),
        encoding="utf-8",
    )
    live.write_text("1 pass | 0 warn | 0 fail (1 run)\n", encoding="utf-8")
    return unit, live


def _verdict(tmp_path: Path, graph_exit: int) -> CaptureVerdict:
    unit, live = _clean_artifacts(tmp_path)
    return assess_capture(
        unit,
        live,
        suite_exit=0,
        live_exit=0,
        stage_exits=[("graph", graph_exit)],
        expected_live_tests=1,
    )


def test_matching_pin_with_matching_deploy_record_is_ok(tmp_path):
    source = _make_source_tree(tmp_path)
    pin = tmp_path / "external" / "KNOWLEDGE_GRAPH.content.sha256"
    pin.parent.mkdir()
    record = _assert_isolated(pin, tmp_path)
    current_hash = _write_current_pin(source, pin, tmp_path)
    current_tree = _deploy_tree(source)
    record.write_text(current_tree + "\n", encoding="ascii")

    # Preconditions before verdict: both independent deployment-local records
    # exist and describe the source that the gate is about to measure.
    assert pin.is_file() and pin.read_text(encoding="ascii").strip() == current_hash
    assert record.is_file()
    assert record.read_text(encoding="ascii").strip() == current_tree
    temp_parent = tmp_path / "graph-tmp"
    temp_parent.mkdir()

    gate = _run_graph_gate(source, pin, temp_parent)
    verdict = _verdict(tmp_path, gate.returncode)

    assert gate.returncode == 0, gate.stdout + gate.stderr
    assert "graph check-hash: OK" in gate.stdout
    assert verdict.exit_code == 0
    assert "CAPTURE VERDICT: PASS" in verdict.summary
    assert "graph pin=MATCHES" in verdict.summary


def test_differing_pin_with_matching_deploy_record_is_loud_fail(tmp_path):
    source = _make_source_tree(tmp_path)
    pin = tmp_path / "external" / "KNOWLEDGE_GRAPH.content.sha256"
    pin.parent.mkdir()
    record = _assert_isolated(pin, tmp_path)
    current_hash = _write_current_pin(source, pin, tmp_path)
    stale_hash = "0" * 64 if current_hash != "0" * 64 else "1" * 64
    pin.write_text(stale_hash + "\n", encoding="ascii")
    current_tree = _deploy_tree(source)
    record.write_text(current_tree + "\n", encoding="ascii")

    # Preconditions before verdict: this exact tree has a deploy record, while
    # its independently computed graph hash differs from the deployed pin.
    assert record.read_text(encoding="ascii").strip() == current_tree
    assert pin.read_text(encoding="ascii").strip() == stale_hash
    assert current_hash != stale_hash
    temp_parent = tmp_path / "graph-tmp"
    temp_parent.mkdir()

    gate = _run_graph_gate(source, pin, temp_parent)
    verdict = _verdict(tmp_path, gate.returncode)

    assert gate.returncode == 1, gate.stdout + gate.stderr
    assert "graph check-hash: FAIL" in gate.stdout
    assert current_hash[:16] in gate.stdout
    assert stale_hash[:16] in gate.stdout
    assert verdict.exit_code == 1
    assert "CAPTURE VERDICT: FAIL" in verdict.summary
    assert "graph pin=DIFFERS" in verdict.summary


def test_absent_pin_is_unknown_not_applicable_with_distinct_exit(tmp_path):
    source = _make_source_tree(tmp_path)
    pin = tmp_path / "external" / "KNOWLEDGE_GRAPH.content.sha256"
    pin.parent.mkdir()
    record = _assert_isolated(pin, tmp_path)
    current_tree = _deploy_tree(source)
    record.write_text(current_tree + "\n", encoding="ascii")

    # The deploy record is deliberately valid so only the absent pin decides C.
    assert not pin.exists()
    assert record.read_text(encoding="ascii").strip() == current_tree
    temp_parent = tmp_path / "graph-tmp"
    temp_parent.mkdir()

    gate = _run_graph_gate(source, pin, temp_parent)
    verdict = _verdict(tmp_path, gate.returncode)

    assert gate.returncode == _NOT_APPLICABLE_EXIT, gate.stdout + gate.stderr
    assert "UNKNOWN / NOT-APPLICABLE" in gate.stdout
    assert "pin is absent" in gate.stdout
    assert verdict.exit_code == 2
    assert "CAPTURE VERDICT: UNKNOWN / NOT-APPLICABLE" in verdict.summary
    assert "CAPTURE VERDICT: FAIL" not in verdict.summary


def test_pin_from_a_different_deployed_tree_is_unknown_not_applicable(tmp_path):
    source = _make_source_tree(tmp_path)
    pin = tmp_path / "external" / "KNOWLEDGE_GRAPH.content.sha256"
    pin.parent.mkdir()
    record = _assert_isolated(pin, tmp_path)
    old_tree = _deploy_tree(source)
    record.write_text(old_tree + "\n", encoding="ascii")
    changed = source / "bulk_downloader" / "new_graph_content.py"
    changed.write_text("VALUE = 2\n", encoding="ascii")
    _git(source, "add", "bulk_downloader/new_graph_content.py")
    _git(source, "commit", "-q", "-m", "a tree this host never deployed")
    current_tree = _deploy_tree(source)
    current_hash = _write_current_pin(source, pin, tmp_path)

    # The hash itself matches.  Only recorded deploy provenance is stale, so a
    # hash-only implementation would wrongly report A/OK here.
    assert pin.read_text(encoding="ascii").strip() == current_hash
    assert old_tree != current_tree
    assert record.read_text(encoding="ascii").strip() == old_tree
    temp_parent = tmp_path / "graph-tmp"
    temp_parent.mkdir()

    gate = _run_graph_gate(source, pin, temp_parent)
    verdict = _verdict(tmp_path, gate.returncode)

    assert gate.returncode == _NOT_APPLICABLE_EXIT, gate.stdout + gate.stderr
    assert "UNKNOWN / NOT-APPLICABLE" in gate.stdout
    assert old_tree in gate.stdout
    assert current_tree in gate.stdout
    assert verdict.exit_code == 2
    assert "CAPTURE VERDICT: UNKNOWN / NOT-APPLICABLE" in verdict.summary
    assert "CAPTURE VERDICT: FAIL" not in verdict.summary


def test_matching_deploy_record_cannot_launder_real_hash_drift_into_unknown(tmp_path):
    source = _make_source_tree(tmp_path)
    pin = tmp_path / "external" / "KNOWLEDGE_GRAPH.content.sha256"
    pin.parent.mkdir()
    record = _assert_isolated(pin, tmp_path)
    current_hash = _write_current_pin(source, pin, tmp_path)
    different_hash = "f" * 64 if current_hash != "f" * 64 else "e" * 64
    pin.write_text(different_hash + "\n", encoding="ascii")
    current_tree = _deploy_tree(source)
    record.write_text(current_tree + "\n", encoding="ascii")

    # Negative-control preconditions: the only permitted B-versus-C
    # discriminator is positively present and matches this exact tree.
    assert record.read_text(encoding="ascii").strip() == current_tree
    assert pin.read_text(encoding="ascii").strip() != current_hash
    temp_parent = tmp_path / "graph-tmp"
    temp_parent.mkdir()

    gate = _run_graph_gate(source, pin, temp_parent)
    verdict = _verdict(tmp_path, gate.returncode)

    assert gate.returncode == 1, gate.stdout + gate.stderr
    assert gate.returncode != _NOT_APPLICABLE_EXIT
    assert "UNKNOWN / NOT-APPLICABLE" not in gate.stdout
    assert "graph pin=DIFFERS" in verdict.summary
    assert "CAPTURE VERDICT: FAIL" in verdict.summary


def test_successful_deploy_records_its_exact_tree_beside_the_pin():
    harness = runpy.run_path(
        str(ROOT / "tests" / "test_deploy_script.py"),
        run_name="_row354_deploy_harness",
    )
    fixture = harness["_setup"]()
    harness["_bundle_current"](fixture)
    pin = Path(fixture.env["BD_GRAPH_HASH_PIN"])
    record = _deploy_record(pin)

    # Preconditions before verdict: this is the isolated deploy harness, the
    # graph pin is external to its clone, and no earlier action made the record.
    assert pin.is_relative_to(Path(fixture.work))
    assert record.is_relative_to(Path(fixture.work))
    assert pin != _REAL_PIN and record != _REAL_DEPLOY_RECORD
    assert not record.exists()

    deployed = harness["_deploy"](fixture)
    current_tree = harness["_git"](
        fixture.clone, "rev-parse", "HEAD^{tree}"
    ).strip()

    assert deployed.returncode == 0, harness["_ctx"](deployed)
    assert record.is_file()
    assert record.read_text(encoding="ascii").strip() == current_tree


def test_transform_control_imports_verdict_without_judging_graph_state():
    """Mutation transform control: imports/paths constrain no state branch."""
    assert callable(assess_capture)
    assert CAPTURE.is_file()


def test_three_graph_pin_verdict_strings_are_pairwise_distinct(tmp_path):
    matching = _verdict(tmp_path / "matching", 0).summary
    differing = _verdict(tmp_path / "differing", 1).summary
    not_applicable = _verdict(tmp_path / "not-applicable", _NOT_APPLICABLE_EXIT).summary

    assert "graph pin=MATCHES" in matching
    assert "graph pin=DIFFERS" in differing
    assert "graph pin=NOT-APPLICABLE" in not_applicable
    assert len({matching, differing, not_applicable}) == 3
