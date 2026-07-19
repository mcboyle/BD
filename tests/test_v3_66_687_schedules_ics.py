"""v3.66.687 (F7) — calendar / ICS export of scheduled capture jobs.

Recurring capture schedules (capture_schedules) fire every `cadence_hours`.
This exposes them as a subscribable iCalendar feed: schedules_to_ics builds
an RFC-5545 VCALENDAR with one recurring VEVENT per schedule (RRULE
FREQ=HOURLY;INTERVAL=cadence_hours, DTSTART=next_run_ts), and
GET /api/schedules/export.ics serves it as text/calendar. Pure, no new
dependency (icalendar is NOT required) -- string generation with correct
CRLF folding + TEXT escaping.
"""
import pytest

from bulk_downloader import capture_schedules as cs


def _sched(**kw):
    base = {"id": 1, "site_id": "acme", "label": "Nightly",
            "cadence_hours": 24, "next_run_ts": 1_700_000_000, "enabled": 1}
    base.update(kw)
    return base


# ── schedules_to_ics: pure generator ────────────────────────────────

def test_empty_list_is_valid_empty_vcalendar():
    ics = cs.schedules_to_ics([], now=1_700_000_000)
    assert ics.startswith("BEGIN:VCALENDAR")
    assert "VERSION:2.0" in ics
    assert ics.rstrip().endswith("END:VCALENDAR")
    assert "BEGIN:VEVENT" not in ics


def test_one_schedule_becomes_a_recurring_vevent():
    ics = cs.schedules_to_ics([_sched()], now=1_700_000_000)
    assert "BEGIN:VEVENT" in ics and "END:VEVENT" in ics
    assert "UID:bd-schedule-1@bulkdownloader" in ics
    assert "RRULE:FREQ=HOURLY;INTERVAL=24" in ics
    assert "SUMMARY:Nightly" in ics
    # DTSTART is the next_run_ts rendered as a UTC timestamp
    assert "DTSTART:20231114T221320Z" in ics


def test_summary_falls_back_to_site_id():
    ics = cs.schedules_to_ics([_sched(label="")], now=1_700_000_000)
    assert "SUMMARY:Capture: acme" in ics


def test_text_fields_are_escaped():
    ics = cs.schedules_to_ics(
        [_sched(label="a,b; c\nd")], now=1_700_000_000)
    # comma/semicolon/newline escaped per RFC 5545 TEXT rules
    assert r"SUMMARY:a\,b\; c\nd" in ics


def test_uses_crlf_line_endings():
    ics = cs.schedules_to_ics([_sched()], now=1_700_000_000)
    assert "\r\n" in ics
    # every physical line ends in CRLF (no bare LF)
    assert "\n" in ics and "\r\n" == ics[ics.index("\n") - 1:ics.index("\n") + 1]


def test_skips_nonpositive_cadence():
    ics = cs.schedules_to_ics(
        [_sched(id=1, cadence_hours=0), _sched(id=2, cadence_hours=24)],
        now=1_700_000_000)
    assert "bd-schedule-1@" not in ics
    assert "bd-schedule-2@" in ics


def test_disabled_schedule_is_marked_cancelled():
    ics = cs.schedules_to_ics([_sched(enabled=0)], now=1_700_000_000)
    assert "STATUS:CANCELLED" in ics
    ics2 = cs.schedules_to_ics([_sched(enabled=1)], now=1_700_000_000)
    assert "STATUS:CONFIRMED" in ics2


# ── the export route ────────────────────────────────────────────────

def test_export_ics_route(fresh_app):
    from bulk_downloader import capture_schedules as _cs
    _cs.add_schedule(site_id="acme", cadence_hours=12, label="Twice daily")
    r = fresh_app.get("/api/schedules/export.ics")
    assert r.status_code == 200
    assert r.mimetype == "text/calendar"
    assert "attachment" in r.headers.get("Content-Disposition", "")
    body = r.get_data(as_text=True)
    assert body.startswith("BEGIN:VCALENDAR")
    assert "BEGIN:VEVENT" in body
    assert "RRULE:FREQ=HOURLY;INTERVAL=12" in body
