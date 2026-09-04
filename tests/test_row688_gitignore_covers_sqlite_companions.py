"""Row 688 -- the SQLite WAL companions are ignored, and unrelated names are not.

`*.db` does not match `downloader_history.db-shm` or `downloader_history.db-wal`:
those names end in `.db-shm` and `.db-wal`, so the glob stops at the hyphen. The
service opens the history database in WAL mode, so both files appear the moment
it starts, and capture.sh's clean-tree gate then refuses against a fresh install
that has done nothing wrong.

WHY THIS TEST BUILDS ITS OWN REPOSITORY. Every live host carries a fleet-local
stopgap in `.git/info/exclude` naming those two files, and a linked worktree
SHARES that file with its parent, so asking the working checkout whether the
companions are ignored answers a question about the stopgap rather than about
the tracked rule. The temporary repository below carries the tree's `.gitignore`
and NOTHING else, so `git check-ignore` can only be answering about that file.

The negative control is the half that matters. An over-broad rule -- `*.db*`,
say -- would ignore the companions AND swallow `downloader_history.notes`, and a
test that only asserted the companions would pass while the tree quietly stopped
reporting real untracked files. The clean-tree gate is what capture.sh trusts;
widening what it cannot see is the more expensive defect of the two.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_GITIGNORE = _REPO / ".gitignore"

# BOTH databases the tree root carries. The rule is a glob precisely so the
# second one is covered, and a test naming only the first would pass while
# video_hashes' companions leaked -- which is the argument that chose the glob
# over two literal names in the first place, so it is the argument this test
# has to be able to make.
_IGNORED = (
    "downloader_history.db-shm",
    "downloader_history.db-wal",
    "video_hashes.db-shm",
    "video_hashes.db-wal",
)
_NOT_IGNORED = ("downloader_history.notes", "downloader_history.db.README")


def _repo_with_tree_gitignore(tmp_path: Path) -> Path:
    """A throwaway repository whose only ignore source is the tracked file."""
    assert _GITIGNORE.is_file(), f"the tree has no .gitignore at {_GITIGNORE}"
    body = _GITIGNORE.read_text()
    assert body.strip(), ".gitignore is empty -- an empty denominator, not a pass"
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(body)
    exclude = tmp_path / ".git" / "info" / "exclude"
    if exclude.exists():
        # Prove the fresh repo carries no exclude rules of its own; git ships a
        # comment-only file, and anything else would confound the measurement.
        live = [
            line
            for line in exclude.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert live == [], f"the temp repo's info/exclude is not empty: {live}"
    return tmp_path


def _is_ignored(repo: Path, name: str) -> bool:
    (repo / name).write_text("")  # the file must EXIST; check-ignore is about paths
    result = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", "--", name],
        capture_output=True,
    )
    assert result.returncode in (0, 1), (
        f"git check-ignore returned {result.returncode} for {name!r} -- UNKNOWN, "
        f"not an answer: {result.stderr.decode()[:200]}"
    )
    return result.returncode == 0


def test_every_wal_companion_is_ignored_and_unrelated_names_are_not(tmp_path):
    repo = _repo_with_tree_gitignore(tmp_path)

    ignored = [name for name in _IGNORED if _is_ignored(repo, name)]
    assert len(ignored) == len(_IGNORED), (
        "EXACT COUNT: expected every SQLite WAL companion to be ignored, got "
        f"{len(ignored)} of {len(_IGNORED)} -- ignored={ignored}, missing="
        f"{sorted(set(_IGNORED) - set(ignored))}. `*.db` does not match a name "
        "ending in .db-shm or .db-wal."
    )

    # NEGATIVE CONTROL. A rule wide enough to swallow these would blind the
    # clean-tree gate capture.sh depends on, so the rule must NOT have widened.
    leaked = [name for name in _NOT_IGNORED if _is_ignored(repo, name)]
    assert leaked == [], (
        f"the ignore rule is too broad: {leaked} should still be reported as "
        "untracked. Ignoring more than the two companions hides real changes "
        "from capture.sh's clean-tree check."
    )

    # PRECONDITION, asserted rather than assumed: a name nothing could match is
    # still visible, so a green above cannot come from check-ignore answering
    # "ignored" to everything.
    assert not _is_ignored(repo, "row688_sentinel_not_ignored.txt")
