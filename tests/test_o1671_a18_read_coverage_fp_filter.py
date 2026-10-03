"""o1671-a18: read_coverage._cited_lines must file-filter false_positive_confirmations.

AUDIT-18: the file filter applied only to `findings`, so every fp-confirmation for
ANY file added its line numbers to the target file's cited set, inflating coverage.
"""

import importlib.util
from pathlib import Path

BD_GATE_SCOPE = "module"

_SRC = Path(__file__).resolve().parents[1] / "tools" / "read_coverage.py"


def _load():
    spec = importlib.util.spec_from_file_location("read_coverage_a18", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fp_item_for_other_file_does_not_cite_target_lines():
    rc = _load()
    audit = {"false_positive_confirmations": [{"file": "pkg/other.py", "line_range": "120-130"}]}
    cited = rc._cited_lines(audit, "pkg/target.py")
    assert cited == set(), f"A18-FP-LEAK: fp item for pkg/other.py cited {sorted(cited)} on pkg/target.py"


def test_fp_item_for_target_file_still_cites_by_path_and_basename():
    rc = _load()
    audit = {
        "false_positive_confirmations": [
            {"file": "pkg/target.py", "line_range": "10-12"},
            {"file": "target.py", "at": "40"},
            {"file": "pkg/other.py", "line_range": "77"},
        ]
    }
    assert rc._cited_lines(audit, "pkg/target.py") == {10, 12, 40}


def test_findings_filter_unchanged():
    rc = _load()
    audit = {
        "findings": [
            {"file": "pkg/target.py", "line_range": "5-6"},
            {"file": "target.py", "line_range": "8"},
            {"file": "pkg/other.py", "line_range": "99"},
        ]
    }
    assert rc._cited_lines(audit, "pkg/target.py") == {5, 6, 8}
