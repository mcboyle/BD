#!/usr/bin/env python3
"""fixture_site2.py - a second self-hosted mock site for testing
BulkDownloader against site shapes the first fixture doesn't cover.

WHAT THIS IS
------------
A standalone Flask app (no dependency on the bulk_downloader package)
serving SYNTHETIC pages. Where tools/fixture_site.py models the common
shapes - login forms, direct downloads, player markup - this second
fixture models the AWKWARD shapes that break naive adapters:

  /infinite/...   infinite-scroll catalog (no page numbers; an API
                  endpoint returns the next batch, "load more" style)
  /api/...        a JSON / GraphQL-style API surface - the catalog is
                  fetched as JSON, not scraped from HTML
  /challenge/...  a Cloudflare-style interstitial: the first request
                  to a protected page returns a challenge page; only
                  after the challenge is "solved" (a cookie is set)
                  does the real content load
  /paywall/...    content visible only to a subscribed session; an
                  unsubscribed session sees a paywall stub
  /spa/...        a single-page-app shell - the HTML is nearly empty
                  and all content arrives via a later API call

SAFETY
------
Same profile as fixture_site.py: it serves only synthetic placeholder
content, executes nothing, spawns no processes, touches no real site
or real code. The worst it can do is serve a wrong page. There is no
fault injector and no management UI here - this fixture is purely
about site STRUCTURE, so it has no command surface at all.

RUN
---
  python tools/fixture_site2.py
  python tools/fixture_site2.py --port 8898 --host 0.0.0.0

CREDENTIALS
-----------
  username: tester    password: fixturepass    (paywall login)
  challenge solve token (cookie value): solved-ok
"""
from __future__ import annotations

import argparse
import struct

from flask import Flask, Response, jsonify, make_response, redirect, request

USERNAME = "tester"
PASSWORD = "fixturepass"

_TOTAL = 84  # bigger catalog than fixture_site.py, for scroll testing
_BATCH = 10


def _item(n: int) -> dict:
    return {
        "id": n,
        "title": f"Mock Item {n:03d}",
        "tag": ["alpha", "beta", "gamma"][n % 3],
        "media_url": f"/media2/{n}.mp4",
    }


def _make_mp4(size_bytes: int = 6144) -> bytes:
    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload) + 8) + kind + payload
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 0x200) + b"isomiso2mp41")
    pad = max(0, size_bytes - len(ftyp) - 8)
    return ftyp + box(b"free", b"\x00" * pad)


# ── Presentation ────────────────────────────────────────────────────
# Styling only. The HTML structure the adapters parse - classes, ids,
# data-attrs, hrefs - is untouched; this adds a stylesheet plus a
# nav/footer wrapper so the fixture reads like a real tube site.
_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0f0f10;color:#e8e8ea;
  font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
a{color:inherit;text-decoration:none}
.fx-nav{position:sticky;top:0;z-index:30;display:flex;align-items:center;
  gap:18px;padding:11px 22px;background:#161618;
  border-bottom:1px solid #303038}
.fx-brand{font-weight:800;font-size:20px;letter-spacing:-.5px;white-space:nowrap}
.fx-brand b{color:#ff7a18}
.fx-search{flex:1;max-width:420px;display:flex}
.fx-search input{flex:1;min-width:0;padding:8px 12px;border:1px solid #303038;
  border-right:0;border-radius:6px 0 0 6px;background:#0f0f10;color:#e8e8ea;
  font-size:14px}
.fx-search span{padding:8px 15px;border-radius:0 6px 6px 0;background:#ff7a18;
  color:#1a1209;font-weight:700;font-size:14px;cursor:pointer}
.fx-links{display:flex;gap:15px;font-size:14px;color:#9a9aa3;flex-wrap:wrap}
.fx-links a:hover{color:#ff9e4d}
.fx-badge{display:inline-block;background:#241a0d;color:#ff9e4d;
  border:1px solid #4a3418;font-size:10px;font-weight:800;padding:3px 8px;
  border-radius:5px;letter-spacing:.6px;vertical-align:middle}
.fx-wrap{max-width:1180px;margin:0 auto;padding:24px 22px 56px}
h1{font-size:22px;margin-bottom:16px;font-weight:700}
h2{font-size:12px;margin:24px 0 11px;color:#9a9aa3;text-transform:uppercase;
  letter-spacing:.7px}
p{color:#cfcfd4}
.hero{background:linear-gradient(135deg,#1f1410,#1b1b1f);
  border:1px solid #303038;border-radius:14px;padding:30px 28px;
  margin-bottom:26px}
.hero h1{margin-bottom:10px}
.hero p{color:#9a9aa3;max-width:680px}
.shapes{display:grid;gap:13px;margin-top:6px;
  grid-template-columns:repeat(auto-fill,minmax(220px,1fr))}
.shape-card{display:block;background:#1b1b1f;border:1px solid #26262c;
  border-radius:11px;padding:16px 17px;border-left:3px solid #ff7a18}
.shape-card:hover{border-color:#ff7a18}
.shape-card b{font-size:14px}
.shape-card span{display:block;margin-top:5px;font-size:12px;color:#8e8e98;
  line-height:1.45}
/* item / catalog grid */
.item-list{display:grid;gap:18px;
  grid-template-columns:repeat(auto-fill,minmax(212px,1fr))}
.item{background:#1b1b1f;border:1px solid #26262c;border-radius:11px;
  overflow:hidden;transition:transform .12s ease,border-color .12s ease}
.item:hover{transform:translateY(-3px);border-color:#ff7a18}
.item .thumb{position:relative;display:block;aspect-ratio:16/9}
.item .thumb .play{position:absolute;inset:0;margin:auto;width:46px;
  height:46px;border-radius:50%;background:rgba(0,0,0,.55);opacity:.85}
.item:hover .thumb .play{opacity:1}
.item .thumb .play::after{content:"";position:absolute;inset:0;margin:auto;
  width:0;height:0;border-style:solid;border-width:8px 0 8px 14px;
  border-color:transparent transparent transparent #fff;left:3px}
.item .card-body{padding:10px 12px 13px}
.item-link{display:block;font-weight:600;font-size:14px;line-height:1.35}
.item:hover .item-link{color:#ff9e4d}
.tag-chip{display:inline-block;margin-top:9px;font-size:11px;font-weight:800;
  color:#ff9e4d;border:1px solid #4a3418;background:#241a0d;padding:2px 7px;
  border-radius:4px;letter-spacing:.4px;text-transform:uppercase}
#load-more{display:inline-block;margin-top:24px;background:#ff7a18;
  color:#1a1209;font-weight:700;font-size:14px;padding:11px 26px;
  border-radius:8px;cursor:pointer}
/* scene page */
.player{position:relative;aspect-ratio:16/9;width:100%;border-radius:13px;
  border:1px solid #26262c;margin-bottom:18px;overflow:hidden;
  background:linear-gradient(135deg,#2a1c34,#16161b)}
.player .play{position:absolute;inset:0;margin:auto;width:86px;height:86px;
  border-radius:50%;background:rgba(0,0,0,.55)}
.player .play::after{content:"";position:absolute;inset:0;margin:auto;
  width:0;height:0;border-style:solid;border-width:16px 0 16px 27px;
  border-color:transparent transparent transparent #fff;left:6px}
.scene-title{font-size:21px;font-weight:700;margin-bottom:14px}
/* primary download button - selected by media href, not class, so a
   paywalled stub never carries that anchor token */
main a[href*="media2"]{display:inline-block;background:#ff7a18;
  color:#1a1209;font-weight:700;font-size:14px;padding:11px 22px;
  border-radius:8px}
main a[href*="media2"]:hover{background:#ff9e4d}
/* challenge interstitial */
#challenge{background:#1b1b1f;border:1px solid #26262c;border-radius:12px;
  padding:26px;margin:18px 0;font-weight:600;color:#ffb13d}
.challenge-note{font-size:13px;color:#8e8e98}
/* paywall */
.paywall{background:#1b1b1f;border:1px dashed #4a3418;border-radius:12px;
  padding:26px;margin-top:14px;text-align:center}
.paywall p{color:#9a9aa3;margin-bottom:14px}
.paywall a{display:inline-block;background:#ff7a18;color:#1a1209;
  font-weight:700;padding:10px 22px;border-radius:8px}
/* login */
.login{max-width:340px;margin:34px auto;background:#1b1b1f;
  border:1px solid #26262c;border-radius:13px;padding:28px}
.login input{display:block;width:100%;margin-bottom:12px;padding:10px 12px;
  background:#0f0f10;border:1px solid #303038;border-radius:7px;
  color:#e8e8ea;font-size:14px}
.login button{width:100%;padding:11px;background:#ff7a18;color:#1a1209;
  font-weight:700;border:0;border-radius:8px;cursor:pointer;font-size:14px}
.login-error{color:#ff6b6b;font-weight:600;text-align:center}
/* spa shell */
#app{min-height:120px;border:1px dashed #303038;border-radius:12px;
  margin:16px 0;display:flex;align-items:center;justify-content:center;
  color:#5a5a63;font-size:13px}
#app::before{content:"SPA shell - content loads via data-api"}
/* footer */
.fx-foot{max-width:1180px;margin:0 auto;padding:20px 22px;color:#5a5a63;
  font-size:12px;border-top:1px solid #26262c;line-height:1.6}
"""

_NAV = (
    "<nav class='fx-nav'>"
    "<a class='fx-brand' href='/'>Test<b>Tube</b></a>"
    "<div class='fx-search'>"
    "<input placeholder='Search items...'><span>Search</span></div>"
    "<div class='fx-links'>"
    "<a href='/'>Home</a>"
    "<a href='/infinite/catalog'>Infinite</a>"
    "<a href='/challenge/scene/3'>Challenge</a>"
    "<a href='/paywall/scene/3'>Paywall</a>"
    "<a href='/spa/'>SPA</a></div>"
    "<span class='fx-badge'>FIXTURE 2</span>"
    "</nav>")

_FOOT = (
    "<footer class='fx-foot'>"
    "TestTube - synthetic test fixture for BulkDownloader (awkward "
    "site shapes). Not a real website. Every item and video file is "
    "placeholder data; nothing here is real content and no external "
    "site is contacted."
    "</footer>")


def _thumb_style(n: int) -> str:
    """Deterministic per-item gradient - CSS only, no images."""
    h1 = (n * 47) % 360
    h2 = (h1 + 38) % 360
    return (f"background:linear-gradient(135deg,"
            f"hsl({h1} 52% 27%),hsl({h2} 58% 16%))")


def _page(title: str, body: str) -> str:
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' "
            "content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{_CSS}</style></head><body>"
            f"{_NAV}<main class='fx-wrap'>{body}</main>{_FOOT}"
            "</body></html>")


def make_app() -> Flask:
    """Build the second fixture app. No options - it has no fault
    injector or throttle simulation; it's purely about site shapes."""
    app = Flask("bd_fixture_site2")

    @app.after_request
    def tag(resp):
        resp.headers["X-Fixture-Site"] = "bd-test-fixture-2"
        return resp

    # ── Landing ─────────────────────────────────────────────────────
    @app.route("/")
    def index():
        return _page("TestTube - Fixture 2",
                     "<div class='hero'>"
                     "<h1>BulkDownloader Test Fixture 2 "
                     "<span class='fx-badge'>SHAPE LAB</span></h1>"
                     "<p>The awkward site shapes the first fixture "
                     "doesn't cover - infinite scroll, JSON/GraphQL "
                     "APIs, challenge interstitials, paywalls and SPA "
                     "shells. All synthetic; not a real website.</p>"
                     "<div class='shapes'>"
                     "<a class='shape-card' href='/infinite/catalog'>"
                     "<b>Infinite scroll</b>"
                     "<span>No page numbers - a batch API feeds more "
                     "items as you scroll.</span></a>"
                     "<a class='shape-card' href='/api/items'>"
                     "<b>JSON / GraphQL API</b>"
                     "<span>Catalog served as JSON, not scraped from "
                     "HTML.</span></a>"
                     "<a class='shape-card' href='/challenge/scene/3'>"
                     "<b>Challenge interstitial</b>"
                     "<span>Cloudflare-style gate - solve it before "
                     "content loads.</span></a>"
                     "<a class='shape-card' href='/paywall/scene/3'>"
                     "<b>Paywall</b>"
                     "<span>Title visible for SEO; the download is "
                     "behind a subscription.</span></a>"
                     "<a class='shape-card' href='/spa/'>"
                     "<b>Single-page app</b>"
                     "<span>Near-empty shell - content arrives via a "
                     "later API call.</span></a>"
                     "</div></div>")

    # ── Shared media endpoint ───────────────────────────────────────
    @app.route("/media2/<int:iid>.mp4")
    def media2(iid):
        data = _make_mp4(6144 + (iid % 5) * 1024)
        r = make_response(data)
        r.headers["Content-Type"] = "video/mp4"
        r.headers["Content-Length"] = str(len(data))
        r.headers["Content-Disposition"] = (
            f"attachment; filename=item_{iid:03d}.mp4")
        return r

    # ════════════════════════════════════════════════════════════════
    # /infinite - infinite-scroll catalog
    # ════════════════════════════════════════════════════════════════
    @app.route("/infinite/catalog")
    def infinite_catalog():
        # The page itself has only the first batch + a "load more"
        # hook. There are NO page-number links - an adapter must call
        # the batch API with an increasing offset.
        first = ""
        for n in range(_BATCH):
            it = _item(n)
            first += (
                f"<div class='item' data-id='{n}'>"
                f"<a class='thumb' style='{_thumb_style(n)}' "
                f"href='/infinite/scene/{n}'>"
                f"<span class='play'></span></a>"
                f"<div class='card-body'>"
                f"<a class='item-link' href='/infinite/scene/{n}'>"
                f"{it['title']}</a>"
                f"<span class='tag-chip'>{it['tag']}</span>"
                f"</div></div>")
        return _page("Infinite Catalog",
                     f"<h1>Infinite Catalog</h1>"
                     f"<div id='items' class='item-list'>{first}</div>"
                     f"<div id='load-more' data-next-offset='{_BATCH}' "
                     f"data-batch='{_BATCH}' data-total='{_TOTAL}'>"
                     f"Load more</div>"
                     f"<script>/* real site would fetch "
                     f"/infinite/batch?offset=N here */</script>")

    @app.route("/infinite/batch")
    def infinite_batch():
        try:
            offset = max(0, int(request.args.get("offset", "0")))
        except ValueError:
            offset = 0
        end = min(offset + _BATCH, _TOTAL)
        items = [_item(n) for n in range(offset, end)]
        return jsonify({
            "items": items,
            "next_offset": end if end < _TOTAL else None,
            "total": _TOTAL,
        })

    @app.route("/infinite/scene/<int:iid>")
    def infinite_scene(iid):
        if not 0 <= iid < _TOTAL:
            return "Not found", 404
        it = _item(iid)
        return _page(it["title"],
                     f"<div class='player' style='{_thumb_style(iid)}'>"
                     f"<span class='play'></span></div>"
                     f"<h1 class='scene-title'>{it['title']}</h1>"
                     f"<a class='download-link' "
                     f"href='/media2/{iid}.mp4'>Download</a>")

    # ════════════════════════════════════════════════════════════════
    # /api - JSON / GraphQL-style API surface
    # ════════════════════════════════════════════════════════════════
    @app.route("/api/items")
    def api_items():
        """REST-style: paginated JSON list."""
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            page = 1
        start = (page - 1) * _BATCH
        end = min(start + _BATCH, _TOTAL)
        return jsonify({
            "page": page,
            "per_page": _BATCH,
            "total": _TOTAL,
            "items": [_item(n) for n in range(start, end)],
        })

    @app.route("/api/item/<int:iid>")
    def api_item(iid):
        if not 0 <= iid < _TOTAL:
            return jsonify({"error": "not found"}), 404
        return jsonify(_item(iid))

    @app.route("/api/graphql", methods=["POST"])
    def api_graphql():
        """GraphQL-style: a single POST endpoint. Recognizes two toy
        queries by substring - enough to test an adapter that speaks
        GraphQL without implementing a real parser."""
        body = request.get_json(silent=True) or {}
        query = (body.get("query") or "")
        if "items" in query:
            return jsonify({"data": {
                "items": [_item(n) for n in range(_BATCH)]}})
        if "item" in query:
            iid = 0
            vs = body.get("variables") or {}
            if isinstance(vs, dict) and "id" in vs:
                try:
                    iid = int(vs["id"])
                except (ValueError, TypeError):
                    iid = 0
            return jsonify({"data": {"item": _item(iid)}})
        return jsonify({"errors": [{"message": "unknown query"}]}), 400

    # ════════════════════════════════════════════════════════════════
    # /challenge - Cloudflare-style interstitial
    # ════════════════════════════════════════════════════════════════
    @app.route("/challenge/scene/<int:iid>")
    def challenge_scene(iid):
        # If the "challenge solved" cookie is absent, return the
        # interstitial instead of the content. The interstitial
        # carries the token an adapter must echo back as a cookie.
        if request.cookies.get("cf_clearance") != "solved-ok":
            resp = make_response(_page(
                "Checking your browser",
                "<h1>Checking your browser before you continue</h1>"
                "<div id='challenge' data-solve-token='solved-ok'>"
                "Please wait...</div>"
                "<p class='challenge-note'>This is a synthetic "
                "challenge page. Set cookie cf_clearance=solved-ok "
                "to proceed.</p>"))
            resp.status_code = 503
            resp.headers["X-Challenge"] = "interstitial"
            return resp
        if not 0 <= iid < _TOTAL:
            return "Not found", 404
        return _page(_item(iid)["title"],
                     f"<h1 class='scene-title'>{_item(iid)['title']}</h1>"
                     f"<a class='download-link' "
                     f"href='/media2/{iid}.mp4'>Download</a>")

    @app.route("/challenge/solve")
    def challenge_solve():
        """Convenience endpoint: visiting it sets the clearance cookie,
        simulating a solved challenge."""
        resp = make_response(redirect("/challenge/scene/3"))
        resp.set_cookie("cf_clearance", "solved-ok")
        return resp

    # ════════════════════════════════════════════════════════════════
    # /paywall - subscription-gated content
    # ════════════════════════════════════════════════════════════════
    @app.route("/paywall/login", methods=["GET", "POST"])
    def paywall_login():
        if request.method == "GET":
            return _page("Subscribe / Log in",
                         "<form class='login' method='post'>"
                         "<input id='login-username' name='username'>"
                         "<input id='login-password' name='password' "
                         "type='password'>"
                         "<button type='submit'>Log in</button></form>")
        if (request.form.get("username") == USERNAME
                and request.form.get("password") == PASSWORD):
            resp = make_response(redirect("/paywall/scene/3"))
            resp.set_cookie("subscription", "active")
            return resp
        return _page("Login failed",
                     "<p class='login-error'>Invalid credentials</p>"), 401

    @app.route("/paywall/scene/<int:iid>")
    def paywall_scene(iid):
        if not 0 <= iid < _TOTAL:
            return "Not found", 404
        if request.cookies.get("subscription") != "active":
            # Unsubscribed: a paywall stub. The title is visible (sites
            # do this for SEO) but the download link is not.
            return _page(_item(iid)["title"],
                         f"<h1 class='scene-title'>{_item(iid)['title']}"
                         f"</h1>"
                         f"<div class='paywall' data-locked='true'>"
                         f"<p>This content requires a subscription.</p>"
                         f"<a href='/paywall/login'>Log in</a></div>")
        return _page(_item(iid)["title"],
                     f"<h1 class='scene-title'>{_item(iid)['title']}</h1>"
                     f"<a class='download-link' "
                     f"href='/media2/{iid}.mp4'>Download</a>")

    # ════════════════════════════════════════════════════════════════
    # /spa - single-page-app shell
    # ════════════════════════════════════════════════════════════════
    @app.route("/spa/")
    def spa_shell():
        # Almost-empty HTML: a real adapter must execute JS or call the
        # API to get anything. The shell only names the API endpoint.
        return _page("SPA",
                     "<div id='app' data-api='/spa/api/state'></div>"
                     "<script>/* real SPA would fetch data-api and "
                     "render here */</script>")

    @app.route("/spa/api/state")
    def spa_state():
        return jsonify({
            "view": "catalog",
            "items": [_item(n) for n in range(_BATCH)],
        })

    @app.route("/spa/api/item/<int:iid>")
    def spa_item(iid):
        if not 0 <= iid < _TOTAL:
            return jsonify({"error": "not found"}), 404
        return jsonify(_item(iid))

    # ── Route map (test introspection; read-only) ───────────────────
    @app.route("/_sitemap")
    def sitemap():
        return jsonify({"routes": sorted(
            str(r) for r in app.url_map.iter_rules())})

    return app


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="BulkDownloader test-fixture site 2 (awkward "
                    "site shapes).")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8898)
    args = p.parse_args(argv)
    app = make_app()
    print(f"Fixture site 2 on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
