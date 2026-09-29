"""IA-08 -- the signed audit chain's runtime files are ignored, unrelated names are not.

log.audit_chain_paths() writes audit_chain.key (the raw Ed25519 signer seed),
audit_chain.jsonl and audit_chain.head beside the resolved history DB, which is
the repo root on a default install. None was ignored, so a fresh install showed
them as untracked: `git add -A` would commit the signer key, and `git clean -fd`
would delete the chain.

The names come from log.AUDIT_CHAIN_FILES, the tuple the writer uses, so a new
chain file added there without an ignore rule fails here rather than leaking.

Same method as row 688: a throwaway repository carrying the tree's .gitignore
and nothing else, because a linked worktree shares the parent's info/exclude and
would answer about a fleet-local stopgap instead of the tracked rule.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from bulk_downloader import log

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_GITIGNORE = _REPO / ".gitignore"

# Siblings a too-broad rule (audit_chain*) would swallow: source and notes that
# must stay visible to `git status`.
_NOT_IGNORED = ("audit_chain.py", "audit_chain_notes.md", "audit_chain.keys.md")


def _repo_with_tree_gitignore(tmp_path: Path) -> Path:
    assert _GITIGNORE.is_file(), f"the tree has no .gitignore at {_GITIGNORE}"
    body = _GITIGNORE.read_text()
    assert body.strip(), ".gitignore is empty -- an empty denominator, not a pass"
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(body)
    exclude = tmp_path / ".git" / "info" / "exclude"
    if exclude.exists():
        live = [
            line
            for line in exclude.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert live == [], f"the temp repo's info/exclude is not empty: {live}"
    return tmp_path


def _is_ignored(repo: Path, name: str) -> bool:
    (repo / name).write_text("")
    result = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", "--", name],
        capture_output=True,
    )
    assert result.returncode in (0, 1), (
        f"git check-ignore returned {result.returncode} for {name!r} -- UNKNOWN, "
        f"not an answer: {result.stderr.decode()[:200]}"
    )
    return result.returncode == 0


def test_every_audit_chain_file_is_ignored_and_siblings_are_not(tmp_path):
    names = tuple(log.AUDIT_CHAIN_FILES)
    assert len(names) == 3, f"denominator changed: AUDIT_CHAIN_FILES={names}"
    repo = _repo_with_tree_gitignore(tmp_path)

    missing = [name for name in names if not _is_ignored(repo, name)]
    assert missing == [], (
        f"IA-08: audit chain file(s) not ignored: {missing} of {list(names)}. "
        "audit_chain.key is the raw signer seed; `git add -A` would commit it."
    )

    leaked = [name for name in _NOT_IGNORED if _is_ignored(repo, name)]
    assert leaked == [], f"ignore rule too broad, hides: {leaked}"

    assert not _is_ignored(repo, "ia08_sentinel_not_ignored.txt")
