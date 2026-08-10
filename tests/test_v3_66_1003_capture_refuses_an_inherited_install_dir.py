"""@1003. capture.sh refuses to run with BD_INSTALL_DIR inherited.

MEASURED TWICE, and it cost a whole capture each time.

  2026-08-09  the operator was told to run ad-hoc probes with an exported
              install-dir override, ran `export` in their interactive shell,
              and ./capture.sh inherited it. 89 tests failed -- 13 "database is
              locked", a UNIQUE violation, `assert 10 == 1`. The clean re-run
              was 12833 passed, and 12744 + 89 = 12833: EVERY failure was the
              variable.
  2026-08-10  the same shape produced six MOD3 Postgres failures and was
              reconstructed to the digit -- an "empty" source migrating exactly
              the 7 rows two earlier files had inserted.

WHY IT POISONS EVERYTHING: `db._resolve_db_path()` prefers the variable over the
working directory, so ONE value makes every test in the run share ONE SQLite
history database, defeating the per-test cwd isolation `tests/conftest.py`'s
autouse `isolated_bd_home` provides. Fresh per invocation, SHARED within it.

REFUSE, DO NOT QUIETLY UNSET. There is no legitimate way to run the suite with
it set process-wide, and a silent fix would hide a broken shell that will poison
the operator's next ad-hoc probe too. Failing costs one second; the alternative
costs a fifteen-minute capture and an hour of misattributed debugging.

Asserted over COMMENT-STRIPPED shell via tests/shell_source.py. The guard's own
explanatory comment necessarily names the variable, and a naive grep cannot tell
prose from code -- CLAUDE.md section 0 records four separate cuts where exactly
that failed a correct repair.
"""

import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
CAPTURE = REPO / "capture.sh"

sys.path.insert(0, str(REPO / "tests"))
from shell_source import blocks_containing, shell_code_only   # noqa: E402


def test_capture_script_exists_and_parses():
    assert CAPTURE.is_file()
    r = subprocess.run(["bash", "-n", str(CAPTURE)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_guard_is_CODE_not_only_a_comment():
    """A check that reads as present while being inert is the failure class
    this whole file exists for.

    Asserted as two independent facts rather than by walking the enclosing
    shell block. `blocks_containing` returns one line per hit for this
    construct -- the condition, then each heredoc line -- so "some block also
    contains `exit`" is FALSE for a correct implementation. That is CLAUDE.md
    section 0's line-scoped-assertion-about-a-shell-construct trap, and the
    first draft of this test walked straight into it."""
    code = shell_code_only(CAPTURE)
    assert '[ -n "${BD_INSTALL_DIR:-}" ]' in code, (
        "the guard's condition did not survive comment stripping -- it is prose")
    assert "exit 2" in code, "nothing in executable shell exits 2"


def test_the_refusal_is_FAST_so_it_costs_a_second_not_a_capture(tmp_path):
    """Guarding late would still let the operator wait fifteen minutes to be
    told their shell was wrong. The check has to be before any work."""
    import time
    env = dict(os.environ)
    env["BD_INSTALL_DIR"] = str(tmp_path)
    t0 = time.perf_counter()
    r = subprocess.run(["bash", str(CAPTURE), "--workers=2"], cwd=str(REPO),
                       env=env, capture_output=True, text=True, timeout=120)
    dt = time.perf_counter() - t0
    assert r.returncode == 2
    assert dt < 10, (
        "the refusal took %.1fs -- it is running work before the guard" % dt)


def test_it_REFUSES_when_the_variable_is_set(tmp_path):
    env = dict(os.environ)
    env["BD_INSTALL_DIR"] = str(tmp_path)
    r = subprocess.run(["bash", str(CAPTURE), "--workers=2"], cwd=str(REPO),
                       env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, (
        "expected a refusal (exit 2), got %d\n%s" % (r.returncode, r.stdout[-400:]))
    combined = r.stdout + r.stderr
    assert "REFUSING TO RUN" in combined
    assert str(tmp_path) in combined, "the refusal must name the offending value"
    assert "unset BD_INSTALL_DIR" in combined, "it must state the fix"


def test_it_DOES_NOT_fire_when_the_variable_is_absent():
    """THE OTHER DIRECTION, and the one that matters most. A guard that refuses
    unconditionally passes the test above and makes capture.sh unrunnable --
    over-sensitivity is a soundness bug here, not a safe default.

    The script is not expected to SUCCEED in this container (it wants
    ~/BulkDownloader and a venv beside it). What is asserted is only that it
    gets PAST the guard: exit 2 with the refusal banner must not happen."""
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)          # POP, do not merely leave unset --
    env["BD_SKIP_CAPTURE_VAULT"] = "1"       # the parent's value is part of the
    try:                                     # denominator (CLAUDE.md section 0)
        r = subprocess.run(["bash", str(CAPTURE), "--workers=2"], cwd=str(REPO),
                           env=env, capture_output=True, text=True, timeout=60)
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        # Getting far enough to hang IS passing the guard.
        out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        assert "REFUSING TO RUN" not in out
        return
    assert "REFUSING TO RUN" not in out, (
        "the guard fired with BD_INSTALL_DIR absent -- it would make capture.sh "
        "unrunnable for everyone")


def test_the_refusal_teaches_the_PREFIX_form_not_export():
    """The 2026-08-09 incident happened because the advice given was `export`.
    The refusal has to correct that, or the operator repeats it."""
    text = CAPTURE.read_text(encoding="utf-8")
    assert 'BD_INSTALL_DIR="$(mktemp -d)" venv/bin/python' in text or \
           'BD_INSTALL_DIR="\\$(mktemp -d)" venv/bin/python' in text, (
        "the refusal does not show the prefix form, which is the actual fix")
