"""pair-admission (O1789 raw-editors-dev-auth, R174-A q2): a pairing code is a login waiting to happen.

POST /api/pair/redeem turns a code into a "pair_redeem" session, which _session_authenticated counts as a login and
the /api/dev/* guard admits. On a box with no token configured _check_token allows every /api call, so GET /api/pair
minted a code for anyone: remote, token-less, two requests -> /api/dev admitted. A code is now minted only for a
loopback peer, a valid token, or a caller that already holds a login session; a redeem can only complete a pairing
one of those started.

Each refusal has a control that shows the probe can say yes: the operator's loopback mint still pairs a remote phone
that then reaches /api/dev, a paired session can mint again, and a token holder can mint.
"""

import pytest

from bulk_downloader import app as bd_app
from bulk_downloader import dev_tools

BD_GATE_SCOPE = "module"
TOKEN = "fixture-pair-admission-token"  # a test fixture value, not a credential
REMOTE = {"REMOTE_ADDR": "10.0.0.99"}
DEV = "/api/dev/runs"
DIAG = "PAIR_ADMISSION_REMOTE_MINT"


@pytest.fixture
def no_token(monkeypatch):
    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BD_TOKEN", raising=False)
    monkeypatch.setattr(dev_tools, "is_dev_mode", lambda: True)
    assert bd_app._accepted_tokens() == [], "COULD NOT LOOK: a token is configured in this environment"


@pytest.fixture
def with_token(monkeypatch):
    monkeypatch.setenv("BD_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("BD_TOKEN", raising=False)
    monkeypatch.setattr(dev_tools, "is_dev_mode", lambda: True)
    assert bd_app._accepted_tokens() == [TOKEN], "COULD NOT LOOK: the token is not what the app reads"


def _client():
    return bd_app.app.test_client()


def _mint(client, **kw):
    r = client.get("/api/pair", **kw)
    return r, ((r.get_json(silent=True) or {}).get("token") or "")


def _refused_mint(r, code, how):
    assert r.status_code == 403 and not code, (
        f"{DIAG}: {how} GET /api/pair -> {r.status_code}, pairing code issued={bool(code)}")


def test_q2_remote_tokenless_mint_redeem_never_reaches_dev(no_token):
    """R174-A q2: remote token-less GET /api/pair -> redeem -> /api/dev. Every step must refuse."""
    phone = _client()
    assert phone.get(DEV, environ_base=REMOTE).status_code == 403, (
        "precondition: the dev guard already admits a remote anonymous caller; q2 cannot measure the mint")
    r, code = _mint(phone, environ_base=REMOTE)
    redeem = phone.post("/api/pair/redeem", json={"token": code or "none"}, environ_base=REMOTE)
    dev = phone.get(DEV, environ_base=REMOTE)
    assert dev.status_code == 403, (
        f"{DIAG}: remote token-less caller reached /api/dev via pair {r.status_code} -> "
        f"redeem {redeem.status_code} -> dev {dev.status_code}")
    _refused_mint(r, code, "remote token-less")


@pytest.mark.parametrize("headers", [
    {"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"},
    {"Host": "bd.local:5555", "Referer": "http://bd.local:5555/", "Origin": "http://bd.local:5555"},
], ids=["forwarded-loopback-headers", "same-origin-headers"])
def test_remote_headers_do_not_buy_a_mint(no_token, headers):
    """Client-written headers are not a peer address and not a login."""
    r, code = _mint(_client(), environ_base=REMOTE, headers=headers)
    _refused_mint(r, code, f"remote token-less with {sorted(headers)}")


def test_the_anonymous_bootstrap_session_does_not_buy_a_mint(no_token):
    """GET / hands anyone a csrf_bootstrap bd_session; it proves nothing about the caller."""
    phone = _client()
    phone.get("/", environ_base=REMOTE)
    r, code = _mint(phone, environ_base=REMOTE)
    _refused_mint(r, code, "remote token-less holding the GET / session")


def test_a_redeem_with_no_minted_code_admits_nothing(no_token):
    phone = _client()
    redeem = phone.post("/api/pair/redeem", json={"token": "never-minted"}, environ_base=REMOTE)
    assert redeem.status_code == 404
    assert phone.get(DEV, environ_base=REMOTE).status_code == 403


def test_control_loopback_mint_pairs_a_remote_phone_that_reaches_dev(no_token):
    """The QR flow: the operator mints on the box, the phone redeems over the LAN and is signed in."""
    r, code = _mint(_client())                    # test client peer = 127.0.0.1
    assert r.status_code == 200 and code, f"loopback mint refused: {r.status_code}"
    phone = _client()
    assert phone.get(DEV, environ_base=REMOTE).status_code == 403
    redeem = phone.post("/api/pair/redeem", json={"token": code}, environ_base=REMOTE)
    assert redeem.status_code == 200
    assert phone.get(DEV, environ_base=REMOTE).status_code == 200


def test_control_a_paired_session_can_mint_again(no_token):
    _, code = _mint(_client())
    phone = _client()
    assert phone.post("/api/pair/redeem", json={"token": code}, environ_base=REMOTE).status_code == 200
    r, again = _mint(phone, environ_base=REMOTE)
    assert r.status_code == 200 and again and again != code


def test_control_a_token_holder_can_mint_and_a_tokenless_caller_cannot(with_token):
    r, code = _mint(_client(), environ_base=REMOTE, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200 and code
    r, code = _mint(_client(), environ_base=REMOTE, headers={"X-BD-Token": TOKEN})
    assert r.status_code == 200 and code
    r, code = _mint(_client(), environ_base=REMOTE)
    assert r.status_code == 401 and not code
