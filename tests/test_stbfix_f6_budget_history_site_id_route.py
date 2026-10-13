"""stb-fix F6: budget history must reach the real handler with the exact site id, for any id.

r2: the server decodes %2F before routing, so a plain <sid> converter 404'd ids holding "/"; the path
route became <path:sid>. r3 (cx177): a path segment still cannot carry ".." or "/../x" (browsers
normalize them, the converter rejects a leading slash), so the SPA (hooks/useBudgetHistory.ts) now sends
GET /api/daily_budget/history?site=<encodeURIComponent(id)>&days=N. The path route stays for plain ids.
Each case seeds a distinct byte count for the exact id and reads it back through the app.
"""
from __future__ import annotations

from urllib.parse import quote

import pytest

BD_GATE_SCOPE = "module"

YMD = "2026-10-13"
IDS = ["7642222c", "a/b", "..", "/../x", "a//b", "trail/", "x?days=1", "frag#1", "50%", "%2F", "sp ace", "café-サイト"]


def _encode_uri_component(s: str) -> str:
    # JS encodeURIComponent leaves A-Z a-z 0-9 - _ . ! ~ * ' ( ) unescaped.
    return quote(s, safe="-_.!~*'()")


@pytest.fixture
def client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    return A.app.test_client()


def _get(client, url, sid):
    r = client.get(url)
    assert r.status_code == 200, f"F6_SITE_ID_NOT_ROUTED: {sid!r} -> {r.status_code}"
    return r.get_json()


@pytest.mark.parametrize("sid", IDS)
def test_history_query_transport_reaches_every_site_id(client, sid):
    """The exact URL shape the SPA builds."""
    from bulk_downloader import daily_budget
    n = 1000 + IDS.index(sid)  # distinct per id
    assert daily_budget.record_site_bytes(sid, n, ymd=YMD), "data control: seed write failed"
    j = _get(client, f"/api/daily_budget/history?site={_encode_uri_component(sid)}&days=30", sid)
    assert j["site_id"] == sid, f"F6_WRONG_SITE_ID: {sid!r} -> {j['site_id']!r}"
    assert {"ymd": YMD, "bytes": n} in j["history"], f"F6_WRONG_HISTORY: {sid!r} -> {j['history']!r}"


def test_history_query_transport_requires_site(client):
    r = client.get("/api/daily_budget/history?days=30")
    assert r.status_code == 400 and "site" in r.get_json()["error"]


@pytest.mark.parametrize("sid", ["7642222c", "a/b"])
def test_history_path_route_still_serves_plain_ids(client, sid):
    """Existing path callers keep working (r2 <path:sid>)."""
    from bulk_downloader import daily_budget
    n = 2000 + len(sid)
    assert daily_budget.record_site_bytes(sid, n, ymd=YMD)
    r = client.get(f"/api/daily_budget/history/{_encode_uri_component(sid)}?days=30")
    assert r.status_code == 200, f"F6_SITE_ID_NOT_ROUTED: {sid!r} -> {r.status_code}"
    j = r.get_json()
    assert j["site_id"] == sid, f"F6_WRONG_SITE_ID: {sid!r} -> {j['site_id']!r}"
    assert {"ymd": YMD, "bytes": n} in j["history"], f"F6_WRONG_HISTORY: {sid!r} -> {j['history']!r}"
