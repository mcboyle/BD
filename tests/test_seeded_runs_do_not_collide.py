"""Each seeded run dedups against the LAST one, so only the first ever downloads.

THE DEFECT, measured on the box 2026-07-29 across two consecutive seeded runs.
The second run:

    "http://127.0.0.1:8899/scene/2?bdseed=1": {
        "status": "skipped_duplicate",
        "message": "Duplicate of history #1 (prior download, 2026-07-29T00:23:43)",
        "filename": ""
    }

That timestamp is the PREVIOUS capture. Nothing downloaded, ~/Downloads stayed
empty, and L11 and L12 reported "no completed downloads" -- which reads as BD
failing, when BD correctly recognised a URL it had already fetched.

seeded_url() returned a byte-identical URL on every run. The diagnosis
recorded here originally claimed history is append-only -- db_log() its only
writer, db_prune() (by AGE, not by marker) its only deleter -- so teardown
structurally could not remove what a completed seeded download leaves behind.
That claim was FALSE: bulk_downloader/db.py:988-992 records the retraction
(batch_ops.bulk_delete issues DELETE FROM history WHERE id = ? and is
reachable over HTTP at POST /api/batch/delete), and `--teardown
--clear-history` now uses exactly that route. The fix below predates the
clear and stands without it: the clear is OPT-IN, so runs must not collide
when nobody passes it.

So the seed set could exercise a real download exactly ONCE, ever, until the
rows aged out. Every capture after the first measured dedup instead of download,
and the operator had to clear the rows by hand for a test to prove anything.

THE FIX IS TO STOP COLLIDING, NOT TO DELETE. Adding a marker-scoped destructive
history route to the app is a much wider blast radius than the problem warrants,
and the residue is inert once it cannot be mistaken for the current run. A
per-run nonce in the query string makes each run's URLs unique. The marker stays
in the URL because `_is_seeded()` is `SEED_MARKER in entry["url"]` and both
teardown and preflight depend on it.

WHY L14 NEEDS A SECOND PASS. Unique URLs would leave L14 (stash-dedup-skip) with
nothing to recognise, and its only previous route to green was the cross-run
collision this cut removes -- which the audit correctly called a one-run-late
gate: it certified that dedup fired at SOME point, not that BD skipped the
duplicate it was just handed. The deliberate duplicate inside a single run does
not help either, because BD collapses it at INTAKE (measured: `"dupes": 1` at
seed time), so it never reaches the runner.

A duplicate therefore has to be queued AFTER the first copy has completed. That
is a real, same-run dedup decision -- the thing L14 claims to test -- rather than
an artefact of last night's history.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "tools" / "live_seed.py"


def _load(name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(SEED_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seeder():
    return _load("bd_live_seed_collide")


# ── denominator canary ───────────────────────────────────────────────────────

def test_the_seeder_still_builds_urls(seeder):
    assert seeder.seeded_url(0), (
        "seeded_url() produced nothing; every assertion below would be vacuous"
    )


# ── the defect ───────────────────────────────────────────────────────────────

def test_two_runs_do_not_produce_identical_urls(seeder):
    """The whole defect in one assertion: run N+1 must not repeat run N.

    Two independent module loads stand in for two runs, which is what the box
    actually does -- capture.sh invokes tools/live_seed.py as a fresh process.
    """
    other = _load("bd_live_seed_collide_second")
    first = seeder.seeded_url(0)
    second = other.seeded_url(0)
    assert first != second, (
        f"both runs seeded {first!r}. The second run dedups against the first's "
        f"history row -- measured on the box as 'skipped_duplicate: Duplicate of "
        f"history #1' -- so nothing downloads and L11/L12 report on a pipeline "
        f"that was never asked to run."
    )


def test_the_marker_survives_the_change(seeder):
    """_is_seeded reads the URL; lose the marker and teardown orphans everything."""
    url = seeder.seeded_url(0)
    assert seeder._is_seeded({"url": url}), (
        f"_is_seeded() no longer recognises {url!r}. Teardown and preflight both "
        f"discriminate on it: without the marker teardown orphans every seeded "
        f"row, and preflight reads seeded work as the operator's and refuses."
    )


def test_the_url_still_points_at_a_servable_path(seeder):
    """A nonce in the query must not disturb routing."""
    parsed = urlparse(seeder.seeded_url(0))
    assert parsed.path in seeder._SEED_PATHS, (
        f"seeded path {parsed.path!r} is not one of {seeder._SEED_PATHS}; the "
        f"nonce belongs in the query string, which Flask ignores when matching."
    )
    assert parse_qs(parsed.query).get(seeder.SEED_MARKER), (
        "the marker is no longer a query parameter"
    )


def test_within_one_run_the_urls_are_stable(seeder):
    """The nonce is per RUN, not per call -- teardown and settle compare URLs."""
    assert seeder.seeded_url(0) == seeder.seeded_url(0), (
        "seeded_url() is not stable within a run. wait_for_settle and teardown "
        "both match on the URL string; a per-call nonce would make every job "
        "unrecognisable to the code that waits for it."
    )


# ── L14's replacement subject ────────────────────────────────────────────────

def test_the_seeder_can_requeue_a_completed_url_for_a_real_dedup_check(seeder):
    """L14 needs a duplicate that reaches the RUNNER, not one dropped at intake.

    BD collapses a repeat inside one seeding batch before it is ever queued
    (measured: "dupes": 1), so the only way to observe a genuine dedup decision
    is to queue the URL again once the first copy has completed.
    """
    assert hasattr(seeder, "requeue_for_dedup"), (
        "the seeder has no way to queue an already-completed URL a second time. "
        "Without it L14 has no subject at all once runs stop colliding, and its "
        "only previous route to green was the cross-run collision this cut "
        "removes."
    )


def test_the_seeder_actually_calls_the_dedup_requeue(seeder):
    """The denominator is the INVOCATION, not the function.

    CUT A this same session shipped a tunnel seeder with 353 lines of tests and
    nothing asserting anything ever called it, so L30 reported "nothing to
    verify" on every capture. Same mistake, same file, one cut later -- so the
    gate here reads main(), not requeue_for_dedup.
    """
    import ast
    src = SEED_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    main_fn = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    assert main_fn is not None, "main() not found in tools/live_seed.py"
    called = {n.func.id for n in ast.walk(main_fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "requeue_for_dedup" in called, (
        "main() never calls requeue_for_dedup, so the dedup pass exists but "
        "never runs and L14 still has nothing to observe."
    )
