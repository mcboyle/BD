#!/usr/bin/env python3
"""fixture_site.py - a self-hosted mock site for testing BulkDownloader.

WHAT THIS IS
------------
A standalone Flask app serving SYNTHETIC pages shaped like the sites
BulkDownloader's adapters target. The HTML matches the selector shapes
in bulk_downloader/templates.py - video.js, JW Player, resolution-anchor
lists, data-href patterns, the VIP4K login + 4-download-variant markup,
HLS playlists. It also has a fault injector with a management UI so you
can simulate connection drops, timeouts, 404s, truncated downloads, and
more - on demand, against otherwise-normal endpoints.

It exists so you can develop and test adapters, the download path, and
throttle/error handling against something deterministic - no live
sites, no network flakiness, no AUP/ToS problem, because there is no
real site involved. All content is placeholder; all video files are
tiny generated MP4s. It mimics the STRUCTURE of sites, never content.

LAYOUT: multiple prefixes, each a self-contained "site"
-------------------------------------------------------
  LOGIN     /formauth  /basicauth  /tokenauth  /csrfauth
  DOWNLOAD  /direct  /range  /redirect  /lazy  /hls
  MARKUP    /videojs  /jwplayer  /reslinks  /datahref  /vip4k
  THROTTLE  /slow  /flaky
  CONTROL   /_control  (management UI - no access control)

FAULT INJECTION
---------------
Open http://127.0.0.1:8899/_control in a browser. Each fault has an
Arm button; armed faults fire on matching requests. A fault with a
target path fires only on requests whose path contains it; a fault
with NO target path applies to ALL requests - pages and downloads
alike. The /_control routes are always exempt so the UI stays usable
even with a no-path fault armed.

  connection_lost  socket closed mid-response (partial body)
  timeout          accept then hang (never respond)
  http_404         content removed
  http_410         content gone
  http_500/502/503 server errors
  http_429         rate limited (+ Retry-After)
  slow_trickle     body sent very slowly (stall vs hang)
  truncate         Content-Length says big, body stops short
  corrupt          right length, wrong bytes (integrity-check bait)
  redirect_loop    A->B->A forever
  flapping         fail N times then succeed
  refused          instant 503 (connection-refused analogue)
  resume_fail      Range requests answered 200 not 206

CREDENTIALS (all login prefixes)
--------------------------------
  username: tester    password: fixturepass
  bearer token:       fixture-token-abc123

RUN
---
  python tools/fixture_site.py
  python tools/fixture_site.py --port 9000 --host 0.0.0.0
(/_control has no access control - don't bind 0.0.0.0 on an untrusted
network.)
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import threading
import time

from flask import Flask, Response, jsonify, make_response, redirect, request

USERNAME = "tester"
PASSWORD = "fixturepass"
BEARER = "fixture-token-abc123"

_TOTAL = 57
_PER_PAGE = 12

# Fault types the UI offers. id -> human label.
FAULT_TYPES = [
    ("connection_lost", "Connection lost (partial body)"),
    ("timeout", "Timeout / hang"),
    ("http_404", "404 Not Found"),
    ("http_410", "410 Gone"),
    ("http_500", "500 Server Error"),
    ("http_502", "502 Bad Gateway"),
    ("http_503", "503 Service Unavailable"),
    ("http_429", "429 Too Many Requests"),
    ("slow_trickle", "Slow trickle (byte-by-byte)"),
    ("truncate", "Truncated download"),
    ("corrupt", "Corrupt payload (bad bytes)"),
    ("redirect_loop", "Redirect loop"),
    ("flapping", "Flapping (fail then OK)"),
    ("refused", "Connection refused (503)"),
    ("resume_fail", "Resume failure (200 not 206)"),
]
_FAULT_IDS = {f[0] for f in FAULT_TYPES}

# One-line description per fault, shown in the injection console.
FAULT_DESC = {
    "connection_lost": "Socket closed mid-response - client gets a partial body.",
    "timeout": "Connection accepted, then the server hangs and never responds.",
    "http_404": "Content removed - 404 Not Found.",
    "http_410": "Content permanently gone - 410 Gone.",
    "http_500": "Server-side failure - 500 Internal Server Error.",
    "http_502": "Bad gateway - 502, upstream returned garbage.",
    "http_503": "Service unavailable - 503.",
    "http_429": "Rate limited - 429 with a Retry-After header.",
    "slow_trickle": "Body dribbles out byte-by-byte - stall vs hang test.",
    "truncate": "Content-Length claims a big file; the body stops short.",
    "corrupt": "Right length, wrong bytes - bait for integrity checks.",
    "redirect_loop": "Endpoint A redirects to B redirects to A, forever.",
    "flapping": "Fails N times, then succeeds - retry/backoff test.",
    "refused": "Instant 503 - the connection-refused analogue.",
    "resume_fail": "Range requests answered 200 (full) instead of 206.",
}

# Category per fault - drives the colour tag in the console.
FAULT_CAT = {
    "connection_lost": "body", "truncate": "body", "corrupt": "body",
    "timeout": "timing", "slow_trickle": "timing", "flapping": "timing",
    "http_404": "error", "http_410": "error", "http_500": "error",
    "http_502": "error", "http_503": "error", "http_429": "error",
    "refused": "error",
    "redirect_loop": "routing", "resume_fail": "routing",
}

# ── Presentation ────────────────────────────────────────────────────
# Pure styling. None of this changes a single response the adapters
# parse - the HTML structure (classes, ids, data-attrs, hrefs) is
# untouched; this only adds a stylesheet plus a nav/footer wrapper so
# the fixture reads like a real tube site instead of raw markup.
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
.fx-links{display:flex;gap:15px;font-size:14px;color:#9a9aa3}
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
.quick{display:flex;flex-wrap:wrap;gap:10px;margin-top:18px}
.quick a{background:#1b1b1f;border:1px solid #303038;border-radius:8px;
  padding:8px 14px;font-size:13px;font-weight:600;color:#cfcfd4}
.quick a:hover{border-color:#ff7a18;color:#ff9e4d}
/* catalog grid */
.scene-grid{display:grid;gap:18px;
  grid-template-columns:repeat(auto-fill,minmax(212px,1fr))}
.scene-card{background:#1b1b1f;border:1px solid #26262c;border-radius:11px;
  overflow:hidden;transition:transform .12s ease,border-color .12s ease}
.scene-card:hover{transform:translateY(-3px);border-color:#ff7a18}
.thumb{position:relative;display:block;aspect-ratio:16/9}
.thumb .play{position:absolute;inset:0;margin:auto;width:46px;height:46px;
  border-radius:50%;background:rgba(0,0,0,.55);opacity:.85;
  transition:opacity .12s}
.scene-card:hover .thumb .play{opacity:1}
.thumb .play::after{content:"";position:absolute;inset:0;margin:auto;
  width:0;height:0;border-style:solid;border-width:8px 0 8px 14px;
  border-color:transparent transparent transparent #fff;left:3px}
.thumb .dur{position:absolute;right:7px;bottom:7px;background:rgba(0,0,0,.82);
  font-size:11px;font-weight:700;padding:2px 6px;border-radius:4px}
.card-body{padding:10px 12px 13px}
.scene-link{display:block;font-weight:600;font-size:14px;line-height:1.35}
.scene-card:hover .scene-link{color:#ff9e4d}
.scene-meta{font-size:12px;color:#8e8e98;margin-top:5px}
.resolution{display:inline-block;margin-top:9px;font-size:11px;font-weight:800;
  color:#ff9e4d;border:1px solid #4a3418;background:#241a0d;padding:2px 7px;
  border-radius:4px;letter-spacing:.4px}
/* pagination */
.pagination{display:flex;gap:10px;margin-top:28px}
.pagination a{padding:9px 19px;background:#1b1b1f;border:1px solid #303038;
  border-radius:8px;font-weight:600;font-size:14px}
.pagination a:hover{border-color:#ff7a18;color:#ff9e4d}
.page-info{font-size:12px;color:#8e8e98;margin-bottom:14px}
/* scene / player page */
.player{position:relative;aspect-ratio:16/9;width:100%;border-radius:13px;
  border:1px solid #26262c;margin-bottom:18px;overflow:hidden}
.player .play{position:absolute;inset:0;margin:auto;width:86px;height:86px;
  border-radius:50%;background:rgba(0,0,0,.55)}
.player .play::after{content:"";position:absolute;inset:0;margin:auto;
  width:0;height:0;border-style:solid;border-width:16px 0 16px 27px;
  border-color:transparent transparent transparent #fff;left:6px}
.player .tag{position:absolute;left:12px;top:12px;background:rgba(0,0,0,.7);
  font-size:11px;font-weight:700;padding:3px 9px;border-radius:5px;
  color:#9a9aa3}
.scene-title{font-size:21px;font-weight:700;margin-bottom:11px}
.meta-display{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px}
.meta-display span{background:#1b1b1f;border:1px solid #26262c;
  font-size:12px;color:#9a9aa3;padding:5px 11px;border-radius:6px}
/* download buttons (covers every markup variant) */
.download-link,.vjs-download-button,.ct_dl_button,.download__item,
.download-button,.download-quality{display:inline-block;background:#ff7a18;
  color:#1a1209;font-weight:700;font-size:14px;padding:11px 22px;
  border-radius:8px;border:0;cursor:pointer}
.download-link:hover,.vjs-download-button:hover,.ct_dl_button:hover,
.download__item:hover,.download-button:hover,.download-quality:hover{
  background:#ff9e4d}
.downloads{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px}
.exp-menu-item{display:inline-block;margin-top:10px;background:#241a0d;
  border:1px solid #4a3418;color:#ff9e4d;font-weight:700;font-size:12px;
  padding:5px 11px;border-radius:5px}
/* video elements */
video{width:100%;max-width:760px;border-radius:13px;background:#000;
  border:1px solid #26262c;display:block}
.video-js{outline:0}
.player-mount{min-height:90px;display:flex;align-items:center}
/* login card */
.login{max-width:340px;margin:34px auto;background:#1b1b1f;
  border:1px solid #26262c;border-radius:13px;padding:28px}
.login input{display:block;width:100%;margin-bottom:12px;padding:10px 12px;
  background:#0f0f10;border:1px solid #303038;border-radius:7px;
  color:#e8e8ea;font-size:14px}
.login button{width:100%;padding:11px;background:#ff7a18;color:#1a1209;
  font-weight:700;border:0;border-radius:8px;cursor:pointer;font-size:14px}
.login-error{color:#ff6b6b;font-weight:600;text-align:center;margin-top:6px}
/* fault-injection console */
.console-head{background:linear-gradient(135deg,#221310,#1b1b1f);
  border:1px solid #303038;border-radius:13px;padding:24px 26px;
  margin-bottom:22px}
.console-head p{color:#9a9aa3;margin-top:7px;max-width:720px;font-size:14px}
.panel{background:#1b1b1f;border:1px solid #26262c;border-radius:11px;
  padding:18px 20px;margin-bottom:18px}
.cfg-row{display:flex;flex-wrap:wrap;gap:18px;align-items:flex-end}
.cfg-row label{font-size:12px;color:#9a9aa3;display:flex;flex-direction:column;
  gap:6px;text-transform:uppercase;letter-spacing:.5px}
.cfg-row input,.cfg-row select{padding:9px 11px;background:#0f0f10;
  border:1px solid #303038;border-radius:7px;color:#e8e8ea;font-size:14px}
.cfg-row input{min-width:280px}
.fault-grid{display:grid;gap:13px;
  grid-template-columns:repeat(auto-fill,minmax(248px,1fr))}
.fault-card{background:#1b1b1f;border:1px solid #26262c;border-radius:10px;
  padding:14px;display:flex;flex-direction:column;gap:8px;
  border-left:3px solid #45454f}
.fault-card.cat-error{border-left-color:#ff5d5d}
.fault-card.cat-body{border-left-color:#ffb13d}
.fault-card.cat-timing{border-left-color:#5db4ff}
.fault-card.cat-routing{border-left-color:#b98dff}
.fault-head{display:flex;justify-content:space-between;align-items:center;
  gap:8px}
.fault-name{font-weight:700;font-size:14px}
.cat-tag{font-size:9px;font-weight:800;letter-spacing:.6px;padding:2px 6px;
  border-radius:4px;background:#26262c;color:#9a9aa3;text-transform:uppercase}
.fault-desc{font-size:12px;color:#8e8e98;flex:1;line-height:1.45}
.arm-btn{align-self:flex-start;background:#ff7a18;color:#1a1209;border:0;
  font-weight:700;font-size:13px;padding:7px 18px;border-radius:7px;
  cursor:pointer}
.arm-btn:hover{background:#ff9e4d}
#armed{display:flex;flex-direction:column;gap:8px}
.armed-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  background:#0f0f10;border:1px solid #303038;border-radius:8px;
  padding:9px 12px;font-size:13px}
.armed-row .aid{font-weight:800;color:#ff9e4d}
.armed-row code{background:#26262c;padding:1px 6px;border-radius:4px;
  font-size:12px}
.armed-row button{margin-left:auto;background:#3a2020;color:#ff8d8d;
  border:1px solid #5a2d2d;border-radius:6px;padding:5px 12px;
  font-weight:700;font-size:12px;cursor:pointer}
.armed-empty{color:#6a6a73;font-size:13px}
#stats{background:#0f0f10;border:1px solid #303038;border-radius:8px;
  padding:13px;font-size:12px;color:#cfcfd4;overflow:auto;
  font-family:Consolas,Menlo,monospace}
.reset-btn{background:#26262c;color:#cfcfd4;border:1px solid #3a3a44;
  border-radius:7px;padding:8px 16px;font-weight:600;font-size:13px;
  cursor:pointer;margin-top:12px}
.reset-btn:hover{border-color:#ff7a18;color:#ff9e4d}
/* footer */
.fx-foot{max-width:1180px;margin:0 auto;padding:20px 22px;color:#5a5a63;
  font-size:12px;border-top:1px solid #26262c;line-height:1.6}
"""

_NAV = (
    "<nav class='fx-nav'>"
    "<a class='fx-brand' href='/'>Test<b>Tube</b></a>"
    "<div class='fx-search'>"
    "<input placeholder='Search scenes, studios, performers...'>"
    "<span>Search</span></div>"
    "<div class='fx-links'>"
    "<a href='/'>Home</a><a href='/catalog'>Catalog</a>"
    "<a href='/_control'>Control</a></div>"
    "<span class='fx-badge'>FIXTURE</span>"
    "</nav>")

_FOOT = (
    "<footer class='fx-foot'>"
    "TestTube - synthetic test fixture for BulkDownloader. "
    "Not a real website. Every scene, studio, performer and video file "
    "is placeholder data generated on the fly; nothing here is real "
    "content and no external site is contacted."
    "</footer>")


def _scene(n: int) -> dict:
    return {
        "id": n,
        "title": f"Test Scene {n:03d}",
        "studio": f"Studio {(n % 5) + 1}",
        "performer": f"Performer {(n % 9) + 1}",
        "duration": f"{(n % 40) + 5}:00",
        "resolution": ["480p", "720p", "1080p", "2160p"][n % 4],
        "date": f"2026-{(n % 12) + 1:02d}-{(n % 27) + 1:02d}",
    }


def _make_mp4(size_bytes: int = 8192) -> bytes:
    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload) + 8) + kind + payload
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 0x200) + b"isomiso2mp41")
    pad = max(0, size_bytes - len(ftyp) - 8)
    return ftyp + box(b"free", b"\x00" * pad)


def _mp4_for(sid: int) -> bytes:
    return _make_mp4(4096 + (sid % 7) * 2048)


def _thumb_style(n: int) -> str:
    """Deterministic per-scene gradient so the catalog reads like a
    real thumbnail grid - no images, just CSS."""
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


def make_app(throttle_after: int = 3, slow_delay: float = 2.0) -> Flask:
    """Build the fixture app. throttle_after: /flaky/ 429s past this many
    concurrent requests. slow_delay: seconds /slow/ waits."""
    app = Flask("bd_fixture_site")
    lock = threading.Lock()
    state = {
        "requests": 0, "by_path": {}, "flaky_inflight": 0,
        "flaky_max_inflight": 0, "throttle_429s": 0,
        "logins_ok": 0, "logins_failed": 0, "faults_fired": 0,
    }
    armed: list[dict] = []
    fault_seq = [0]

    def count(path: str):
        with lock:
            state["requests"] += 1
            state["by_path"][path] = state["by_path"].get(path, 0) + 1

    def _find_fault(path: str):
        """Return the first armed fault matching this request, or None.
        Decrements the fault's 'times' budget and expires exhausted
        faults.

        A fault with a target path matches only requests whose path
        contains it. A fault with NO target path applies to ALL
        requests - pages and downloads alike."""
        with lock:
            for f in armed:
                target = f.get("path") or ""
                if target and target not in path:
                    continue
                # no target path -> matches everything
                f["fired"] += 1
                state["faults_fired"] += 1
                if f["times"] != "inf":
                    f["times"] = int(f["times"]) - 1
                hit = dict(f)
                armed[:] = [a for a in armed
                            if a["times"] == "inf" or int(a["times"]) > 0]
                return hit
        return None

    def _apply_fault(fault: dict, sid: int = 0):
        """Produce the faulty Response for a matched fault, or None to
        fall through to normal handling.

        `sid` is the scene id when a download endpoint matched; page
        routes pass the default 0. It only affects the size/content of
        body-faults (truncate/corrupt/connection_lost) - for those a
        page request gets a faulty MP4-shaped body, which is fine: the
        point of the fault is the failure mode, not the payload."""
        ft = fault["type"]
        data = _mp4_for(sid)
        if ft == "http_404":
            return make_response("Not Found", 404)
        if ft == "http_410":
            return make_response("Gone", 410)
        if ft == "http_500":
            return make_response("Internal Server Error", 500)
        if ft == "http_502":
            return make_response("Bad Gateway", 502)
        if ft in ("http_503", "refused"):
            return make_response("Service Unavailable", 503)
        if ft == "http_429":
            r = make_response("Too Many Requests", 429)
            r.headers["Retry-After"] = "5"
            return r
        if ft == "timeout":
            time.sleep(60)
            return make_response("(timeout fault elapsed)", 504)
        if ft == "connection_lost":
            half = data[:len(data) // 2]

            def gen_lost():
                yield half
            r = Response(gen_lost(), mimetype="video/mp4")
            r.headers["Content-Length"] = str(len(data))
            return r
        if ft == "truncate":
            part = data[:max(1, len(data) // 5)]
            r = make_response(part)
            r.headers["Content-Type"] = "video/mp4"
            r.headers["Content-Length"] = str(len(data))
            return r
        if ft == "corrupt":
            bad = (b"\xde\xad\xbe\xef" * (len(data) // 4 + 1))[:len(data)]
            r = make_response(bad)
            r.headers["Content-Type"] = "video/mp4"
            r.headers["Content-Length"] = str(len(bad))
            return r
        if ft == "slow_trickle":
            def gen_trickle():
                for b in data[:64]:
                    yield bytes([b])
                    time.sleep(0.5)
            r = Response(gen_trickle(), mimetype="video/mp4")
            r.headers["Content-Length"] = str(len(data))
            return r
        if ft == "redirect_loop":
            return redirect(f"/_loop/a/{sid}.mp4")
        if ft == "flapping":
            return make_response("Service Unavailable (flapping)", 503)
        if ft == "resume_fail":
            r = make_response(data)
            r.headers["Content-Type"] = "video/mp4"
            r.headers["Content-Length"] = str(len(data))
            return r
        return None

    @app.after_request
    def tag(resp):
        resp.headers["X-Fixture-Site"] = "bd-test-fixture"
        return resp

    @app.before_request
    def _global_fault_hook():
        """Fault injection runs here so an armed fault fires on ANY
        matching request - pages and downloads alike. The /_control
        management routes are exempt so the UI stays usable even with
        a no-path fault armed.

        If a download endpoint has a scene id in its path we parse it
        out so body-faults size their payload correctly; otherwise
        sid defaults to 0."""
        path = request.path or ""
        if path.startswith("/_control"):
            return None
        fault = _find_fault(path)
        if not fault:
            return None
        # Best-effort scene-id extraction for body-faults.
        sid = 0
        import re as _re
        m = _re.search(r"/(\d+)(?:\.mp4|_\d+\.ts|/?$)", path)
        if m:
            try:
                sid = int(m.group(1))
            except ValueError:
                sid = 0
        faulty = _apply_fault(fault, sid)
        # Returning a response from before_request short-circuits the
        # normal view. Returning None lets the request proceed (used
        # when _apply_fault has no opinion).
        return faulty

    # ── Landing + catalog ───────────────────────────────────────────
    @app.route("/")
    def index():
        count("/")
        return _page("TestTube - Fixture Site",
                     "<div class='hero'>"
                     "<h1>BulkDownloader Test Fixture "
                     "<span class='fx-badge'>SYNTHETIC</span></h1>"
                     "<p>A self-hosted mock tube site for exercising "
                     "adapters, the download path, and fault handling. "
                     "Every scene, studio, performer and video file is "
                     "placeholder data - this is not a real website and "
                     "no external site is ever contacted.</p>"
                     "<div class='quick'>"
                     "<a class='catalog-link' href='/catalog'>Browse "
                     "catalog</a>"
                     "<a href='/videojs/scene/0'>video.js markup</a>"
                     "<a href='/jwplayer/scene/0'>JW Player markup</a>"
                     "<a href='/vip4k/scene/0'>VIP4K markup</a>"
                     "<a href='/hls/scene/0.m3u8'>HLS playlist</a>"
                     "<a href='/_control'>Fault console</a>"
                     "</div></div>")

    @app.route("/catalog")
    def catalog():
        count("/catalog")
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            page = 1
        start = (page - 1) * _PER_PAGE
        last = (_TOTAL + _PER_PAGE - 1) // _PER_PAGE
        cards = ""
        for n in range(start, min(start + _PER_PAGE, _TOTAL)):
            s = _scene(n)
            cards += (
                f"<div class='scene-card' data-scene-id='{n}'>"
                f"<a class='thumb' style='{_thumb_style(n)}' "
                f"href='/scene/{n}'>"
                f"<span class='play'></span>"
                f"<span class='dur'>{s['duration']}</span></a>"
                f"<div class='card-body'>"
                f"<a class='scene-link' href='/scene/{n}'>"
                f"{s['title']}</a>"
                f"<div class='scene-meta'>{s['studio']} &middot; "
                f"{s['performer']}</div>"
                f"<span class='resolution'>{s['resolution']}</span>"
                f"</div></div>")
        nav = ""
        if page > 1:
            nav += f"<a class='prev' href='/catalog?page={page-1}'>Prev</a>"
        if page < last:
            nav += f"<a class='next' href='/catalog?page={page+1}'>Next</a>"
        return _page(f"Catalog - page {page}",
                     f"<h1>Catalog</h1>"
                     f"<div class='page-info' data-page='{page}' "
                     f"data-last='{last}' data-total='{_TOTAL}'>"
                     f"Page {page} of {last} &middot; {_TOTAL} scenes"
                     f"</div>"
                     f"<div class='scene-grid'>{cards}</div>"
                     f"<nav class='pagination'>{nav}</nav>")

    @app.route("/scene/<int:sid>")
    def scene(sid):
        count(f"/scene/{sid}")
        if not 0 <= sid < _TOTAL:
            return "Not found", 404
        s = _scene(sid)
        return _page(s["title"],
                     f"<div class='player' style='{_thumb_style(sid)}'>"
                     f"<span class='tag'>SYNTHETIC PREVIEW</span>"
                     f"<span class='play'></span></div>"
                     f"<h1 class='scene-title'>{s['title']}</h1>"
                     f"<div class='meta' data-studio='{s['studio']}' "
                     f"data-performer='{s['performer']}' "
                     f"data-resolution='{s['resolution']}'></div>"
                     f"<div class='meta-display'>"
                     f"<span>Studio: {s['studio']}</span>"
                     f"<span>Performer: {s['performer']}</span>"
                     f"<span>{s['resolution']}</span>"
                     f"<span>{s['duration']}</span>"
                     f"<span>{s['date']}</span></div>"
                     f"<a class='download-link' "
                     f"href='/direct/media/{sid}.mp4'>"
                     f"Download {s['resolution']}</a>")

    # ── LOGIN: /formauth ────────────────────────────────────────────
    @app.route("/formauth/login", methods=["GET"])
    def formauth_form():
        count("/formauth/login")
        return _page("Login",
                     "<form class='login' method='post' "
                     "action='/formauth/login'>"
                     "<input id='login-username' name='username'>"
                     "<input id='login-password' name='password' "
                     "type='password'>"
                     "<button type='submit'>Log in</button></form>")

    @app.route("/formauth/login", methods=["POST"])
    def formauth_submit():
        count("/formauth/login[POST]")
        if (request.form.get("username") == USERNAME
                and request.form.get("password") == PASSWORD):
            with lock:
                state["logins_ok"] += 1
            r = make_response(redirect("/formauth/members"))
            r.set_cookie("fixture_session", "valid-session-token")
            return r
        with lock:
            state["logins_failed"] += 1
        return _page("Login failed",
                     "<p class='login-error'>Invalid credentials</p>"), 401

    @app.route("/formauth/members")
    def formauth_members():
        count("/formauth/members")
        if request.cookies.get("fixture_session") != "valid-session-token":
            return redirect("/formauth/login")
        return _page("Members",
                     "<h1>Members Area</h1><a class='download-link' "
                     "href='/direct/media/1.mp4'>Download</a>")

    # ── LOGIN: /basicauth ───────────────────────────────────────────
    @app.route("/basicauth/members")
    def basicauth_members():
        count("/basicauth/members")
        a = request.authorization
        if not a or a.username != USERNAME or a.password != PASSWORD:
            with lock:
                state["logins_failed"] += 1
            r = make_response("Unauthorized", 401)
            r.headers["WWW-Authenticate"] = 'Basic realm="Fixture"'
            return r
        with lock:
            state["logins_ok"] += 1
        return _page("Members",
                     "<h1>Basic Auth Members</h1><a class='download-link' "
                     "href='/direct/media/2.mp4'>Download</a>")

    # ── LOGIN: /tokenauth ───────────────────────────────────────────
    @app.route("/tokenauth/members")
    def tokenauth_members():
        count("/tokenauth/members")
        auth = request.headers.get("Authorization", "")
        key = request.headers.get("X-API-Key", "")
        if auth == f"Bearer {BEARER}" or key == BEARER:
            with lock:
                state["logins_ok"] += 1
            return _page("Members",
                         "<h1>Token Auth Members</h1>"
                         "<a class='download-link' "
                         "href='/direct/media/3.mp4'>Download</a>")
        with lock:
            state["logins_failed"] += 1
        return jsonify({"error": "missing or invalid token"}), 401

    # ── LOGIN: /csrfauth (two-step) ─────────────────────────────────
    def _csrf_token() -> str:
        return hashlib.sha256(b"fixture-csrf").hexdigest()[:16]

    @app.route("/csrfauth/login", methods=["GET"])
    def csrfauth_form():
        count("/csrfauth/login")
        return _page("Login",
                     f"<form class='login' method='post' "
                     f"action='/csrfauth/login'>"
                     f"<input type='hidden' name='csrf_token' "
                     f"value='{_csrf_token()}'>"
                     f"<input id='login-username' name='username'>"
                     f"<input id='login-password' name='password' "
                     f"type='password'>"
                     f"<button type='submit'>Log in</button></form>")

    @app.route("/csrfauth/login", methods=["POST"])
    def csrfauth_submit():
        count("/csrfauth/login[POST]")
        if request.form.get("csrf_token") != _csrf_token():
            return _page("Bad request",
                         "<p class='login-error'>CSRF token missing or "
                         "wrong</p>"), 403
        if (request.form.get("username") == USERNAME
                and request.form.get("password") == PASSWORD):
            with lock:
                state["logins_ok"] += 1
            r = make_response(redirect("/csrfauth/members"))
            r.set_cookie("fixture_session", "valid-session-token")
            return r
        with lock:
            state["logins_failed"] += 1
        return _page("Login failed",
                     "<p class='login-error'>Invalid credentials</p>"), 401

    @app.route("/csrfauth/members")
    def csrfauth_members():
        count("/csrfauth/members")
        if request.cookies.get("fixture_session") != "valid-session-token":
            return redirect("/csrfauth/login")
        return _page("Members",
                     "<h1>CSRF Auth Members</h1><a class='download-link' "
                     "href='/direct/media/4.mp4'>Download</a>")

    # ── DOWNLOAD: /direct ───────────────────────────────────────────
    # (Fault injection is handled globally by _global_fault_hook;
    #  these endpoints only implement the normal path.)
    @app.route("/direct/media/<int:sid>.mp4")
    def direct_media(sid):
        count(f"/direct/media/{sid}.mp4")
        data = _mp4_for(sid)
        r = make_response(data)
        r.headers["Content-Type"] = "video/mp4"
        r.headers["Content-Length"] = str(len(data))
        r.headers["Content-Disposition"] = (
            f"attachment; filename=scene_{sid:03d}.mp4")
        r.headers["Accept-Ranges"] = "bytes"
        return r

    # ── DOWNLOAD: /range (206 partial content) ──────────────────────
    @app.route("/range/media/<int:sid>.mp4")
    def range_media(sid):
        count(f"/range/media/{sid}.mp4")
        data = _mp4_for(sid)
        total = len(data)
        rng = request.headers.get("Range", "")
        if rng.startswith("bytes="):
            try:
                s_s, e_s = rng.split("=", 1)[1].split("-", 1)
                start = int(s_s) if s_s else 0
                end = int(e_s) if e_s else total - 1
                start, end = max(0, start), min(end, total - 1)
                if start > end:
                    r = make_response("", 416)
                    r.headers["Content-Range"] = f"bytes */{total}"
                    return r
                chunk = data[start:end + 1]
                r = make_response(chunk, 206)
                r.headers["Content-Type"] = "video/mp4"
                r.headers["Content-Range"] = f"bytes {start}-{end}/{total}"
                r.headers["Content-Length"] = str(len(chunk))
                r.headers["Accept-Ranges"] = "bytes"
                return r
            except (ValueError, IndexError):
                pass
        r = make_response(data)
        r.headers["Content-Type"] = "video/mp4"
        r.headers["Content-Length"] = str(total)
        r.headers["Accept-Ranges"] = "bytes"
        return r

    # ── DOWNLOAD: /redirect chain  +  /_loop fault loop ─────────────
    @app.route("/redirect/media/<int:sid>.mp4")
    def redirect_media(sid):
        count(f"/redirect/media/{sid}.mp4")
        return redirect(f"/redirect/hop1/{sid}.mp4")

    @app.route("/redirect/hop1/<int:sid>.mp4")
    def redirect_hop1(sid):
        count(f"/redirect/hop1/{sid}.mp4")
        return redirect(f"/redirect/hop2/{sid}.mp4")

    @app.route("/redirect/hop2/<int:sid>.mp4")
    def redirect_hop2(sid):
        count(f"/redirect/hop2/{sid}.mp4")
        return redirect(f"/direct/media/{sid}.mp4")

    @app.route("/_loop/a/<int:sid>.mp4")
    def loop_a(sid):
        count("/_loop/a")
        return redirect(f"/_loop/b/{sid}.mp4")

    @app.route("/_loop/b/<int:sid>.mp4")
    def loop_b(sid):
        count("/_loop/b")
        return redirect(f"/_loop/a/{sid}.mp4")

    # ── DOWNLOAD: /lazy (JS-injected URL) ───────────────────────────
    @app.route("/lazy/scene/<int:sid>")
    def lazy_scene(sid):
        count(f"/lazy/scene/{sid}")
        if not 0 <= sid < _TOTAL:
            return "Not found", 404
        return _page(f"Lazy {sid}",
                     f"<h1 class='scene-title'>Test Scene {sid:03d}</h1>"
                     f"<div id='player' class='player-mount'></div>"
                     f"<script>setTimeout(function(){{"
                     f"var a=document.createElement('a');"
                     f"a.className='download-link';"
                     f"a.href='/direct/media/{sid}.mp4';"
                     f"a.textContent='Download';"
                     f"document.getElementById('player').appendChild(a);"
                     f"}},300);</script>")

    # ── DOWNLOAD: /hls ──────────────────────────────────────────────
    @app.route("/hls/scene/<int:sid>.m3u8")
    def hls_playlist(sid):
        count(f"/hls/scene/{sid}.m3u8")
        segs = "\n".join(f"#EXTINF:6.0,\n/hls/seg/{sid}_{i}.ts"
                         for i in range(4))
        body = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:6\n"
                f"#EXT-X-MEDIA-SEQUENCE:0\n{segs}\n#EXT-X-ENDLIST\n")
        return Response(body, mimetype="application/vnd.apple.mpegurl")

    @app.route("/hls/seg/<int:sid>_<int:idx>.ts")
    def hls_segment(sid, idx):
        count(f"/hls/seg/{sid}_{idx}.ts")
        return Response((b"\x47" + b"\x00" * 187) * 16,
                        mimetype="video/mp2t")

    # ── MARKUP prefixes (match templates.py selector shapes) ────────
    @app.route("/videojs/scene/<int:sid>")
    def videojs_scene(sid):
        count(f"/videojs/scene/{sid}")
        return _page(f"VideoJS {sid}",
                     f"<video class='video-js' controls>"
                     f"<source src='/direct/media/{sid}.mp4' "
                     f"type='video/mp4'></video>"
                     f"<a class='vjs-download-button' "
                     f"href='/direct/media/{sid}.mp4'>Download</a>")

    @app.route("/jwplayer/scene/<int:sid>")
    def jwplayer_scene(sid):
        count(f"/jwplayer/scene/{sid}")
        return _page(f"JWPlayer {sid}",
                     f"<div id='jwplayer-container' class='jwplayer'>"
                     f"<div class='jw-media'>"
                     f"<video src='/direct/media/{sid}.mp4'></video>"
                     f"</div></div>")

    @app.route("/reslinks/scene/<int:sid>")
    def reslinks_scene(sid):
        count(f"/reslinks/scene/{sid}")
        links = "".join(
            f"<a class='download-quality' data-quality='{q}' "
            f"href='/direct/media/{sid}.mp4?q={q}'>{q}</a>"
            for q in ("720p", "1080p", "2160p"))
        return _page(f"ResLinks {sid}",
                     f"<div class='downloads'>{links}</div>")

    @app.route("/datahref/scene/<int:sid>")
    def datahref_scene(sid):
        count(f"/datahref/scene/{sid}")
        return _page(f"DataHref {sid}",
                     f"<div class='download-button' "
                     f"data-href='/direct/media/{sid}.mp4'>Download</div>")

    @app.route("/vip4k/scene/<int:sid>")
    def vip4k_scene(sid):
        count(f"/vip4k/scene/{sid}")
        return _page(f"VIP4K {sid}",
                     f"<a class='ct_dl_button' "
                     f"href='/direct/media/{sid}.mp4'>DL href</a>"
                     f"<div class='download-button' "
                     f"data-href='/direct/media/{sid}.mp4'>DL data-href</div>"
                     f"<a class='download__item' "
                     f"data-download='/direct/media/{sid}.mp4'>"
                     f"DL data-download</a>"
                     f"<div id='exDownloadMenu'>"
                     f"<span class='exp-menu-item' data-tier='2160p'>"
                     f"4K</span></div>")

    @app.route("/vip4k/login", methods=["GET", "POST"])
    def vip4k_login():
        count("/vip4k/login")
        if request.method == "GET":
            return _page("Login",
                         "<form class='login' method='post'>"
                         "<div class='login__fields'>"
                         "<input id='login-username' name='_username' "
                         "class='input__area'>"
                         "<input id='login-password' name='_password' "
                         "class='input__area' type='password'></div>"
                         "<button type='submit' class='btn--primary'>"
                         "Log in</button></form>")
        if (request.form.get("_username") == USERNAME
                and request.form.get("_password") == PASSWORD):
            with lock:
                state["logins_ok"] += 1
            r = make_response(redirect("/vip4k/scene/0"))
            r.set_cookie("fixture_session", "valid-session-token")
            return r
        with lock:
            state["logins_failed"] += 1
        return _page("Login failed",
                     "<p class='login-error'>Invalid</p>"), 401

    # ── THROTTLE: /slow  /flaky ─────────────────────────────────────
    @app.route("/slow/scene/<int:sid>")
    def slow_scene(sid):
        count(f"/slow/scene/{sid}")
        time.sleep(slow_delay)
        if not 0 <= sid < _TOTAL:
            return "Not found", 404
        return _page(f"Slow {sid}",
                     f"<h1 class='scene-title'>Test Scene {sid:03d}</h1>"
                     f"<a class='download-link' "
                     f"href='/direct/media/{sid}.mp4'>Download</a>")

    @app.route("/flaky/media/<int:sid>.mp4")
    def flaky_media(sid):
        count(f"/flaky/media/{sid}.mp4")
        with lock:
            state["flaky_inflight"] += 1
            inflight = state["flaky_inflight"]
            state["flaky_max_inflight"] = max(
                state["flaky_max_inflight"], inflight)
        try:
            if inflight > throttle_after:
                with lock:
                    state["throttle_429s"] += 1
                r = make_response("Too Many Requests", 429)
                r.headers["Retry-After"] = "5"
                return r
            time.sleep(0.25)
            data = _mp4_for(sid)
            r = make_response(data)
            r.headers["Content-Type"] = "video/mp4"
            r.headers["Content-Length"] = str(len(data))
            return r
        finally:
            with lock:
                state["flaky_inflight"] -= 1

    # ── CONTROL: management UI + endpoints ──────────────────────────
    # No access control - this is a localhost test tool. If you run the
    # fixture with --host 0.0.0.0 on an untrusted network, /_control is
    # reachable; don't do that.

    @app.route("/_control")
    def control_ui():
        fault_cards = "".join(
            f"<div class='fault-card cat-{FAULT_CAT.get(fid, 'misc')}'>"
            f"<div class='fault-head'>"
            f"<span class='fault-name'>{label}</span>"
            f"<span class='cat-tag'>{FAULT_CAT.get(fid, 'misc')}</span>"
            f"</div>"
            f"<p class='fault-desc'>{FAULT_DESC.get(fid, '')}</p>"
            f"<button class='arm-btn' onclick=\"arm('{fid}')\">Arm</button>"
            f"</div>"
            for fid, label in FAULT_TYPES)
        return _page("TestTube - Fault Console", """
<div class='console-head'>
  <h1>Fault Injection Console <span class='fx-badge'>LIVE</span></h1>
  <p>Arm a fault and it fires on matching requests. With a target path,
  it fires only where the path matches; with the field left blank it
  applies to every download request (media and segments), not pages.
  The console itself is always exempt.</p>
</div>
<h2>Configuration</h2>
<div class='panel'>
  <div class='cfg-row'>
    <label>Target path (blank = all downloads)
      <input id='target' placeholder='/direct/media/5.mp4'></label>
    <label>Fire count
      <select id='times'>
        <option value='1'>Once</option>
        <option value='3'>3 times</option>
        <option value='4'>4 (flapping: 3 fail + 1 ok)</option>
        <option value='inf'>Until disarmed</option>
      </select></label>
  </div>
</div>
<h2>Faults</h2>
<div class='fault-grid'>__FAULT_ROWS__</div>
<h2>Armed faults</h2>
<div class='panel'><div id='armed'>
  <span class='armed-empty'>No faults armed.</span></div></div>
<h2>Live stats</h2>
<div class='panel'><pre id='stats'>loading...</pre>
  <button class='reset-btn' onclick='resetAll()'>Reset counters</button>
</div>
<script>
function arm(type){
  var body = {type:type,
    path:document.getElementById('target').value,
    times:document.getElementById('times').value};
  fetch('/_control/inject',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}).then(refresh);
}
function disarm(id){
  fetch('/_control/disarm/'+id,{method:'POST'}).then(refresh);
}
function resetAll(){
  fetch('/_control/reset',{method:'POST'}).then(refresh);
}
function refresh(){
  fetch('/_control/armed').then(function(r){return r.json();})
    .then(function(d){
    if(!d.armed.length){
      document.getElementById('armed').innerHTML=
        "<span class='armed-empty'>No faults armed.</span>";
    } else {
      document.getElementById('armed').innerHTML=d.armed.map(
        function(f){return "<div class='armed-row'><span class='aid'>#"+
        f.id+"</span><b>"+f.type+"</b><code>"+(f.path||'(all paths)')+
        "</code><span>times="+f.times+"</span><span>fired="+f.fired+
        "</span><button onclick='disarm("+f.id+")'>Disarm</button></div>";
      }).join('');
    }
  });
  fetch('/_control/stats').then(function(r){return r.json();})
    .then(function(d){
    document.getElementById('stats').textContent=
      JSON.stringify(d,null,2);
  });
}
refresh(); setInterval(refresh,2000);
</script>""".replace("__FAULT_ROWS__", fault_cards))

    @app.route("/_control/inject", methods=["POST"])
    def control_inject():
        body = request.get_json(silent=True) or {}
        ftype = body.get("type", "")
        if ftype not in _FAULT_IDS:
            return jsonify({"error": f"unknown fault type: {ftype}"}), 400
        times = body.get("times", "1")
        if times != "inf":
            try:
                times = max(1, int(times))
            except (ValueError, TypeError):
                times = 1
        with lock:
            fault_seq[0] += 1
            fault = {"id": fault_seq[0], "type": ftype,
                     "path": (body.get("path") or "").strip(),
                     "times": times, "fired": 0}
            armed.append(fault)
        return jsonify({"ok": True, "armed": fault})

    @app.route("/_control/disarm/<int:fid>", methods=["POST"])
    def control_disarm(fid):
        with lock:
            before = len(armed)
            armed[:] = [a for a in armed if a["id"] != fid]
            removed = before - len(armed)
        return jsonify({"ok": True, "removed": removed})

    @app.route("/_control/armed")
    def control_armed():
        with lock:
            return jsonify({"armed": [dict(a) for a in armed]})

    @app.route("/_control/stats")
    def control_stats():
        with lock:
            return jsonify(dict(state))

    @app.route("/_control/reset", methods=["POST"])
    def control_reset():
        with lock:
            state.update({"requests": 0, "by_path": {},
                          "flaky_inflight": 0, "flaky_max_inflight": 0,
                          "throttle_429s": 0, "logins_ok": 0,
                          "logins_failed": 0, "faults_fired": 0})
            armed.clear()
        return jsonify({"ok": True})

    @app.route("/_control/sitemap")
    def control_sitemap():
        return jsonify({"routes": sorted(
            str(r) for r in app.url_map.iter_rules())})

    return app


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="BulkDownloader test-fixture site.")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind host (default 127.0.0.1). /_control has no "
                        "access control - avoid 0.0.0.0 on untrusted nets")
    p.add_argument("--port", type=int, default=8899)
    p.add_argument("--throttle-after", type=int, default=3,
                   help="/flaky/ 429s past this many concurrent requests")
    p.add_argument("--slow-delay", type=float, default=2.0,
                   help="seconds /slow/ waits before responding")
    args = p.parse_args(argv)
    app = make_app(throttle_after=args.throttle_after,
                   slow_delay=args.slow_delay)
    print(f"Fixture site on http://{args.host}:{args.port}")
    print(f"Management UI: http://127.0.0.1:{args.port}/_control")
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
