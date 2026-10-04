"""o1698-c9-lint-rules: the PostToolUse lint hook runs the O1707 (2) C9 ruleset only.

C9 = ruff F + E9 (`ruff check --select F,E9`) plus `ruff format --check --diff`; before this cut the hook ran ruff with
its defaults (O1702: I, UP, BLE, S, PLW ...), so every edit of a large file showed pre-existing style findings.
The hook stays advisory (exit 0), keeps one 5 s budget, and a failing or timed-out linter is never reported clean.

The candidate lives outside the repo (harness-cut shape, O1045). Opt in with
BD_O1698_C9_LINT_RULES_CANDIDATE=<candidate bd-lint-hook.py>. Uses the real ruff on PATH (or ~/.local/bin);
fixtures are under tmp_path, stubs are tiny shell scripts. Nothing outside tmp_path is written.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_O1698_C9_LINT_RULES_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

LONG = 'x = "' + "a" * 120 + '"\n'


def _hook() -> Path:
    p = Path(CANDIDATE)
    assert p.is_file(), f"candidate missing: {p}"
    return p


def _lint(path: Path, **env) -> tuple[int, str, float]:
    """Run the hook on one edited file -> (rc, additionalContext or '', seconds)."""
    e = dict(os.environ, **env)
    ev = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(path)}})
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(_hook())], input=ev, capture_output=True, text=True, env=e, timeout=60)
    took = time.monotonic() - t0
    out = r.stdout.strip()
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""
    return r.returncode, ctx, took


def _stub(tmp_path: Path, name: str, body: str) -> Path:
    s = tmp_path / name
    s.write_text("#!/bin/sh\n" + body + "\n")
    s.chmod(0o755)
    return s


def test_f401_is_reported_and_the_ruleset_is_named(tmp_path):
    f = tmp_path / "unused.py"
    f.write_text("import os\n")
    rc, ctx, _ = _lint(f)
    assert rc == 0
    assert "unused.py:1:8: F401" in ctx, ctx
    assert "F,E9" in ctx, f"the output must name the C9 rule set: {ctx!r}"


def test_e501_only_file_is_not_reported(tmp_path):
    f = tmp_path / "long.py"
    f.write_text(LONG)
    rc, ctx, _ = _lint(f)
    assert rc == 0 and ctx == "", f"E501 is not in C9 (F,E9) and the file is formatted: {ctx!r}"


def test_project_config_cannot_widen_the_ruleset(tmp_path):
    (tmp_path / "ruff.toml").write_text('[lint]\nextend-select = ["E501", "I", "UP"]\n')
    f = tmp_path / "long.py"
    f.write_text("import sys\nimport os\n\nprint(os, sys)\n" + LONG)  # unsorted imports (I001) + E501, no F
    rc, ctx, _ = _lint(f)
    assert rc == 0 and ctx == "", f"a project extend-select must not leak past F,E9: {ctx!r}"


def test_bad_format_shows_the_diff(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def f( a,b ):\n  return a+b\n")
    rc, ctx, _ = _lint(f)
    assert rc == 0
    assert "-def f( a,b ):" in ctx and "+def f(a, b):" in ctx, ctx
    assert "would be reformatted" not in ctx, "the stderr summary is not a diff line"


def test_hanging_ruff_is_could_not_look(tmp_path):
    f = tmp_path / "unused.py"
    f.write_text("import os\n")
    hang = _stub(tmp_path, "ruff", "sleep 60")
    rc, ctx, took = _lint(f, BD_LINT_RUFF=str(hang))
    assert rc == 0 and took < 9, (rc, took)
    assert re.search(r"LINT: COULD NOT LOOK \(ruff[^)]*timed out", ctx), ctx


@pytest.mark.parametrize("body,expect", [
    ('[ "$1" = format ] && { echo "error: Failed to parse" >&2; exit 2; }; exit 0', r"ruff-format rc 2: error: Failed to parse"),
    ('[ "$1" = format ] && exit 1; exit 0', r"ruff-format rc 1, no diff"),
    ('[ "$1" = check ] && { echo "ruff failed" >&2; exit 2; }; exit 0', r"ruff rc 2: ruff failed"),
])
def test_a_failing_ruff_job_is_never_clean(tmp_path, body, expect):
    f = tmp_path / "x.py"
    f.write_text("x = 1\n")
    stub = _stub(tmp_path, "ruff", body)
    rc, ctx, _ = _lint(f, BD_LINT_RUFF=str(stub))
    assert rc == 0
    assert re.search(r"LINT: COULD NOT LOOK \(" + expect, ctx), ctx


def test_ruleset_lives_in_one_constant():
    src = _hook().read_text()
    assert len(re.findall(r'^RUFF_SELECT = "F,E9"(\s*#.*)?$', src, re.M)) == 1, "one RUFF_SELECT constant"
    assert src.count('"F,E9"') == 1, "the rule set literal appears only in the constant"
    assert '"--select", RUFF_SELECT' in src
