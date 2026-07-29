"""A .gitignore line with an inline comment silently ignores nothing.

THE DEFECT, and it is the highest-consequence one in this family.

    .gitignore:28
    vapid_keys.json          # generated web-push PRIVATE key -- must never be committed

`.gitignore` has NO inline comments. `#` starts a comment only at the beginning
of a line. So that pattern is the whole string -- filename, run of spaces, and
comment prose -- and it has never matched any path. Measured on the deploy host:

    $ git check-ignore -v vapid_keys.json
    (no output, exit 1)
    $ git status --porcelain
    ?? vapid_keys.json

The web-push PRIVATE key is untracked-and-unignored, which is the state where a
single `git add -A` commits it. Nothing has been committed, but the line
asserting it "must never be committed" is precisely why nobody checked -- a
comment claiming a protection the mechanism does not provide.

WHY A DERIVED GATE AND NOT A ONE-LINE FIX. This class is invisible by
construction: the file still parses, git reports no error, and the line reads
correctly to a human. The only way to know whether there are others is to ask
the question over every tracked .gitignore. Today the answer is one; the gate
keeps it one.

THE SECOND DEFECT, in a gate I shipped two hours before writing this.
tests/test_generated_artifacts_are_not_tracked.py asked
`git check-ignore -q community_scrapers_cache` -- no trailing slash -- against
the rule `/community_scrapers_cache/`, which is directory-only. Whether that
matches depends on whether the directory EXISTS, because git cannot classify a
path it cannot stat. In the sandbox it existed and the gate passed; on the box
`git reset --hard` had deleted it (untracking a file makes the next deploy
remove it) and the same gate failed. A check whose answer depends on the
filesystem rather than on the rule is not certifying the rule.

That is the same trailing-slash defect this session already diagnosed in
tests/test_git_deploy_gaps_are_documented.py, shipped again an hour later. The
fix is to query a path INSIDE the directory, which resolves against the rule
regardless of what exists -- and `test_a_directory_rule_matches_by_path_not_by_existence`
below locks the mechanism in a throwaway repository so the reasoning cannot rot.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd or ROOT), *args],
                          capture_output=True, text=True)


def _gitignore_files() -> list[str]:
    """Every tracked .gitignore, by `git ls-files` -- never a tree walk.

    Ephemeral agent worktrees live under the repository root; rglob descends
    into them and returns other agents' copies of these files.
    """
    out = _git("ls-files").stdout.splitlines()
    return [p for p in out if p == ".gitignore" or p.endswith("/.gitignore")]


def test_the_scan_finds_the_gitignore_files():
    """A zero-length denominator would make every assertion below vacuous."""
    found = _gitignore_files()
    assert found, "no tracked .gitignore found -- the scan cannot see its subject"


# ── the defect ───────────────────────────────────────────────────────────────

def test_no_pattern_carries_an_inline_comment():
    """`#` only opens a comment at the start of a line."""
    offenders = []
    for rel in _gitignore_files():
        for lineno, raw in enumerate(
                (ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "#" in stripped:
                offenders.append(f"{rel}:{lineno}: {raw!r}")
    assert not offenders, (
        "these lines carry what looks like an inline comment. gitignore has no "
        "inline comments, so the pattern is the WHOLE line -- filename, spaces "
        "and prose -- and it matches nothing. Move the comment to its own "
        "line:\n  " + "\n  ".join(offenders)
    )


def test_the_web_push_private_key_is_ignored():
    """Names the consequence the rule above exists to prevent.

    Kept separate from the generic scan so the failure says what is at stake
    rather than only which line is malformed.
    """
    assert _git("check-ignore", "-q", "vapid_keys.json").returncode == 0, (
        "vapid_keys.json -- the generated web-push PRIVATE key -- is not "
        "ignored. It sits untracked on the deploy host, which is one "
        "`git add -A` away from being committed."
    )


# ── the mechanism, locked so the reasoning cannot rot ────────────────────────

def test_a_directory_rule_matches_by_path_not_by_existence():
    """Why an ignore check must query a path INSIDE the directory.

    A `dir/` rule is directory-only. `git check-ignore dir` can only match when
    git can stat `dir` and see it is a directory; with the directory absent the
    same rule and the same query disagree. Querying `dir/anything` resolves
    against the rule alone.

    Built in a throwaway repository rather than asserted, because this is the
    exact reasoning that produced two shipped defects in one session.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        subprocess.run(["git", "init", "-q", str(repo)], check=True,
                       capture_output=True)
        (repo / ".gitignore").write_text("/somedir/\n", encoding="utf-8")

        # directory ABSENT -- the bare-name query cannot resolve
        bare = _git("check-ignore", "-q", "somedir", cwd=repo).returncode
        inside = _git("check-ignore", "-q", "somedir/probe.json", cwd=repo).returncode
        assert inside == 0, (
            "querying a path inside a directory-only rule failed even though "
            "the rule is present -- the premise of the fix is wrong."
        )
        assert bare != 0, (
            "the bare-name query matched with the directory absent, so the "
            "existence-dependence this test documents does not exist and the "
            "explanation in tests/test_generated_artifacts_are_not_tracked.py "
            "is wrong. Re-derive before trusting either."
        )

        # directory PRESENT -- now the bare-name query agrees, which is exactly
        # why the sandbox passed while the deploy host failed
        (repo / "somedir").mkdir()
        assert _git("check-ignore", "-q", "somedir", cwd=repo).returncode == 0, (
            "with the directory present the bare-name query still did not "
            "match; the two environments would then agree and the observed "
            "sandbox-vs-box split would have another cause."
        )


# ── generated artifacts observed on the deploy host ──────────────────────────

# Measured 2026-07-29 in `git status --porcelain` on test4 after #60 landed.
# Deliberately NOT included: plugins/ackgate.py and plugins/handdropped.py.
# Those are 50 and 6 bytes, created 17 ms apart, and ackgate.py contains
# `PLUGIN = {"name": "myplugin", "version": "1.0.0"}` -- they are TEST FIXTURES,
# not operator files. Ignoring them would hide a live plugins-directory leak
# that the Cut B guard was supposed to have closed. They stay visible until
# that is re-derived.
# Also excluded: templates/reviewed/*.template.json (non-.bak). Two siblings are
# tracked and shipped, so whether a new one is generated or hand-authored is an
# operator question, not a derivable one.
_GENERATED = [
    "vapid_keys.json",
    "tools/deployed_version.txt",
]


@pytest.mark.parametrize("rel", _GENERATED)
def test_generated_artifacts_are_ignored(rel):
    assert _git("check-ignore", "-q", rel).returncode == 0, (
        f"{rel} is generated at runtime on the deploy host and is neither "
        f"tracked nor ignored, so it makes the deployed tree read dirty and is "
        f"one `git add -A` from being committed."
    )
