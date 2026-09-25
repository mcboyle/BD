"""A scheduled export must preserve every successful run in a shared folder."""

import json

from bulk_downloader import exports, scheduled_exports

BD_GATE_SCOPE = "module"


def test_same_second_schedules_keep_both_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduled_exports.time, "strftime", lambda _fmt: "20260925-120000")
    monkeypatch.setattr(
        exports, "to_csv", lambda filter_dict, **_kwargs: filter_dict["site_id"].encode()
    )
    schedules = [
        {
            "id": index,
            "format": "csv",
            "destination": str(tmp_path),
            "filter_json": json.dumps({"site_id": site_id}),
        }
        for index, site_id in ((1, "first"), (2, "second"))
    ]

    results = [scheduled_exports._run_one(schedule) for schedule in schedules]

    assert all(result["ok"] for result in results)
    assert results[0]["file"] != results[1]["file"]
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {b"first", b"second"}


def test_retention_keeps_other_schedule_in_shared_folder(tmp_path, monkeypatch):
    timestamps = iter(("20260925-120000", "20260925-120001"))
    monkeypatch.setattr(scheduled_exports.time, "strftime", lambda _fmt: next(timestamps))
    monkeypatch.setattr(
        exports, "to_csv", lambda filter_dict, **_kwargs: filter_dict["site_id"].encode()
    )
    first = {
        "id": 1,
        "format": "csv",
        "destination": str(tmp_path),
        "filter_json": '{"site_id":"first"}',
        "retention_count": 1,
    }
    second = {**first, "id": 2, "filter_json": '{"site_id":"second"}'}

    first_result = scheduled_exports._run_one(first)
    second_result = scheduled_exports._run_one(second)
    assert first_result["ok"] and second_result["ok"]
    assert len(list(tmp_path.iterdir())) == 2

    scheduled_exports._apply_retention(first)

    assert len(list(tmp_path.iterdir())) == 2
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {b"first", b"second"}
