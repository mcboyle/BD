"""ITEM B -- pin the /api/sites/<sid>/queue `ts` <-> `since` round trip.

The api_queue handler in app_sites_queue.py returns

    "ts": r.get("ts_updated","") or r.get("ts_added",""),

a RAW UTC stamp straight from the queue table. That looks inconsistent next to
the in-memory job shape, which fills the same "ts" key with a LOCAL HH:MM:SS
via runner_util._ts(). The reflexive "fix" -- convert the queue-row `ts` to
local time so both shapes match -- is wrong, because this field is not merely
a display value here: it is the delta-poll cursor for the endpoint's own
`since` query param, and `db.py:1792` (queue_changed_since) compares it
directly against the UTC `ts_updated` column with a bare `>`:

    "SELECT * FROM queue WHERE site_id=? AND ts_updated > ? ..."

A cursor has to live on the same clock as the column it filters, or the
comparison is between two different clocks wearing the same string shape.
runner_util._utc_iso_to_local_iso (the function that already exists for the
analogous `ts_iso` field, see runner_util.py:81-116) documents exactly this
trap at its own docstring: "db.py:1792 uses it as a monotonic cursor... so
stamping 'localtime' would give the column mixed semantics."

THIS FILE DOES NOT CHANGE THE PRODUCTION VALUE. It pins the current (raw UTC,
unconverted) contract with a round trip through the real endpoint, forced
under one west-of-UTC and one east-of-UTC zone -- this box is UTC (CLAUDE.md
section 7), so an unforced test would pass or fail identically regardless of
whether the code was TZ-aware at all, proving nothing.

RED proof (see test_reflexive_localize_fix_breaks_the_round_trip below, and
the method note in the class docstring): substituting the LOCALIZED cursor
for the raw one -- i.e. exactly what the reflexive fix would produce, using
runner_util's own conversion helper -- makes the round trip return the wrong
row set in BOTH directions:
  * west-of-UTC (America/Los_Angeles, UTC-8 in January): the localized cursor
    sorts as an EARLIER date, so `ts_updated > cursor` becomes true for every
    row, not just the newer ones -- the poll returns rows it already sent.
  * east-of-UTC (Asia/Tokyo, UTC+9): the localized cursor sorts as a LATER
    time-of-day, so `ts_updated > cursor` becomes false for every row -- the
    poll silently returns nothing, even though new rows exist.
This was verified live (see report) by swapping the raw cursor for
`_utc_iso_to_local_iso(raw_cursor)` in the pin test itself and observing the
exact-match assertion fail in both zones before restoring the raw cursor.

COMPLETENESS NOTE, stated here rather than assumed: an AST-informed grep for
`since=` across frontend/src, tools/*.py and toolchain/bin found ZERO callers
of this endpoint with a `since` parameter. The one in-repo consumer,
tools/live_seed.py:586, GETs the endpoint WITHOUT `since` and reads only
status/message/filename -- never `ts`. So the contract pinned here currently
has NO LIVE CALLER; the breakage the reflexive fix would cause is real but
latent, not observed in production traffic today. Pinning it now stops it
from becoming a live incident the day a `since`-based poller is added.
"""
from __future__ import annotations

import os
import time

import pytest

# Two zones with opposite UTC offsets in January (no DST ambiguity for either
# at that date), per CLAUDE.md's requirement to force both directions since
# this box's own clock is UTC and cannot reproduce either failure mode.
_WEST_OF_UTC = "America/Los_Angeles"   # UTC-8 in January
_EAST_OF_UTC = "Asia/Tokyo"            # UTC+9, no DST at all

_SITE_PREFIX = "cutB_ts_since"

# Fixed, hand-picked UTC timestamps -- not wall-clock-derived, so the test is
# deterministic and immune to execution-time flakiness. Format matches
# db.py's own strftime('%Y-%m-%dT%H:%M:%S','now') stamp exactly.
_ROW1 = ("https://example.invalid/cutB/1", "2026-01-01T00:00:00")
_ROW2 = ("https://example.invalid/cutB/2", "2026-01-01T00:00:05")
_ROW3 = ("https://example.invalid/cutB/3", "2026-01-01T00:00:10")

# The exact (wrong) row set each zone's LOCALIZED cursor is expected to
# produce -- derived from row2's raw UTC stamp (2026-01-01T00:00:05)
# converted with runner_util._utc_iso_to_local_iso under each forced TZ:
# America/Los_Angeles (UTC-8, January) -> 2025-12-31T16:00:05, which sorts
# EARLIER than all three seeded rows, so `ts_updated > cursor` is true for
# every row (over-return); Asia/Tokyo (UTC+9) -> 2026-01-01T09:00:05, which
# sorts LATER than all three, so the comparison is false for every row
# (silent drop). Verified live with runner_util._utc_iso_to_local_iso
# directly against these fixed timestamps before being hardcoded here.
_EXPECTED_BROKEN_ROUND_TRIP = {
    _WEST_OF_UTC: {_ROW1[0], _ROW2[0], _ROW3[0]},
    _EAST_OF_UTC: set(),
}


@pytest.fixture
def runner_db(clean_workdir):
    """Isolated BD home with the queue table created, per test_cut41's
    runner_db fixture (tests/test_cut41_ts_iso_producers.py:103)."""
    from bulk_downloader.db import db_init
    db_init()
    return clean_workdir


def _register(sid):
    """Register a minimal site so `_app_runners()`'s membership check
    (app_sites_queue.py:858) passes. api_queue never touches the runner
    object itself -- only `sid not in runners` -- so a bare placeholder
    is enough; verified by reading app_sites_queue.py:857-870."""
    from bulk_downloader import app_state as st
    st.s_cfg[sid] = {"name": sid}
    st.runners[sid] = object()

    def _cleanup():
        st.runners.pop(sid, None)
        st.s_cfg.pop(sid, None)
    return _cleanup


def _seed_rows(sid):
    """Insert the three fixed rows directly, bypassing queue_upsert's
    strftime('now') stamp so ts_updated is exactly what this test says it
    is, not whatever the wall clock happens to read."""
    from bulk_downloader.db import db_conn
    with db_conn() as cx:
        for url, ts in (_ROW1, _ROW2, _ROW3):
            cx.execute(
                "INSERT INTO queue(site_id, url, status, ts_added, ts_updated) "
                "VALUES (?, ?, 'done', ?, ?)",
                (sid, url, ts, ts))


def _get_queue(path):
    from bulk_downloader import app as a
    return a.app.test_client().get(path)


class _ForcedTZ:
    """Context manager: force TZ for the duration of the block, restore after.

    Plain contextlib rather than monkeypatch.setenv because this needs to
    call time.tzset() on both entry AND exit -- monkeypatch's teardown
    restores the env var but does not itself re-run tzset()."""

    def __init__(self, tz):
        self.tz = tz
        self._orig = None

    def __enter__(self):
        self._orig = os.environ.get("TZ")
        os.environ["TZ"] = self.tz
        time.tzset()
        return self

    def __exit__(self, *exc):
        if self._orig is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._orig
        time.tzset()
        return False


@pytest.mark.parametrize("tz", [_WEST_OF_UTC, _EAST_OF_UTC])
def test_since_round_trip_pins_the_raw_utc_cursor(tz, runner_db):
    """THE PIN. Under a forced non-UTC zone in both directions: the `ts`
    the endpoint returns must be the raw ts_updated value (no local
    conversion), and feeding that value straight back as `since` must
    return exactly the rows strictly newer than it -- matching
    queue_changed_since's `ts_updated > ts_since` (db.py:1792).
    """
    sid = f"{_SITE_PREFIX}_{tz.replace('/', '_')}"
    cleanup = _register(sid)
    try:
        _seed_rows(sid)
        with _ForcedTZ(tz):
            body = _get_queue(f"/api/sites/{sid}/queue").get_json()
            rows = {r["url"]: r["ts"] for r in body["rows"]}
            assert set(rows) == {_ROW1[0], _ROW2[0], _ROW3[0]}, (
                f"expected all 3 seeded rows, got {rows!r}")

            # PIN: the returned "ts" is the raw stored value, byte for byte --
            # no local-time conversion happened, under TZ={tz}.
            assert rows[_ROW2[0]] == _ROW2[1], (
                f"under TZ={tz}, api_queue returned ts={rows[_ROW2[0]]!r} for "
                f"row2, expected the raw stored ts_updated={_ROW2[1]!r} "
                f"unchanged. The api_queue handler in app_sites_queue.py must "
                f"not localize this field -- it is the since-cursor, and "
                f"db.py:1792 compares it against the UTC ts_updated column.")

            cursor = rows[_ROW2[0]]
            body2 = _get_queue(
                f"/api/sites/{sid}/queue?since={cursor}").get_json()
            got = {r["url"] for r in body2["rows"]}
            assert got == {_ROW3[0]}, (
                f"under TZ={tz}, since={cursor!r} (fed back verbatim from "
                f"the endpoint's own ts) returned {got!r}, expected only "
                f"{{{_ROW3[0]!r}}} (the one row strictly newer than row2). "
                f"The round trip is broken.")
    finally:
        cleanup()


@pytest.mark.parametrize("tz", [_WEST_OF_UTC, _EAST_OF_UTC])
def test_reflexive_localize_fix_breaks_the_round_trip(tz, runner_db):
    """RED-PROOF CHARACTERIZATION, kept as a permanent regression guard.

    Simulates the reflexive "fix" this cut declines to make: convert the
    cursor to local time before using it, exactly as
    runner_util._utc_iso_to_local_iso already does for the unrelated
    `ts_iso` field. Demonstrates the two failure modes CLAUDE.md's item
    description names ("broke the round trip in BOTH TZ directions"):

      * west-of-UTC: the localized cursor sorts EARLIER than the true UTC
        cursor, so `ts_updated > cursor` now matches rows that were already
        sent -- the poll over-returns.
      * east-of-UTC: the localized cursor sorts LATER, so `ts_updated >
        cursor` matches nothing -- the poll silently drops new rows.

    If this test ever starts passing its "must NOT equal the correct
    round trip" assertion turning into an equality (i.e. localizing stops
    being harmful), the guarding comment on the api_queue handler in
    app_sites_queue.py is stale and should be re-examined -- it would mean
    this box's SQLite build or Python's tzset semantics changed underneath
    the comparison.
    """
    from bulk_downloader.runner_util import _utc_iso_to_local_iso

    sid = f"{_SITE_PREFIX}_bad_{tz.replace('/', '_')}"
    cleanup = _register(sid)
    try:
        _seed_rows(sid)
        with _ForcedTZ(tz):
            raw_cursor = _ROW2[1]
            localized_cursor = _utc_iso_to_local_iso(raw_cursor)
            assert localized_cursor != raw_cursor, (
                f"TZ={tz} produced a no-op conversion ({localized_cursor!r} "
                f"== {raw_cursor!r}); this zone cannot demonstrate the "
                f"defect and the parametrize list should be revisited")

            # PRECONDITION, and it is load-bearing for the EAST-of-UTC row of
            # _EXPECTED_BROKEN_ROUND_TRIP specifically. That row's expected
            # value is the EMPTY SET (the silent-drop failure mode), so
            # `got == expected_broken` is satisfied by "the endpoint returned
            # nothing because the defect fired" AND, identically, by "the
            # endpoint returned nothing because no rows were ever seeded" --
            # CLAUDE.md section 0's canonical instance, an assertion that
            # cannot see its own subject. Measured, not theorised: with
            # _seed_rows() no-op'd, the America/Los_Angeles parametrization
            # fails (its expected set is non-empty) while Asia/Tokyo PASSES.
            # Prove the rows exist through the same endpoint first, so the
            # empty result below can only mean the cursor filtered them out.
            seeded = {r["url"] for r in
                      _get_queue(f"/api/sites/{sid}/queue").get_json()["rows"]}
            assert seeded == {_ROW1[0], _ROW2[0], _ROW3[0]}, (
                f"TZ={tz}: precondition failed -- the unfiltered endpoint "
                f"returned {seeded!r}, not the 3 seeded rows. Without this "
                f"the empty-set expectation below cannot distinguish 'the "
                f"localized cursor dropped every row' from 'nothing was ever "
                f"inserted'.")

            # SECOND control, same reason, one level in: prove the `since`
            # branch itself discriminates on this data before attributing an
            # empty result to the cursor's VALUE. Measured: with the handler's
            # `rows = queue_changed_since(...)` replaced by `rows = []`, the
            # unfiltered precondition above still passes (it takes the
            # no-`since` branch) and Asia/Tokyo's empty expectation is again
            # satisfied for the wrong reason. With this control the only
            # variable left between the two calls is the cursor string.
            control = {r["url"] for r in _get_queue(
                f"/api/sites/{sid}/queue?since={raw_cursor}"
            ).get_json()["rows"]}
            assert control == {_ROW3[0]}, (
                f"TZ={tz}: control failed -- the RAW UTC cursor "
                f"{raw_cursor!r} returned {control!r}, not {{{_ROW3[0]!r}}}. "
                f"The since branch is broken independently of localization, "
                f"so the comparison below would attribute its result to the "
                f"wrong cause.")

            body = _get_queue(
                f"/api/sites/{sid}/queue?since={localized_cursor}"
            ).get_json()
            got = {r["url"] for r in body["rows"]}

            # POSITIVE pin, not just "!= the correct answer" -- an assertion
            # of the shape `got != {_ROW3[0]}` PASSES on an empty result set
            # (e.g. a no-op'd seeder returning zero rows), which proves
            # nothing about breakage; see CLAUDE.md section 0. Pin the exact
            # (wrong) row set each zone is expected to produce instead, per
            # the direction analysis in this test's docstring: west-of-UTC
            # localizes the cursor EARLIER than every seeded row (so the
            # poll over-returns all three); east-of-UTC localizes it LATER
            # than every seeded row (so the poll returns none).
            expected_broken = _EXPECTED_BROKEN_ROUND_TRIP[tz]
            assert got == expected_broken, (
                f"TZ={tz}: localizing the cursor (as the reflexive fix "
                f"would) was expected to produce the broken result "
                f"{expected_broken!r} -- instead got {got!r}. Either the "
                f"defect this comment warns about no longer reproduces, or "
                f"this test stopped exercising it (an empty `got` here "
                f"could mean the seeder silently inserted nothing rather "
                f"than the endpoint behaving correctly); investigate before "
                f"trusting the guarding comment on the api_queue handler in "
                f"app_sites_queue.py.")
            assert got != {_ROW3[0]}, (
                f"TZ={tz}: got the CORRECT round-trip result "
                f"{{{_ROW3[0]!r}}} despite feeding in the localized cursor "
                f"-- the defect this test guards against appears to be "
                f"fixed, or this zone no longer demonstrates it.")
    finally:
        cleanup()
