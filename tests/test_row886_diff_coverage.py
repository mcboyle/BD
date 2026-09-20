"""Cut 886: git diff coverage analyzer contract tests."""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import importlib.machinery
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_PATH = REPO_ROOT / "toolchain" / "bin" / "bd-diff-coverage"


def _load_diff_coverage_module():
    assert TOOL_PATH.is_file(), f"missing toolchain binary: {TOOL_PATH}"
    loader = importlib.machinery.SourceFileLoader("diff_coverage", str(TOOL_PATH))
    spec = importlib.util.spec_from_loader("diff_coverage", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_diff_analyzer_identifies_uncovered_lines():
    """Verify diff coverage analyzer parses diff and identifies uncovered lines."""
    mod = _load_diff_coverage_module()

    diff_content = """diff --git a/bulk_downloader/example.py b/bulk_downloader/example.py
--- a/bulk_downloader/example.py
+++ b/bulk_downloader/example.py
@@ -10,0 +11,4 @@
+def new_feature():
+    x = 1
+    y = 2
+    return x + y
"""
    # Coverage data where lines 11 and 12 were executed, but 13 and 14 were not
    coverage_data = {
        "bulk_downloader/example.py": {
            "executed_lines": [11, 12],
            "missing_lines": [13, 14],
        }
    }

    result = mod.analyze_diff_coverage(diff_content, coverage_data)
    assert result["covered_count"] == 2
    assert result["missing_count"] == 2
    assert result["missing_lines"]["bulk_downloader/example.py"] == [13, 14]
    assert result["coverage_ratio"] == 0.5


def test_gate_exits_nonzero_when_modified_line_lacks_test_execution():
    """Verify CLI gate exits nonzero (code 1) when diff has uncovered lines."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cov_file = Path(tmpdir) / "coverage.json"
        cov_file.write_text("""{
            "files": {
                "bulk_downloader/example.py": {
                    "executed_lines": [1, 2],
                    "missing_lines": [3]
                }
            }
        }""")
        diff_file = Path(tmpdir) / "patch.diff"
        diff_file.write_text("""diff --git a/bulk_downloader/example.py b/bulk_downloader/example.py
--- a/bulk_downloader/example.py
+++ b/bulk_downloader/example.py
@@ -1,1 +1,3 @@
+line 1
+line 2
+line 3
""")

        proc = subprocess.run(
            [sys.executable, str(TOOL_PATH), "--diff", str(diff_file), "--coverage", str(cov_file)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode != 0
        assert "UNCOVERED" in proc.stdout or "uncovered" in proc.stdout or "missing" in proc.stdout


def test_100_percent_covered_diff_passes_cleanly():
    """Verify 100% covered diff exits 0 cleanly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cov_file = Path(tmpdir) / "coverage.json"
        cov_file.write_text("""{
            "files": {
                "bulk_downloader/example.py": {
                    "executed_lines": [1, 2],
                    "missing_lines": []
                }
            }
        }""")
        diff_file = Path(tmpdir) / "patch.diff"
        diff_file.write_text("""diff --git a/bulk_downloader/example.py b/bulk_downloader/example.py
--- a/bulk_downloader/example.py
+++ b/bulk_downloader/example.py
@@ -1,1 +1,2 @@
+line 1
+line 2
""")

        proc = subprocess.run(
            [sys.executable, str(TOOL_PATH), "--diff", str(diff_file), "--coverage", str(cov_file)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0
        assert "100%" in proc.stdout or "PASS" in proc.stdout or "clean" in proc.stdout.lower()


# ── correctness REFUTE E1/E2 (2026-09-20): fail closed on absent coverage; parser resets per file ──

def test_empty_or_absent_coverage_is_uncovered_not_covered():
    """E1: an empty report, or a file the report never saw, makes every added line MISSING."""
    mod = _load_diff_coverage_module()
    diff_content = """diff --git a/pkg/new.py b/pkg/new.py
--- /dev/null
+++ b/pkg/new.py
@@ -0,0 +1,3 @@
+a = 1
+b = 2
+c = 3
"""
    empty = mod.analyze_diff_coverage(diff_content, {})
    assert (empty["covered_count"], empty["missing_count"]) == (0, 3), empty
    assert empty["missing_lines"] == {"pkg/new.py": [1, 2, 3]}
    assert empty["coverage_ratio"] == 0.0
    other_file_only = mod.analyze_diff_coverage(diff_content, {"files": {"pkg/other.py": {"executed_lines": [1, 2, 3]}}})
    assert other_file_only["missing_count"] == 3, other_file_only
    # a line neither executed nor listed as missing is still missing (only executions count)
    partial = mod.analyze_diff_coverage(diff_content, {"pkg/new.py": {"executed_lines": [1], "missing_lines": []}})
    assert (partial["covered_count"], partial["missing_count"]) == (1, 2), partial


def test_gate_refuses_when_the_coverage_report_is_absent():
    """E1 at the CLI: no report -> rc 2 and COULD NOT LOOK, never rc 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        diff_file = Path(tmpdir) / "patch.diff"
        diff_file.write_text("""diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1,0 +1,1 @@
+line
""")
        proc = subprocess.run(
            [sys.executable, str(TOOL_PATH), "--diff", str(diff_file), "--coverage", str(Path(tmpdir) / "missing.json")],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 2, proc.stdout
        assert "COULD NOT LOOK" in proc.stdout and "UNKNOWN" in proc.stdout


def test_parser_resets_on_file_boundaries_and_ignores_deleted_files():
    """E2: '+++ /dev/null' (a deleted file) must not append a phantom line to the previous file."""
    mod = _load_diff_coverage_module()
    diff_content = """diff --git a/a.py b/a.py
--- /dev/null
+++ b/a.py
@@ -0,0 +1,2 @@
+one
+two
diff --git a/b.py b/b.py
deleted file mode 100644
--- a/b.py
+++ /dev/null
@@ -1,2 +0,0 @@
-gone
-gone too
diff --git a/c.py b/c.py
--- a/c.py
+++ b/c.py
@@ -5,1 +5,2 @@
 kept
+added at 6
"""
    assert mod.parse_diff_added_lines(diff_content) == {"a.py": [1, 2], "c.py": [6]}
