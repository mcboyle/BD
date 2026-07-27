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

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

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
_SHELLCHECK_COMMENT_RE = re.compile(r"^\s*#\s*shellcheck\b(?P<rest>.*)$")
_SHELLCHECK_DIRECTIVE_RE = re.compile(
    r"^\s+(?P<key>[a-z-]+)=(?P<value>\S+)"
)

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


def _strip_shell_comments(source: str) -> str:
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
        index = 0
        escaped = False
        while index < len(raw):
            char = raw[index]
            if escaped:
                kept.append(char)
                escaped = False
                index += 1
                continue
            if char == "\\" and quote != "'":
                kept.append(char)
                escaped = True
                index += 1
                continue
            if quote is None and char in "'\"":
                quote = char
                kept.append(char)
                index += 1
                continue
            if quote is not None and char == quote:
                quote = None
                kept.append(char)
                index += 1
                continue
            if quote is None and char == "#" and (index == 0 or raw[index - 1] in " \t"):
                break
            kept.append(char)
            index += 1

        line = "".join(kept)
        kept_lines.append(line)

        if quote is None:
            match = _HEREDOC_START_RE.search(line)
            if match is not None:
                heredoc_terminator = match.group("word")
                heredoc_strip_tabs = "<<-" in line

    return "\n".join(kept_lines)


def _code(path: Path) -> str:
    return _strip_shell_comments(_read(path))


def _logical_lines(source: str) -> list[str]:
    """Join backslash continuations so a wrapped apt list stays one line."""
    return source.replace("\\\n", " ").splitlines()


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


def test_comment_stripper_reaches_capture_sh_real_anchors() -> None:
    """The stripper is only useful if it changes the answer on the real file.

    capture.sh mentions ``sudo systemctl stop bulkdownloader`` in BOTH a comment
    and the command itself. If this ever stopped being true the ordering test
    below would still pass, but it would have stopped being a test of anything
    -- so the premise is asserted, not assumed.
    """
    raw = _read(CAPTURE_SH)
    code = _code(CAPTURE_SH)

    assert raw.count("systemctl stop bulkdownloader") >= 2, (
        "capture.sh no longer mentions the service stop in a comment as well "
        "as in code; re-derive whether the comment-stripped ordering check "
        "still has a subject"
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

    Scanned over CODE, not raw text. The previous version scanned the whole
    file, so a maintainer writing "installs the GTK typelibs (python3-gi)" in a
    comment failed the build. Over-sensitivity is a soundness bug in its own
    right (CLAUDE.md 0): a gate that cries wolf gets switched off, and a switched
    -off gate sees nothing. A package name in prose cannot be handed to apt.
    """
    assert consumer.is_file(), f"{consumer.name} is missing"
    code = _code(consumer)

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


def test_install_linux_regenerates_the_gui_parity_inventory() -> None:
    code = _code(INSTALL_LINUX)

    assert INVENTORY_TOOL in code, (
        "install_linux.sh must run " + INVENTORY_TOOL + ": reports/ is "
        "gitignored and build-time generated, so a stale unzip-overlay copy "
        "survives `git clean -fd` and reads as inventory drift"
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
    """
    code = _code(CAPTURE_SH)

    regen_lines = [
        index
        for index, line in enumerate(code.splitlines())
        if INVENTORY_TOOL in line
    ]
    assert len(regen_lines) == 1, (
        f"expected exactly one {INVENTORY_TOOL} invocation in capture.sh code, "
        f"found {len(regen_lines)} (lines {[n + 1 for n in regen_lines]}) -- "
        "with more than one, 'the regen' is not a single thing to order"
    )
    regen = regen_lines[0]

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


_FAKE_PYTHON = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with open(os.environ["CAPTURE_ORDER_LOG"], "a", encoding="utf-8") as stream:
    stream.write("python " + json.dumps(args) + "\n")

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
    tmp_path: Path, *, service_stays_active: bool
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fake_home = tmp_path / "BulkDownloader"
    fake_bin = tmp_path / "bin"
    (fake_home / "bulk_downloader").mkdir(parents=True)
    (fake_home / "venv" / "bin").mkdir(parents=True)
    (fake_home / "frontend" / "dist").mkdir(parents=True)
    fake_bin.mkdir()
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
            "CAPTURE_ORDER_LOG": str(order_log),
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


def test_fragment_has_no_prose_comment_shaped_like_a_shellcheck_directive() -> None:
    """The tool-free half of the D5 guard, so an absent linter is not a hole.

    shellcheck parses the first word after ``# `` as a directive KEY and aborts
    the WHOLE FILE when it does not parse (SC1073/SC1072). A comment that merely
    began with the word "shellcheck" as prose therefore left the single source
    of truth with ZERO lint coverage, while consumers reported SC1094 "parsing
    of sourced file failed" and silently dropped it -- or, if they failed to
    parse for their own reasons, reported nothing at all.
    """
    offenders: list[str] = []
    for number, line in enumerate(_read(FRAGMENT).splitlines(), start=1):
        match = _SHELLCHECK_COMMENT_RE.match(line)
        if match is None:
            continue
        directive = _SHELLCHECK_DIRECTIVE_RE.match(match.group("rest"))
        if directive is None or directive.group("key") not in _SHELLCHECK_DIRECTIVE_KEYS:
            offenders.append(f"{FRAGMENT_REL}:{number}: {line.strip()}")

    assert not offenders, (
        "these comments open with the word 'shellcheck' but are not valid "
        f"directives, so shellcheck aborts the file: {offenders}"
    )


def test_fragment_is_shellcheck_parseable() -> None:
    """The gate D5 exists to keep.

    ``--severity=warning`` is pinned deliberately, in BOTH directions. It still
    reports SC1073/SC1072/SC1124 (error) and SC1094 (warning), which are the
    entire D5 signature. Gating on the DEFAULT severity instead would fail the
    correct file forever on SC2317 (info, "command appears to be unreachable")
    for the double-source guard's ``return 0 2>/dev/null || true`` -- a genuine
    false positive, and a gate that fires on identity gets switched off.

    An absent shellcheck SKIPS with a reason. A check that cannot reach its
    subject must not report OK.
    """
    if shutil.which("shellcheck") is None:
        pytest.skip(
            "shellcheck not installed -- this gate could not reach its "
            "subject, so it reports nothing rather than reporting OK. Install "
            "shellcheck (scripts/cloud-setup.sh installs it) and re-run."
        )

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
    a ``# shellcheck source=`` directive; install_linux.sh and
    scripts/cloud-setup.sh currently abort on parse errors of their own, so
    their SC1094 count is zero for the wrong reason -- blindness, not health.
    Adding them here without fixing those first would be a check whose
    denominator excludes its subject.
    """
    if shutil.which("shellcheck") is None:
        pytest.skip(
            "shellcheck not installed -- this gate could not reach its subject"
        )

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
