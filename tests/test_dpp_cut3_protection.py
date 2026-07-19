"""D++ cut 3 (Layer C) — protection tagging, recognized from the NETWORK log +
cookies + page markup. DETECTION ONLY (F2): names / shapes / scheme tags —
NEVER a token, signed-URL, or cookie VALUE.

NEW pure `player_recognition.recognize_protection(network_log, *, cookies, html,
script_srcs)`:
  {
    "signing": {"schemes":[...], "param_names":[...], "hosts":[url_shape]},
    "token_refresh": [url_shape],
    "anti_bot": [sorted vendors],
    "anti_bot_signals": {vendor: [signal names]},
    "captcha": [sorted: turnstile|hcaptcha|recaptcha],
    "drm": bool, "drm_reasons":[...], "drm_license_hosts":[url_shape],
    "header_preconditions": [{"url_shape":...,"status":...,"missing":[hdr names]}],
    "cookie_names": [sorted names],   # names only; degraded under redaction
  }

F2 PROOF baked in: signing fixtures carry redacted PLACEHOLDER values and the
recognizer must surface the param NAME + scheme, never the value. Pure/stdlib.
SYNTHETIC fixtures only (sandbox-safe; no browser, no real capture).
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import player_recognition as pr  # noqa: E402

_PH = "__REDACTED__"  # stand-in for the redactor PLACEHOLDER on signing values


def _e(url, *, status="200", ct=None, req_headers=None, resp_headers=None):
    e = {"url": url, "response_status": status}
    rh = []
    if ct:
        rh.append({"name": "content-type", "value": ct})
    for n, v in (resp_headers or {}).items():
        rh.append({"name": n, "value": v})
    e["response_headers"] = rh
    qh = []
    for n, v in (req_headers or {}).items():
        qh.append({"name": n, "value": v})
    e["request_headers"] = qh
    return e


def _rp(net, *, cookies=None, html="", script_srcs=None):
    out = pr.recognize_protection(net, cookies=cookies, html=html,
                                  script_srcs=script_srcs)
    assert isinstance(out, dict)
    for k in ("signing", "token_refresh", "anti_bot", "anti_bot_signals",
              "captcha", "drm", "drm_reasons", "drm_license_hosts",
              "header_preconditions", "cookie_names"):
        assert k in out, f"missing key {k}"
    return out


def _no_values_leak(out):
    """F2 self-check: the placeholder/value must never appear anywhere in the
    serialized protection block."""
    import json
    blob = json.dumps(out)
    assert _PH not in blob, "redacted value leaked into protection output"
    assert "secrettoken" not in blob.lower()


# --------------------------------------------------------------------------- #
# Signing schemes — by query-param NAME, on REDACTED values (F2)
# --------------------------------------------------------------------------- #
def test_akamai_token_scheme():
    net = [_e(f"https://media.cdn.example/v/1080.mp4?hdnts={_PH}&hdnea={_PH}",
              ct="video/mp4")]
    out = _rp(net)
    assert "akamai_token" in out["signing"]["schemes"]
    assert "hdnts" in out["signing"]["param_names"]
    assert "media.cdn.example" in " ".join(out["signing"]["hosts"])
    _no_values_leak(out)


def test_cloudfront_scheme_requires_triplet():
    net = [_e(f"https://d1.cloudfront.net/v.mp4?Policy={_PH}&Signature={_PH}&Key-Pair-Id={_PH}",
              ct="video/mp4")]
    out = _rp(net)
    assert "cloudfront" in out["signing"]["schemes"]
    # a lone Signature param must NOT be mistaken for cloudfront
    out2 = _rp([_e(f"https://x/v.mp4?Signature={_PH}", ct="video/mp4")])
    assert "cloudfront" not in out2["signing"]["schemes"]


def test_aws_sigv4_scheme():
    net = [_e(f"https://s3.example/v.mp4?X-Amz-Algorithm=AWS4&X-Amz-Signature={_PH}&X-Amz-Credential={_PH}",
              ct="video/mp4")]
    out = _rp(net)
    assert "aws_sigv4" in out["signing"]["schemes"]
    _no_values_leak(out)


def test_jwt_scheme():
    net = [_e(f"https://m/v.mp4?jwt={_PH}", ct="video/mp4")]
    out = _rp(net)
    assert "jwt" in out["signing"]["schemes"]


def test_generic_token_scheme():
    net = [_e(f"https://m/v.mp4?token={_PH}&expires=1700000000", ct="video/mp4")]
    out = _rp(net)
    assert "generic_token" in out["signing"]["schemes"]
    assert "token" in out["signing"]["param_names"]


def test_unsigned_media_has_no_signing():
    net = [_e("https://m/v.mp4?quality=hd", ct="video/mp4")]
    out = _rp(net)
    assert out["signing"]["schemes"] == []


# --------------------------------------------------------------------------- #
# Token-refresh endpoint — by path NAME
# --------------------------------------------------------------------------- #
def test_token_refresh_endpoint():
    net = [
        _e("https://api.example/auth/token/refresh", ct="application/json"),
        _e("https://api.example/get_signed_url", ct="application/json"),
        _e("https://api.example/home", ct="text/html"),
    ]
    out = _rp(net)
    shapes = " ".join(out["token_refresh"])
    assert "token/refresh" in shapes
    assert "get_signed_url" in shapes
    assert "/home" not in shapes


# --------------------------------------------------------------------------- #
# Anti-bot vendors — header NAMES + script hosts (cookie jar is PLACEHOLDER live)
# --------------------------------------------------------------------------- #
def test_cloudflare_antibot_by_header_name():
    net = [_e("https://site.example/", resp_headers={"cf-ray": "abc-IAD",
                                                      "cf-mitigated": "challenge"})]
    out = _rp(net)
    assert "cloudflare" in out["anti_bot"]
    assert any("cf-ray" in s for s in out["anti_bot_signals"]["cloudflare"])


def test_datadome_antibot_by_script_and_header():
    out = _rp([_e("https://site.example/", resp_headers={"x-datadome": "protected"})],
              html='<script src="https://js.datadome.co/tags.js"></script>')
    assert "datadome" in out["anti_bot"]


def test_kasada_antibot_by_header_name():
    out = _rp([_e("https://site.example/api", resp_headers={"x-kpsdk-ct": "v"})])
    assert "kasada" in out["anti_bot"]


def test_perimeterx_antibot_by_script():
    out = _rp([], html='<script src="https://client.perimeterx.net/PXxxxx/main.min.js"></script>')
    assert "perimeterx" in out["anti_bot"]


def test_akamai_antibot_by_cookie_name():
    # cookies arrive as a name-only list in dev/synthetic; recognizer reads NAMES
    out = _rp([], cookies=[{"name": "_abck", "value": _PH},
                           {"name": "bm_sz", "value": _PH}])
    assert "akamai" in out["anti_bot"]
    _no_values_leak(out)


def test_queueit_antibot_by_host():
    out = _rp([_e("https://x.queue-it.net/?c=site", ct="text/html")])
    assert "queue_it" in out["anti_bot"]


def test_clean_site_has_no_antibot():
    out = _rp([_e("https://site.example/", ct="text/html")], html="<html></html>")
    assert out["anti_bot"] == []


# --------------------------------------------------------------------------- #
# Captcha widgets — markup / script src
# --------------------------------------------------------------------------- #
def test_turnstile_captcha():
    out = _rp([], html='<div class="cf-turnstile" data-sitekey="x"></div>')
    assert "turnstile" in out["captcha"]


def test_hcaptcha_captcha():
    out = _rp([], html='<script src="https://js.hcaptcha.com/1/api.js"></script>')
    assert "hcaptcha" in out["captcha"]


def test_recaptcha_captcha():
    out = _rp([], html='<div class="g-recaptcha" data-sitekey="x"></div>')
    assert "recaptcha" in out["captcha"]


# --------------------------------------------------------------------------- #
# DRM / EME — drm:true + license host shape
# --------------------------------------------------------------------------- #
def test_drm_eme_detected():
    out = _rp([_e("https://lic.widevine.example/getLicense", ct="application/octet-stream")],
              html='<script>navigator.requestMediaKeySystemAccess("com.widevine.alpha")</script>')
    assert out["drm"] is True
    assert any("widevine" in r for r in out["drm_reasons"])
    assert any("widevine.example" in h for h in out["drm_license_hosts"])


def test_no_drm_on_clean_site():
    out = _rp([_e("https://m/v.mp4", ct="video/mp4")], html="<html></html>")
    assert out["drm"] is False


# --------------------------------------------------------------------------- #
# Header preconditions — media 403 missing Referer/Origin/Range (names only)
# --------------------------------------------------------------------------- #
def test_header_precondition_403_missing_referer():
    net = [_e("https://media.example/v.mp4?token=" + _PH, status="403",
              ct="video/mp4", req_headers={"user-agent": "x"})]
    out = _rp(net)
    pc = out["header_preconditions"]
    assert pc and pc[0]["status"] == "403"
    assert "referer" in [m.lower() for m in pc[0]["missing"]]
    _no_values_leak(out)


def test_no_precondition_when_media_200():
    net = [_e("https://media.example/v.mp4", status="200", ct="video/mp4",
              req_headers={"referer": "https://site"})]
    out = _rp(net)
    assert out["header_preconditions"] == []


# --------------------------------------------------------------------------- #
# Cookie names — names only, degraded gracefully under redaction
# --------------------------------------------------------------------------- #
def test_cookie_names_from_list():
    out = _rp([], cookies=[{"name": "sessionid", "value": _PH},
                           {"name": "cf_clearance", "value": _PH}])
    assert "sessionid" in out["cookie_names"]
    assert "cf_clearance" in out["cookie_names"]
    _no_values_leak(out)


def test_cookie_placeholder_string_tolerated():
    # live redacted captures set cap["cookies"] = PLACEHOLDER (a str) — must not crash
    out = _rp([], cookies=_PH)
    assert out["cookie_names"] == []


def test_empty_inputs_safe():
    out = _rp([])
    assert out["signing"]["schemes"] == []
    assert out["anti_bot"] == []
    assert out["drm"] is False
