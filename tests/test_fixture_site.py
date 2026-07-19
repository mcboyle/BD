"""Tests for tools/fixture_site.py — the self-hosted mock site.

The fixture site is itself test infrastructure, so it needs its own
tests: if the fixture is wrong, every test that relies on it is
suspect. Covers each login prefix, each download technique, the
player-markup prefixes, throttle simulation, and every fault type.

These drive the app with Flask's test client — no real socket — so
they're fast and don't need a free port.
"""
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))


def _client():
    """Plain helper — the custom runner does not inject @pytest.fixture
    parameters, only autouse fixtures. Each test calls this directly."""
    from fixture_site import make_app
    return make_app(throttle_after=3, slow_delay=0.1).test_client()


# ── Core site ───────────────────────────────────────────────────────────

def test_fixture_index():
    client = _client()
    r = client.get("/")
    assert r.status_code == 200
    assert b"Test Fixture" in r.data


def test_fixture_response_tagged():
    client = _client()
    """Every response carries the X-Fixture-Site header so tests can
    confirm they hit the fixture and not something else."""
    r = client.get("/")
    assert r.headers.get("X-Fixture-Site") == "bd-test-fixture"


def test_fixture_catalog_paginates():
    client = _client()
    p1 = client.get("/catalog?page=1")
    assert b"data-page='1'" in p1.data
    p2 = client.get("/catalog?page=2")
    assert b"data-page='2'" in p2.data
    # different scenes on different pages
    assert p1.data != p2.data


def test_fixture_catalog_bad_page_defaults():
    client = _client()
    r = client.get("/catalog?page=notanumber")
    assert r.status_code == 200
    assert b"data-page='1'" in r.data


def test_fixture_scene_detail():
    client = _client()
    r = client.get("/scene/5")
    assert r.status_code == 200
    assert b"scene-title" in r.data
    assert b"download-link" in r.data


def test_fixture_scene_out_of_range_404():
    client = _client()
    assert client.get("/scene/99999").status_code == 404


# ── Login prefixes ──────────────────────────────────────────────────────

def test_fixture_formauth_good_creds():
    client = _client()
    r = client.post("/formauth/login",
                     data={"username": "tester", "password": "fixturepass"})
    assert r.status_code == 302  # redirect to members


def test_fixture_formauth_bad_creds():
    client = _client()
    r = client.post("/formauth/login",
                     data={"username": "wrong", "password": "wrong"})
    assert r.status_code == 401


def test_fixture_formauth_members_requires_session():
    client = _client()
    """Hitting members without logging in redirects to login."""
    r = client.get("/formauth/members")
    assert r.status_code == 302


def test_fixture_basicauth_rejects_no_auth():
    client = _client()
    r = client.get("/basicauth/members")
    assert r.status_code == 401
    assert "WWW-Authenticate" in r.headers


def test_fixture_basicauth_accepts_good():
    client = _client()
    import base64
    cred = base64.b64encode(b"tester:fixturepass").decode()
    r = client.get("/basicauth/members",
                    headers={"Authorization": f"Basic {cred}"})
    assert r.status_code == 200


def test_fixture_tokenauth_rejects_no_token():
    client = _client()
    assert client.get("/tokenauth/members").status_code == 401


def test_fixture_tokenauth_accepts_bearer():
    client = _client()
    r = client.get("/tokenauth/members",
                    headers={"Authorization": "Bearer fixture-token-abc123"})
    assert r.status_code == 200


def test_fixture_tokenauth_accepts_api_key():
    client = _client()
    r = client.get("/tokenauth/members",
                    headers={"X-API-Key": "fixture-token-abc123"})
    assert r.status_code == 200


def test_fixture_csrfauth_rejects_missing_token():
    client = _client()
    """POST without the CSRF token must be rejected."""
    r = client.post("/csrfauth/login",
                     data={"username": "tester", "password": "fixturepass"})
    assert r.status_code == 403


def test_fixture_csrfauth_two_step_flow():
    client = _client()
    """The real flow: GET the form, extract the token, POST with it."""
    import re
    form = client.get("/csrfauth/login")
    m = re.search(rb"name='csrf_token' value='([a-f0-9]+)'", form.data)
    assert m, "csrf token not found in form"
    token = m.group(1).decode()
    r = client.post("/csrfauth/login",
                     data={"csrf_token": token, "username": "tester",
                           "password": "fixturepass"})
    assert r.status_code == 302


# ── Download techniques ─────────────────────────────────────────────────

def test_fixture_direct_download():
    client = _client()
    r = client.get("/direct/media/5.mp4")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "video/mp4"
    assert "Content-Disposition" in r.headers
    assert len(r.data) > 0


def test_fixture_direct_download_real_mp4_header():
    client = _client()
    """The generated file must start with a valid MP4 ftyp box."""
    r = client.get("/direct/media/5.mp4")
    # bytes 4-8 are the box type
    assert r.data[4:8] == b"ftyp"


def test_fixture_range_returns_206():
    client = _client()
    r = client.get("/range/media/5.mp4",
                    headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert len(r.data) == 100
    assert "Content-Range" in r.headers


def test_fixture_range_no_range_returns_full():
    client = _client()
    r = client.get("/range/media/5.mp4")
    assert r.status_code == 200
    assert r.headers.get("Accept-Ranges") == "bytes"


def test_fixture_range_unsatisfiable_416():
    client = _client()
    r = client.get("/range/media/5.mp4",
                    headers={"Range": "bytes=999999-1000000"})
    assert r.status_code == 416


def test_fixture_redirect_chain():
    client = _client()
    """The redirect chain must resolve to a real file when followed."""
    r = client.get("/redirect/media/5.mp4", follow_redirects=True)
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "video/mp4"


def test_fixture_hls_playlist():
    client = _client()
    r = client.get("/hls/scene/5.m3u8")
    assert r.status_code == 200
    assert b"#EXTM3U" in r.data
    assert b".ts" in r.data


def test_fixture_hls_segment():
    client = _client()
    r = client.get("/hls/seg/5_0.ts")
    assert r.status_code == 200
    # MPEG-TS sync byte
    assert r.data[0] == 0x47


# ── Player-markup prefixes ──────────────────────────────────────────────

def test_fixture_videojs_markup():
    client = _client()
    r = client.get("/videojs/scene/5")
    assert b"video-js" in r.data
    assert b"<source" in r.data


def test_fixture_vip4k_all_four_variants():
    client = _client()
    """The VIP4K page must carry all four download-variant markups."""
    r = client.get("/vip4k/scene/5")
    assert b"ct_dl_button" in r.data
    assert b"data-href" in r.data
    assert b"data-download" in r.data
    assert b"exDownloadMenu" in r.data


def test_fixture_datahref_markup():
    client = _client()
    r = client.get("/datahref/scene/5")
    assert b"data-href" in r.data


# ── Throttle simulation ─────────────────────────────────────────────────

def test_fixture_flaky_serves_when_uncontended():
    client = _client()
    """A single request below the concurrency threshold succeeds."""
    r = client.get("/flaky/media/5.mp4")
    assert r.status_code == 200


# ── Fault injection ─────────────────────────────────────────────────────

def test_fixture_inject_404_then_expires():
    client = _client()
    """A once-fault fires once, then the endpoint returns to normal."""
    client.post("/_control/inject", json={"type": "http_404", "times": 1})
    assert client.get("/direct/media/5.mp4").status_code == 404
    assert client.get("/direct/media/5.mp4").status_code == 200


def test_fixture_inject_truncate():
    client = _client()
    """Truncate: body shorter than the Content-Length header claims."""
    client.post("/_control/inject",
                json={"type": "truncate", "path": "/direct/media/7.mp4"})
    r = client.get("/direct/media/7.mp4")
    declared = int(r.headers["Content-Length"])
    assert len(r.data) < declared


def test_fixture_inject_corrupt():
    client = _client()
    """Corrupt: right length, wrong bytes."""
    client.post("/_control/inject",
                json={"type": "corrupt", "path": "/direct/media/8.mp4"})
    r = client.get("/direct/media/8.mp4")
    assert r.data[:4] == b"\xde\xad\xbe\xef"


def test_fixture_inject_500():
    client = _client()
    client.post("/_control/inject",
                json={"type": "http_500", "path": "/direct/media/9.mp4"})
    assert client.get("/direct/media/9.mp4").status_code == 500


def test_fixture_inject_flapping():
    client = _client()
    """Flapping armed with times=4: 3 failures then recovery."""
    client.post("/_control/inject",
                json={"type": "flapping", "path": "/direct/media/11.mp4",
                      "times": "4"})
    codes = [client.get("/direct/media/11.mp4").status_code
             for _ in range(5)]
    assert 503 in codes        # it failed
    assert codes[-1] == 200    # and eventually recovered


def test_fixture_inject_resume_fail():
    client = _client()
    """resume_fail: a Range request gets a plain 200, no 206."""
    client.post("/_control/inject",
                json={"type": "resume_fail", "path": "/range/media/12.mp4"})
    r = client.get("/range/media/12.mp4",
                   headers={"Range": "bytes=0-50"})
    assert r.status_code == 200  # NOT 206 — resume defeated


def test_fixture_inject_unknown_type_rejected():
    client = _client()
    r = client.post("/_control/inject", json={"type": "not_a_real_fault"})
    assert r.status_code == 400


def test_fixture_inject_path_scoped():
    client = _client()
    """A path-scoped fault must NOT fire on other paths."""
    client.post("/_control/inject",
                json={"type": "http_404", "path": "/direct/media/20.mp4",
                      "times": "inf"})
    # the scoped path faults
    assert client.get("/direct/media/20.mp4").status_code == 404
    # a different path does not
    assert client.get("/direct/media/21.mp4").status_code == 200


def test_fixture_nopath_fault_hits_pages():
    """A fault armed with NO target path applies to ALL requests -
    pages included, not just downloads."""
    client = _client()
    client.post("/_control/inject", json={"type": "http_500", "times": 1})
    # /catalog is a page, not a download - it must still fault
    assert client.get("/catalog").status_code == 500
    # and then return to normal once the once-budget is spent
    assert client.get("/catalog").status_code == 200


def test_fixture_nopath_fault_hits_downloads_too():
    """The no-path fault still covers downloads as well as pages."""
    client = _client()
    client.post("/_control/inject", json={"type": "http_404", "times": 1})
    assert client.get("/direct/media/5.mp4").status_code == 404


def test_fixture_control_routes_exempt_from_faults():
    """Even with a permanent no-path fault armed, /_control must stay
    reachable - otherwise you couldn't disarm the fault."""
    client = _client()
    client.post("/_control/inject", json={"type": "http_500",
                                          "times": "inf"})
    assert client.get("/_control").status_code == 200
    assert client.get("/_control/stats").status_code == 200
    assert client.get("/_control/armed").status_code == 200
    # and disarm still works to clear it
    armed = client.get("/_control/armed").get_json()["armed"]
    client.post(f"/_control/disarm/{armed[0]['id']}")
    assert client.get("/_control/armed").get_json()["armed"] == []


def test_fixture_disarm():
    client = _client()
    client.post("/_control/inject",
                json={"type": "http_500", "times": "inf"})
    armed = client.get("/_control/armed").get_json()["armed"]
    assert len(armed) == 1
    client.post(f"/_control/disarm/{armed[0]['id']}")
    assert client.get("/_control/armed").get_json()["armed"] == []


def test_fixture_reset_clears_faults_and_counters():
    client = _client()
    client.post("/_control/inject",
                json={"type": "http_500", "times": "inf"})
    client.get("/")  # bump a counter
    client.post("/_control/reset")
    stats = client.get("/_control/stats").get_json()
    assert stats["faults_fired"] == 0
    assert client.get("/_control/armed").get_json()["armed"] == []


def test_fixture_control_ui_loads():
    client = _client()
    """The management UI page renders and contains an Arm button."""
    r = client.get("/_control")
    assert r.status_code == 200
    assert b"Arm" in r.data
    assert b"connection_lost" in r.data


def test_fixture_stats_track_requests():
    client = _client()
    client.post("/_control/reset")
    client.get("/")
    client.get("/catalog")
    stats = client.get("/_control/stats").get_json()
    assert stats["requests"] >= 2


def test_fixture_sitemap_lists_routes():
    client = _client()
    routes = client.get("/_control/sitemap").get_json()["routes"]
    # spot-check that the major prefixes are all registered
    joined = " ".join(routes)
    for prefix in ("/formauth", "/basicauth", "/tokenauth", "/csrfauth",
                   "/direct", "/range", "/redirect", "/hls",
                   "/videojs", "/vip4k", "/flaky", "/_control"):
        assert prefix in joined, f"{prefix} missing from sitemap"
