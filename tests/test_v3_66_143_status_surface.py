"""v3.66.143 — Settings/cockpit "what is active" status surface + supporting
coverage: consolidated /cockpit/api/status, the System Status GUI page,
profile_sync.handoff_status, offline-capture readiness, and end-to-end bad
selector / bad URL rejection through the extractor.
"""
import os
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")


# ── /cockpit/api/status endpoint ──────────────────────────────────────────

def _client():
    from bulk_downloader.app import app
    return app.test_client()


def test_status_endpoint_shape():
    r = _client().get("/cockpit/api/status")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    for k in ("browser_backend", "capture_assets", "manual_login_handoff",
              "keepalive", "corpus"):
        assert k in d, f"missing status block: {k}"
    # backend block reports a selected backend + cloakbrowser availability
    bb = d["browser_backend"]
    assert bb.get("selected") in ("cloakbrowser", "playwright")
    assert isinstance(bb["cloakbrowser"]["available"], bool)
    # capture-asset block reports locality of rrweb/snapdom
    assert isinstance(d["capture_assets"]["local"], bool)
    assert "rrweb_present" in d["capture_assets"]
    # keepalive reports the default backend + a keeper list
    assert d["keepalive"]["default_backend"] in ("cloakbrowser", "playwright")
    assert isinstance(d["keepalive"]["keepers"], list)
    # corpus block is present (capture corpus may be absent in this env)
    assert "present" in d["corpus"]


def test_status_blocks_are_individually_guarded(monkeypatch):
    # a broken subsystem must not blank the whole panel — its block carries an
    # error and the rest still resolve.
    import bulk_downloader.profile_sync as ps
    monkeypatch.setattr(ps, "handoff_status",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    d = _client().get("/cockpit/api/status").get_json()
    assert d["ok"] is True
    assert "error" in d["manual_login_handoff"]
    assert d["browser_backend"].get("selected") in ("cloakbrowser", "playwright")
    assert "rrweb_present" in d["capture_assets"]


# ── System Status GUI page is wired ───────────────────────────────────────

def test_systemstatus_page_and_nav_wired():
    src = _client().get("/cockpit/").get_data(as_text=True)
    assert "PAGES.systemstatus" in src                 # render handler present
    assert ('data-p="systemstatus"' in src or "systemstatus:[" in src)  # nav entry present (now a Health container tab via redirect)
    assert "/api/status" in src                        # page fetches the endpoint


# ── profile_sync.handoff_status (read-only) ───────────────────────────────

def _w(p, data="x"):
    from pathlib import Path
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(data)


def test_handoff_status_empty_safe(tmp_path):
    from bulk_downloader import profile_sync as ps
    st = ps.handoff_status(profiles_root=str(tmp_path / "nope"))
    assert st["present"] is False and st["sites"] == []


def test_handoff_status_reports_manual_and_session(tmp_path):
    from bulk_downloader import profile_sync as ps
    root = tmp_path / "profiles"
    # site A: manual + a main that received a session (has Cookies)
    _w(root / "siteA" / "manual" / "Default" / "Cookies", "C")
    _w(root / "siteA" / "main" / "Default" / "Cookies", "C")
    # site B: manual exists but no runtime session yet
    _w(root / "siteB" / "manual" / "Default" / "Cookies", "C")
    (root / "siteB" / "keepalive_0" / "Default").mkdir(parents=True)
    st = ps.handoff_status(profiles_root=str(root))
    assert st["present"] is True
    sites = {s["site"]: s for s in st["sites"]}
    assert sites["siteA"]["manual_present"] and sites["siteA"]["handed_off"] is True
    assert sites["siteB"]["manual_present"] and sites["siteB"]["handed_off"] is False


# ── offline capture / backend readiness ───────────────────────────────────

def test_offline_assets_local_and_no_cdn():
    from bulk_downloader import dom_recorder as dr
    assert dr.using_local_assets() is True
    blob = (dr.recorder_script() + dr.rrweb_js()[:4000] + dr.snapdom_js()[:4000]).lower()
    for needle in ("cdn.jsdelivr", "unpkg.com", "cdnjs.cloudflare", "esm.sh"):
        assert needle not in blob


def test_backend_downgrades_to_playwright_when_cloak_unavailable(monkeypatch):
    from bulk_downloader import cloak
    monkeypatch.setattr(cloak, "is_available", lambda: False)
    # an explicit request for cloakbrowser downgrades; default lands on playwright
    assert cloak.resolve_backend({"browser_backend": "cloakbrowser"}) == "playwright"
    assert cloak.resolve_backend({}) == "playwright"


# ── end-to-end: bad selector / bad URL rejection through the extractor ─────

def test_bad_links_only_yields_review_required_and_no_download_row():
    from bulk_downloader.template_extractor import extract_from_html
    html = """
    <header class="site-header"><a href="/">Home</a>
      <a href="/login">Log in</a><a href="/search?q=x">Search</a></header>
    <div class="related"><a href="/v/other-1">Related video</a>
      <a href="/share" class="share">Share</a></div>
    """
    r = extract_from_html(html, page_url="https://example.com/v/9")
    assert r["ok"] is True
    # none of these are real download controls: no row selector is trusted, and
    # the draft is flagged for review rather than silently picking a bad link.
    assert r["template"]["row_selectors"] == []
    assert r["template"].get("review_required") is True
    assert "n_rejected" in r["stats"]   # rejected-candidate surfacing is wired


def test_bad_url_with_score_is_filtered_from_rows():
    # a link that DOES score (download-y text) but resolves to a generic page
    # with no media signal must not become a row; the safeguard flags review.
    from bulk_downloader.template_extractor import extract_from_html
    html = """
    <div class="downloads">
      <a class="dl" href="/account/settings">Download settings</a>
      <a class="dl" href="/">Download home</a>
    </div>"""
    r = extract_from_html(html, page_url="https://example.com/v/9")
    assert r["ok"] is True
    # no media/download URL signal on either -> no trusted row, review required
    assert r["template"]["row_selectors"] == [] or r["template"].get("review_required") is True
