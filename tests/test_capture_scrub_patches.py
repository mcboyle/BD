"""F0.3 capture_scrub.py patches A/B/C/D — targeted unit tests.

A: forwarding headers added to the sensitive-header mask.
B: bare-IPv4 masked in header values (UA version strings kept; URL hosts preserved);
   self-verify (scan_residual) flags a residual header IP.
C: a *_draft.json is refused by main(); selector slots / workflow descriptors are
   never treated as secrets even inside walk().
D: default --token-min lowered to 24.

Harness: zero-arg test functions; no pytest fixtures; restore sys.argv in finally.
"""
import sys, os, io, json, tempfile, contextlib

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "tools"))
import capture_scrub as cs   # noqa: E402

PH = cs.PH


# ── Patch A : forwarding-header value redacted (dict-shape + {name,value} list) ──
def test_patch_a_forwarding_header_value_redacted():
    obj = {
        "request_headers": {"x-forwarded-for": "203.0.113.7", "x-detected-ip": "198.51.100.9"},
        "response_headers": [{"name": "via", "value": "1.1 198.51.100.9"},
                             {"name": "content-type", "value": "text/html"}],
    }
    red = cs.walk(obj, "safe", 24)
    assert red["request_headers"]["x-forwarded-for"] == PH
    assert red["request_headers"]["x-detected-ip"] == PH
    vals = {h["name"]: h["value"] for h in red["response_headers"]}
    assert vals["via"] == PH                       # forwarding header masked by name
    assert vals["content-type"] == "text/html"     # benign header untouched
    assert cs.scan_residual(red) == []             # self-verify: clean


# ── Patch B : IP in a non-listed header still masked; UA version kept ───────────
def test_patch_b_ip_in_nonforwarding_header_masked_ua_kept():
    obj = {"request_headers": [
        {"name": "x-debug-origin", "value": "10.1.2.3"},
        {"name": "user-agent", "value": "Mozilla Chrome/137.0.0.0 Safari"},
    ]}
    red = cs.walk(obj, "safe", 24)
    vals = {h["name"]: h["value"] for h in red["request_headers"]}
    assert PH in vals["x-debug-origin"]            # 10.1.2.3 masked (header_ctx)
    assert "137.0.0.0" in vals["user-agent"]       # UA version string survives
    assert cs.scan_residual(red) == []


# ── Patch B : a destination CDN IP inside a URL host is preserved (safe mode) ───
def test_patch_b_url_host_ip_preserved():
    out = cs.scrub_string("https://203.0.113.50/path/clip.ts", "safe", 24, header_ctx=False)
    assert "203.0.113.50" in out


# ── Patch B : self-verify catches a residual header IP (regression guard) ───────
def test_patch_b_scan_residual_flags_header_ip():
    # an UNscrubbed header IP must be reported by scan_residual (header_ctx)
    leak = {"response_headers": [{"name": "x-origin-debug", "value": "192.0.2.55"}]}
    hits = cs.scan_residual(leak)
    assert any(k == "ipv4-header" for _, k in hits)
    # ...and a UA version string is NOT a residual
    ua = {"request_headers": [{"name": "user-agent", "value": "Chrome/137.0.0.0"}]}
    assert cs.scan_residual(ua) == []


# ── Patch C : selector slot + workflow descriptor are not redacted in walk() ────
def test_patch_c_selector_and_workflow_preserved():
    obj = {
        "selectors": {"login": {"password": "input#password", "submit": "button.login"}},
        "workflow": {"auth": "modal_login", "capture_mode": "manual"},
    }
    red = cs.walk(obj, "safe", 24)
    assert red["selectors"]["login"]["password"] == "input#password"
    assert red["workflow"]["auth"] == "modal_login"
    assert red["workflow"]["capture_mode"] == "manual"


# ── Patch C : main() refuses a *_draft.json by name (no output written) ─────────
def test_patch_c_draft_name_refused():
    d = tempfile.mkdtemp(prefix="bd_scrub_")
    p = os.path.join(d, "wow_draft.json")
    open(p, "w").write(json.dumps({"selectors": {"login": {"password": "input#pw"}}}))
    argv = sys.argv
    try:
        sys.argv = ["capture_scrub.py", p]
        rc = cs.main()
    finally:
        sys.argv = argv
    assert rc == 1
    assert not os.path.exists(os.path.join(d, "wow_draft.redacted.json"))


# ── Patch D : default token-min is 24; a clean capture still writes clean ───────
def test_patch_d_default_token_min_24_and_clean_capture_writes():
    cs.stats.clear()
    d = tempfile.mkdtemp(prefix="bd_scrub_")
    p = os.path.join(d, "cap.json")
    open(p, "w").write(json.dumps({
        "host": "example.test",
        "network_log": [{"url": "https://example.test/a", "type": "xhr"}],
        "dom_log": [],
    }))
    out = os.path.join(d, "cap.out.json")
    argv = sys.argv
    buf = io.StringIO()
    try:
        sys.argv = ["capture_scrub.py", p, "-o", out]
        with contextlib.redirect_stdout(buf):
            rc = cs.main()
    finally:
        sys.argv = argv
    txt = buf.getvalue()
    assert rc == 0, txt
    assert os.path.exists(out)
    assert "token-min=24" in txt                   # patch D default surfaced


# ── Patch D : a 24-char mixed token is masked at the new threshold ──────────────
def test_patch_d_token_min_24_masks_24char_token():
    tok = "abc123def456ghi789jkl012"                # 24 chars, digits + letters
    assert len(tok) == 24
    assert cs.scrub_string(tok, "safe", 24) == PH
