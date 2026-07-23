"""Review-only corpus disposition bucket artifacts."""

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "corpus_disposition_buckets.py"


def _row(sha, status, errors):
    return {
        "sha256": sha * 64,
        "bytes": 123,
        "copy_count": 2,
        "paths": [f"/private/{sha}.wacz"],
        "read_only_preflight": {
            "normalized_status": status,
            "gate_errors": errors,
            "gate_error_count": len(errors),
        },
    }


def test_generator_buckets_only_review_required_rows_without_source_paths(tmp_path):
    source = {
        "unique_valid_wacz": [
            _row("a", "review_ready", []),
            _row("d", "draft_review_required", [
                "selectors.download must have a trigger or row_selectors"]),
            _row("c", "draft_review_required", [
                "network_patterns must be a non-empty list",
                "selectors.download must have a trigger or row_selectors"]),
            _row("b", "draft_review_required", ["resolutions list is empty"]),
        ]
    }
    source_path = tmp_path / "source.json"
    manifest_path = tmp_path / "buckets.json"
    report_path = tmp_path / "buckets.md"
    source_path.write_text(json.dumps(source), encoding="utf-8")

    run = subprocess.run(
        [sys.executable, str(TOOL), "--source", str(source_path),
         "--manifest-out", str(manifest_path), "--report-out", str(report_path)],
        cwd=ROOT, text=True, capture_output=True, check=False)

    assert run.returncode == 0, run.stderr
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["summary"] == {
        "auto_promotions": 0,
        "bucket_count": 3,
        "media_or_network_review": 1,
        "resolution_review": 1,
        "review_required": 3,
        "selector_review": 2,
    }
    assert [row["capture_sha256"] for row in payload["rows"]] == [
        "b" * 64, "c" * 64, "d" * 64]
    assert all(row["semantic_review_required"] is True for row in payload["rows"])
    assert all(row["auto_promotion"] is False for row in payload["rows"])
    serialized = manifest_path.read_text(encoding="utf-8")
    assert "/private/" not in serialized
    assert '"paths"' not in serialized
    assert "://" not in serialized
    assert "?" not in serialized
    assert "No auto-promotion" in report_path.read_text(encoding="utf-8")

    check = subprocess.run(
        [sys.executable, str(TOOL), "--source", str(source_path),
         "--manifest-out", str(manifest_path), "--report-out", str(report_path),
         "--check"],
        cwd=ROOT, text=True, capture_output=True, check=False)
    assert check.returncode == 0, check.stderr
    assert "no drift" in check.stdout
