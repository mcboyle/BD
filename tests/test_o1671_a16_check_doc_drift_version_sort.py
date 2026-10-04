import json

import pytest
from tools import check_doc_drift

BD_GATE_SCOPE = "module"


@pytest.mark.parametrize("older,newer", [(9, 10), (99, 100), (999, 1000), (9999, 10000), (10, 11)])
def test_scan_selects_numeric_newest_handoff_without_writing(tmp_path, older, newer):
    for patch in (newer, older):
        (tmp_path / f"KB_HANDOFF_v3_66_{patch}.md").write_text(f"handoff {patch}\n")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = check_doc_drift.scan(str(tmp_path))
    assert result["newest_handoff_in_tree"] == f"KB_HANDOFF_v3_66_{newer}.md", "NUMERIC_HANDOFF_NEWEST_MISMATCH"
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("patch", [None, 999])
def test_no_or_single_handoff_control(tmp_path, patch):
    expected = None if patch is None else f"KB_HANDOFF_v3_66_{patch}.md"
    if expected:
        (tmp_path / expected).write_text("single\n")
    assert check_doc_drift.scan(str(tmp_path))["newest_handoff_in_tree"] == expected


def test_cli_json_reports_numeric_newest_and_keeps_required_doc_gate(tmp_path, capsys):
    (tmp_path / "bulk_downloader").mkdir()
    (tmp_path / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.1000"\n')
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("guide\n")
    for name in check_doc_drift.REQUIRED:
        (tmp_path / name).write_text("required\n")
    for patch in (999, 1000):
        (tmp_path / f"KB_HANDOFF_v3_66_{patch}.md").write_text("handoff\n")
    assert check_doc_drift.main(["--root", str(tmp_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["newest_handoff_in_tree"] == "KB_HANDOFF_v3_66_1000.md", "NUMERIC_HANDOFF_CLI_MISMATCH"
    assert report["version"] == "3.66.1000" and all(report["required"].values())
    (tmp_path / "README.md").unlink()
    assert check_doc_drift.main(["--root", str(tmp_path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["required"]["README.md"] is False
