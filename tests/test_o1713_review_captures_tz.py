import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from tools import bulk_review_captures as review

BD_GATE_SCOPE = "module"


def _entry(name, timestamp, selector):
    return {
        "cand": {
            "host": "captures.example.test",
            "status": "review_ready",
            "selectors": {"player": {"container": selector}},
            "source": {"capture_file": name, "captured_at": timestamp},
            "review_notes": [],
        },
        "n": 1,
        "src": Path(name),
    }


def test_offsetless_timestamp_is_utc():
    parsed = review._parse_dt("2026-06-03T00:00:00")
    assert parsed == datetime(2026, 6, 3, tzinfo=timezone.utc), "NAIVE-UTC"
    assert parsed.tzinfo is timezone.utc


@pytest.mark.parametrize("timestamp", ["2026-06-02T23:00:00+00:00",
                                        "2026-06-03T01:00:00+02:00",
                                        "2026-06-02T23:00:00"])
def test_merge_orders_naive_timestamps_and_aware_floor(timestamp):
    entries = [
        _entry("older.wacz", timestamp, "#older"),
        _entry("newest.wacz", "2026-06-03T00:00:00", "#newest"),
        _entry("unknown.wacz", "invalid", "#older"),
    ]
    assert len(entries) == 3
    count, merged = review._merge_host_candidates(entries, 30)
    assert count == 1
    assert [c["capture_file"] for c in merged["merged_from"]] == [
        "newest.wacz", "older.wacz", "unknown.wacz",
    ], "NAIVE-AWARE-SORT"
    assert merged["source"]["capture_file"] == "newest.wacz"
    assert merged["selectors"]["player"]["container"] == "#older"
    assert merged["merge_stats"]["contributors"] == 3
    assert merged["merge_alternatives"][0]["kept_votes"] == 2


def test_aware_only_merge_negative_control():
    entries = [
        _entry("older.wacz", "2026-06-03T00:30:00+02:00", "#older"),
        _entry("newest.wacz", "2026-06-02T23:00:00+00:00", "#newest"),
    ]
    count, merged = review._merge_host_candidates(entries, 30)
    assert count == 1
    assert [c["capture_file"] for c in merged["merged_from"]] == [
        "newest.wacz", "older.wacz",
    ]
    assert merged["selectors"]["player"]["container"] == "#newest"
    assert merged["merge_stats"]["contributors"] == 2
    payload = json.dumps((count, merged), sort_keys=True).encode()
    print("AWARE-CONTROL-SHA256=" + hashlib.sha256(payload).hexdigest())


@pytest.mark.parametrize("timestamp", ["2026-06-03T00:00:00Z",
                                        "2026-06-03T00:00:00+00:00",
                                        "2026-06-03T00:00:00+05:30"])
def test_aware_datetime_preserves_offset(timestamp):
    parsed = review._parse_dt(timestamp)
    expected = datetime.fromisoformat(timestamp)
    assert parsed == expected
    assert parsed.utcoffset() == expected.utcoffset()
    if timestamp.endswith("+05:30"):
        assert parsed.utcoffset() == timedelta(hours=5, minutes=30)


@pytest.mark.parametrize("timestamp", ["", "invalid", None])
def test_invalid_timestamp_retains_none_fallback(timestamp):
    assert review._parse_dt(timestamp) is None
