"""Defense-in-depth Origin check on state-changing /api/ requests (rec #4).

`_check_csrf` skipped the CSRF check whenever no session cookie was present,
relying on `_check_token`'s same-origin Referer defense. But on a no-auth-token
deployment `_check_token` allows all requests (`not tok`), so a sessionless
cross-origin POST to the localhost app had NO CSRF defense — the classic
"malicious public page attacks the victim's localhost app" vector. This adds a
same-origin Origin check (independent of session/auth): a state-changing /api/
request whose Origin is cross-origin is refused 403; same-origin or absent Origin
falls through to the existing checks (so same-origin browser use and bearer/CLI
are unaffected).

RED on pristine (cross-origin POST is not refused — routes through to 404);
GREEN after the Origin check (403 + "cross-origin").
"""

from bulk_downloader.app import app


def test_cross_origin_state_change_refused():
    c = app.test_client()
    r = c.post("/api/zzz_csrf_probe",
               headers={"Origin": "https://evil.example", "Host": "localhost:5555"})
    assert r.status_code == 403, r.status_code
    assert b"cross-origin" in r.data, r.data[:200]


def test_same_origin_state_change_not_refused_by_origin_check():
    c = app.test_client()
    r = c.post("/api/zzz_csrf_probe",
               headers={"Origin": "http://localhost:5555", "Host": "localhost:5555"})
    # Same-origin must NOT be blocked by the cross-origin guard (it routes on to
    # a 404 for the nonexistent probe path, or to the handler — never the 403).
    assert not (r.status_code == 403 and b"cross-origin" in r.data), r.status_code


def test_absent_origin_preserves_prior_behaviour():
    c = app.test_client()
    r = c.post("/api/zzz_csrf_probe", headers={"Host": "localhost:5555"})
    # No Origin header → unchanged behaviour (not the cross-origin 403).
    assert not (r.status_code == 403 and b"cross-origin" in r.data), r.status_code


def test_get_requests_unaffected():
    c = app.test_client()
    r = c.get("/api/health", headers={"Origin": "https://evil.example"})
    # GET is read-only — the Origin guard must not touch it.
    assert not (r.status_code == 403 and b"cross-origin" in r.data), r.status_code
