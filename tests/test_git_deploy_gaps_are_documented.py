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
import tempfile
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


_PROBE_TOKEN = "bd-row211-spa-probe"


def _build_probe_dist(root: Path) -> tuple[Path, bytes, str, bytes]:
    """A throwaway frontend/dist whose bytes cannot occur in the real bundle.

    The PARENT deliberately gets a DECOY index.html. In production
    `frontend/index.html` is the raw un-built Vite template, so an
    implementation that served the index from the dist root's parent would
    answer 200 with a blank app -- the silent failure. Planting a differing
    decoy turns that into an exact-bytes mismatch rather than a 404, which is
    the only reason the byte comparison below is load-bearing.

    The parent gets NO assets/ directory, so an implementation that looked
    assets up outside the dist root finds nothing.
    """
    frontend = root / "frontend"
    dist = frontend / "dist"
    (dist / "assets").mkdir(parents=True)
    index_bytes = ('<!doctype html><div id="root">%s-index</div>' % _PROBE_TOKEN).encode()
    asset_name = "assets/%s-bundle.js" % _PROBE_TOKEN
    asset_bytes = ("// %s-asset\n" % _PROBE_TOKEN).encode()
    decoy_bytes = ('<!doctype html><div id="root">%s-DECOY</div>' % _PROBE_TOKEN).encode()
    (dist / "index.html").write_bytes(index_bytes)
    (dist / asset_name).write_bytes(asset_bytes)
    (frontend / "index.html").write_bytes(decoy_bytes)
    # The fixture's own preconditions: assert it built the shape claimed above,
    # so a later byte comparison cannot pass because nothing was written.
    assert (dist / "index.html").read_bytes() == index_bytes
    assert (dist / asset_name).read_bytes() == asset_bytes
    assert (frontend / "index.html").read_bytes() != index_bytes, (
        "the decoy index is byte-identical to the real one, so serving from "
        "the wrong directory would be invisible"
    )
    assert not (frontend / "assets").exists(), (
        "the probe parent has an assets/ directory; a wrong-directory asset "
        "lookup could then succeed by accident"
    )
    return dist, index_bytes, asset_name, asset_bytes


def _assert_spa_served_from(expected_root: Path) -> None:
    """Prove, by serving, that the SPA comes out of `expected_root`.

    ARM 1 is a path-identity statement about the live module global.
    ARM 2 proves that global is what ACTUALLY governs serving: it plants a
    probe bundle somewhere else, repoints the global at it, and demands those
    exact bytes back through the Flask test client. Without ARM 2, ARM 1 would
    only be a statement about a name that nothing has to read.
    """
    # Lazy import on purpose. conftest's autouse isolated_bd_home has already
    # set BD_HOME/TMPDIR and chdir'd by the time a test body runs; hoisting
    # this to module level would import the app -- blueprints, queue recovery,
    # folder-watcher thread -- against the real HOME at collection time.
    import bulk_downloader.app as bd_app

    # ARM 1 -- the runtime serving root IS frontend/dist. Resolved-path
    # identity only: no built bundle is required, so a clean checkout (and CI,
    # which never runs `npm run build`) does not fail here.
    live_root = bd_app._M2_DIST_ROOT
    assert live_root.resolve() == expected_root.resolve(), (
        "SPA serving root is not frontend/dist: "
        "bulk_downloader.app._M2_DIST_ROOT resolves to %s, but the deploy "
        "runbook's 'rebuild the frontend' step is about %s. Either the app "
        "was repointed -- fix it, or rewrite the runbook in the same cut -- "
        "or this gate is aimed at the wrong checkout."
        % (live_root.resolve(), expected_root.resolve())
    )

    # ARM 2 -- serving really reads that global, proved with planted bytes.
    with tempfile.TemporaryDirectory(prefix="bd_spa_gate_") as td:
        dist, index_bytes, asset_name, asset_bytes = _build_probe_dist(Path(td))
        assert dist.resolve() != (REPO_ROOT / "frontend" / "dist").resolve(), (
            "the probe bundle landed on the real serving root; the byte "
            "comparisons below would then prove nothing about WHICH directory "
            "was read"
        )
        saved = bd_app._M2_DIST_ROOT
        try:
            bd_app._M2_DIST_ROOT = dist
            # Precondition: the handler resolves the global at CALL time.
            assert bd_app.serve_spa_root.__globals__["_M2_DIST_ROOT"] is dist, (
                "the repoint did not reach serve_spa_root's globals"
            )
            client = bd_app.app.test_client()
            r = client.get("/")
            assert r.status_code == 200, (
                "GET / returned %s with a valid SPA bundle at the serving "
                "root -- the site root does not serve the bundle"
                % (r.status_code,)
            )
            assert r.data == index_bytes, (
                "GET / did not return the bytes planted at the serving root "
                "(got %r): the directory _M2_DIST_ROOT names is not the one "
                "the handler reads from -- a hardcoded path, a snapshot taken "
                "at import time, or the dist root's parent."
                % (r.data[:80],)
            )
            a = client.get("/" + asset_name)
            assert (a.status_code, a.data) == (200, asset_bytes), (
                "a real asset under the serving root came back %s/%r instead "
                "of 200 and its own bytes: assets are not being read out of "
                "the directory _M2_DIST_ROOT names."
                % (a.status_code, a.data[:80])
            )
            # Negative control: exact-byte matching above is only meaningful
            # if a MISSING asset is not also answered with something.
            miss = client.get("/assets/%s-absent.js" % _PROBE_TOKEN)
            assert miss.status_code == 404, (
                "a missing asset returned %s, so the asset arm above proves "
                "nothing -- anything under the serving root would 'match'."
                % (miss.status_code,)
            )
        finally:
            bd_app._M2_DIST_ROOT = saved
        assert bd_app._M2_DIST_ROOT is saved, (
            "the serving root was not restored; every later test in this "
            "process would run against the probe"
        )


def test_the_spa_is_actually_served_from_that_directory():
    """Guards against the warning outliving the behaviour it describes.

    If the app stopped serving from frontend/dist, the rebuild warning would
    be cargo -- true about the filesystem, irrelevant to the operator.

    This USED to be `assert "frontend/dist" in app.py`, which is a textual
    proxy for a runtime property (backlog row 211). The load-bearing
    expression is `Path(__file__).parent.parent / "frontend" / "dist"`, which
    contains no such substring, while five comment and error-string mentions
    do -- so the scan was satisfied by prose alone and stayed green under a
    repoint. It now serves.

    WHAT THIS GATE DOES NOT PROVE, stated so nobody reads more into it:

    1. WHERE, never WHETHER-FRESH. It proves the app serves out of
       frontend/dist. It substitutes a probe bundle precisely so it works on
       an unbuilt checkout, so a stale or corrupt REAL frontend/dist is
       invisible here. That is the deploy hazard the runbook warns about and
       it stays with capture and the live-service L36 check.
    2. IT ASSUMES A CALL-TIME LOOKUP. ARM 2 works because serve_spa_root
       reads _M2_DIST_ROOT from module globals per request. A refactor that
       snapshots the root at import time would red this gate on semantically
       correct code; the fix then is to expose the live root, not to weaken
       the gate back into a scan.
    3. THE RUNBOOK ARM BELOW STAYS A TEXT SCAN, deliberately: its subject
       genuinely IS what the document says, which is the documentation
       exemption in project-knowledge/CUT_TIERING.md, not a proxy.
    """
    _assert_spa_served_from(REPO_ROOT / "frontend" / "dist")


def test_the_gate_does_not_require_a_built_bundle():
    """OVER-SENSITIVITY CONTROL: correct wiring + an UNBUILT SPA stays green.

    CI never runs `npm run build` -- frontend/dist is absent in every shard --
    and a fresh clone has no bundle either. A behavioural gate that demanded a
    real one would red on correct code, be switched off, and then nothing
    would guard the runbook warning at all. This pins that it does not.
    """
    import bulk_downloader.app as bd_app

    with tempfile.TemporaryDirectory(prefix="bd_spa_unbuilt_") as td:
        fake_dist = Path(td) / "checkout" / "frontend" / "dist"
        fake_dist.parent.mkdir(parents=True)
        # PRECONDITION -- genuinely unbuilt, which is the whole point.
        assert not fake_dist.exists(), (
            "the control is not testing an unbuilt checkout"
        )
        saved = bd_app._M2_DIST_ROOT
        try:
            bd_app._M2_DIST_ROOT = fake_dist      # correct wiring, no bundle
            _assert_spa_served_from(fake_dist)    # must NOT raise
        finally:
            bd_app._M2_DIST_ROOT = saved
        assert bd_app._M2_DIST_ROOT is saved, (
            "the serving root was not restored after the control"
        )

def test_a_prose_mention_of_frontend_dist_cannot_satisfy_this_gate():
    """RED CONTROL: repoint the RUNTIME root, leave the PROSE intact.

    This is the permanent evasion fixture. It puts the tree into the one state
    that separates a behavioural gate from a textual proxy -- the app is
    serving from somewhere that is NOT frontend/dist, while the string
    "frontend/dist" is still all over bulk_downloader/app.py's comments and
    error messages -- and demands that the gate above notice.
    """
    import bulk_downloader.app as bd_app

    # PRECONDITION 1 -- the old textual proxy is still satisfied, so a failure
    # below cannot be read as "the string vanished".
    src = (REPO_ROOT / "bulk_downloader" / "app.py").read_text(encoding="utf-8")
    assert "frontend/dist" in src, (
        "precondition lost: bulk_downloader/app.py no longer contains the "
        "literal 'frontend/dist' anywhere. This control only means something "
        "while a text scan would still pass."
    )
    with tempfile.TemporaryDirectory(prefix="bd_spa_evasion_") as td:
        decoy = Path(td) / "frontend" / "build"
        decoy.mkdir(parents=True)
        # PRECONDITION 2 -- the decoy is genuinely not the real serving root.
        assert decoy.resolve() != (REPO_ROOT / "frontend" / "dist").resolve(), (
            "the evasion decoy resolved to the real serving root; this control "
            "would then assert nothing"
        )
        saved = bd_app._M2_DIST_ROOT
        try:
            bd_app._M2_DIST_ROOT = decoy
            # PRECONDITION 3 -- the repoint is visible to the handler.
            assert bd_app.serve_spa_root.__globals__["_M2_DIST_ROOT"] is decoy, (
                "the repoint did not reach serve_spa_root's globals, so the "
                "app under test is not the one that was repointed"
            )
            with pytest.raises(
                AssertionError, match="SPA serving root is not frontend/dist"
            ):
                test_the_spa_is_actually_served_from_that_directory()
        finally:
            bd_app._M2_DIST_ROOT = saved
        assert bd_app._M2_DIST_ROOT is saved, (
            "the serving root was not restored; every later test in this "
            "process would run against the decoy"
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


BD_GATE_SCOPE = "repo-wide"
