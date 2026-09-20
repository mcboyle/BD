"""Row 921 static template schema linter contract."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BD_GATE_SCOPE = "module"
_TOOL = Path(__file__).parents[1] / "toolchain" / "bin" / "bd-template-lint"


def _lint(tmp_path, source):
    module = tmp_path / "template.py"
    module.write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_TOOL), str(module)],
        text=True, capture_output=True, check=False,
    )


def test_valid_template_passes_and_bad_regex_fails(tmp_path):
    valid = _lint(tmp_path, 'ITEMS = [{"id": "ok", "name": "OK", "patterns": [r"ok\\.test"]}]')
    assert valid.returncode == 0, valid.stderr
    malformed = _lint(tmp_path, 'ITEMS = [{"id": "bad", "name": "Bad", "patterns": [r"("]}]')
    assert malformed.returncode != 0
    assert "invalid regex" in malformed.stderr


def test_required_template_attributes_are_enforced(tmp_path):
    result = _lint(tmp_path, 'ITEMS = [{"id": "missing-name", "patterns": []}]')
    assert result.returncode != 0
    assert "missing required attribute: name" in result.stderr


# ── FIXER (row921 REFUTE: lint must judge the pattern as the runtime compiles it) ──

import pytest


@pytest.mark.parametrize("pattern", [r"tube\Z", r"\Dtube", r"a\Sb", r"x\Wy", r"\Bfoo", r"\Atube"])
def test_escapes_that_change_meaning_under_lowercasing_are_refused(tmp_path, pattern):
    """accessors.py compiles pat.lower(): \\Z becomes the invalid \\z, and
    \\D \\S \\W \\B silently invert. Raw-compile success proves nothing."""
    res = _lint(tmp_path, 'ITEMS = [{"id": "t", "name": "T", "patterns": [r"%s"]}]' % pattern)
    assert res.returncode != 0, res.stdout + res.stderr
    assert "lowercases" in res.stdout + res.stderr or "INVALID as the runtime" in res.stdout + res.stderr


@pytest.mark.parametrize("pattern", [r"tube$", r"ok\.test", r"\d+tube", r"(?:www\.)?site\.test/\w+", r"a\\Db"])
def test_patterns_stable_under_lowercasing_pass(tmp_path, pattern):
    """Positive control: lower-case escapes, an escaped backslash before an
    upper-case letter (literal), and plain patterns are accepted."""
    res = _lint(tmp_path, 'ITEMS = [{"id": "t", "name": "T", "patterns": [r"%s"]}]' % pattern)
    assert res.returncode == 0, res.stdout + res.stderr


def test_lint_agrees_with_the_runtime_matcher_on_every_shipped_pattern():
    """The real _data_*.py templates: every pattern the linter passes compiles
    the way accessors.py compiles it (lowercased) -- no silent substring
    fallback can be reached from shipped data."""
    import re
    root = Path(__file__).parents[1] / "bulk_downloader" / "site_templates"
    res = subprocess.run([sys.executable, str(_TOOL)], capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    checked = 0
    for module in sorted(root.glob("_data_*.py")):
        ns = {}
        exec(compile(module.read_text(encoding="utf-8"), str(module), "exec"), ns)
        for item in ns.get("ITEMS") or ns.get("TEMPLATES") or []:
            for pat in item.get("patterns") or []:
                re.compile(pat.lower())
                checked += 1
    assert checked > 0
