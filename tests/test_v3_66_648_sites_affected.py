"""v3.66.648 -- S2.3 (part): extractor -> sites-affected blast-radius map.

app_ytdlp_status gains a read-only /api/ytdlp/sites_affected that maps each
extractor backend (aylo / ytdlp / gallerydl / search / playwright-default) to the
configured sites that depend on it -- so when an extractor breaks or goes stale,
the affected sites are visible at a glance. Pure, config-derived, read-only.

Sandbox-safe: pure helper + a minimal Flask app + a seeded app_state.s_cfg.
"""
from __future__ import annotations

import bulk_downloader.app_ytdlp_status as ys


def test_extractor_site_map_classifies_each_backend():
    s_cfg = {
        "a": {"name": "A", "use_aylo_extractor": True},
        "b": {"name": "B", "use_ytdlp_fallback": True, "use_aylo_extractor": True},
        "c": {"name": "C"},                       # no flags -> playwright default
        "d": {"use_search_extractor": True},
        "e": {"use_gallerydl_fallback": True},
    }
    m = ys.extractor_site_map(s_cfg)
    assert {e["site_id"] for e in m["aylo"]} == {"a", "b"}, m
    assert {e["site_id"] for e in m["ytdlp"]} == {"b"}, m
    assert {e["site_id"] for e in m["search"]} == {"d"}, m
    assert {e["site_id"] for e in m["gallerydl"]} == {"e"}, m
    assert {e["site_id"] for e in m["playwright"]} == {"c"}, m
    # a multi-backend site appears under each backend it enables
    assert "b" in {e["site_id"] for e in m["aylo"]}


def test_sites_affected_route_registered():
    from flask import Flask
    app = Flask(__name__)
    n = ys.register_routes(app)
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/ytdlp/sites_affected" in rules, f"route missing; have {sorted(rules)}"
    assert n >= 2, "register_routes should now report both ytdlp_status routes"


def test_sites_affected_route_returns_map():
    import bulk_downloader.app_state as st
    from flask import Flask
    saved = getattr(st, "s_cfg", None)
    st.s_cfg = {
        "a": {"name": "A", "use_ytdlp_fallback": True},
        "b": {"name": "B"},   # playwright default
    }
    try:
        app = Flask(__name__)
        ys.register_routes(app)
        r = app.test_client().get("/api/ytdlp/sites_affected")
        assert r.status_code == 200, r.status_code
        data = r.get_json()
        assert data["ok"] is True, data
        assert data["by_extractor"]["ytdlp"]["count"] == 1, data
        assert data["by_extractor"]["playwright"]["count"] == 1, data
        assert data["total_sites"] == 2, data
        assert "ytdlp_stale" in data
    finally:
        if saved is not None:
            st.s_cfg = saved
