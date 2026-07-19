"""v3.66.245 — FLOOR: a signed URL in a NON-DOM capture field must be fully
stripped under default/strip posture (scan_floor_secrets clean), and kept only
under a keep_full surface.

Repro of the live failure (2026-06-14, task.log): a default-posture capture
(relaxed redaction OFF) raised
``WaczRedactionError: floor secret(s) would survive into capture.json:
signed_url (1 site)``. Root cause: redact_capture runs _floor_walk on every
non-dom field with redact_signed_query=False (rationale: "network_log is already
query-redacted at capture time"), but scan_floor_secrets flags signed_url in ANY
non-dom field and, in default posture, never forgives it. A signed URL in a
field NOT scrubbed at capture time (action_timeline, route/media summaries,
metadata) therefore survived the scrub yet tripped the floor. Worse, the kv pass
scrubs token/Signature but leaves Expires (a timestamp), which the signed_url
detector still treats as sensitive -- so only the full signed-query pass clears it.

Fix: _floor_walk runs the signed-query pass when the network surface is NOT
keep_full (default/strip), and skips it under keep_full (so kept signing is
preserved, exactly as scan_floor_secrets forgives it on a keep_full surface).

RED on pristine 244: test_signed_url_in_nondom_field_stripped_by_default FAILS
(scan flags signed_url). GREEN after the _floor_walk fix. The keep_full case
guards against over-stripping.
"""
import sys


def _car():
    sys.path.insert(0, "/home/claude/work")
    from bulk_downloader import capture_artifact_redact as car
    return car


_SIGNED = ("https://cdn.example.com/media/film.mp4"
           "?token=abc123SECRETsig&Expires=1781000000&Signature=ZZZ999")


def _cap():
    # signed URL in a non-dom field that capture-time network scrubbing does not
    # cover (an operator-interaction effect URL recorded in the action timeline).
    return {
        "dom_log": [],
        "network_log": [],
        "action_timeline": [{"selector": 'a[download]', "effect_url": _SIGNED}],
    }


def test_signed_url_in_nondom_field_stripped_by_default():
    car = _car()
    prof = car.current_profile()
    assert prof.get("network_signed_urls") != car.KEEP_FULL  # default == strip
    red = car.redact_capture(_cap(), prof)
    findings = car.scan_floor_secrets(red, prof)
    # the floor must be clean: no signed_url (or any) residual survives default scrub
    assert findings == [], findings
    # and the value is structurally intact (host + path kept, query neutralized)
    eff = red["action_timeline"][0]["effect_url"]
    assert eff.startswith("https://cdn.example.com/media/film.mp4?"), eff
    assert "1781000000" not in eff and "ZZZ999" not in eff, eff


def test_keep_full_still_keeps_nondom_signing():
    """Under a keep_full network surface the full signed-query pass is SKIPPED, so
    the non-secret signing param (Expires) is preserved and the floor forgives the
    residual -- the fix must not over-strip on keep_full. (The kv pass still
    neutralizes token=/Signature= in non-dom fields regardless of posture; that is
    pre-existing and not what keep_full preserves.)"""
    car = _car()
    base = car.current_profile()
    prof = dict(base)
    prof["network_signed_urls"] = car.KEEP_FULL
    red = car.redact_capture(_cap(), prof)
    eff = red["action_timeline"][0]["effect_url"]
    # the full query pass did NOT run -> the non-secret signing param survives,
    # distinguishing keep_full from the default case (which strips it)
    assert "1781000000" in eff, eff
    # and the floor forgives the signed residual on the keep_full surface
    findings = car.scan_floor_secrets(red, prof)
    assert findings == [], findings


def test_hard_credentials_still_stripped_regardless():
    """A JWT / userinfo in a non-dom field is NEVER kept, even under keep_full."""
    car = _car()
    prof = dict(car.current_profile())
    prof["network_signed_urls"] = car.KEEP_FULL
    jwt = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
           "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")
    cap = {"dom_log": [], "network_log": [],
           "action_timeline": [{"v": jwt, "u": "https://u:p@host.example/x"}]}
    red = car.redact_capture(cap, prof)
    findings = car.scan_floor_secrets(red, prof)
    assert findings == [], findings  # jwt + userinfo stripped, none survive
