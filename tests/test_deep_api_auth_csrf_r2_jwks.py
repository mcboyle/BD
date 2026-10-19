"""deep-api-auth-csrf r2 (O1789; security lens DACS-B REFUTE F1 on r1 + advisory).

F1  oidc.verify_id_token handed whatever the jwks_uri decoded to straight to authlib's jwt.decode as
    the KEY. A JWKS endpoint answering 200 with the JSON string "attacker" made "attacker" an HMAC
    secret, so an HS256 id_token signed with it (iss/aud/nonce matching) logged in as any `sub`.
ADV The algorithm was never pinned: even a well-formed JWKS carrying a symmetric ("oct") key let an
    HS256 id_token verify. An OIDC id_token checked against a JWKS is asymmetric (RS*/PS*/ES*) only.

The IdP is an httpx.MockTransport behind the real guarded_transport seam, as in the r1 test.
"""
from __future__ import annotations

import base64
import time

import httpx
import pytest
from authlib.jose import JsonWebKey, JsonWebToken

from bulk_downloader import oidc, ssrf_transport

BD_GATE_SCOPE = "module"

_ISS = "https://idp.invalid"
_CID = "client-r2"
_NONCE = "nonce-r2"
_JWKS_URI = _ISS + "/jwks"


def _idp(monkeypatch, status, payload):
    def handler(request):
        return httpx.Response(status, json=payload)
    monkeypatch.setattr(ssrf_transport, "guarded_transport", lambda *a, **k: httpx.MockTransport(handler))
    monkeypatch.setattr(oidc, "_DISCO_CACHE", {})
    monkeypatch.setattr(oidc, "oidc_config", lambda: {"issuer": _ISS, "client_id": _CID})


def _claims(sub="victim"):
    now = int(time.time())
    return {"iss": _ISS, "aud": _CID, "sub": sub, "nonce": _NONCE, "iat": now, "exp": now + 300}


def _token(alg, key, kid=None):
    header = {"alg": alg}
    if kid:
        header["kid"] = kid
    return JsonWebToken([alg]).encode(header, _claims(), key).decode()


def _verify(token):
    return oidc.verify_id_token(token, nonce=_NONCE, disco={"jwks_uri": _JWKS_URI})


def _b64(s: bytes) -> str:
    return base64.urlsafe_b64encode(s).rstrip(b"=").decode()


@pytest.mark.parametrize("jwks", ["attacker", ["attacker"], 7, {"kty": "oct", "k": _b64(b"attacker")},
                                  {"keys": "attacker"}, {"keys": {"kty": "oct", "k": _b64(b"attacker")}}])
def test_f1_jwks_that_is_not_a_key_set_is_refused(monkeypatch, jwks):
    _idp(monkeypatch, 200, jwks)
    try:
        got = _verify(_token("HS256", b"attacker"))
    except ValueError:
        return
    except Exception as e:
        raise AssertionError(f"JWKS_STRING_ACCEPTED: {jwks!r} reached jwt.decode -> {type(e).__name__}: {e}") from e
    raise AssertionError(f"JWKS_STRING_ACCEPTED: {jwks!r} verified sub={got.get('sub')!r}")


@pytest.mark.parametrize("shape", ["bare-jwk", "pem-string", "jwk-list", "keys-of-pem"])
def test_f1_jwks_shape_is_checked_even_for_an_asymmetric_token(monkeypatch, shape):
    """The algorithm pin alone would refuse the HS256 cases above; these RS256 tokens verify against the
    key they carry, so only the key-set check can refuse them."""
    key = JsonWebKey.generate_key("RSA", 2048, {"kid": "k1"}, is_private=True)
    pub = key.as_dict(is_private=False)
    pem = key.as_pem(is_private=False).decode()
    jwks = {"bare-jwk": pub, "pem-string": pem, "jwk-list": [pub], "keys-of-pem": {"keys": [pem]}}[shape]
    _idp(monkeypatch, 200, jwks)
    try:
        got = _verify(_token("RS256", key, kid="k1"))
    except ValueError:
        return
    except Exception as e:
        raise AssertionError(f"JWKS_STRING_ACCEPTED: {shape} reached jwt.decode -> {type(e).__name__}: {e}") from e
    raise AssertionError(f"JWKS_STRING_ACCEPTED: {shape} verified sub={got.get('sub')!r}")


def test_adv_hs256_against_a_jwks_oct_key_is_refused(monkeypatch):
    _idp(monkeypatch, 200, {"keys": [{"kty": "oct", "kid": "k1", "k": _b64(b"attacker")}]})
    try:
        got = _verify(_token("HS256", b"attacker", kid="k1"))
    except ValueError:
        return
    except Exception as e:
        # authlib's own refusal of an unlisted alg is UnsupportedAlgorithmError, not ValueError: the
        # pin must surface as one refusal type so the callback answers it like every other bad token.
        raise AssertionError(f"HS256_WITH_JWKS_ACCEPTED: refused as {type(e).__name__}: {e}") from e
    raise AssertionError(f"HS256_WITH_JWKS_ACCEPTED: verified sub={got.get('sub')!r}")


def test_jwks_non_2xx_still_refused(monkeypatch):
    _idp(monkeypatch, 503, {"keys": []})
    with pytest.raises(httpx.HTTPStatusError):
        _verify(_token("HS256", b"attacker"))


@pytest.mark.parametrize("kty,crv_or_size,alg", [("RSA", 2048, "RS256"), ("RSA", 2048, "PS256"),
                                                  ("EC", "P-256", "ES256")])
def test_control_asymmetric_id_token_against_its_jwks_verifies(monkeypatch, kty, crv_or_size, alg):
    key = JsonWebKey.generate_key(kty, crv_or_size, {"kid": "k1"}, is_private=True)
    _idp(monkeypatch, 200, {"keys": [key.as_dict(is_private=False)]})
    got = _verify(_token(alg, key, kid="k1"))
    assert got["sub"] == "victim" and got["nonce"] == _NONCE


def test_control_wrong_signing_key_is_still_refused(monkeypatch):
    signer = JsonWebKey.generate_key("RSA", 2048, {"kid": "k1"}, is_private=True)
    other = JsonWebKey.generate_key("RSA", 2048, {"kid": "k1"}, is_private=True)
    _idp(monkeypatch, 200, {"keys": [other.as_dict(is_private=False)]})
    with pytest.raises(Exception):
        _verify(_token("RS256", signer, kid="k1"))
