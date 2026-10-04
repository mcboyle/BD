"""O1826 C18 -- scheduling: corrupt holidays.json, silent heatmap failure, active_window.

M141. ``list_holidays`` returned [] when holidays.json failed to parse, so
``add_holiday`` rewrote a damaged but recoverable calendar as a single entry.
A missing file is still []; an unreadable one now raises HolidaysFileError,
and add_holiday / remove_holiday refuse (False) without touching the file.

M140. ``hour_heatmap`` swallowed a failed history query and returned all
zeros, indistinguishable from "no history". It now carries ``error``.

M142. ``next_run_safe_time`` promised an active_window gate and never checked
one; it now only returns a time inside the configured window.
"""
from __future__ import annotations

import contextlib
import datetime
import importlib
import json

import pytest

from bulk_downloader import scheduling as sc

BD_GATE_SCOPE = "module"

_TRUNCATED = '[{"date": "2026-12-25", "label": "Christmas"},\n {"date": "2026-'


@pytest.fixture
def hpath(tmp_path, monkeypatch):
    p = tmp_path / "holidays.json"
    monkeypatch.setattr(sc, "_holidays_path", lambda: p)
    return p


# ─── M141 holidays ────────────────────────────────────────────────────

def test_add_holiday_refuses_corrupt_file_and_leaves_it(hpath):
    hpath.write_text(_TRUNCATED, encoding="utf-8")
    before = hpath.read_bytes()
    ok = sc.add_holiday("2026-01-01", "New Year")
    after = hpath.read_bytes()
    assert (ok, after) == (False, before), (
        "O1826C18-M141: add_holiday on a corrupt holidays.json returned %r "
        "and rewrote it as %r" % (ok, after.decode("utf-8", "replace")))


def test_list_holidays_raises_on_corrupt_file(hpath):
    hpath.write_text(_TRUNCATED, encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        sc.list_holidays()
    assert type(ei.value).__name__ == "HolidaysFileError"


def test_list_holidays_raises_on_non_list_json(hpath):
    hpath.write_text(json.dumps({"date": "2026-12-25"}), encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        sc.list_holidays()
    assert type(ei.value).__name__ == "HolidaysFileError"


def test_remove_holiday_refuses_corrupt_file(hpath):
    hpath.write_text(_TRUNCATED, encoding="utf-8")
    before = hpath.read_bytes()
    assert sc.remove_holiday("2026-12-25") is False
    assert hpath.read_bytes() == before


def test_missing_file_is_empty_and_add_creates_it(hpath):
    assert sc.list_holidays() == []
    assert sc.add_holiday("2026-01-01", "New Year") is True
    assert json.loads(hpath.read_text(encoding="utf-8")) == [
        {"date": "2026-01-01", "label": "New Year"}]


def test_add_holiday_appends_to_valid_file(hpath):
    orig = [{"date": "2026-12-25", "label": "Christmas"},
            {"date": "2026-07-04", "label": "Fourth"}]
    hpath.write_text(json.dumps(orig), encoding="utf-8")
    assert sc.add_holiday("2026-01-01", "New Year") is True
    assert sc.list_holidays() == orig + [
        {"date": "2026-01-01", "label": "New Year"}]


# ─── M140 heatmap ─────────────────────────────────────────────────────

def _db():
    return importlib.import_module("bulk_downloader.db")


def test_heatmap_reports_db_failure(monkeypatch):
    def boom():
        raise RuntimeError("o1826c18: history unreadable")
    monkeypatch.setattr(_db(), "db_conn", boom)
    heat = sc.hour_heatmap("site-x")
    assert heat.get("error") == "RuntimeError: o1826c18: history unreadable", (
        "O1826C18-M140: history query raised, heatmap reported no error: %r"
        % {k: v for k, v in heat.items() if k != "hours"})
    assert [h["total"] for h in heat["hours"]] == [0] * 24


def test_heatmap_success_has_no_error(monkeypatch):
    class _Cx:
        def execute(self, *_a):
            return self

        def fetchall(self):
            return [("done", "2026-06-15T03:10:00"),
                    ("failed", "2026-06-15T03:40:00")]

    @contextlib.contextmanager
    def conn():
        yield _Cx()
    monkeypatch.setattr(_db(), "db_conn", conn)
    heat = sc.hour_heatmap("site-x")
    assert heat["error"] is None
    assert heat["hours"][3] == {"hour": 3, "done": 1, "failed": 1,
                                "total": 2, "success_rate_pct": 50.0}


# ─── M142 active_window ───────────────────────────────────────────────

def _ts(day, hh, mm=0):
    return datetime.datetime(2026, 6, day, hh, mm).timestamp()


def _hm(ts):
    d = datetime.datetime.fromtimestamp(ts)
    return (d.day, d.hour, d.minute)


def test_next_run_lands_inside_active_window(hpath):
    got = sc.next_run_safe_time({"active_window": "01:00-02:00"},
                                now=_ts(15, 12))
    assert got is not None and _hm(got) == (16, 1, 0), (
        "O1826C18-M142: active_window 01:00-02:00 from 12:00 returned %r"
        % (_hm(got) if got is not None else None,))


def test_next_run_active_window_wraps_and_list_form(hpath):
    got = sc.next_run_safe_time({"active_window": ["bad", "23:00-01:00"]},
                                now=_ts(15, 12))
    assert _hm(got) == (15, 23, 0)
    got = sc.next_run_safe_time({"active_window": "23:00-01:00"},
                                now=_ts(15, 23, 30))
    assert _hm(got) == (15, 23, 35)


def test_next_run_two_disjoint_windows_lands_in_either(hpath):
    cfg = {"active_window": ["01:00-02:00", "03:00-04:00"]}
    got = [sc.next_run_safe_time(cfg, now=_ts(15, 12)),
           sc.next_run_safe_time(cfg, now=_ts(15, 2, 30))]
    assert [_hm(g) if g is not None else None for g in got] == [
        (16, 1, 0), (15, 3, 0)], (
        "O1826C18-M142-ANY: two disjoint active windows gave %r"
        % ([_hm(g) if g is not None else None for g in got],))


def test_next_run_active_window_end_is_exclusive(hpath):
    got = sc.next_run_safe_time({"active_window": "01:00-02:00"},
                                now=_ts(15, 1, 30), earliest_minutes_ahead=30)
    assert got is not None and _hm(got) == (16, 1, 0), (
        "O1826C18-M142-END: first slot at window end 02:00 returned %r"
        % (_hm(got) if got is not None else None,))
    got = sc.next_run_safe_time({"active_window": "01:00-02:00"},
                                now=_ts(15, 1, 30), earliest_minutes_ahead=29)
    assert _hm(got) == (15, 1, 59)


def test_next_run_active_window_composes_with_quiet_hours(hpath):
    cfg = {"active_window": "01:00-02:00",
           "quiet_hours_enabled": True, "quiet_hours": "01:00-01:30"}
    assert _hm(sc.next_run_safe_time(cfg, now=_ts(15, 12))) == (16, 1, 30)


def test_next_run_without_active_window_unchanged(hpath):
    now = _ts(15, 12)
    assert sc.next_run_safe_time({}, now=now) == now + 300
    assert sc.next_run_safe_time({"active_window": "garbage"},
                                 now=now) == now + 300
