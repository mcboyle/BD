"""Contract tests for the shared system-dependency fragment and provisioner.

The change under test gives BD **one** source of truth for system packages
(``scripts/lib/system_deps.sh``) and an operator-facing provisioner
(``scripts/provision_test_host.sh``) that the box actually runs, so the
gui-parity inventory is regenerated outside the Claude-cloud session script.

Method note (CLAUDE.md 0/1). These tests **execute** the subject rather than
grep it. A grep for ``bd_system_pkgs`` matches a comment; sourcing the file and
calling the function proves the name resolves and returns the right list. Where
execution is genuinely impossible (a shell script's statement order) the
assertion is made over **comment-stripped code**, never over raw file text.

WHY THIS FILE WAS REWRITTEN. The previous version reported 32 passed against
two separately box-fatal mutations, measured by hand:

* adding a bogus package name to the ``gtk`` list -- invisible, because every
  package assertion was SUPERSET-only ("each expected name is present"), and a
  superset assertion cannot see an EXTRA name. apt-get is all-or-nothing, so one
  unknown name installs nothing at all;
* replacing ``bd_start_display``'s body with ``return 1`` -- invisible, because
  the only assertion was ``declare -F bd_start_display`` (it is DEFINED). A host
  then gets no display and ``test_v3_43_80_modules::test_all_modules_import``
  false-fails.

Both are the same defect in two costumes: a gate whose denominator structurally
excludes its subject reports OK, truthfully and uselessly. The corrections are
EXACT-SET package assertions and BEHAVIOURAL display assertions, plus four
things the old file could not see at all: group leakage, a leaked shell option,
capture.sh's *execution* order (as opposed to its byte offsets), and whether
shellcheck can parse the single source of truth.

The unknown-is-a-third-state rule drives the design here:

* ``_run_fragment`` reserves exit codes 97/98 for "could not source the
  fragment" and "sourced but the function is undefined". Every test asserts the
  result is *not* one of those before interpreting it. Without that guard the
  unknown-group test would pass on a tree with no fragment at all -- a missing
  file also produces a non-zero exit and empty stdout.
* Ordering assertions locate every anchor explicitly and fail with a distinct
  "anchor absent" message, because ``str.find`` returns ``-1`` for an absent
  needle and ``-1 < anything`` would certify an ordering that does not exist.
* The shellcheck gate SKIPS with a reason when shellcheck is absent rather than
  passing, and is backed by a tool-free structural check so its absence does not
  leave the D5 regression completely unguarded.

The inverse rule matters just as much: a gate that fires on identity gets
switched off. So the anti-drift scans run over CODE with comments stripped (a
maintainer writing "installs the GTK typelibs (python3-gi)" in a comment is not
a drift), and the shellcheck gate pins ``--severity=warning`` because the fixed
fragment still emits one genuine false-positive SC2317 (info) that a bare
exit-0 assertion would fail on forever.

Lane marker: none is declared on purpose. ``tests/conftest.py``
``pytest_collection_modifyitems`` assigns exactly one capture lane marker to
every collected item, and ``capture_lanes.classify_capture_file`` fails closed
to ``serial`` for any file outside ``tests/capture_parallel_files.txt`` -- which
this file is. Verified by calling the classifier directly
(``classify_capture_path("tests/test_provision_test_host.py") -> 'serial'``); no
repository test file declares an explicit ``capture_serial`` pytestmark.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import warnings

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]

FRAGMENT_REL = "scripts/lib/system_deps.sh"
FRAGMENT = REPO_ROOT / FRAGMENT_REL
PROVISIONER = REPO_ROOT / "scripts" / "provision_test_host.sh"
CLOUD_SETUP = REPO_ROOT / "scripts" / "cloud-setup.sh"
INSTALL_LINUX = REPO_ROOT / "install_linux.sh"
CAPTURE_SH = REPO_ROOT / "capture.sh"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

INVENTORY_TOOL = "tools/gui_parity_inventory.py"
PARALLEL_LANE_MARKER = "-m capture_parallel"

# Every shell file this cut touches. `bash -n` must parse all of them.
SHELL_FILES = (
    FRAGMENT,
    PROVISIONER,
    INSTALL_LINUX,
    CAPTURE_SH,
    CLOUD_SETUP,
)

# The contract's package groups, verbatim and EXACT. These are compared with
# `==`, not `issubset`: a superset assertion cannot see an added name, and one
# unknown name makes apt-get install NOTHING (all-or-nothing, exit 100), so an
# extra name is at least as fatal as a missing one.
#
# `x11-utils` is in `gtk` deliberately: xdpyinfo is the only probe in
# `_bd_display_active` that proves an X SERVER is answering, so a host this
# fragment provisions must be able to run the fragment's own best check.
EXPECTED_GROUPS: dict[str, tuple[str, ...]] = {
    "core": ("git", "python3.12", "python3.12-venv", "python3-pip"),
    "node": ("nodejs", "npm"),
    "gtk": (
        "xvfb",
        "libgtk-3-0t64",
        "gir1.2-gtk-3.0",
        "python3-gi",
        "libcairo2",
        "libgirepository-1.0-1",
        "x11-utils",
    ),
}

GROUP_ORDER = ("core", "node", "gtk")

ALL_PACKAGES = frozenset(
    name for names in EXPECTED_GROUPS.values() for name in names
)

# Package names that can ONLY be a package name in these shell files, so their
# absence from a consumer's CODE is a sound anti-drift signal. Measured against
# the tree: lowercase `xvfb` occurs only on package lines while the `Xvfb`
# occurrences are binary references -- so this comparison must stay
# case-sensitive. `x11-utils` occurs nowhere outside the fragment (measured
# repo-wide), so it belongs here rather than in AMBIGUOUS_PACKAGES.
DISCRIMINATING_PACKAGES = (
    "xvfb",
    "libgtk-3-0t64",
    "gir1.2-gtk-3.0",
    "python3-gi",
    "libcairo2",
    "libgirepository-1.0-1",
    "python3.12-venv",
    "python3-pip",
    "nodejs",
    "x11-utils",
)

# Deliberately NOT absence-checked: each is also a command or an ordinary word
# in these files (`git`, `python3.12` and `npm` are all invoked as programs).
# Asserting their absence would cry wolf, so
# `test_no_consumer_hardcodes_an_apt_package_list` covers them by checking apt
# argument positions instead. The two predicates together contain every package
# name in ALL_PACKAGES -- that is the denominator check, asserted in
# `test_anti_drift_predicates_cover_every_package_name`.
AMBIGUOUS_PACKAGES = ("git", "python3.12", "npm")

# Files that must never carry their own copy of the package lists.
CONSUMERS = (CLOUD_SETUP, INSTALL_LINUX, PROVISIONER)

_SOURCE_MISSING = 97
_FUNCTION_UNDEFINED = 98

# Resolved once, at import, BEFORE any test narrows PATH. The bd_start_display
# probes replace PATH entirely so the host's real /usr/bin/Xvfb cannot be found;
# looking the shell itself up through that PATH would fail with a
# FileNotFoundError that says nothing about the subject.
_BASH = shutil.which("bash") or "/bin/bash"
# Same reason, for the two externals the isolated probe toolbox has to provide:
# grep (probe 2's pipeline needs it) and sleep (copied to make a process whose
# /proc comm looks like an X server). Resolved at import for the same reason.
_GREP = shutil.which("grep")
_SLEEP = shutil.which("sleep")

# Matches `apt install`, `apt-get install`, `apt-get -y install`,
# `apt-get --yes install` and the cloud-setup helper `apt_i`.
_APT_INSTALL_RE = re.compile(
    r"\bapt(?:-get)?\b(?:\s+-{1,2}[^\s]+)*\s+install\b|\bapt_i\b"
)

# `NAME=value`, optionally preceded by export/local/readonly/declare.
_ASSIGN_RE = re.compile(
    r"^\s*(?:export\s+|local\s+|readonly\s+|declare\s+(?:-\w+\s+)*)?"
    r"([A-Za-z_][A-Za-z0-9_]*)=(.*)$"
)
_VAR_REF_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")

# Only these keys are real shellcheck directives. A comment that opens with the
# word "shellcheck" and is not one of them is SC1073/SC1072 and ABORTS the parse
# of the whole file -- which is how the single source of truth came to have zero
# lint coverage while every consumer still looked fine.
_SHELLCHECK_DIRECTIVE_KEYS = frozenset(
    {"shell", "enable", "source", "source-path", "disable", "external-sources"}
)
# No `\b` after "shellcheck", and that is not an oversight: shellcheck matches
# the literal prefix, case-sensitively, with no word boundary. Measured against
# shellcheck 0.9.0 -- `# shellcheckery is not a word` and `# shellcheck-ish note`
# BOTH abort with SC1072/SC1073, while `# SHELLCHECK disable=SC2086` does not.
# The `\s*` after `#` matches its tolerance too: `#shellcheck prose` and
# `#   shellcheck prose` both abort.
_SHELLCHECK_COMMENT_RE = re.compile(r"^\s*#\s*shellcheck(?P<rest>.*)$")
# A directive is a sequence of `key=value` tokens. The old predicate matched
# only the HEAD of the rest of the line, so `disable=SC2086 -- prose` parsed as
# key='disable' and was certified valid -- which is precisely the form that
# aborted install_linux.sh, scripts/cloud-setup.sh and the provisioner in this
# branch while the backstop reported clean.
_SHELLCHECK_TOKEN_RE = re.compile(r"^(?P<key>[a-z][a-z-]*)=(?P<value>\S+)$")

# shellcheck codes that mean "this file (or a file it sources) did not parse".
# These are the D5 signature and none of them may ever appear again.
_PARSE_FAILURE_CODES = ("SC1072", "SC1073", "SC1124", "SC1094")

# Hermetic display number for the bd_start_display behaviour tests.
#
# bd_start_display hardcodes /tmp/.X<n>-lock and /tmp/.X11-unix/X<n>, so the
# number is chosen to be one no X server would plausibly own, and every test
# that uses it asserts BOTH before and after that neither path exists -- if the
# implementation ever touches them the test fails rather than silently
# colliding with a real display. Nothing here starts a real X server: `Xvfb`,
# `xdpyinfo` and (where needed) the entire PATH are stubs, and TMPDIR is
# redirected into tmp_path so even the Xvfb error-log scratch file is contained.
PROBE_DISPLAY_NUM = "9021"
PROBE_DISPLAY = f":{PROBE_DISPLAY_NUM}"
PROBE_LOCK = Path(f"/tmp/.X{PROBE_DISPLAY_NUM}-lock")
PROBE_SOCKET = Path(f"/tmp/.X11-unix/X{PROBE_DISPLAY_NUM}")


# --- helpers: running the fragment -------------------------------------------


def _run_fragment(
    snippet: str,
    *,
    function: str = "bd_system_pkgs",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Source the fragment and run ``snippet``, with unknown states reserved.

    ``cwd`` is pinned to ``REPO_ROOT`` because ``tests/conftest.py`` installs an
    autouse fixture that chdirs into ``tmp_path``; a relative source path would
    otherwise resolve against the wrong tree.

    This harness sets ``set -eu`` deliberately -- it makes an unnoticed failure
    inside a snippet loud. That is exactly why it is NOT used by
    ``test_fragment_does_not_leak_shell_options_into_its_consumers``: a harness
    that sets the options itself can never observe the fragment setting them.
    """
    script = (
        "set -eu\n"
        f". {FRAGMENT_REL} || exit {_SOURCE_MISSING}\n"
        f"declare -F {function} >/dev/null 2>&1 || exit {_FUNCTION_UNDEFINED}\n"
        f"{snippet}\n"
    )
    child_env = dict(os.environ)
    if env:
        child_env.update(env)
    return subprocess.run(
        [_BASH, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=child_env,
        timeout=120,
    )


def _assert_fragment_was_reached(
    result: subprocess.CompletedProcess[str],
    function: str = "bd_system_pkgs",
) -> None:
    """Fail with a distinct message when the check could not be evaluated."""
    assert result.returncode != _SOURCE_MISSING, (
        f"UNKNOWN: {FRAGMENT_REL} could not be sourced -- this check never "
        f"reached its subject. stderr: {result.stderr}"
    )
    assert result.returncode != _FUNCTION_UNDEFINED, (
        f"UNKNOWN: {FRAGMENT_REL} sourced but {function}() is undefined -- "
        "sourcing cleanly proves nothing about what it defined."
    )


def _packages(group: str) -> list[str]:
    result = _run_fragment(f"bd_system_pkgs {group}")
    _assert_fragment_was_reached(result)
    assert result.returncode == 0, (
        f"bd_system_pkgs {group} exited {result.returncode}: {result.stderr}"
    )
    return result.stdout.split()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- helpers: comment-stripped shell source ----------------------------------


_HEREDOC_START_RE = re.compile(
    r"<<-?\s*(?P<quote>['\"]?)(?P<word>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)"
)


def _strip_shell_comments(source: str, *, blank_quoted: bool = False) -> str:
    """Blank out shell comments, keeping line numbering and everything else.

    Why this exists, in both directions (CLAUDE.md 0):

    * **Blindness.** capture.sh's regen block carries a comment that quotes
      ``sudo systemctl stop bulkdownloader`` verbatim. An ordering assertion
      over raw text would find that COMMENT when the block is moved above the
      real stop and would certify the ordering it was written to forbid.
    * **Over-sensitivity.** The anti-drift scan must not fire on a maintainer
      writing "installs the GTK typelibs (python3-gi)" in prose.

    What it handles: full-line and trailing ``#`` comments, quote state carried
    ACROSS lines (capture.sh embeds a multi-line single-quoted python program),
    backslash escapes, and heredoc bodies -- which are kept verbatim, because a
    heredoc body is content a consumer emits, not a comment.

    ``blank_quoted=True`` additionally replaces the CONTENTS of every quoted
    string with spaces, keeping the quote characters and every column position.
    The rationale is the same one that justifies stripping comments: a package
    name inside a quoted string cannot be word-split into separate apt
    arguments (``apt-get install -y "xvfb libgtk-3-0t64"`` is ONE bogus
    argument, not a package list), and a tool path inside a quoted string is
    being *named*, not executed. Quoted text is therefore prose by
    construction, exactly like a comment. Both views are built in the same
    pass, and -- load-bearing -- the heredoc bookkeeping is driven from the
    CODE view only: blanking ``<<'USAGE'`` to ``<<'     '`` would hide the
    heredoc opener, the USAGE body would be parsed as code, and the first
    apostrophe in it desynchronises quote state for the rest of the file. That
    was measured on scripts/provision_test_host.sh.

    What it does not handle: ``$'...'`` ANSI-C quoting and ``((...))``
    arithmetic containing ``#``. Neither occurs in the files scanned here; if
    one is introduced, ``test_comment_stripper_self_check`` is where to extend
    the instrument.
    """
    kept_lines: list[str] = []
    heredoc_terminator: str | None = None
    heredoc_strip_tabs = False
    quote: str | None = None

    for raw in source.splitlines():
        if heredoc_terminator is not None:
            kept_lines.append(raw)
            candidate = raw.lstrip("\t") if heredoc_strip_tabs else raw
            if candidate.rstrip() == heredoc_terminator:
                heredoc_terminator = None
            continue

        kept: list[str] = []
        blanked: list[str] = []
        index = 0
        escaped = False
        while index < len(raw):
            char = raw[index]
            if escaped:
                kept.append(char)
                blanked.append(" " if quote is not None else char)
                escaped = False
                index += 1
                continue
            if char == "\\" and quote != "'":
                kept.append(char)
                blanked.append(" " if quote is not None else char)
                escaped = True
                index += 1
                continue
            if quote is None and char in "'\"":
                quote = char
                kept.append(char)
                blanked.append(char)
                index += 1
                continue
            if quote is not None and char == quote:
                quote = None
                kept.append(char)
                blanked.append(char)
                index += 1
                continue
            if quote is None and char == "#" and (index == 0 or raw[index - 1] in " \t"):
                break
            kept.append(char)
            blanked.append(" " if quote is not None else char)
            index += 1

        line = "".join(kept)
        kept_lines.append("".join(blanked) if blank_quoted else line)

        if quote is None:
            match = _HEREDOC_START_RE.search(line)
            if match is not None:
                heredoc_terminator = match.group("word")
                heredoc_strip_tabs = "<<-" in line

    return "\n".join(kept_lines)


def _code(path: Path) -> str:
    return _strip_shell_comments(_read(path))


def _unquoted_code(path: Path) -> str:
    """Comment-stripped code with quoted string CONTENTS blanked out too."""
    return _strip_shell_comments(_read(path), blank_quoted=True)


def _logical_lines(source: str) -> list[str]:
    """Join backslash continuations so a wrapped apt list stays one line."""
    return source.replace("\\\n", " ").splitlines()


def _logical_pairs(code: str, unquoted: str) -> list[tuple[int, str, str]]:
    """``(physical line number, joined code, joined unquoted)`` per logical line.

    Two properties this has and ``_logical_lines`` does not, both learned by
    getting it wrong first:

    * the continuation structure is derived from the CODE view ONLY and applied
      to both. Deriving it twice skews the views by one line the moment a ``\\``
      continuation sits inside a quoted string, because the blanked view no
      longer ends in a backslash -- scripts/provision_test_host.sh's
      ``DECLARED="$(grep -oE '...' ... \\`` does exactly that;
    * the number returned is the REAL 1-based file line the logical line starts
      on, so a failure message points at the file rather than at a post-join
      index nobody can locate.
    """
    code_lines = code.splitlines()
    unquoted_lines = unquoted.splitlines()
    assert len(code_lines) == len(unquoted_lines), (
        "UNKNOWN: the code and quote-blanked views disagree on line count "
        f"({len(code_lines)} vs {len(unquoted_lines)}) -- the instrument is "
        "broken, so nothing measured with it means anything"
    )

    pairs: list[tuple[int, str, str]] = []
    start: int | None = None
    acc_code = ""
    acc_unquoted = ""
    for number, (code_line, unquoted_line) in enumerate(
        zip(code_lines, unquoted_lines), start=1
    ):
        if start is None:
            start, acc_code, acc_unquoted = number, "", ""
        if code_line.endswith("\\"):
            acc_code += code_line[:-1] + " "
            acc_unquoted += unquoted_line[:-1] + " "
            continue
        pairs.append((start, acc_code + code_line, acc_unquoted + unquoted_line))
        start = None
    if start is not None:
        pairs.append((start, acc_code, acc_unquoted))
    return pairs


# An interpreter token immediately in front of a script path: `$VPYTHON`,
# `"$VPYTHON"`, `${PY}`, `venv/bin/python`, `./venv/bin/python`, `python3.12`.
_INTERP = r'(?:"?\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"?|\S*python[0-9.]*)'


def _tool_invocations(path: Path, tool: str) -> list[int]:
    """File line numbers where ``tool`` is EXECUTED, not merely mentioned.

    ``tool in line`` -- what every one of these checks used to be -- cannot
    tell an invocation from a defensive existence guard, a diagnostic string,
    or a comment. Measured: neutering install_linux.sh's regen to
    ``if ! true; then`` left the substring alive in the guard
    ``[ -f "$INSTALL_DIR/tools/gui_parity_inventory.py" ]`` two lines above, so
    the gate reported OK over dead code. In the other direction, ADDING that
    same defensive guard to capture.sh made a correct file fail an "exactly one
    invocation" count -- the same predicate crying wolf.

    Two conditions, and both are required:

    1. an interpreter-ish token sits immediately in front of the tool path on
       the joined logical line (optionally with flags between), which is what
       ``exec``, ``env VAR=1``, ``sudo``, ``nohup`` and ``run_step`` wrappers
       all still look like;
    2. the tool token survives quote-blanking, i.e. it is a bare word rather
       than text inside a string.

    RESIDUE, stated rather than claimed away: this proves the tool is in
    ARGUMENT POSITION OF AN INTERPRETER, not that the enclosing branch is ever
    reached. A regen moved into a function nobody calls would still count. For
    capture.sh that residue is closed by the execution probe in section I; for
    install_linux.sh and the provisioner it is not.
    """
    source = _read(path)
    pattern = re.compile(
        r"(?:^|\s)" + _INTERP + r"(?:\s+-[A-Za-z]\S*)*\s+" + re.escape(tool) + r"\b"
    )
    return [
        number
        for number, code_line, unquoted_line in _logical_pairs(
            _strip_shell_comments(source), _strip_shell_comments(source, blank_quoted=True)
        )
        if pattern.search(code_line) and tool in unquoted_line
    ]


def _tool_mentions(path: Path, tool: str) -> list[str]:
    """Every CODE line naming ``tool``, for a failure message that explains."""
    return [
        f"{number}: {code_line.strip()}"
        for number, code_line, _ in _logical_pairs(
            _strip_shell_comments(_read(path)),
            _strip_shell_comments(_read(path), blank_quoted=True),
        )
        if tool in code_line
    ]


def _tokens(line: str) -> set[str]:
    return {token.strip("\"'`,;()") for token in line.split()}


def _code_line_index(code: str, pattern: str, label: str) -> int:
    """Index of the first CODE line matching ``pattern``, or a loud failure."""
    compiled = re.compile(pattern)
    for index, line in enumerate(code.splitlines()):
        if compiled.search(line):
            return index
    raise AssertionError(
        f"UNKNOWN: no code line in capture.sh matches {label} "
        f"({pattern!r}). The ordering assertion has no anchor, so it cannot "
        "certify anything -- an absent needle is not a satisfied ordering."
    )


# --- A. the fragment exists --------------------------------------------------


def test_system_deps_fragment_exists_and_is_readable() -> None:
    assert FRAGMENT.is_file(), f"{FRAGMENT_REL} is missing"
    assert os.access(FRAGMENT, os.R_OK), f"{FRAGMENT_REL} is not readable"
    assert _read(FRAGMENT).strip(), f"{FRAGMENT_REL} is empty"


def test_comment_stripper_self_check() -> None:
    """The instrument before the measurement.

    Every assertion in sections D and F is made over ``_strip_shell_comments``
    output. An instrument that quietly stopped working would make those checks
    report OK over the wrong text, so it is exercised on inputs whose answer is
    known by construction.
    """
    stripped = _strip_shell_comments(
        "#!/usr/bin/env bash\n"
        "# a comment naming sudo systemctl stop bulkdownloader\n"
        'echo "keep me"  # trailing comment naming python3-gi\n'
        'echo "a # inside double quotes"\n'
        "echo 'a # inside single quotes'\n"
        'num="${disp#:}"\n'
        "cat <<'BODY'\n"
        "# this line is heredoc content, not a comment\n"
        "BODY\n"
        "python3 -c 'first line\n"
        "second line still quoted # not a comment\n"
        "'\n"
    )
    lines = stripped.splitlines()

    assert lines[0] == "", "shebang line should be blanked"
    assert "systemctl" not in lines[1], "full-line comment survived"
    assert lines[2].rstrip() == 'echo "keep me"', f"bad trailing strip: {lines[2]!r}"
    assert "python3-gi" not in stripped, "trailing comment survived"
    assert lines[3] == 'echo "a # inside double quotes"'
    assert lines[4] == "echo 'a # inside single quotes'"
    assert lines[5] == 'num="${disp#:}"', f"parameter expansion mangled: {lines[5]!r}"
    assert "heredoc content" in stripped, "heredoc body was wrongly stripped"
    assert "second line still quoted # not a comment" in stripped, (
        "quote state did not carry across lines"
    )
    assert len(lines) == len(
        (
            "#!/usr/bin/env bash\n"
            "# a comment naming sudo systemctl stop bulkdownloader\n"
            'echo "keep me"  # trailing comment naming python3-gi\n'
            'echo "a # inside double quotes"\n'
            "echo 'a # inside single quotes'\n"
            'num="${disp#:}"\n'
            "cat <<'BODY'\n"
            "# this line is heredoc content, not a comment\n"
            "BODY\n"
            "python3 -c 'first line\n"
            "second line still quoted # not a comment\n"
            "'\n"
        ).splitlines()
    ), "the stripper must preserve line numbering"


def test_comment_stripper_blank_quoted_self_check() -> None:
    """The instrument for the quote-blanked view, before it is measured with.

    ``blank_quoted=True`` is what stops
    ``test_consumers_do_not_restate_package_names`` firing on an operator hint
    in an ``echo`` and what stops ``_tool_invocations`` counting a defensive
    ``[ -f "$DIR/tools/gui_parity_inventory.py" ]`` as an invocation. Both of
    those are absence assertions, so a stripper that silently over-blanked
    would make them pass over nothing at all.
    """
    source = (
        'echo "you may need libgtk-3-0 instead"  # advice\n'
        'apt-get install -y xvfb libcairo2\n'
        "cat <<'USAGE'\n"
        "it's a heredoc body with an apostrophe and xvfb in it\n"
        "USAGE\n"
        'if [ -f "$DIR/tools/gui_parity_inventory.py" ]; then\n'
        '  "$PY" tools/gui_parity_inventory.py\n'
        "fi\n"
        'DECLARED="$(grep -oE \'__version__ *= *"[^"]+"\' file \\\n'
        "            | head -1)\"\n"
    )
    code = _strip_shell_comments(source)
    unquoted = _strip_shell_comments(source, blank_quoted=True)
    code_lines = code.splitlines()
    unquoted_lines = unquoted.splitlines()

    assert len(code_lines) == len(unquoted_lines) == len(source.splitlines()), (
        "blank_quoted must preserve line numbering exactly like the code view"
    )
    for index, (left, right) in enumerate(zip(code_lines, unquoted_lines)):
        assert len(left) == len(right), (
            f"blank_quoted changed column positions on line {index + 1}: "
            f"{left!r} vs {right!r}"
        )

    assert "libgtk-3-0" not in unquoted, "quoted echo prose was not blanked"
    assert "xvfb" in unquoted_lines[1], (
        "a BARE apt argument must survive quote-blanking -- blanking it would "
        "make the anti-drift scan assert over nothing"
    )
    assert "libcairo2" in unquoted_lines[1]
    assert "xvfb" in unquoted_lines[3], (
        "heredoc bodies are kept verbatim in BOTH views: a heredoc body can be "
        "a generated script that really does run apt, so blanking it would be "
        "a blind spot rather than a prose exemption"
    )
    assert "gui_parity_inventory" not in unquoted_lines[5], (
        "a quoted path inside a [ -f ... ] guard must be blanked"
    )
    assert "tools/gui_parity_inventory.py" in unquoted_lines[6], (
        "a bare tool path in argument position must survive"
    )

    pairs = _logical_pairs(code, unquoted)
    numbers = [number for number, _, _ in pairs]
    assert numbers == [1, 2, 3, 4, 5, 6, 7, 8, 9], (
        "the continuation inside the double-quoted $(...) must be joined in "
        f"BOTH views from the CODE view's boundaries, got {numbers}"
    )


def test_comment_stripper_reaches_capture_sh_real_anchors() -> None:
    """The stripper is only useful if it changes the answer on the real file.

    ANTI-VACUITY, not prose coupling. The previous version of this test also
    asserted ``raw.count("systemctl stop bulkdownloader") >= 2`` on the premise
    that without a comment mentioning the stop, the ordering test "would still
    pass but stop being a test of anything". That premise was MEASURED AND IS
    FALSE: with the comment reworded so it no longer quotes the command, and
    the regen then hoisted above the service stop,
    ``test_capture_regen_is_ordered_against_every_prerequisite`` still fails.
    Its teeth come from ``code.count(...) == 1`` and from ``_code_line_index``
    locating its anchors in CODE, not from a comment existing anywhere. So the
    assertion was buying nothing and charging a build failure for rewording a
    comment -- a gate firing on identity, which CLAUDE.md 0 calls a soundness
    bug in its own right.

    What remains is the property the ordering test genuinely depends on: the
    stripper is not inert on capture.sh, and exactly one service-stop COMMAND
    survives it.
    """
    raw = _read(CAPTURE_SH)
    code = _code(CAPTURE_SH)

    assert code != raw, (
        "_strip_shell_comments is inert on capture.sh -- it removed nothing, "
        "so every assertion made over the 'comment-stripped' file is really "
        "being made over the raw text"
    )
    assert code.count("systemctl stop bulkdownloader") == 1, (
        "comment-stripped capture.sh should hold exactly one service-stop "
        f"command, found {code.count('systemctl stop bulkdownloader')}"
    )
    assert not [
        line for line in code.splitlines() if line.lstrip().startswith("#")
    ], "comment-stripped capture.sh still contains comment lines"


# --- B. every group executes and returns EXACTLY the contracted names ---------


@pytest.mark.parametrize("group", GROUP_ORDER)
def test_bd_system_pkgs_returns_exactly_the_contracted_packages(group: str) -> None:
    """EXACT set, not superset.

    A superset assertion ("every expected name is present") is blind to an
    ADDED name, and apt-get is all-or-nothing: one unknown package name exits
    100 and installs nothing at all -- not the interpreter, not the SPA
    toolchain, not the display libraries. An extra name is therefore at least as
    box-fatal as a missing one, and the old assertion could not see it.
    """
    packages = _packages(group)
    expected = set(EXPECTED_GROUPS[group])
    actual = set(packages)

    assert actual == expected, (
        f"bd_system_pkgs {group} returned {sorted(actual)}; the contract is "
        f"{sorted(expected)}. Unexpected: {sorted(actual - expected)}; "
        f"missing: {sorted(expected - actual)}"
    )
    assert len(packages) == len(actual), (
        f"bd_system_pkgs {group} repeats a package: {packages}"
    )


def test_bd_system_pkgs_groups_do_not_leak_into_each_other() -> None:
    """core/node/gtk must be pairwise disjoint.

    A `core` that also returned the gtk names would satisfy every superset
    assertion in this file and every per-group exact-set assertion would still
    have to be written for it to be caught -- so the property is asserted
    directly, with its own name, because leakage is what an installer's
    per-group criticality grading depends on: scripts/provision_test_host.sh
    installs core and node as `core` and gtk as `optional`, and a leaked gtk
    name would silently promote an optional capability to load-bearing.
    """
    resolved = {group: set(_packages(group)) for group in GROUP_ORDER}

    overlaps = {
        f"{left}&{right}": sorted(resolved[left] & resolved[right])
        for index, left in enumerate(GROUP_ORDER)
        for right in GROUP_ORDER[index + 1 :]
        if resolved[left] & resolved[right]
    }
    assert not overlaps, (
        f"package groups leak into each other: {overlaps} -- per-group apt "
        "transactions and per-group criticality both depend on disjointness"
    )


def test_bd_system_pkgs_all_is_exactly_the_union_of_the_groups() -> None:
    """`all` is the denominator: exactly core + node + gtk, deduplicated."""
    combined = _packages("all")
    union = {name for group in GROUP_ORDER for name in _packages(group)}

    assert union, "core/node/gtk produced no packages at all"
    assert set(combined) == union, (
        f"bd_system_pkgs all returned {sorted(set(combined))}; the union of "
        f"the groups is {sorted(union)}. Unexpected: "
        f"{sorted(set(combined) - union)}; missing: {sorted(union - set(combined))}"
    )
    assert set(combined) == set(ALL_PACKAGES), (
        f"bd_system_pkgs all drifted from the contract: unexpected "
        f"{sorted(set(combined) - set(ALL_PACKAGES))}, missing "
        f"{sorted(set(ALL_PACKAGES) - set(combined))}"
    )
    assert len(combined) == len(set(combined)), (
        f"bd_system_pkgs all is not deduplicated: {combined}"
    )


def test_bd_system_pkgs_all_orders_core_then_node_then_gtk() -> None:
    combined = _packages("all")
    positions = {name: index for index, name in enumerate(combined)}
    missing = [
        name
        for group in GROUP_ORDER
        for name in EXPECTED_GROUPS[group]
        if name not in positions
    ]
    assert not missing, (
        f"bd_system_pkgs all omits {missing}; the ordering assertion has no "
        "subject"
    )
    blocks = [
        [positions[name] for name in EXPECTED_GROUPS[group]]
        for group in GROUP_ORDER
    ]

    assert max(blocks[0]) < min(blocks[1]), f"node precedes core: {combined}"
    assert max(blocks[1]) < min(blocks[2]), f"gtk precedes node: {combined}"


# --- C. an unknown group fails loudly ----------------------------------------


@pytest.mark.parametrize(
    ("snippet", "label"),
    [
        pytest.param("bd_system_pkgs bogus", "bogus", id="unknown-group"),
        pytest.param('bd_system_pkgs ""', "empty", id="empty-group"),
        pytest.param("bd_system_pkgs", "missing", id="no-argument"),
    ],
)
def test_bd_system_pkgs_rejects_unknown_groups(snippet: str, label: str) -> None:
    """An empty list must never be echoed silently (CLAUDE.md 0)."""
    result = _run_fragment(snippet)
    _assert_fragment_was_reached(result)

    assert result.returncode != 0, (
        f"bd_system_pkgs ({label}) exited 0 -- an unknown group that reports "
        "success is an empty denominator reading as OK"
    )
    assert result.stdout.strip() == "", (
        f"bd_system_pkgs ({label}) wrote to stdout: {result.stdout!r}"
    )


def test_bd_system_pkgs_unknown_group_explains_itself_on_stderr() -> None:
    result = _run_fragment("bd_system_pkgs bogus")
    _assert_fragment_was_reached(result)

    assert result.stderr.strip(), (
        "bd_system_pkgs bogus failed silently -- failing loudly means saying "
        "why, on stderr"
    )


# --- D. bd_start_display, behaviourally --------------------------------------
#
# HERMETICITY. Not one of these tests starts an X server or reads a real
# display's state. `xdpyinfo` and `Xvfb` are stubs on a PATH this test controls;
# the display number is one nothing plausibly owns; TMPDIR is redirected into
# tmp_path so the function's mktemp scratch file is contained; and every test
# asserts /tmp/.X<n>-lock and /tmp/.X11-unix/X<n> are absent BEFORE and AFTER,
# so a future implementation that starts touching them fails loudly here instead
# of colliding with a real display on the box.


def _write_stub(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _display_stub_dir(
    tmp_path: Path,
    *,
    xdpyinfo: str | None,
    xvfb: bool,
    spawn_log: Path,
    marker: Path | None = None,
    xvfb_creates_marker: bool = False,
) -> Path:
    """Build a stub bin dir for the bd_start_display probes.

    ``xdpyinfo`` is ``"active"`` (display is served), ``"inactive"`` (never
    served), ``"marker"`` (served only once ``marker`` exists) or ``None`` (the
    tool is absent).
    """
    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir(exist_ok=True)

    if xdpyinfo == "active":
        _write_stub(
            stub_dir / "xdpyinfo",
            f'#!/bin/sh\n[ "${{DISPLAY:-}}" = "{PROBE_DISPLAY}" ] || exit 1\nexit 0\n',
        )
    elif xdpyinfo == "inactive":
        _write_stub(stub_dir / "xdpyinfo", "#!/bin/sh\nexit 1\n")
    elif xdpyinfo == "marker":
        assert marker is not None
        _write_stub(
            stub_dir / "xdpyinfo",
            f'#!/bin/sh\n[ "${{DISPLAY:-}}" = "{PROBE_DISPLAY}" ] || exit 1\n'
            f'[ -f "{marker}" ] || exit 1\nexit 0\n',
        )

    if xvfb:
        create = f': > "{marker}"\n' if (xvfb_creates_marker and marker) else ""
        _write_stub(
            stub_dir / "Xvfb",
            f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{spawn_log}"\n{create}exit 0\n',
        )

    return stub_dir


def _assert_no_real_display_state() -> None:
    assert not PROBE_LOCK.exists(), (
        f"{PROBE_LOCK} exists -- this probe's display number is not free, so "
        "the behaviour tests below would be reading somebody else's state"
    )
    assert not PROBE_SOCKET.exists(), (
        f"{PROBE_SOCKET} exists -- the probe display number is in use"
    )


def _run_display(
    snippet: str,
    tmp_path: Path,
    stub_dir: Path,
    *,
    isolate_path: bool,
) -> subprocess.CompletedProcess[str]:
    path = str(stub_dir) if isolate_path else f"{stub_dir}{os.pathsep}{os.environ['PATH']}"
    return _run_fragment(
        snippet,
        function="bd_start_display",
        env={"PATH": path, "TMPDIR": str(tmp_path)},
    )


def test_bd_start_display_is_defined(tmp_path: Path) -> None:
    """Defined-ness only -- kept because the behaviour tests below need a
    distinct message for "the function vanished" versus "it misbehaved"."""
    result = _run_fragment(
        "declare -F bd_start_display",
        function="bd_start_display",
    )
    _assert_fragment_was_reached(result, "bd_start_display")

    assert result.returncode == 0, result.stderr


def test_bd_start_display_is_idempotent_on_an_already_served_display(
    tmp_path: Path,
) -> None:
    """The contract, and the operator bug it exists to prevent.

    A display already served must be reported as usable WITHOUT starting a
    second server -- starting one produced "Fatal server error: Server is
    already active for display 99". The stub Xvfb records every invocation, so
    "did not spawn" is measured rather than assumed.
    """
    _assert_no_real_display_state()
    spawn_log = tmp_path / "xvfb-spawns.log"
    stub_dir = _display_stub_dir(
        tmp_path, xdpyinfo="active", xvfb=True, spawn_log=spawn_log
    )

    result = _run_display(
        f"bd_start_display {PROBE_DISPLAY}", tmp_path, stub_dir, isolate_path=False
    )
    _assert_fragment_was_reached(result, "bd_start_display")

    assert result.returncode == 0, (
        f"bd_start_display returned {result.returncode} for a display that is "
        f"already served. stderr: {result.stderr}"
    )
    assert result.stdout == f"{PROBE_DISPLAY}\n", (
        "stdout must carry the display value and nothing else (the fragment's "
        f"rule 3 -- callers capture it with $(...)): {result.stdout!r}"
    )
    assert not spawn_log.exists() or spawn_log.read_text(encoding="utf-8") == "", (
        "bd_start_display started a competing X server on a display that was "
        f"already served: {spawn_log.read_text(encoding='utf-8')!r}"
    )
    _assert_no_real_display_state()


def test_bd_start_display_starts_a_server_when_the_display_is_free(
    tmp_path: Path,
) -> None:
    """The other half of the contract: it must actually spawn.

    A ``return 1`` body passes the idempotence test above vacuously (nothing to
    spawn) -- this one fails it, and so does a body that reports success without
    starting anything. The stub Xvfb publishes a marker file that the stub
    xdpyinfo keys on, so the readiness poll observes a transition it did not
    fabricate.
    """
    _assert_no_real_display_state()
    spawn_log = tmp_path / "xvfb-spawns.log"
    marker = tmp_path / "display-served.marker"
    stub_dir = _display_stub_dir(
        tmp_path,
        xdpyinfo="marker",
        xvfb=True,
        spawn_log=spawn_log,
        marker=marker,
        xvfb_creates_marker=True,
    )

    result = _run_display(
        f"bd_start_display {PROBE_DISPLAY}", tmp_path, stub_dir, isolate_path=False
    )
    _assert_fragment_was_reached(result, "bd_start_display")

    assert result.returncode == 0, (
        f"bd_start_display could not start a free display: {result.stderr}"
    )
    assert result.stdout == f"{PROBE_DISPLAY}\n", (
        f"stdout is not exactly the display value: {result.stdout!r}"
    )
    assert spawn_log.is_file(), "bd_start_display never invoked Xvfb"
    spawns = [
        line for line in spawn_log.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(spawns) == 1, f"expected exactly one Xvfb spawn, got {spawns}"
    assert PROBE_DISPLAY in spawns[0], (
        f"Xvfb was started on the wrong display: {spawns[0]!r}"
    )
    assert not list(tmp_path.glob("bd-xvfb-*")), (
        "bd_start_display left its Xvfb scratch log behind: "
        f"{[p.name for p in tmp_path.glob('bd-xvfb-*')]}"
    )
    _assert_no_real_display_state()


def test_bd_start_display_fails_loudly_when_no_display_can_be_provided(
    tmp_path: Path,
) -> None:
    """Non-zero, EMPTY stdout, reason on stderr.

    PATH is replaced entirely (not merely prefixed) so the real /usr/bin/Xvfb on
    this host cannot be found -- otherwise this test would start a real server.
    The function returns before it needs any external tool other than the
    ``command -v`` lookups themselves, so an isolated PATH is sufficient.
    """
    _assert_no_real_display_state()
    spawn_log = tmp_path / "xvfb-spawns.log"
    stub_dir = _display_stub_dir(
        tmp_path, xdpyinfo="inactive", xvfb=False, spawn_log=spawn_log
    )

    result = _run_display(
        f"bd_start_display {PROBE_DISPLAY}", tmp_path, stub_dir, isolate_path=True
    )
    _assert_fragment_was_reached(result, "bd_start_display")

    assert result.returncode != 0, (
        "bd_start_display reported success with no display and no way to make "
        "one -- the caller would then export a DISPLAY nothing serves"
    )
    assert result.stdout == "", (
        f"a failed bd_start_display must echo nothing: {result.stdout!r}"
    )
    assert "Xvfb" in result.stderr, (
        f"the failure does not say why on stderr: {result.stderr!r}"
    )
    assert not spawn_log.exists(), "a spawn was attempted with no Xvfb present"
    _assert_no_real_display_state()


@pytest.mark.parametrize(
    ("argument", "label"),
    [
        pytest.param("abc", "non-numeric", id="non-numeric"),
        pytest.param('""', "empty-string", id="empty-string"),
        pytest.param(":", "bare-colon", id="bare-colon"),
        pytest.param('":9021x"', "trailing-garbage", id="trailing-garbage"),
    ],
)
def test_bd_start_display_rejects_an_invalid_display(
    tmp_path: Path, argument: str, label: str
) -> None:
    """An invalid display must fail before anything is spawned.

    The empty-string case is the one that was wrong: ``${1:-:99}`` treats an
    EMPTY argument as an ABSENT one, so ``bd_start_display ""`` answered about
    :99 -- a different display from the one the caller named -- while
    ``bd_start_display abc`` correctly failed. Two spellings of "no display"
    disagreeing inside one file is the drift this fragment exists to stop. The
    display number is also interpolated into a grep -E pattern, so a
    non-numeric argument that got through would arrive as a REGEX.
    """
    _assert_no_real_display_state()
    spawn_log = tmp_path / "xvfb-spawns.log"
    stub_dir = _display_stub_dir(
        tmp_path, xdpyinfo="inactive", xvfb=True, spawn_log=spawn_log
    )

    result = _run_display(
        f"bd_start_display {argument}", tmp_path, stub_dir, isolate_path=False
    )
    _assert_fragment_was_reached(result, "bd_start_display")

    assert result.returncode != 0, (
        f"bd_start_display accepted the invalid display {label}"
    )
    assert result.stdout == "", (
        f"bd_start_display ({label}) echoed something: {result.stdout!r}"
    )
    assert result.stderr.strip(), (
        f"bd_start_display ({label}) failed silently"
    )
    assert not spawn_log.exists(), (
        f"bd_start_display ({label}) spawned a server for an invalid display: "
        f"{spawn_log.read_text(encoding='utf-8')!r}"
    )
    _assert_no_real_display_state()


# --- D2. _bd_display_active's SECOND and THIRD probes ------------------------
#
# WHY THIS SECTION EXISTS, AND WHY IT DOES NOT REUSE `_display_stub_dir`.
#
# `_display_stub_dir` writes a stub `xdpyinfo` UNCONDITIONALLY, and `_run_display`
# puts that directory FIRST on PATH. Every one of the four call sites above
# passes "active", "inactive" or "marker" -- never None. So `_bd_display_active`'s
# probe 1 always finds xdpyinfo and always answers, probe 2 (`ss`) is reached
# only when probe 1 says no (and then resolves to the HOST's real ss, whose
# answer nobody controls), and probe 3 (the lock file) requires `competent -eq 0`,
# which no test above ever arranges. The denominator -- the PATH those tests
# build -- structurally excludes two thirds of the subject.
#
# Measured consequence, before this section existed: TWELVE distinct mutations of
# probes 2 and 3 all reported "54 passed", exit 0. Among them the verbatim revert
# of the D2 fix to its original dead code
# (`grep -q "[[:space:]]/tmp/.X11-unix/X${num}$"`, which matches ZERO rows because
# the path is followed by an inode and two peer columns), deleting probe 2
# outright, deleting probe 3's body, dropping the `@?` so an abstract-only socket
# is invisible, unescaping the `.`, dropping either boundary, and inverting the
# probe-precedence guard so a stale lock file overrides a competent "no" --
# restoring a false positive the fragment's own comment records as MEASURED.
#
# THE FIX IS THE TOOLBOX, not more stubs. `_probe_toolbox` builds a PATH holding
# EXACTLY the probe tools a case is meant to exercise and nothing else, so the
# probe under test is the only one that can answer. It never contains Xvfb, so a
# case whose expected answer is "not active" cannot spawn anything. Two
# properties follow that the PATH-PREFIX design cannot claim: the verdict does
# not depend on what the host happens to have installed (verified by re-running
# these cases with an always-yes xdpyinfo, an always-yes ss and a logging Xvfb
# prepended to the host PATH -- no case changed answer), and adding a FOURTH
# probe to the fragment cannot flip any case, because its tool is not in the box.
#
# THE OBSERVABLE IS ALWAYS `bd_start_display`, the public contract -- never
# `_bd_display_active`. Naming the private helper would be easier and would turn
# a routine rename into a red suite. Verified: renaming `_bd_display_active` at
# all five of its sites leaves this section green.


# Recorded VERBATIM from `ss -lx` (iproute2) while a real
# `Xvfb :77 -screen 0 1024x768x24` was serving :77:
#
#   Netid State  Recv-Q Send-Q       Local Address:Port   Peer Address:PortProcess
#   u_str LISTEN 0      0      @/tmp/.X11-unix/X77 121390            * 0
#   u_str LISTEN 0      0       /tmp/.X11-unix/X77 121391            * 0
#
# BOTH rows are always emitted: the kernel publishes the abstract ('@'-prefixed)
# socket and the filesystem one. Only the socket path is substituted below; the
# column shape -- crucially, the inode and two peer columns AFTER the path -- is
# the recorded one, which is what makes an end-anchored pattern provably dead.
_SS_HEADER = (
    "Netid State  Recv-Q Send-Q       Local Address:Port   Peer Address:PortProcess"
)
_SS_ABSTRACT = "u_str LISTEN 0      0      @{path} 121390            * 0          "
_SS_FILESYSTEM = "u_str LISTEN 0      0       {path} 121391            * 0          "

# Disjoint from PROBE_DISPLAY_NUM (9021) on purpose: probe 3 needs a REAL
# /tmp/.X<n>-lock (the path is hardcoded in the fragment and cannot be
# redirected), so a leak from this range can never poison the tests above.
_LOCK_RANGE = range(9100, 9200)


def _x_socket_path(num: str) -> str:
    return f"/tmp/.X11-unix/X{num}"


def _probe_toolbox(
    tmp_path: Path,
    *,
    ss_rows: tuple[str, ...] | None,
    xdpyinfo: str | None = None,
) -> Path:
    """A PATH holding exactly the probe tools this case is meant to exercise.

    ``printf`` rather than ``cat`` in the stubs: it is a ``/bin/sh`` builtin, so
    the stub needs nothing on PATH itself. The only external the whole probe
    path needs is ``grep`` (probe 2 pipes into it); probe 3 and both
    ``bd_start_display`` exits are pure builtins. That is measured, not assumed
    -- every case here runs with the toolbox as the ENTIRE PATH.
    """
    box = tmp_path / "probebin"
    box.mkdir(exist_ok=True)
    assert _GREP, (
        "UNKNOWN: no grep on PATH at import time, so probe 2's pipeline cannot "
        "run and these cases would be measuring the harness"
    )
    with contextlib.suppress(FileExistsError):
        os.symlink(_GREP, box / "grep")

    if ss_rows is not None:
        quoted = " ".join(
            "'" + row.replace("'", "'\\''") + "'" for row in ss_rows
        )
        _write_stub(box / "ss", f"#!/bin/sh\nprintf '%s\\n' {quoted}\nexit 0\n")

    if xdpyinfo == "inactive":
        _write_stub(box / "xdpyinfo", "#!/bin/sh\nexit 1\n")

    return box


def _assert_display_reported_active(
    result: subprocess.CompletedProcess[str], display: str, why: str
) -> None:
    _assert_fragment_was_reached(result, "bd_start_display")
    assert result.returncode == 0, (
        f"{why}: bd_start_display {display} returned {result.returncode} for a "
        f"display that IS being served. stderr={result.stderr!r}"
    )
    assert result.stdout == f"{display}\n", (
        f"{why}: stdout must be exactly the display value: {result.stdout!r}"
    )


def _assert_display_reported_inactive(
    result: subprocess.CompletedProcess[str], display: str, why: str
) -> None:
    _assert_fragment_was_reached(result, "bd_start_display")
    assert result.returncode != 0, (
        f"{why}: bd_start_display {display} reported SUCCESS for a display "
        "nobody is serving, so the caller would export a DISPLAY that answers "
        "nothing"
    )
    assert result.stdout == "", (
        f"{why}: a failed bd_start_display must echo nothing: {result.stdout!r}"
    )
    # THE ANTI-VACUITY GUARD. A bash error, a missing fragment or a typo in the
    # harness also gives a non-zero exit with empty stdout, so without this a
    # broken probe would "prove" not-active for every case. The function's own
    # name-prefixed diagnostic is the fragment's rule 3 and is structural; the
    # WORDING of the message deliberately is not asserted, so rephrasing the
    # Xvfb-absent diagnostic does not fail this.
    assert "bd_start_display:" in result.stderr, (
        f"{why}: non-zero with empty stdout, but the failure did not come from "
        "bd_start_display's own failure path -- this case proved nothing about "
        f"the probe. stderr={result.stderr!r}"
    )


_SS_PROBE_CASES = [
    pytest.param(
        (_SS_FILESYSTEM.format(path=_x_socket_path(PROBE_DISPLAY_NUM)),),
        PROBE_DISPLAY,
        True,
        id="filesystem-socket-active",
    ),
    pytest.param(
        (_SS_ABSTRACT.format(path=_x_socket_path(PROBE_DISPLAY_NUM)),),
        PROBE_DISPLAY,
        True,
        id="abstract-socket-only-active",
    ),
    pytest.param(
        (
            _SS_ABSTRACT.format(path=_x_socket_path(PROBE_DISPLAY_NUM)),
            _SS_FILESYSTEM.format(path=_x_socket_path(PROBE_DISPLAY_NUM)),
        ),
        PROBE_DISPLAY,
        True,
        id="both-sockets-active",
    ),
    pytest.param(
        (
            _SS_ABSTRACT.format(path=_x_socket_path(PROBE_DISPLAY_NUM)),
            _SS_FILESYSTEM.format(path=_x_socket_path(PROBE_DISPLAY_NUM)),
        ),
        ":902",
        False,
        id="longer-display-number-not-active",
    ),
    pytest.param(
        (_SS_FILESYSTEM.format(path=f"/tmp/XX11-unix/X{PROBE_DISPLAY_NUM}"),),
        PROBE_DISPLAY,
        False,
        id="decoy-unescaped-dot-not-active",
    ),
    pytest.param(
        (
            _SS_FILESYSTEM.format(
                path=f"/run/user/1000/container/tmp/.X11-unix/X{PROBE_DISPLAY_NUM}"
            ),
        ),
        PROBE_DISPLAY,
        False,
        id="decoy-nested-path-not-active",
    ),
    pytest.param((), PROBE_DISPLAY, False, id="no-listener-not-active"),
]


@pytest.mark.parametrize(("rows", "display", "expect_active"), _SS_PROBE_CASES)
def test_display_socket_probe_reads_a_real_ss_listing(
    tmp_path: Path, rows: tuple[str, ...], display: str, expect_active: bool
) -> None:
    """KILLS six probe-2 mutations, one case each. All six survive the old suite.

    * ``filesystem-socket-active`` / ``abstract-socket-only-active`` /
      ``both-sockets-active`` kill the D2 REVERT -- ``grep -q
      "[[:space:]]/tmp/.X11-unix/X${num}$"``. The recorded rows carry an inode
      and two peer columns after the path, so an end-anchored pattern matches
      zero rows and the probe is dead code that silently falls through to
      ``return 1``. They also kill deleting probe 2 outright.
    * ``abstract-socket-only-active`` kills dropping the ``@?``: the character
      before ``/tmp/`` is ``@``, so a pattern without it goes blind exactly
      where only the abstract socket is published.
    * ``decoy-unescaped-dot-not-active`` kills unescaping the ``.`` -- ``.``
      then matches the second ``X`` of ``/tmp/XX11-unix/X9021``.
    * ``longer-display-number-not-active`` kills dropping the TRAILING
      boundary: a question about :902 must not be answered by a live X9021.
    * ``decoy-nested-path-not-active`` kills dropping the LEADING boundary. Not
      hypothetical: a bind-mounted X socket inside a container appears in the
      host's ``ss -lx`` under its full host path and must not answer for ours.
    * ``no-listener-not-active`` is the control -- without it every "active"
      case would be satisfied by a probe that says yes to everything.

    The toolbox holds ss and grep and NOTHING else, so probe 1 cannot answer,
    probe 3 cannot answer, and no server can be spawned.
    """
    _assert_no_real_display_state()
    box = _probe_toolbox(tmp_path, ss_rows=(_SS_HEADER,) + tuple(rows))
    assert not (box / "xdpyinfo").exists(), (
        "probe 1 must not be able to answer, or this case is not about probe 2"
    )
    assert not (box / "Xvfb").exists(), "nothing may be spawned by these cases"

    result = _run_display(
        f"bd_start_display {display}", tmp_path, box, isolate_path=True
    )

    if expect_active:
        _assert_display_reported_active(result, display, "socket probe")
    else:
        _assert_display_reported_inactive(result, display, "socket probe")
    _assert_no_real_display_state()


@contextlib.contextmanager
def _reserved_display_lock():
    """Reserve a display number nothing else owns, and give back its lock path.

    ``/tmp/.X<n>-lock`` is hardcoded in the fragment and cannot be redirected,
    so probe 3 can only be reached with a real file at a real path. The
    reservation is DERIVED rather than asserted: walk a range disjoint from
    PROBE_DISPLAY_NUM, skip any number whose socket exists, and take the first
    whose lock can be created with ``O_CREAT|O_EXCL`` -- so two concurrent runs
    cannot collide. An exhausted range is UNKNOWN and fails; it is not a reason
    to guess a number.
    """
    for num in _LOCK_RANGE:
        if Path(_x_socket_path(str(num))).exists():
            continue
        lock = Path(f"/tmp/.X{num}-lock")
        try:
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        os.close(handle)
        try:
            yield str(num), lock
        finally:
            lock.unlink(missing_ok=True)
        return
    raise AssertionError(
        f"UNKNOWN: no free display number in {_LOCK_RANGE} -- probe 3 could "
        "not be reached, so this check examined nothing"
    )


@contextlib.contextmanager
def _lock_holding_process(tmp_path: Path, name: str):
    """A live process whose ``/proc/<pid>/comm`` is ``name``, without an X server.

    ``/bin/sleep`` is COPIED to the wanted name, so comm reports that name and
    the fragment's ``X*`` case can be exercised with no X server anywhere. The
    name deliberately does not contain "Xvfb", so ``pgrep -a Xvfb`` is
    unchanged by this suite. The comm is READ BACK and a mismatch is UNKNOWN,
    because a trick that silently stopped working would make these cases pass
    over the wrong process.
    """
    assert _SLEEP, "UNKNOWN: no sleep binary to build a lock-holding stand-in from"
    executable = tmp_path / name
    shutil.copy2(_SLEEP, executable)
    process = subprocess.Popen([str(executable), "30"])
    try:
        comm = Path(f"/proc/{process.pid}/comm").read_text(encoding="utf-8").strip()
        assert comm == name, (
            f"UNKNOWN: the stand-in's /proc comm is {comm!r}, expected {name!r} "
            "-- probe 3's comm case would be exercised against the wrong shape"
        )
        yield process.pid
    finally:
        process.kill()
        process.wait()


def _pid_that_is_never_live() -> int:
    """``pid_max`` is one past the last assignable pid, so it is never a process."""
    pid_max = int(Path("/proc/sys/kernel/pid_max").read_text(encoding="utf-8").strip())
    assert not Path(f"/proc/{pid_max}").exists(), (
        f"UNKNOWN: /proc/{pid_max} exists, so pid_max is not a reliably dead "
        "pid on this host and the dead-pid case would be testing nothing"
    )
    return pid_max


def _lock_probe_result(
    tmp_path: Path, num: str, contents: str
) -> subprocess.CompletedProcess[str]:
    Path(f"/tmp/.X{num}-lock").write_text(contents, encoding="utf-8")
    box = _probe_toolbox(tmp_path, ss_rows=None)
    assert not (box / "ss").exists() and not (box / "xdpyinfo").exists(), (
        "probe 3 is only reachable when NO competent probe is available"
    )
    return _run_display(f"bd_start_display :{num}", tmp_path, box, isolate_path=True)


# The recorded on-disk format: Xvfb writes `printf '%10d\n' <pid>` -- five
# leading spaces for a five-digit pid, eleven bytes. `od -c /tmp/.X77-lock`
# against a real server: "           2   3   3   9   2  \n".
def _lock_body(pid: int) -> str:
    return f"{pid:10d}\n"


def test_display_lock_probe_answers_for_a_live_x_server(tmp_path: Path) -> None:
    """KILLS: deleting probe 3's body (e.g. wrapping it in ``if false``).

    Probe 3 is the ONLY thing that can answer here -- the toolbox holds neither
    ss nor xdpyinfo, so ``competent`` stays 0. Measured: this mutation reported
    54 passed against the old suite, because no test ever arranged a PATH where
    probe 3 was reachable at all.
    """
    with _reserved_display_lock() as (num, lock):
        with _lock_holding_process(tmp_path, "Xprobe-standin") as pid:
            result = _lock_probe_result(tmp_path, num, _lock_body(pid))
            _assert_display_reported_active(result, f":{num}", "lock fallback")
        assert lock.exists(), "the reservation vanished mid-test"


def test_display_lock_probe_ignores_a_live_non_x_process(tmp_path: Path) -> None:
    """KILLS: widening probe 3's comm case from ``''|X*`` to ``*``.

    Pids are recycled, so "the lock names a live pid" certifies nothing on its
    own; the process has to look like an X server. Measured: accepting any comm
    reported 54 passed.

    NOTE for a future tightening: the stand-in is named ``Xprobe-standin`` so
    that narrowing ``X*`` to an explicit ``Xvfb|Xorg|Xvnc|Xwayland|Xprobe*``
    list keeps passing. Narrowing it with NO wildcard fails
    ``test_display_lock_probe_answers_for_a_live_x_server`` -- that is the one
    designed-in coupling, it is a genuine behaviour change (the fragment's
    comment says the ``X*`` shape is deliberate), and this is where it is
    written down.
    """
    with _reserved_display_lock() as (num, _lock):
        with _lock_holding_process(tmp_path, "notanxserver") as pid:
            result = _lock_probe_result(tmp_path, num, _lock_body(pid))
            _assert_display_reported_inactive(
                result, f":{num}", "lock fallback, non-X comm"
            )


def test_display_lock_probe_ignores_a_dead_pid(tmp_path: Path) -> None:
    """KILLS: dropping probe 3's liveness check (``[ -d /proc/$pid ] || kill -0``).

    A killed server leaves its lock behind, which is why the lock is evidence
    of a CLAIM and never of service. Without the liveness check the unreadable
    ``/proc/<dead>/comm`` reads as the empty string and the ``''`` arm returns
    0 -- so a stale lock would report the display active and bd_start_display
    would hand back a DISPLAY nothing serves. Measured: 54 passed.
    """
    with _reserved_display_lock() as (num, _lock):
        result = _lock_probe_result(
            tmp_path, num, _lock_body(_pid_that_is_never_live())
        )
        _assert_display_reported_inactive(
            result, f":{num}", "lock fallback, dead pid"
        )


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("not-a-pid\n", id="not-a-number"),
        pytest.param(f"{-1:10d}\n", id="negative-one"),
    ],
)
def test_display_lock_probe_ignores_a_non_numeric_lock(
    tmp_path: Path, body: str
) -> None:
    """KILLS: dropping probe 3's numeric validation of the lock's contents.

    The pid is read straight out of a file anything can write, and the two
    cases are not equivalent -- which is why both are here rather than only the
    obvious one:

    * ``not-a-pid`` is inert either way (``/proc/not-a-pid`` is absent and
      ``kill -0 not-a-pid`` errors), so on its own it does NOT kill the
      mutation. Kept as the control it always was;
    * ``-1`` is the case that has teeth, measured rather than reasoned about:
      ``kill -0 -1`` SUCCEEDS -- it signals every process the caller may signal
      -- so without the ``*[!0-9]*`` rejection the probe treats the lock as
      live, then fails to read ``/proc/-1/comm``, gets the empty string, and
      the ``''`` arm returns 0. A stale lock holding ``-1`` would report the
      display active and bd_start_display would hand back a DISPLAY nothing
      serves.
    """
    with _reserved_display_lock() as (num, _lock):
        result = _lock_probe_result(tmp_path, num, body)
        _assert_display_reported_inactive(
            result, f":{num}", "lock fallback, non-numeric pid"
        )


@pytest.mark.parametrize(
    ("use_ss", "use_xdpyinfo"),
    [
        pytest.param(True, False, id="ss-says-no"),
        pytest.param(False, True, id="xdpyinfo-says-no"),
        pytest.param(True, True, id="both-say-no"),
    ],
)
def test_display_stale_lock_never_overrides_a_competent_no(
    tmp_path: Path, use_ss: bool, use_xdpyinfo: bool
) -> None:
    """KILLS: three mutations of the probe-PRECEDENCE guard, behaviourally.

    The arrangement is the false positive the fragment's own comment records as
    MEASURED: a live process holding ``/tmp/.X<n>-lock`` while listening on
    nothing. If the lock is allowed to answer, ``bd_start_display`` reports
    success for a display nobody serves.

    * ``[ "$competent" -eq 0 ]`` weakened to ``-ge 0`` -- all three cases;
    * the guard deleted outright -- all three cases. The old suite caught this
      one only INCIDENTALLY (a lint-adjacent test noticed ``competent`` became
      unused), which is why ``-ge 0``, which keeps the variable, survived;
    * probe 2 no longer setting ``competent=1`` -- ``ss-says-no`` only, which
      is exactly right: under that mutation xdpyinfo still sets the flag, so
      ``xdpyinfo-says-no`` MUST stay green.
    """
    with _reserved_display_lock() as (num, lock):
        with _lock_holding_process(tmp_path, "Xprobe-standin") as pid:
            lock.write_text(_lock_body(pid), encoding="utf-8")
            box = _probe_toolbox(
                tmp_path,
                ss_rows=(_SS_HEADER,) if use_ss else None,
                xdpyinfo="inactive" if use_xdpyinfo else None,
            )
            result = _run_display(
                f"bd_start_display :{num}", tmp_path, box, isolate_path=True
            )
            _assert_display_reported_inactive(
                result, f":{num}", "probe precedence"
            )


# --- E. the fragment must not leak shell options into its consumers ----------


def _run_without_shell_flags(snippet: str) -> subprocess.CompletedProcess[str]:
    """Deliberately NOT ``_run_fragment``.

    ``_run_fragment`` opens with ``set -eu``. A harness that sets the very
    options it is asked to detect can never observe them being set by the
    subject -- the denominator excludes the subject and the check reports OK.
    This one sets no shell options at all, exactly like install_linux.sh and
    scripts/cloud-setup.sh, which run WITHOUT errexit on purpose so an optional
    step can degrade to a warning instead of aborting the install.
    """
    script = (
        f". {FRAGMENT_REL} || exit {_SOURCE_MISSING}\n"
        f"declare -F bd_system_pkgs >/dev/null 2>&1 || exit {_FUNCTION_UNDEFINED}\n"
        f"declare -F bd_start_display >/dev/null 2>&1 || exit {_FUNCTION_UNDEFINED}\n"
        f"{snippet}\n"
    )
    return subprocess.run(
        [_BASH, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_fragment_does_not_leak_shell_options_into_its_consumers() -> None:
    """Behavioural: after sourcing, a consumer that set no flags must survive.

    Three separate leaks, three separate markers, so the failure message names
    which option leaked:

    * ``set -e``        -- the bare ``false`` would abort the script;
    * ``set -u``        -- expanding an unset variable would abort it;
    * ``set -o pipefail`` -- ``false | true`` would report 1 instead of 0.

    This is not hypothetical bookkeeping. install_linux.sh and cloud-setup.sh
    are full of ``|| echo "(skipped)"`` degradations that a leaked errexit would
    convert into hard aborts, and the gate that forbids ``set -e``
    (tests/test_cloak_install_path.py) reads install_linux.sh ONLY -- so a
    ``set -e`` introduced HERE would still report clean there.
    """
    result = _run_without_shell_flags(
        "false\n"
        "printf 'AFTER-FALSE\\n'\n"
        ': "${_bd_probe_never_set_variable:+set}"\n'
        "printf 'AFTER-UNSET\\n'\n"
        "false | true\n"
        "printf 'PIPE-RC=%s\\n' \"$?\"\n"
        ': "${_bd_probe_never_set_variable}"\n'
        "printf 'AFTER-BARE-UNSET\\n'\n"
        "printf 'CONTINUED\\n'\n"
    )
    _assert_fragment_was_reached(result)
    stdout = result.stdout

    assert "AFTER-FALSE" in stdout, (
        "the consumer aborted on a failing command after sourcing "
        f"{FRAGMENT_REL} -- errexit leaked out of the fragment. stdout="
        f"{stdout!r} stderr={result.stderr!r}"
    )
    assert "AFTER-BARE-UNSET" in stdout, (
        "the consumer aborted on an unset variable after sourcing "
        f"{FRAGMENT_REL} -- nounset leaked out of the fragment. stdout="
        f"{stdout!r} stderr={result.stderr!r}"
    )
    assert "PIPE-RC=0" in stdout, (
        "`false | true` reported non-zero after sourcing the fragment -- "
        f"pipefail leaked out. stdout={stdout!r}"
    )
    assert "CONTINUED" in stdout, (
        f"the consumer did not reach the end. stdout={stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert result.returncode == 0, (
        f"a consumer that set no shell flags exited {result.returncode} after "
        f"sourcing the fragment. stderr={result.stderr!r}"
    )


def test_fragment_leaves_shell_flag_state_untouched() -> None:
    """State comparison, so the diagnostic names the exact option.

    The behavioural test above proves the consequence; this one proves the
    cause, and survives a leak whose consequence happens not to be exercised.
    """
    result = _run_without_shell_flags(
        "printf 'flags_after=%s\\n' \"$-\"\n"
        "printf 'opts_after=%s\\n' \"${SHELLOPTS:-}\"\n"
    )
    _assert_fragment_was_reached(result)

    values = dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
        if "=" in line
    )
    flags = values.get("flags_after", "")
    opts = set(filter(None, values.get("opts_after", "").split(":")))

    assert "e" not in flags, f"errexit is set after sourcing: $-={flags!r}"
    assert "u" not in flags, f"nounset is set after sourcing: $-={flags!r}"
    assert "pipefail" not in opts, f"pipefail is set after sourcing: {sorted(opts)}"
    assert "errexit" not in opts, f"errexit is set after sourcing: {sorted(opts)}"
    assert "nounset" not in opts, f"nounset is set after sourcing: {sorted(opts)}"


def test_fragment_declares_no_shell_options_at_file_scope() -> None:
    """The structural form of the same rule, over comment-stripped code.

    Cheap, and it catches an option set inside a function body with `set -e`
    where the behavioural probe would only see it if that function ran.
    """
    offenders = [
        line.strip()
        for line in _code(FRAGMENT).splitlines()
        if re.match(r"^\s*set\s+[-+]", line)
    ]

    assert not offenders, (
        f"{FRAGMENT_REL} sets shell options: {offenders} -- options set in a "
        "sourced fragment leak into every consumer the instant it sources us"
    )


# --- F. the provisioner exists, is executable, and sources the fragment -------


def test_provisioner_exists_and_is_executable() -> None:
    assert PROVISIONER.is_file(), "scripts/provision_test_host.sh is missing"
    assert os.access(PROVISIONER, os.X_OK), (
        "scripts/provision_test_host.sh is not executable"
    )


@pytest.mark.parametrize(
    "consumer",
    (PROVISIONER, CLOUD_SETUP, INSTALL_LINUX),
    ids=lambda path: path.name,
)
def test_consumer_sources_the_shared_fragment(consumer: Path) -> None:
    directive = re.compile(
        r"^\s*(?:\.|source)\s+\S*system_deps\.sh",
        re.MULTILINE,
    )

    assert directive.search(_code(consumer)), (
        f"{consumer.name} must source {FRAGMENT_REL}"
    )


@pytest.mark.parametrize(
    "consumer",
    (PROVISIONER, CLOUD_SETUP),
    ids=lambda path: path.name,
)
def test_display_consumers_call_the_shared_helper(consumer: Path) -> None:
    """Idempotence lives in one place or it lives nowhere.

    Both scripts previously decided for themselves whether :99 was up, using
    `pgrep -x Xvfb` -- the wrong denominator in both directions, and the direct
    cause of "Fatal server error: Server is already active for display 99".
    """
    code = _code(consumer)

    assert re.search(r"\bbd_start_display\b", code), (
        f"{consumer.name} no longer calls bd_start_display -- a second copy of "
        "the display logic is a second thing that can disagree"
    )
    assert not re.search(r"\bpgrep\b[^\n]*Xvfb", code), (
        f"{consumer.name} decides display liveness with pgrep again; that "
        "tests a PROCESS NAME while the subject is a DISPLAY"
    )


# --- F2. the provisioner's package phase, BEHAVIOURALLY ----------------------
#
# `grep -rn "install_group" tests/` and `grep -rn "run_step" tests/` both
# returned NOTHING before this section existed. The per-group apt split is
# correct in source and carries sixty lines of rationale, and no test in the
# repository read its call sites, drove install_group, or observed a single apt
# invocation. Measured consequence: TWELVE mutations reported "54 passed",
# exit 0 -- including the verbatim D4 defect (collapsing the three per-group
# calls into one `install_group 03b_pkgs_all "..." all core`), regrading gtk
# from optional to core, deleting a call site, hardcoding the criticality in
# either direction, swallowing an apt failure with a bare `record ... "OK"`,
# never invoking apt at all, and turning the empty-list branch into a silent
# `return 0`.
#
# THE PROBE. The REAL scripts/provision_test_host.sh, truncated after its LAST
# install_group call site, with LOGDIR redirected into tmp_path and a fake
# apt-get and sudo first on PATH. It installs nothing, needs no root, and never
# reaches step [4/8]. Anchoring the cut on the call sites themselves rather than
# on the "[3/8]" step header means renumbering the eight steps or editing the
# rationale cannot break it.
#
# WHAT IS PINNED HERE AND WHAT IS READ FROM SOURCE, deliberately asymmetric:
#
#   * LABELS are read from the call sites. They are cosmetic, so hardcoding
#     them would make rewording one a build failure.
#   * SLUGS are never referenced at all.
#   * The group -> CRITICALITY map is PINNED in this file and verified
#     BEHAVIOURALLY. Reading criticality back out of the file under test would
#     make the gate blind to exactly the regrade mutation it exists to catch.
#
# ONE CLAIMED DEFECT IS DELIBERATELY NOT GATED. Appending `|| true; return 0`
# to install_group is BEHAVIOUR-PRESERVING: run_step calls record() before its
# own `return "$rc"`, and all three call sites already end in `|| true`, so the
# return value is discarded either way. It was applied and the FULL observable
# output -- every apt argv and every verdict row -- diffed against pristine
# across all five scenarios: identical in all five. A gate that fired on it
# would be a tripwire on an identity transform, which CLAUDE.md 0 calls a
# soundness bug. The real property behind the request -- "an apt failure is
# RECORDED" -- is gate B, which does kill the mutations that genuinely stop the
# recording or downgrade its severity.

# Which apt transaction each group is entitled to, and how badly it matters.
# PINNED, never derived from the file under test. See the block comment above.
EXPECTED_GROUP_KINDS: dict[str, str] = {
    "core": "core",
    "node": "core",
    "gtk": "optional",
}

# The trailing \S excludes the DEFINITION line `install_group() {`.
_INSTALL_GROUP_CALL_RE = re.compile(r"^\s*install_group\s+\S")
_PROVISION_LOGDIR_LITERAL = 'LOGDIR="/tmp/bd_provision"'

# Logs its whole argv, then behaves like apt-get: anything that is not an
# `install` exits 0, and an `install` whose argument list contains a name in
# $PROVISION_APT_FAIL_NAMES exits 100 the way a real unavailable package does.
_FAKE_APT_GET = r'''#!/bin/sh
printf '%s\n' "$*" >> "$PROVISION_APT_LOG"
_verb=""
for _arg in "$@"; do
    case "$_arg" in
        -*) continue ;;
    esac
    _verb="$_arg"
    break
done
[ "$_verb" = "install" ] || exit 0
for _arg in "$@"; do
    for _bad in ${PROVISION_APT_FAIL_NAMES:-}; do
        if [ "$_arg" = "$_bad" ]; then
            echo "E: Unable to locate package $_arg" >&2
            exit 100
        fi
    done
done
exit 0
'''

_FAKE_SUDO = r'''#!/bin/sh
while [ "$#" -gt 0 ]; do
    case "$1" in
        -*) shift ;;
        *) break ;;
    esac
done
exec "$@"
'''


def _package_phase_call_sites() -> dict[str, str]:
    """``{group: label}`` for every ``install_group`` call in the provisioner."""
    sites: dict[str, str] = {}
    for line in _code(PROVISIONER).splitlines():
        if not _INSTALL_GROUP_CALL_RE.match(line):
            continue
        tokens = shlex.split(line.split("||", 1)[0])
        if len(tokens) >= 5:
            sites[tokens[3]] = tokens[2]
    return sites


def _build_package_phase_probe(path: Path) -> None:
    """The real provisioner, cut after its last install_group call site.

    MAINTENANCE COST, documented rather than discovered: if the three call
    sites are ever refactored into a loop or an ``if``, truncating after the
    last one leaves an unterminated compound. That does NOT report OK -- the
    generated probe is run through ``bash -n`` and the gate fails with an
    explicit UNKNOWN naming the cause.
    """
    raw_lines = _read(PROVISIONER).splitlines()
    code_lines = _code(PROVISIONER).splitlines()
    call_sites = [
        index
        for index, line in enumerate(code_lines)
        if _INSTALL_GROUP_CALL_RE.match(line)
    ]
    assert call_sites, (
        "UNKNOWN: scripts/provision_test_host.sh has no install_group call "
        "site, so this probe cannot locate its subject. Either the package "
        "phase was removed -- which is a defect -- or it was refactored and "
        "this probe needs a new anchor."
    )

    probe = "\n".join(raw_lines[: call_sites[-1] + 1])
    assert probe.count(_PROVISION_LOGDIR_LITERAL) == 1, (
        f"UNKNOWN: expected exactly one {_PROVISION_LOGDIR_LITERAL!r} to "
        "redirect; without it the probe writes into the operator's real "
        "/tmp/bd_provision"
    )
    probe = probe.replace(
        _PROVISION_LOGDIR_LITERAL, 'LOGDIR="${PROVISION_TEST_LOGDIR:?}"'
    )
    probe += '\nprintf "%s" "$ROWS" > "${PROVISION_TEST_ROWS:?}"\nexit 0\n'
    _write_stub(path, probe)

    parsed = subprocess.run(
        [_BASH, "-n", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert parsed.returncode == 0, (
        "UNKNOWN: the generated package-phase probe does not parse, so nothing "
        "below could be measured. The install_group call sites have probably "
        "moved inside a compound command (a loop or an `if`), which leaves the "
        f"truncation unterminated. bash -n said:\n{parsed.stderr}"
    )


def _run_package_phase(
    tmp_path: Path,
    *,
    fail_names: tuple[str, ...] = (),
    fragment_body: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str], dict[str, str]]:
    """Run the probe and return ``(completed, apt argv lines, {label: result})``.

    ``fragment_body`` is the ONLY fault injection, and it is used by the
    empty-list case alone: it builds a throwaway repo whose fragment is a stub.
    Every other case sources the REAL scripts/lib/system_deps.sh. That
    asymmetry is deliberate -- a stub installed for every test is how
    ``_display_stub_dir`` made two thirds of ``_bd_display_active``
    unreachable.
    """
    stub_bin = tmp_path / "aptbin"
    stub_bin.mkdir(exist_ok=True)
    _write_stub(stub_bin / "apt-get", _FAKE_APT_GET)
    _write_stub(stub_bin / "sudo", _FAKE_SUDO)

    apt_log = tmp_path / "apt-calls.log"
    rows_file = tmp_path / "rows.txt"
    logdir = tmp_path / "provision-logs"

    if fragment_body is None:
        repo = REPO_ROOT
    else:
        repo = tmp_path / "fake-repo"
        (repo / "bulk_downloader").mkdir(parents=True, exist_ok=True)
        (repo / "bulk_downloader" / "__init__.py").write_text(
            '__version__ = "package-phase-probe"\n', encoding="utf-8"
        )
        (repo / "scripts" / "lib").mkdir(parents=True, exist_ok=True)
        (repo / "scripts" / "lib" / "system_deps.sh").write_text(
            fragment_body, encoding="utf-8"
        )

    probe = tmp_path / "package-phase-probe.sh"
    _build_package_phase_probe(probe)

    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{stub_bin}{os.pathsep}{env['PATH']}",
            "PROVISION_APT_LOG": str(apt_log),
            "PROVISION_APT_FAIL_NAMES": " ".join(fail_names),
            "PROVISION_TEST_LOGDIR": str(logdir),
            "PROVISION_TEST_ROWS": str(rows_file),
        }
    )
    # The repo is ALWAYS named explicitly: the probe lives in tmp_path, so
    # find_repo's implicit candidates would miss the marker and hard-exit 2
    # before reaching the subject.
    completed = subprocess.run(
        [_BASH, str(probe), str(repo)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    apt_calls = (
        apt_log.read_text(encoding="utf-8").splitlines() if apt_log.is_file() else []
    )
    rows: dict[str, str] = {}
    if rows_file.is_file():
        for line in rows_file.read_text(encoding="utf-8").splitlines():
            fields = line.split("|")
            if len(fields) >= 2:
                rows[fields[0]] = fields[1]
    return completed, apt_calls, rows


def _install_transactions(apt_calls: list[str]) -> list[frozenset[str]]:
    """The package set of every apt INSTALL transaction, flags discarded."""
    transactions: list[frozenset[str]] = []
    for call in apt_calls:
        tokens = call.split()
        if "install" not in tokens:
            continue
        arguments = tokens[tokens.index("install") + 1 :]
        transactions.append(
            frozenset(token for token in arguments if not token.startswith("-"))
        )
    return transactions


def _assert_package_phase_reached(
    completed: subprocess.CompletedProcess[str], rows: dict[str, str]
) -> dict[str, str]:
    """Rows and call sites, or a distinct UNKNOWN for "it never got there"."""
    assert rows, (
        "UNKNOWN: the package-phase probe recorded no verdict rows at all, so "
        "it never reached its subject and nothing below means anything. "
        f"rc={completed.returncode} stdout={completed.stdout[-2000:]!r} "
        f"stderr={completed.stderr[-2000:]!r}"
    )
    sites = _package_phase_call_sites()
    missing = [group for group in GROUP_ORDER if group not in sites]
    assert not missing, (
        f"UNKNOWN: scripts/provision_test_host.sh has no install_group call "
        f"site for {missing}, so those groups' verdict rows cannot be "
        f"identified. Call sites found: {sites}"
    )
    return sites


def test_package_phase_issues_one_partitioned_transaction_per_group(
    tmp_path: Path,
) -> None:
    """KILLS the D4 collapse, group mixing, a deleted call site, and both regrades.

    Run with the whole gtk list unavailable, which is the case the design
    exists for: apt is ALL-OR-NOTHING, so one bad name in a combined list
    installs NOTHING -- no interpreter, no SPA toolchain, no display libraries
    -- and reports it as a single failed row that cannot say which name was to
    blame. Per-group transactions turn that into "gtk is absent, core and node
    are installed", which is the difference between a provisioned host and a
    bare one.

    Measured, each of these reported 54 passed before this gate existed:

    * the verbatim D4 defect, ``install_group 03b_pkgs_all "..." all core``;
    * ``install_group`` always asking ``bd_system_pkgs all`` (names mixed
      across transactions);
    * a call site passing the wrong group;
    * the gtk call site deleted;
    * gtk regraded core (the WARN becomes a blocking FAIL) and, in the other
      direction, the kind hardcoded so a core failure downgrades to WARN;
    * ``run_step`` replaced by a bare apt call plus ``record "$label" "OK"``,
      and apt never actually invoked.

    Transaction membership is compared with ``==``, never ``issubset``: a
    superset assertion cannot see an added or mixed name, which is the same
    blindness that let a bogus package into the gtk list unnoticed.
    """
    live = {group: set(_packages(group)) for group in GROUP_ORDER}
    drift = {
        group: sorted(live[group])
        for group in GROUP_ORDER
        if live[group] != set(EXPECTED_GROUPS[group])
    }
    assert not drift, (
        f"UNKNOWN: the fragment's package lists have drifted from "
        f"EXPECTED_GROUPS ({drift}) -- fix that pin first (see "
        "test_bd_system_pkgs_returns_exactly_the_contracted_packages). "
        "Partitioning cannot be judged against a stale denominator."
    )

    completed, apt_calls, rows = _run_package_phase(
        tmp_path, fail_names=EXPECTED_GROUPS["gtk"]
    )
    transactions = _install_transactions(apt_calls)

    assert len(transactions) >= len(GROUP_ORDER), (
        f"expected one apt install transaction per group ({len(GROUP_ORDER)}), "
        f"got {len(transactions)}. apt was called with: {apt_calls}"
    )
    expected_sets = {
        group: frozenset(EXPECTED_GROUPS[group]) for group in GROUP_ORDER
    }
    matched: list[str] = []
    for transaction in transactions:
        names = [
            group for group, names_ in expected_sets.items() if transaction == names_
        ]
        assert names, (
            f"an apt install transaction carried {sorted(transaction)}, which "
            "is not EXACTLY one package group. One transaction per group is "
            "the whole design: apt is all-or-nothing, so a mixed or combined "
            f"list makes one bad name install nothing. apt calls: {apt_calls}"
        )
        matched.append(names[0])
    assert sorted(matched) == sorted(GROUP_ORDER), (
        f"the apt transactions covered {sorted(matched)}, expected exactly one "
        f"each of {sorted(GROUP_ORDER)}. apt calls: {apt_calls}"
    )

    sites = _assert_package_phase_reached(completed, rows)
    for group in ("core", "node"):
        assert rows.get(sites[group]) == "OK", (
            f"the {group} group did not get its own successful transaction "
            f"while gtk was failing: row {rows.get(sites[group])!r}, expected "
            f"'OK'. That is the all-or-nothing collapse this split prevents. "
            f"Rows: {rows}"
        )
    assert rows.get(sites["gtk"]) == "WARN", (
        f"gtk's apt failure was recorded {rows.get(sites['gtk'])!r}, expected "
        "'WARN'. gtk is graded 'optional' because the capability is probed BY "
        "DOING at step [6/8] -- 'FAIL' means it was regraded core and a host "
        "with a working display from elsewhere now fails the verdict; 'OK' "
        f"means the failure was swallowed. Rows: {rows}"
    )


def test_a_core_tier_apt_failure_is_recorded_as_blocking(tmp_path: Path) -> None:
    """KILLS: hardcoding the criticality to ``optional``, and any swallowed failure.

    ``core`` is graded core because every step from [4/8] to [8/8] gates on
    venv/bin/python, and the venv cannot be built without the interpreter and
    its own package manager. A WARN there would let the verdict certify a host
    on which nothing downstream is meaningful.

    The other half is contagion: node and gtk must still read OK, which is only
    possible because they got their OWN transactions.
    """
    completed, apt_calls, rows = _run_package_phase(
        tmp_path, fail_names=EXPECTED_GROUPS["core"]
    )
    sites = _assert_package_phase_reached(completed, rows)

    assert rows.get(sites["core"]) == "FAIL", (
        f"apt exited 100 for the core group and the row read "
        f"{rows.get(sites['core'])!r}, expected 'FAIL'. 'WARN' means the "
        "criticality was downgraded; 'OK' means the failure never reached the "
        f"verdict at all. Rows: {rows}. apt calls: {apt_calls}"
    )
    for group in ("node", "gtk"):
        assert rows.get(sites[group]) == "OK", (
            f"the {group} group read {rows.get(sites[group])!r} while only "
            "core's packages were unavailable -- the groups are sharing a "
            f"transaction. Rows: {rows}. apt calls: {apt_calls}"
        )


@pytest.mark.parametrize("status", (0, 1), ids=("returns-0-prints-nothing", "returns-1"))
def test_an_empty_package_list_blocks_and_runs_no_installer(
    tmp_path: Path, status: int
) -> None:
    """KILLS: the empty-list branch turned into a silent ``return 0``, or ``OK``.

    THE DECISIVE HALF IS THE FIRST ASSERTION. ``apt-get install -y`` with ZERO
    package arguments exits 0 having installed nothing -- measured on this host
    -- so an installer handed an empty list reports success while installing
    nothing, and command substitution DISCARDS bd_system_pkgs' non-zero exit,
    which is how the empty list gets there in the first place. Asserting only
    on the recorded row would miss a variant that runs apt and then records
    UNKNOWN anyway.

    Both spellings of "no list" are covered because they are different code
    paths: a lookup that fails (status 1) and one that succeeds while printing
    nothing (status 0). The second is the dangerous one -- ``if ! pkgs="$(...)"``
    never fires for it.

    UNKNOWN rather than FAIL is the contract: what failed is the DENOMINATOR,
    not the capability. The script does not know what this group's deps ARE,
    which is a different statement from "this host lacks them". Both block.
    """
    completed, apt_calls, rows = _run_package_phase(
        tmp_path,
        fragment_body=(
            f"bd_system_pkgs() {{ return {status}; }}\n"
            "bd_start_display() { return 0; }\n"
            ":\n"
        ),
    )
    sites = _assert_package_phase_reached(completed, rows)

    assert _install_transactions(apt_calls) == [], (
        "the installer was invoked despite an empty package list. `apt-get "
        "install -y` with no package arguments exits 0 having installed "
        "nothing, so this is exactly how a provisioner reports success while "
        f"provisioning nothing. apt calls: {apt_calls}"
    )
    for group in GROUP_ORDER:
        assert rows.get(sites[group]) == "UNKNOWN", (
            f"bd_system_pkgs {group} returned nothing and the row read "
            f"{rows.get(sites[group])!r}, expected 'UNKNOWN'. A step that "
            "could not be EVALUATED has to say so and block -- reporting OK "
            f"because nothing was examined is worse than having no step. "
            f"Rows: {rows}"
        )


# --- G. anti-drift: the package names live in exactly one file ---------------


def _package_bearing_variables(code: str) -> dict[str, list[str]]:
    """Variables assigned a LITERAL value containing fragment-owned packages.

    One level deep and literal-only by design: values built with ``$(...)`` or
    backticks are exactly how a consumer is SUPPOSED to get the list
    (``pkgs="$(bd_system_pkgs gtk)"``), so they are not offenders.
    """
    bearing: dict[str, list[str]] = {}
    for line in _logical_lines(code):
        match = _ASSIGN_RE.match(line)
        if match is None:
            continue
        name, value = match.group(1), match.group(2)
        if "$(" in value or "`" in value:
            continue
        hits = sorted(ALL_PACKAGES & _tokens(value))
        if hits:
            bearing.setdefault(name, []).extend(hits)
    return bearing


@pytest.mark.parametrize(
    "consumer",
    CONSUMERS,
    ids=lambda path: path.name,
)
def test_consumers_do_not_restate_package_names(consumer: Path) -> None:
    """The point of the fragment: three scripts cannot disagree about deps.

    Scanned over UNQUOTED CODE. Two rounds of over-sensitivity were measured
    off this one assertion, and both were gates firing on identity:

    * the original scanned the whole raw file, so writing "installs the GTK
      typelibs (python3-gi)" in a COMMENT failed the build. Fixed by stripping
      comments;
    * the comment-stripped version still failed on an operator hint --
      ``echo "(on 22.04 you may need libgtk-3-0 instead of libgtk-3-0t64)"`` --
      because ``_strip_shell_comments`` blanks ``#`` comments and not echo
      prose. Reproduced before it was fixed.

    A package name inside a quoted string cannot be word-split into separate
    apt arguments: ``apt-get install -y "xvfb libgtk-3-0t64"`` is ONE bogus
    argument, not a package list. Quoted text is therefore prose by
    construction, exactly like a comment, so the scan runs over
    ``_unquoted_code``.

    TEETH RETAINED, and each was measured on the mutation rather than argued:

    * ``apt-get install -y xvfb libgtk-3-0t64`` (bare arguments) is still
      caught here;
    * ``PKGLIST=xvfb`` (bare assignment) is still caught here;
    * ``PKGS="xvfb libgtk-3-0t64"`` followed by ``apt_i $PKGS`` is caught by
      ``test_no_consumer_hardcodes_an_apt_package_list``, whose
      ``_package_bearing_variables`` deliberately keeps reading ``_code`` with
      quotes INTACT. Quote-blanking that helper too would lose the case.

    NEW BLIND SPOT, declared rather than papered over: a discriminating package
    name inside a quoted string that is neither an assignment value nor an apt
    argument is now invisible here. It is also inert -- it cannot become an
    argv element. A name inside a HEREDOC BODY is still caught, deliberately: a
    heredoc body can be a generated script that really does run apt.
    """
    assert consumer.is_file(), f"{consumer.name} is missing"
    code = _unquoted_code(consumer)

    restated = [name for name in DISCRIMINATING_PACKAGES if name in code]
    assert not restated, (
        f"{consumer.name} restates package names owned by {FRAGMENT_REL}: "
        f"{restated} -- call bd_system_pkgs instead"
    )


def test_fragment_is_the_one_file_that_names_the_packages() -> None:
    """The inverse check: absence everywhere is only meaningful if the
    fragment itself still holds the names -- in CODE, not in a comment."""
    code = _code(FRAGMENT)

    missing = [name for name in DISCRIMINATING_PACKAGES if name not in code]
    assert not missing, (
        f"{FRAGMENT_REL} does not name {missing} in code -- the anti-drift "
        "check would then be asserting over a package list nobody owns"
    )


@pytest.mark.parametrize(
    "consumer",
    CONSUMERS + (CAPTURE_SH,),
    ids=lambda path: path.name,
)
def test_no_consumer_hardcodes_an_apt_package_list(consumer: Path) -> None:
    """Covers the names absence cannot check (git, python3.12, npm).

    Those are legitimate commands elsewhere in these files, so the subject is
    narrowed to apt argument positions rather than dropped -- otherwise the
    anti-drift denominator would silently exclude a quarter of the packages.

    WHAT THIS COVERS, precisely, so the docstring is not a false claim:

    * ``apt install``, ``apt-get install``, ``apt-get -y install``,
      ``apt-get --yes install`` and cloud-setup's ``apt_i`` helper -- the flag
      forms matter, because ``apt-get -y install xvfb`` used to slip past a
      pattern anchored on ``apt-get install``;
    * package names appearing literally on such a line;
    * ONE level of variable indirection in the same file:
      ``PKGS="git python3.12"`` followed by ``apt_i $PKGS``. The variable's
      value must be a literal; ``pkgs="$(bd_system_pkgs gtk)"`` is the correct
      idiom and is deliberately not an offender.

    WHAT IT DOES NOT COVER: values assembled through command substitution,
    ``eval``, indirect expansion (``${!name}``), arrays populated element by
    element, a list read from another file, or a variable assigned in one file
    and used in another. Those are not currently reachable in these scripts, and
    a check that claimed them would be claiming a denominator it does not have.
    """
    assert consumer.is_file(), f"{consumer.name} is missing"
    code = _code(consumer)
    bearing = _package_bearing_variables(code)

    offenders: list[tuple[str, str]] = []
    for line in _logical_lines(code):
        if not _APT_INSTALL_RE.search(line):
            continue
        for name in sorted(ALL_PACKAGES & _tokens(line)):
            offenders.append((name, line.strip()))
        for referenced in _VAR_REF_RE.findall(line):
            if referenced in bearing:
                offenders.append(
                    (f"${referenced} -> {bearing[referenced]}", line.strip())
                )

    assert not offenders, (
        f"{consumer.name} passes fragment-owned packages to apt directly: "
        f"{offenders} -- capture bd_system_pkgs into a variable instead"
    )


def test_anti_drift_predicates_cover_every_package_name() -> None:
    """The denominator check for the two predicates above.

    Every name in ALL_PACKAGES must be reachable by at least one of them, or a
    package could be hardcoded in a place neither looks and both would report
    clean.
    """
    covered = set(DISCRIMINATING_PACKAGES) | set(AMBIGUOUS_PACKAGES)
    uncovered = sorted(set(ALL_PACKAGES) - covered)

    assert not uncovered, (
        f"{uncovered} are in no anti-drift predicate -- add each to "
        "DISCRIMINATING_PACKAGES (if the bare name can only be a package) or "
        "to AMBIGUOUS_PACKAGES (if it is also a command)"
    )
    stale = sorted(covered - set(ALL_PACKAGES))
    assert not stale, (
        f"{stale} are listed in an anti-drift predicate but are in no package "
        "group -- the predicate is asserting over a name nobody owns"
    )


def test_apt_install_predicate_self_check() -> None:
    """The instrument, again. Each of these was a real blind spot."""
    positives = (
        "apt-get install -y xvfb",
        "apt install python3-gi",
        "apt-get -y install xvfb",
        "apt-get --yes install libcairo2",
        "  step 'GTK' optional apt_i xvfb libgtk-3-0t64",
        "sudo apt-get install -y nodejs npm",
    )
    for line in positives:
        assert _APT_INSTALL_RE.search(line), f"predicate missed {line!r}"

    negatives = ("apt-get update", "apt-cache policy xvfb", "aptitude search git")
    for line in negatives:
        assert not _APT_INSTALL_RE.search(line), f"predicate cried wolf on {line!r}"

    indirect = _package_bearing_variables(
        'PKGS="git python3.12"\nSAFE="$(bd_system_pkgs gtk)"\nARR=(xvfb libcairo2)\n'
    )
    assert "PKGS" in indirect, "literal indirection not detected"
    assert "ARR" in indirect, "array-literal indirection not detected"
    assert "SAFE" not in indirect, (
        "a value built from bd_system_pkgs must NOT be an offender -- that is "
        "the idiom this whole fragment exists to make people use"
    )


# --- H. the gui-parity inventory is regenerated where it matters -------------


def test_install_linux_actually_invokes_the_gui_parity_regen() -> None:
    """KILLS: neutering the regen to ``if ! true; then``.

    The previous version of this test was ``INVENTORY_TOOL in _code(...)`` --
    a substring grep. Measured: replacing install_linux.sh's real invocation
    ``if ! "$VPYTHON" tools/gui_parity_inventory.py >"$_parity_err" 2>&1; then``
    with ``if ! true; then`` left the whole suite at 54 passed, exit 0, because
    the string still occurs eight lines above in the guard
    ``[ -f "$INSTALL_DIR/tools/gui_parity_inventory.py" ]``. The gate was
    certifying its own subject's obituary.

    ``_tool_invocations`` requires the tool to sit in argument position of an
    interpreter AND to survive quote-blanking, so a guard, a diagnostic string
    and a comment are all correctly not invocations. See its docstring for what
    it does NOT prove (branch reachability).

    reports/ is gitignored and build-time generated, so a stale unzip-overlay
    copy survives ``git clean -fd`` and reads as inventory drift.
    """
    hits = _tool_invocations(INSTALL_LINUX, INVENTORY_TOOL)

    assert len(hits) == 1, (
        f"install_linux.sh must EXECUTE {INVENTORY_TOOL} exactly once; found "
        f"{len(hits)} invocations (lines {hits}). Every code line that names "
        f"it: {_tool_mentions(INSTALL_LINUX, INVENTORY_TOOL)}"
    )


def test_provisioner_actually_invokes_the_gui_parity_regen() -> None:
    """KILLS: deleting or neutering the provisioner's step [7/8] regen.

    Same substring hole as install_linux.sh, and the provisioner has THREE
    non-invoking mentions of the tool (an ``[ ! -f ... ]`` guard and an UNKNOWN
    diagnostic string as well as the real ``run_step`` line), so a grep here
    was even further from its subject.
    """
    hits = _tool_invocations(PROVISIONER, INVENTORY_TOOL)

    assert len(hits) == 1, (
        f"scripts/provision_test_host.sh must EXECUTE {INVENTORY_TOOL} exactly "
        f"once; found {len(hits)} invocations (lines {hits}). Every code line "
        f"that names it: {_tool_mentions(PROVISIONER, INVENTORY_TOOL)}"
    )


def test_capture_regen_is_ordered_against_every_prerequisite() -> None:
    """Statement order over comment-stripped CODE, against four anchors.

    The old version of this test compared two ``str.find`` offsets against the
    raw file and asserted only ``regen < parallel lane``. That predicate is TRUE
    both before and after the defect it was supposed to guard, so it certified a
    fix it structurally could not observe. Worse, the regen block's own comment
    quotes ``sudo systemctl stop bulkdownloader``, so a raw-text search would
    find the COMMENT and report the ordering satisfied while the command ran
    afterwards.

    Each anchor is load-bearing:

    * ``cd "$BD_HOME"`` and ``mkdir -p "$OUT"`` -- the regen runs
      ``venv/bin/python`` by relative path and redirects into ``$OUT``;
    * ``sudo systemctl stop`` and the is-active confirmation --
      tools/gui_parity_inventory.py imports bulk_downloader.app, whose module
      body unconditionally runs db_init(), db_integrity_check() and five
      scheduler/thread starters. Run while the service is live, that is a second
      process integrity-checking and scheduling against the same BD_HOME
      database;
    * the parallel lane -- a regen after the suite proves nothing.

    The "exactly one" count is over ``_tool_invocations``, not over
    ``INVENTORY_TOOL in line``. The substring form cried wolf: adding the same
    defensive ``[ ! -f "$BD_HOME/tools/gui_parity_inventory.py" ]`` guard that
    install_linux.sh already carries made the count read 2 and failed a
    correct file. Measured before and after -- the invocation count stays 1.
    """
    code = _code(CAPTURE_SH)

    regen_lines = _tool_invocations(CAPTURE_SH, INVENTORY_TOOL)
    assert len(regen_lines) == 1, (
        f"expected exactly one {INVENTORY_TOOL} invocation in capture.sh code, "
        f"found {len(regen_lines)} (lines {regen_lines}) -- with more than "
        "one, 'the regen' is not a single thing to order. Every code line that "
        f"names it: {_tool_mentions(CAPTURE_SH, INVENTORY_TOOL)}"
    )
    regen = regen_lines[0] - 1

    anchors = {
        'cd "$BD_HOME"': _code_line_index(
            code, r'^\s*cd\s+"\$BD_HOME"', 'cd "$BD_HOME"'
        ),
        'mkdir -p "$OUT"': _code_line_index(
            code, r'\bmkdir\s+-p\s+"\$OUT"', 'mkdir -p "$OUT"'
        ),
        "systemctl stop bulkdownloader": _code_line_index(
            code, r"systemctl\s+stop\s+bulkdownloader", "the service stop"
        ),
        "systemctl is-active": _code_line_index(
            code,
            r"systemctl\s+is-active\s+--quiet\s+bulkdownloader",
            "the is-active confirmation",
        ),
    }
    lane = _code_line_index(
        code, re.escape(PARALLEL_LANE_MARKER), PARALLEL_LANE_MARKER
    )

    for label, index in anchors.items():
        assert index < regen, (
            f"capture.sh runs the gui-parity regen (code line {regen + 1}) "
            f"BEFORE {label} (code line {index + 1}). "
            "See this test's docstring for why each prerequisite is fatal."
        )
    assert regen < lane, (
        f"capture.sh regenerates the inventory at code line {regen + 1}, after "
        f"the parallel lane starts at code line {lane + 1}; the suite would "
        "still compare against the stale copy"
    )


_EXIT_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*_EXIT)=")


def _capture_verdict_command(code: str) -> str:
    """The whole joined ``tools/capture_verdict.py`` command line, or a loud
    failure. An absent command must not read as "every stage is wired"."""
    for _, joined, _unused in _logical_pairs(code, code):
        if "tools/capture_verdict.py" in joined:
            return joined
    raise AssertionError(
        "UNKNOWN: capture.sh has no tools/capture_verdict.py command line, so "
        "the stage-exit wiring check has no subject. An absent verdict call is "
        "not a satisfied wiring."
    )


def _parity_block_exit_vars(code: str) -> list[str]:
    """Every ``*_EXIT`` variable ASSIGNED inside the [2a/9] parity block.

    Both boundaries are located explicitly and fail loudly when absent: an
    empty variable set would otherwise certify the wiring vacuously, which is
    the exact shape of a gate whose denominator excludes its subject.
    """
    lines = code.splitlines()
    start = _code_line_index(
        code, r"^\s*PARITY_JSON=", "the [2a/9] parity block start (PARITY_JSON=)"
    )
    end = None
    for index in range(start, len(lines)):
        if re.search(r"\brun_with_heartbeat\b", lines[index]):
            end = index
            break
    assert end is not None, (
        "UNKNOWN: no run_with_heartbeat after the [2a/9] parity block, so the "
        "block has no end boundary and the stage-exit scan has no denominator"
    )

    names: list[str] = []
    for line in lines[start:end]:
        match = _EXIT_ASSIGN_RE.match(line)
        if match is not None and match.group(1) not in names:
            names.append(match.group(1))
    return names


def test_capture_feeds_every_parity_stage_exit_to_the_verdict() -> None:
    """KILLS: deleting ``--stage-exit "parity-inventory=$PARITY_EXIT"``.

    Measured: with that flag removed the whole suite still reported 54 passed,
    exit 0. The regen could then skip, fail, write nothing, or degrade to the
    ENDPOINT_CATALOG.md fallback, and the capture would still certify green --
    PARITY_EXIT would be computed with care and thrown away.

    The wiring is DERIVED, not grepped for: the variables come from what the
    [2a/9] block actually assigns and the flags from the verdict command line
    actually written, so renaming PARITY_EXIT, reordering the flags or adding
    a second parity stage exit all behave correctly instead of crying wolf.

    SCOPE, deliberately narrow: the denominator is the [2a/9] block this cut
    owns, NOT every ``*_EXIT`` in capture.sh. A whole-file rule would fire
    immediately on STOP_REQUEST_EXIT, which is assigned and only echoed --
    genuinely diagnostic, not a stage verdict. Adding a NEW ``*_EXIT`` inside
    [2a/9] without wiring it does fail here, and that is a correct finding: a
    new stage exit needs a verdict decision.

    This is a STATEMENT-level check. It proves the wiring exists in source, not
    that the verdict ran -- capture.sh's verdict call is far past the [2b/9]
    sentinel the execution probe truncates at.
    """
    code = _code(CAPTURE_SH)
    verdict = _capture_verdict_command(code)
    variables = _parity_block_exit_vars(code)

    assert variables, (
        "UNKNOWN: the [2a/9] parity block assigns no *_EXIT variable at all, "
        "so this check has nothing to trace to the verdict"
    )
    unwired = [
        name
        for name in variables
        if f"${name}" not in verdict and "${%s}" % name not in verdict
    ]
    assert not unwired, (
        f"capture.sh computes {unwired} in the [2a/9] gui-parity block and "
        "never hands them to tools/capture_verdict.py, so the capture can "
        "certify green with an inventory that was skipped, failed, or built "
        f"from the ENDPOINT_CATALOG.md fallback. Verdict command: {verdict!r}"
    )


# --- I. capture.sh EXECUTION order, not text order ---------------------------
#
# Statement order is necessary, not sufficient: the assertions above would still
# pass if the block were wrapped in `if false; then` or moved into a function
# nobody calls. This probe RUNS capture.sh's prefix with a fake venv/bin/python,
# a fake systemctl and a fake sudo that append to one shared, ordered log, and
# asserts the order of what actually executed.


CAPTURE_SENTINEL = "# ── [2b/9]"


def _build_capture_probe(path: Path) -> None:
    source = _read(CAPTURE_SH)
    assert source.count(CAPTURE_SENTINEL) == 1, (
        f"capture.sh must contain exactly one {CAPTURE_SENTINEL!r} sentinel; "
        "the probe splits the script there"
    )
    probe = source.split(CAPTURE_SENTINEL, 1)[0]
    replacements = {
        'OUT="/tmp/bd_capture"': 'OUT="${CAPTURE_TEST_OUT:?}"',
        'ARCHIVE="/tmp/bd_capture.tar.gz"': 'ARCHIVE="${CAPTURE_TEST_ARCHIVE:?}"',
    }
    for old, new in replacements.items():
        assert probe.count(old) == 1, f"capture.sh no longer contains {old!r}"
        probe = probe.replace(old, new)
    probe += "\nexit 0\n"
    _write_stub(path, probe)


# One fake interpreter, shared by the capture.sh probe and the provisioner
# probe below, because both scripts run the SAME gui-parity regen and the SAME
# read-back of `route_source`. Two copies would be two things that can disagree
# about what the generator does -- which is the drift this whole cut exists to
# stop.
_FAKE_PYTHON = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with open(os.environ["PROBE_ORDER_LOG"], "a", encoding="utf-8") as stream:
    stream.write("python " + json.dumps(args) + "\n")

# `-c <program> <args...>`: RUN capture.sh's own program rather than opining on
# what it would have decided. sys.argv[0] is "-c" for a real `python -c`, so the
# program's sys.argv[1] must be the FIRST argument after the program text -- the
# obvious slice is off by one and makes the probe read the program itself as its
# own argument.
if args[:1] == ["-c"]:
    sys.argv = ["-c"] + args[2:]
    exec(compile(args[1], "<capture-probe -c>", "exec"), {"__name__": "__main__"})
    raise SystemExit(0)

# The gui-parity regen. The real tool writes reports/gui_parity_inventory.json
# and records which route source it used; capture.sh reads that field back
# because the tool exits 0 even when the app import failed and it fell back to
# ENDPOINT_CATALOG.md. A probe that never wrote the file could not reach that
# branch at all -- which is exactly how deleting the check went unnoticed.
if any(arg.endswith("tools/gui_parity_inventory.py") for arg in args):
    report = Path("reports/gui_parity_inventory.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps({"route_source": os.environ["PROBE_ROUTE_SOURCE"]}),
        encoding="utf-8",
    )
    raise SystemExit(0)

if args[:2] == ["-m", "pytest"]:
    marker = next(
        value for value in ("capture_parallel", "capture_serial") if value in args
    )
    junit = next(
        value.split("=", 1)[1] for value in args if value.startswith("--junitxml=")
    )
    Path(junit).parent.mkdir(parents=True, exist_ok=True)
    Path(junit).write_text(
        '<testsuites tests="1"><testsuite tests="1">'
        f'<testcase classname="fake" name="{marker}"/>'
        "</testsuite></testsuites>\n",
        encoding="utf-8",
    )
raise SystemExit(0)
'''


def _run_capture_probe(
    tmp_path: Path,
    *,
    service_stays_active: bool,
    route_source: str = "live url_map",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fake_home = tmp_path / "BulkDownloader"
    fake_bin = tmp_path / "bin"
    (fake_home / "bulk_downloader").mkdir(parents=True)
    (fake_home / "venv" / "bin").mkdir(parents=True)
    (fake_home / "frontend" / "dist").mkdir(parents=True)
    # The fake home must model a HEALTHY box, not a broken one. Without
    # tools/gui_parity_inventory.py a defensive existence guard in capture.sh
    # would correctly skip the regen and this probe would report "never
    # executed" -- a fixture-completeness failure indistinguishable from the
    # defect it is looking for. Measured: adding that guard to capture.sh with
    # the old fixture produced exactly that false failure.
    (fake_home / "tools").mkdir(parents=True)
    fake_bin.mkdir()
    (fake_home / "tools" / "gui_parity_inventory.py").write_text(
        "# capture probe stand-in for the real generator\n", encoding="utf-8"
    )
    (fake_home / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "capture-probe"\n', encoding="utf-8"
    )
    (fake_home / "CHANGELOG.md").write_text("# capture probe\n", encoding="utf-8")
    (fake_home / "frontend" / "dist" / "index.html").write_text(
        "<!doctype html><title>capture probe</title>\n", encoding="utf-8"
    )

    order_log = tmp_path / "order.log"
    _write_stub(fake_home / "venv" / "bin" / "python", _FAKE_PYTHON)
    _write_stub(fake_home / "venv" / "bin" / "pip", "#!/bin/sh\nexit 0\n")
    _write_stub(fake_bin / "sudo", '#!/bin/sh\nexec "$@"\n')
    _write_stub(
        fake_bin / "systemctl",
        "#!/bin/sh\n"
        f'printf \'systemctl %s\\n\' "$*" >> "{order_log}"\n'
        'if [ "${1:-}" = "is-active" ]; then exit '
        f"{'0' if service_stays_active else '3'}; fi\n"
        "exit 0\n",
    )
    _write_stub(fake_bin / "sleep", "#!/bin/sh\nexit 0\n")

    probe = tmp_path / "capture-probe.sh"
    _build_capture_probe(probe)

    env = dict(os.environ)
    env.update(
        {
            "BD_HOME": str(fake_home),
            "CAPTURE_TEST_OUT": str(tmp_path / "capture-out"),
            "CAPTURE_TEST_ARCHIVE": str(tmp_path / "capture.tar.gz"),
            "PROBE_ORDER_LOG": str(order_log),
            "PROBE_ROUTE_SOURCE": route_source,
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
        }
    )
    completed = subprocess.run(
        ["bash", str(probe), "--workers=2"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    entries = (
        order_log.read_text(encoding="utf-8").splitlines()
        if order_log.is_file()
        else []
    )
    return completed, entries


def _first_index(entries: list[str], needle: str, label: str) -> int:
    for index, entry in enumerate(entries):
        if needle in entry:
            return index
    raise AssertionError(
        f"UNKNOWN: capture.sh never executed {label} -- the execution-order "
        f"assertion has no subject. Recorded calls: {entries}"
    )


def test_capture_execution_order_regen_runs_after_the_service_stop(
    tmp_path: Path,
) -> None:
    """Execution order, measured by running the script.

    Statement order can be defeated by an `if false`, a function that is never
    called, or a redirect that fails before the command runs -- and the last of
    those is exactly what happens if the block is hoisted above
    ``mkdir -p "$OUT"``: the redirect into ``$OUT`` fails, the regen never
    executes at all, and this test fails on the ABSENT call rather than on an
    out-of-order one.
    """
    completed, entries = _run_capture_probe(tmp_path, service_stays_active=False)

    assert entries, (
        "the capture probe recorded no calls at all -- it did not get far "
        f"enough to prove anything. stdout={completed.stdout[-2000:]!r} "
        f"stderr={completed.stderr[-2000:]!r}"
    )
    stop_at = _first_index(entries, "systemctl stop", "systemctl stop")
    is_active_at = _first_index(entries, "systemctl is-active", "systemctl is-active")
    regen_at = _first_index(entries, INVENTORY_TOOL, INVENTORY_TOOL)
    lane_at = _first_index(entries, "capture_parallel", "the parallel pytest lane")

    assert stop_at < regen_at, (
        "the gui-parity regen executed BEFORE the service stop. It imports "
        "bulk_downloader.app, whose module body runs db_integrity_check() and "
        f"starts five scheduler groups. Recorded order: {entries}"
    )
    assert is_active_at < regen_at, (
        "the regen executed before the service-inactive confirmation: "
        f"{entries}"
    )
    assert regen_at < lane_at, (
        f"the regen executed after the parallel lane started: {entries}"
    )


def test_capture_skips_the_regen_when_the_service_is_still_active(
    tmp_path: Path,
) -> None:
    """A failed stop must make the inventory UNKNOWN, not run it anyway.

    Ordering alone is positional theatre if a box where the stop did not take
    (no sudo, no systemd, wedged unit) still puts a second process on the live
    database. Unknown is a third state and it fails.
    """
    completed, entries = _run_capture_probe(tmp_path, service_stays_active=True)

    assert entries, (
        f"the capture probe recorded no calls. stderr={completed.stderr[-2000:]!r}"
    )
    assert not any(INVENTORY_TOOL in entry for entry in entries), (
        "capture.sh ran the gui-parity regen while the service was still "
        f"active: {entries}"
    )
    combined = completed.stdout + completed.stderr
    assert "SKIPPED" in combined, (
        "the skip was silent -- a step that did not happen has to say so. "
        f"output tail: {combined[-2000:]!r}"
    )


def test_capture_route_source_check_passes_a_live_url_map_inventory(
    tmp_path: Path,
) -> None:
    """The healthy half of the route_source verification, RUN not read.

    ``exit=0`` here is only meaningful because the sibling test below gets
    ``exit=3`` from the same probe: a check that answers "fine" to everything
    answers nothing. Both cases execute capture.sh's OWN ``-c`` predicate
    against a JSON file the fake generator really wrote, so the branch logic
    under test is capture.sh's rather than a stub's opinion of it.
    """
    completed, entries = _run_capture_probe(
        tmp_path, service_stays_active=False, route_source="live url_map"
    )

    assert entries, (
        f"the capture probe recorded no calls. stderr={completed.stderr[-2000:]!r}"
    )
    assert any("route_source" in entry for entry in entries), (
        "capture.sh never executed the route_source verification -- the "
        f"inventory regen exited 0 and nothing read the result back: {entries}"
    )
    assert "  exit=0" in completed.stdout, (
        "an app-derived inventory must leave PARITY_EXIT at 0. stdout tail: "
        f"{completed.stdout[-2000:]!r}"
    )


def test_capture_route_source_check_degrades_a_catalog_derived_inventory(
    tmp_path: Path,
) -> None:
    """KILLS: deleting capture.sh's route_source ``elif`` branch.

    Measured: with that branch removed the whole suite still reported 54
    passed, exit 0. tools/gui_parity_inventory.py wraps its
    ``import bulk_downloader.app`` in a bare except, falls back to parsing
    ENDPOINT_CATALOG.md, writes a DIFFERENT item set and STILL EXITS 0 -- so
    exit 0 alone certifies a confidently-wrong artifact, and the box goes on
    failing the same reconcile gate the block exists to fix.

    The assertion is on the verdict capture.sh publishes (``exit=3``, which is
    what feeds ``--stage-exit "parity-inventory=$PARITY_EXIT"``), never on the
    ABSENCE of something -- with the branch deleted the value is 0, and an
    absence assertion would have been satisfied by the mutant.
    """
    completed, entries = _run_capture_probe(
        tmp_path, service_stays_active=False, route_source="endpoint catalog"
    )

    assert entries, (
        f"the capture probe recorded no calls. stderr={completed.stderr[-2000:]!r}"
    )
    assert any("route_source" in entry for entry in entries), (
        "capture.sh never executed the route_source verification, so a "
        f"catalog-derived inventory would have passed unexamined: {entries}"
    )
    assert "  exit=3" in completed.stdout, (
        "a catalog-derived inventory must set PARITY_EXIT=3; capture.sh "
        "reported something else, so a silently degraded regen reaches the "
        f"verdict as a pass. stdout tail: {completed.stdout[-2000:]!r}"
    )
    assert "ENDPOINT_CATALOG.md fallback" in completed.stderr, (
        "the degradation was not explained on stderr: "
        f"{completed.stderr[-2000:]!r}"
    )


# --- I2. the provisioner's step [7/8], EXECUTED ------------------------------
#
# Same hole, same shape, different file: nothing ran the provisioner's
# route_source verification either, so deleting the whole `ROUTE_SOURCE=...` +
# `case` block reported 54 passed. The probe splices the provisioner's PRELUDE
# (which is `set -uo pipefail`, usage(), find_repo(), the cd, the LOGDIR mkdir
# and the record()/run_step() definitions -- no apt, no sudo, nothing
# elevated) onto step [7/8] alone.

PROVISIONER_PRELUDE_SENTINEL = (
    "# ------------------------------------------------------------ [2/8] fragment"
)
PROVISIONER_STEP7_SENTINEL = (
    "# ------------------------------------------------- [7/8] gui-parity inventory"
)
PROVISIONER_STEP8_SENTINEL = (
    "# ------------------------------------------------------------- [8/8] verdict"
)


def _build_provisioner_probe(path: Path) -> None:
    source = _read(PROVISIONER)
    for sentinel in (
        PROVISIONER_PRELUDE_SENTINEL,
        PROVISIONER_STEP7_SENTINEL,
        PROVISIONER_STEP8_SENTINEL,
    ):
        assert source.count(sentinel) == 1, (
            f"scripts/provision_test_host.sh must contain exactly one "
            f"{sentinel!r}; the probe splices the script there. If the step "
            "banners were reflowed, move the sentinel -- this is a loud "
            "failure on a structural edit, not a finding about the code."
        )

    probe = (
        source.split(PROVISIONER_PRELUDE_SENTINEL, 1)[0]
        + source.split(PROVISIONER_STEP7_SENTINEL, 1)[1].split(
            PROVISIONER_STEP8_SENTINEL, 1
        )[0]
    )
    assert probe.count(_PROVISION_LOGDIR_LITERAL) == 1, (
        f"UNKNOWN: expected exactly one {_PROVISION_LOGDIR_LITERAL!r} to "
        "redirect; without it the probe writes into the operator's real "
        "/tmp/bd_provision"
    )
    probe = probe.replace(
        _PROVISION_LOGDIR_LITERAL, 'LOGDIR="${PROVISION_TEST_LOGDIR:?}"'
    )
    probe += '\nprintf "%s" "$ROWS" > "${PROVISION_TEST_ROWS:?}"\nexit 0\n'
    _write_stub(path, probe)

    parsed = subprocess.run(
        [_BASH, "-n", str(path)], capture_output=True, text=True, timeout=60
    )
    assert parsed.returncode == 0, (
        "UNKNOWN: the spliced provisioner probe does not parse, so nothing "
        f"below could be measured. bash -n said:\n{parsed.stderr}"
    )


def _run_provisioner_inventory_step(
    tmp_path: Path, *, route_source: str
) -> tuple[subprocess.CompletedProcess[str], list[str], dict[str, str]]:
    repo = tmp_path / "fake-repo"
    (repo / "bulk_downloader").mkdir(parents=True)
    (repo / "tools").mkdir(parents=True)
    (repo / "venv" / "bin").mkdir(parents=True)
    (repo / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "provisioner-probe"\n', encoding="utf-8"
    )
    (repo / "tools" / "gui_parity_inventory.py").write_text(
        "# provisioner probe stand-in for the real generator\n", encoding="utf-8"
    )
    _write_stub(repo / "venv" / "bin" / "python", _FAKE_PYTHON)

    order_log = tmp_path / "order.log"
    rows_file = tmp_path / "rows.txt"
    probe = tmp_path / "provisioner-inventory-probe.sh"
    _build_provisioner_probe(probe)

    env = dict(os.environ)
    env.update(
        {
            "PROBE_ORDER_LOG": str(order_log),
            "PROBE_ROUTE_SOURCE": route_source,
            "PROVISION_TEST_LOGDIR": str(tmp_path / "provision-logs"),
            "PROVISION_TEST_ROWS": str(rows_file),
        }
    )
    completed = subprocess.run(
        [_BASH, str(probe), str(repo)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    entries = (
        order_log.read_text(encoding="utf-8").splitlines()
        if order_log.is_file()
        else []
    )
    rows: dict[str, str] = {}
    if rows_file.is_file():
        for line in rows_file.read_text(encoding="utf-8").splitlines():
            fields = line.split("|")
            if len(fields) >= 2:
                rows[fields[0]] = fields[1]
    return completed, entries, rows


@pytest.mark.parametrize(
    ("route_source", "expected"),
    [
        pytest.param("live url_map", "OK", id="live-url-map"),
        pytest.param("endpoint catalog", "UNKNOWN", id="endpoint-catalog-fallback"),
    ],
)
def test_provisioner_verifies_the_inventory_route_source(
    tmp_path: Path, route_source: str, expected: str
) -> None:
    """KILLS: deleting the provisioner's ``ROUTE_SOURCE=`` + ``case`` block.

    Measured: with that block removed the suite still reported 54 passed, exit
    0. tools/gui_parity_inventory.py wraps its ``import bulk_downloader.app``
    in a bare except, falls back to ENDPOINT_CATALOG.md, writes a different
    item set and STILL EXITS 0 -- so ``run_step ... || true`` records OK for a
    confidently-wrong artifact and the box keeps failing the very reconcile
    gate step [7/8] exists to fix.

    The assertion is that the row EXISTS with the expected verdict, never that
    some row is absent: with the block deleted the row DISAPPEARS entirely, and
    a ``not any(... "OK" ...)`` form would have been satisfied by the mutant.

    Both directions are parametrised for the same reason: a check that answers
    OK to everything answers nothing.
    """
    completed, entries, rows = _run_provisioner_inventory_step(
        tmp_path, route_source=route_source
    )

    assert entries, (
        "the provisioner probe never invoked venv/bin/python, so step [7/8] "
        f"did not run at all. rc={completed.returncode} "
        f"stdout={completed.stdout[-2000:]!r} stderr={completed.stderr[-2000:]!r}"
    )
    assert any(INVENTORY_TOOL in entry for entry in entries), (
        f"the probe never ran {INVENTORY_TOOL}: {entries}"
    )
    assert rows.get("gui-parity inventory") == "OK", (
        "the regen itself did not record OK, so the route_source verdict below "
        f"would be about the wrong thing. Rows: {rows}"
    )
    assert rows.get("inventory route source") == expected, (
        f"with route_source={route_source!r} the provisioner recorded "
        f"{rows.get('inventory route source')!r}, expected {expected!r}. A "
        "missing row means the read-back was deleted; the generator exits 0 "
        "either way, so exit 0 alone does not prove the inventory is "
        f"app-derived. Rows: {rows}"
    )


# --- J. every shell file we touch parses -------------------------------------


@pytest.mark.parametrize(
    "shell_file",
    SHELL_FILES,
    ids=lambda path: path.name,
)
def test_shell_files_parse(shell_file: Path) -> None:
    assert shell_file.is_file(), f"{shell_file} is missing"
    result = subprocess.run(
        ["bash", "-n", str(shell_file)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"bash -n {shell_file.name} failed: {result.stderr}"
    )


# --- K. shellcheck can actually see the single source of truth ---------------


def _shellcheck_directive_offenders(path: Path) -> list[str]:
    """Comments shaped like a shellcheck directive that shellcheck will reject.

    The comment TEXT is derived from the existing instrument -- the raw line
    minus the same-index ``_strip_shell_comments`` line -- rather than
    re-parsed. That is exact (the stripper keeps everything before the ``#``
    and preserves line numbering) and it buys three properties for free, all
    checked against real shellcheck 0.9.0:

    * a directive inside a quoted string is not a directive
      (``echo "# shellcheck disable=SC2086 -- prose"`` is fine, both here and
      in shellcheck);
    * a heredoc body is not a directive;
    * an INLINE directive is detectable -- when the code part of the line is
      non-empty the comment follows a command, which is SC1126 *and* SC1073,
      fatal even when the directive itself is well formed.

    RESIDUE, on purpose: directive VALUES are not validated.
    ``# shellcheck disable=notacode`` aborts under real shellcheck and passes
    here. Guessing shellcheck's value grammar is how a gate starts crying wolf;
    ``test_no_shell_file_fails_to_parse_under_shellcheck`` is the layer that
    owns values.

    ONE KNOWING DIVERGENCE TOWARD STRICTNESS: an unknown KEY
    (``# shellcheck bogus=value``) is flagged although shellcheck only
    info-warns (SC1107). A directive that silently does nothing is a real
    defect, so this stays -- and the message names the key, so a genuinely new
    shellcheck directive is a one-line fix to
    ``_SHELLCHECK_DIRECTIVE_KEYS`` rather than a mystery.
    """
    raw_lines = _read(path).splitlines()
    code_lines = _code(path).splitlines()
    assert len(raw_lines) == len(code_lines), (
        f"UNKNOWN: the comment stripper changed {path.name}'s line count "
        f"({len(raw_lines)} raw vs {len(code_lines)} code) -- the comment "
        "extraction below would then be reading the wrong lines"
    )

    offenders: list[str] = []
    for number, (raw, code) in enumerate(zip(raw_lines, code_lines), start=1):
        comment = raw[len(code) :]
        match = _SHELLCHECK_COMMENT_RE.match(comment)
        if match is None:
            continue
        where = f"{path.name}:{number}"
        if code.strip():
            offenders.append(
                f"{where}: a directive may only precede a command, never "
                f"follow one (SC1126 + SC1073): {raw.strip()}"
            )
            continue
        # A trailing `# ...` after the directive is legal -- the tree uses that
        # form twice -- so only the text before it is the directive.
        tokens = match.group("rest").split("#", 1)[0].split()
        if not tokens:
            offenders.append(
                f"{where}: bare 'shellcheck' comment with no key=value: "
                f"{raw.strip()}"
            )
            continue
        for token in tokens:
            parsed = _SHELLCHECK_TOKEN_RE.match(token)
            if parsed is None:
                offenders.append(
                    f"{where}: {token!r} is not a key=value directive token "
                    f"(put prose on its own comment line above): {raw.strip()}"
                )
                break
            if parsed.group("key") not in _SHELLCHECK_DIRECTIVE_KEYS:
                offenders.append(
                    f"{where}: {parsed.group('key')!r} is not a shellcheck "
                    "directive key; if shellcheck now supports it, add it to "
                    f"_SHELLCHECK_DIRECTIVE_KEYS: {raw.strip()}"
                )
                break
    return offenders


def test_shellcheck_directive_predicate_self_check(tmp_path: Path) -> None:
    """The instrument before the measurement, calibrated against the real tool.

    Every case below was written to a file and linted with shellcheck 0.9.0 in
    this sandbox; the expectations are what it ACTUALLY did, not what the
    documentation implies. The two deliberate divergences are asserted here as
    well, so they stay deliberate instead of decaying into surprises.
    """
    aborts = (
        "# shellcheck disable=SC2086 -- word splitting is the point",
        "# shellcheck disable=SC2086 word splitting is the point",
        "# shellcheck can parse the single source of truth",
        "# shellcheck: prose here",
        "# shellcheck",
        "# shellcheckery is not a word",
        "# shellcheck-ish note about linting",
        "# shellcheck disable = SC2086",
        "#shellcheck prose here",
        "echo hi  # shellcheck disable=SC2086",
    )
    accepts = (
        "# shellcheck disable=SC2086",
        "# shellcheck disable=SC2086  # $SUDO is empty when already root",
        "# shellcheck disable=SC2086,SC2154",
        "# shellcheck source=scripts/lib/system_deps.sh",
        "# shellcheck shell=bash",
        "# shellcheck enable=require-variable-braces",
        "# shellcheck external-sources=true",
        "# SHELLCHECK disable=SC2086",
        '# word "shellcheck" unless it really is a directive.',
        "# `# shellcheck disable=SC2086`, and that suppression was two defects",
        'echo "# shellcheck disable=SC2086 -- prose in a string"',
        "echo '# shellcheck disable=SC2086 -- prose in a string'",
    )

    scratch = tmp_path / "directive-selfcheck.sh"

    def _offenders(line: str) -> list[str]:
        scratch.write_text(
            f'#!/bin/sh\nfoo=1\n{line}\necho "$foo"\n', encoding="utf-8"
        )
        return _shellcheck_directive_offenders(scratch)

    for line in aborts:
        assert _offenders(line), (
            f"the predicate misses {line!r}, which shellcheck 0.9.0 rejects "
            "with SC1072/SC1073 -- and a parse abort silently deletes every "
            "other finding in the file"
        )
    for line in accepts:
        assert not _offenders(line), (
            f"the predicate cries wolf on {line!r}, which shellcheck 0.9.0 "
            "accepts. Over-sensitivity is a soundness bug: a gate that fires "
            "on a harmless comment gets switched off."
        )
    # The two knowing divergences, asserted so they cannot drift silently.
    assert _offenders("# shellcheck bogus=value"), (
        "STRICTER than shellcheck on purpose: an unknown directive key is "
        "silently inert, which is a defect worth naming"
    )
    assert not _offenders("# shellcheck disable=notacode"), (
        "LOOSER than shellcheck on purpose: directive VALUES are shellcheck's "
        "job. If this ever starts flagging, the residue documented in "
        "_shellcheck_directive_offenders is stale"
    )


@pytest.mark.parametrize("shell_file", SHELL_FILES, ids=lambda path: path.name)
def test_no_shell_file_has_a_comment_shaped_like_a_broken_shellcheck_directive(
    shell_file: Path,
) -> None:
    """KILLS: reverting any of the three ``disable=SC2086 -- prose`` lines.

    THE DENOMINATOR WAS THE HOLE. This check used to read the fragment and
    nothing else, and its predicate matched only the HEAD of the line, so
    ``# shellcheck disable=SC2086 -- word splitting is the point: ...`` parsed
    as key='disable' and was certified valid. That exact line was introduced by
    this branch at install_linux.sh:75, install_linux.sh:83 and
    scripts/cloud-setup.sh:324, real shellcheck 0.9.0 rejected all three with
    SC1072/SC1073, and the suite reported 54 passed. Two failures at once: the
    subject was outside the denominator AND outside the predicate.

    A parse abort is not one lost finding, it is ALL of them. Measured on a
    four-line file: with the directive malformed shellcheck reports SC1072 and
    SC1073 and nothing else; with the prose moved to its own line above a bare
    ``# shellcheck disable=SC2086`` the same file reports SC2006, SC2116 and
    SC2086. Three real findings vanish while the directive is broken.

    This half needs no binary, which is the point: neither install_linux.sh nor
    scripts/provision_test_host.sh installs shellcheck (only cloud-setup.sh
    does), so on the operator's box -- which CLAUDE.md 7 calls the gate -- the
    tool-based half below skips and this is the only thing standing.
    """
    offenders = _shellcheck_directive_offenders(shell_file)

    assert not offenders, (
        f"{shell_file.name} has comments that open with the word 'shellcheck' "
        "but are not valid directives, so shellcheck ABORTS the whole file and "
        f"every other finding in it disappears: {offenders}"
    )


def _require_shellcheck(what: str) -> None:
    """Skip LOUDLY when the linter is absent.

    A silent skip is indistinguishable from a pass in a capture log. The
    warning is emitted before the skip because pytest prints its warnings
    summary even under ``-q``, and this file classifies to capture.sh's SERIAL
    lane, which runs ``-n 0`` -- so the line lands in
    ``$OUT/02_pytest_serial.log`` where an operator reading a green capture can
    still see that this gate did not run. (Behaviour under real xdist workers
    is not verified: pytest-xdist is not installed in the sandbox where this
    was written.)

    Absence is deliberately NOT a failure. A host without a linter is not a
    broken host, and a hard failure here would be the cry-wolf mode CLAUDE.md 0
    warns about. The coverage that does not depend on the binary is
    ``test_no_shell_file_has_a_comment_shaped_like_a_broken_shellcheck_directive``,
    which runs over all five files with no tool at all.
    """
    if shutil.which("shellcheck") is not None:
        return
    warnings.warn(
        f"BD-GATE-UNRUNNABLE: shellcheck is not installed, so {what} could not "
        "reach its subject and reports nothing rather than reporting OK. "
        "scripts/cloud-setup.sh installs it; install_linux.sh and "
        "scripts/provision_test_host.sh do not.",
        UserWarning,
        stacklevel=2,
    )
    pytest.skip(f"shellcheck not installed -- {what} could not reach its subject")


@pytest.mark.parametrize("shell_file", SHELL_FILES, ids=lambda path: path.name)
def test_no_shell_file_fails_to_parse_under_shellcheck(shell_file: Path) -> None:
    """KILLS: reverting any ``disable=SC2086 -- prose`` line, with the real tool.

    The tool-free predicate above models shellcheck's directive grammar; this
    one asks shellcheck. Both are needed: the predicate runs on a box with no
    linter, and shellcheck catches the value-level forms the predicate
    deliberately does not guess at (``disable=notacode``).

    ONLY the parse codes are asserted -- never the exit code, never the
    severity. install_linux.sh, capture.sh and scripts/cloud-setup.sh all emit
    ordinary style findings today, so pinning exit 0 would fail them forever,
    which is the over-sensitivity half of the same rule. Measured on this tree:
    all five files report ZERO parse errors, so admitting all five is honest
    rather than aspirational.

    This corrects a claim the old docstring made and the tree has outgrown:
    install_linux.sh and scripts/cloud-setup.sh do NOT currently abort on parse
    errors of their own. They used to; that is why they were excluded.
    """
    _require_shellcheck(f"the parse gate for {shell_file.name}")

    result = subprocess.run(
        ["shellcheck", "--format=gcc", str(shell_file)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    parse_failures = [code for code in _PARSE_FAILURE_CODES if code in result.stdout]

    assert not parse_failures, (
        f"{shell_file.name} does not parse under shellcheck ({parse_failures}). "
        "A parse abort is not one lost finding, it is every finding in the "
        f"file, and any consumer that sources it loses it too:\n{result.stdout}"
    )


def test_fragment_is_shellcheck_parseable() -> None:
    """The gate D5 exists to keep.

    ``--severity=warning`` is pinned deliberately, in BOTH directions. It still
    reports SC1073/SC1072/SC1124 (error) and SC1094 (warning), which are the
    entire D5 signature. Gating on the DEFAULT severity instead would fail the
    correct file forever on SC2317 (info, "command appears to be unreachable")
    for the double-source guard's ``return 0 2>/dev/null || true`` -- a genuine
    false positive, and a gate that fires on identity gets switched off.

    The fragment alone is held to this stricter bar: it is the single source of
    truth, it is the only one of the five that is lint-clean at warning
    severity today, and pinning the other four there would be a promise about
    files this cut does not own.

    An absent shellcheck SKIPS with a reason. A check that cannot reach its
    subject must not report OK.
    """
    _require_shellcheck("the fragment's shellcheck gate")

    strict = subprocess.run(
        ["shellcheck", "--severity=warning", str(FRAGMENT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert strict.returncode == 0, (
        f"shellcheck --severity=warning {FRAGMENT_REL} failed:\n"
        f"{strict.stdout}\n{strict.stderr}"
    )

    everything = subprocess.run(
        ["shellcheck", "--format=gcc", str(FRAGMENT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    parse_failures = [
        code for code in _PARSE_FAILURE_CODES if code in everything.stdout
    ]
    assert not parse_failures, (
        f"{FRAGMENT_REL} does not parse ({parse_failures}); consumers that "
        "source it lose cross-file analysis entirely:\n" + everything.stdout
    )


@pytest.mark.parametrize(
    "consumer",
    (PROVISIONER,),
    ids=lambda path: path.name,
)
def test_consumer_shellcheck_can_follow_the_sourced_fragment(consumer: Path) -> None:
    """SC1094 from a consumer means the fragment did not parse.

    Only the consumer's ability to FOLLOW the source directive is asserted --
    not that the consumer is lint-clean. Pinning its exit code would make this
    gate fire on any unrelated finding anyone ever adds, which is the
    over-sensitivity half of the same rule.

    The provisioner is the subject because it is the one consumer that carries
    a ``# shellcheck source=`` directive that shellcheck can FOLLOW without
    being told where the file is. install_linux.sh and scripts/cloud-setup.sh
    also carry one, and both parse cleanly today (measured -- the old docstring
    here claimed the opposite, and that claim is now stale), but they compute
    the sourced path from a variable, so ``-x`` cannot resolve it and SC1094
    would be absent for the wrong reason. Their parse health is asserted by
    ``test_no_shell_file_fails_to_parse_under_shellcheck`` instead.
    """
    _require_shellcheck(f"the sourced-fragment gate for {consumer.name}")

    result = subprocess.run(
        ["shellcheck", "-x", "--format=gcc", str(consumer)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert "SC1094" not in result.stdout, (
        f"shellcheck cannot parse the fragment {consumer.name} sources, so it "
        "silently drops it and analyses the consumer against a file it never "
        f"read:\n{result.stdout}"
    )


# --- L. the provisioner is documented ----------------------------------------


def test_claude_md_documents_the_provisioner() -> None:
    source = _read(CLAUDE_MD)

    assert "provision_test_host.sh" in source, (
        "CLAUDE.md must name scripts/provision_test_host.sh -- an operator "
        "path nobody documents is one nobody runs"
    )


def test_claude_md_keeps_the_canonical_regen_command() -> None:
    """Guard the literal that tests/test_generated_artifact_workflow.py pins,
    so documenting the provisioner cannot break it."""
    source = _read(CLAUDE_MD)

    assert '.venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"' in source
