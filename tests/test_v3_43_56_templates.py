"""v3.43.56: VIP4K login selectors + Gamma Kosmos template.

User-reported coverage gaps:
  1. The existing wowgirls_network template had only DOWNLOAD
     selectors, not LOGIN. The wizard's teach step had to find
     username/password from scratch for vip4k/wowgirls/black4k
     sites every time. Now the template covers both.
  2. Gamma Entertainment's "Kosmos" React player (Dfxtra,
     Adult Time, Devil's Film, etc) had no template at all. Users
     adding these sites got the 154-selector fallback list which
     usually picks up `#username` but misses the React-specific
     download selectors. Added gamma_kosmos template.

Both templates' selectors are derived from real HTML samples the
user pasted. Test confirms the selectors are present and
correctly shaped.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent


# ── wowgirls_network template — login selectors ──────────────────


def test_wow_template_has_login_block():
    """The wowgirls_network template must include learned.login.*"""
    from bulk_downloader import templates as t
    tpl = t.get("wowgirls_network")
    assert tpl is not None
    learned = tpl.get("learned", {})
    assert "login" in learned, "no login block in wowgirls_network template"


def test_wow_template_login_selectors_match_real_html():
    """Verify the login selectors are the exact ones from the
    vip4k HTML the user provided. If the site ever changes the
    field IDs we'll need to update — but for now these are the
    canonical selectors."""
    from bulk_downloader import templates as t
    login = t.get("wowgirls_network")["learned"]["login"]
    user_sels = login["user_field"]
    pass_sels = login["pass_field"]
    submit_sels = login["submit_btn"]
    # The user's HTML had <input id="login-username"> and
    # <input id="login-password">. Most-specific selectors
    # must be present.
    assert "#login-username" in user_sels
    assert "#login-password" in pass_sels
    # The name attribute selectors are the backup if IDs change
    assert any("_username" in s for s in user_sels)
    assert any("_password" in s for s in pass_sels)
    # Submit list must have at least one usable selector
    assert len(submit_sels) >= 1


def test_wow_template_name_reflects_login_coverage():
    """The template name should mention login now."""
    from bulk_downloader import templates as t
    name = t.get("wowgirls_network")["name"].lower()
    # New name is "WowGirls / VIP4K-network (login + 4 download variants)"
    assert "login" in name


def test_wow_template_still_covers_downloads():
    """Login addition mustn't break the download selectors."""
    from bulk_downloader import templates as t
    dl = t.get("wowgirls_network")["learned"]["download"]
    # All four original variants still present
    assert "a.download__item[data-download]" in dl["row_selectors"]
    assert "div.download-button[data-href]" in dl["row_selectors"]
    assert "a.ct_dl_button[href]" in dl["row_selectors"]
    # Parallel url_attribute list still aligned
    assert len(dl["url_attribute"]) == len(dl["row_selectors"])


# ── gamma_kosmos template ───────────────────────────────────────


def test_gamma_kosmos_template_registered():
    """The new template is registered in the templates list."""
    from bulk_downloader import templates as t
    tpl = t.get("gamma_kosmos")
    assert tpl is not None
    assert tpl["id"] == "gamma_kosmos"


def test_gamma_kosmos_patterns_match_dfxtra():
    """The user's site is dfxtra. The pattern list must contain
    a regex that matches dfxtra.com."""
    from bulk_downloader import templates as t
    patterns = t.get("gamma_kosmos")["patterns"]
    # At least one of the patterns must match dfxtra.com
    test_url = "https://dfxtra.com/some/path"
    assert any(re.search(p, test_url) for p in patterns), (
        f"no pattern matches dfxtra.com — patterns: {patterns}")


def test_gamma_kosmos_login_selectors():
    """Gamma sites use <input id="username"> per the user's HTML.
    Most-specific selector must be present."""
    from bulk_downloader import templates as t
    login = t.get("gamma_kosmos")["learned"]["login"]
    assert "#username" in login["user_field"]
    # Password selector — Gamma usually pairs with #password
    assert "#password" in login["pass_field"]


def test_gamma_kosmos_download_url_pattern():
    """The Kosmos player's download URLs follow
    /movieaction/download/<id>/<quality>/mp4. The row_selectors
    must include a selector keyed off that URL pattern."""
    from bulk_downloader import templates as t
    rows = t.get("gamma_kosmos")["learned"]["download"]["row_selectors"]
    # At least one selector must use /movieaction/download/ as a
    # filter — this disambiguates from random href links
    assert any("/movieaction/download/" in s for s in rows), (
        f"no selector keys off /movieaction/download/ — got {rows}")


def test_gamma_kosmos_skip_button_dismissed():
    """The interstitial 'No Thanks. Continue to Members Area' link
    must be in dismiss_selectors, otherwise workers get stuck on
    the splash page after login."""
    from bulk_downloader import templates as t
    cfg = t.get("gamma_kosmos")["config_defaults"]
    dismiss = cfg.get("dismiss_selectors", "")
    # The exact text or the class name — either is fine
    assert ("SkipPageButton" in dismiss or
            "No Thanks" in dismiss or
            "Continue to Members" in dismiss), (
        f"dismiss_selectors doesn't catch the interstitial: {dismiss}")


def test_gamma_kosmos_quality_preference_safe():
    """Should default to 4K-first (no chasing 5K/6K tiers the
    Gamma CDN doesn't actually serve)."""
    from bulk_downloader import templates as t
    qp = t.get("gamma_kosmos")["config_defaults"]["quality_preference"]
    prefs = [p.strip() for p in qp.split(",")]
    assert "2160" in prefs
    # No 5K/6K (Gamma doesn't serve those tiers anyway)
    for tier in ("3132", "2880", "4320"):
        assert tier not in prefs, (
            f"gamma_kosmos shouldn't prefer {tier} — Gamma doesn't serve it")


def test_gamma_kosmos_covers_known_brands():
    """The patterns list should cover the major Gamma brands.
    User can add more via the site edit form, but the common
    ones should be in the template out of the box."""
    from bulk_downloader import templates as t
    patterns = t.get("gamma_kosmos")["patterns"]
    pattern_text = " ".join(patterns)
    # At minimum: dfxtra (user's site) + at least 2 other Gamma brands
    assert "dfxtra" in pattern_text
    gamma_brands = ["devilsfilm", "adulttime", "girlsway", "evilangel",
                      "burningangel", "transangels", "deviantefamily",
                      "deviantfamily", "familyhookups"]
    matches = sum(1 for b in gamma_brands if b in pattern_text)
    assert matches >= 3, (
        f"only {matches} Gamma brand patterns — should be at least 3")


# ── Integration: templates compile and lookup-by-url works ──────


def test_templates_module_loads_without_error():
    """Smoke test that the new templates don't break imports."""
    from bulk_downloader import templates as t
    all_tpls = t.list_templates()
    assert len(all_tpls) > 0
    # Both new/updated templates appear in the list
    ids = {tpl["id"] for tpl in all_tpls}
    assert "wowgirls_network" in ids
    assert "gamma_kosmos" in ids


def test_suggest_for_dfxtra_url():
    """When a user adds dfxtra.com, the gamma_kosmos template
    must show up in the suggestions."""
    from bulk_downloader import templates as t
    suggested = t.suggest_for_url("https://dfxtra.com/login")
    assert "gamma_kosmos" in suggested, (
        f"gamma_kosmos not suggested for dfxtra.com — got {suggested}")


def test_suggest_for_vip4k_url():
    """When a user adds vip4k.com, wowgirls_network must show up."""
    from bulk_downloader import templates as t
    suggested = t.suggest_for_url("https://vip4k.com/en/login")
    assert "wowgirls_network" in suggested, (
        f"wowgirls_network not suggested for vip4k.com — got {suggested}")
