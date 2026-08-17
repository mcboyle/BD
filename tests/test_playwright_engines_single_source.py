"""The Playwright ENGINE list lives in exactly one file, and it is complete.

WHAT THIS EXISTS TO FIX
-----------------------
Live check L4 (``playwright-browsers-installed``, live_tests/checks.py) asks
``playwright.sync_api`` for chromium/firefox/webkit ``executable_path`` and
stats each one. On the operator's box it reported:

    [WARN] L4 - Chromium installed; 2 other engine(s) missing: firefox, webkit

That WARN was CORRECT and permanently unactionable: no provisioning path this
repo owns ever installed firefox or webkit on the box, so no amount of
re-provisioning could clear it. The check could see its subject; the installer
could not reach it.

WHY A CONTRACT TEST AND NOT JUST A ONE-LINE EDIT
-----------------------------------------------
Measured at the time this file was written, FIVE shell sites install or
dependency-install Playwright browsers, carrying FOUR different engine lists
between them (install_linux.sh, scripts/provision_test_host.sh, and three in
scripts/cloud-setup.sh including the bd-provision heredoc). That is the exact
shape CLAUDE.md 5 forbids for apt packages, one level up: N private copies of a
list is N different answers to "which engines does BD install?", and the copy
nobody updated is the one the box runs.

So the list moves into ``scripts/lib/system_deps.sh`` beside ``bd_system_pkgs``
-- the file that already exists to be the one owner of provisioning lists --
and every consumer asks it.

THE DENOMINATOR IS DERIVED, NOT LISTED (CLAUDE.md 0)
----------------------------------------------------
``test_no_shell_file_hardcodes_a_playwright_engine`` does NOT check a fixed
list of three scripts. It finds every shell file in the tree that invokes
``playwright install`` and asserts over all of them, so a SIXTH install site
added next month is inside the denominator on the day it lands. A gate that
enumerates its own subjects certifies only the subjects somebody remembered.

THE BOUNDARY, DECLARED RATHER THAN LEFT UNEXAMINED (CLAUDE.md 0)
-----------------------------------------------------------------
The scan below covers ``*.sh``. Other files in the tree also run
``playwright install``, and every one of them is deliberately OUTSIDE this
contract for a stated reason -- not because nobody looked:

* ``install_windows.bat`` / ``install_dev.bat`` -- a batch file cannot source a
  bash fragment. They are also a different deployment target: L4's WARN was
  raised by capture.sh on the Linux box, and that is the only host this cut
  changes. Widening to Windows is a separate cut with a separate mechanism.
* ``frontend/package.json`` (``e2e:install``) -- the NODE playwright package,
  which keeps its OWN browser pool under the frontend toolchain. L4 imports
  ``playwright.sync_api`` from the app venv, so that pool is not the pool it
  enumerates; making them agree would be a claim about a browser L4 never
  stats.
* ``toolchain/bin/bd-fetch`` -- an operator convenience that fetches missing
  dev prerequisites on demand, not a provisioning path the box runs.
* ``requirements.txt``, ``downloader_ui.py``, ``bulk_downloader/selftest.py``,
  ``bulk_downloader/healthcheck.py``, ``live_tests/checks.py`` -- prose:
  comments, docstrings and operator hints. None is an invocation.

So the claim this file certifies is exactly its test names: every SHELL
provisioning path takes its engine list from one place. It does not claim the
Windows installer does, because it does not.

METHOD (CLAUDE.md 1)
--------------------
The fragment is EXECUTED, never grepped: sourcing it and calling the function
proves the name resolves and returns the right list, where a grep for
``bd_playwright_engines`` also matches this docstring. Exit codes 97/98 are
reserved for "could not source" and "sourced but undefined" so a tree with no
fragment at all FAILS loudly instead of satisfying an absence assertion.

The shell-file assertions are made over comment-stripped, quote-blanked CODE.
Both directions were considered: install_linux.sh prints an operator hint
(``echo "    sudo $VPYTHON -m playwright install-deps ..."``) that is prose and
must not fire the gate, while a heredoc BODY is kept verbatim because
scripts/cloud-setup.sh emits a real bd-provision script inside one and that
script really does install browsers.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]

FRAGMENT_REL = "scripts/lib/system_deps.sh"
FRAGMENT = REPO_ROOT / FRAGMENT_REL

_SOURCE_MISSING = 97
_FUNCTION_UNDEFINED = 98

_BASH = shutil.which("bash") or "/bin/bash"

# The contract, EXACT (compared with ==, never issubset).
#
# `core` is what BD itself launches: bulk_downloader never asks Playwright for
# firefox or webkit, so a failure to install those must not be graded like a
# failure to install chromium.
#
# `extra` is what L4 audits beyond that. It is not decoration -- L4's PASS
# branch requires all three on disk, so an install that stops at `core` leaves
# a correctly provisioned host WARNing forever.
EXPECTED_ENGINES: dict[str, tuple[str, ...]] = {
    "core": ("chromium",),
    "extra": ("firefox", "webkit"),
    "all": ("chromium", "firefox", "webkit"),
}

# The engine names Playwright itself accepts. Used as the anti-drift alphabet:
# any of these appearing as a BARE argument to `playwright install` in a
# consumer is a hardcoded copy of the fragment's list.
PLAYWRIGHT_ENGINE_NAMES = (
    "chromium",
    "chromium-headless-shell",
    "firefox",
    "webkit",
)

# The fragment function every consumer must go through.
ENGINE_FUNCTION = "bd_playwright_engines"

# `playwright install` / `playwright install-deps`, however it is spelled:
# `$VPYTHON -m playwright install`, `./venv/bin/python -m playwright install`,
# `"$PY" -m playwright install-deps`.
_PW_INSTALL_RE = re.compile(r"playwright\s+install(?:-deps)?\b")


# --- helpers: running the fragment -------------------------------------------


def _run_fragment(snippet: str, *, function: str = ENGINE_FUNCTION):
    """Source the fragment and run ``snippet``, with unknown states reserved.

    ``cwd`` is pinned to REPO_ROOT because tests/conftest.py chdirs into
    tmp_path; a relative source path would resolve against the wrong tree.
    """
    script = (
        "set -eu\n"
        f". {FRAGMENT_REL} || exit {_SOURCE_MISSING}\n"
        f"declare -F {function} >/dev/null 2>&1 || exit {_FUNCTION_UNDEFINED}\n"
        f"{snippet}\n"
    )
    return subprocess.run(
        [_BASH, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=120,
    )


def _assert_reached(result, function: str = ENGINE_FUNCTION) -> None:
    assert result.returncode != _SOURCE_MISSING, (
        f"UNKNOWN: {FRAGMENT_REL} could not be sourced -- this check never "
        f"reached its subject. stderr: {result.stderr}"
    )
    assert result.returncode != _FUNCTION_UNDEFINED, (
        f"UNKNOWN: {FRAGMENT_REL} sourced but {function}() is undefined -- "
        f"sourcing cleanly proves nothing about what it defined."
    )


def _engines(group: str) -> list[str]:
    result = _run_fragment(f"{ENGINE_FUNCTION} {group}")
    _assert_reached(result)
    assert result.returncode == 0, (
        f"{ENGINE_FUNCTION} {group} exited {result.returncode}: {result.stderr}"
    )
    return result.stdout.split()


# --- helpers: comment-stripped, quote-blanked shell source --------------------


_HEREDOC_START_RE = re.compile(
    r"<<-?\s*(?P<quote>['\"]?)(?P<word>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)"
)


def _strip_shell_comments(source: str, *, blank_quoted: bool = False) -> str:
    """Blank shell comments (and optionally quoted CONTENTS), keeping columns.

    Heredoc bodies are kept in both views on purpose: scripts/cloud-setup.sh
    writes the bd-provision script inside ``<<'PROV'``, and that body really
    does run ``playwright install``. Blanking it would put a real install site
    outside the denominator.

    ONE narrow exception, and it was measured rather than assumed: a FULL-LINE
    ``#`` comment inside a heredoc body is blanked. The generated script is
    shell, so its comments are comments -- and leaving them in made this gate
    fire on the sentence "``playwright install`` with no engine argument
    installs every default browser", a comment written to explain the very
    fix it then failed. A gate that cries wolf gets switched off, so
    over-sensitivity is a soundness bug in its own right (CLAUDE.md 0).

    The exception is deliberately FULL-LINE only and carries no quote state
    across heredoc lines. That is what keeps it from re-opening the desync
    that motivated verbatim bodies in the first place: ``<<'USAGE'`` bodies
    contain apostrophes, and tracking quotes through them puts the rest of
    the file in the wrong state. A trailing ``#`` on a real command inside a
    heredoc is still read as code -- conservative in the safe direction.
    """
    kept_lines: list[str] = []
    heredoc_terminator: str | None = None
    heredoc_strip_tabs = False
    quote: str | None = None

    for raw in source.splitlines():
        if heredoc_terminator is not None:
            candidate = raw.lstrip("\t") if heredoc_strip_tabs else raw
            if candidate.rstrip() == heredoc_terminator:
                kept_lines.append(raw)
                heredoc_terminator = None
                continue
            kept_lines.append("" if raw.lstrip().startswith("#") else raw)
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


def _logical_pairs(code: str, unquoted: str) -> list[tuple[int, str, str]]:
    """(1-based start line, joined code, joined unquoted) per logical line.

    The continuation structure is derived from the CODE view only and applied
    to both; deriving it twice skews the views the moment a ``\\`` sits inside
    a quoted string.
    """
    code_lines = code.splitlines()
    unquoted_lines = unquoted.splitlines()
    assert len(code_lines) == len(unquoted_lines), (
        "UNKNOWN: the code and quote-blanked views disagree on line count -- "
        "the instrument is broken, so nothing measured with it means anything"
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


def _shell_files() -> list[Path]:
    """Every TRACKED .sh, from git rather than from a tree walk.

    rglob was wrong here, and CLAUDE.md section 7 says why: tree-walking checks
    must use `git ls-files`. An rglob denominator includes anything sitting in
    the working directory -- and isolated subagents create full git worktrees
    under .claude/worktrees/, each a complete copy of this repo. Those copies
    carry the UNFIXED scripts, so this gate failed naming
    `.claude/worktrees/agent-*/install_linux.sh` as an offender: files that are
    gitignored, not part of the repository, and not the subject of any
    assertion here.

    The blocklist approach cannot fix that -- it would need a new exclusion for
    every future directory anyone happens to create. Asking git for the tracked
    set makes the denominator exactly the repository, by construction.

    (The inverse mistake is equally available: a filter EXCLUDING any path
    containing ".claude" would drop the whole tree when the checkout itself
    lives under such a path. Both are the same error -- a denominator defined by
    where files sit rather than by what they are.)
    """
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.sh"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    found = [REPO_ROOT / rel for rel in out.split("\0") if rel]
    assert found, (
        "git ls-files returned no .sh files -- the denominator is empty, so "
        "every assertion below would pass over nothing. Refusing to report a "
        "clean scan (CLAUDE.md 0: a zero-in-every-bucket summary is a failure "
        "signal, not a pass)."
    )
    return sorted(p for p in found if "node_modules" not in p.parts)


def _install_sites(path: Path) -> list[tuple[int, str, str]]:
    """Logical lines in ``path`` that INVOKE `playwright install[-deps]`.

    Returns (line number, code, quote-blanked code). The quote-blanked view is
    what the argument assertions read: install_linux.sh prints a copy-pasteable
    operator hint inside an ``echo``, and a package/engine name inside a quoted
    string cannot be word-split into an argv element, so it is prose by
    construction -- exactly like a comment.
    """
    source = path.read_text(encoding="utf-8", errors="replace")
    code = _strip_shell_comments(source)
    unquoted = _strip_shell_comments(source, blank_quoted=True)
    return [
        (number, code_line, unquoted_line)
        for number, code_line, unquoted_line in _logical_pairs(code, unquoted)
        if _PW_INSTALL_RE.search(unquoted_line)
    ]


# --- A0. the instrument, before anything is measured with it ------------------


def test_install_site_scanner_self_check(tmp_path: Path) -> None:
    """``_install_sites`` is the denominator for three absence assertions.

    An instrument that quietly stopped seeing install lines would make all
    three pass over nothing -- the exact failure this file exists to prevent --
    so it is exercised on inputs whose answer is known by construction, in
    BOTH directions.
    """
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/bash\n"
        "# a comment naming playwright install chromium\n"
        'echo "run: python -m playwright install chromium"\n'
        './venv/bin/python -m playwright install $_pw_core\n'
        "cat > out <<'GEN'\n"
        "# a comment inside the heredoc naming playwright install webkit\n"
        "./venv/bin/python -m playwright install-deps firefox\n"
        "GEN\n",
        encoding="utf-8",
    )
    hits = {number: unquoted for number, _code, unquoted in _install_sites(probe)}

    assert 2 not in hits, (
        "a FULL-LINE comment was read as an install site -- the gate would "
        "fire on prose"
    )
    assert 3 not in hits, (
        "a quoted operator hint was read as an install site; install_linux.sh "
        "prints exactly this shape and must not be an offender"
    )
    assert 4 in hits, (
        "a real `playwright install` invocation was NOT seen -- every absence "
        f"assertion below would be vacuous. saw: {sorted(hits)}"
    )
    assert 6 not in hits, (
        "a comment INSIDE a heredoc body was read as an install site"
    )
    assert 7 in hits, (
        "a real install line inside a heredoc body was NOT seen. "
        "scripts/cloud-setup.sh emits the bd-provision installer inside "
        f"<<'PROV'; missing it would exclude a real install site. saw: {sorted(hits)}"
    )
    assert "chromium" not in hits[4], (
        "quote-blanking did not run on the code being scanned"
    )
    assert "firefox" in hits[7], (
        "heredoc bodies must keep their bare arguments -- otherwise the "
        "hardcoded-engine scan cannot see one there"
    )


# --- A. the fragment owns the list, and it executes ---------------------------


@pytest.mark.parametrize("group", sorted(EXPECTED_ENGINES))
def test_bd_playwright_engines_returns_exactly_the_contracted_engines(
    group: str,
) -> None:
    """EXACT set, not superset.

    A superset assertion cannot see an ADDED name, and `playwright install
    <unknown>` fails the whole command -- so an extra name costs the operator
    chromium too.
    """
    engines = _engines(group)
    expected = set(EXPECTED_ENGINES[group])
    actual = set(engines)

    assert actual == expected, (
        f"{ENGINE_FUNCTION} {group} returned {sorted(actual)}; the contract is "
        f"{sorted(expected)}. Unexpected: {sorted(actual - expected)}; "
        f"missing: {sorted(expected - actual)}"
    )
    assert len(engines) == len(actual), (
        f"{ENGINE_FUNCTION} {group} repeats an engine: {engines}"
    )


def test_all_is_exactly_core_plus_extra() -> None:
    """`all` is the denominator L4 enumerates; it must not drift from its parts."""
    combined = _engines("all")
    union = set(_engines("core")) | set(_engines("extra"))

    assert union, "core/extra produced no engines at all"
    assert set(combined) == union, (
        f"{ENGINE_FUNCTION} all returned {sorted(set(combined))}; core+extra is "
        f"{sorted(union)}"
    )
    assert len(combined) == len(set(combined)), (
        f"{ENGINE_FUNCTION} all is not deduplicated: {combined}"
    )


def test_the_engines_l4_audits_are_all_installable() -> None:
    """The subject, named. This is the WARN the cut exists to clear.

    live_tests/checks.py's L4 iterates the literal tuple
    ``("chromium", "firefox", "webkit")`` and returns WARN when chromium is
    present but any other engine is missing. So the installable set has to
    contain all three, or the check is asking about engines nothing installs.
    """
    installable = set(_engines("all"))
    l4_engines = {"chromium", "firefox", "webkit"}
    missing = sorted(l4_engines - installable)

    assert not missing, (
        f"L4 (playwright-browsers-installed) enumerates {sorted(l4_engines)} "
        f"but BD's provisioning installs {sorted(installable)}; {missing} can "
        f"never be present, so L4 WARNs forever on a correctly provisioned host"
    )


def test_l4_and_the_fragment_agree_on_the_engine_set() -> None:
    """Read L4's OWN tuple out of checks.py rather than restating it here.

    Restating it would be a third copy: the fragment, L4, and this test could
    then all be internally consistent while two of them disagreed with the
    third. The tuple is parsed from the source so this assertion moves when
    the check moves.
    """
    checks = (REPO_ROOT / "live_tests" / "checks.py").read_text(
        encoding="utf-8", errors="replace"
    )
    match = re.search(
        r"def l4_playwright_browsers_installed.*?engines\s*=\s*\(([^)]*)\)",
        checks,
        re.S,
    )
    assert match is not None, (
        "UNKNOWN: could not locate L4's `engines = (...)` tuple in "
        "live_tests/checks.py -- this assertion has no subject, so it cannot "
        "certify agreement with anything"
    )
    l4_engines = set(re.findall(r"['\"]([a-z_-]+)['\"]", match.group(1)))
    assert l4_engines, "L4's engine tuple parsed as empty"

    installable = set(_engines("all"))
    assert l4_engines <= installable, (
        f"L4 audits {sorted(l4_engines)}; the fragment installs "
        f"{sorted(installable)}. Unreachable: {sorted(l4_engines - installable)}"
    )


# --- B. an unknown group fails loudly, empty-handed ---------------------------


@pytest.mark.parametrize(
    ("snippet", "label"),
    [
        pytest.param(f"{ENGINE_FUNCTION} bogus", "bogus", id="unknown-group"),
        pytest.param(f'{ENGINE_FUNCTION} ""', "empty", id="empty-group"),
        pytest.param(ENGINE_FUNCTION, "missing", id="no-argument"),
    ],
)
def test_bd_playwright_engines_rejects_unknown_groups(snippet: str, label: str) -> None:
    """An empty list must never be echoed silently.

    ``playwright install`` with ZERO engine arguments installs every default
    browser rather than failing, so a silently-empty list does not even fail
    loudly at the consumer -- it quietly changes what gets installed.
    """
    result = _run_fragment(snippet)
    _assert_reached(result)

    assert result.returncode != 0, (
        f"{ENGINE_FUNCTION} ({label}) exited 0 -- an unknown group that "
        f"reports success is an empty denominator reading as OK"
    )
    assert result.stdout.strip() == "", (
        f"{ENGINE_FUNCTION} ({label}) wrote to stdout: {result.stdout!r}"
    )
    assert result.stderr.strip(), (
        f"{ENGINE_FUNCTION} ({label}) failed silently -- failing loudly means "
        f"saying why, on stderr"
    )


# --- C. anti-drift: no consumer restates the list ----------------------------


def test_the_fragment_is_the_one_file_that_names_the_engines() -> None:
    """The inverse check: absence elsewhere means nothing if the owner is empty."""
    code = _strip_shell_comments(FRAGMENT.read_text(encoding="utf-8"))
    missing = [name for name in ("chromium", "firefox", "webkit")
               if name not in code]
    assert not missing, (
        f"{FRAGMENT_REL} does not name {missing} in CODE -- the anti-drift "
        f"check below would then be asserting over a list nobody owns"
    )


def test_at_least_one_shell_file_installs_playwright_browsers() -> None:
    """Anti-vacuity for the scan below.

    ``test_no_shell_file_hardcodes_a_playwright_engine`` is an absence
    assertion over a derived denominator. If the derivation ever returns zero
    files -- a renamed script, a broken stripper -- it would pass over nothing
    at all, which is the failure mode this whole file is about.
    """
    sites = {
        path.relative_to(REPO_ROOT).as_posix(): _install_sites(path)
        for path in _shell_files()
    }
    populated = {rel: hits for rel, hits in sites.items() if hits}
    assert populated, (
        "no shell file in the tree invokes `playwright install` -- the "
        "anti-drift scan has an empty denominator and certifies nothing"
    )


def test_no_shell_file_hardcodes_a_playwright_engine() -> None:
    """Every install site takes its engines from the fragment.

    DERIVED denominator: every .sh in the tree that invokes `playwright
    install` or `playwright install-deps`, not a hand-kept list of three
    scripts. A fourth installer added later is inside the subject on day one.

    Over-sensitivity was considered in both directions. An engine name inside
    a quoted string (install_linux.sh's copy-pasteable operator hint) is prose
    and is blanked before the scan; an engine name in a HEREDOC body is NOT,
    because scripts/cloud-setup.sh emits a real installer inside one.
    """
    offenders: list[str] = []
    for path in _shell_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for number, _code_line, unquoted_line in _install_sites(path):
            tokens = {
                token.strip("\"'`,;()")
                for token in unquoted_line.split()
            }
            hardcoded = sorted(tokens & set(PLAYWRIGHT_ENGINE_NAMES))
            if hardcoded:
                offenders.append(
                    f"{rel}:{number} passes {hardcoded} literally: "
                    f"{unquoted_line.strip()[:140]}"
                )
    assert not offenders, (
        "these `playwright install` sites carry their own engine list instead "
        f"of calling {ENGINE_FUNCTION}:\n  " + "\n  ".join(offenders)
    )


def test_every_install_site_resolves_the_list_through_the_fragment() -> None:
    """The positive half: absence of a literal is not presence of the call.

    A site could satisfy the scan above by installing NOTHING. Each install
    site must reference the fragment function -- directly, or through a
    variable the same file assigned from it.
    """
    unsourced: list[str] = []
    for path in _shell_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        sites = _install_sites(path)
        if not sites:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        code = _strip_shell_comments(source)
        # Variables this file assigns from the fragment function.
        derived = set(
            re.findall(
                r"^\s*(?:local\s+|export\s+)?([A-Za-z_][A-Za-z0-9_]*)="
                r"[^\n]*" + re.escape(ENGINE_FUNCTION),
                code,
                re.M,
            )
        )
        for number, code_line, _unquoted in sites:
            referenced = set(
                re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", code_line)
            )
            if ENGINE_FUNCTION in code_line:
                continue
            if referenced & derived:
                continue
            unsourced.append(
                f"{rel}:{number} installs browsers without reaching "
                f"{ENGINE_FUNCTION}: {code_line.strip()[:140]}"
            )
    assert not unsourced, (
        "these install sites neither call the fragment nor use a variable "
        f"assigned from it:\n  " + "\n  ".join(unsourced)
    )


def test_install_deps_covers_every_engine_not_just_chromium() -> None:
    """firefox and webkit need OS libraries chromium does not.

    Measured from Playwright 1.61's own nativeDeps table
    (``playwright/driver/package/lib/coreBundle.js``, key
    ``ubuntu24.04-x64``): chromium needs 21 apt packages, firefox 25, webkit 53
    -- the webkit set pulls the whole gstreamer stack plus libgtk-4-1,
    libenchant, libhyphen and libwoff1, almost none of which chromium's set
    contains. ``playwright install-deps chromium`` therefore leaves a
    downloaded webkit that cannot launch.

    Those 78 names deliberately do NOT move into scripts/lib/system_deps.sh.
    Playwright owns that table and versions it with itself; a copy in this repo
    would be a second list that drifts on every playwright bump, which is the
    defect this file exists to prevent, not an instance of the fix.
    """
    deps_sites: list[tuple[str, int, str]] = []
    for path in _shell_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for number, _code_line, unquoted_line in _install_sites(path):
            if re.search(r"playwright\s+install-deps\b", unquoted_line):
                deps_sites.append((rel, number, unquoted_line))

    assert deps_sites, (
        "no shell file runs `playwright install-deps` -- on a headless Ubuntu "
        "host every engine downloads fine and none of them launches, and this "
        "assertion has no subject"
    )

    narrow = [
        f"{rel}:{number}: {line.strip()[:140]}"
        for rel, number, line in deps_sites
        if "chromium" in line.split()
    ]
    assert not narrow, (
        "these `playwright install-deps` sites name chromium only, so firefox "
        "and webkit are downloaded without their OS libraries:\n  "
        + "\n  ".join(narrow)
    )


# --- D. the shell still parses ------------------------------------------------


def test_fragment_parses_under_bash() -> None:
    proc = subprocess.run(
        [_BASH, "-n", str(FRAGMENT)], capture_output=True, text=True
    )
    assert proc.returncode == 0, f"{FRAGMENT_REL} does not parse:\n{proc.stderr}"


BD_GATE_SCOPE = "repo-wide"
