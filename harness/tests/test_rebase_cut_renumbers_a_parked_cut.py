"""A PARKED cut carries a version main has already passed.

bd-rebase-cut.py's resolve_trio said the cut's number is "the higher number by
construction: the cut was numbered after the base it was frozen against, and
main has only moved forward since." True of a cut frozen from the tip. FALSE of
a parked one -- and BD had a parked one: the v3.66.1374 candidate for rows 413,
414, 417 and 418 sat tagged and unmerged while main reached v3.66.1377.

Taking the cut side unexamined there walks the version BACKWARDS, and every
assertion inside the tool still passes, because each one only checks that the
side it kept is self-consistent. That is the wrong-but-green shape the tool was
written to prevent, reappearing in the tool itself.

These tests pin the two halves: an undeclared backwards renumber is REFUSED,
and a declared one rewrites the version token while keeping every other byte of
the cut's own CHANGELOG entry -- because the entry is recovered from the commit
precisely so that a punctuation-sensitive anchor is never retyped (A7).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HOME = Path(os.environ.get("BD_HARNESS_HOME", str(Path.home())))
SCRIPT = HOME / "bd-rebase-cut.py"

PREAMBLE = "# Changelog\n\nASCII-only.\n\n"
# Deliberately punctuation-heavy: this is the text that must survive byte for
# byte. Retyping a line like this is how an anchor goes quietly wrong.
CUT_PROSE = (
    "\n- fix: the probe's `--only` path (row 413/417) -- 2 of 6 tiers, not 6.\n"
    "  MEASURED: 5,102,802,950 bytes; see \"related videos\" [sic] grid.\n"
)


def git(r, *a, check=True):
    p = subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(a)}: {p.stderr}")
    return p.stdout.strip()


def _trio(r, version, entry_prose):
    (r / "bulk_downloader").mkdir(exist_ok=True)
    (r / "bulk_downloader" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (r / "tests").mkdir(exist_ok=True)
    (r / "tests" / "test_settings_center_slice4.py").write_text(
        f'assert __version__ == "{version}"\n')
    prior = (r / "CHANGELOG.md").read_text()
    body = prior[len(PREAMBLE):] if prior.startswith(PREAMBLE) else ""
    (r / "CHANGELOG.md").write_text(
        f"{PREAMBLE}## v{version} - test entry\n{entry_prose}\n{body}")


@pytest.fixture()
def repo(tmp_path):
    """base -> main advances to 1003; a cut PARKED at 1001 branches off base."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t"); git(r, "config", "user.name", "t")
    (r / "CHANGELOG.md").write_text(PREAMBLE)
    (r / "PIN_INDEX.json").write_text('{"version": "3.66.1000"}\n')
    _trio(r, "3.66.1000", "\n- base\n")
    git(r, "add", "-A"); git(r, "commit", "-qm", "base")
    base = git(r, "rev-parse", "HEAD")

    git(r, "checkout", "-q", "-b", "cut", base)
    _trio(r, "3.66.1001", CUT_PROSE)
    git(r, "add", "-A"); git(r, "commit", "-qm", "the parked cut")

    git(r, "checkout", "-q", "main")
    _trio(r, "3.66.1003", "\n- main moved on\n")
    git(r, "add", "-A"); git(r, "commit", "-qm", "main advances past the cut")

    # The tool regenerates artifacts through the repo interpreter after its
    # last source edit. A stub keeps that step real (it must be CALLED) without
    # dragging the whole toolchain into a unit test.
    vb = r / "venv" / "bin"
    vb.mkdir(parents=True)
    (vb / "python").write_text(
        "#!/bin/sh\n"
        "d=$(cd \"$(dirname \"$0\")/../..\" && pwd)\n"
        "echo \"stub regen: $*\" >> \"$d/regen.log\"\n"
        "v=$(sed -n 's/^__version__ = \"\\(.*\\)\"$/\\1/p' \"$d/bulk_downloader/__init__.py\")\n"
        "printf '{\"version\": \"%s\"}\\n' \"$v\" > \"$d/PIN_INDEX.json\"\n")
    (vb / "python").chmod(0o755)

    git(r, "remote", "add", "origin", str(origin))
    git(r, "push", "-q", "origin", "main")
    git(r, "fetch", "-q", "origin")
    git(r, "checkout", "-q", "cut")
    return r


def run(work, version, *extra, script=SCRIPT):
    return subprocess.run(
        [sys.executable, str(script), "--work", str(work), "--version", version, *extra],
        capture_output=True, text=True)


def test_an_undeclared_backwards_renumber_is_refused(repo):
    """The precondition that makes the rest safe, asserted directly."""
    before = git(repo, "rev-parse", "HEAD")
    r = run(repo, "3.66.1004")
    assert r.returncode != 0, r.stdout
    assert "CARRIES v3.66.1001" in (r.stdout + r.stderr), r.stdout + r.stderr
    assert "--renumber" in (r.stdout + r.stderr)
    assert git(repo, "rev-parse", "HEAD") == before, "a refusal must change nothing"


def test_a_declared_renumber_rewrites_the_trio_and_keeps_the_entry_verbatim(repo):
    r = run(repo, "3.66.1004", "--renumber")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "renumber 3.66.1001 -> 3.66.1004" in r.stdout, r.stdout

    assert (repo / "bulk_downloader" / "__init__.py").read_text() == \
        '__version__ = "3.66.1004"\n'
    assert (repo / "tests" / "test_settings_center_slice4.py").read_text() == \
        'assert __version__ == "3.66.1004"\n'

    log = (repo / "CHANGELOG.md").read_text()
    # the cut's entry is on top, renumbered...
    assert log.startswith(PREAMBLE + "## v3.66.1004 - test entry\n"), log[:200]
    # ...its prose survived byte for byte, punctuation included...
    assert CUT_PROSE in log, "the cut's own entry was not preserved verbatim"
    # ...and it is anchored on the release main actually ends with.
    assert log.index("## v3.66.1004") < log.index("## v3.66.1003") < log.index("## v3.66.1000")
    assert "3.66.1001" not in log, "the parked number must not survive anywhere"

    # NEGATIVE CONTROL ON THE REBASE ITSELF: the cut is applied, on a branch,
    # with main as its parent -- not merely 'the tool exited 0'.
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "cut"
    assert git(repo, "rev-parse", "HEAD~1") == git(repo, "rev-parse", "origin/main")
    for marker in ("rebase-merge", "rebase-apply"):
        assert not (repo / ".git" / marker).exists()

    # and the regeneration step actually ran, after the edits, not instead of them
    assert (repo / "regen.log").exists(), "bd-regen-order was never invoked"
    assert "bd-regen-order" in (repo / "regen.log").read_text()
    # and the generated artifact was rebuilt from the RENUMBERED source
    assert "3.66.1004" in (repo / "PIN_INDEX.json").read_text()


def test_the_same_run_against_the_original_tool_fails(repo):
    """RED PROVENANCE, replayed rather than asserted.

    The defective parent is kept beside the tool. If this ever passes, the
    renumber support was not what made the test above go green.
    """
    original = HOME / "bd-persist" / "harness" / "bd-rebase-cut.py.ORIGINAL-pre-renumber"
    if not original.exists():
        pytest.skip("pre-renumber original not retained")
    r = run(repo, "3.66.1004", script=original)
    assert r.returncode != 0, "the original tool must NOT be able to do this"
    assert "does not carry" in (r.stdout + r.stderr) or \
           "not v3.66.1004" in (r.stdout + r.stderr), r.stdout + r.stderr


def test_a_trio_that_auto_merges_is_still_renumbered(tmp_path):
    """THE CASE THAT ESCAPED THE FIRST FIX.

    resolve_trio only runs on a path git REPORTS as conflicted. When a parked
    cut carries the number main has just taken, both sides are IDENTICAL, git
    auto-merges, and the renumber never fires. v3.66.1379's real rebase came
    out with a 1379 CHANGELOG and a 1378 __init__.py, and the only thing that
    noticed was PIN_INDEX regenerating from the wrong number.

    A renumber covering part of the trio is worse than none: the trio exists so
    that the three agree, and here they did not.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t"); git(r, "config", "user.name", "t")
    (r / "CHANGELOG.md").write_text(PREAMBLE)
    (r / "PIN_INDEX.json").write_text('{"version": "3.66.1000"}\n')
    _trio(r, "3.66.1000", "\n- base\n")
    git(r, "add", "-A"); git(r, "commit", "-qm", "base")
    base = git(r, "rev-parse", "HEAD")

    # The cut is frozen at 1001 ...
    git(r, "checkout", "-q", "-b", "cut", base)
    _trio(r, "3.66.1001", CUT_PROSE)
    git(r, "add", "-A"); git(r, "commit", "-qm", "the parked cut")

    # ... and main then SHIPS 1001 itself, so the version file and the pin are
    # byte-identical on both sides and git will auto-merge them silently.
    git(r, "checkout", "-q", "main")
    _trio(r, "3.66.1001", "\n- main took the same number\n")
    git(r, "add", "-A"); git(r, "commit", "-qm", "main takes 1001")

    vb = r / "venv" / "bin"; vb.mkdir(parents=True)
    (vb / "python").write_text(
        "#!/bin/sh\n"
        "d=$(cd \"$(dirname \"$0\")/../..\" && pwd)\n"
        "v=$(sed -n 's/^__version__ = \"\\(.*\\)\"$/\\1/p' \"$d/bulk_downloader/__init__.py\")\n"
        "printf '{\"version\": \"%s\"}\\n' \"$v\" > \"$d/PIN_INDEX.json\"\n")
    (vb / "python").chmod(0o755)
    git(r, "remote", "add", "origin", str(origin))
    git(r, "push", "-q", "origin", "main")
    git(r, "fetch", "-q", "origin")
    git(r, "checkout", "-q", "cut")

    # PRECONDITION: the two sides really are identical, so git cannot conflict
    # on them -- without this the test would pass for the wrong reason.
    assert git(r, "show", "main:bulk_downloader/__init__.py") == \
           git(r, "show", "cut:bulk_downloader/__init__.py")

    res = run(r, "3.66.1002", "--renumber")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "auto-merged, not conflicted" in res.stdout, res.stdout

    # ALL THREE agree, which is the contract the trio exists to hold
    assert (r / "bulk_downloader" / "__init__.py").read_text() == \
        '__version__ = "3.66.1002"\n'
    assert (r / "tests" / "test_settings_center_slice4.py").read_text() == \
        'assert __version__ == "3.66.1002"\n'
    log = (r / "CHANGELOG.md").read_text()
    assert log.startswith(PREAMBLE + "## v3.66.1002 - test entry\n"), log[:160]
    assert CUT_PROSE in log
    assert "3.66.1002" in (r / "PIN_INDEX.json").read_text()
    assert '__version__ = "3.66.1001"' not in (r / "bulk_downloader" / "__init__.py").read_text()
