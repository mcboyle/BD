# RETIRED with bd-retrio.py on 2026-08-31 -- see ../superseded/README.md. Kept
# because it is the RED provenance for the renumber behaviour that was folded
# into bd-rebase-cut.py; it does not run against the live harness any more.
"""bd-retrio.py: resolving the release-trio collision without retyping prose.

The behaviours worth testing are the refusals and the one guarantee that
matters -- that the CHANGELOG entry is carried byte-for-byte from the
pre-rebase commit rather than retyped, because a retyped punctuation-sensitive
header is how an anchor goes quietly wrong.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(os.environ.get("BD_HARNESS_HOME", str(Path.home()))) / "bd-retrio.py"


def git(r, *a, check=True):
    p = subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(p.stderr)
    return p.stdout.strip()


def run(work, pre, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), "--work", str(work), "--pre", pre, *extra],
                          capture_output=True, text=True)


CL = """# Changelog

## v3.66.{v} - {title}

- {body}

"""


@pytest.fixture()
def repo(tmp_path):
    """A repo whose main advanced past a cut, reproducing the real collision."""
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t"); git(r, "config", "user.name", "t")
    (r / "bulk_downloader").mkdir(); (r / "tests").mkdir()

    def write(v, entries):
        (r / "bulk_downloader" / "__init__.py").write_text(f'__version__ = "3.66.{v}"\n')
        (r / "tests" / "test_settings_center_slice4.py").write_text(
            f'def t():\n    assert __version__ == "3.66.{v}", __version__\n')
        (r / "CHANGELOG.md").write_text("# Changelog\n\n" + "".join(entries))

    base_entry = CL.format(v=100, title="base release", body="base line").split("# Changelog\n\n")[1]
    write(100, [base_entry])
    git(r, "add", "-A"); git(r, "commit", "-qm", "base")
    base = git(r, "rev-parse", "HEAD")

    # The cut: version 101, with a distinctive entry containing tricky punctuation.
    git(r, "checkout", "-q", "-b", "cut")
    cut_entry = ("## v3.66.101 - the cut's own entry -- with punctuation\n\n"
                 "- A line with `backticks`, an em-dash-like -- and \"quotes\".\n\n")
    write(101, [cut_entry, base_entry])
    git(r, "add", "-A"); git(r, "commit", "-qm", "the cut")
    pre = git(r, "rev-parse", "HEAD")

    # Main advances to 102 independently.
    git(r, "checkout", "-q", "main")
    other_entry = CL.format(v=102, title="main moved on", body="other work").split("# Changelog\n\n")[1]
    write(102, [other_entry, base_entry])
    git(r, "add", "-A"); git(r, "commit", "-qm", "main advances")
    git(r, "update-ref", "refs/remotes/origin/main", "HEAD")

    git(r, "checkout", "-q", "cut")
    subprocess.run(["git", "-C", str(r), "rebase", "main"], capture_output=True)
    return {"repo": r, "pre": pre, "cut_entry": cut_entry}


def test_it_resolves_and_carries_the_entry_byte_for_byte(repo):
    r = repo["repo"]
    conflicted = git(r, "diff", "--name-only", "--diff-filter=U")
    assert "CHANGELOG.md" in conflicted, f"precondition: the trio must be conflicted, got {conflicted!r}"

    res = run(r, repo["pre"])
    assert res.returncode == 0, res.stdout + res.stderr
    assert "main=3.66.102 -> cut=3.66.103" in res.stdout

    cl = (r / "CHANGELOG.md").read_text()
    # The prose survives verbatim; only the version header changed.
    body = repo["cut_entry"].split("\n", 1)[1]
    assert body in cl, "the cut's prose was not carried byte-for-byte"
    assert "## v3.66.103 - the cut's own entry -- with punctuation" in cl
    assert "## v3.66.101" not in cl
    # And it is anchored on main's head entry, not somewhere else.
    assert cl.index("## v3.66.103") < cl.index("## v3.66.102")
    assert '__version__ = "3.66.103"' in (r / "bulk_downloader" / "__init__.py").read_text()
    assert '"3.66.103"' in (r / "tests" / "test_settings_center_slice4.py").read_text()


def test_it_refuses_when_not_mid_rebase(tmp_path):
    r = tmp_path / "clean"; r.mkdir()
    git(r, "init", "-q", "-b", "main")
    res = run(r, "HEAD")
    assert res.returncode == 2
    assert "not mid-rebase" in res.stderr


def test_it_refuses_a_conflict_outside_the_trio(repo):
    r = repo["repo"]
    # Manufacture an extra unmerged path, so the tool must not proceed.
    (r / "extra.txt").write_text("x")
    git(r, "add", "extra.txt")
    subprocess.run(["git", "-C", str(r), "update-index", "--unresolve", "extra.txt"],
                   capture_output=True)
    conflicted = git(r, "diff", "--name-only", "--diff-filter=U")
    if "extra.txt" not in conflicted:
        pytest.skip("could not manufacture an out-of-trio conflict in this git")
    res = run(r, repo["pre"])
    assert res.returncode == 2
    assert "outside the trio" in res.stderr


def test_it_refuses_a_version_that_already_has_an_entry(repo):
    res = run(repo["repo"], repo["pre"], "--version", "3.66.102")
    assert res.returncode == 2
    assert "already has a CHANGELOG entry" in res.stderr


def test_it_refuses_an_unrecoverable_entry(repo):
    """A --pre whose CHANGELOG has fewer than two headers cannot be sliced."""
    r = repo["repo"]
    thin = git(r, "hash-object", "-w", "--stdin", check=True) if False else None
    res = run(r, "main")  # main's CHANGELOG has two headers, so use a bare tree instead
    # main HAS two headers, so this must succeed structurally; assert the guard
    # by pointing at a commit whose CHANGELOG we know is single-entry.
    base = git(r, "rev-list", "--max-parents=0", "HEAD")
    res2 = run(r, base)
    assert res2.returncode == 2, res2.stdout
    assert "fewer than two release headers" in res2.stderr
