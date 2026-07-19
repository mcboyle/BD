"""v3.66.245 — REGRESSION: /api/template/test_extract enqueue must call the real
SiteRunner.load_urls(), not the nonexistent .add_url().

Repro of the live bug captured 2026-06-14: the B2 live-extract surface returned
``{"error":"enqueue failed: 'SiteRunner' object has no attribute 'add_url'"}``.
SiteRunner has no add_url/add_urls; the real enqueue method is load_urls([...])
(the same fix the v3.66.8 audit applied at the crash-recovery resume path). This
recreated that bug in a new spot (app.py test_extract enqueue, and a silent twin
in /api/discovery/run).

RED on pristine 244: the POST returns 500 'enqueue failed' (AttributeError on the
real runner, which has no add_url). GREEN after the swap to load_urls([url]).

App-booting test: re-reads BD_HOME at import under the wipe marker (conftest).
No pytest fixtures; stages a temp sites_config and stubs the live scheduler.
"""
import json
import os

import pytest

pytestmark = pytest.mark.bd_module_wipe


def _boot_with_site():
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader import app as a
    from bulk_downloader import db
    db.db_init()
    a.SITES_FILE.write_text(json.dumps({"wow": {
        "name": "WowGirls", "hostname": "auth.wowgirls.com",
        "max_concurrent": 2, "wait": 5, "password": "PW"}}), encoding="utf-8")
    a._load_sites_config()
    # The live scheduler/start path is orthogonal to the enqueue under test and
    # spins a background thread; stub it. Record load_urls to prove the real
    # enqueue method is the one invoked.
    r = a.runners["wow"]
    calls = {"load_urls": [], "start": 0}
    r.update_config = lambda *_a, **_k: None
    r.start = lambda *_a, **_k: calls.__setitem__("start", calls["start"] + 1)
    r.load_urls = lambda urls, **_k: (calls["load_urls"].extend(list(urls)),
                                      (len(list(urls)), 0, 0))[1]
    return a, a.app.test_client(), calls


_TPL = {"host": "auth.wowgirls.com",
        "selectors": {"download": {"trigger": "a[download]"}},
        "resolutions": [1080, 720]}
_URL = "https://venus.wowgirls.com/film/z6663c31/the-steamy-girl"


def test_test_extract_enqueues_via_load_urls():
    """The override-extract POST with a url must enqueue (not 500) and route
    through SiteRunner.load_urls([url])."""
    a, c, calls = _boot_with_site()
    r = c.post("/api/template/test_extract",
               json={"site_id": "wow", "template": _TPL, "url": _URL,
                     "persist": False})
    assert r.status_code == 200, (r.status_code, r.get_json())
    body = r.get_json()
    assert body.get("ok") is True, body
    assert body.get("enqueued") is True, body
    assert body.get("started") is True, body
    # the real enqueue method received exactly the requested URL
    assert calls["load_urls"] == [_URL], calls


def test_test_extract_no_url_sets_override_without_enqueue():
    """No url => override is set + runner started, but nothing enqueued (the
    run picks up the site's existing queue). Guards the enqueue branch is gated
    on a url being present."""
    a, c, calls = _boot_with_site()
    r = c.post("/api/template/test_extract",
               json={"site_id": "wow", "template": _TPL, "persist": False})
    assert r.status_code == 200, (r.status_code, r.get_json())
    body = r.get_json()
    assert body.get("ok") is True, body
    assert body.get("enqueued") is False, body
    assert calls["load_urls"] == [], calls
