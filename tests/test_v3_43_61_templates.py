"""v3.43.61 — templates from the serpapps repo survey (38 zips, 33 unique sites).

User uploaded 38 zips across 3 batches covering 33 unique sites:

  - 3 with full technical research docs (erome, alpha_porno, chaturbate)
  - 16 free-tube READMEs with architecture hints
  - 4 live-streaming (chaturbate, stripchat, bongacams, fansly_live)
  - 3 paywalled creator (manyvids, justforfans, fansly_live)
  - 3 DRM-protected legit (netflix, hulu, tubi)
  - 1 piracy aggregator (123movies)
  - 4 mainstream/social with better external tools (youtube, vk, reddit, deviantart)
  - 2 product waitlist pages (ai-downloader, open-video-downloader) -- NOT
    sites so no template entries.

29 new template ids cover all 33 sites (some templates group multiple
operator-related domains: aylo_free_tubes covers 6, wgcz_tubes covers 2,
txxx_network covers 7 sibling tubes).
"""
from __future__ import annotations

TEMPLATE_URL_FIXTURES = [
    # VERIFIED research-backed
    ("erome", "https://www.erome.com/a/AbCd1234"),
    ("alpha_porno", "https://www.alphaporno.com/videos/sample-title-12345678/"),
    ("chaturbate", "https://chaturbate.com/somemodel/"),
    # Speculative tube sites
    ("spankbang", "https://spankbang.com/12345/video/some-title"),
    ("aylo_free_tubes", "https://www.pornhub.com/view_video.php?viewkey=ph123"),
    ("aylo_free_tubes", "https://www.redtube.com/12345"),
    ("aylo_free_tubes", "https://www.youporn.com/watch/12345/"),
    ("aylo_free_tubes", "https://www.tube8.com/foo/bar/"),
    ("wgcz_tubes", "https://www.xvideos.com/video12345/some-title"),
    ("wgcz_tubes", "https://www.xnxx.com/video-abc/some-title"),
    ("beeg", "https://www.beeg.com/12345678"),
    ("motherless", "https://motherless.com/ABCDEF1"),
    ("eporner", "https://www.eporner.com/video-abc/title/"),
    ("xhamster", "https://xhamster.com/videos/title-12345"),
    ("txxx_network", "https://www.txxx.com/videos/12345/title/"),
    ("txxx_network", "https://www.upornia.com/videos/123/"),
    ("txxx_network", "https://hclips.com/videos/12345/"),
    ("thisvid", "https://thisvid.com/videos/12345/"),
    ("porntrex", "https://www.porntrex.com/video/12345/title"),
    ("yespornplease", "https://www.yespornplease.com/video/12345"),
    ("youjizz", "https://www.youjizz.com/videos/title-12345.html"),
    # Specialized
    ("redgifs", "https://www.redgifs.com/watch/some-slug-id"),
    # Live-cam limitation stubs
    ("stripchat_live", "https://stripchat.com/somemodel"),
    ("bongacams_live", "https://bongacams.com/somemodel"),
    ("fansly_live", "https://fansly.com/somecreator"),
    # Paywalled creator
    ("manyvids", "https://www.manyvids.com/Video/12345/title/"),
    ("justforfans", "https://justfor.fans/somecreator"),
    # DRM-protected legit
    ("netflix", "https://www.netflix.com/title/12345"),
    ("hulu", "https://www.hulu.com/watch/abc-def"),
    ("tubi", "https://tubitv.com/movies/12345/title"),
    # Piracy aggregator family
    ("movies123", "https://123movies.online/movie/12345"),
    ("movies123", "https://gomovies.something/foo"),
    ("movies123", "https://fmovies.io/movie/12345"),
    # Mainstream / social — recommends external tool
    ("youtube", "https://www.youtube.com/watch?v=abcDEF12345"),
    ("youtube", "https://youtu.be/abcDEF12345"),
    ("vk_video", "https://vk.com/video12345_67890"),
    ("reddit_media", "https://www.reddit.com/r/videos/comments/abc/title/"),
    ("reddit_media", "https://v.redd.it/abcdef"),
    ("deviantart", "https://www.deviantart.com/someartist/art/title-12345"),
]

# v3.43.62: 4 live-cam templates flipped to functional (live_recorder
# runtime ships). They lose the _limitation marker -- the v3.43.62
# test suite separately verifies their `use_live_recorder` flag.
LIMITATION_TEMPLATES = [
    "manyvids", "justforfans",
    "netflix", "hulu", "tubi",
    "movies123",
    "youtube", "vk_video", "reddit_media", "deviantart",
]

SPECULATIVE_TEMPLATES = [
    "spankbang", "aylo_free_tubes", "wgcz_tubes", "beeg", "motherless",
    "eporner", "xhamster", "txxx_network", "thisvid", "porntrex",
    "yespornplease", "youjizz", "redgifs",
]


def test_total_template_count():
    """v3.43.61 added 29 new templates; total should be at least 90."""
    from bulk_downloader import templates
    assert len(templates.TEMPLATES) >= 90, (
        f"expected >=90 templates after v3.43.61, got {len(templates.TEMPLATES)}"
    )


def test_no_duplicate_ids():
    """No template id should appear more than once -- a duplicate would
    mean the v3.43.61 insert collided with an existing built-in."""
    from bulk_downloader import templates
    ids = [t["id"] for t in templates.TEMPLATES]
    seen = {}
    dupes = []
    for i in ids:
        if i in seen:
            dupes.append(i)
        seen[i] = True
    assert not dupes, f"duplicate template ids: {dupes}"


def test_all_new_template_ids_registered():
    from bulk_downloader import templates
    seen = {t["id"] for t in templates.TEMPLATES}
    missing = []
    for template_id, _url in TEMPLATE_URL_FIXTURES:
        if template_id not in seen:
            missing.append(template_id)
    assert not missing, f"templates not registered: {sorted(set(missing))}"


def test_all_new_template_urls_route():
    from bulk_downloader import templates
    failures = []
    for template_id, url in TEMPLATE_URL_FIXTURES:
        suggested = templates.suggest_for_url(url)
        if template_id not in suggested:
            failures.append((url, template_id, suggested))
    assert not failures, (
        "url-routing failures:\n  " + "\n  ".join(
            f"{u} -> {s} (expected {tid})"
            for u, tid, s in failures
        )
    )


def test_limitation_stubs_carry_marker():
    """Sites where downloads can't or won't reliably work get a
    `_limitation` key in config_defaults so the wizard surfaces the
    situation to the user instead of silently failing later."""
    from bulk_downloader import templates
    missing_marker = []
    for tid in LIMITATION_TEMPLATES:
        t = templates.get(tid)
        if t is None:
            missing_marker.append(f"{tid}=NOT_REGISTERED")
            continue
        cfg = t.get("config_defaults") or {}
        if "_limitation" not in cfg:
            missing_marker.append(f"{tid}=NO_MARKER")
    assert not missing_marker, f"limitation templates without marker: {missing_marker}"


def test_aylo_covers_all_brands():
    from bulk_downloader.templates import suggest_for_url
    for url in [
        "https://www.redtube.com/x",
        "https://www.pornhub.com/x",
        "https://www.pornhubpremium.com/x",
        "https://www.youporn.com/x",
        "https://www.tube8.com/x",
    ]:
        assert "aylo_free_tubes" in suggest_for_url(url), \
            f"aylo_free_tubes should match {url}"


def test_wgcz_covers_xvideos_and_xnxx():
    from bulk_downloader.templates import suggest_for_url
    assert "wgcz_tubes" in suggest_for_url("https://www.xvideos.com/foo")
    assert "wgcz_tubes" in suggest_for_url("https://www.xnxx.com/foo")


def test_txxx_covers_sibling_tubes():
    from bulk_downloader.templates import suggest_for_url
    hosts = ("txxx.com", "hclips.com", "hqporner.com", "hdzog.com",
             "upornia.com", "voyeurhit.com")
    failures = []
    for host in hosts:
        if "txxx_network" not in suggest_for_url(f"https://www.{host}/x"):
            failures.append(host)
    assert not failures, f"txxx_network should match: {failures}"


def test_verified_templates_say_so():
    """The 3 templates built from real research docs label themselves
    VERIFIED in their description."""
    from bulk_downloader import templates
    failures = []
    for tid in ("erome", "alpha_porno", "chaturbate"):
        t = templates.get(tid)
        if t is None:
            failures.append(f"{tid}=NOT_REGISTERED")
            continue
        desc = t.get("description", "").upper()
        if "VERIFIED" not in desc:
            failures.append(f"{tid}=NOT_LABELED_VERIFIED")
    assert not failures, f"verified-label issues: {failures}"


def test_speculative_templates_disclose_status():
    from bulk_downloader import templates
    failures = []
    for tid in SPECULATIVE_TEMPLATES:
        t = templates.get(tid)
        if t is None:
            failures.append(f"{tid}=NOT_REGISTERED")
            continue
        desc = t.get("description", "").lower()
        ok = "speculative" in desc or "teach pass" in desc or "best-guess" in desc
        if not ok:
            failures.append(f"{tid}=NO_DISCLOSURE")
    assert not failures, f"speculative-disclosure issues: {failures}"


def test_research_backed_templates_have_real_selectors():
    """erome / alpha_porno have non-trivial selectors (>=3) from their
    research docs. chaturbate is verified-research but live-stream so
    selectors are empty by design -- excluded here."""
    from bulk_downloader import templates
    failures = []
    for tid in ("erome", "alpha_porno"):
        t = templates.get(tid)
        assert t is not None
        rs = t.get("learned", {}).get("download", {}).get("row_selectors", [])
        if len(rs) < 3:
            failures.append(f"{tid}={len(rs)}_selectors_too_few")
    assert not failures, f"verified templates with thin selectors: {failures}"


def test_vaporware_repos_not_templated():
    """The 2 product-waitlist repos (ai-downloader, open-video-downloader)
    are products not sites; no template named after them."""
    from bulk_downloader import templates
    for fake_tid in ("ai_downloader", "open_video_downloader"):
        assert templates.get(fake_tid) is None, \
            f"vaporware {fake_tid!r} should NOT be templated"
