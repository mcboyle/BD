"""Cut 8 write surface: global daily byte budget (cross-site cap key).

`daily_budget` is per-site only; this adds a global counterpart. The write
surface extends the existing POST /api/global_config with one optional key,
`global_daily_byte_budget` (int bytes, 0 = uncapped). It is enforced at the
SAME worker seam as the per-site budget (runner pauses when over). The read
helper sums today's bytes across all sites.

RED on pristine 379: daily_budget has no global helpers; the config key is
neither accepted nor enforced.
"""
from __future__ import annotations


def _new_client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, csrf


# ── store / helper layer ──────────────────────────────────────────────

def test_bytes_today_all_sums_sites():
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader import daily_budget as db
    db.record_site_bytes("siteA", 1000)
    db.record_site_bytes("siteB", 2500)
    assert db.bytes_today_all() >= 3500


def test_global_budget_getset_roundtrip():
    from bulk_downloader import daily_budget as db
    db.set_global_budget(5_000_000)
    assert db.get_global_budget() == 5_000_000
    db.set_global_budget(0)  # 0 = uncapped
    assert db.get_global_budget() == 0


def test_is_over_global_budget_shape_and_logic():
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader import daily_budget as db
    # uncapped -> never over
    out = db.is_over_global_budget(global_budget=0)
    assert out["over"] is False
    assert out["budget_bytes"] == 0
    # capped below usage -> over
    db.record_site_bytes("siteC", 10_000)
    out = db.is_over_global_budget(global_budget=1)
    assert out["over"] is True
    assert set(("over", "used_bytes", "budget_bytes")).issubset(out.keys())


# ── write surface: global_config POST ─────────────────────────────────

def test_post_global_config_accepts_cap_key():
    c, csrf = _new_client()
    r = c.post("/api/global_config",
               json={"global_daily_byte_budget": 7_000_000},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.get_json()
    g = c.get("/api/global_config").get_json()
    assert int(g.get("global_daily_byte_budget", 0)) == 7_000_000


def test_post_global_config_clamps_negative_to_zero():
    c, csrf = _new_client()
    c.post("/api/global_config",
           json={"global_daily_byte_budget": -50},
           headers={"X-CSRF-Token": csrf})
    g = c.get("/api/global_config").get_json()
    assert int(g.get("global_daily_byte_budget", -1)) == 0


def test_post_global_config_rejects_non_int():
    c, csrf = _new_client()
    r = c.post("/api/global_config",
               json={"global_daily_byte_budget": "lots"},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_setting_cap_key_wires_into_daily_budget_module():
    """POSTing the key must propagate to the enforcement module's state
    (mirrors how global_max_concurrent calls set_global_concurrent_cap)."""
    c, csrf = _new_client()
    c.post("/api/global_config",
           json={"global_daily_byte_budget": 12_345_678},
           headers={"X-CSRF-Token": csrf})
    from bulk_downloader import daily_budget as db
    assert db.get_global_budget() == 12_345_678
