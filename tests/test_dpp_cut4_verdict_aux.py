"""D++ cut 4 (Layer D + E) — aux content + the honest verdict layer.

Recognizer (pure, player_recognition):
  - recognize_aux(network_log, html, config_seam) -> {captions, audio,
    storyboard, chapters, ssai}  (WebVTT/SRT <track>, EXT-X-MEDIA, DASH text/
    audio AdaptationSet, storyboard sprites, chapters, SCTE-35/VMAP/VAST). F2.
  - detect_drm STRUCTURAL signals (Gap 1, folded from cut 3): bare EME
    `encrypted` event, `keySystems` config, DASH `<ContentProtection>` — in
    addition to the vendor needles.
  - recognize_protection now emits `gating_cookie_names` (Gap 2 ceiling note:
    known anti-bot/auth names only; no per-request scope map under redaction).

Builder-side (build_template_from_wacz; core reuse is legitimate here):
  - reject_noise(network_log) -> explicit NOT-media set, reusing core
    honeypot_score (subdomain-boundary tracker match) + bad_terms.
  - classify(framework, protocol, protection, aux, noise, selectors) ->
    {site_type, downloadable, requires_runtime_capture, recommended_path,
     reasons}. Consumes cut-3 protection: drm -> not_downloadable;
     anti_bot/signed -> pick_test_promote.
  - confidence rubric weighted by recovered signal (not "a pattern exists").
  - template self-test: selectors_resolve count vs captured HTML.
  - gold-merge guard: a thin draft never overwrites a selector-rich reviewed gold.
  - provenance: per-field signal source. schema_version v1 -> v2.

SYNTHETIC fixtures only. Pure/stdlib. RED-first.
"""
import os
import sys
import json
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import player_recognition as pr            # noqa: E402
import build_template_from_wacz as btw     # noqa: E402


def _e(url, *, ct=None, status="200", body=None):
    rh = []
    if ct:
        rh.append({"name": "content-type", "value": ct})
    e = {"url": url, "response_status": status, "response_headers": rh}
    if body is not None:
        e["response_body"] = body
    return e


# --------------------------------------------------------------------------- #
# recognize_aux — caption / audio / storyboard / chapters
# --------------------------------------------------------------------------- #
def _aux(net, *, html="", config_seam=None):
    out = pr.recognize_aux(net, html=html, config_seam=config_seam)
    assert isinstance(out, dict)
    for k in ("captions", "audio", "storyboard", "chapters", "ssai"):
        assert k in out, f"missing aux key {k}"
    return out


def test_aux_caption_track_from_html():
    html = '<track kind="subtitles" srclang="en" src="/subs/en.vtt" label="English">'
    out = _aux([], html=html)
    assert out["captions"]
    assert any("en.vtt" in c.get("url_shape", "") for c in out["captions"])


def test_aux_caption_from_hls_ext_x_media():
    m3u8 = ('#EXTM3U\n'
            '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",LANGUAGE="en",URI="sub/en.m3u8"\n')
    out = _aux([_e("https://x/master.m3u8", ct="application/vnd.apple.mpegurl", body=m3u8)])
    assert out["captions"]


def test_aux_multi_audio_from_hls():
    m3u8 = ('#EXTM3U\n'
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",LANGUAGE="en",NAME="English"\n'
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",LANGUAGE="fr",NAME="French"\n')
    out = _aux([_e("https://x/master.m3u8", ct="application/vnd.apple.mpegurl", body=m3u8)])
    assert len(out["audio"]) >= 2


def test_aux_dash_text_adaptationset():
    mpd = ('<MPD><Period><AdaptationSet contentType="text" mimeType="text/vtt" lang="es">'
           '<Representation/></AdaptationSet></Period></MPD>')
    out = _aux([_e("https://x/manifest.mpd", ct="application/dash+xml", body=mpd)])
    assert out["captions"]


def test_aux_storyboard_and_chapters():
    html = ('<track kind="chapters" src="/ch.vtt">'
            '<track kind="metadata" label="thumbnails" src="/storyboard.vtt">')
    out = _aux([], html=html)
    assert out["chapters"]
    assert out["storyboard"]


def test_aux_ssai_scte35_and_vmap():
    m3u8 = '#EXTM3U\n#EXT-X-DATERANGE:ID="ad1",SCTE35-OUT=0xFC\n#EXT-X-CUE-OUT:30\n'
    net = [_e("https://x/live.m3u8", ct="application/vnd.apple.mpegurl", body=m3u8),
           _e("https://ads.example/vmap?p=1", ct="application/xml")]
    out = _aux(net)
    assert out["ssai"]


def test_aux_empty_safe():
    out = _aux([])
    assert out["captions"] == [] and out["ssai"] == []


# --------------------------------------------------------------------------- #
# Gap 1 — structural DRM signals folded into detect_drm
# --------------------------------------------------------------------------- #
def test_drm_structural_encrypted_event():
    drm, reasons = pr.detect_drm('<script>v.addEventListener("encrypted", f)</script>')
    assert drm and any("encrypted" in r for r in reasons)


def test_drm_structural_keysystems():
    drm, reasons = pr.detect_drm('<script>player.configure({keySystems:{}})</script>')
    assert drm


def test_drm_structural_dash_contentprotection():
    drm, reasons = pr.detect_drm('<MPD><ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011"/></MPD>')
    assert drm


def test_drm_vendor_needle_still_works():
    drm, _ = pr.detect_drm("<script>com.widevine.alpha</script>")
    assert drm


def test_no_drm_on_plain_video():
    drm, _ = pr.detect_drm("<video src='/v.mp4'></video>")
    assert drm is False


# --------------------------------------------------------------------------- #
# Gap 2 — gating_cookie_names (ceiling: known anti-bot/auth names only)
# --------------------------------------------------------------------------- #
def test_protection_gating_cookie_names():
    out = pr.recognize_protection(
        [], cookies=[{"name": "cf_clearance", "value": "x"},
                     {"name": "sessionid", "value": "y"},
                     {"name": "_ga", "value": "z"}])
    assert "gating_cookie_names" in out
    assert "cf_clearance" in out["gating_cookie_names"]
    assert "_ga" not in out["gating_cookie_names"]  # analytics is not a gate


# --------------------------------------------------------------------------- #
# reject_noise (builder-side, reuses core honeypot/bad_terms)
# --------------------------------------------------------------------------- #
def test_reject_noise_scores_down_trackers():
    net = [_e("https://ads.doubleclick.net/ad?x=1"),
           _e("https://www.googletagmanager.com/gtm.js"),
           _e("https://cdn.example.com/v/1080.mp4", ct="video/mp4")]
    out = btw.reject_noise(net)
    rejected = " ".join(r["url_shape"] for r in out["rejected"])
    assert "doubleclick.net" in rejected
    assert "googletagmanager.com" in rejected
    assert "1080.mp4" not in rejected  # real media is NOT noise


def test_reject_noise_subdomain_boundary():
    # mydoubleclick.net is NOT doubleclick.net (boundary respected, via core)
    out = btw.reject_noise([_e("https://mydoubleclick.net/v.mp4", ct="video/mp4")])
    assert all("mydoubleclick.net" not in r["url_shape"] for r in out["rejected"])


# --------------------------------------------------------------------------- #
# classify() — the honest verdict
# --------------------------------------------------------------------------- #
def test_classify_drm_not_downloadable():
    v = btw.classify(framework="shaka", protocol={"primary": "dash"},
                     protection={"drm": True}, aux={}, noise={}, selectors={})
    assert v["site_type"] == "drm_protected"
    assert v["downloadable"] is False
    assert v["recommended_path"] == "not_downloadable"


def test_classify_signed_hls_runtime_capture():
    v = btw.classify(framework="jwplayer", protocol={"primary": "hls"},
                     protection={"drm": False, "signing": {"schemes": ["akamai_token"]},
                                 "anti_bot": ["cloudflare"]},
                     aux={}, noise={}, selectors={"player": {}})
    assert v["site_type"].startswith("signed") or v["requires_runtime_capture"] is True
    assert v["recommended_path"] == "pick_test_promote"


def test_classify_clean_progressive_auto_template():
    v = btw.classify(framework="video.js", protocol={"primary": "progressive"},
                     protection={"drm": False, "signing": {"schemes": []}, "anti_bot": []},
                     aux={}, noise={},
                     selectors={"player": {"container": "video"}})
    assert v["site_type"] == "direct_progressive"
    assert v["downloadable"] is True
    assert v["recommended_path"] == "auto_template"


def test_classify_iframe_embed():
    v = btw.classify(framework="iframe_embed", protocol={"primary": None},
                     protection={"drm": False}, aux={}, noise={}, selectors={})
    assert v["site_type"] == "iframe_embed"


# --------------------------------------------------------------------------- #
# confidence rubric (recovered-signal weighted)
# --------------------------------------------------------------------------- #
def test_confidence_rubric_rewards_recovery():
    rich = btw.confidence_rubric(
        selectors={"player": {"container": "video", "play_button": "x"}, "quality": {"available_resolutions": [1080]}},
        renditions=[{"resolution": 1080}, {"resolution": 720}],
        aux={"captions": [{"url_shape": "/en.vtt"}]},
        selectors_resolve={"checked": 3, "resolved": 3})
    thin = btw.confidence_rubric(selectors={}, renditions=[], aux={},
                                 selectors_resolve={"checked": 0, "resolved": 0})
    assert rich["score"] > thin["score"]
    assert rich["band"] in ("medium", "high")
    assert thin["band"] == "low"


# --------------------------------------------------------------------------- #
# template self-test — selectors_resolve vs captured HTML
# --------------------------------------------------------------------------- #
def test_selectors_resolve_counts():
    html = '<video></video><button aria-label="Play"></button>'
    sel = {"player": {"container": "video", "play_button": '[aria-label*="play" i]'},
           "quality": {"open_menu": "button.missing-thing"}}
    out = btw.selectors_resolve(sel, html)
    assert out["checked"] >= 3
    assert out["resolved"] >= 2          # video + play resolve
    assert out["resolved"] < out["checked"]  # the missing one does not


# --------------------------------------------------------------------------- #
# gold-merge guard — thin draft never overwrites a richer reviewed gold
# --------------------------------------------------------------------------- #
def test_gold_merge_guard_blocks_thin_overwrite(tmp_path=None):
    d = tempfile.mkdtemp()
    gold = os.path.join(d, "site.template.json")
    rich = {"selectors": {"player": {"container": "video", "play_button": "x"},
                          "quality": {"open_menu": "y"}, "download": {"trigger": "z"}},
            "template_status": "enabled"}
    json.dump(rich, open(gold, "w"))
    thin = {"selectors": {"player": {"container": "video"}}, "template_status": "draft_requires_review"}
    res = btw.gold_merge_guard(gold, thin)
    assert res["blocked"] is True
    # original gold untouched
    assert json.load(open(gold))["template_status"] == "enabled"


def test_gold_merge_guard_allows_when_no_existing():
    d = tempfile.mkdtemp()
    out = os.path.join(d, "new.template.json")
    res = btw.gold_merge_guard(out, {"selectors": {"player": {"container": "video"}}})
    assert res["blocked"] is False


# --------------------------------------------------------------------------- #
# Draft integration — verdict + tracks + provenance + schema_version v2
# --------------------------------------------------------------------------- #
_JW_HTML = (
    '<div id="jwplayer-0" class="jwplayer jw-reset"><div class="jw-icon-display"></div></div>'
    '<track kind="subtitles" srclang="en" src="/subs/en.vtt">'
    '<script>jwplayer("jwplayer-0").setup({playlist:[{sources:['
    '{file:"https://media.example.com/v/abc_1080.mp4?token=SEC",label:"1080p",height:1080,type:"video/mp4"}'
    ']}]});</script>'
)
_NET = [_e("https://cdn.example.com/v/1080.mp4?hdnts=SEC", ct="video/mp4"),
        _e("https://www.google-analytics.com/collect?v=1")]


def _build_draft():
    orig = btw._load_capture
    btw._load_capture = lambda p: {
        "dom_log": [{"type": "full_snapshot", "html": _JW_HTML}],
        "network_log": _NET, "url": "https://site.example.com/watch",
        "host": "site.example.com", "captured_at": "2026-06-18T00:00:00Z",
        "dom_log_count": 1, "network_log_count": len(_NET),
    }
    try:
        from pathlib import Path
        return btw.build_template(Path(tempfile.NamedTemporaryFile(suffix=".wacz", delete=False).name))
    finally:
        btw._load_capture = orig


def test_draft_schema_version_v2():
    d = _build_draft()
    assert d["schema_version"] == "bulk_downloader.template_draft.v2"


def test_draft_carries_verdict():
    d = _build_draft()
    assert "verdict" in d
    for k in ("site_type", "downloadable", "requires_runtime_capture", "recommended_path"):
        assert k in d["verdict"]
    # signed + akamai_token -> not a clean auto_template
    assert d["verdict"]["recommended_path"] in ("pick_test_promote", "not_downloadable")


def test_draft_carries_tracks_and_noise():
    d = _build_draft()
    assert d["recognition"].get("tracks") is not None
    assert d["recognition"]["tracks"]["captions"]          # the en.vtt track
    # the google-analytics beacon is recognized as noise, not media
    noise_blob = json.dumps(d["recognition"].get("noise") or {})
    assert "google-analytics" in noise_blob


def test_draft_provenance_stamped():
    d = _build_draft()
    assert d.get("provenance"), "provenance map must be present"
    # F2: provenance carries SIGNAL names, never values/tokens
    assert "SEC" not in json.dumps(d["provenance"])


def test_draft_self_test_present():
    d = _build_draft()
    assert "selectors_resolve" in d
    assert d["selectors_resolve"]["checked"] >= 1


def test_draft_no_token_leak_anywhere():
    d = _build_draft()
    assert "SEC" not in json.dumps(d), "redacted token leaked into the draft"
