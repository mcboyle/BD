"""O1789 bug-hunt hl-history-search: history / search / saved-search input handling.

Findings (harness-work/O1789/bughunt/history-library-search/FINDINGS.md, finder bd-worker-S04-B, BASE b0ea77a6):
  F3 /api/search/facets counts a different row set than /api/search for the same q (LIKE on filename/url vs FTS).
  F4 LIKE wildcards in q are not escaped: /api/history?q=a_b matches 'axb', q=% matches every row; library browse too.
  F5 /api/history?limit>=2**63 -> 500 (OverflowError); no upper bound at all.
  F6 POST /api/history/prune {"days": "abc"|null|Infinity} -> 500; a negative days is accepted silently.
  F7 /api/history?paginate=1&cursor=abc -> 200 with the FIRST page instead of a refusal.
  F9 PATCH /api/saved_searches/<id> stores an empty name/query and the TEXT 'false' for enabled.
  F10 type-confused saved-search bodies and /api/session_history?limit=abc -> 500 with raw exception text.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import pytest

pytestmark = pytest.mark.bd_module_wipe


@pytest.fixture
def client(clean_workdir):
    from bulk_downloader import app as app_mod
    from bulk_downloader.db import db_init, db_log
    from bulk_downloader.migrations import apply_pending

    db_init()
    assert apply_pending(backup_first=False)["errors"] == 0

    def _log(site_id, slug, status, message, title):
        target = clean_workdir / f"{slug}.mp4"
        target.write_bytes(b"x")
        db_log(site_id, "Acme", f"https://a.example/v/{slug}", status, filename=f"{slug}.mp4",
               file_size=1, message=message, file_path=str(target), title=title, title_source="document.title")

    _log("siteA", "axb", "done", "plain words", "Alpha")
    _log("siteB", "a_b", "failed", "rate 100 percent 100% done", "Beta")
    _log("siteB", "zzz", "done", "tokseven marker", "Gamma Title")
    app = app_mod.app
    saved = app.config.get("PROPAGATE_EXCEPTIONS")
    app.config["PROPAGATE_EXCEPTIONS"] = False  # a handler crash must read as HTTP 500, as it does live
    try:
        yield app.test_client()
    finally:
        app.config["PROPAGATE_EXCEPTIONS"] = saved


def _slugs(rows):
    return sorted(r["url"].rsplit("/", 1)[-1] for r in rows)


# ---- F3: facets count the same rows search returns ------------------------------------------------------------
@pytest.mark.parametrize("q", ["tokseven", "Gamma", "a_b", "Acme"])
def test_f3_facets_agree_with_search(client, q):
    found = client.get(f"/api/search?q={q}").get_json()["results"]
    facets = client.get(f"/api/search/facets?q={q}").get_json()["facets"]
    by_site = {}
    for r in found:
        by_site[r["site_id"]] = by_site.get(r["site_id"], 0) + 1
    assert (facets["total"], facets["by_site"]) == (len(found), by_site), (
        f"F3: facets for q={q!r} = {facets} but /api/search returned {len(found)} rows {by_site}")


# ---- F4: LIKE wildcards are literal ---------------------------------------------------------------------------
@pytest.mark.parametrize("q,expected", [("a_b", ["a_b"]), ("%", ["a_b"]), ("axb", ["axb"])])
def test_f4_history_query_wildcards_are_literal(client, q, expected):
    from urllib.parse import quote
    rows = client.get(f"/api/history?q={quote(q)}").get_json()
    assert _slugs(rows) == expected, f"F4: /api/history?q={q!r} matched {_slugs(rows)}"
    page = client.get(f"/api/history?paginate=1&q={quote(q)}").get_json()["rows"]
    assert _slugs(page) == expected, f"F4: cursor page q={q!r} matched {_slugs(page)}"


# Library rows exist for the two 'done' downloads only (axb, zzz): no library path holds a literal 'a_b'.
@pytest.mark.parametrize("q,expected", [("a_b", []), ("axb", ["axb.mp4"])])
def test_f4_library_browse_query_wildcards_are_literal(client, q, expected):
    body = client.get(f"/api/library/browse?q={q}").get_json()
    paths = sorted(r["file_path"].rsplit("/", 1)[-1] for r in body["rows"])
    assert paths == expected, f"F4: /api/library/browse?q={q} matched {paths}"



# ADD (queue-templates-io L6): the live queue search had the same unescaped LIKE.
# r3: queue/search 404s an unknown site (q-import-export L6), so the rows go to a site the app knows.
@pytest.mark.parametrize("q,expected", [("a_b", ["a_b"]), ("_", ["a_b"]), ("%", []), ("axb", ["axb"])])
def test_f4_queue_search_wildcards_are_literal(client, q, expected):
    from urllib.parse import quote
    from bulk_downloader.db import queue_upsert
    sid = client.post("/api/sites", json={"name": "HlQueueSite"}).get_json()["id"]
    for slug in ("a_b", "axb"):
        queue_upsert(sid, f"https://q.example/v/{slug}")
    rows = client.get(f"/api/sites/{sid}/queue/search?q={quote(q)}").get_json()["rows"]
    assert _slugs(rows) == expected, f"F4/L6: queue search q={q!r} matched {_slugs(rows)}"

# ---- F5: limit is bounded ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("limit", [str(2 ** 63), str(10 ** 9)])
def test_f5_history_huge_limit_is_clamped(client, limit):
    r = client.get(f"/api/history?limit={limit}")
    assert r.status_code == 200, f"F5: limit={limit} -> {r.status_code} {r.data[:120]!r}"
    r = client.get(f"/api/history?paginate=1&limit={limit}")
    assert r.status_code == 200, f"F5: paginated limit={limit} -> {r.status_code} {r.data[:120]!r}"


def test_f5_control_small_limit_still_limits(client):
    assert len(client.get("/api/history?limit=2").get_json()) == 2


# ---- F6: prune days validated -----------------------------------------------------------------------------------
@pytest.mark.parametrize("raw", ['{"days": "abc"}', '{"days": null}', '{"days": Infinity}', '{"days": -5}',
                                 '{"days": true}', '{"days": 3.9}', '[1]'])
def test_f6_prune_bad_days_is_400(client, raw):
    r = client.post("/api/history/prune", data=raw, content_type="application/json")
    assert r.status_code == 400, f"F6: prune {raw} -> {r.status_code} {r.data[:120]!r}"
    assert len(client.get("/api/history").get_json()) == 3


@pytest.mark.parametrize("raw,days", [('{"days": 30}', 30), ('{"days": "7"}', 7), ('{}', 90)])
def test_f6_control_valid_days_prune(client, raw, days):
    r = client.post("/api/history/prune", data=raw, content_type="application/json")
    assert r.status_code == 200 and r.get_json()["days"] == days


# ---- F7: a bad cursor is refused --------------------------------------------------------------------------------
@pytest.mark.parametrize("cursor", ["abc", "1.5", str(2 ** 63), "-1"])
def test_f7_history_bad_cursor_is_400(client, cursor):
    r = client.get(f"/api/history?paginate=1&cursor={cursor}")
    assert r.status_code == 400, f"F7: cursor={cursor!r} -> {r.status_code} {r.data[:120]!r}"
    assert r.get_json()["error"] == "cursor_invalid"


def test_f7_control_cursor_walk(client):
    first = client.get("/api/history?paginate=1&limit=2").get_json()
    rest = client.get(f"/api/history?paginate=1&limit=2&cursor={first['next_cursor']}").get_json()
    assert len(first["rows"]) == 2 and len(rest["rows"]) == 1


# ---- F9 / F10: saved searches -----------------------------------------------------------------------------------
def _saved(sid):
    from bulk_downloader.db import db_conn
    with db_conn() as cx:
        r = cx.execute("SELECT name, query, enabled, typeof(enabled) AS t FROM saved_searches WHERE id=?",
                       (sid,)).fetchone()
    return dict(r)


@pytest.fixture
def saved_id(client):
    r = client.post("/api/saved_searches", json={"name": "keep", "query": "tokseven"})
    assert r.status_code == 200, r.data
    return r.get_json()["id"]


@pytest.mark.parametrize("patch", [{"name": ""}, {"query": "  "}, {"name": 5}, {"enabled": "false"}])
def test_f9_patch_refuses_what_post_refuses(client, saved_id, patch):
    client.patch(f"/api/saved_searches/{saved_id}", json=patch)
    row = _saved(saved_id)
    assert (row["name"], row["query"], row["enabled"], row["t"]) == ("keep", "tokseven", 1, "integer"), (
        f"F9: PATCH {patch} stored {row}")


@pytest.mark.parametrize("patch,enabled", [({"enabled": False}, 0), ({"enabled": 0}, 0), ({"enabled": True}, 1)])
def test_f9_control_patch_enabled_bool(client, saved_id, patch, enabled):
    assert client.patch(f"/api/saved_searches/{saved_id}", json=patch).get_json()["ok"] is True
    row = _saved(saved_id)
    assert (row["enabled"], row["t"]) == (enabled, "integer")


def test_f9_patch_name_is_stripped_like_post(client, saved_id):
    assert client.patch(f"/api/saved_searches/{saved_id}", json={"name": " renamed "}).get_json()["ok"] is True
    assert _saved(saved_id)["name"] == "renamed"


@pytest.mark.parametrize("method,path,raw", [
    ("post", "/api/saved_searches", '{"name": 5, "query": [1]}'),
    ("post", "/api/saved_searches", '"str"'),
    ("post", "/api/saved_searches", '{"name": "n", "query": "q", "schedule": [1]}'),
    ("patch", "/api/saved_searches/1", '[1]'),
])
def test_f10_type_confused_saved_search_body_is_400(client, saved_id, method, path, raw):
    r = getattr(client, method)(path, data=raw, content_type="application/json")
    assert r.status_code == 400, f"F10: {method.upper()} {path} {raw} -> {r.status_code} {r.data[:120]!r}"


def test_f10_session_history_bad_limit_is_400(client):
    r = client.get("/api/session_history?limit=abc")
    assert r.status_code == 400, f"F10: session_history limit=abc -> {r.status_code} {r.data[:120]!r}"


def test_f10_control_session_history_ok(client):
    assert client.get("/api/session_history?limit=5").status_code == 200


# ---- r2 (cx158 F1): a malformed or non-object body is refused, never read as {} --------------------------------
# r1 parsed with get_json(silent=True) or {}: a malformed body became {} and ran a 90-day prune (BASE answered 400).
_OLD_URL = "https://a.example/v/a_b"


def _age_old_row():
    from bulk_downloader.db import db_conn
    with db_conn() as cx:
        cx.execute("UPDATE history SET ts=datetime('now', '-400 days') WHERE url=?", (_OLD_URL,))


def _history_urls(client):
    return sorted(r["url"] for r in client.get("/api/history").get_json())


@pytest.mark.parametrize("raw", ['{"days": 30', "", "null", "false", "0", '""', "[]"])
def test_r2_prune_malformed_or_non_object_body_deletes_nothing(client, raw):
    _age_old_row()
    r = client.post("/api/history/prune", data=raw, content_type="application/json")
    assert r.status_code == 400, f"r2/F1: prune body {raw!r} -> {r.status_code} {r.data[:120]!r}"
    assert _OLD_URL in _history_urls(client), f"r2/F1: prune body {raw!r} deleted the 400-day-old row"


@pytest.mark.parametrize("raw", ['{"days": 30}', "{}"])
def test_r2_control_valid_prune_deletes_the_old_row(client, raw):
    _age_old_row()
    r = client.post("/api/history/prune", data=raw, content_type="application/json")
    assert r.status_code == 200 and r.get_json()["deleted"] == 1, r.data
    assert _OLD_URL not in _history_urls(client)


@pytest.mark.parametrize("method,raw", [("patch", '{"name": "x"'), ("patch", "null"), ("patch", ""),
                                        ("post", '{"name": "n", "query": "q"'), ("post", "null")])
def test_r2_saved_search_malformed_body_is_400(client, saved_id, method, raw):
    path = f"/api/saved_searches/{saved_id}" if method == "patch" else "/api/saved_searches"
    r = getattr(client, method)(path, data=raw, content_type="application/json")
    assert r.status_code == 400, f"r2/F1: {method.upper()} body {raw!r} -> {r.status_code} {r.data[:120]!r}"
    assert _saved(saved_id)["name"] == "keep"
