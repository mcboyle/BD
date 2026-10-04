"""O1815 R12 (P2-4): bd-fixture-gen must scrub response headers and non-media bodies.

Base kept Set-Cookie response headers and response-body secrets verbatim in
trace.sanitized.har and in the routes embedded in the generated pytest file.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

BD_GATE_SCOPE = "module"

_TOOL = Path(__file__).resolve().parents[1] / "toolchain" / "bin" / "bd-fixture-gen"


def _entry(content: dict) -> dict:
    return {
        "request": {"url": "https://api.test.org/v1/me", "headers": []},
        "response": {
            "status": 200,
            "headers": [
                {"name": "Set-Cookie", "value": "sid=SETCOOKIEMARKER; HttpOnly"},
                {"name": "Content-Type", "value": "application/json"},
            ],
            "content": content,
        },
    }


def _generate(tmp_path: Path) -> str:
    body = json.dumps(
        {
            "access_token": "BODYTOKENMARKER",
            "email": "victim@example.com",
            "title": "BENIGNMARKER",
        }
    )
    har = {
        "log": {
            "version": "1.2",
            "entries": [
                _entry({"mimeType": "application/json", "text": body}),
                _entry(
                    {
                        "mimeType": "application/json",
                        "encoding": "base64",
                        "text": base64.b64encode(
                            body.replace("BODYTOKENMARKER", "B64TOKENMARKER").encode()
                        ).decode(),
                    }
                ),
            ],
        }
    }
    src = tmp_path / "in.har"
    src.write_text(json.dumps(har), encoding="utf-8")
    out = tmp_path / "gen"
    proc = subprocess.run(
        [sys.executable, str(_TOOL), str(src), "--out", str(out), "--name", "r12"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    text = (out / "trace.sanitized.har").read_text(encoding="utf-8")
    suite = (out / "test_r12_fixture.py").read_text(encoding="utf-8")
    routes = json.loads(
        base64.b64decode(
            re.search(r'_ROUTES_DATA = "([A-Za-z0-9+/=]+)"', suite).group(1)
        )
    )
    decoded = [
        base64.b64decode(r["body"]).decode() if r["is_base64"] else r["body"]
        for r in routes
    ]
    har_out = json.loads(text)
    decoded += [
        base64.b64decode(e["response"]["content"]["text"]).decode()
        for e in har_out["log"]["entries"]
        if e["response"]["content"].get("encoding") == "base64"
    ]
    return text + suite + "\n".join(decoded)


def test_response_headers_and_bodies_are_scrubbed(tmp_path: Path) -> None:
    blob = _generate(tmp_path)
    # positive control: non-secret body content seen in HAR, routes, and the base64 path
    assert blob.count("BENIGNMARKER") >= 4
    leaked = [
        m
        for m in (
            "SETCOOKIEMARKER",
            "BODYTOKENMARKER",
            "B64TOKENMARKER",
            "victim@example.com",
        )
        if m in blob
    ]
    assert leaked == [], f"O1815-R12 HAR response secrets leaked: {leaked}"


def _run(tmp_path: Path, response: dict) -> tuple[str, str]:
    """Return (sanitized HAR text + decoded HAR bodies, decoded generated-suite route bodies)."""
    har = {
        "log": {
            "entries": [
                {
                    "request": {"url": "https://api.test.org/v1/me", "headers": []},
                    "response": {"status": 200, **response},
                }
            ]
        }
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "in.har"
    src.write_text(json.dumps(har), encoding="utf-8")
    out = tmp_path / "gen"
    proc = subprocess.run(
        [sys.executable, str(_TOOL), str(src), "--out", str(out), "--name", "r12b"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    har_text = (out / "trace.sanitized.har").read_text(encoding="utf-8")
    content = json.loads(har_text)["log"]["entries"][0]["response"]["content"]
    if content.get("encoding") == "base64":
        har_text += "\n" + base64.b64decode(content["text"]).decode()
    suite = (out / "test_r12b_fixture.py").read_text(encoding="utf-8")
    routes = json.loads(
        base64.b64decode(
            re.search(r'_ROUTES_DATA = "([A-Za-z0-9+/=]+)"', suite).group(1)
        )
    )
    bodies = "\n".join(
        base64.b64decode(r["body"]).decode() if r["is_base64"] else r["body"]
        for r in routes
    )
    return har_text, bodies


_HTML = (
    '<input name="access_token" value="HTMLTOKENMARKER">'
    "<input value='HTMLCSRFMARKER' type=hidden name='csrf_token'>"
    '<meta name="csrf-token" content="HTMLMETAMARKER"><p>BENIGNMARKER</p>'
)
_JSON = json.dumps(
    {
        "message": "Authorization: Bearer JSONBEARERMARKER",
        "hint": "use Bearer JSONBAREMARKER next",
        "link": "/cb?access_token=JSONQUERYMARKER&page=2",
        "title": "BENIGNMARKER",
    }
)
_HTML_MARKERS = ("HTMLTOKENMARKER", "HTMLCSRFMARKER", "HTMLMETAMARKER")
_JSON_MARKERS = ("JSONBEARERMARKER", "JSONBAREMARKER", "JSONQUERYMARKER")


def test_set_cookie2_and_redirect_location_are_scrubbed(tmp_path: Path) -> None:
    har_text, bodies = _run(
        tmp_path,
        {
            "headers": [
                {"name": "Set-Cookie2", "value": "sid=COOKIE2MARKER"},
                {"name": "sEt-CoOkIe2", "value": "sid=COOKIE2MARKER"},
                {"name": "SET-COOKIE2", "value": "sid=COOKIE2MARKER"},
                {
                    "name": "Location",
                    "value": "https://x.test/cb?access_token=LOCATIONMARKER&page=2",
                },
                {
                    "name": "Content-Location",
                    "value": "/cb#access_token=LOCATIONMARKER",
                },
            ],
            "redirectURL": "https://x.test/cb?token=LOCATIONMARKER&page=2",
            "content": {"mimeType": "text/plain", "text": "BENIGNMARKER"},
        },
    )
    # positive controls: non-secret query param and body survive
    assert har_text.count("page=2") == 2 and "BENIGNMARKER" in bodies
    leaked = [m for m in ("COOKIE2MARKER", "LOCATIONMARKER") if m in har_text + bodies]
    assert leaked == [], f"O1815-R12 MED1 response header secrets leaked: {leaked}"


def test_credential_shaped_text_is_scrubbed_in_har_and_decoded_routes(
    tmp_path: Path,
) -> None:
    cases = {
        "html": ({"mimeType": "text/html", "text": _HTML}, _HTML_MARKERS),
        "json": ({"mimeType": "application/json", "text": _JSON}, _JSON_MARKERS),
        "plain": ({"mimeType": "text/plain", "text": _JSON}, _JSON_MARKERS),
        "b64html": (
            {
                "mimeType": "text/html",
                "encoding": "base64",
                "text": base64.b64encode(_HTML.encode()).decode(),
            },
            _HTML_MARKERS,
        ),
    }
    leaked = []
    for label, (content, markers) in cases.items():
        har_text, bodies = _run(tmp_path / label, {"content": content})
        # positive control: the benign marker reaches both the HAR and the decoded routes
        assert "BENIGNMARKER" in har_text and "BENIGNMARKER" in bodies, label
        leaked += [f"{label}:har:{m}" for m in markers if m in har_text]
        leaked += [f"{label}:route:{m}" for m in markers if m in bodies]
    assert leaked == [], f"O1815-R12 MED2 credential-shaped text leaked: {leaked}"


def _b64url(raw: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(raw).encode()).decode().rstrip("=")


_JWT_HEAD = _b64url({"alg": "HS256", "typ": "JWT"})
_JWT_BODY = _b64url({"sub": "r12-victim", "sid": "JWTPAYLOADMARKER"})
_JWT_SIG = "JWTSIGMARKER_r12-0123456789abcdefABCDEF"
_JWT = f"{_JWT_HEAD}.{_JWT_BODY}.{_JWT_SIG}"
# fake secrets assembled at runtime so the source never carries a scanner-shaped literal (O1870)
_AWS_KEY_ID = "".join(("AK", "IA", "R12LEAK", "MARKER0XY"))
_BEARER_TOKEN = "".join(("R12BEARER", "SHAPE", "MARKER", ".abc-def_ghi"))
_SHAPE_MARKERS = {
    "jwt": _JWT,
    "jwt-payload": _JWT_BODY,
    "jwt-sig": _JWT_SIG,
    "aws": _AWS_KEY_ID,
    "bearer": _BEARER_TOKEN,
}
# negative control: a non-secret base64 blob of JWT-like length (it even starts with eyJ, no dots)
_BENIGN_B64 = base64.b64encode(
    json.dumps(
        {
            "thumb": "r12-benign",
            "w": 640,
            "h": 360,
            "frames": list(range(10)),
            "codec": "avc1",
        }
    ).encode()
).decode()
_SHAPE_TEXT = (
    f"session ready {_JWT} key id {_AWS_KEY_ID} send Bearer {_BEARER_TOKEN} "
    f"preview {_BENIGN_B64} BENIGNMARKER"
)
_SHAPE_JSON = json.dumps(
    {
        "message": f"session ready {_JWT}",
        "detail": f"key id {_AWS_KEY_ID}",
        "hint": f"send Bearer {_BEARER_TOKEN}",
        "preview": _BENIGN_B64,
        "title": "BENIGNMARKER",
    }
)
_SHAPE_HTML = f"<p>{_SHAPE_TEXT}</p><span data-x='{_JWT}'>{_AWS_KEY_ID}</span>"


def test_unlabeled_credential_shapes_are_scrubbed_in_har_and_decoded_routes(
    tmp_path: Path,
) -> None:
    assert abs(len(_BENIGN_B64) - len(_JWT)) <= 40, (len(_BENIGN_B64), len(_JWT))
    cases = {
        "json": {"mimeType": "application/json", "text": _SHAPE_JSON},
        "plain": {"mimeType": "text/plain", "text": _SHAPE_TEXT},
        "html": {"mimeType": "text/html", "text": _SHAPE_HTML},
        "b64json": {
            "mimeType": "application/json",
            "encoding": "base64",
            "text": base64.b64encode(_SHAPE_JSON.encode()).decode(),
        },
        "b64html": {
            "mimeType": "text/html",
            "encoding": "base64",
            "text": base64.b64encode(_SHAPE_HTML.encode()).decode(),
        },
    }
    leaked = []
    for label, content in cases.items():
        har_text, bodies = _run(tmp_path / label, {"content": content})
        # positive + negative controls: benign text and the non-secret base64 blob survive both outputs
        for out, text in (("har", har_text), ("route", bodies)):
            assert "BENIGNMARKER" in text, (label, out)
            assert _BENIGN_B64 in text, f"benign base64 blob scrubbed: {label}:{out}"
        leaked += [
            f"{label}:har:{k}" for k, m in _SHAPE_MARKERS.items() if m in har_text
        ]
        leaked += [
            f"{label}:route:{k}" for k, m in _SHAPE_MARKERS.items() if m in bodies
        ]
    assert leaked == [], f"O1815-R12 R3 unlabeled credential shapes leaked: {leaked}"


def _b64url_raw(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _signed_jwt(header_json: str, payload_json: str) -> tuple[str, str, str]:
    """A valid HS256 JWT built from exact JSON text (whitespace kept); returns (token, payload, sig)."""
    signing_input = f"{_b64url_raw(header_json.encode())}.{_b64url_raw(payload_json.encode())}"
    sig = _b64url_raw(hmac.new(b"r12-key", signing_input.encode(), hashlib.sha256).digest())
    return f"{signing_input}.{sig}", signing_input.split(".")[1], sig


# R4 (cx4 r3 MED1): valid JSON whitespace after '{' moves the base64url prefix off eyJ
_SPACED_JWT = _signed_jwt('{"alg":"HS256","typ":"JWT"}', '{ "sub": "r12-victim", "sid": "SPACEDMARKER" }')
_NEWLINE_JWT = _signed_jwt('{ "alg": "HS256" }', '{\n  "sid": "NEWLINEMARKER"\n}')
_ANY_PAYLOAD_MARKERS = {
    f"{name}-{part}": value
    for name, jwt in (("spaced", _SPACED_JWT), ("newline", _NEWLINE_JWT))
    for part, value in zip(("token", "payload", "sig"), jwt)
}
_DOTTED_BENIGN = "eyes.example.org v1.2.3"
_ANY_TEXT = (
    f"session {_SPACED_JWT[0]} and {_NEWLINE_JWT[0]} "
    f"preview {_BENIGN_B64} host {_DOTTED_BENIGN} BENIGNMARKER"
)
_ANY_JSON = json.dumps(
    {
        "message": f"session {_SPACED_JWT[0]}",
        "detail": _NEWLINE_JWT[0],
        "preview": _BENIGN_B64,
        "host": _DOTTED_BENIGN,
        "title": "BENIGNMARKER",
    }
)
_ANY_HTML = f"<p>{_ANY_TEXT}</p><span data-x='{_SPACED_JWT[0]}'>{_NEWLINE_JWT[0]}</span>"


def test_signed_jwt_with_any_payload_is_scrubbed_in_har_and_decoded_routes(
    tmp_path: Path,
) -> None:
    # premise: these are the non-eyJ payload / header prefixes that escaped r3
    assert _SPACED_JWT[1].startswith("eyAi"), _SPACED_JWT[1]
    assert _NEWLINE_JWT[0].startswith("eyAi") and _NEWLINE_JWT[1].startswith("ewo"), _NEWLINE_JWT
    cases = {
        "json": {"mimeType": "application/json", "text": _ANY_JSON},
        "plain": {"mimeType": "text/plain", "text": _ANY_TEXT},
        "html": {"mimeType": "text/html", "text": _ANY_HTML},
        "b64json": {
            "mimeType": "application/json",
            "encoding": "base64",
            "text": base64.b64encode(_ANY_JSON.encode()).decode(),
        },
        "b64html": {
            "mimeType": "text/html",
            "encoding": "base64",
            "text": base64.b64encode(_ANY_HTML.encode()).decode(),
        },
    }
    leaked = []
    for label, content in cases.items():
        har_text, bodies = _run(tmp_path / label, {"content": content})
        # negative controls: benign text, the dotless base64 blob and dotted non-JWT text survive
        for out, text in (("har", har_text), ("route", bodies)):
            assert "BENIGNMARKER" in text, (label, out)
            assert _BENIGN_B64 in text, f"benign base64 blob scrubbed: {label}:{out}"
            assert _DOTTED_BENIGN in text, f"dotted non-JWT text scrubbed: {label}:{out}"
        leaked += [
            f"{label}:har:{k}" for k, m in _ANY_PAYLOAD_MARKERS.items() if m in har_text
        ]
        leaked += [
            f"{label}:route:{k}" for k, m in _ANY_PAYLOAD_MARKERS.items() if m in bodies
        ]
    assert leaked == [], f"O1815-R12 R4 any-payload signed JWT leaked: {leaked}"


# R5 (cx11 r4 MED1): a signed JWT in a non-sensitive query key of a response URL kept by key alone
_STATE_JWT = _signed_jwt('{"alg":"HS256","typ":"JWT"}', '{ "sub": "r12-victim", "sid": "STATEMARKER" }')
_STATE_URL = (
    f"https://x.test/cb?state={_STATE_JWT[0]}&page=URLBENIGNMARKER&host={_DOTTED_BENIGN}"
)


def test_signed_jwt_in_response_url_query_value_is_scrubbed(tmp_path: Path) -> None:
    assert _STATE_JWT[1].startswith("eyAi"), _STATE_JWT[1]
    cookie = {"name": "Set-Cookie", "value": "session=COOKIECONTROLMARKER"}
    content = {"mimeType": "text/plain", "text": "BENIGNMARKER"}
    cases = {
        "location": {"headers": [cookie, {"name": "Location", "value": _STATE_URL}]},
        "content-location": {
            "headers": [cookie, {"name": "Content-Location", "value": _STATE_URL}]
        },
        "redirectURL": {"headers": [cookie], "redirectURL": _STATE_URL},
    }
    leaked = []
    for label, response in cases.items():
        har_text, bodies = _run(tmp_path / label, {**response, "content": content})
        har_text = urllib.parse.unquote_plus(har_text)
        # controls: benign query values and body survive, Set-Cookie still dropped
        assert "page=URLBENIGNMARKER" in har_text, f"benign query value scrubbed: {label}"
        assert f"host={_DOTTED_BENIGN}" in har_text, f"dotted benign value scrubbed: {label}"
        assert "BENIGNMARKER" in bodies, label
        assert "COOKIECONTROLMARKER" not in har_text, f"Set-Cookie kept: {label}"
        leaked += [
            f"{label}:{out}:{part}"
            for out, text in (("har", har_text), ("route", bodies))
            for part, value in zip(("token", "payload", "sig"), _STATE_JWT)
            if value in text
        ]
    assert leaked == [], f"O1815-R12 R5 signed JWT in response URL query leaked: {leaked}"
