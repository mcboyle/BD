"""O1789 dat-retry-class: deep-adapters-transport FINDINGS F7, F13, F14.

Each test is the FINDINGS scenario (R01-R08) and fails with an
O1789-DAT-Fn-... marker on a tree that still has the defect. Each finding
also has a control: the input FINDINGS names as handled correctly, which
must stay that way after the fix.
"""
import pytest

from bulk_downloader import retry_policy as rp
from bulk_downloader import runner_transport as rt

BD_GATE_SCOPE = "module"


# ── F7: "429" as a bare substring ────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "curl: (18) end of response with 1429 bytes missing",
    "HTTP 500 from https://cdn.example/v/4291/seg.ts",
    "connect to 10.0.0.5:4290 refused",
    "downloaded 14297 of 99999 bytes then stalled",
    "file missing on https://rate-limit-test.example/",
])
def test_f7_429_like_numbers_and_hosts_are_not_rate_limited(msg):
    got = rp.classify_failure(msg)
    assert got == "transient", (
        f"O1789-DAT-F7-SUBSTRING-429: classify_failure({msg!r}) -> {got!r}; "
        f"a number or host merely containing 429 / rate-limit is not a "
        f"throttle (rate_limited waits 300 s, 10 attempts)")


@pytest.mark.parametrize("msg", [
    "HTTP 429 Too Many Requests",
    "HTTP Error 429: Too Many Requests",
    "status=429",
    "got 429 from https://cdn.example/v/1/seg.ts",
    "rate limit exceeded",
])
def test_f7_ctl_a_real_429_is_still_rate_limited(msg):
    assert rp.classify_failure(msg) == "rate_limited", msg


def test_f7_ctl_connection_reset_is_transient():
    assert rp.classify_failure("connection reset by peer") == "transient"
    assert rp.compute_next_delay("transient", 0, apply_jitter=False) == 30


# ── F13: classifier / delay edge inputs ──────────────────────────────────────

def test_f13_status_407_is_auth_like_its_text():
    got = rp.classify_failure("", 407)
    assert got == "auth", (
        f"O1789-DAT-F13-407-PERMANENT: status 407 -> {got!r}, while the text "
        f"'407 Proxy Authentication Required' -> "
        f"{rp.classify_failure('407 Proxy Authentication Required')!r}")


def test_f13_ctl_407_text_is_auth():
    assert rp.classify_failure("407 Proxy Authentication Required") == "auth"


def test_f13_infinite_status_and_attempt_do_not_raise():
    try:
        cls = rp.classify_failure("", float("inf"))
        d = rp.compute_next_delay("transient", float("inf"), apply_jitter=False)
        ok = rp.should_retry("transient", float("inf"))
    except OverflowError as e:
        pytest.fail(f"O1789-DAT-F13-OVERFLOW: {e}")
    assert cls == "transient"
    assert d == 0, f"an infinite attempt is past the cap, got delay {d}"
    assert ok is False


def test_f13_should_retry_coerces_attempt_like_compute_next_delay():
    try:
        got = (rp.should_retry("transient", None),
               rp.should_retry("transient", "2"),
               rp.should_retry("transient", "99"))
    except TypeError as e:
        pytest.fail(f"O1789-DAT-F13-SHOULD-RETRY-TYPEERROR: {e}")
    assert got == (True, True, False)


def test_f13_unknown_profile_never_undercuts_rate_limited_base():
    base = rp.CLASSES["rate_limited"]["base_delay_s"]
    lo = min(rp.compute_next_delay("rate_limited", 0, profile="no-such-profile")
             for _ in range(500))
    assert lo >= base, (
        f"O1789-DAT-F13-PROFILE-UNDERCUT: rate_limited attempt 0 with an "
        f"unknown profile waited {lo} s (class base {base} s)")


def test_f13_ctl_retry_after_still_wins_with_a_profile():
    d = rp.compute_next_delay("rate_limited", 0, retry_after_header=900,
                              profile="no-such-profile")
    assert d >= 900


@pytest.mark.parametrize("cls", ["transient", "rate_limited", "auth"])
def test_f13_default_jitter_stays_within_class_max(cls):
    cfg = rp.CLASSES[cls]
    hi = max(rp.compute_next_delay(cls, a)
             for a in range(cfg["max_attempts"]) for _ in range(2000))
    assert hi <= cfg["max_delay_s"], (
        f"O1789-DAT-F13-JITTER-PAST-MAX: {cls} jittered delay {hi} > class "
        f"max {cfg['max_delay_s']}")


def test_f13_ctl_jitter_still_varies_below_the_cap():
    ds = {rp.compute_next_delay("transient", 1) for _ in range(500)}
    assert len(ds) > 10 and min(ds) < 60 < max(ds)


def _classify_client():
    from flask import Flask
    from bulk_downloader import app_retry_policy as arp
    app = Flask("dat_retry_class")
    arp.register_routes(app)
    return app.test_client()


@pytest.mark.parametrize("body", [[1], "str"])
def test_f13_classify_api_non_object_body_is_400(body):
    r = _classify_client().post("/api/retry_policy/classify", json=body)
    assert r.status_code == 400, (
        f"O1789-DAT-F13-API-500: body {body!r} -> {r.status_code} "
        f"{r.get_json(silent=True)!r}")


def test_f13_classify_api_infinite_status_is_not_500():
    r = _classify_client().post("/api/retry_policy/classify",
                                data='{"status_code": Infinity}',
                                content_type="application/json")
    assert r.status_code == 200, (
        f"O1789-DAT-F13-API-500: status_code Infinity -> {r.status_code} "
        f"{r.get_json(silent=True)!r}")
    assert r.get_json()["class"] == "transient"


def test_f13_ctl_classify_api_503():
    r = _classify_client().post("/api/retry_policy/classify",
                                json={"message": "x", "status_code": 503})
    assert r.status_code == 200
    assert r.get_json()["class"] == "transient"


# ── F14: _is_transient_download_error ────────────────────────────────────────

def _transient(msg):
    return rt._is_transient_download_error(Exception(msg))


@pytest.mark.parametrize("msg", [
    "Connection reset by peer while reading https://separate-cdn.example/a.mp4",
    "read timed out fetching https://accelerate.example/a.mp4",
    "connection reset (https://cdn.example/generated/a.mp4)",
])
def test_f14_rate_substring_in_a_word_does_not_veto(msg):
    assert _transient(msg) is True, (
        f"O1789-DAT-F14-VETO-SUBSTRING: {msg!r} not retried on the same URL")


@pytest.mark.parametrize("msg", [
    "curl: (7) Failed to connect to 127.0.0.1 port 3128 after 0 ms: "
    "Couldn't connect to server",
    "curl: (56) CONNECT tunnel failed, response 502",
    "curl: (97) connection to proxy closed",
    "curl: (97) Can't complete SOCKS5 connection to example.com. (4)",
    "curl: (52) Empty reply from server",
    "curl: (92) HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR (err 2)",
    "Server disconnected without sending a response.",
])
def test_f14_proxy_side_hiccups_are_transient(msg):
    assert _transient(msg) is True, (
        f"O1789-DAT-F14-PROXY-NOT-TRANSIENT: {msg!r}")


@pytest.mark.parametrize("msg,want", [
    ("connection reset by peer", True),
    ("rate limited: connection reset", False),
    ("ratelimit hit, connection reset", False),
    ("download stopped: connection reset", False),
    ("HTTP 404", False),
    ("curl: (56) CONNECT tunnel failed, response 407", False),
])
def test_f14_ctl_veto_and_non_network_failures_unchanged(msg, want):
    assert _transient(msg) is want, msg
