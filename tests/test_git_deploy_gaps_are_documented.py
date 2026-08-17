"""What `git reset --hard` does NOT do must be written down in one place.

The box moved from an `unzip -o` overlay to a pure-git deploy
(`git fetch origin main` + `git reset --hard origin/main` + restart). Retiring
the overlay model from the docs is easy to do badly in two opposite ways:

  1. Delete too little -- leave `unzip -o` runbooks that no longer describe
     anything, so a session runs a deploy-manifest step against a hazard that
     cannot occur.
  2. Delete too much -- rip out the surviving post-deploy warnings along with
     the overlay text they were sitting next to. Those warnings were never
     about the overlay. They are about the gap between "the files changed" and
     "the running system changed", and that gap is identical under git.

The second failure is the dangerous one, and it nearly happened here. A sweep
of this repo was briefed that THREE warnings survive the deploy change. There
are FOUR: the SPA bundle under `frontend/dist/` is gitignored, carries zero
tracked files, and is therefore never delivered by a git deploy at all. Had
the three-item list been written into every runbook at once, the omission
would have been frozen into ten documents simultaneously -- retiring one stale
claim by manufacturing another.

So this file pins the FACTS that make each warning true, and pins that the
canonical runbook states all four. Any doc that repeats the list is a second
denominator that will drift; prefer pointing at the canonical one.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL = REPO_ROOT / "docs" / "repo" / "FRESH_HOST_BRINGUP.md"


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT),
        capture_output=True, text=True, timeout=60,
    )
    return proc.stdout


# --------------------------------------------------------------------------
# The structural facts. If one of these changes, the corresponding warning is
# no longer true and the doc must be revisited -- the test failing IS the
# notification.
# --------------------------------------------------------------------------

def test_frontend_dist_is_not_delivered_by_a_git_deploy():
    """The fourth gap, and the one the sweep missed."""
    tracked = [p for p in _git("ls-files", "frontend/dist").splitlines() if p.strip()]
    assert tracked == [], (
        "frontend/dist/ now has tracked files:\n  "
        + "\n  ".join(tracked[:10])
        + "\n\nIf the SPA bundle is committed, `git reset --hard` DOES deliver "
        "it, and the 'rebuild the frontend' warning in the deploy runbook is "
        "no longer true. Update the runbook in the same cut."
    )
    # Queries a path INSIDE the directory. `dist/` (frontend/.gitignore:3) is a
    # directory-only rule, and `git check-ignore frontend/dist` can only match
    # when git can stat the path and see it is a directory. This asked for the
    # bare name, so it failed on any checkout where the SPA has not been built
    # -- reporting "neither tracked nor gitignored" about a rule that is present
    # and correct. Same defect, and same fix, as
    # tests/test_generated_artifacts_are_not_tracked.py; the mechanism is locked
    # in tests/test_gitignore_rules_actually_match.py
    # ::test_a_directory_rule_matches_by_path_not_by_existence.
    ignored = _git("check-ignore", "-v", "frontend/dist/.probe").strip()
    assert ignored, (
        "frontend/dist/ is neither tracked nor gitignored. It is now in an "
        "undefined state -- decide which, and say so in the runbook."
    )


def test_the_spa_is_actually_served_from_that_directory():
    """Guards against the warning outliving the behaviour it describes.

    If the app stopped serving from frontend/dist, the rebuild warning would
    be cargo -- true about the filesystem, irrelevant to the operator.
    """
    app = (REPO_ROOT / "bulk_downloader" / "app.py").read_text(encoding="utf-8")
    assert "frontend/dist" in app, (
        "bulk_downloader/app.py no longer references frontend/dist. The "
        "deploy runbook's frontend-rebuild step may now be describing "
        "nothing."
    )


# --------------------------------------------------------------------------
# The canonical runbook must state all four gaps.
# --------------------------------------------------------------------------

# Each gap is (label, regex alternatives). Matching is deliberately loose on
# wording and strict on subject: the point is that the operator is TOLD about
# the thing, not that a particular sentence survives.
_GAPS = (
    ("bytecode caches are not cleared",
     r"__pycache__|\.pyc\b"),
    ("gitignored generated artifacts are not refreshed",
     r"gui_parity_inventory|gitignored"),
    ("the service is not restarted",
     r"systemctl\s+restart|restart the service"),
    ("the SPA bundle is not rebuilt",
     r"frontend/dist|npm run build"),
)


def deploy_section() -> str:
    """The Deploy runbook ONLY -- not the whole document.

    Searching the whole file is how the first version of this test passed
    vacuously: `frontend/dist` occurs in the zip-walk paragraph and
    `gui_parity_inventory` in the release checklist, neither of which tells a
    deploying operator anything. A gate whose denominator is the entire
    document cannot see the section it is asked about, so it reports OK.
    """
    text = CANONICAL.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^#{2,4}\s+Routine deploy and rollback\s*$", line.strip()):
            start = i
            break
    assert start is not None, (
        f"{CANONICAL.relative_to(REPO_ROOT)} has no routine deploy/rollback heading. The "
        f"canonical deploy runbook moved or was renamed; repoint this test at "
        f"wherever it now lives rather than widening the search back to the "
        f"whole file."
    )
    end = len(lines)
    opener = re.match(r"^(#{2,4})\s", lines[start]).group(1)
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(#{1,4})\s", lines[j])
        if m and len(m.group(1)) <= len(opener):
            end = j
            break
    section = "\n".join(lines[start:end])
    assert len(section.splitlines()) >= 5, (
        "the Deploy section is under five lines; it cannot contain a runbook "
        "and this test would pass over almost nothing."
    )
    return section


@pytest.mark.parametrize("label,pattern", _GAPS, ids=[g[0] for g in _GAPS])
def test_canonical_runbook_documents_the_gap(label: str, pattern: str):
    section = deploy_section()
    assert re.search(pattern, section, re.I), (
        f"{CANONICAL.relative_to(REPO_ROOT)} does not tell the operator that "
        f"{label}.\n\n"
        f"A git deploy moves files. It does not make the running system match "
        f"them. All four gaps below survive the move from `unzip -o` to "
        f"`git reset --hard` -- they were never properties of the overlay:\n"
        + "\n".join(f"  - {lbl}" for lbl, _ in _GAPS)
        + "\n\nDo not remove one of these while retiring the overlay text it "
          "happens to sit beside."
    )


def test_the_gap_list_has_not_silently_shrunk():
    """Denominator canary for this file's own subject.

    Someone deleting a _GAPS entry would make the parametrised test above pass
    over a smaller set, quietly. Four is the measured count as of the git-
    deploy migration; changing it is a deliberate act that lands here.
    """
    assert len(_GAPS) == 4, (
        f"_GAPS has {len(_GAPS)} entries, expected 4. If a gap was genuinely "
        f"closed -- for example the deploy now restarts the service itself -- "
        f"say so here and in the runbook. If one was added, add it to both."
    )
