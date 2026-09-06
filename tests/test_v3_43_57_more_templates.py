"""v3.43.57 — additional adult site templates.

User requested coverage for a list of sites: Nubile.com / Nubile.net,
Nookies, NewSensation(s), Tiny4K, Bang.com, TeenMegaWorld, DogFart,
TeamSkeet, UltraFilms. Some were already covered:

  - WowGirls   → wowgirls_network (existing)
  - VIP4K      → wowgirls_network + vip4k_family (existing)
  - AdultTime  → gamma_kosmos (v3.43.56)
  - Evil Angel → gamma_kosmos (v3.43.56)
  - BangBros   → bangbros_network (existing, speculative)
  - Vixen      → vixen_network (existing, speculative)
  - Kink       → kink_network (existing, speculative)
  - Brazzers   → mindgeek_family (existing, speculative)

New templates added in this release:
  - nubiles_network    (Nubile.com, Nubile.net, NubileFilms,
                          NubilePorn, MomsTeachSex, +)
  - nookies            (nookies.com)
  - new_sensations     (newsensations.com)
  - bang_originals     (bang.com, BangGlamkore, BangTrickery, +)
  - teen_mega_world    (teenmegaworld.net/.com + sub-brands)
  - dogfart_network    (Blacks On Blondes, Cuckold Sessions, +)
  - teamskeet_network  (Exxxtra Small, Innocent High, BFFs, +)
  - ultrafilms         (ultrafilms.com — ACTIVE in user's queue)

Plus tiny4k.com added to wowgirls_network's patterns (same operator).

All NEW templates are speculative — created without HTML samples
in hand. The selectors are broad-pattern best guesses that should
catch most common adult-site HTML, but real-world use will likely
need a teach pass to refine. When user provides HTML samples for
any of these, the template should be updated and the description
flipped from "(speculative)" to "(verified)".
"""
from __future__ import annotations


def test_nubiles_template_registered():
    from bulk_downloader import templates
    t = templates.get("nubiles_network")
    assert t is not None
    assert "nubile" in t["name"].lower()


def test_nubiles_covers_both_dot_com_and_dot_net():
    """User listed both nubile.com and nubile.net — both must match."""
    from bulk_downloader.templates import suggest_for_url
    assert "nubiles_network" in suggest_for_url("https://nubile.com/foo")
    assert "nubiles_network" in suggest_for_url("https://nubile.net/foo")
    assert "nubiles_network" in suggest_for_url("https://nubiles.net/foo")


def test_nubiles_covers_sub_brands():
    """The Nubiles operator runs several sub-brands."""
    from bulk_downloader.templates import suggest_for_url
    for brand in (
        "nubilefilms.com", "nubileporn.com",
        "momsteachsex.com", "momslickteens.com", "momsbangteens.com",
        "stepsiblingscaught.com",
    ):
        url = f"https://{brand}/something"
        suggested = suggest_for_url(url)
        assert "nubiles_network" in suggested, (
            f"{brand} should suggest nubiles_network, got {suggested}")


def test_nookies_template_registered():
    from bulk_downloader import templates
    t = templates.get("nookies")
    assert t is not None


def test_nookies_pattern_matches():
    from bulk_downloader.templates import suggest_for_url
    assert "nookies" in suggest_for_url("https://nookies.com/video/1")


def test_new_sensations_template_registered():
    from bulk_downloader import templates
    t = templates.get("new_sensations")
    assert t is not None
    assert "sensations" in t["name"].lower()


def test_new_sensations_pattern_matches():
    from bulk_downloader.templates import suggest_for_url
    assert "new_sensations" in suggest_for_url("https://newsensations.com/v/1")


def test_bang_originals_template_registered():
    """Distinct from bangbros_network — bang.com is its own platform."""
    from bulk_downloader import templates
    t = templates.get("bang_originals")
    assert t is not None
    bb = templates.get("bangbros_network")
    assert bb is not None
    assert t["id"] != bb["id"]


def test_bang_com_routes_to_bang_originals_not_bangbros():
    """bang.com should NOT match bangbros_network's patterns."""
    from bulk_downloader.templates import suggest_for_url
    suggested = suggest_for_url("https://bang.com/v/123")
    assert "bang_originals" in suggested
    # And bangbros_network should still match bangbros.com
    suggested_bb = suggest_for_url("https://bangbros.com/v/1")
    assert "bangbros_network" in suggested_bb


def test_bang_originals_covers_sub_brands():
    from bulk_downloader.templates import suggest_for_url
    for brand in (
        "bangglamkore.com", "bangtrickery.com",
        "bangrealmilfs.com", "bangrealteens.com",
        "bangcasting.com",
    ):
        url = f"https://{brand}/v"
        assert "bang_originals" in suggest_for_url(url)


def test_teen_mega_world_template_registered():
    from bulk_downloader import templates
    t = templates.get("teen_mega_world")
    assert t is not None


def test_teen_mega_world_covers_main_domains():
    from bulk_downloader.templates import suggest_for_url
    for brand in ("teenmegaworld.net", "teenmegaworld.com",
                    "old-n-young.com", "tutor4k.com",
                    "anal-beauty.com"):
        url = f"https://{brand}/v"
        assert "teen_mega_world" in suggest_for_url(url), (
            f"{brand} should match teen_mega_world")


def test_dogfart_template_registered():
    from bulk_downloader import templates
    t = templates.get("dogfart_network")
    assert t is not None
    assert "dogfart" in t["name"].lower() or "blacks" in t["name"].lower()


def test_dogfart_covers_major_brands():
    from bulk_downloader.templates import suggest_for_url
    for brand in (
        "dogfartnetwork.com", "blacksonblondes.com",
        "cuckoldsessions.com", "watchingmymomgoblack.com",
        "wefuckblackgirls.com", "gloryhole.com",
    ):
        url = f"https://{brand}/v"
        assert "dogfart_network" in suggest_for_url(url), (
            f"{brand} should match dogfart_network")


def test_teamskeet_template_registered():
    from bulk_downloader import templates
    t = templates.get("teamskeet_network")
    assert t is not None


def test_teamskeet_covers_major_brands():
    from bulk_downloader.templates import suggest_for_url
    for brand in (
        "teamskeet.com", "exxxtrasmall.com", "innocenthigh.com",
        "bffs.com", "fostertapes.com", "latinasextapes.com",
        "thisgirlsucks.com", "sislovesme.com",
    ):
        url = f"https://{brand}/v"
        assert "teamskeet_network" in suggest_for_url(url), (
            f"{brand} should match teamskeet_network")


def test_ultrafilms_template_registered():
    """UltraFilms is in user's ACTIVE queue with 777+ URLs pending.
    Even though template is speculative, it must at least be
    registered so the wizard picker can suggest it."""
    from bulk_downloader import templates
    t = templates.get("ultrafilms")
    assert t is not None


def test_ultrafilms_pattern_matches():
    from bulk_downloader.templates import suggest_for_url
    assert "ultrafilms" in suggest_for_url("https://ultrafilms.com/v/123")


# ── Tiny4K belongs to PornPros / Fame Digital, not VIP4K ────────
#
# CORRECTED by the PM-handoff 2026-09-06 template gap report (brief
# matcher-templates.md, A3). v3.43.57 asserted tiny4k was "rolled into
# wowgirls_network"; the report measured the site and it is PornPros /
# Fame Digital. The premise of the two assertions below was wrong, so
# they now pin the CORRECTED owner rather than being deleted -- the
# behavioural question ("who claims tiny4k?") is still asked, and it is
# asked more strictly, because the old form accepted either of two ids.


def test_tiny4k_is_claimed_by_pornpros_not_the_vip4k_family():
    """tiny4k.com is PornPros / Fame Digital. Exactly one template may
    claim it, and it is not wowgirls_network or vip4k_family."""
    from bulk_downloader.templates import suggest_for_url
    suggested = suggest_for_url("https://tiny4k.com/v/1")
    assert suggested == ["pornpros_tiny4k"], suggested
    assert "wowgirls_network" not in suggested
    assert "vip4k_family" not in suggested


def test_the_vip4k_family_no_longer_claims_tiny4k():
    """The mis-assignment must be REMOVED from the pattern list, not
    merely outranked by a template that happens to sort earlier."""
    from bulk_downloader import templates
    vip = templates.get("vip4k_family")
    assert vip is not None
    joined = " ".join(vip.get("patterns", []))
    assert "tiny4k" not in joined, joined
    # negative control: the rest of the family is untouched
    assert "vip4k" in joined and "black4k" in joined, joined


# ── All speculative templates have safe quality_preference defaults ──


def test_new_templates_use_safe_quality_preference():
    """All v3.43.57 new templates should default quality_preference
    to "2160,1080,720" (or 1080-first for non-4K studios), never
    chase 5K/8K by default."""
    from bulk_downloader import templates
    new_ids = ["nubiles_network", "nookies", "new_sensations",
                 "bang_originals", "teen_mega_world",
                 "dogfart_network", "teamskeet_network", "ultrafilms"]
    for tid in new_ids:
        t = templates.get(tid)
        assert t is not None
        defaults = t.get("config_defaults") or {}
        qpref = defaults.get("quality_preference", "")
        prefs = [p.strip() for p in qpref.split(",")]
        # No 4320 (8K) or 3132/2880 (6K/5K) in the default preference
        for tier in ("4320", "3132", "2880", "3240"):
            assert tier not in prefs, (
                f"{tid}: should not default to {tier} (use 2160-first)")
        # Either 2160 or 1080 must be FIRST in the preference
        assert prefs[0] in ("2160", "1080"), (
            f"{tid}: first preference should be 2160 or 1080, got {prefs[0]}")


def test_new_templates_have_login_selectors():
    """All v3.43.57 new templates should ship login selectors so
    do_login's automated path works without teach (eventually —
    once selectors prove correct in production)."""
    from bulk_downloader import templates
    new_ids = ["nubiles_network", "nookies", "new_sensations",
                 "bang_originals", "teen_mega_world",
                 "dogfart_network", "teamskeet_network", "ultrafilms"]
    for tid in new_ids:
        t = templates.get(tid)
        login = (t.get("learned") or {}).get("login") or {}
        assert login.get("user_field"), f"{tid} missing user_field selectors"
        assert login.get("pass_field"), f"{tid} missing pass_field selectors"
        assert login.get("submit_btn"), f"{tid} missing submit_btn selectors"


def test_new_templates_have_download_selectors():
    """All v3.43.57 new templates must have row_selectors for the
    download pipeline. Speculative or not, at least one selector
    must be present."""
    from bulk_downloader import templates
    new_ids = ["nubiles_network", "nookies", "new_sensations",
                 "bang_originals", "teen_mega_world",
                 "dogfart_network", "teamskeet_network", "ultrafilms"]
    for tid in new_ids:
        t = templates.get(tid)
        dl = (t.get("learned") or {}).get("download") or {}
        rows = dl.get("row_selectors") or []
        assert len(rows) > 0, f"{tid} missing download row_selectors"


def test_new_templates_marked_speculative():
    """Speculative templates must say so in their description so
    users know to run a teach pass on first use."""
    from bulk_downloader import templates
    # nubiles_network, nookies and bang_originals left this list on
    # 2026-09-06: the PM-handoff template gap report (brief
    # matcher-templates.md, C) confirmed their selectors against real
    # HTML, which is exactly the condition this docstring names for
    # dropping the word. The remaining five are still unconfirmed.
    new_ids = ["new_sensations", "teen_mega_world",
                 "dogfart_network", "teamskeet_network", "ultrafilms"]
    for tid in new_ids:
        t = templates.get(tid)
        desc = (t.get("description") or "").lower()
        assert "speculative" in desc, (
            f"{tid}: description should mark it speculative until "
            f"selectors are confirmed against real HTML")
    # ...and the three that were confirmed must no longer claim to be
    # guesses, so this gate keeps teeth in both directions.
    for tid in ("nubiles_network", "nookies", "bang_originals"):
        desc = (templates.get(tid).get("description") or "").lower()
        assert "speculative" not in desc, (
            f"{tid}: selectors are verified; the word misleads the user "
            f"into running an unnecessary teach pass")


# ── Total count regression ───────────────────────────────────────


def test_total_template_count_increased():
    """v3.43.56 had 54 templates. v3.43.57 adds 8, total 62."""
    from bulk_downloader.templates import list_templates
    assert len(list_templates()) >= 62


def test_no_duplicate_template_ids():
    """No two templates share an ID."""
    from bulk_downloader.templates import list_templates
    ids = [t["id"] for t in list_templates()]
    assert len(ids) == len(set(ids)), (
        f"duplicate template IDs: "
        f"{[i for i in set(ids) if ids.count(i) > 1]}")
