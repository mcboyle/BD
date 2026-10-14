"""dev-paired-session-scope: a paired device gets the app API, not /api/dev/* (O1789 HIGH-F1).

A pair_redeem session is a LOGIN (_LOGIN_SESSION_SOURCES), so _check_token and the
dev guard both admitted it: a paired phone could read /api/dev/request_replay_list
(captured X-BD-Token in clear), flip feature flags and read /api/dev/proc. PM spec
(ANSWER-pm-B-dev-paired-scope-1240Z): /api/dev/* admits loopback, the master
bearer / X-BD-Token, or an authenticated session whose source is NOT pair_redeem;
a pair_redeem session gets 403 'dev tools need the master token on a paired device'.

Each repro sits beside the controls that show the probe can say yes: loopback, the
master token (both headers, also on top of the paired cookie), and the generic
refusal for the manual / csrf_bootstrap sessions, which never authenticated.
"""

import pytest

from bulk_downloader import app as bd_app

BD_GATE_SCOPE = "module"

TOKEN = "fixture-dev-paired-scope-token"  # a test fixture value, not a credential
REMOTE = {"REMOTE_ADDR": "10.0.0.98"}
DEV_ROUTES = ["/api/dev/request_replay_list", "/api/dev/feature_flags", "/api/dev/proc"]
PAIRED_REFUSAL = "dev tools need the master token on a paired device"
GENERIC_REFUSAL = "dev endpoints require"

PAIRED_ADMITTED = "PAIRED_DEVICE_SESSION_REACHED_DEV_TOOLS"


@pytest.fixture(params=["with_token", "no_token"])
def client(request, monkeypatch):
    if request.param == "with_token":
        monkeypatch.setenv("BD_AUTH_TOKEN", TOKEN)
    else:
        monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BD_TOKEN", raising=False)
    monkeypatch.setenv("BD_DEV_MODE", "1")
    want = [TOKEN] if request.param == "with_token" else []
    assert bd_app._accepted_tokens() == want, (
        "COULD NOT LOOK: the token is not what the app reads"
    )
    return bd_app.app.test_client()


@pytest.fixture
def with_token(monkeypatch):
    monkeypatch.setenv("BD_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("BD_TOKEN", raising=False)
    monkeypatch.setenv("BD_DEV_MODE", "1")
    assert bd_app._accepted_tokens() == [TOKEN], (
        "COULD NOT LOOK: the token is not what the app reads"
    )
    return bd_app.app.test_client()


def _error(r):
    body = r.get_json(silent=True)
    return body.get("error", "") if isinstance(body, dict) else ""


@pytest.mark.parametrize("route", DEV_ROUTES)
def test_paired_session_is_refused_with_the_master_token_message(client, route):
    client.set_cookie("bd_session", bd_app._session_create(source="pair_redeem"))
    r = client.get(route, environ_base=REMOTE)
    assert r.status_code == 403, f"{PAIRED_ADMITTED}: {route} -> {r.status_code}"
    assert _error(r) == PAIRED_REFUSAL, f"PAIRED_REFUSAL_TEXT: {route} -> {_error(r)!r}"


@pytest.mark.parametrize("source", ["manual", "csrf_bootstrap"])
@pytest.mark.parametrize("route", DEV_ROUTES)
def test_non_login_sessions_keep_the_generic_refusal(client, route, source):
    client.set_cookie("bd_session", bd_app._session_create(source=source))
    r = client.get(route, environ_base=REMOTE)
    if bd_app._accepted_tokens():
        # _check_token refuses before the dev guard: these sessions never logged in.
        assert r.status_code == 401, f"{source} session on a token box: {r.status_code}"
        return
    assert r.status_code == 403, (
        f"{source} session reached dev tools: {route} -> {r.status_code}"
    )
    assert GENERIC_REFUSAL in _error(r) and PAIRED_REFUSAL not in _error(r), (
        f"{source}: {_error(r)!r}"
    )


@pytest.mark.parametrize("route", DEV_ROUTES)
def test_loopback_is_admitted_as_before(client, route):
    client.set_cookie("bd_session", bd_app._session_create(source="pair_redeem"))
    r = client.get(route)  # test-client peer is 127.0.0.1
    assert r.status_code == 200, (
        f"loopback control refused: {route} -> {r.status_code} {_error(r)!r}"
    )


@pytest.mark.parametrize("header", ["Authorization", "X-BD-Token"])
@pytest.mark.parametrize("route", DEV_ROUTES)
@pytest.mark.parametrize("paired_cookie", [False, True])
def test_master_token_is_admitted(with_token, route, header, paired_cookie):
    if paired_cookie:
        with_token.set_cookie(
            "bd_session", bd_app._session_create(source="pair_redeem")
        )
    value = f"Bearer {TOKEN}" if header == "Authorization" else TOKEN
    r = with_token.get(route, environ_base=REMOTE, headers={header: value})
    assert r.status_code == 200, (
        f"master token control refused: {route} {header} -> {r.status_code} {_error(r)!r}"
    )


def test_unauthenticated_remote_keeps_the_generic_refusal(client):
    r = client.get(DEV_ROUTES[0], environ_base=REMOTE)
    if bd_app._accepted_tokens():
        assert r.status_code == 401, (
            f"no-credential remote on a token box: {r.status_code}"
        )
        return
    assert r.status_code == 403 and GENERIC_REFUSAL in _error(r), (
        f"{r.status_code} {_error(r)!r}"
    )
