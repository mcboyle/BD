"""Rows 545, 559 and 562: the "Already have" arm must carry its whole result.

One coherent safety contract over one block of ``runner_transport._do_download``
(the "Already have" pre-download check), which today drops a different part of
its result in each of three ways.

ROW 545 -- THE SKIP ARM EATS THE FORCE FLAG. Approve and the capture workflow's
"verify live" both stamp ``force_download`` on the job to bypass dedup.
``_dedup_preflight`` honours it (runner_integrity.py, first statement). The skip
arm never reads it: it asks ``skip_if_exists and final_path.exists()``, gets
"same" from ``db_skip_identity``, POPS the flag, writes another
``bytes_fetched=0`` "already on disk" row and reports "Already have". So a
corrupt-but-present file can never be re-fetched from the UI, and guided capture
grades that no-op as "Media validated".

ROW 559 -- THE DIAGNOSTIC IS GATED BEHIND A DIFFERENT QUESTION. ``db_skip_
identity`` is called ONLY inside ``if skip_if_exists and final_path.exists()``;
otherwise ``_identity`` is set to the empty string. But its "unproven" answer is
keyed on the URL, not on the rendered path -- it is the row-479 cut's one
operator-visible output, a ``needs_review`` row naming a file whose attribution
records no transfer. Turn ``skip_if_exists`` off, or change a tier or date so
the template renders a name that is not on disk, and that output has an EMPTY
denominator for exactly the upgraded-host rows it was written for.

ROW 562 -- THE DIAGNOSTIC'S OWN WRITE CAN PARK THE JOB. That ``needs_review``
``db_log`` is unguarded. A sqlite write lock held past the 10s busy_timeout
under multi-worker load, or a full disk, turns a job that was ABOUT TO DOWNLOAD
into an unclassified "worker error: database is locked", parked 600s with no
history row -- a diagnostic that costs the download it was only meant to
annotate.

THE CONTRACT. The identity is measured for every job; the skip decision consults
the force flag as well as the config gate; and a failure to RECORD the diagnostic
is recorded and does not replace the transfer.

WHY ``_do_download`` IS DRIVEN DIRECTLY HERE. ``_process_one`` calls
``_dedup_preflight`` first, and at this cut's base that preflight short-circuits
on any 'done' row -- including the zero-transfer rows these tests seed. Row 544
(same cut, tests/test_row544_*.py) is what makes this block reachable through
``_process_one`` for a same-URL re-run; driving the transport directly keeps
each row's evidence about its own defect.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

_SITE_ID = "row545site"
_SITE_NAME = "Row 545 Site"
_URL = "https://members.example.test/scene/row545-first"
_TITLE = "Row 545 Scene"

# Renders one name for every scene on the site, so final_path is deterministic
# and a second run of the same URL lands on an existing file.
_COLLIDING_TEMPLATE = "{site} - {resolution}"
_COLLIDING_NAME = "Row 545 Site - 1080p.mp4"
# Varies with the resolution label, which is how a tier change makes the
# rendered path miss a file the URL still owns (row 559's second shape).
_TIERED_TEMPLATE = "{site} {resolution} tier"

_PAYLOAD = b"ROW545 real scene bytes, transferred over the fake wire"


class _Locator:
    def __init__(self, href: str):
        self.href = href

    def get_attribute(self, name):
        return self.href if name == "href" else None

    def click(self):  # pragma: no cover - the fake transport is pinned below
        raise AssertionError("the fake transport must be used, not a click")


class _FakePage:
    """The minimum Playwright surface ``_do_download`` touches."""

    def __init__(self, url: str, title: str):
        self.url = url
        self._title = title

    def title(self):
        return self._title

    def evaluate(self, _script):
        return {"og_title": self._title, "document_title": self._title, "h1": ""}


class _Transport:
    """Records every transfer: the counts below are measurements, not guesses."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def install(self, runner):
        def _http_download(page_url, page, ctx, file_url, final_path):
            Path(final_path).parent.mkdir(parents=True, exist_ok=True)
            Path(final_path).write_bytes(_PAYLOAD)
            self.calls.append((page_url, str(final_path)))
            return len(_PAYLOAD), len(_PAYLOAD)

        def _pw_save(dl, final_path):
            raise AssertionError(
                "the browser arm ran; this fixture pins the HTTP arm so the "
                "transfer counts below measure one known path")

        runner._http_download = _http_download
        runner._pw_save = _pw_save


def _runner(download_dir, *, template=_COLLIDING_TEMPLATE, skip_if_exists=True):
    from bulk_downloader.db import db_init
    from bulk_downloader.migrations import apply_pending
    from bulk_downloader.runner import SiteRunner

    db_init()
    result = apply_pending(backup_first=False)
    assert result["errors"] == 0, result

    return SiteRunner(
        _SITE_ID,
        {
            "name": _SITE_NAME,
            "download_dir": str(download_dir),
            "filename_template": template,
            "skip_if_exists": skip_if_exists,
            # ffprobe/hash verification are separate contracts; this fixture is
            # about the pre-download decision and must not depend on whether
            # ffprobe exists on the host.
            "verify_integrity": False,
            "verify_hash": False,
            "use_http_dl": True,
            "learned": {
                "download": {
                    "row_selectors": ["a.download"],
                    "url_attribute": "href",
                }
            },
        },
    )


def _best(score=1080):
    return {
        "locator": _Locator("https://cdn.example.test/row545.mp4"),
        "text": f"Download {score}",
        "score": score,
        "size": 0,  # the >1MB advertised-size sanity check is a separate contract
        "_via_learned": True,
        "_learned_sel": "a.download",
        "_all_candidates": [],
    }


def _tier_name(score):
    """The name _TIERED_TEMPLATE renders for a candidate scoring `score`.

    Derived from the same `res_label` the transport uses rather than typed as a
    literal: `{resolution}` comes from `res_label(best["score"])`, NOT from the
    `res_lbl` argument, and a test that hard-coded "720p" would silently be
    asserting about a name the template never produces.
    """
    from bulk_downloader.runner_transport import res_label

    return f"{_SITE_NAME} {res_label(score)} tier.mp4"


def _run(runner, dl_dir, url=_URL, title=_TITLE, score=1080):
    from bulk_downloader.runner_transport import res_label

    runner._do_download(_FakePage(url, title), None, url, _best(score),
                        Path(dl_dir), res_label(score))


def _stop(runner):
    try:
        runner.stop()
        runner._stop_auto_retry()
    except Exception:
        pass


def _history():
    from bulk_downloader.db import db_conn

    with db_conn() as cx:
        return [dict(r) for r in cx.execute(
            "SELECT id, url, status, filename, file_size, message, "
            "bytes_fetched, library_id FROM history ORDER BY id").fetchall()]


def _library():
    from bulk_downloader.db import db_conn

    with db_conn() as cx:
        return [dict(r) for r in cx.execute(
            "SELECT id, file_path, title, history_id FROM library ORDER BY id"
        ).fetchall()]


def _mp4s(download_dir):
    return sorted(p.name for p in Path(download_dir).rglob("*.mp4"))


def _set_force(runner, url=_URL):
    """Stamp force_download the way Approve does, and PROVE it is stamped."""
    with runner._lock:
        job = runner.jobs.setdefault(url, {"url": url, "status": "pending"})
        job["force_download"] = True
    with runner._lock:
        assert runner.jobs[url].get("force_download") is True, runner.jobs.get(url)


def _forced(runner, url=_URL):
    with runner._lock:
        return bool(runner.jobs.get(url, {}).get("force_download"))


def _seed_unproven(download_dir, name=_COLLIDING_NAME, *, bytes_fetched=None):
    """An upgraded-host row: 'done', a file really on disk, no transfer proof.

    Returns the path. ``bytes_fetched=None`` is the pre-v8 NULL shape;
    ``bytes_fetched=0`` is the shape the skip arm itself writes.
    """
    from bulk_downloader.db import db_log

    path = Path(download_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PRIOR bytes of unknown provenance")
    db_log(_SITE_ID, _SITE_NAME, _URL, "done", path.name, path.stat().st_size,
           "", bytes_fetched=bytes_fetched, file_path=str(path))
    return path


def _assert_unproven(page_url, path):
    """PRECONDITION helper: the seeded state really is the 'unproven' state."""
    from bulk_downloader.db import db_skip_identity

    identity, owned = db_skip_identity(page_url, str(path))
    assert (identity, owned) == ("unproven", str(path)), (
        f"the fixture did not build the unproven state: "
        f"db_skip_identity -> {(identity, owned)!r}")


@pytest.fixture
def scene(clean_workdir):
    download_dir = clean_workdir / "downloads"
    download_dir.mkdir()
    made: list = []

    def _make(**kw):
        runner = _runner(download_dir, **kw)
        transport = _Transport()
        transport.install(runner)
        made.append(runner)
        return runner, transport, download_dir

    try:
        yield _make
    finally:
        for r in made:
            _stop(r)


# ── Row 545: the force flag ─────────────────────────────────────────────────

def test_row545_a_forced_redownload_is_not_eaten_by_the_skip_arm(scene):
    """RED at base f993f654: transfer count stays 1 and the flag is gone."""
    runner, transport, download_dir = scene()

    _run(runner, download_dir)

    # PRECONDITIONS: a real transfer happened, the file is on disk, and the
    # URL now PROVABLY owns it -- so the skip arm's "same" branch is the one
    # the forced run below will meet. Without this the test could pass for the
    # trivial reason that no skip was ever possible.
    assert len(transport.calls) == 1, transport.calls
    first_path = Path(transport.calls[0][1])
    assert first_path.name == _COLLIDING_NAME
    assert first_path.read_bytes() == _PAYLOAD
    from bulk_downloader.db import db_skip_identity
    assert db_skip_identity(_URL, str(first_path)) == ("same", str(first_path))
    assert [r["status"] for r in _history()] == ["done"]

    _set_force(runner)

    _run(runner, download_dir)

    assert len(transport.calls) == 2, (
        "the forced re-download never transferred: the skip arm consumed "
        f"force_download and reported 'Already have'. calls={transport.calls}")

    second_path = Path(transport.calls[1][1])
    assert second_path != first_path, (
        "the forced transfer overwrote the reserved name rather than taking "
        "its own; staging_claim.reserve is what decides this")
    assert second_path.read_bytes() == _PAYLOAD
    assert second_path.exists()

    rows = _history()
    assert len(rows) == 2, rows
    assert rows[1]["status"] == "done"
    assert rows[1]["message"] != "already on disk", rows[1]
    assert rows[1]["bytes_fetched"] == len(_PAYLOAD), (
        f"the forced row records no transfer: {rows[1]!r}")

    # The flag is consumed by the SUCCESS path (runner_transport ~1497), so a
    # later ordinary retry does not keep bypassing the check silently.
    assert _forced(runner) is False, (
        "force_download survived a successful forced download")


def test_row545_negative_control_an_unforced_rerun_still_skips(scene):
    """NEGATIVE CONTROL. The guard must not have been removed or widened.

    Same fixture, same second run, no force flag: the skip must still fire,
    transfer nothing, add no file, and report the file the URL owns.
    """
    runner, transport, download_dir = scene()

    _run(runner, download_dir)
    assert len(transport.calls) == 1, transport.calls
    assert _mp4s(download_dir) == [_COLLIDING_NAME]
    assert _forced(runner) is False, "precondition: no force flag is set"

    for _ in range(2):
        _run(runner, download_dir)

    assert len(transport.calls) == 1, (
        f"skip_if_exists no longer skips a proven same-work re-run: "
        f"{transport.calls}")
    assert _mp4s(download_dir) == [_COLLIDING_NAME]

    rows = _history()
    assert len(rows) == 3, rows
    assert [r["status"] for r in rows] == ["done", "done", "done"], rows
    assert [r["message"] for r in rows[1:]] == ["already on disk"] * 2, rows
    assert [r["bytes_fetched"] for r in rows[1:]] == [0, 0], rows


def test_row545_negative_control_force_does_not_disable_the_config_gate(scene):
    """NEGATIVE CONTROL. ``skip_if_exists`` still governs the skip.

    A fix that made the arm unconditional would pass the force test above.
    """
    runner, transport, download_dir = scene(skip_if_exists=False)

    _run(runner, download_dir)
    assert len(transport.calls) == 1, transport.calls
    assert _forced(runner) is False

    _run(runner, download_dir)

    assert len(transport.calls) == 2, (
        "skip_if_exists=False must never skip, forced or not: "
        f"{transport.calls}")


# ── Row 559: the diagnostic's denominator ───────────────────────────────────

def _needs_review(rows):
    return [r for r in rows if r["status"] == "needs_review"]


def test_row559_the_unproven_diagnostic_fires_when_skip_if_exists_is_off(scene):
    """RED at base f993f654: zero needs_review rows.

    ``skip_if_exists`` off means the identity call is skipped entirely, so the
    row-479 diagnostic cannot fire even though the unproven attribution is
    exactly what it was written to surface.
    """
    runner, transport, download_dir = scene(skip_if_exists=False)
    prior = _seed_unproven(download_dir)

    # PRECONDITIONS: the seeded state IS the unproven state, the file is on
    # disk, and the rendered path is that same file (so nothing but the config
    # gate can be the reason the diagnostic does or does not fire).
    _assert_unproven(_URL, prior)
    assert prior.exists() and prior.name == _COLLIDING_NAME
    assert runner.config.get("skip_if_exists") is False
    assert _needs_review(_history()) == [], _history()

    _run(runner, download_dir)

    flagged = _needs_review(_history())
    assert len(flagged) == 1, (
        "the unproven attribution was never made operator-visible: "
        f"history={_history()}")
    assert "records no transfer" in flagged[0]["message"], flagged[0]
    assert str(prior) in flagged[0]["message"], (
        f"the diagnostic must name the file it is about; got {flagged[0]!r}")
    assert flagged[0]["filename"] == prior.name, flagged[0]
    assert flagged[0]["bytes_fetched"] is None, flagged[0]

    # skip_if_exists is off, so the job goes on to download -- the diagnostic
    # annotates the run, it does not replace it.
    assert len(transport.calls) == 1, transport.calls


def test_row559_the_unproven_diagnostic_fires_when_the_template_moves(scene):
    """RED at base f993f654: zero needs_review rows.

    The second shape the row names: a tier or date change renders a name that
    is not on disk, so ``final_path.exists()`` is False and the identity -- which
    is keyed on the URL, not on the rendered path -- is never asked for.
    """
    runner, transport, download_dir = scene(template=_TIERED_TEMPLATE)
    prior = _seed_unproven(download_dir, name=_tier_name(1080))

    _assert_unproven(_URL, prior)
    # PRECONDITION: this run renders a DIFFERENT path, which does not exist.
    moved = download_dir / _tier_name(720)
    assert moved != prior and not moved.exists()
    assert runner.config.get("skip_if_exists") is True
    assert _needs_review(_history()) == [], _history()

    _run(runner, download_dir, score=720)

    # PRECONDITION, checked after the fact because it is the run that renders
    # it: the tier really did move the destination.
    assert len(transport.calls) == 1, transport.calls
    assert Path(transport.calls[0][1]) == moved, transport.calls

    flagged = _needs_review(_history())
    assert len(flagged) == 1, (
        "a tier change hid the unproven attribution: "
        f"history={_history()}")
    assert str(prior) in flagged[0]["message"], flagged[0]


def test_row559_negative_control_a_proven_transfer_raises_no_diagnostic(scene):
    """NEGATIVE CONTROL. The diagnostic must not fire for every job.

    Making the identity unconditional must not turn ordinary healthy runs into
    needs_review noise -- and it must not turn the "same" answer into a skip
    when ``skip_if_exists`` is off either.
    """
    runner, transport, download_dir = scene()

    _run(runner, download_dir)
    _run(runner, download_dir)
    _run(runner, download_dir)

    rows = _history()
    assert len(rows) == 3, rows
    assert _needs_review(rows) == [], (
        f"a proven same-work skip produced a needs_review row: {rows}")
    assert len(transport.calls) == 1, transport.calls


def test_row559_negative_control_a_fresh_url_raises_no_diagnostic(scene):
    """NEGATIVE CONTROL. An unknown identity is not a diagnostic.

    "unknown" (nothing attributes anything) must stay silent; only "unproven"
    -- a done row over a file that exists with no transfer recorded -- speaks.
    """
    runner, transport, download_dir = scene(skip_if_exists=False)
    stray = download_dir / _COLLIDING_NAME
    stray.write_bytes(b"a hand-copied file nothing in the db knows about")

    from bulk_downloader.db import db_skip_identity
    assert db_skip_identity(_URL, str(stray)) == ("unknown", None), (
        "precondition: the fixture must build the UNKNOWN state, not another")

    _run(runner, download_dir)

    rows = _history()
    assert _needs_review(rows) == [], (
        f"an unattributed file produced an unproven diagnostic: {rows}")
    assert len(transport.calls) == 1, transport.calls


def test_row559_negative_control_the_skip_still_needs_the_path_to_exist(scene):
    """NEGATIVE CONTROL for the change's blast radius.

    Measuring the identity unconditionally must NOT extend the skip to a
    rendered path that is not on disk. A proven-same URL whose template has
    moved must download to the new name, not report "Already have".
    """
    runner, transport, download_dir = scene(template=_TIERED_TEMPLATE)

    _run(runner, download_dir, score=1080)
    assert len(transport.calls) == 1, transport.calls
    first = Path(transport.calls[0][1])
    assert first.name == _tier_name(1080), transport.calls
    from bulk_downloader.db import db_skip_identity
    assert db_skip_identity(_URL, str(first)) == ("same", str(first))

    _run(runner, download_dir, score=720)

    assert len(transport.calls) == 2, (
        "the skip fired for a rendered path that does not exist: "
        f"{transport.calls}")
    assert Path(transport.calls[1][1]).name == _tier_name(720)


# ── Row 562: the diagnostic's own write ─────────────────────────────────────

class _LockedNeedsReview:
    """db_log stand-in that fails ONLY the needs_review write.

    Targeted rather than blanket so the done-path write still works: that is
    what lets the tests below prove the job completed after the diagnostic
    failed, instead of proving only that nothing was recorded at all.
    """

    def __init__(self, real):
        self._real = real
        self.raised = 0
        self.passed_through = 0

    def __call__(self, site_id, site_name, url, status, *a, **kw):
        if status == "needs_review":
            self.raised += 1
            raise sqlite3.OperationalError("database is locked")
        self.passed_through += 1
        return self._real(site_id, site_name, url, status, *a, **kw)


@pytest.fixture
def locked_needs_review(monkeypatch):
    from bulk_downloader import runner_transport as rt

    stub = _LockedNeedsReview(rt.db_log)
    monkeypatch.setattr(rt, "db_log", stub)
    return stub


def test_row562_a_locked_diagnostic_write_does_not_park_the_job(
    scene, locked_needs_review, caplog,
):
    """RED at base f993f654: sqlite3.OperationalError escapes _do_download.

    In production ``_process_one`` catches it as "worker error: database is
    locked" -- unclassified, parked 600s, no history row -- for a job that was
    about to download.
    """
    runner, transport, download_dir = scene()
    prior = _seed_unproven(download_dir, bytes_fetched=0)

    _assert_unproven(_URL, prior)
    assert prior.exists()
    assert locked_needs_review.raised == 0

    with caplog.at_level("WARNING"):
        _run(runner, download_dir)

    # PRECONDITION: the raising write really did fire. Without this the test
    # would pass on a tree where the diagnostic was simply deleted.
    assert locked_needs_review.raised == 1, (
        "the needs_review write never happened, so nothing was proven about "
        "its failure mode")

    # The job it was annotating went on to download.
    assert len(transport.calls) == 1, (
        f"the diagnostic's failure cost the download: {transport.calls}")
    assert locked_needs_review.passed_through >= 1
    rows = _history()
    assert [r["status"] for r in rows] == ["done", "done"], rows
    assert rows[1]["bytes_fetched"] == len(_PAYLOAD), rows[1]

    # A7: a swallowed failure is a fail-open. The refusal must carry sqlite's
    # own words and name the step, so an operator can act on it.
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    matched = [m for m in warnings if "database is locked" in m]
    assert matched, (
        f"the locked write was swallowed without a diagnostic: {warnings}")
    assert any("needs_review" in m or "unproven" in m for m in matched), (
        f"the warning does not name the step that failed: {matched}")


def test_row562_negative_control_the_diagnostic_is_still_written(scene):
    """NEGATIVE CONTROL. The guard must not have been obtained by deleting
    the write, or by swallowing a success into silence."""
    runner, transport, download_dir = scene()
    prior = _seed_unproven(download_dir, bytes_fetched=0)
    _assert_unproven(_URL, prior)

    _run(runner, download_dir)

    flagged = _needs_review(_history())
    assert len(flagged) == 1, _history()
    assert "records no transfer" in flagged[0]["message"], flagged[0]


def test_row562_negative_control_other_db_failures_are_not_swallowed(
    scene, monkeypatch,
):
    """NEGATIVE CONTROL for the blast radius of the try/except.

    The guard must cover ONLY the diagnostic write. A failure of the terminal
    ``done`` write is a different claim -- the file moved, the record did not --
    and must still be visible rather than absorbed into the same handler.
    """
    from bulk_downloader import runner_transport as rt

    runner, transport, download_dir = scene()
    real = rt.db_log
    fired = {"n": 0}

    def _explode_on_done(site_id, site_name, url, status, *a, **kw):
        if status == "done":
            fired["n"] += 1
            raise sqlite3.OperationalError("database is locked")
        return real(site_id, site_name, url, status, *a, **kw)

    monkeypatch.setattr(rt, "db_log", _explode_on_done)

    with pytest.raises(sqlite3.OperationalError):
        _run(runner, download_dir)

    assert fired["n"] == 1, (
        "the done-path write never fired, so the blast radius is unmeasured")
    assert len(transport.calls) == 1, transport.calls
