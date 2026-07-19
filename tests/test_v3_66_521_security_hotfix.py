"""v3.66.521 security hotfix -- RED-first regression suite.

Covers the four defects closed by the 521 cut (see VERIFY_MATRIX_v3_66_520.md):

  VR-P01 (HIGH)  notify_apprise.send(): every TAGGED runtime notification is
                 silently dropped -- add(u) attaches no tag, notify(tag=event)
                 then matches zero services. Driven through the REAL send()/
                 _fire() path against a live in-process webhook receiver (not a
                 mock of _fire).
  VR-P02 (HIGH)  app_settings_center._mask_secrets(): non-recursive masking
                 returns accounts[].password (and any nested secret) raw on the
                 read endpoints. Flat top-level secrets must STILL mask.
  VR-P03 (MED-H) capture_artifact_redact._kv_key_is_secret(): the hand-kept
                 _KV_SECRET_KEYWORDS copy drifted from the SoT SENSITIVE_QS_KEY,
                 so OAuth '#code=...&state=...' fragment secrets (and hash/
                 expires/policy/apikey/captcha/...) survived the floor. Fix makes
                 the kv check consult SENSITIVE_QS_KEY so the two can't drift.
  VR-P05 (MED)   notify_apprise.validate_urls(): references the removed
                 apprise.AppriseURLBase (gone in apprise>=1.7) -> every URL
                 rejected.

Zero-arg test functions; no pytest fixtures (also runs under run_tests.py).
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from bulk_downloader import notify_apprise as notify
from bulk_downloader import app_settings_center as sc
from bulk_downloader import capture_artifact_redact as car


# ---- shared: a throwaway in-process webhook receiver for the apprise path ----

class _CaptureHandler(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):  # noqa: N802
        ln = int(self.headers.get("Content-Length", "0") or 0)
        if ln:
            self.rfile.read(ln)
        type(self).received.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):  # quiet
        pass


def _start_receiver():
    """Start a fresh loopback receiver; return (srv, url, received_list)."""
    handler = type("H", (_CaptureHandler,), {"received": []})
    srv = HTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"json://127.0.0.1:{port}", handler.received


def _deliveries_after(fire, timeout=5.0):
    """Run `fire()` (which drives a real apprise send) and count POSTs."""
    srv, url, received = _start_receiver()
    try:
        fire(url)
        # send() runs notify on a worker thread w/ its own join timeout; give the
        # loopback POST a beat to land.
        deadline = time.time() + timeout
        while not received and time.time() < deadline:
            time.sleep(0.05)
        return len(received)
    finally:
        srv.shutdown()


# ----------------------------- VR-P01 -----------------------------

def test_p01_tagged_notification_is_delivered_via_real_send():
    """A tagged event (the runtime path) must actually deliver. On pristine
    source add(u) has no tag so notify(tag=event) drops it -> 0 delivered."""
    if not notify.is_available():  # apprise absent -> nothing to assert
        return
    n = _deliveries_after(
        lambda url: notify.send([url], title="t", body="b", tag="download_complete")
    )
    assert n >= 1, "tagged notification was dropped (VR-P01)"


def test_p01_dispatcher_fire_delivers_tagged_event():
    """Drive the real NotificationDispatcher._fire path (not a mock of _fire)."""
    if not notify.is_available():
        return

    def fire(url):
        d = notify.NotificationDispatcher()
        d._urls = [url]
        d._fire("title", "body", tag="download_complete")

    assert _deliveries_after(fire) >= 1, "dispatcher._fire dropped a tagged event (VR-P01)"


# ----------------------------- VR-P05 -----------------------------

def test_p05_validate_urls_accepts_a_valid_url():
    """A well-formed apprise URL must validate ok=True. Pristine source hits the
    removed apprise.AppriseURLBase and rejects everything."""
    if not notify.is_available():
        return
    res = notify.validate_urls(["json://localhost:8765/path"])
    assert len(res) == 1
    assert res[0].ok is True, f"valid URL rejected (VR-P05): {res[0].error}"


def test_p05_validate_urls_does_not_reference_removed_apprise_symbol():
    """Guard the specific regression: no AttributeError on AppriseURLBase."""
    if not notify.is_available():
        return
    res = notify.validate_urls(["tgram://123456789:abcDEFtoken/987654321"])
    assert res, "validate_urls returned nothing"
    assert "AppriseURLBase" not in (res[0].error or ""), "still references removed symbol (VR-P05)"


# ----------------------------- VR-P02 -----------------------------

def test_p02_nested_account_password_is_masked():
    """accounts[].password must not be returned raw (VR-P02)."""
    masked = sc._mask_secrets({"accounts": [{"username": "u", "password": "RAWPW"}]})
    assert "RAWPW" not in json.dumps(masked), "nested account password leaked (VR-P02)"


def test_p02_deeply_nested_secret_is_masked():
    masked = sc._mask_secrets({"outer": {"inner": {"api_key": "RAWKEY"}}})
    assert "RAWKEY" not in json.dumps(masked), "deeply nested secret leaked (VR-P02)"


def test_p02_flat_secret_still_masks_presence_only():
    """The recursion must NOT break the existing flat-secret behavior."""
    masked = sc._mask_secrets({"password": "TOP", "username": "keepme"})
    assert masked.get("password") == {"present": True}, "flat secret masking regressed"
    assert masked.get("username") == "keepme", "non-secret scalar altered"


def test_p02_nonsecret_nested_values_preserved():
    masked = sc._mask_secrets({"accounts": [{"username": "alice", "rotate_every": 5}]})
    assert masked["accounts"][0]["username"] == "alice"
    assert masked["accounts"][0]["rotate_every"] == 5


# ----------------------------- VR-P03 -----------------------------

def test_p03_oauth_fragment_code_state_redacted():
    """An OAuth redirect '#code=...&state=...' in a free string must be scrubbed
    by the floor (VR-P03 -- live exchangeable credential into capture.json)."""
    cap = {"action_timeline": [
        {"redirect": "https://auth.example.com/cb#code=SECRETAUTHCODE&state=NONCEVAL"}
    ]}
    out = json.dumps(car.redact_capture(cap))
    assert "SECRETAUTHCODE" not in out, "OAuth auth code survived redaction (VR-P03)"
    assert "NONCEVAL" not in out, "OAuth state survived redaction (VR-P03)"


def test_p03_kv_secret_is_credential_subset_of_sot():
    """The kv floor scrubs the CREDENTIAL class only. Its query-secret portion is
    *derived from* the SoT ``SENSITIVE_QS_KEY`` and is a strict SUBSET of it (the
    floor can never scrub a query key the SoT would not flag -> no unsafe drift),
    which is what closes the OAuth-fragment leak. Signing-METADATA-only keys
    (expires/policy/hash/x-amz-date) that a keep_full surface legitimately retains
    must NOT be scrubbed by the always-on floor -- a wholesale delegation regressed
    exactly this (test_v3_66_245). Three header-origin credential markers
    (csrf/xsrf/bearer) that the original floor scrubbed but the query SoT never
    carried are preserved explicitly."""
    from bulk_downloader.capture_redact import SENSITIVE_QS_KEY
    # query-secret credentials: the VALUE is the secret -> floor scrubs even under
    # keep_full; each is also flagged by the query SoT (the derived subset).
    sot_credential = [
        "code", "state", "token", "apikey", "captcha", "challenge", "nonce",
        "otp", "password", "sig", "signature", "secret", "auth", "session",
        "sid", "jwt", "credential",
        "X-Amz-Signature", "X-Amz-Security-Token",  # SigV4 real secrets
    ]
    for k in sot_credential:
        assert car._kv_key_is_secret(k) is True, \
            f"credential {k!r} not scrubbed by the kv floor (VR-P03 leak)"
        assert SENSITIVE_QS_KEY.search(k), f"SoT should also flag credential {k!r}"
    # header-origin extras: scrubbed by the floor, NOT in the query SoT (preserved
    # from the original tuple so prior coverage isn't silently dropped).
    for k in ["csrf", "xsrf", "bearer"]:
        assert car._kv_key_is_secret(k) is True, \
            f"header-origin credential {k!r} dropped from the kv floor"
    # signing-metadata-only: NOT a secret on its own -> kept by the always-on floor
    # (stripped only by the gated signed-query pass; keep_full retains them).
    signing_meta = ["expires", "policy", "hash",
                    "X-Amz-Expires", "X-Amz-Date", "X-Amz-Algorithm"]
    for k in signing_meta:
        assert car._kv_key_is_secret(k) is False, \
            f"signing-metadata {k!r} over-scrubbed by the floor (breaks keep_full / VR-P03)"
    # subset invariant for the DERIVED portion: any non-extra key the floor scrubs,
    # the query SoT also flags (csrf/xsrf/bearer are the known header-origin set).
    extra = ("csrf", "xsrf", "bearer")
    benign = ["username", "barcode", "geocode", "estate", "make", "network"]
    for k in sot_credential + signing_meta + benign:
        if car._kv_key_is_secret(k) and not any(e in k.lower() for e in extra):
            assert SENSITIVE_QS_KEY.search(k), \
                f"floor scrubs {k!r} but the query SoT does not -- unsafe drift (VR-P03)"
    # benign keys: neither the floor nor the SoT treats them as secret.
    for k in benign:
        assert car._kv_key_is_secret(k) is False, f"benign {k!r} wrongly scrubbed"


def test_p03_anchored_oauth_keys_are_secret_substrings_are_not():
    assert car._kv_key_is_secret("code") is True
    assert car._kv_key_is_secret("state") is True
    # anchored: must not catch these by substring
    assert car._kv_key_is_secret("barcode") is False
    assert car._kv_key_is_secret("geocode") is False
