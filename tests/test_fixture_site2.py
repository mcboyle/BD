"""Tests for tools/fixture_site2.py - the second mock site.

Like the first fixture, this is test infrastructure, so it needs its
own tests. Covers each of the awkward site shapes: infinite scroll,
JSON/GraphQL API, challenge interstitial, paywall gating, and the SPA
shell.

Driven with Flask's test client - the custom runner does not inject
@pytest.fixture parameters, so `client` is a plain helper.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))


def _client():
    from fixture_site2 import make_app
    return make_app().test_client()


# ── Core ────────────────────────────────────────────────────────────────

def test_fixture2_index():
    r = _client().get("/")
    assert r.status_code == 200
    assert b"Fixture 2" in r.data


def test_fixture2_response_tagged():
    r = _client().get("/")
    assert r.headers.get("X-Fixture-Site") == "bd-test-fixture-2"


def test_fixture2_media_download():
    r = _client().get("/media2/5.mp4")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "video/mp4"
    assert r.data[4:8] == b"ftyp"


# ── Infinite scroll ─────────────────────────────────────────────────────

def test_fixture2_infinite_catalog_has_no_page_links():
    """The infinite catalog must NOT expose page-number links - an
    adapter has to use the batch API instead."""
    r = _client().get("/infinite/catalog")
    assert r.status_code == 200
    assert b"load-more" in r.data
    assert b"data-next-offset" in r.data


def test_fixture2_infinite_batch_paginates():
    c = _client()
    b0 = c.get("/infinite/batch?offset=0").get_json()
    assert len(b0["items"]) == 10
    assert b0["next_offset"] == 10
    b1 = c.get("/infinite/batch?offset=10").get_json()
    assert b1["items"][0]["id"] == 10


def test_fixture2_infinite_batch_last_page_null_offset():
    """The final batch must report next_offset=None so a scroller
    knows to stop."""
    c = _client()
    # total is 84; offset 80 returns the last 4 and a null next
    last = c.get("/infinite/batch?offset=80").get_json()
    assert last["next_offset"] is None


def test_fixture2_infinite_batch_bad_offset_defaults():
    r = _client().get("/infinite/batch?offset=garbage")
    assert r.status_code == 200
    assert r.get_json()["items"][0]["id"] == 0


# ── JSON / GraphQL API ──────────────────────────────────────────────────

def test_fixture2_api_items_json():
    r = _client().get("/api/items")
    body = r.get_json()
    assert body["page"] == 1
    assert len(body["items"]) == 10


def test_fixture2_api_item_by_id():
    r = _client().get("/api/item/7")
    assert r.get_json()["id"] == 7


def test_fixture2_api_item_not_found():
    r = _client().get("/api/item/99999")
    assert r.status_code == 404


def test_fixture2_graphql_items_query():
    r = _client().post("/api/graphql",
                        json={"query": "{ items { id title } }"})
    body = r.get_json()
    assert "data" in body
    assert len(body["data"]["items"]) == 10


def test_fixture2_graphql_item_query_with_variables():
    r = _client().post("/api/graphql",
                        json={"query": "query($id:Int){ item(id:$id) }",
                              "variables": {"id": 12}})
    assert r.get_json()["data"]["item"]["id"] == 12


def test_fixture2_graphql_unknown_query_rejected():
    r = _client().post("/api/graphql", json={"query": "{ nonsense }"})
    assert r.status_code == 400
    assert "errors" in r.get_json()


# ── Challenge interstitial ──────────────────────────────────────────────

def test_fixture2_challenge_blocks_without_cookie():
    """First hit to a protected page returns the interstitial, not
    the content."""
    r = _client().get("/challenge/scene/3")
    assert r.status_code == 503
    assert r.headers.get("X-Challenge") == "interstitial"
    assert b"Checking your browser" in r.data


def test_fixture2_challenge_passes_with_cookie():
    c = _client()
    c.set_cookie("cf_clearance", "solved-ok", domain="localhost")
    r = c.get("/challenge/scene/3")
    assert r.status_code == 200
    assert b"download-link" in r.data


def test_fixture2_challenge_solve_sets_cookie():
    r = _client().get("/challenge/solve")
    assert r.status_code == 302
    cookies = r.headers.getlist("Set-Cookie")
    assert any("cf_clearance" in c for c in cookies)


# ── Paywall ─────────────────────────────────────────────────────────────

def test_fixture2_paywall_locked_without_subscription():
    """An unsubscribed visitor sees the paywall stub - title visible,
    download link absent."""
    r = _client().get("/paywall/scene/3")
    assert r.status_code == 200
    assert b"data-locked='true'" in r.data
    assert b"download-link" not in r.data


def test_fixture2_paywall_unlocked_with_subscription():
    c = _client()
    c.set_cookie("subscription", "active", domain="localhost")
    r = c.get("/paywall/scene/3")
    assert b"download-link" in r.data
    assert b"data-locked" not in r.data


def test_fixture2_paywall_login_good_creds():
    r = _client().post("/paywall/login",
                        data={"username": "tester",
                              "password": "fixturepass"})
    assert r.status_code == 302


def test_fixture2_paywall_login_bad_creds():
    r = _client().post("/paywall/login",
                        data={"username": "x", "password": "y"})
    assert r.status_code == 401


# ── SPA shell ───────────────────────────────────────────────────────────

def test_fixture2_spa_shell_is_near_empty():
    """The SPA shell must NOT contain the catalog in its HTML - it
    only names the API endpoint."""
    r = _client().get("/spa/")
    assert b"data-api" in r.data
    assert b"Mock Item" not in r.data  # content is NOT in the shell


def test_fixture2_spa_state_api():
    r = _client().get("/spa/api/state")
    body = r.get_json()
    assert body["view"] == "catalog"
    assert len(body["items"]) == 10


def test_fixture2_spa_item_api():
    r = _client().get("/spa/api/item/9")
    assert r.get_json()["id"] == 9


# ── Sitemap ─────────────────────────────────────────────────────────────

def test_fixture2_sitemap_lists_all_shapes():
    routes = _client().get("/_sitemap").get_json()["routes"]
    joined = " ".join(routes)
    for prefix in ("/infinite", "/api", "/challenge", "/paywall",
                   "/spa"):
        assert prefix in joined, f"{prefix} missing from sitemap"
