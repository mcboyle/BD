"""Contract tests for the shared system-dependency fragment and provisioner.

The change under test gives BD **one** source of truth for system packages
(``scripts/lib/system_deps.sh``) and an operator-facing provisioner
(``scripts/provision_test_host.sh``) that the box actually runs, so the
gui-parity inventory is regenerated outside the Claude-cloud session script.

Method note (CLAUDE.md 0/1). These tests **execute** the fragment rather than
grep it. A grep for ``bd_system_pkgs`` matches a comment; sourcing the file and
calling the function proves the name resolves and returns the right list.

The unknown-is-a-third-state rule drives two design choices here:

* ``_run_fragment`` reserves exit codes 97/98 for "could not source the
  fragment" and "sourced but the function is undefined". Every test asserts the
  result is *not* one of those before interpreting it. Without that guard the
  unknown-group test would pass on a tree with no fragment at all -- a missing
  file also produces a non-zero exit and empty stdout.
* The offset-ordering assertions check ``!= -1`` explicitly, because
  ``str.find`` returns ``-1`` for an absent needle and ``-1 < anything`` would
  certify an ordering that does not exist.

Lane marker: none is declared on purpose. ``tests/conftest.py``
``pytest_collection_modifyitems`` assigns exactly one capture lane marker to
every collected item, and ``capture_lanes.classify_capture_file`` fails closed
to ``serial`` for any file outside ``tests/capture_parallel_files.txt`` -- which
this file is. Verified by calling the classifier directly; no repository test
file declares an explicit ``capture_serial`` pytestmark.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
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

# The contract's package groups, verbatim. The gtk list is the one lifted from
# scripts/cloud-setup.sh's "GTK + Xvfb" step.
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
    ),
}

ALL_PACKAGES = frozenset(
    name for names in EXPECTED_GROUPS.values() for name in names
)

# Package names that can ONLY be a package name in these shell files, so their
# absence is a sound anti-drift signal. Measured against the pre-change tree:
# lowercase `xvfb` occurs once in cloud-setup.sh (the apt line) while the four
# `Xvfb` occurrences are binary references -- so this comparison must stay
# case-sensitive.
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
)

# Deliberately NOT absence-checked: each is also a command or an ordinary word
# in these files (measured pre-change: git 13x and python3.12 4x in
# cloud-setup.sh, npm 32x in install_linux.sh). Asserting their absence would
# cry wolf, so `test_no_consumer_hardcodes_an_apt_package_list` covers them by
# checking apt argument positions instead. The two predicates together contain
# every package name -- that is the denominator check.
AMBIGUOUS_PACKAGES = ("git", "python3.12", "npm")

# Files that must never carry their own copy of the package lists.
CONSUMERS = (CLOUD_SETUP, INSTALL_LINUX, PROVISIONER)

_SOURCE_MISSING = 97
_FUNCTION_UNDEFINED = 98

_APT_INSTALL_RE = re.compile(r"\bapt(?:-get)?\s+install\b|\bapt_i\b")


def _run_fragment(
    snippet: str,
    *,
    function: str = "bd_system_pkgs",
) -> subprocess.CompletedProcess[str]:
    """Source the fragment and run ``snippet``, with unknown states reserved.

    ``cwd`` is pinned to ``REPO_ROOT`` because ``tests/conftest.py`` installs an
    autouse fixture that chdirs into ``tmp_path``; a relative source path would
    otherwise resolve against the wrong tree.
    """
    script = (
        "set -eu\n"
        f". {FRAGMENT_REL} || exit {_SOURCE_MISSING}\n"
        f"declare -F {function} >/dev/null 2>&1 || exit {_FUNCTION_UNDEFINED}\n"
        f"{snippet}\n"
    )
    return subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
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


def _logical_lines(source: str) -> list[str]:
    """Join backslash continuations so a wrapped apt list stays one line."""
    return source.replace("\\\n", " ").splitlines()


def _tokens(line: str) -> set[str]:
    return {token.strip("\"'`,;") for token in line.split()}


# --- A. the fragment exists --------------------------------------------------


def test_system_deps_fragment_exists_and_is_readable() -> None:
    assert FRAGMENT.is_file(), f"{FRAGMENT_REL} is missing"
    assert os.access(FRAGMENT, os.R_OK), f"{FRAGMENT_REL} is not readable"
    assert _read(FRAGMENT).strip(), f"{FRAGMENT_REL} is empty"


# --- B. every group executes and returns the contracted names ----------------


@pytest.mark.parametrize("group", sorted(EXPECTED_GROUPS))
def test_bd_system_pkgs_returns_the_contracted_packages(group: str) -> None:
    packages = _packages(group)

    missing = [name for name in EXPECTED_GROUPS[group] if name not in packages]
    assert not missing, f"bd_system_pkgs {group} omitted {missing}: {packages}"


def test_bd_system_pkgs_all_covers_every_group() -> None:
    """`all` is the denominator: it must contain core + node + gtk."""
    combined = _packages("all")
    union = {
        name
        for group in ("core", "node", "gtk")
        for name in _packages(group)
    }

    assert union, "core/node/gtk produced no packages at all"
    missing = sorted(union - set(combined))
    assert not missing, f"bd_system_pkgs all is missing {missing}"
    assert len(combined) == len(set(combined)), (
        f"bd_system_pkgs all is not deduplicated: {combined}"
    )


def test_bd_system_pkgs_all_orders_core_then_node_then_gtk() -> None:
    combined = _packages("all")
    positions = {name: index for index, name in enumerate(combined)}
    blocks = [
        [positions[name] for name in EXPECTED_GROUPS[group]]
        for group in ("core", "node", "gtk")
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


# --- D. the display helper is defined ----------------------------------------


def test_bd_start_display_is_defined() -> None:
    """Defined-ness only. Starting a real X server is not a test's business."""
    result = _run_fragment(
        "declare -F bd_start_display",
        function="bd_start_display",
    )
    _assert_fragment_was_reached(result, "bd_start_display")

    assert result.returncode == 0, result.stderr


# --- E. the provisioner exists, is executable, and sources the fragment -------


def test_provisioner_exists_and_is_executable() -> None:
    assert PROVISIONER.is_file(), "scripts/provision_test_host.sh is missing"
    assert os.access(PROVISIONER, os.X_OK), (
        "scripts/provision_test_host.sh is not executable"
    )


def test_provisioner_sources_the_shared_fragment() -> None:
    assert PROVISIONER.is_file(), "scripts/provision_test_host.sh is missing"
    source = _read(PROVISIONER)
    directive = re.compile(
        r"^\s*(?:\.|source)\s+\S*system_deps\.sh",
        re.MULTILINE,
    )

    assert directive.search(source), (
        "scripts/provision_test_host.sh must source " + FRAGMENT_REL
    )


def test_cloud_setup_sources_the_shared_fragment() -> None:
    source = _read(CLOUD_SETUP)
    directive = re.compile(
        r"^\s*(?:\.|source)\s+\S*system_deps\.sh",
        re.MULTILINE,
    )

    assert directive.search(source), (
        "scripts/cloud-setup.sh must source " + FRAGMENT_REL
    )


def test_install_linux_sources_the_shared_fragment() -> None:
    source = _read(INSTALL_LINUX)
    directive = re.compile(
        r"^\s*(?:\.|source)\s+\S*system_deps\.sh",
        re.MULTILINE,
    )

    assert directive.search(source), (
        "install_linux.sh must source " + FRAGMENT_REL
    )


# --- F. anti-drift: the package names live in exactly one file ---------------


@pytest.mark.parametrize(
    "consumer",
    CONSUMERS,
    ids=lambda path: path.name,
)
def test_consumers_do_not_restate_package_names(consumer: Path) -> None:
    """The point of the fragment: three scripts cannot disagree about deps."""
    assert consumer.is_file(), f"{consumer.name} is missing"
    source = _read(consumer)

    restated = [name for name in DISCRIMINATING_PACKAGES if name in source]
    assert not restated, (
        f"{consumer.name} restates package names owned by {FRAGMENT_REL}: "
        f"{restated} -- call bd_system_pkgs instead"
    )


def test_fragment_is_the_one_file_that_names_the_packages() -> None:
    """The inverse check: absence everywhere is only meaningful if the
    fragment itself still holds the names."""
    source = _read(FRAGMENT)

    missing = [name for name in DISCRIMINATING_PACKAGES if name not in source]
    assert not missing, (
        f"{FRAGMENT_REL} does not contain {missing} -- the anti-drift check "
        "would then be asserting over a package list nobody owns"
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
    anti-drift denominator would silently exclude a third of the packages.
    """
    assert consumer.is_file(), f"{consumer.name} is missing"

    offenders: list[tuple[str, str]] = []
    for line in _logical_lines(_read(consumer)):
        if not _APT_INSTALL_RE.search(line):
            continue
        for name in sorted(ALL_PACKAGES & _tokens(line)):
            offenders.append((name, line.strip()))

    assert not offenders, (
        f"{consumer.name} passes fragment-owned packages to apt directly: "
        f"{offenders} -- expand bd_system_pkgs into a variable instead"
    )


# --- G/H. the gui-parity inventory is regenerated where it matters -----------


def test_install_linux_regenerates_the_gui_parity_inventory() -> None:
    source = _read(INSTALL_LINUX)

    assert INVENTORY_TOOL in source, (
        "install_linux.sh must run " + INVENTORY_TOOL + ": reports/ is "
        "gitignored and build-time generated, so a stale unzip-overlay copy "
        "survives `git clean -fd` and reads as inventory drift"
    )


def test_capture_regenerates_the_gui_parity_inventory_before_the_suite() -> None:
    """Ordering is the whole point -- a regen after the lanes proves nothing."""
    source = _read(CAPTURE_SH)

    regen_at = source.find(INVENTORY_TOOL)
    lane_at = source.find(PARALLEL_LANE_MARKER)

    assert regen_at != -1, f"capture.sh never references {INVENTORY_TOOL}"
    assert lane_at != -1, (
        f"capture.sh never references {PARALLEL_LANE_MARKER} -- the ordering "
        "assertion below has no anchor to compare against"
    )
    assert regen_at < lane_at, (
        f"capture.sh regenerates the inventory at offset {regen_at}, after the "
        f"parallel lane starts at {lane_at}; the suite would still compare "
        "against the stale copy"
    )


# --- I. every shell file we touch parses ------------------------------------


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


# --- J. the provisioner is documented ---------------------------------------


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
