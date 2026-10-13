"""BD-SEC-6 (bughunt api-auth-csrf-ssrf F1 + F2): when a token is configured, an /api/ request is authenticated ONLY by
a token or by a session created by a real login (the QR pairing redeem).

  F1  the anonymous bd_session that GET / (and /api/csrf) mints for anyone counted as authenticated;
  F2  a Referer whose host:port equalled the Host header authenticated -- both are headers the client writes.

Each repro is paired with the controls that show the probe can say yes: the real token works, a real login session
works (read and CSRF-guarded write), and an app with no token configured behaves as before.
"""

import ast
import inspect
import time

import pytest

from bulk_downloader import app as bd_app
from bulk_downloader import app_state

BD_GATE_SCOPE = "module"
TOKEN = "fixture-bd-sec-6-token"  # a test fixture value, not a credential
READ = "/api/global_config"
HOST = "bd.local:5555"
DENIED = "BD_SEC_6_UNAUTHENTICATED_REQUEST_ALLOWED"


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("BD_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("BD_TOKEN", raising=False)
    assert bd_app._accepted_tokens() == [TOKEN], (
        "COULD NOT LOOK: the token is not what the app reads"
    )
    return bd_app.app.test_client()


@pytest.fixture
def auth_off(monkeypatch):
    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BD_TOKEN", raising=False)
    assert bd_app._accepted_tokens() == [], (
        "COULD NOT LOOK: a token is configured in this environment"
    )
    return bd_app.app.test_client()


def _bearer():
    return {"Authorization": f"Bearer {TOKEN}"}


def _session_cookie(response):
    for line in response.headers.getlist("Set-Cookie"):
        if line.startswith("bd_session="):
            return line.split("=", 1)[1].split(";", 1)[0]
    return ""


def _refused(response):
    assert response.status_code == 401, (
        f"{DENIED}: {response.status_code} {response.get_data(as_text=True)[:120]}"
    )
    assert response.get_json() == {"error": "authentication required"}


# ── F1: the anonymous session ────────────────────────────────────────────────────────────────────────────────────────


def test_f1_the_session_minted_by_get_root_does_not_authenticate(auth_on):
    """The finder's repro: GET / hands any client a bd_session cookie; with it the API answered 200 and took writes."""
    first = auth_on.get("/")
    assert _session_cookie(first), (
        "COULD NOT LOOK: GET / set no bd_session cookie, the repro has no cookie to try"
    )
    _refused(auth_on.get(READ))
    _refused(auth_on.get("/api/secrets/status"))
    _refused(auth_on.get("/api/csrf"))
    _refused(auth_on.post(READ, json={}))


@pytest.mark.parametrize("source", ["csrf_bootstrap", "manual", "a_source_added_later"])
def test_f1_a_session_that_is_not_a_login_does_not_authenticate(auth_on, source):
    """Fail-closed by source: only the listed login sources authenticate; the default and any new source do not."""
    sess = bd_app._session_create(source=source)
    assert bd_app._session_valid(sess) and not bd_app._session_authenticated(sess)
    auth_on.set_cookie("bd_session", sess)
    _refused(auth_on.get(READ))
    _refused(
        auth_on.post(
            READ, json={}, headers={"X-CSRF-Token": bd_app._csrf_token_for(sess)}
        )
    )


def test_f1_an_unknown_or_expired_login_session_does_not_authenticate(auth_on):
    auth_on.set_cookie("bd_session", "not-a-session")
    _refused(auth_on.get(READ))
    sess = bd_app._session_create(source="pair_redeem")
    with bd_app._session_lock:
        bd_app._sessions[sess]["last_used"] = time.time() - bd_app.SESSION_IDLE_TTL - 5
    auth_on.set_cookie("bd_session", sess)
    _refused(auth_on.get(READ))


# ── F2: the Referer ──────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host,referer",
    [
        (HOST, f"http://{HOST}/"),
        (HOST, f"http://{HOST}/app/queue?x=1"),
        ("bd.local", "http://bd.local:80/"),
        ("bd.local", "https://bd.local:443/"),
        ("BD.Local:5555", "http://bd.local:5555/"),
        ("localhost", "http://localhost/"),
    ],
)
def test_f2_a_referer_that_matches_the_host_does_not_authenticate(
    auth_on, host, referer
):
    """The finder's repro: curl -H 'Referer: http://H/' -> 200 on GET and POST. Every shape the old parse accepted."""
    headers = {"Host": host, "Referer": referer}
    _refused(auth_on.get(READ, headers=headers))
    _refused(auth_on.post(READ, json={}, headers=headers))


def test_f2_a_referer_plus_an_anonymous_session_does_not_authenticate(auth_on):
    auth_on.get("/")
    _refused(auth_on.get(READ, headers={"Host": HOST, "Referer": f"http://{HOST}/"}))


def test_f2_check_token_reads_no_referer_at_all():
    """Static: the function that authenticates never reads the Referer header again (comments are not code)."""
    tree = ast.parse(inspect.getsource(bd_app._check_token))
    strings = {
        n.value.lower()
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert "authorization" in strings, (
        "COULD NOT LOOK: the scan does not see the headers this function reads"
    )
    assert "referer" not in strings and "referrer" not in strings
    assert not any(
        isinstance(n, ast.Attribute) and n.attr == "referrer" for n in ast.walk(tree)
    )


# ── controls: what must keep working ─────────────────────────────────────────────────────────────────────────────────


def test_control_no_credentials_and_a_wrong_token_are_refused(auth_on):
    _refused(auth_on.get(READ))
    _refused(auth_on.get(READ, headers={"Authorization": "Bearer nope"}))
    _refused(auth_on.get(READ, headers={"X-BD-Token": "nope"}))
    _refused(auth_on.get(READ, headers={"Referer": "http://evil.example/"}))


def test_control_the_real_token_works(auth_on):
    assert auth_on.get(READ, headers=_bearer()).status_code == 200
    assert auth_on.get(READ, headers={"X-BD-Token": TOKEN}).status_code == 200
    assert auth_on.post(READ, json={}, headers=_bearer()).status_code != 401


def test_control_a_real_login_session_works_for_reads_and_guarded_writes(auth_on):
    """The whole login: an authenticated client asks for a pairing token; a client with NO credentials redeems it and
    from then on is authenticated by that session alone, CSRF double-submit included."""
    pair = auth_on.get("/api/pair", headers=_bearer())
    assert pair.status_code == 200
    phone = bd_app.app.test_client()
    _refused(phone.get(READ))
    redeemed = phone.post("/api/pair/redeem", json={"token": pair.get_json()["token"]})
    assert redeemed.status_code == 200, redeemed.get_data(as_text=True)[:200]
    sess = _session_cookie(redeemed)
    assert sess and bd_app._session_authenticated(sess)
    assert phone.get(READ).status_code == 200
    csrf = phone.get("/api/csrf")
    assert (
        csrf.status_code == 200
        and csrf.get_json()["csrf_token"] == redeemed.get_json()["csrf_token"]
    )
    assert phone.post(READ, json={}).status_code == 403, (
        "a guarded write without the CSRF token must stay refused"
    )
    written = phone.post(
        READ, json={}, headers={"X-CSRF-Token": csrf.get_json()["csrf_token"]}
    )
    assert written.status_code not in (401, 403), written.status_code


def test_control_the_login_itself_is_reachable_without_credentials_and_refuses_a_bad_token(
    auth_on,
):
    """/api/pair/redeem must be reachable by a client that has nothing yet -- and gives nothing for a wrong, missing or
    spent pairing token (no session cookie, no access)."""
    stranger = bd_app.app.test_client()
    for body, status in [({"token": "wrong"}, 404), ({}, 400)]:
        r = stranger.post("/api/pair/redeem", json=body)
        assert r.status_code == status and not _session_cookie(r), (body, r.status_code)
    _refused(stranger.get(READ))
    token = auth_on.get("/api/pair", headers=_bearer()).get_json()["token"]
    assert stranger.post("/api/pair/redeem", json={"token": token}).status_code == 200
    again = bd_app.app.test_client().post("/api/pair/redeem", json={"token": token})
    assert again.status_code == 404 and not _session_cookie(again)
    with app_state._pairing_lock:
        assert token not in app_state._pairing_tokens


def test_control_asking_for_a_pairing_token_still_needs_authentication(auth_on):
    _refused(auth_on.get("/api/pair"))
    auth_on.get("/")
    _refused(
        auth_on.get("/api/pair", headers={"Host": HOST, "Referer": f"http://{HOST}/"})
    )


def test_control_the_open_paths_stay_open(auth_on):
    assert auth_on.get("/api/health").status_code != 401
    assert auth_on.get("/metrics").status_code != 401


def test_control_an_app_with_no_token_configured_is_unchanged(auth_off):
    """Auth unconfigured = allow all, exactly as before: no credentials, an anonymous session, a Referer."""
    assert auth_off.get(READ).status_code == 200
    assert (
        auth_off.get(
            READ, headers={"Host": HOST, "Referer": f"http://{HOST}/"}
        ).status_code
        == 200
    )
    minted = auth_off.get("/api/csrf")
    assert (
        minted.status_code == 200
        and minted.get_json()["minted"] is True
        and _session_cookie(minted)
    )
    assert auth_off.get(READ).status_code == 200
    written = auth_off.post(
        READ, json={}, headers={"X-CSRF-Token": minted.get_json()["csrf_token"]}
    )
    assert written.status_code not in (401, 403), written.status_code


# ── F3: an Origin that is present but is not ours (amended scope) ────────────────────────────────────────────────────

CROSS = "BD_SEC_6_CROSS_ORIGIN_WRITE_ALLOWED"


def _cross_origin_refused(response):
    assert response.status_code == 403, f"{CROSS}: {response.status_code}"
    assert response.get_json()["error"] == "cross-origin request refused"


@pytest.mark.parametrize(
    "origin",
    [
        "null",
        "NULL",
        "file://",
        "data:text/html,x",
        "about:blank",
        "http://",
        "not a url",
        "http://[::1",
        "//",
    ],
)
def test_f3_an_origin_that_names_no_host_is_refused_on_a_write(auth_off, origin):
    """The finder's repro: POST with 'Origin: null' -> 200 on an app with no token (urlparse gives no hostname, the
    guard fell through). Every Origin that is present and does not name our Host is cross-origin."""
    _cross_origin_refused(
        auth_off.post(READ, json={}, headers={"Host": HOST, "Origin": origin})
    )


def test_f3_a_login_session_does_not_carry_a_null_origin_write(auth_on):
    token = auth_on.get("/api/pair", headers=_bearer()).get_json()["token"]
    phone = bd_app.app.test_client()
    csrf = phone.post("/api/pair/redeem", json={"token": token}).get_json()[
        "csrf_token"
    ]
    _cross_origin_refused(
        phone.post(READ, json={}, headers={"X-CSRF-Token": csrf, "Origin": "null"})
    )


def test_f3_controls_other_origins_and_no_origin_behave_as_before(auth_off):
    _cross_origin_refused(
        auth_off.post(
            READ, json={}, headers={"Host": HOST, "Origin": "http://evil.example"}
        )
    )
    for headers in [
        {"Host": HOST, "Origin": f"http://{HOST}"},
        {"Host": "bd.local", "Origin": "http://bd.local:80"},
        {"Host": "bd.local", "Origin": "https://bd.local:443"},
        {"Host": HOST},
    ]:
        assert auth_off.post(READ, json={}, headers=headers).status_code not in (
            401,
            403,
        ), headers
    assert (
        auth_off.get(READ, headers={"Host": HOST, "Origin": "null"}).status_code == 200
    ), "reads are not guarded"


def test_f3_a_token_request_is_not_an_origin_matter(auth_on):
    r = auth_on.post(READ, json={}, headers=dict(_bearer(), Origin="null"))
    assert r.status_code not in (401, 403)


# ── F4: the Host allow-list (DNS rebinding) ──────────────────────────────────────────────────────────────────────────

REBOUND = "BD_SEC_6_FOREIGN_HOST_SERVED"


def _host_refused(response):
    assert response.status_code == 403, f"{REBOUND}: {response.status_code}"
    assert response.get_json()["error"] == "host not allowed"


@pytest.mark.parametrize(
    "host",
    [
        "evil.example:5555",
        "evil.example",
        "evil.example.",
        "EVIL.Example:5555",
        "127.0.0.1.evil.example",
        "localhost.evil.example:5555",
        "bd.local.evil.example",
        "1.2.3",
        "[evil.example]:5555",
        "[",
        "a.b:c:5555",
    ],
)
def test_f4_a_host_that_is_not_ours_is_refused_before_anything_runs(auth_off, host):
    """The finder's repro: Host + Origin evil.example:<port> -> POST 200 and GET 200 on an app with no token: to the
    browser a rebinding page is same-origin, so only the Host can tell. Refused for reads, writes and the shell."""
    _host_refused(auth_off.get(READ, headers={"Host": host}))
    _host_refused(
        auth_off.post(READ, json={}, headers={"Host": host, "Origin": f"http://{host}"})
    )
    shell = auth_off.get("/", headers={"Host": host})
    _host_refused(shell)
    assert not _session_cookie(shell), (
        "a refused host must not be handed a session cookie"
    )
    _host_refused(auth_off.get("/api/health", headers={"Host": host}))


def test_f4_a_foreign_host_is_refused_even_with_the_real_token(auth_on):
    _host_refused(auth_on.get(READ, headers=dict(_bearer(), Host="evil.example:5555")))


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:5555",
        "10.0.70.164:5555",
        "[::1]:5555",
        "[fe80::1]",
        "localhost",
        "localhost:5555",
        "LOCALHOST.:5555",
        "bd.local:5555",
        "nas.lan",
        "bd.home.arpa:5555",
        "embeddings.localhost",
        "bd.internal",
        "bulkdownloader:5555",
    ],
)
def test_f4_control_addresses_and_private_names_are_served(auth_off, host):
    assert bd_app._host_allowed(host)
    assert auth_off.get(READ, headers={"Host": host}).status_code == 200


def test_f4_control_this_machines_own_name_is_served(auth_off, monkeypatch):
    monkeypatch.setattr(bd_app, "_own_host_names_cache", frozenset({"bd.example.org"}))
    assert (
        auth_off.get(READ, headers={"Host": "bd.example.org:5555"}).status_code == 200
    )
    _host_refused(auth_off.get(READ, headers={"Host": "other.example.org:5555"}))
    monkeypatch.setattr(bd_app, "_own_host_names_cache", None)
    import socket

    assert socket.gethostname().lower().rstrip(".") in bd_app._own_host_names()


@pytest.mark.parametrize(
    "configured",
    [["bd.example.org", ".proxy.example.net"], "bd.example.org, .proxy.example.net"],
)
def test_f4_control_a_name_the_operator_listed_is_served(
    auth_off, monkeypatch, configured
):
    """A reverse-proxy / public DNS name works once listed in app_config "allowed_hosts"; nothing else does."""
    _host_refused(auth_off.get(READ, headers={"Host": "bd.example.org"}))
    monkeypatch.setitem(bd_app._app_cfg, "allowed_hosts", configured)
    assert (
        auth_off.get(READ, headers={"Host": "bd.example.org:8443"}).status_code == 200
    )
    assert (
        auth_off.get(READ, headers={"Host": "a.proxy.example.net"}).status_code == 200
    )
    _host_refused(auth_off.get(READ, headers={"Host": "proxy.example.net"}))
    _host_refused(auth_off.get(READ, headers={"Host": "bd.example.org.evil.example"}))
    _host_refused(auth_off.get(READ, headers={"Host": "evil.example"}))


def test_f4_control_a_request_with_no_host_header_is_not_a_browser(auth_off):
    assert bd_app._host_allowed("") and bd_app._host_allowed("   ")
