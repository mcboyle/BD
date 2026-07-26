"""Deployment-local source-graph pin and release-hygiene regression tests.

The trusted pin lives outside the release tree.  ``capture.sh`` must derive a
fresh graph into a temporary directory, compare its logical content with that
pin, and remove the temporary database on every exit path.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CAPTURE = ROOT / "capture.sh"
_BLOCK_BEGIN = "# bd_graph_gate_function_begin"
_BLOCK_END = "# bd_graph_gate_function_end"


def _make_source_tree(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "bulk_downloader").mkdir(parents=True)
    (source / "frontend" / "src").mkdir(parents=True)
    (source / "tools").mkdir(parents=True)
    (source / "venv" / "bin").mkdir(parents=True)
    (source / "bulk_downloader" / "sample.py").write_text(
        "def answer():\n    return 42\n", encoding="utf-8")
    (source / "frontend" / "src" / "sample.ts").write_text(
        "export const answer = 42;\n", encoding="utf-8")
    shutil.copy2(ROOT / "tools" / "l0_extract.py", source / "tools")
    shutil.copy2(ROOT / "tools" / "graph_build.py", source / "tools")
    os.symlink(sys.executable, source / "venv" / "bin" / "python")
    return source


def _write_pin(source: Path, pin: Path, scratch: Path) -> None:
    db = scratch / "seed.db"
    subprocess.run(
        [sys.executable, "tools/l0_extract.py", "--root", str(source),
         "--db", str(db)], cwd=source, check=True, capture_output=True, text=True)
    subprocess.run(
        [sys.executable, "tools/graph_build.py", "--db", str(db),
         "--hash-pin", str(pin), "--write-hash"],
        cwd=source, check=True, capture_output=True, text=True)


def _graph_function() -> str:
    text = CAPTURE.read_text(encoding="utf-8")
    assert _BLOCK_BEGIN in text and _BLOCK_END in text, (
        "capture.sh must expose one testable run_graph_hash_gate function block")
    return text.split(_BLOCK_BEGIN, 1)[1].split(_BLOCK_END, 1)[0]


def _run_graph_gate(source: Path, pin: Path, temp_parent: Path, *, required: bool):
    script = _graph_function() + "\nrun_graph_hash_gate\nrc=$?\necho GRAPH_RC=$rc\nexit $rc\n"
    env = os.environ.copy()
    env.update({
        "BD_GRAPH_HASH_PIN": str(pin),
        "BD_REQUIRE_GRAPH_HASH": "1" if required else "0",
        "TMPDIR": str(temp_parent),
    })
    return subprocess.run(
        ["bash", "-c", script], cwd=source, env=env,
        capture_output=True, text=True)


def _assert_graph_tmp_clean(temp_parent: Path) -> None:
    assert list(temp_parent.glob("bd_graph.*")) == [], (
        "capture graph check leaked its temporary SQLite directory")


def test_explicit_hash_cli_invocations_remain_compatible(tmp_path):
    source = _make_source_tree(tmp_path)
    database = tmp_path / "explicit.db"
    pin = tmp_path / "external" / "explicit.content.sha256"
    pin.parent.mkdir()
    extract = subprocess.run(
        [
            sys.executable,
            "tools/l0_extract.py",
            "--root",
            str(source),
            "--db",
            str(database),
        ],
        cwd=source,
        capture_output=True,
        text=True,
    )
    assert extract.returncode == 0, extract.stdout + extract.stderr

    write = subprocess.run(
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
    assert write.returncode == 0, write.stdout + write.stderr
    assert "graph write-hash: wrote" in write.stdout

    check = subprocess.run(
        [
            sys.executable,
            "tools/graph_build.py",
            "--db",
            str(database),
            "--hash-pin",
            str(pin),
            "--check-hash",
        ],
        cwd=source,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert "graph check-hash: OK" in check.stdout


def test_matching_external_source_pin_succeeds_and_cleans_temp_db(tmp_path):
    source = _make_source_tree(tmp_path)
    pin = tmp_path / "external" / "KNOWLEDGE_GRAPH.content.sha256"
    pin.parent.mkdir()
    temp_parent = tmp_path / "tmp"
    temp_parent.mkdir()
    _write_pin(source, pin, tmp_path)

    result = _run_graph_gate(source, pin, temp_parent, required=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "graph check-hash: OK" in result.stdout
    _assert_graph_tmp_clean(temp_parent)


def test_source_mutation_fails_against_external_pin_and_cleans_temp_db(tmp_path):
    source = _make_source_tree(tmp_path)
    pin = tmp_path / "external" / "KNOWLEDGE_GRAPH.content.sha256"
    pin.parent.mkdir()
    temp_parent = tmp_path / "tmp"
    temp_parent.mkdir()
    _write_pin(source, pin, tmp_path)
    (source / "bulk_downloader" / "sample.py").write_text(
        "def answer():\n    return 43\n", encoding="utf-8")

    result = _run_graph_gate(source, pin, temp_parent, required=True)

    assert result.returncode != 0
    assert "graph check-hash: FAIL" in result.stdout
    _assert_graph_tmp_clean(temp_parent)


def test_missing_external_pin_fails_when_required_without_creating_db(tmp_path):
    source = _make_source_tree(tmp_path)
    pin = tmp_path / "missing" / "KNOWLEDGE_GRAPH.content.sha256"
    temp_parent = tmp_path / "tmp"
    temp_parent.mkdir()

    result = _run_graph_gate(source, pin, temp_parent, required=True)

    assert result.returncode != 0
    assert "required" in (result.stdout + result.stderr).lower()
    _assert_graph_tmp_clean(temp_parent)


def test_missing_external_pin_is_explicit_unknown_when_optional(tmp_path):
    source = _make_source_tree(tmp_path)
    pin = tmp_path / "missing" / "KNOWLEDGE_GRAPH.content.sha256"
    temp_parent = tmp_path / "tmp"
    temp_parent.mkdir()

    result = _run_graph_gate(source, pin, temp_parent, required=False)

    assert result.returncode == 0
    assert "UNKNOWN -- optional check not armed" in result.stdout
    _assert_graph_tmp_clean(temp_parent)


def test_capture_propagates_graph_exit_to_final_verdict():
    text = CAPTURE.read_text(encoding="utf-8")
    assert 'GRAPH_EXIT=$?' in text
    assert '--stage-exit "graph=$GRAPH_EXIT"' in text


def test_capture_rejects_unreadable_pin_and_cleanup_is_set_u_safe():
    text = CAPTURE.read_text(encoding="utf-8")
    assert '[ ! -r "$graph_pin" ]' in text
    assert '${graph_tmp:-}' in text


def test_release_excludes_exact_graph_database_and_pin_names():
    from bulk_downloader.dev_suite import _manifest_excluded

    excluded = (
        "KNOWLEDGE_GRAPH.db",
        "KNOWLEDGE_GRAPH.db-wal",
        "KNOWLEDGE_GRAPH.db-shm",
        "KNOWLEDGE_GRAPH.db-journal",
        "KNOWLEDGE_GRAPH.db.sha256",
        "KNOWLEDGE_GRAPH.content.sha256",
    )
    for name in excluded:
        assert _manifest_excluded(f"review/artifacts/{name}") is True, name

    assert _manifest_excluded("docs/KNOWLEDGE_GRAPH.md") is False
    assert _manifest_excluded("bulk_downloader/app.py") is False


def test_operator_policy_documents_external_pin_lifecycle():
    policy = (ROOT / "project-knowledge" / "OPERATOR_POLICY_DECISIONS.md").read_text(
        encoding="utf-8")
    assert "/var/lib/bulkdownloader/validation/KNOWLEDGE_GRAPH.content.sha256" in policy
    assert "BD_REQUIRE_GRAPH_HASH=1" in policy
    assert "immediately before" in policy.lower()
    assert "release_root=/home/mboyle/BulkDownloader" in policy
    assert "set -euo pipefail" in policy
    assert 'install -d -o root -g "$operator_group" -m 0750' in policy
    assert 'install -o root -g "$operator_group" -m 0640' in policy
    assert '"$graph_tmp/KNOWLEDGE_GRAPH.content.sha256"' in policy


def test_canonical_certification_command_requires_graph_pin():
    plan = (ROOT / "docs" / "superpowers" / "plans" /
            "2026-07-22-dependency-graph-hardening.md").read_text(encoding="utf-8")
    assert "BD_REQUIRE_GRAPH_HASH=1 DISPLAY=:99 ./capture.sh --workers=60 --summary" in plan
