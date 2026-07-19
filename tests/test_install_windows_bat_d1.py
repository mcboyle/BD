"""Regression test for the v3.63.9 D1 install_windows.bat bug.

Bug shape. Line 162 in v3.63.9 read:
    "%PYTHON_CMD%" -m venv "%VENV_DIR%"

With the D1 change (set "PYTHON_CMD=py -3.12"), the outer quotes
made cmd.exe parse the whole expansion as a single executable name
("py -3.12"), which doesn't exist. install_windows.bat aborted at
[3/7] on every Windows host that has the py launcher (i.e., the
documented target setup).

Fix shape:
    %PYTHON_CMD% -m venv "%VENV_DIR%"

(unquoted, so cmd parses the variable's contents as separate
tokens). Safe because PYTHON_CMD is only ever a launcher invocation
or a single command name — never a quoted path with spaces.

This test pins the corrected shape. If a future maintainer adds the
outer quotes back, the v3.63.9 bug returns silently — but this test
fails loudly.

No new _bat_lint rule for this — the v3.63.8 paren-aware lint
deliberately stays small (L3+L4 only; L5/L6 were retracted because
the rule whitelist would have boxed out valid corpus). The lesson
applies in reverse here: don't add a "no outer quotes around
multi-token variable" rule that might box out a different valid
pattern in another script. Pin the specific file instead.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BAT_PATH = REPO_ROOT / "install_windows.bat"


def test_install_windows_bat_exists():
    """Sanity: the file is shipped."""
    assert BAT_PATH.is_file(), f"{BAT_PATH} not found"


def test_install_windows_bat_venv_invocation_unquoted():
    """The venv-create line must NOT wrap %PYTHON_CMD% in quotes.

    v3.63.9 shipped:
        "%PYTHON_CMD%" -m venv "%VENV_DIR%"
    which collapses 'py -3.12' into one impossible executable.

    Correct form:
        %PYTHON_CMD% -m venv "%VENV_DIR%"
    """
    text = BAT_PATH.read_text(encoding="utf-8")
    bad = '"%PYTHON_CMD%" -m venv'
    good = "%PYTHON_CMD% -m venv"
    assert bad not in text, (
        f"install_windows.bat regressed to the v3.63.9 D1 bug: "
        f"the venv-create line wraps %PYTHON_CMD% in quotes, which "
        f"breaks `set \"PYTHON_CMD=py -3.12\"` because cmd treats "
        f"`\"py -3.12\"` as a single executable name. Remove the "
        f"outer quotes around %PYTHON_CMD%."
    )
    assert good in text, (
        f"install_windows.bat is missing the expected unquoted "
        f"venv-create line: `{good} ...`"
    )


def test_install_windows_bat_python_cmd_assignment_present():
    """The D1 assignment that motivated this whole class of bug must
    still be in place. If a maintainer removes it and falls back to
    `set PYTHON_CMD=py`, the bug doesn't reappear, but the test exists
    to keep this contract visible alongside its counterpart."""
    text = BAT_PATH.read_text(encoding="utf-8")
    # The 3.12-preference assignment from the D1 block.
    assert 'set "PYTHON_CMD=py -3.12"' in text, (
        "install_windows.bat is missing the D1 `set "
        "\"PYTHON_CMD=py -3.12\"` line. If D1 has been intentionally "
        "removed or reshaped, update or delete this test along "
        "with the change."
    )


def test_install_windows_bat_no_other_quoted_pythoncmd_invocations():
    """Defensive: scan the whole file for `"%PYTHON_CMD%"` to make
    sure no OTHER call site has the same bug shape. The fix is to a
    single line today, but the bug class is `"%PYTHON_CMD%"` anywhere
    in the file."""
    text = BAT_PATH.read_text(encoding="utf-8")
    quoted = text.count('"%PYTHON_CMD%"')
    assert quoted == 0, (
        f'install_windows.bat contains {quoted} occurrence(s) of '
        f'"%PYTHON_CMD%" (quoted). The D1 change makes PYTHON_CMD '
        f'a multi-token value (`py -3.12`); ANY quoted expansion '
        f'will break the same way the v3.63.9 venv-create call '
        f'broke. Remove the outer quotes.'
    )
