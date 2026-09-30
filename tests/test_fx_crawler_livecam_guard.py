"""fx-crawler-livecam-guard (ORDER-FIX-SWEEP-T175.md Cut E; results/test1/REPROVE-T177.md dfxtra rows).

test1 dfxtra, T177: /api/discovery/scenes/start run 68d6b461a168 (listing https://members.dfxtra.com/en, newest_n=1)
COMPLETED with scene_shapes ["/livecam/autologin"] and auto-queued https://members.dfxtra.com/livecam/autologin -- the
members home "Live Cams" nav card, an autologin hop to a partner cam site. _scene_cohort's one-thumbnail guard names that
URL, but drops a cohort only when it holds ONE thumbnail; the recorded label (both segments fixed) means >= 2 destinations.

r2 (lens bd-review-correctness-D2-D REFUTE of r1): a denylist cannot close the nav/utility space (31 of 35 probed routes
still queued). The rule is now positive: without a product scene-URL-rule row, a cohort must differ by an id or slug.
Plus the product's own _NON_SCENE_HINTS read per path segment (script routes like login.php), and partner cam
subdomains / two-level public suffixes as off-site.

r3 (lens REFUTE of r2, false drops): a hint word counts only as the WHOLE route segment (or a script stem) in the first
two path segments, never for a scene-rule URL; scene slugs are titles. _NON_SCENE_HINTS is back as on BASE (the product
rule matches it as substrings for every site). The lens's 21 title slugs and tube /v/<slug>-<n> are must-KEEP tests.

Fixture: synthetic anchors in the crawler's _ANCHOR_JS row shape (the crawl DB keeps only the queued URL); the probe
shape is the lens's: the same route thumbnailed on the members and www host, plus three text nav links, no scene cards.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from bulk_downloader.scene_crawler import _same_site, _scene_cohort

BD_GATE_SCOPE = "module"

LISTING = "https://members.dfxtra.com/en"


def _a(url, img, text=""):
    return {"url": url, "has_img": img, "text": text, "title": "", "aria": "", "img_alt": "", "nearest": ""}


def _nav():
    return [_a("https://members.dfxtra.com/en/channels", False, "Channels"),
            _a("https://members.dfxtra.com/en/models", False, "Models"),
            _a("https://members.dfxtra.com/en/videos", False, "Videos")]


def _scene_cards():
    return [_a(f"https://members.dfxtra.com/en/video/blacksonblondes/Scene-{n}/{291190 + n}", True, f"Scene {n}")
            for n in range(4)]


def _pair(route):
    return [_a("https://members.dfxtra.com" + route, True, "Banner"),
            _a("https://www.dfxtra.com" + route, True, "Banner")] + _nav()


# The lens's 35 probed routes (VERDICT-bd-review-correctness-D2-D.md): 4 r1 already dropped + 31 it did not.
LENS_ROUTES = [
    "/livecam/autologin", "/LiveCam/AutoLogin", "/en/livecam/", "/live/autologin?x",
    "/en/help", "/en/support", "/members/membership", "/en/forgot-password",
    "/en/livecam-autologin", "/live-cam/go", "/live/sso", "/cams/enter", "/cam/room", "/en/live", "/login.php/sso",
    "/members/logout.php", "/tour/join.php", "/out/partner", "/go/partner", "/redirect/partner", "/store/item",
    "/shop/dvd", "/en/chat", "/members/profile", "/en/settings", "/en/favorites", "/en/password", "/en/subscribe",
    "/en/trial", "/en/renew", "/en/sites", "/en/network", "/en/vod", "/en/gift", "/en/affiliates",
]


@pytest.mark.parametrize("route", LENS_ROUTES)
def test_thumbnailed_nav_or_utility_pair_is_not_a_scene(route):
    scenes, shapes = _scene_cohort(_pair(route), LISTING)
    got = [s["url"] for s in scenes]
    assert got == [], f"FX_CRAWLER_LIVECAM_QUEUED_AS_SCENE: {route} -> {got} shapes={shapes}"


def test_recorded_shape_two_queries():
    anchors = [_a("https://members.dfxtra.com/livecam/autologin?src=home", True, "Live Cams"),
               _a("https://members.dfxtra.com/livecam/autologin?src=nav", True, "Live Cams")] + _nav()
    assert _scene_cohort(anchors, LISTING)[0] == [], "FX_CRAWLER_LIVECAM_QUEUED_AS_SCENE: two-queries"


def test_a_row_of_different_thumbnailed_nav_words_is_not_a_scene_cohort():
    anchors = [_a(f"https://members.dfxtra.com/en/{w}", True, w) for w in ("help", "vod", "sites", "gift")] + _nav()
    got = [s["url"] for s in _scene_cohort(anchors, LISTING)[0]]
    assert got == [], f"FX_CRAWLER_NAV_WORD_COHORT_QUEUED: {got}"


def test_partner_cam_subdomain_is_off_site():
    anchors = [_a(f"https://live.dfxtra.com/room/{n}-model-{n}", True, "Live") for n in range(3)] + _nav()
    got = [s["url"] for s in _scene_cohort(anchors, LISTING)[0]]
    assert got == [], f"FX_CRAWLER_PARTNER_HOST_QUEUED: {got}"


def test_two_level_public_suffix_is_not_one_site():
    assert not _same_site("https://cams.partner.co.uk/x", "https://members.site.co.uk/en"), "FX_CRAWLER_CO_UK_SAME_SITE"
    assert _same_site("https://www.site.co.uk/x", "https://members.site.co.uk/en")
    assert _same_site("https://www.dfxtra.com/x", LISTING)


def test_real_scene_cards_still_win_next_to_the_banners():
    anchors = _pair("/livecam/autologin")[:2] + _scene_cards() + _nav()
    paths = [urlsplit(s["url"]).path for s in _scene_cohort(anchors, LISTING)[0]]
    assert len(paths) == 4 and all(p.startswith("/en/video/blacksonblondes/") for p in paths), paths


def test_negative_control_word_inside_a_slug_is_still_a_scene():
    cards = [_a(f"https://members.dfxtra.com/en/video/blacksonblondes/Live-Cam-Login-{n}/{300 + n}", True)
             for n in range(3)]
    assert len(_scene_cohort(cards + _nav(), LISTING)[0]) == 3


def test_negative_control_scene_rule_url_under_a_channel_named_join_is_kept():
    cards = [_a(f"https://members.dfxtra.com/en/video/join/Some-Title-{n}/{400 + n}", True) for n in range(3)]
    assert len(_scene_cohort(cards + _nav(), LISTING)[0]) == 3


def test_negative_control_hintless_id_slug_cohort_is_kept():
    # hqporner-shaped: no product scene hint in the path, but the cards differ by an id-slug.
    cards = [_a(f"https://hqporner.example/hdporn/{12000 + n}-some_scene_{n}.html", True) for n in range(3)]
    got = _scene_cohort(cards, "https://hqporner.example/top/month")[0]
    assert len(got) == 3, [s["url"] for s in got]


@pytest.mark.parametrize("route", ["/support/ticket-{n}-open", "/account/order-{n}", "/members/login.php/sso-{n}"])
def test_non_scene_hint_route_is_dropped_even_when_cards_differ_by_id(route):
    # The positive rule alone would admit these (they differ by an id); the product's _NON_SCENE_HINTS,
    # read per segment (script stems included), must drop them.
    anchors = [_a("https://members.dfxtra.com" + route.format(n=n), True, "x") for n in range(3)] + _nav()
    got = [s["url"] for s in _scene_cohort(anchors, LISTING)[0]]
    assert got == [], f"FX_CRAWLER_NON_SCENE_HINT_QUEUED: {got}"


# r3 must-KEEP: the lens's 21 title slugs (VERDICT-bd-review-correctness-D2-D.md, r2 REFUTE) -- scene titles that begin
# with a _NON_SCENE_HINTS word or one of r2's added words.
TITLE_SLUGS = [
    "Help-Me-Step-Bro", "About-Last-Night", "Legal-Teens-Party", "Support-Group", "Terms-Of-Service", "Contact-Sport",
    "Account-Manager", "Upgrade-Day", "Login-Loser", "Privacy-Please", "Feedback-Loop", "Livecam-Couple", "Signin-Sheet",
    "Logout-Now", "Membership-Drive", "Billing-Error", "Forgot-My-Keys", "Faq-Night", "Dmca-Test", "2257-Records",
    "Autologin-Test",
]


@pytest.mark.parametrize("slug", TITLE_SLUGS + ["Sunrise-Session"])
def test_title_slug_scene_cards_are_kept(slug):
    cards = [_a(f"https://members.dfxtra.com/en/video/blacksonblondes/{slug}-{n}/{500 + n}", True, slug) for n in range(4)]
    got = _scene_cohort(cards + _nav(), LISTING)[0]
    assert len(got) == 4, f"FX_CRAWLER_TITLE_SLUG_DROPPED: {slug} kept {len(got)}/4"


@pytest.mark.parametrize("slug", TITLE_SLUGS)
def test_one_title_slug_card_among_scenes_is_not_lost(slug):
    cards = _scene_cards()[:3] + [_a(f"https://members.dfxtra.com/en/video/blacksonblondes/{slug}-9/{599}", True, slug)]
    got = _scene_cohort(cards + _nav(), LISTING)[0]
    assert len(got) == 4, f"FX_CRAWLER_TITLE_SLUG_DROPPED: {slug} among scenes kept {len(got)}/4"


@pytest.mark.parametrize("slug", ["help-me-step-bro", "about-last-night", "livecam-couple", "login-loser", "support-group"])
def test_tube_style_slug_scenes_are_kept(slug):
    cards = [_a(f"https://tube.example/v/{slug}-{120 + n}", True, slug) for n in range(3)]
    got = _scene_cohort(cards, "https://tube.example/newest")[0]
    assert len(got) == 3, f"FX_CRAWLER_TUBE_SLUG_DROPPED: {slug} kept {len(got)}/3"


@pytest.mark.parametrize("url,expected", [
    ("https://x.example/video/livecam-couple-123", True), ("https://x.example/videos/livecams-best-of/9", True),
    ("https://x.example/scene/signin-sheet/4", True), ("https://x.example/video/logout-now/5", True),
    ("https://x.example/video/autologin-test/6", True), ("https://x.example/video/live-cam-girl/7", True),
])
def test_product_scene_rule_is_unchanged_for_title_words(url, expected):
    from bulk_downloader.playlist_extractor import _looks_like_scene_url
    assert _looks_like_scene_url(url) is expected, f"FX_CRAWLER_PRODUCT_RULE_REGRESSED: {url}"


# r4 (bd-worker-A12-A, PM ORDER-CUTE-A12): the lens's r2 open escapes and the order's script routes (logout.php, join.php)
# are route words the crawler reads as whole early segments; cards differing by an id or slug no longer rescue them.
ESCAPE_PAIRS = {
    "out": ["/out/brazzers-network", "/out/reality-kings"], "go": ["/go/partner1", "/go/partner2"],
    "top-rated": ["/en/top-rated", "/en/most-viewed"], "vod.html": ["/en/vod.html", "/en/vod.html?x=1"],
    "live-cams": ["/live-cams/model-1", "/live-cams/model-2"], "cams": ["/cams/model-1", "/cams/model-2"],
    "webcams": ["/webcams/anna-rose", "/webcams/bella-mae"], "store": ["/store/item-101", "/store/item-102"],
    "login.php": ["/members/login.php?id=1", "/members/login.php?id=2"],
    "logout.php": ["/members/logout.php?id=1", "/en/members/logout.php?id=2"],
    "join.php": ["/tour/join.php?id=1", "/tour/join.php?id=2"], "join": ["/join/offer-1", "/join/offer-2"],
    "cams-enter": ["/cams/enter?room=1", "/cams/enter?room=2"],
}


@pytest.mark.parametrize("name", sorted(ESCAPE_PAIRS))
def test_escape_route_cards_that_differ_by_id_are_not_scenes(name):
    anchors = [_a("https://members.dfxtra.com" + route, True, "x") for route in ESCAPE_PAIRS[name]] + _nav()
    got = [s["url"] for s in _scene_cohort(anchors, LISTING)[0]]
    assert got == [], f"FX_CRAWLER_ESCAPE_ROUTE_QUEUED: {name} -> {got}"


@pytest.mark.parametrize("sub", ["webcam", "chat"])
def test_webcam_and_chat_subdomains_are_off_site(sub):
    anchors = [_a(f"https://{sub}.dfxtra.com/room/{n}-model", True, "x") for n in range(3)] + _nav()
    got = [s["url"] for s in _scene_cohort(anchors, LISTING)[0]]
    assert got == [], f"FX_CRAWLER_PARTNER_HOST_QUEUED: {sub} -> {got}"


# Must-KEEP: titles that begin with one of the crawler's route words.
ROUTE_WORD_TITLES = ["Live-Cam-Girl", "Chat-Room-Fun", "Shop-Lifter", "Go-Big", "Out-Of-Town", "Store-Clerk",
                     "Join-The-Party", "Cams-Off", "Logout-Now", "Top-Rated-Babe"]


@pytest.mark.parametrize("slug", ROUTE_WORD_TITLES)
def test_route_word_titles_are_kept(slug):
    cards = [_a(f"https://members.dfxtra.com/en/video/blacksonblondes/{slug}-{n}/{700 + n}", True, slug) for n in range(4)]
    tube = [_a(f"https://tube.example/v/{slug.lower()}-{120 + n}", True, slug) for n in range(3)]
    assert len(_scene_cohort(cards + _nav(), LISTING)[0]) == 4, f"FX_CRAWLER_TITLE_SLUG_DROPPED: {slug}"
    assert len(_scene_cohort(tube, "https://tube.example/newest")[0]) == 3, f"FX_CRAWLER_TUBE_SLUG_DROPPED: {slug}"
