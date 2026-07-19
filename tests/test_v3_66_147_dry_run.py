"""v3.66.147 — dry-run diagnostics tests.

  #1 Candidate Inspector (`dry_run.inspect_candidates`) — every candidate is
     classified with a verdict + reason, the winner is an accepted (non-nav)
     download, and a nav-only page yields no winner. No download.
  #5 Static Template Test Runner (`dry_run.template_dry_run`) — reports template
     match, resolutions, redacted patterns, lint, hit-counts, and the candidate
     classification. No download.

Plus the two endpoints against the real app via the fresh_app client.
"""
from __future__ import annotations

import bulk_downloader.app as bd_app
from bulk_downloader import dry_run


REPTYLE = "https://app.reptyle.com/"
NOHOST = "https://no-such-host.example/"

# Page source: nav/account links (must be rejected) + real download links.
SAMPLE_HTML = """
<html><body>
  <nav class="navbar">
    <a href="/">Home</a>
    <a href="/movies">Movies</a>
    <a href="/settings">Settings</a>
  </nav>
  <a href="/logout">Log out</a>
  <div class="player" role="dialog">
    <a href="/movie/9/download-resolution/1080" class="dl">Download 1080p</a>
    <a href="/movie/9/download-resolution/2160" class="dl">Download 2160p</a>
  </div>
</body></html>
"""

NAV_ONLY_HTML = """
<html><body><nav class="navbar">
  <a href="/">Home</a><a href="/movies">Movies</a><a href="/deals">Deals</a>
  <a href="/account">Account</a>
</nav></body></html>
"""


def _seed(sid, **cfg):
    cfg.setdefault("name", sid)
    bd_app.s_cfg[sid] = cfg
    return cfg


def _row_for(rows, url):
    return next((r for r in rows if r["url"] == url), None)


# ── #1 inspect_candidates ────────────────────────────────────────────────

def test_inspector_winner_is_accepted_download():
    res = dry_run.inspect_candidates(SAMPLE_HTML, page_url="https://app.reptyle.com/movie/9")
    assert res["ok"] is True
    w = res["winner"]
    assert w is not None and w["accepted"] is True
    assert "download-resolution" in w["url"]
    assert res["safe_candidate_available"] is True


def test_inspector_flags_nav_and_homepage_with_reasons():
    res = dry_run.inspect_candidates(SAMPLE_HTML, page_url="https://app.reptyle.com/movie/9")
    rows = res["candidates"]
    home = _row_for(rows, "/")
    movies = _row_for(rows, "/movies")
    assert home and home["accepted"] is False and "homepage link" in home["reason"]
    assert movies and movies["accepted"] is False and "navigation URL" in movies["reason"]
    # every candidate carries a reason/verdict
    assert all("accepted" in r and "reason" in r for r in rows)


def test_inspector_nav_only_page_has_no_winner():
    res = dry_run.inspect_candidates(NAV_ONLY_HTML, page_url="https://app.reptyle.com/")
    assert res["ok"] is True
    assert res["winner"] is None
    assert res["safe_candidate_available"] is False
    assert res["n_accepted"] == 0


def test_inspector_empty_html():
    res = dry_run.inspect_candidates("", page_url=REPTYLE)
    assert res["ok"] is False and res["winner"] is None


# ── #5 template_dry_run ──────────────────────────────────────────────────

def test_dry_run_matches_reptyle_template():
    res = dry_run.template_dry_run(REPTYLE)
    assert res["ok"] is True
    assert res["template_matched"] is True
    assert res["template"]["enabled"] is True
    assert res["template"]["host"] == "app.reptyle.com"
    assert 2160 in res["template"]["resolutions"]
    assert res["network_patterns"]            # non-empty, redacted
    assert res["has_blocking_lint"] is False  # reviewed template is clean


def test_dry_run_no_template_for_unknown_host():
    res = dry_run.template_dry_run(NOHOST)
    assert res["ok"] is True
    assert res["template_matched"] is False
    assert res["template"]["enabled"] is False


def test_dry_run_with_html_classifies_and_counts():
    res = dry_run.template_dry_run(REPTYLE, html=SAMPLE_HTML)
    assert res["candidate_classification"] is not None
    assert res["candidate_classification"]["winner"] is not None
    assert res["safe_candidate_selected"] is True
    assert isinstance(res["selector_hit_counts"], dict)
    # hit-counts present for the reviewed template's groups
    assert any(k.startswith("download.") for k in res["selector_hit_counts"])


def test_dry_run_redacts_token_like_patterns():
    fake = {"host": "x.test", "status": "enabled",
            "network_patterns": ["media/clip?token=SECRET123", "api/video"]}
    # patched template with a token-ish pattern → redacted query stripped
    pats = dry_run._redact_patterns(fake)
    assert all("SECRET123" not in p and "token" not in p for p in pats)


# ── endpoints ────────────────────────────────────────────────────────────

def test_inspect_endpoint(fresh_app):
    _seed("d1", login_url=REPTYLE)
    r = fresh_app.post("/api/sites/d1/candidates/inspect", json={"html": SAMPLE_HTML})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["winner"] is not None
    assert "download-resolution" in body["winner"]["url"]


def test_dry_run_endpoint(fresh_app):
    _seed("d2", login_url=REPTYLE)
    r = fresh_app.post("/api/sites/d2/template/dry_run", json={"html": SAMPLE_HTML})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["template_matched"] is True
    assert body["safe_candidate_selected"] is True


def test_inspect_endpoint_unknown_site_404(fresh_app):
    r = fresh_app.post("/api/sites/ghost/candidates/inspect", json={"html": "<a>x</a>"})
    assert r.status_code == 404
    assert r.get_json()["ok"] is False
