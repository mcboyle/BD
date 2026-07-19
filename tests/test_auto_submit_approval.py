"""Tests for the per-site auto-submit approval model + this session's
detector additions (F5 Smooth Streaming / HLS markers, F8 login vocab).

Approval model: every surface that used to hard-set
`do_not_auto_submit=True` (login-form bot defense, F8 interactive
challenges, page-level auth options, page CAPTCHA/bot-defense blockers,
and the F12 two-step POST reveal) now instead emits an approve/decline
decision that is remembered per site:

  * needs_approval=True + approval_status in
    {pending, approved, declined}, derived from site_memory;
  * do_not_auto_submit stays True (safe default) until the operator
    explicitly APPROVES that surface on that site, then it opens;
  * a decline keeps it closed; the choice is keyed per host so it never
    leaks across sites;
  * persisted via learn.record_auto_submit_decision (login/blocker) or
    learn.record_post_reveal_decision (post-reveal), into two separate
    stores under learned.deep_detect.

Plain functions + lazy imports for runner + real-pytest compatibility.
"""
import pytest

pytestmark = pytest.mark.bd_module_wipe


# ── Login form: bot defense → approval gate ─────────────────────────

_BOT_FORM = (
    '<html><body><form method="POST" action="/login">'
    '<input name="username"><input type="password" name="pw">'
    '<div class="cf-turnstile" data-sitekey="x"></div>'
    '<button>Sign in</button></form></body></html>'
)


def test_bot_defense_form_is_pending_by_default():
    from bulk_downloader import deep_detect as dd
    best = dd.score_login_page(_BOT_FORM, base_url="https://site.test/")["best"]
    assert best["needs_approval"] is True
    assert best["approval_status"] == "pending"
    # Safe default: no auto-submit until approved.
    assert best["do_not_auto_submit"] is True
    assert best.get("approval_key")


def test_bot_defense_form_opens_after_approve():
    from bulk_downloader import deep_detect as dd
    from bulk_downloader import learn
    best = dd.score_login_page(_BOT_FORM, base_url="https://site.test/")["best"]
    cfg = learn.record_auto_submit_decision({}, best["approval_key"], "approve")
    sm = learn.deep_detect_site_memory(cfg)
    again = dd.score_login_page(
        _BOT_FORM, base_url="https://site.test/", site_memory=sm)["best"]
    assert again["approval_status"] == "approved"
    assert again["do_not_auto_submit"] is False


def test_bot_defense_form_stays_closed_after_decline():
    from bulk_downloader import deep_detect as dd
    from bulk_downloader import learn
    best = dd.score_login_page(_BOT_FORM, base_url="https://site.test/")["best"]
    cfg = learn.record_auto_submit_decision({}, best["approval_key"], "decline")
    sm = learn.deep_detect_site_memory(cfg)
    again = dd.score_login_page(
        _BOT_FORM, base_url="https://site.test/", site_memory=sm)["best"]
    assert again["approval_status"] == "declined"
    assert again["do_not_auto_submit"] is True


# ── F8: interactive challenge (MFA) → approval gate ─────────────────

def test_mfa_challenge_form_needs_approval():
    from bulk_downloader import deep_detect as dd
    html = ('<form method="POST" action="/2fa">'
            '<input name="otp_code"><button>Verify code</button></form>')
    best = dd.score_login_page(html, base_url="https://mfa.test/")["best"]
    assert "mfa" in best["login_types"]
    assert best["needs_approval"] is True
    assert best["approval_status"] == "pending"
    assert best["do_not_auto_submit"] is True


# ── Plain password form: automatable, never prompts ─────────────────

def test_plain_password_form_is_automatable():
    from bulk_downloader import deep_detect as dd
    html = ('<form method="POST" action="/login">'
            '<input name="email"><input type="password" name="pw">'
            '<button>Log in</button></form>')
    best = dd.score_login_page(html, base_url="https://plain.test/")["best"]
    assert best["login_types"] == ["form_password"]
    # No challenge / bot defense → the approval gate never engaged.
    assert not best.get("needs_approval")
    assert best["do_not_auto_submit"] is False


# ── Page-level CAPTCHA blocker → approval gate ──────────────────────

_CAPTCHA_PAGE = '<html><body><div class="g-recaptcha"></div></body></html>'


def test_captcha_blocker_pending_blocks():
    from bulk_downloader import deep_detect as dd
    bl = dd.scan_blockers(_CAPTCHA_PAGE, base_url="https://c.test/")
    assert bl["needs_approval"] is True
    assert bl["approval_status"] == "pending"
    assert bl["do_not_auto_submit"] is True
    # blocked rolls up do_not_auto_submit OR do_not_download.
    assert bl["blocked"] is True


def test_captcha_blocker_unblocks_after_approve():
    from bulk_downloader import deep_detect as dd
    from bulk_downloader import learn
    bl = dd.scan_blockers(_CAPTCHA_PAGE, base_url="https://c.test/")
    cfg = learn.record_auto_submit_decision({}, bl["approval_key"], "approve")
    sm = learn.deep_detect_site_memory(cfg)
    bl2 = dd.scan_blockers(_CAPTCHA_PAGE, base_url="https://c.test/",
                           site_memory=sm)
    assert bl2["approval_status"] == "approved"
    assert bl2["do_not_auto_submit"] is False
    assert bl2["blocked"] is False


def test_drm_still_blocks_even_when_bot_defense_approved():
    from bulk_downloader import deep_detect as dd
    from bulk_downloader import learn
    # DRM is a separate do_not_download gate; approving the auto-submit
    # surface must not unblock encrypted content.
    drm = ('<html><body><script>widevine</script>'
           '<div class="cf-turnstile"></div></body></html>')
    cfg = learn.record_auto_submit_decision({}, "d.test/", "approve")
    bl = dd.scan_blockers(drm, base_url="https://d.test/",
                          site_memory=learn.deep_detect_site_memory(cfg))
    assert bl["do_not_download"] is True
    assert bl["blocked"] is True


# ── Per-host key isolation ──────────────────────────────────────────

def test_approval_keys_are_per_host_no_leak():
    from bulk_downloader import deep_detect as dd
    from bulk_downloader import learn
    a = dd.score_login_page(_BOT_FORM, base_url="https://site-a.test/")["best"]
    b = dd.score_login_page(_BOT_FORM, base_url="https://site-b.test/")["best"]
    assert a["approval_key"] != b["approval_key"]
    assert a["approval_key"].startswith("site-a.test/")
    assert b["approval_key"].startswith("site-b.test/")
    # Approving site-a must NOT open site-b.
    sm = learn.deep_detect_site_memory(
        learn.record_auto_submit_decision({}, a["approval_key"], "approve"))
    ra = dd.score_login_page(
        _BOT_FORM, base_url="https://site-a.test/", site_memory=sm)["best"]
    rb = dd.score_login_page(
        _BOT_FORM, base_url="https://site-b.test/", site_memory=sm)["best"]
    assert ra["approval_status"] == "approved"
    assert rb["approval_status"] == "pending"


def test_absolute_action_keys_to_its_own_host():
    from bulk_downloader import deep_detect as dd
    html = ('<form method="POST" action="https://auth.cdn.test/login">'
            '<input type="password" name="p">'
            '<div class="cf-turnstile"></div><button>x</button></form>')
    best = dd.score_login_page(html, base_url="https://front.test/")["best"]
    assert best["approval_key"] == "auth.cdn.test/login"


# ── F12 post-reveal uses its own store, still works ─────────────────

_PR_FORM = (
    '<html><head><style>.t{display:none}</style></head><body>'
    '<form method="POST" action="/gen?file_id=1">'
    '<input type="hidden" name="csrf" value="a">'
    '<input class="t" name="trap">'
    '<button>Generate Link</button></form></body></html>'
)


def test_post_reveal_pending_then_approved():
    from bulk_downloader import deep_detect as dd
    from bulk_downloader import learn
    pr = dd.detect_post_reveal_forms(_PR_FORM, base_url="https://pr.test/")[0]
    assert pr["needs_approval"] is True
    assert pr["approval_status"] == "pending"
    cfg = learn.record_post_reveal_decision({}, pr["action"], "approve")
    sm = learn.deep_detect_site_memory(cfg)
    pr2 = dd.detect_post_reveal_forms(
        _PR_FORM, base_url="https://pr.test/", site_memory=sm)[0]
    assert pr2["approval_status"] == "approved"


def test_post_reveal_and_auto_submit_stores_are_separate():
    from bulk_downloader import deep_detect as dd
    from bulk_downloader import learn
    pr = dd.detect_post_reveal_forms(_PR_FORM, base_url="https://pr.test/")[0]
    # Recording a post-reveal decision must not populate the
    # auto_submit_decisions store, and vice versa.
    cfg = learn.record_post_reveal_decision({}, pr["action"], "approve")
    sm = learn.deep_detect_site_memory(cfg)
    assert sm.get("post_reveal_decisions")
    assert not sm.get("auto_submit_decisions")


def test_clean_post_reveal_form_not_required():
    from bulk_downloader import deep_detect as dd
    clean = ('<form method="POST" action="/get-file">'
             '<input type="hidden" name="id" value="9">'
             '<button>Download</button></form>')
    pr = dd.detect_post_reveal_forms(clean, base_url="https://x.test/")[0]
    assert pr["needs_approval"] is False
    assert pr["approval_status"] == "not_required"


# ── Persistence helpers: validation / no-ops ────────────────────────

def test_record_auto_submit_rejects_bad_decision():
    from bulk_downloader import learn
    cfg = learn.record_auto_submit_decision({}, "k", "maybe")
    sm = learn.deep_detect_site_memory(cfg)
    assert not sm.get("auto_submit_decisions")


def test_record_auto_submit_rejects_empty_key():
    from bulk_downloader import learn
    cfg = learn.record_auto_submit_decision({}, "", "approve")
    assert cfg == {}


def test_record_auto_submit_tolerates_non_dict_config():
    from bulk_downloader import learn
    # No crash, returns the input unchanged.
    assert learn.record_auto_submit_decision(None, "k", "approve") is None


def test_record_post_reveal_rejects_bad_decision():
    from bulk_downloader import learn
    cfg = learn.record_post_reveal_decision({}, "https://x/y", "nope")
    sm = learn.deep_detect_site_memory(cfg)
    assert not sm.get("post_reveal_decisions")


# ── F5: Smooth Streaming parser ─────────────────────────────────────

_ISM = (
    '<?xml version="1.0"?>'
    '<SmoothStreamingMedia MajorVersion="2" MinorVersion="0" '
    'Duration="600000000" IsLive="FALSE">'
    '<StreamIndex Type="video" Name="video">'
    '<QualityLevel Index="0" Bitrate="8000000" FourCC="H264" '
    'MaxWidth="1920" MaxHeight="1080"/>'
    '<QualityLevel Index="1" Bitrate="2000000" FourCC="H264" '
    'MaxWidth="1280" MaxHeight="720"/></StreamIndex>'
    '<StreamIndex Type="audio" Name="audio" Language="eng">'
    '<QualityLevel Index="0" Bitrate="128000" FourCC="AACL" '
    'Channels="2" SamplingRate="48000"/></StreamIndex>'
    '<StreamIndex Type="text" Name="cc" Language="eng">'
    '<QualityLevel Index="0" Bitrate="1000"/></StreamIndex>'
    '</SmoothStreamingMedia>'
)


def test_is_smooth_manifest():
    from bulk_downloader import deep_detect as dd
    assert dd.is_smooth_manifest(_ISM) is True
    assert dd.is_smooth_manifest("<html></html>") is False
    assert dd.is_smooth_manifest("") is False


def test_parse_smooth_streaming_extracts_levels_sorted():
    from bulk_downloader import deep_detect as dd
    r = dd.parse_smooth_streaming(_ISM)
    assert r["kind"] == "smooth_streaming"
    assert r["drm_or_encryption_detected"] is False
    # Video sorted highest-first.
    assert [v["height"] for v in r["video"]] == [1080, 720]
    assert r["video"][0]["resolution"]["label"] == "1080p"
    assert len(r["audio"]) == 1 and r["audio"][0]["lang"] == "eng"
    assert len(r["subtitles"]) == 1


def test_parse_smooth_streaming_reports_drm_does_not_bypass():
    from bulk_downloader import deep_detect as dd
    prot = _ISM.replace(
        '<StreamIndex Type="video"',
        '<Protection><ProtectionHeader SystemID="9a04f079-9840-4286-'
        'ab92-e65be0885f95">x</ProtectionHeader></Protection>'
        '<StreamIndex Type="video"')
    r = dd.parse_smooth_streaming(prot)
    assert r["drm_or_encryption_detected"] is True
    assert any("Protection" in w or "DRM" in w for w in r["warnings"])


def test_parse_smooth_streaming_bad_xml_is_not_smooth():
    from bulk_downloader import deep_detect as dd
    r = dd.parse_smooth_streaming("not xml at all")
    assert r["kind"] == "not_smooth"


# ── F5: HLS marker additions ────────────────────────────────────────

def test_hls_init_segment_and_preload_hint_markers():
    from bulk_downloader import deep_detect as dd
    hls = ('#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n'
           '#EXT-X-PRELOAD-HINT:TYPE=PART,URI="p.mp4"\n'
           '#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n'
           'v.m3u8\n')
    h = dd.parse_hls_master(hls)
    assert h.get("init_segment_present") is True
    assert h.get("low_latency") is True


# ── F8: expanded login vocabulary classifies more types ─────────────

def test_f8_passkey_vocab_classifies_webauthn():
    from bulk_downloader import deep_detect as dd
    html = ('<form action="/login"><button>Sign in with a passkey</button>'
            '<script>navigator.credentials.get({})</script></form>')
    best = dd.score_login_page(html, base_url="https://x.test/")["best"]
    assert "webauthn" in best["login_types"]


def test_f8_saml_markers_classify_saml():
    from bulk_downloader import deep_detect as dd
    html = ('<form action="/adfs/ls?wa=wsignin1.0">'
            '<input name="SAMLRequest" value="x"><button>go</button></form>')
    best = dd.score_login_page(html, base_url="https://x.test/")["best"]
    assert "saml" in best["login_types"]
