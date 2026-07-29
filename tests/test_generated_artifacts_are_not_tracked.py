"""Git tracks a file the application rewrites, so every deploy reverts it.

THE DEFECT. `bulk_downloader/community_scrapers.py:133-140`:

    def _cache_dir() -> Path:
        p = Path(".") / "community_scrapers_cache"
        p.mkdir(parents=True, exist_ok=True)
        return p

The path is cwd-relative, and on the deploy host the service's cwd IS the
install root IS the git work tree -- so the cache lands inside the repository.
`_INDEX_CACHE_TTL_S` is 3600 and `_save_index_cache` runs on expiry, so the app
rewrites it at least hourly.

`community_scrapers_cache/community_scrapers_index.json` is TRACKED. Per
CLAUDE.md section 7 the box updates with `git fetch` + `git reset --hard`, which
has no `unzip -x` equivalent and discards operator live-edits. So the cycle is:
deploy reverts the file to the committed copy, the app overwrites it within the
hour, the next deploy reverts it again. Observed on the box as a permanently
dirty tree:

    $ git status --porcelain
     M community_scrapers_cache/community_scrapers_index.json

The committed copy is stale cache data by construction. Its sibling
`community_scrapers_manifest.json` -- which records what the operator actually
installed, i.e. real state -- is NOT tracked. Git carries the cache and drops
the state.

WHY A DERIVED DENOMINATOR AND NOT A LIST. A test naming this one file passes
forever while the next generated artifact gets committed. The denominator here
is every directory the application CREATES AT RUNTIME whose path resolves
against the repository, derived by AST from `mkdir` call sites. A new one is
caught the first time someone commits a file into it, with no edit to this test.

THE PREDICATE TRAP THIS TEST WAS BUILT AROUND. A first version resolved the
trailing literal of any `mkdir` target and reported NINE directories, including
`templates` -- because `eol_export.py:61` does `Path(dest_dir) / "templates"`,
where `dest_dir` is a caller-supplied EXPORT destination that is not in this
tree at all. `templates/reviewed/*.json` are shipped assets and are correctly
tracked; flagging them would have been a false positive that cost real time, and
gitignoring them would have broken the shipped set.

The fix is that the BASE must resolve repo-relative, not merely the trailing
segment. With that requirement the set is 1, not 9. This is CLAUDE.md section 1
applied literally: the instrument (AST over git ls-files) fixes the denominator,
and the predicate (base must be repo-relative) fixes the subject. The first
version had the right instrument and the wrong subject.

ONE ASSERTION HERE IS NOT RED, AND SAYS SO. `test_no_tracked_file_is_gitignored`
passes on pristine source -- there are zero violations today. It is a guard
against a FUTURE force-add (`git add -f secrets.json` after this cut ignores it),
not evidence of a present defect, and it is labelled as such rather than counted
as part of the RED.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True).stdout


def _repo_relative_segments(node: ast.AST) -> list[str] | None:
    """Resolve `Path("<literal>") / "a" / "b"` to ['a','b'], else None.

    Returns None whenever the BASE is dynamic -- a parameter, an env var,
    tempfile.gettempdir(). That is the whole point: a dynamic base means the
    directory is somewhere else entirely, and counting its trailing literal is
    the predicate matching the wrong subject.
    """
    segs: list[str] = []
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        if not (isinstance(node.right, ast.Constant)
                and isinstance(node.right.value, str)):
            return None
        segs.insert(0, node.right.value)
        node = node.left
    if not segs:
        return None
    if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Path"
            and node.args and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)):
        base = node.args[0].value
        return segs if base in (".", "") else [base] + segs
    return None


def _app_created_dirs() -> dict[str, list[str]]:
    """Every directory application code mkdirs at a repo-relative path.

    Denominator is `git ls-files`, never rglob or a bare find -- ephemeral agent
    worktrees live under the repository root and a tree walk descends into them,
    returning a denominator that includes other agents' copies of these files.
    """
    files = [f for f in _git("ls-files", "-z", "bulk_downloader/*.py").split("\0") if f]
    assert len(files) > 100, (
        f"the denominator collapsed to {len(files)} files -- a scan that cannot "
        f"see the application cannot certify anything about it."
    )
    out: dict[str, list[str]] = {}
    for rel in files:
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        binds: dict[str, list[str]] = {}
        for n in ast.walk(tree):
            if (isinstance(n, ast.Assign) and len(n.targets) == 1
                    and isinstance(n.targets[0], ast.Name)):
                segs = _repo_relative_segments(n.value)
                if segs:
                    binds[n.targets[0].id] = segs
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "mkdir"):
                recv = n.func.value
                segs = _repo_relative_segments(recv) or (
                    binds.get(recv.id) if isinstance(recv, ast.Name) else None)
                if segs:
                    out.setdefault("/".join(segs), []).append(f"{rel}:{n.lineno}")
    return out


# ── the denominator itself must be non-degenerate ────────────────────────────

def test_the_scan_finds_at_least_one_app_created_directory():
    """A zero-in-every-bucket result is a failure signal, not a pass.

    `bd-guardcheck` once reported `0 ok, 0 drifted, 7 missing` and exited 0 --
    it could not see the files it certifies and said so with a success code. If
    this scan silently resolves nothing, every assertion below passes vacuously.
    """
    dirs = _app_created_dirs()
    assert dirs, (
        "the AST scan resolved NO app-created directories. Either the predicate "
        "broke or the denominator is empty; either way the assertions below "
        "would certify a tree they cannot see."
    )


# ── the defect ───────────────────────────────────────────────────────────────

def test_app_created_directories_hold_no_tracked_files():
    """A tracked file inside a runtime-generated directory is reverted every deploy."""
    offenders = {}
    for d, sites in sorted(_app_created_dirs().items()):
        tracked = [ln for ln in _git("ls-files", "--", d).splitlines() if ln]
        if tracked:
            offenders[d] = (tracked, sites)
    assert not offenders, (
        "these directories are created by the application at runtime and also "
        "contain files tracked by git, so `git reset --hard` reverts them on "
        "every deploy and the app overwrites them again:\n" +
        "\n".join(f"  {d}  (mkdir at {s[0]})\n" +
                  "\n".join(f"      tracked: {t}" for t in f)
                  for d, (f, s) in offenders.items())
    )


def test_app_created_directories_are_gitignored():
    """Not ignoring them leaves the deployed tree permanently dirty."""
    unignored = [d for d in sorted(_app_created_dirs())
                 if subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", d]).returncode != 0]
    assert not unignored, (
        f"the application creates {unignored} at runtime but they are not "
        f"gitignored, so the deployed work tree reports dirty forever and any "
        f"check reading `git status` sees noise it cannot attribute."
    )


# ── the future guard, which is NOT red today and is labelled so ──────────────

def test_no_tracked_file_is_gitignored():
    """A file that is both tracked and ignored has an INERT ignore rule.

    NOT a RED test: there are zero violations on pristine source. It exists so
    that once the operator-state paths below are ignored, a later
    `git add -f secrets.json` is caught -- the ignore rule would silently do
    nothing and the credential file would start being committed.
    """
    tracked = _git("ls-files", "-z")
    proc = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--stdin", "-z"],
                          input=tracked, capture_output=True, text=True)
    bad = [p for p in proc.stdout.split("\0") if p]
    assert not bad, (
        f"these paths are tracked AND matched by .gitignore, so their ignore "
        f"rule is inert and they keep being committed: {bad}"
    )


# ── operator state must be ignored, so `git clean -fd` cannot take it ────────

# Measured on the deploy host 2026-07-29 via `git status --porcelain`: every one
# of these was `??` -- untracked AND unignored. That state is the worst of both:
# `git clean -fd` destroys them (including the credential store), and they make
# the tree read dirty to anything inspecting git status. This IS a list, and it
# is a list of the FIX rather than of the denominator -- the denominator guard
# is test_no_tracked_file_is_gitignored above.
_OPERATOR_STATE = [
    "secrets.json",
    "secrets_meta.json",
    "profiles/",
    "state/",
    "live_tests/results/",
    "plugins/plugins.registry.json",
    "macros/",
]


@pytest.mark.parametrize("rel", _OPERATOR_STATE)
def test_operator_state_paths_are_ignored(rel):
    ignored = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "-q", rel]).returncode == 0
    assert ignored, (
        f"{rel} is operator state on the deploy host and is neither tracked nor "
        f"ignored. `git clean -fd` would destroy it -- for secrets.json that is "
        f"the credential store -- and it makes the deployed tree read dirty."
    )
