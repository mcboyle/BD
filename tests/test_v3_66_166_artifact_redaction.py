"""F2 / wave 166 — derivation-boundary redaction of capture-DERIVED artifacts.

Proves that capture-derived material (template drafts + normalized review
candidates) cannot persist secrets: cookies, tokens, Authorization-style
values, signed-URL query strings, URL/authority userinfo, session ids, emails,
and credentials are redacted, while structure-only evidence survives
(hostnames, counts, status codes, content types, endpoint/media templates,
selector shapes, capture SHA-256).

ALL fixtures are synthetic. No real WACZ / capture JSON is read or committed.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

from bulk_downloader.capture_artifact_redact import (  # noqa: E402
    PLACEHOLDER,
    redact_artifact,
    redact_value,
    scan_artifact_secrets,
)
import build_template_from_wacz as b  # noqa: E402
from bulk_downloader.template_normalize import normalize_draft  # noqa: E402

# ── Synthetic secrets (clearly fake; never sourced from a real capture) ──────
SYN_EMAIL = "member.test@example.com"
SYN_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF_-ghiJKLmnop"
SYN_TURNSTILE = "0.synthFAKEturnstileTOKEN-_value." + "Zz9" * 30  # long opaque
SYN_SIGNED = ("https://cdn.example.com/media/clip/AVC_1080.mp4"
              "?Expires=1700000000&Signature=SYNTHsigVALUE&Key-Pair-Id=APKAID")
SYN_COOKIE = "sessionid=SYNTHsess9999; csrftoken=SYNTHcsrfABCD; theme=dark"
SYN_USERINFO_URL = "https://syntheticuser:synthpass@portal.example.com/account"
SYN_BARE_AUTHORITY = "syntheticuser:synthpass@portal.example.com"


# ── Unit: each secret class is redacted; placeholder left; scans clean ───────
def test_email_redacted() -> None:
    out = redact_value(f"logged in as {SYN_EMAIL} ok")
    assert SYN_EMAIL not in out and PLACEHOLDER in out
    assert scan_artifact_secrets(out) == []


def test_jwt_redacted() -> None:
    out = redact_value(f"Authorization: Bearer {SYN_JWT}")
    assert SYN_JWT not in out
    assert scan_artifact_secrets(out) == []


def test_turnstile_opaque_token_redacted() -> None:
    out = redact_value(SYN_TURNSTILE)
    assert out == PLACEHOLDER
    assert scan_artifact_secrets(out) == []


def test_signed_url_query_redacted_path_kept() -> None:
    out = redact_value(SYN_SIGNED)
    assert "Signature=SYNTHsigVALUE" not in out
    assert "APKAID" not in out
    # host + path (the reusable endpoint shape) survive
    assert "cdn.example.com/media/clip/AVC_1080.mp4" in out
    assert scan_artifact_secrets(out) == []


def test_cookie_pairs_redacted() -> None:
    out = redact_value(SYN_COOKIE)
    assert "SYNTHsess9999" not in out and "SYNTHcsrfABCD" not in out
    # non-secret pair survives (structure)
    assert "theme=dark" in out
    assert scan_artifact_secrets(out) == []


def test_url_userinfo_stripped() -> None:
    out = redact_value(SYN_USERINFO_URL)
    assert "syntheticuser" not in out and "synthpass" not in out
    assert "portal.example.com/account" in out
    assert scan_artifact_secrets(out) == []


def test_bare_authority_userinfo_stripped() -> None:
    out = redact_value(SYN_BARE_AUTHORITY)
    assert "synthpass" not in out
    assert out.endswith("portal.example.com")
    assert scan_artifact_secrets(out) == []


# ── Unit: structure-only evidence is PRESERVED (value-content, not key-name) ─
def test_selector_shapes_preserved() -> None:
    # Keys named email/password but the VALUES are selector shapes, not secrets.
    art = {
        "selectors": {
            "login": {"email": "input#email",
                      "password": 'input[type="password"]'},
            "download": {"button_hint": '[aria-label*="Download" i]'},
        }
    }
    out = redact_artifact(art)
    assert out == art               # untouched
    assert scan_artifact_secrets(out) == []


def test_structure_only_fields_preserved() -> None:
    art = {
        "match": {"hosts": ["app.example.com"],
                  "url_patterns": ["^https://app.example.com/"]},
        "network_discovery": {
            "top_hosts": [{"host": "api.example.com", "count": 12}],
            "api_patterns": ["/api/v{version}/movie/{movie_id}/"
                             "download-resolution/{resolution}"],
            "media_patterns": [".../AVC_{resolution}.mp4", ".../{manifest}.m3u8"],
            "resolutions_seen": [2160, 1080, 720],
            "status_counts": {"200": 30, "206": 5},
            "content_type_counts": {"video/mp4": 9, "application/json": 3},
        },
        "source": {"capture_sha256": "a" * 64, "dom_log_count": 4682},
    }
    out = redact_artifact(art)
    assert out == art               # nothing structural is touched
    assert scan_artifact_secrets(out) == []


def test_capture_sha256_not_mistaken_for_token() -> None:
    sha = "823cb97af7874fbc352abc50a9257b9e2e7afcdf05f0fafea3ed3bdff35c54d3"
    assert redact_value(sha) == sha          # 64-hex hash preserved
    assert scan_artifact_secrets(sha) == []


def test_redaction_idempotent() -> None:
    art = {"a": SYN_EMAIL, "b": SYN_SIGNED, "c": SYN_COOKIE, "d": SYN_TURNSTILE}
    once = redact_artifact(art)
    twice = redact_artifact(once)
    assert once == twice
    assert scan_artifact_secrets(twice) == []


# ── The scanner DOES flag unredacted material (negative control) ─────────────
def test_scanner_flags_unredacted() -> None:
    dirty = {"label": SYN_EMAIL, "u": SYN_USERINFO_URL,
             "j": SYN_JWT, "url": SYN_SIGNED, "tok": SYN_TURNSTILE}
    kinds = {kind for _, kind in scan_artifact_secrets(dirty)}
    assert {"email", "userinfo", "jwt", "signed_url", "opaque_token"} <= kinds


# ── End-to-end: synthetic WACZ with planted secrets -> build_template ────────
def _make_wacz(capture: dict) -> Path:
    d = Path(tempfile.mkdtemp())
    wacz = d / "synthetic.wacz"
    with zipfile.ZipFile(wacz, "w") as z:
        z.writestr("archive/capture.json", json.dumps(capture))
    return wacz


def _dirty_capture() -> dict:
    html = (
        '<input id="email"><input id="password" type="password">'
        '<button type="submit" name="submit">Login</button>'
        '<div aria-label="video player"><button class="vjs-big-play-button">'
        '</button></div><button aria-label="Download video">Download</button>'
        f'<!-- session debris: {SYN_EMAIL} {SYN_TURNSTILE} -->'
    )
    return {
        # URL itself carries userinfo + a signed query (worst case)
        "url": f"https://syntheticuser:synthpass@app.example.com/clip/9?token={SYN_JWT}",
        "origin": SYN_USERINFO_URL,
        "host": "app.example.com",
        "captured_at": "2026-06-08T00:00:00Z",
        "dom_log_count": 100,
        "network_log_count": 3,
        "cookies": SYN_COOKIE,
        "dom_log": [
            {"type": "full_snapshot", "html": html,
             "label": f"page for {SYN_EMAIL}"},
        ],
        "network_log": [
            {"url": SYN_SIGNED, "response_status": 200,
             "response_headers": [{"name": "content-type", "value": "video/mp4"}]},
            {"url": "https://api.example.com/api/v1/movie/9/download-resolution/1080",
             "response_status": 200,
             "response_headers": [{"name": "content-type",
                                   "value": "application/json"}]},
        ],
    }


def test_build_template_persists_no_secrets() -> None:
    wacz = _make_wacz(_dirty_capture())
    try:
        draft = b.build_template(wacz)
    finally:
        shutil.rmtree(wacz.parent, ignore_errors=True)

    # 1. structural proof: scanner finds nothing
    assert scan_artifact_secrets(draft) == []

    # 2. literal proof: no planted secret survives serialization
    blob = json.dumps(draft)
    for secret in ("synthpass", "SYNTHsigVALUE", "APKAID", "SYNTHsess9999",
                   "SYNTHcsrfABCD", SYN_EMAIL, SYN_JWT, SYN_TURNSTILE):
        assert secret not in blob, f"leaked: {secret}"

    # 3. useful structure survives the scrub
    assert "app.example.com" in draft["match"]["hosts"]
    assert draft["selectors"]["login"]["email"] == "input#email"
    assert draft["selectors"]["login"]["password"] == "input#password"
    nd = draft["network_discovery"]
    assert ".../AVC_{resolution}.mp4" in nd["media_patterns"]
    assert 1080 in nd["resolutions_seen"]
    assert nd["content_type_counts"].get("video/mp4")


def test_normalize_candidate_persists_no_secrets() -> None:
    wacz = _make_wacz(_dirty_capture())
    try:
        draft = b.build_template(wacz)
    finally:
        shutil.rmtree(wacz.parent, ignore_errors=True)
    candidate = normalize_draft(draft)
    assert scan_artifact_secrets(candidate) == []
    blob = json.dumps(candidate)
    for secret in ("synthpass", "SYNTHsigVALUE", SYN_EMAIL, SYN_JWT, SYN_TURNSTILE):
        assert secret not in blob, f"leaked: {secret}"
    # never auto-enabled
    assert candidate["status"] in ("review_ready", "draft_review_required")


def test_normalize_scrubs_unscrubbed_flat_draft() -> None:
    # A hand-authored / flat draft that did NOT pass through build_template.
    flat = {
        "schema_version": "x",
        "host": "app.example.com",
        "selectors": {"download": {"button_hint": "text=/Download/i"}},
        "network_patterns": [SYN_SIGNED],
        "resolutions": [1080],
        "source": {"capture_file": f"note-{SYN_EMAIL}.wacz",
                   "dom_log_count": 1, "network_log_count": 1},
    }
    candidate = normalize_draft(flat)
    assert scan_artifact_secrets(candidate) == []
    assert SYN_EMAIL not in json.dumps(candidate)
