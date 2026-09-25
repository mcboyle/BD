"""History API limits must not turn a negative value into an unlimited query."""

BD_GATE_SCOPE = "module"


def test_history_rejects_negative_limit_in_both_response_shapes():
    import bulk_downloader.app as app_module
    from bulk_downloader.db import db_conn, db_init

    db_init()
    sid = "hunt-history-limit"
    with db_conn() as cx:
        for i in range(3):
            cx.execute(
                "INSERT INTO history(site_id, site_name, url, status) VALUES(?, ?, ?, ?)",
                (sid, "Hunt", f"https://example.invalid/{i}", "failed"),
            )

    client = app_module.app.test_client()
    valid = client.get(f"/api/history?site_id={sid}&limit=1")
    assert valid.status_code == 200
    assert len(valid.get_json()) == 1
    for pagination in ("", "&paginate=1"):
        invalid = client.get(f"/api/history?site_id={sid}&limit=-1{pagination}")
        assert invalid.status_code == 400
    # Lens: zero and non-integer limits are rejected on both shapes (a non-integer used to raise -> HTTP 500).
    for bad in ("0", "abc", "1.5"):
        for pagination in ("", "&paginate=1"):
            r = client.get(f"/api/history?site_id={sid}&limit={bad}{pagination}")
            assert r.status_code == 400, (bad, pagination, r.status_code)
    paged = client.get(f"/api/history?site_id={sid}&limit=2&paginate=1").get_json()
    assert len(paged["rows"]) == 2 and paged["next_cursor"] is not None
