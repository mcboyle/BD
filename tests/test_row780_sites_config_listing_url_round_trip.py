"""Row 780 -- SITES-LOADER-DROPS-LISTING_URL-WHILE-TWO-READERS-USE-IT.

`_site_primary_url` (bulk_downloader/app.py) is consulted by two callers as
a fallback content-URL source when a site has no ``crawler_listing_url``:
the template capture builder (app_sites_teach.py) and the id-core
rename/probe path (app_sites_id_core.py). Before this cut, plain
``listing_url`` was not in CFG_FIELDS, so POST /api/config/import's
per-site normalization (``cfg = {k: raw.get(k, "") for k in CFG_FIELDS}``,
bulk_downloader/app_config.py) silently dropped it on every save/reload
round-trip, while ``crawler_listing_url`` (which IS in CFG_FIELDS)
survived. A site imported with only a bare ``listing_url`` lost its
content URL, and both readers fell back to the login URL instead.
"""
from __future__ import annotations

BD_GATE_SCOPE = "repo-wide"

LOGIN = "https://auth.example.invalid/login"
LISTING = "https://members.example.invalid/scenes"
CRAWLER = "https://members.example.invalid/library"


def _import_one(fresh_app, app_module, **content):
    before = set(app_module.s_cfg)
    r = fresh_app.post(
        "/api/config/import",
        json={
            "mode": "merge",
            "sites": [{"name": f"row780-{len(before)}", **content}],
        },
    )
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    minted = [sid for sid in app_module.s_cfg if sid not in before]
    assert len(minted) == 1, (
        "precondition: the import created exactly one new site, got %r"
        % (minted,))
    return app_module.s_cfg[minted[0]]


def test_listing_url_round_trips_through_config_import(fresh_app):
    """RED (before this cut's fix): a bare listing_url, with no
    crawler_listing_url, was dropped by the /api/config/import loader --
    the persisted site config came back with listing_url == "" even
    though it was submitted, and _site_primary_url fell back to the
    login URL instead of the listing URL that was actually configured."""
    from bulk_downloader import app as app_module

    cfg = _import_one(fresh_app, app_module, login_url=LOGIN,
                       listing_url=LISTING)

    assert cfg.get("listing_url") == LISTING, (
        "the site's own listing_url must survive the import round-trip, "
        "got %r" % (cfg.get("listing_url"),))
    assert app_module._site_primary_url(cfg) == LISTING, (
        "with no crawler_listing_url, _site_primary_url must resolve the "
        "site's own listing_url instead of falling back to the login URL; "
        "got %r" % (app_module._site_primary_url(cfg),))


def test_crawler_listing_url_still_wins_over_listing_url(fresh_app):
    """Negative control: when BOTH keys are present, crawler_listing_url
    (the canonical, always-persisted field) must still take priority over
    the plain listing_url fallback this cut also makes durable -- the fix
    must not flip that precedence. Exact-value assertion; the two URLs are
    distinct (nonzero fixture) so a precedence bug is distinguishable from
    an accidental pass."""
    from bulk_downloader import app as app_module

    assert LISTING != CRAWLER, "precondition: the fixture URLs must differ"
    cfg = _import_one(fresh_app, app_module, login_url=LOGIN,
                       crawler_listing_url=CRAWLER, listing_url=LISTING)

    assert cfg.get("crawler_listing_url") == CRAWLER
    assert cfg.get("listing_url") == LISTING
    assert app_module._site_primary_url(cfg) == CRAWLER, (
        "crawler_listing_url must win over listing_url when both are set; "
        "got %r" % (app_module._site_primary_url(cfg),))
