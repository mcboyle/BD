"""U17 — cookie-jar inspector (D-21) + cookie-age report (D-22) +
auth-cookie heuristic tester (D-29). All three read SiteRunner.cookies;
D-29 exercises the real login._looks_authenticated heuristic.
"""
import time
import types

from bulk_downloader import dev_suite as ds
from bulk_downloader import login as _login


def _now():
    return time.time()


def _runners(jar, saved_at=None):
    """One fake runner 's1' holding the given cookie jar."""
    return {"s1": types.SimpleNamespace(
        cookies=jar, cookie_saved_at=saved_at or (_now() - 3600))}


# ── cookie_jar_inspect (D-21) ─────────────────────────────────────

def test_cookie_jar_inspect_lists_cookies_without_values():
    jar = [{"name": "sessionid", "value": "secret-token-value",
            "domain": ".x.com", "path": "/", "httpOnly": True,
            "secure": True, "sameSite": "Lax"}]
    r = ds.cookie_jar_inspect(runners=_runners(jar))
    assert r["ok"] is True
    cookie = r["sites"][0]["cookies"][0]
    assert cookie["name"] == "sessionid"
    assert cookie["value_length"] == len("secret-token-value")
    # the value itself must NEVER appear
    assert "value" not in cookie
    assert cookie["http_only"] is True and cookie["secure"] is True


def test_cookie_jar_inspect_reports_domains_and_counts():
    jar = [{"name": "a", "value": "1", "domain": ".x.com"},
           {"name": "b", "value": "2", "domain": ".cdn.x.com"}]
    r = ds.cookie_jar_inspect(runners=_runners(jar))
    s = r["sites"][0]
    assert s["cookie_count"] == 2
    assert sorted(s["distinct_domains"]) == [".cdn.x.com", ".x.com"]


def test_cookie_jar_inspect_unknown_site():
    r = ds.cookie_jar_inspect(runners=_runners([]), site_id="ghost")
    assert r["ok"] is False
    assert "no live runner" in r["error"]


# ── cookie_age_report (D-22) ──────────────────────────────────────

def test_cookie_age_report_classifies_expiry():
    now = _now()
    jar = [
        {"name": "sess", "value": "x", "expires": now + 7 * 86400},  # ok
        {"name": "soon", "value": "y", "expires": now + 1800},       # <24h
        {"name": "old", "value": "z", "expires": now - 100},         # expired
        {"name": "pref", "value": "w"},                              # session
    ]
    r = ds.cookie_age_report(runners=_runners(jar))
    s = r["sites"][0]
    assert s["session_cookies"] == 1
    assert s["expired_cookies"] == 1
    assert s["expiring_within_24h"] == 1
    assert r["sites_with_expired_cookies"] == 1


def test_cookie_age_report_identifies_soonest():
    now = _now()
    jar = [{"name": "later", "value": "x", "expires": now + 10 * 86400},
           {"name": "earlier", "value": "y", "expires": now + 2 * 3600}]
    r = ds.cookie_age_report(runners=_runners(jar))
    soonest = r["sites"][0]["soonest_to_expire"]
    assert soonest["name"] == "earlier"
    assert soonest["hours_left"] == 2.0


def test_cookie_age_report_reports_jar_age():
    r = ds.cookie_age_report(
        runners=_runners([{"name": "a", "value": "1"}],
                         saved_at=_now() - 2 * 3600))
    assert r["sites"][0]["jar_age"] == "2h ago"


# ── auth_cookie_test (D-29) ───────────────────────────────────────

def test_auth_cookie_test_accepts_an_auth_named_cookie():
    # use a real hint word so the test tracks the actual heuristic
    hint = _login._AUTH_COOKIE_HINTS[0]
    jar = [{"name": hint + "_id", "value": "abc12345"}]
    r = ds.auth_cookie_test(runners=_runners(jar))
    s = r["sites"][0]
    assert s["looks_authenticated"] is True
    assert s["auth_named_cookies"] == [hint + "_id"]


def test_auth_cookie_test_rejects_a_single_stray_cookie():
    # one non-auth cookie must NOT read as logged in
    jar = [{"name": "analytics", "value": "tracking-blob-1234"}]
    r = ds.auth_cookie_test(runners=_runners(jar))
    assert r["sites"][0]["looks_authenticated"] is False


def test_auth_cookie_test_accepts_several_substantial_cookies():
    # 4+ substantial (value len > 8) non-csrf cookies -> signal 2
    jar = [{"name": f"c{i}", "value": "long-enough-value"}
           for i in range(4)]
    r = ds.auth_cookie_test(runners=_runners(jar))
    s = r["sites"][0]
    assert s["looks_authenticated"] is True
    assert s["substantial_cookies"] == 4


def test_auth_cookie_test_skips_csrf_named_cookies():
    # a csrf cookie must not be counted as auth-named
    jar = [{"name": "csrf_token", "value": "abcdefghijkl"}]
    r = ds.auth_cookie_test(runners=_runners(jar))
    assert r["sites"][0]["auth_named_cookies"] == []


# ── routes ────────────────────────────────────────────────────────

def test_cookie_jar_route(fresh_app):
    resp = fresh_app.get("/api/dev/cookie_jar")
    assert resp.status_code == 200
    assert resp.get_json()["tool"] == "cookie_jar_inspect"


def test_cookie_age_route(fresh_app):
    resp = fresh_app.get("/api/dev/cookie_age")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_auth_cookie_test_route(fresh_app):
    resp = fresh_app.get("/api/dev/auth_cookie_test")
    assert resp.status_code == 200
    assert resp.get_json()["tool"] == "auth_cookie_test"
