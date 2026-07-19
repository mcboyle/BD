"""v3.66.246 capture/selector/download/template sweep — regression guards.

Covers the five fixes from the adversarial capture-pipeline sweep plus the
golden-template overwrite protection:

  Issue 1  shared FINISH-sentinel race (onboarding) — per-capture sentinel.
  Issue 2  download-trigger collapse — discriminating modal-scoped row survives
           the normalizer; tag-aware trigger.
  Issue 3a quality.available_resolutions — unioned with the observed ladder.
  Issue 3b login.email — recovered for #user-email (id-relax + timeline).
  Issue 4  over-redaction — long CSS class-chain selectors no longer scrubbed
           as opaque credential tokens (genuine tokens still scrubbed).
  Golden   promote never clobbers an existing gold without a recoverable .bak.

Synthetic + browser-free. Each test derives the repo root itself (the runner
chdirs to a temp dir) and uses tempfile, not pytest fixtures.
"""
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))


# ── synthetic capture fixtures (faithful to the wowgirls onboarding capture) ──

def _wowgirls_like_capture():
    """A capture whose download anchors ARE the rows (no separate opener):
    <a class=ct_dl_button data-framerate=…> inside a dialog, a #user-email login
    input with a non-email type, and a partial in-player quality menu."""
    login_form = {
        "type": 2, "tagName": "form", "attributes": {"class": "loginform"}, "id": 1,
        "childNodes": [
            {"type": 2, "tagName": "input", "id": 2,
             "attributes": {"id": "user-email", "type": "text"}},
            {"type": 2, "tagName": "input", "id": 3,
             "attributes": {"type": "password", "name": "password"}},
            {"type": 2, "tagName": "button", "id": 4, "attributes": {"type": "submit"}},
        ],
    }
    modal = {
        "type": 2, "tagName": "div", "id": 10,
        "attributes": {"role": "dialog", "class": "ct_dl_modal"},
        "childNodes": [
            {"type": 2, "tagName": "a", "id": 11,
             "attributes": {"class": "ct_dl_button", "data-framerate": "60fps",
                            "download": "", "href": "/download-resolution/x2160_60FPS.mp4"},
             "childNodes": [{"type": 3, "textContent": "2160"}]},
            {"type": 2, "tagName": "a", "id": 12,
             "attributes": {"class": "ct_dl_button", "data-framerate": "30fps",
                            "download": "", "href": "/download-resolution/x2160_30FPS.mp4"},
             "childNodes": [{"type": 3, "textContent": "2160"}]},
        ],
    }
    qmenu = {
        "type": 2, "tagName": "div", "id": 22,
        "attributes": {"aria-label": "Open the video quality settings menu"},
        "childNodes": [
            {"type": 2, "tagName": "button", "id": 23, "attributes": {},
             "childNodes": [{"type": 3, "textContent": "540p"}]},
            {"type": 2, "tagName": "button", "id": 24, "attributes": {},
             "childNodes": [{"type": 3, "textContent": "360p"}]},
        ],
    }
    player = {"type": 2, "tagName": "div", "id": 20,
              "attributes": {"aria-label": "video player"},
              "childNodes": [{"type": 2, "tagName": "video", "id": 21,
                              "attributes": {"class": "jw-video jw-reset"}}, qmenu]}
    root = {"type": 2, "tagName": "body", "id": 100,
            "childNodes": [login_form, player, modal]}
    return {
        "url": "https://auth.wowgirls.com/login",
        "origin": "https://auth.wowgirls.com",
        "host": "auth.wowgirls.com",
        "captured_at": "2026-06-14T17:52:12+00:00",
        "dom_log": [{"type": "meta", "data": {}},
                    {"type": "full_snapshot", "data": {"node": root}}],
        "network_log": [
            {"url": "https://content-video2.wowgirls.com/p/x2160_60FPS.mp4",
             "type": "media", "status": 206,
             "response_headers": [{"name": "content-type", "value": "video/mp4"}]},
            {"url": "https://content-video2.wowgirls.com/p/x4320_60FPS.mp4",
             "type": "media", "status": 206,
             "response_headers": [{"name": "content-type", "value": "video/mp4"}]},
        ],
        "action_timeline": [
            {"ts": 1, "selector": "#user-email", "role": "login/submit", "tag": "input",
             "effect": {"req_count": 0, "direct_media": 0}},
            {"ts": 2, "selector": "div.loginform-submit-button", "role": "login/submit",
             "tag": "div", "effect": {"req_count": 62, "direct_media": 0}},
            {"ts": 3, "selector": "video.jw-video.jw-reset", "role": "media element",
             "tag": "video", "effect": {"req_count": 3, "direct_media": 0}},
            {"ts": 4,
             "selector": ("div.jw-icon.jw-icon-inline.jw-button-color.jw-reset."
                          "jw-icon-settings.jw-settings-submenu-button"),
             "role": "quality select", "tag": "div",
             "effect": {"req_count": 0, "direct_media": 0}},
            {"ts": 5, "selector": 'a.ct_dl_button[data-framerate="60fps"]',
             "role": "download link", "tag": "a",
             "effect": {"req_count": 3, "direct_media": 1}},
            {"ts": 6, "selector": 'a.ct_dl_button[data-framerate="30fps"]',
             "role": "download link", "tag": "a",
             "effect": {"req_count": 3, "direct_media": 1}},
        ],
    }


def _build(capture):
    from build_template_from_wacz import build_template
    tmp = tempfile.mkdtemp()
    wacz = os.path.join(tmp, "synth.wacz")
    with zipfile.ZipFile(wacz, "w") as z:
        z.writestr("archive/capture.json", json.dumps(capture))
    return build_template(Path(wacz))


# ── Issue 4: over-redaction of CSS class-chain selectors ──────────────────────

def test_issue4_long_css_chain_selector_is_not_scrubbed():
    from bulk_downloader.capture_artifact_redact import redact_value, _looks_like_opaque_token
    chains = [
        ("span.jw-icon.jw-icon-inline.jw-button-color.jw-reset."
         "jw-icon-settings.jw-settings-submenu-button"),
        "div.jw-icon.jw-icon-inline.jw-button-color.jw-reset.jw-icon-settings.jw-x",
        "button.jw-reset-text.jw-settings-content-item.some-extra-long-class-name",
    ]
    for s in chains:
        assert len(s) >= 40, "fixture must exceed the opaque-token length floor"
        assert not _looks_like_opaque_token(s), f"CSS chain flagged as token: {s}"
        assert redact_value(s) == s, f"CSS chain was scrubbed: {s}"


def test_issue4_genuine_opaque_tokens_still_scrubbed():
    from bulk_downloader.capture_artifact_redact import redact_value, PLACEHOLDER
    tokens = [
        "eyJ0aGlzIjoiYSBmYWtlIHRva2VuIHdpdGggbG90cyBvZiBlbnRyb3B5In0xMjM0",
        "aGVsbG93b3JsZGZvb2JhcjEyMzQ1Njc4OWFiY2RlZmdoaWprbG1ub3BxcnN0dXY",
    ]
    for t in tokens:
        assert redact_value(t) == PLACEHOLDER, f"token not scrubbed: {t}"
    # signed query and kv-secret must still scrub
    sq = "https://x.com/v.mp4?token=abcdefghijklmnopqrstuvwxyz123456&Expires=9"
    assert redact_value(sq) != sq


def test_issue4_derived_steps_keep_full_selectors():
    # Simulate capture-time redaction: the action_timeline `selector` is a bare
    # leaf string scrubbed by redact_value when the WACZ is written. On the
    # pre-fix redactor the long JW class-chain is destroyed to <scrubbed>; after
    # the fix it survives, so the builder's derived step keeps the real selector.
    from bulk_downloader.capture_artifact_redact import redact_value
    cap = _wowgirls_like_capture()
    for e in cap["action_timeline"]:
        e["selector"] = redact_value(e["selector"])   # what capture write does
    tpl = _build(cap)
    joined = "\n".join(tpl.get("workflow", {}).get("derived_steps", []))
    assert "jw-settings-submenu-button" in joined, \
        "long JW selector was scrubbed at capture time"
    assert "<scrubbed>" not in joined, "a derived step selector was scrubbed"


# ── Issue 2: download trigger / discriminating modal-scoped row ───────────────

def test_issue2_trigger_is_discriminating_not_generic():
    dl = _build(_wowgirls_like_capture())["selectors"]["download"]
    assert dl.get("trigger") == "a.ct_dl_button[data-framerate]", dl.get("trigger")
    assert dl.get("trigger") != "a[download]"


def test_issue2_modal_scoped_discriminating_row_present_and_survives_normalizer():
    import bulk_downloader.template_normalize as TN
    draft = _build(_wowgirls_like_capture())
    rows = draft["selectors"]["download"].get("row_selectors") or []
    assert any(r == '[role="dialog"] a.ct_dl_button[data-framerate]' for r in rows), rows
    cand = TN.normalize_draft(draft)
    nrows = cand["selectors"]["download"].get("row_selectors") or []
    assert any("ct_dl_button" in r for r in nrows), \
        f"discriminating modal-scoped row dropped by normalizer: {nrows}"
    # never auto-enabled
    assert (cand.get("status") or cand.get("template_status")) != "enabled"


def test_issue2_button_download_step_maps_to_trigger_not_row():
    """A <button> download step is an OPENER (trigger), never a row."""
    cap = _wowgirls_like_capture()
    cap["dom_log"] = [{"type": "meta", "data": {}}, {"type": "full_snapshot", "data": {"node":
        {"type": 2, "tagName": "div", "id": 1, "attributes": {"class": "app"}, "childNodes": [
            {"type": 2, "tagName": "button", "id": 2, "attributes": {"class": "dl"}}]}}}]
    cap["action_timeline"] = [
        {"ts": 1, "selector": "button.dl", "role": "download link", "tag": "button",
         "effect": {"req_count": 2, "direct_media": 1}}]
    dl = _build(cap)["selectors"].get("download", {})
    assert dl.get("trigger") == "button.dl", dl.get("trigger")
    assert "button.dl" not in (dl.get("row_selectors") or []), \
        "a single button trigger must not be injected as a row"


# ── Issue 3a / 3b: quality ladder + login email ───────────────────────────────

def test_issue3a_quality_resolutions_unioned_with_network():
    tpl = _build(_wowgirls_like_capture())
    ar = tpl["selectors"].get("quality", {}).get("available_resolutions") or []
    assert 4320 in ar and 2160 in ar, ar          # observed-but-not-in-menu kept
    assert 540 in ar and 360 in ar, ar             # in-menu kept
    assert ar == sorted(ar, reverse=True)          # descending, deduped


def test_issue3b_login_email_recovered_for_user_email():
    login = _build(_wowgirls_like_capture())["selectors"].get("login", {})
    assert login.get("email") == "input#user-email", login.get("email")


# ── Issue 1: per-capture FINISH sentinel + CANCEL sibling ─────────────────────

def test_issue1_onboarding_finish_sentinels_are_unique():
    from onboard_site_template import build_capture_command
    a = build_capture_command("wowgirls", "https://auth.wowgirls.com/login", ":99")
    b = build_capture_command("wowgirls", "https://auth.wowgirls.com/login", ":99")
    assert a["finish_file"] != b["finish_file"], "same-site launches collided"
    assert a["finish_file"].endswith(".FINISH")
    assert "--finish-file" in a["capture_cmd"]


def test_issue1_cancel_sibling_derives_from_finish_file():
    """capture_session._wait_for_finish must treat <stem>.CANCEL (sibling of the
    --finish-file) as the discard signal, and the default path stays out_dir/CANCEL."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "capture_session", str(_ROOT / "tools" / "capture_session.py"))
    cs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cs)

    d = Path(tempfile.mkdtemp())
    fin = d / "host_site_ts.FINISH"
    # sibling CANCEL of the finish-file -> "cancel"
    (d / "host_site_ts.CANCEL").write_text("", "utf-8")
    assert cs._wait_for_finish(d, 2.0, str(fin)) == "cancel"

    # default path (no finish-file) uses out_dir/CANCEL
    d2 = Path(tempfile.mkdtemp())
    (d2 / "CANCEL").write_text("", "utf-8")
    assert cs._wait_for_finish(d2, 2.0, None) == "cancel"

    # a per-capture finish-file is detected as "finish"
    d3 = Path(tempfile.mkdtemp())
    f3 = d3 / "a_b_c.FINISH"
    f3.write_text("", "utf-8")
    assert cs._wait_for_finish(d3, 2.0, str(f3)) == "finish"


# ── Golden: promote never clobbers a gold without a recoverable backup ────────

def _gold(host, rows):
    return {"host": host, "status": "enabled", "schema": "reviewed",
            "network_patterns": ["/media/x.mp4"], "resolutions": [1080, 720],
            "selectors": {"download": {"trigger": "button.open", "row_selectors": rows},
                          "login": {"email": "input#e", "password": "input#p"}}}


def test_gold_promote_backs_up_existing_before_overwrite():
    import bulk_downloader.template_manager as TM
    rd = Path(tempfile.mkdtemp())
    dd = Path(tempfile.mkdtemp())
    (rd / "x.example.template.json").write_text(
        json.dumps(_gold("x.example", ['[role="dialog"] a.rich[data-q]'])), "utf-8")
    thin = {"schema_version": "bulk_downloader.template.review_candidate.v1",
            "host": "x.example", "status": "review_ready",
            "network_patterns": ["/media/y.mp4"], "resolutions": [480],
            "selectors": {"download": {"trigger": "a[download]"}}}
    (dd / "x.example.template-draft.json").write_text(json.dumps(thin), "utf-8")

    res = TM.promote_draft("x.example.template-draft.json", enable=True,
                           reviewed_dir=rd, drafts_dir=dd)
    assert res.get("ok")
    bak = rd / "x.example.template.json.bak"
    assert bak.exists(), "existing gold was overwritten with no .bak"
    restored = json.loads(bak.read_text())
    assert restored["selectors"]["download"]["row_selectors"] == \
        ['[role="dialog"] a.rich[data-q]'], "the rich gold was not preserved in .bak"
    # the .bak must not be discoverable as a template
    assert "x.example.template.json.bak" not in [p.name for p in rd.glob("*.template.json")]


def test_gold_first_promote_makes_no_spurious_backup():
    import bulk_downloader.template_manager as TM
    rd = Path(tempfile.mkdtemp())
    dd = Path(tempfile.mkdtemp())
    cand = {"schema_version": "bulk_downloader.template.review_candidate.v1",
            "host": "new.example", "status": "review_ready",
            "network_patterns": ["/media/y.mp4"], "resolutions": [1080],
            "selectors": {"download": {"trigger": "button.dl",
                                       "row_selectors": ['[role="dialog"] a[download]']}}}
    (dd / "new.example.template-draft.json").write_text(json.dumps(cand), "utf-8")
    TM.promote_draft("new.example.template-draft.json", enable=True,
                     reviewed_dir=rd, drafts_dir=dd)
    assert not (rd / "new.example.template.json.bak").exists(), \
        "first promote should not leave a .bak (nothing to back up)"
