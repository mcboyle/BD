"""v3.66.785 -- AF1 confidence tier: a mechanical triage score/tier layered over
``candidate_filter.classify`` for A-DISCO (level-4 enumeration).

Three tiers:
  * ``high``   -- auto-queue-safe (a strong-signal download URL, no rejections);
  * ``review`` -- uncertain: route to the operator, never auto-queue;
  * ``reject`` -- clearly not the target, drop.

THE INVARIANT THIS FILE EXISTS TO PIN (the dominant failure shape this program
hunts): the tier is derived over EVERY classified candidate, and anything that is
not an unambiguous strong-signal download lands in ``review`` -- it FAILS TO
REVIEW, never silently to ``high``. A resolution-label-only download (which the
binary filter accepts, because a stray "1080p" is a positive signal) must NOT be
auto-queued; a trigger (a menu opener with no URL) must NOT be auto-queued.
"""
from bulk_downloader import candidate_filter as cf


def _tier(**kw):
    return cf.confidence_tier(cf.classify(**kw))


def _score(**kw):
    return cf.confidence_score(cf.classify(**kw))


# -- strong-signal downloads are auto-queue-safe (high) ----------------------

def test_media_extension_is_high():
    assert _tier(url="/get/123/8k.mp4", text="Download",
                 selector="a.dl[href$='.mp4']") == "high"


def test_manifest_url_is_high():
    assert _tier(url="https://cdn.site.com/hls/master.m3u8") == "high"
    assert _tier(url="https://cdn.site.com/dash/manifest.mpd") == "high"


def test_download_path_is_high():
    assert _tier(url="https://site.com/download/9981", text="Get file") == "high"


def test_api_pattern_is_high():
    assert _tier(url="https://site.com/api/v2/media/source?id=9") == "high"


# -- uncertain candidates route to review (never auto-queued) -----------------

def test_resolution_only_download_is_review():
    # The binary filter ACCEPTS this as a download (resolution_label is a positive
    # signal), but a stray "1080p" is not a media/download URL. It must land in
    # review, NOT high -- otherwise auto-queue grabs it. This is the whole point.
    v = cf.classify(url="https://site.com/v/9?q=1080p", text="1080p")
    assert v.accepted and v.kind == "download"           # binary filter said yes
    assert "resolution_label" in v.positive_signals
    assert not (set(v.positive_signals) & {
        "media_extension", "manifest_url", "download_path", "api_pattern"})
    assert cf.confidence_tier(v) == "review"             # ...but NOT auto-queue


def test_trigger_is_review():
    # A quality/download menu opener with no URL: reveals controls, is not itself a
    # download. Useful to a human, never auto-queued.
    v = cf.classify(text="Choose quality", classes="btn download-menu")
    assert v.accepted and v.kind == "trigger"
    assert cf.confidence_tier(v) == "review"


# -- clear non-targets are dropped (reject) ----------------------------------

def test_generic_selector_is_reject():
    assert _tier(url="https://site.com/somepage", selector="a[href]") == "reject"


def test_nav_url_is_reject():
    assert _tier(url="https://site.com/categories/all", text="Categories") == "reject"


def test_no_signal_is_reject():
    assert _tier(url="https://site.com/about", text="About us") == "reject"


# -- fail-to-review invariant: nothing accepted-but-weak is ever 'high' -------

def test_fail_to_review_invariant():
    # Every accepted candidate that is NOT a strong-signal download must be
    # 'review'. Sweeping the weak-but-accepted shapes: none may be auto-queued.
    weak_but_accepted = [
        cf.classify(url="https://site.com/v/42?q=720p", text="720p"),   # res-only
        cf.classify(text="Download HD", classes="quality-btn"),          # trigger
        cf.classify(text="Select format", classes="format-menu"),        # trigger
    ]
    for v in weak_but_accepted:
        assert v.accepted, v.reason
        assert cf.confidence_tier(v) == "review", (v.kind, v.positive_signals)


def test_reject_verdict_never_high_or_review():
    # A rejected verdict is a drop, never accidentally promoted.
    for v in (cf.classify(url="https://site.com/login", text="Sign in"),
              cf.classify(url="https://site.com/", text="Home"),
              cf.classify(selector="*", url="https://x.com/y")):
        assert not v.accepted
        assert cf.confidence_tier(v) == "reject"


# -- score is a well-formed, ordered signal ----------------------------------

def test_score_range_is_unit_interval():
    for v in (cf.classify(url="/a/b.mp4"),
              cf.classify(url="https://s.com/v/1?q=1080p", text="1080p"),
              cf.classify(text="quality", classes="menu"),
              cf.classify(url="https://s.com/login")):
        s = cf.confidence_score(v)
        assert 0.0 <= s <= 1.0, s


def test_score_ordering_strong_gt_weak_gt_trigger_gt_reject():
    strong = _score(url="https://cdn.com/hls/master.m3u8")
    weak = _score(url="https://s.com/v/1?q=1080p", text="1080p")
    trig = _score(text="choose quality", classes="download-menu")
    rej = _score(url="https://s.com/login", text="Sign in")
    assert strong > weak > trig > rej
    assert rej == 0.0


def test_tier_is_monotonic_in_score():
    # tier and score agree: a higher score never yields a lower tier.
    rank = {"reject": 0, "review": 1, "high": 2}
    verdicts = [
        cf.classify(url="https://cdn.com/hls/master.m3u8"),
        cf.classify(url="https://s.com/download/9"),
        cf.classify(url="https://s.com/v/1?q=1080p", text="1080p"),
        cf.classify(text="choose quality", classes="download-menu"),
        cf.classify(url="https://s.com/login", text="Sign in"),
    ]
    scored = sorted(verdicts, key=cf.confidence_score)
    tiers = [rank[cf.confidence_tier(v)] for v in scored]
    assert tiers == sorted(tiers), tiers


def test_tier_constants_exist():
    assert cf.TIER_HIGH == "high"
    assert cf.TIER_REVIEW == "review"
    assert cf.TIER_REJECT == "reject"


def test_every_verdict_maps_to_exactly_one_tier():
    valid = {cf.TIER_HIGH, cf.TIER_REVIEW, cf.TIER_REJECT}
    for v in (cf.classify(url="/a.mp4"),
              cf.classify(url="https://s.com/v/1?q=1080p", text="1080p"),
              cf.classify(text="quality", classes="menu"),
              cf.classify(url="https://s.com/login"),
              cf.classify(url="https://s.com/nothing", text="x")):
        assert cf.confidence_tier(v) in valid
