"""F3.1 route completion (v3.66.226): PATCH /api/saved_searches/<id>.

The saved_searches.update() logic (partial fields, action validation, the
"bad action is dropped not coerced" guarantee) is already pinned in
test_v3_66_223_watch_enqueue.py. This file proves only the HTTP surface the
SPA action-picker drives: the route is registered for PATCH, is CSRF-gated,
passes the body through to update(), and reports {ok} faithfully (False on a
no-op / unmatched id), so a bad PATCH can never silently flip a rule's lane.

run_tests.py conventions: zero-arg functions, no pytest builtins, repo root
via Path(__file__).resolve().parent.parent.
"""
from pathlib import Path
import sys

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _client():
    from bulk_downloader import app as A
    return A.app.test_client()


def _csrf(c):
    d = c.get("/api/csrf").get_json()
    assert d.get("ok") and d.get("csrf_token"), d
    return d["csrf_token"]


def _add(name="patch-rule", query="alpha"):
    from bulk_downloader import saved_searches as ss
    ss._ensure_table()
    sid = ss.add(name=name, query=query)
    assert sid is not None
    return sid


def test_patch_route_is_registered_for_patch():
    """The endpoint must accept PATCH (not just appear for DELETE)."""
    from bulk_downloader import app as A
    methods = set()
    for rule in A.app.url_map.iter_rules():
        if rule.rule == "/api/saved_searches/<int:search_id>":
            methods |= rule.methods
    assert "PATCH" in methods, f"PATCH not registered: {sorted(methods)}"


def test_patch_requires_csrf():
    """A cookie-backed browser session that omits the token is rejected.
    (CSRF intentionally doesn't apply to cookie-less requests — those are
    defended by the token/Referer layer — so the session must be minted
    first via /api/csrf, which sets bd_session, before omitting the token
    proves the gate.)"""
    from bulk_downloader.db import db_init
    db_init()
    c = _client()
    sid = _add(name="patch-csrf")
    c.get("/api/csrf")  # mints the session + sets the bd_session cookie
    r = c.patch(f"/api/saved_searches/{sid}", json={"action": "enqueue"})
    assert r.status_code == 403, (
        f"PATCH from a cookie session without the token should 403, got "
        f"{r.status_code}: {r.get_json()}")


def test_patch_applies_valid_action():
    from bulk_downloader.db import db_init
    from bulk_downloader import saved_searches as ss
    db_init()
    c = _client()
    sid = _add(name="patch-apply")
    tok = _csrf(c)
    r = c.patch(f"/api/saved_searches/{sid}", json={"action": "enqueue"},
                headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get("ok") is True, r.get_json()
    row = [x for x in ss.list_all() if x["id"] == sid][0]
    assert row["action"] == "enqueue"


def test_patch_bad_action_is_dropped_not_coerced():
    """A bogus action -> no accepted change -> ok False, lane unchanged."""
    from bulk_downloader.db import db_init
    from bulk_downloader import saved_searches as ss
    db_init()
    c = _client()
    sid = _add(name="patch-bad")
    tok = _csrf(c)
    r = c.patch(f"/api/saved_searches/{sid}", json={"action": "bogus"},
                headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get("ok") is False, r.get_json()
    row = [x for x in ss.list_all() if x["id"] == sid][0]
    assert row["action"] == "notify"  # untouched default lane


def test_patch_unmatched_id_reports_not_ok():
    from bulk_downloader.db import db_init
    db_init()
    c = _client()
    tok = _csrf(c)
    r = c.patch("/api/saved_searches/99999999", json={"name": "ghost"},
                headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get("ok") is False, r.get_json()
