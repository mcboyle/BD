"""Tests for bulk_downloader.inspect_pick (Track F Wave A — observational inspector).

Pure-function tests: no browser. Each behaviour has a positive control (an
input that MUST produce the wrong answer if the logic regresses), per the MAX
verification posture.
"""
import importlib


def _m():
    return importlib.import_module("bulk_downloader.inspect_pick")


# ── selector derivation ──────────────────────────────────────────────
def test_selector_prefers_stable_id():
    m = _m()
    d = {"tag": "a", "id": "download-btn", "classes": ["x"], "nth": 3}
    assert m.build_selector(d) == "#download-btn"


def test_selector_skips_unstable_id_uses_classes():
    m = _m()
    # uuid-ish id must NOT be used; stable class path instead
    d = {"tag": "a", "id": "a1b2c3d4-1111-2222-3333-444455556666",
         "classes": ["download-link", "btn"]}
    sel = m.build_selector(d)
    assert sel == "a.download-link.btn"
    assert "#" not in sel  # positive control: the uuid id was rejected


def test_selector_drops_hashed_classes():
    m = _m()
    d = {"tag": "div", "classes": ["css-1a2b3c", "controls", "sc-bdVaJa"]}
    # only the human class survives
    assert m.build_selector(d) == "div.controls"


def test_selector_qualifies_with_short_data_attr():
    m = _m()
    d = {"tag": "a", "classes": ["quality"], "data_attrs": {"q": "1080"}}
    assert m.build_selector(d) == 'a.quality[data-q="1080"]'


def test_selector_falls_back_to_nth_child_with_ancestor():
    m = _m()
    d = {"tag": "a", "classes": [], "nth": 4,
         "ancestors": [{"tag": "div", "classes": ["controls"]},
                       {"tag": "section", "classes": []}]}
    sel = m.build_selector(d)
    assert sel == "div.controls > a:nth-child(4)"


def test_selector_ancestor_id_anchor():
    m = _m()
    d = {"tag": "button", "classes": [], "nth": 2,
         "ancestors": [{"tag": "div", "id": "player", "classes": []}]}
    assert m.build_selector(d) == "#player > button:nth-child(2)"


# ── xpath ─────────────────────────────────────────────────────────────
def test_xpath_uses_class_predicate_and_index():
    m = _m()
    d = {"tag": "a", "of_type_nth": 4,
         "ancestors": [{"tag": "div", "classes": ["controls"]}]}
    assert m.build_xpath(d) == "//div[@class='controls']/a[4]"


# ── role heuristic ────────────────────────────────────────────────────
def test_role_download():
    m = _m()
    r, c = m.role_of({"tag": "a", "text": "Download", "classes": ["download-link"]})
    assert r == "download link" and c >= 0.9


def test_role_play():
    m = _m()
    r, _ = m.role_of({"tag": "button", "classes": ["play-btn"], "text": "Play"})
    assert r == "play button"


def test_role_media_element_by_tag():
    m = _m()
    assert m.role_of({"tag": "video"})[0] == "media element"


def test_role_unknown_is_low_confidence():
    m = _m()
    r, c = m.role_of({"tag": "span", "text": "lorem", "classes": ["wrap"]})
    assert r == "element" and c < 0.5  # positive control: not falsely confident


# ── excerpt redaction (values out, structure in) ──────────────────────
def test_redact_excerpt_scrubs_urls_and_keeps_structure():
    m = _m()
    html = ('<a class="download-link" id="dl" href="https://cdn.x/v/abc?sig=SECRET123&exp=99" '
            'data-id="user-7781" aria-label="Download">Download</a>')
    out = m.redact_excerpt(html)
    # structure kept
    assert 'class="download-link"' in out
    assert 'id="dl"' in out
    assert 'aria-label="Download"' in out
    assert ">Download<" in out
    # values scrubbed — positive controls: the secrets MUST be gone
    assert "SECRET123" not in out
    assert "user-7781" not in out
    # keep_structure posture: the href path may survive, but the signing query
    # value is scrubbed (consistent with how network_log URLs are handled)
    assert "sig=SECRET123" not in out


def test_redact_excerpt_denies_unknown_attr_values():
    m = _m()
    out = m.redact_excerpt('<input name="email" value="alice@example.com" type="email">')
    assert "alice@example.com" not in out          # value scrubbed
    assert 'name="email"' in out and 'type="email"' in out  # structure kept


def test_redact_excerpt_empty_safe():
    m = _m()
    assert m.redact_excerpt(None) == ""
    assert m.redact_excerpt("") == ""


# ── click → effect correlation (counts/kinds, never URLs) ─────────────
def _media_log():
    return [
        {"url": "https://watch.x/login", "timestamp": 100, "resource_type": "document"},
        {"url": "https://cdn.x/master.m3u8", "timestamp": 1000, "response_status": 200},
        {"url": "https://cdn.x/s0.ts", "timestamp": 1100, "response_status": 200},
        {"url": "https://cdn.x/s1.ts", "timestamp": 1200, "response_status": 200},
        {"url": "https://x/api/late", "timestamp": 9000, "response_status": 200},
    ]


def test_effects_window_counts_only_in_range():
    m = _m()
    eff = m.effects_for_click(900, _media_log(), window_ms=2500)
    # the three media reqs at 1000-1200 fall in (900, 3400]; the 9000 one does not
    assert eff["req_count"] == 3
    assert eff["manifest"] == 1
    assert eff["segments"] == 2
    assert eff["nav"] is False  # positive control: the doc at t=100 is outside the window


def test_effects_detects_nav():
    m = _m()
    eff = m.effects_for_click(50, _media_log(), window_ms=200)
    assert eff["nav"] is True and eff["req_count"] == 1


def test_effects_zero_when_nothing_followed():
    m = _m()
    eff = m.effects_for_click(4000, _media_log(), window_ms=500)
    assert eff["req_count"] == 0 and eff["manifest"] == 0


# ── action entry (selector+structure, redacted excerpt, effect) ───────
def test_build_action_entry_shape_no_values():
    m = _m()
    desc = {"tag": "a", "classes": ["download-link"], "text": "Download",
            "outer_html": '<a class="download-link" href="https://cdn.x/v?sig=SEKRET">Download</a>'}
    e = m.build_action_entry(desc, _media_log(), click_ts=900, window_ms=2500)
    assert e["selector"] == "a.download-link"
    assert e["role"] == "download link"
    assert "SEKRET" not in e["excerpt"]            # redacted
    assert e["effect"]["req_count"] == 3
    assert isinstance(e["confidence"], float)


# ── verify summary (advisory; gaps + trigger + tier) ──────────────────
def test_verify_flags_zero_network_gap_and_resolves_trigger():
    m = _m()
    cap = {"network_log": _media_log()}
    tl = [
        {"selector": ".play-btn", "role": "play button",
         "effect": {"req_count": 3, "manifest": 1, "segments": 2, "direct_media": 0}},
        {"selector": "a.quality", "role": "quality select",
         "effect": {"req_count": 0, "manifest": 0, "segments": 0, "direct_media": 0}},
        {"selector": "a.download-link", "role": "download link",
         "effect": {"req_count": 1, "manifest": 0, "segments": 0, "direct_media": 1}},
    ]
    v = m.verify_summary(tl, cap)
    assert v["tier"] == "ready"                     # capture has manifest+segments
    assert v["gap_count"] == 1                       # the quality click fired 0 req
    assert any("a.quality" in w for w in v["warnings"])
    assert v["trigger_selector"] == ".play-btn"      # first media-producing click
    assert "play captured" in v["checks"]
    assert "download captured" in v["checks"]
    assert v["trigger_resolved"] is True             # trigger resolved -> True


def test_verify_does_not_warn_on_zero_network_field_focus():
    """#2a regression: a 0-network click on a FORM FIELD (input/textarea/select)
    is expected (focusing a field fires no network) and must NOT produce a
    warning or count toward gap_count — otherwise the verify bar shows advisories
    that can never clear. Positive control: the SAME 0-network click on a button
    DOES warn, proving the suppression is field-specific, not blanket."""
    m = _m()
    cap = {"network_log": []}
    field_tl = [
        {"selector": "#user-email", "role": "element", "tag": "input",
         "effect": {"req_count": 0, "manifest": 0, "segments": 0, "direct_media": 0}},
        {"selector": "#user-password", "role": "element", "tag": "input",
         "effect": {"req_count": 0, "manifest": 0, "segments": 0, "direct_media": 0}},
    ]
    v = m.verify_summary(field_tl, cap)
    assert v["gap_count"] == 0, v
    assert v["warnings"] == [] or not any("fired 0 network" in w for w in v["warnings"]), v
    assert v["trigger_resolved"] is False
    # positive control: a button firing 0 network is still flagged
    btn_tl = [{"selector": "button.go", "role": "login/submit", "tag": "button",
               "effect": {"req_count": 0, "manifest": 0, "segments": 0, "direct_media": 0}}]
    vb = m.verify_summary(btn_tl, cap)
    assert vb["gap_count"] == 1, vb
    assert any("button.go" in w and "fired 0 network" in w for w in vb["warnings"]), vb


def test_verify_softens_zero_network_warning_when_capture_ready():
    """#238: a 0-network non-field click on a READY capture (media already
    present) reads as 'already satisfied; capture is ready', not the missed-click
    hedge — on a complete capture such a click (a play/pause toggle, a menu open)
    is redundant, not a problem. The selector is still named; the gap still counts
    (advisory)."""
    m = _m()
    ready_cap = {"network_log": _media_log()}        # master.m3u8 -> tier 'ready'
    tl = [{"selector": "video.jw-video", "role": "element", "tag": "video",
           "effect": {"req_count": 0, "manifest": 0, "segments": 0, "direct_media": 0}}]
    v = m.verify_summary(tl, ready_cap)
    assert v["tier"] == "ready", v
    assert any("video.jw-video" in w and "already satisfied; capture is ready" in w
               for w in v["warnings"]), v
    assert not any("missed click" in w for w in v["warnings"]), v


def test_verify_keeps_missed_click_hedge_when_not_ready():
    """Counterpart to the above: with NO media (tier below 'ready') the same
    0-network click KEEPS the missed-click hedge — a missed click may be exactly
    why nothing was captured yet, so the softer wording would be misleading."""
    m = _m()
    tl = [{"selector": "video.jw-video", "role": "element", "tag": "video",
           "effect": {"req_count": 0, "manifest": 0, "segments": 0, "direct_media": 0}}]
    v = m.verify_summary(tl, {"network_log": []})    # no media -> tier 'thin'
    assert v["tier"] != "ready", v
    assert any("video.jw-video" in w and "missed click, or already satisfied?" in w
               for w in v["warnings"]), v
    assert not any("capture is ready" in w for w in v["warnings"]), v


def test_verify_thin_when_empty():
    m = _m()
    v = m.verify_summary([], {"network_log": []})
    assert v["tier"] == "thin"
    assert v["gap_count"] == 0


def test_verify_rrweb_crosscheck_mismatch_warns():
    m = _m()
    # rrweb saw 5 clicks, only 2 resolved -> warn (the (B) cross-check)
    v = m.verify_summary(
        [{"selector": "a", "effect": {"req_count": 1}},
         {"selector": "b", "effect": {"req_count": 1}}],
        {"network_log": _media_log()}, recorded_clicks=5)
    assert any("unresolved" in w for w in v["warnings"])


# ── sequence correlation (most-recent preceding click owns each request) ──────
def test_correlate_timeline_no_double_count_close_clicks():
    m = _m()
    # login at 400 then play at 1900; the manifest fires at 2000. The manifest
    # must be attributed to PLAY (most-recent preceding click), NOT login, even
    # though login's 2.5s window (400..2900) also overlaps it.
    net = [
        {"url": "https://x/app", "timestamp": 600, "resource_type": "document"},
        {"url": "https://cdn.x/master.m3u8", "timestamp": 2000, "response_status": 200},
        {"url": "https://cdn.x/s0.ts", "timestamp": 2100, "response_status": 200},
        {"url": "https://cdn.x/s1.ts", "timestamp": 2200, "response_status": 200},
    ]
    picks = [
        {"ts": 400, "descriptor": {"tag": "a", "classes": ["login-submit"], "text": "Sign in"}},
        {"ts": 1900, "descriptor": {"tag": "button", "classes": ["play-btn"], "text": "Play"}},
    ]
    tl = m.correlate_timeline(picks, net, window_ms=2500)
    assert len(tl) == 2
    login, play = tl[0], tl[1]
    # login owns only the nav doc at 600; play owns the manifest + 2 segments
    assert login["effect"]["manifest"] == 0
    assert login["effect"]["nav"] is True
    assert play["effect"]["manifest"] == 1
    assert play["effect"]["segments"] == 2
    # and the verify trigger now resolves to the play button, not login
    v = m.verify_summary(tl, {"network_log": net})
    assert v["trigger_selector"] == "button.play-btn"


def test_buffered_play_click_with_in_page_ts_resolves_trigger():
    """#2b end-to-end at unit level: a play click drained from the in-page buffer
    carries an epoch-ms in-page ts; media requests that load just after it must
    correlate to it, producing a resolved trigger (the venus play click that was
    previously dropped). Mirrors the w212wow cross-origin scenario."""
    m = _m()
    t0 = 1781396300000
    picks = [{"descriptor": {"tag": "button", "classes": ["vjs-big-play-button"],
                             "text": "Play"}, "ts": t0}]
    network_log = [
        {"url": "https://content-video2.example.com/x_1080.mp4",
         "timestamp": t0 + 400, "response_headers": {"content-type": "video/mp4"}},
        {"url": "https://content-video2.example.com/x.m3u8",
         "timestamp": t0 + 600, "response_headers": {"content-type": "application/x-mpegurl"}},
    ]
    tl = m.correlate_timeline(picks, network_log)
    assert len(tl) == 1
    eff = tl[0]["effect"]
    assert eff.get("req_count", 0) >= 1              # media attributed to the play click
    v = m.verify_summary(tl, {"network_log": network_log})
    assert v["trigger_resolved"] is True            # the play click is now the trigger
