"""deep-scheduler-cron D4-tail: update_schedule's duplicate guard ignored urls.

add_schedule dedups on (site_id, cadence_hours, label, urls) (D4), but
update_schedule refused any edit giving a site two enabled schedules at one
cadence, so a schedule with pinned urls could not move to a cadence a
sibling with DIFFERENT urls already uses. The update key is now
(site_id, cadence_hours, urls): the pinned-url move is allowed, a true
duplicate is still refused. Label stays out of the update key (F038 r2
invariant, test_f038_schedules_edit::test_r2_f1).
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"


def _fresh_db():
    from bulk_downloader.db import db_init
    db_init()


def _row(sid):
    from bulk_downloader import capture_schedules as cs
    return next(s for s in cs.list_schedules() if s["id"] == sid)


def test_d4tail_pinned_url_schedule_moves_to_different_url_siblings_cadence():
    _fresh_db()
    from bulk_downloader import capture_schedules as cs
    a = cs.add_schedule(site_id="d4tail-move", cadence_hours=24, urls=["https://a.example/1"])
    b = cs.add_schedule(site_id="d4tail-move", cadence_hours=48, urls=["https://b.example/2"])
    res = cs.update_schedule(b, cadence_hours=24)
    assert res == {"status": "ok"}, (
        f"DSC-D4-TAIL: pinned-url schedule refused a cadence only a different-url sibling uses: {res}")
    assert _row(b)["cadence_hours"] == 24 and _row(a)["cadence_hours"] == 24
    cs.remove_schedule(a); cs.remove_schedule(b)


def test_d4tail_true_duplicate_still_refused_on_update_control():
    _fresh_db()
    from bulk_downloader import capture_schedules as cs
    urls = ["https://x.example/1"]
    a = cs.add_schedule(site_id="d4tail-dup", cadence_hours=24, label="L", urls=urls)
    b = cs.add_schedule(site_id="d4tail-dup", cadence_hours=48, label="L", urls=urls)
    res = cs.update_schedule(b, cadence_hours=24)
    assert res == {"status": "duplicate", "existing_id": a}, f"DSC-D4-TAIL: true duplicate edit not refused: {res}"
    assert _row(b)["cadence_hours"] == 48, "DSC-D4-TAIL: refused edit still wrote"
    # Same urls, other label: still one enabled row per (site, cadence, urls) on update.
    c = cs.add_schedule(site_id="d4tail-dup", cadence_hours=12, label="other", urls=urls)
    res = cs.update_schedule(c, cadence_hours=24)
    assert res == {"status": "duplicate", "existing_id": a}, f"DSC-D4-TAIL: same-urls edit not refused: {res}"
    for sid in (a, b, c):
        cs.remove_schedule(sid)
