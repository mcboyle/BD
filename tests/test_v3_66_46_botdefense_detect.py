"""F9/F10 detect-side: named bot-defense classification + fingerprinting
signal detection. Detect-and-report only — no evasion."""
from bulk_downloader.deep_detect import (
    scan_blockers, classify_bot_defenses, detect_fingerprinting_signals,
)


def test_named_systems_from_markers():
    html = '<script src="https://datadome.co/t.js"></script><div id="px-captcha"></div>'
    names = classify_bot_defenses(html)
    assert "DataDome" in names
    assert "PerimeterX/HUMAN" in names


def test_akamai_grouped_from_cryptic_markers():
    # _abck / bm_sz are Akamai cookies — should surface a human name.
    assert "Akamai Bot Manager" in classify_bot_defenses("set-cookie: _abck=...; bm_sz=...")


def test_cloudflare_turnstile_grouped():
    assert "Cloudflare" in classify_bot_defenses('<div class="cf-turnstile"></div>')


def test_fingerprinting_canvas_webgl_audio():
    js = ('<script>c.toDataURL();gl.getExtension("WEBGL_debug_renderer_info");'
          'new AudioContext().createOscillator();</script>')
    sigs = detect_fingerprinting_signals(js)
    assert set(sigs) >= {"canvas", "webgl", "audio"}


def test_lone_font_signal_suppressed():
    # offsetWidth alone is too weak — benign uses everywhere.
    assert detect_fingerprinting_signals("<script>el.offsetWidth</script>") == []


def test_font_signal_kept_with_stronger_signal():
    js = "<script>c.toDataURL(); el.offsetWidth;</script>"
    sigs = detect_fingerprinting_signals(js)
    assert "canvas" in sigs and "fonts" in sigs


def test_scan_blockers_surfaces_named_and_fingerprinting():
    html = ('<script src="datadome.co/t.js"></script>'
            '<script>document.createElement("canvas").toDataURL();</script>')
    out = scan_blockers(html, base_url="https://x.com/")
    assert out["bot_defense_systems"] == ["DataDome"]
    assert "canvas" in out["fingerprinting"]
    # presence forces the approval gate
    assert out["do_not_auto_submit"] is True


def test_raw_bot_defenses_still_present_for_backcompat():
    out = scan_blockers('<div id="px-captcha"></div>', base_url="https://x.com/")
    assert out["bot_defenses"]            # raw markers retained
    assert out["bot_defense_systems"]     # named added alongside


def test_clean_page_no_false_positive():
    out = scan_blockers('<video><source src="https://x/v.mp4"></video>',
                        base_url="https://x.com/")
    assert out["bot_defense_systems"] == []
    assert out["fingerprinting"] == []
