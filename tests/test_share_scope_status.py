"""Row share-scope-status (outbound-integrations F2 HIGH).

A read-only share token with scope all_stats read GET /api/status, whose per-site
"config" carries plex_token, stash_api_key, jellyfin_api_key and ha_token. The scope
text promised "/api/status_all, /api/capacity" -- /api/status_all is not a route.
all_stats now grants exactly the routes its text names, and every SHARE_ROUTES entry
is a real GET route named in its scope's KNOWN_SCOPES text and the module docstring.

Real app (fresh_app: scratch workdir, in-memory SQLite), auth configured,
synthetic secrets. No network.
"""
from __future__ import annotations

import re

import pytest

from bulk_downloader import shares

BD_GATE_SCOPE = "module"

H = {"Host": "127.0.0.1:5000"}
OP_TOKEN = "op-token-share-scope-status"
OP = dict(H, Authorization="Bearer " + OP_TOKEN)
# Synthetic values in the BD-SEC-1 canary shape .gitleaks.toml already allowlists (no new allowlist).
SECRETS = {"plex_token": "bdsec1-canary-sscope-plex-PLXA",
           "stash_api_key": "bdsec1-canary-sscope-stash-STSB",
           "jellyfin_api_key": "bdsec1-canary-sscope-jf-JFNC",
           "ha_token": "bdsec1-canary-sscope-ha-HATD"}
ALL_STATS_ROUTES = ["/api/capacity", "/api/health/checklist", "/api/gamification/report"]
PATH_RE = re.compile(r"(/[A-Za-z0-9_/]+)")


@pytest.fixture
def client(fresh_app, monkeypatch):
    import bulk_downloader.app as A
    monkeypatch.setattr(A, "_expected_token", lambda: OP_TOKEN)
    monkeypatch.setattr(A, "_bd_token_secondary", lambda: "")
    return fresh_app


def _token(scopes):
    res = shares.create_token(scopes=scopes, label="share-scope-status")
    assert res["ok"] is True, res
    return res["token"]


def _plant_secrets(client):
    from bulk_downloader import app_state as ST
    r = client.post("/api/sites", json={"name": "ShareScope", "plex_url": "http://192.0.2.1:32400",
                                        "plex_token": "x"}, headers=OP)
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    sid = next(k for k, v in ST.s_cfg.items() if v.get("name") == "ShareScope")
    for key, value in SECRETS.items():       # one field per PUT: a refused field cannot hide the rest
        r = client.put("/api/sites/%s" % sid, json={key: value}, headers=OP)
        assert r.status_code == 200, (key, r.get_data(as_text=True)[:200])
        assert ST.s_cfg[sid].get(key) == value, key
    return sid


def _leaked(body):
    return sorted(k for k, v in SECRETS.items() if v in body)


def test_all_stats_token_is_refused_on_api_status_in_both_forms(client):
    _plant_secrets(client)
    tok = _token(["all_stats"])
    by_header = client.get("/api/status", headers=dict(H, **{"X-Share-Token": tok}))
    by_query = client.get("/api/status?ro_token=" + tok, headers=H)
    for reply in (by_header, by_query):
        body = reply.get_data(as_text=True)
        assert reply.status_code == 401, ("SHARE-STATUS-LEAK", reply.status_code, _leaked(body))
        assert _leaked(body) == []
    # the same token still opens what all_stats names (the probe can say yes)
    assert client.get("/api/capacity", headers=dict(H, **{"X-Share-Token": tok})).status_code == 200


@pytest.mark.parametrize("path", ALL_STATS_ROUTES)
def test_all_stats_token_reads_its_named_routes(client, path):
    tok = _token(["all_stats"])
    assert client.get(path, headers=dict(H, **{"X-Share-Token": tok})).status_code == 200
    assert client.get(path + "?ro_token=" + tok, headers=H).status_code == 200
    assert client.get(path, headers=H).status_code == 401              # no token: still closed


def test_no_share_reachable_route_returns_a_planted_site_secret(client):
    _plant_secrets(client)
    tok = _token(["all"])
    answered = 0
    for prefix, _scope in shares.SHARE_ROUTES:
        reply = client.get(prefix, headers=dict(H, **{"X-Share-Token": tok}))
        body = reply.get_data(as_text=True)
        assert _leaked(body) == [], ("SHARE-ROUTE-LEAK", prefix, reply.status_code, _leaked(body))
        if reply.status_code == 200:
            answered += 1
    assert answered == len(shares.SHARE_ROUTES)                        # every route was really read


def _get_rules():
    import bulk_downloader.app as A
    return {r.rule for r in A.app.url_map.iter_rules() if "GET" in (r.methods or ())}


def _docstring_grants():
    """scope -> set of paths named on its line of the module docstring's grant list."""
    out = {}
    for line in (shares.__doc__ or "").splitlines():
        m = re.match(r"\s*•\s*([a-z_]+)\s+—\s+(.*)$", line)
        if m and m.group(1) in shares.KNOWN_SCOPES:
            out[m.group(1)] = set(PATH_RE.findall(m.group(2)))
    return out


def test_every_share_route_is_a_get_route_named_by_its_scope():
    rules = _get_rules()
    routed = {}
    for prefix, scope in shares.SHARE_ROUTES:
        assert prefix in rules, ("SHARE-ROUTE-NOT-A-GET-ROUTE", prefix)
        routed.setdefault(scope, set()).add(prefix)
    docs = _docstring_grants()
    for scope, prefixes in routed.items():
        named = set(PATH_RE.findall(shares.KNOWN_SCOPES[scope]))
        assert named == prefixes, ("KNOWN_SCOPES-TEXT-MISMATCH", scope, sorted(named ^ prefixes))
        assert docs.get(scope) == prefixes, ("DOCSTRING-MISMATCH", scope, sorted(docs.get(scope, set()) ^ prefixes))
    # a scope with no route grants nothing, so none may be advertised (except the super-scope)
    assert set(shares.KNOWN_SCOPES) - {"all"} == set(routed)
    assert set(docs) - {"all"} == set(routed)
