#!/usr/bin/env python3
"""Generate the synthetic capture corpus that lets the capture tests RUN.

WHY THIS EXISTS. 65 tests across 13 files skipped with "captures not present":
they consume capture artifacts through `capture_test_fixtures.capture_fixture_lane()`,
which is disabled unless its capture-root environment variable (named in
`capture_test_fixtures.py`) points at a real corpus. THE LITERAL IS DELIBERATELY
NOT SPELLED HERE: the config-surface scanner matches on the `BD_` prefix across
`tools/`, so writing it would enter this generator's name into the parity ledger
as an unledgered runtime tunable and fail `test_open_parity_debt_is_zero`
(measured: open_runtime_tunable 1 vs a baseline of 0). CLAUDE.md section 4 says
it outright -- if the name is not a real config key, do not prefix it -- and
section 0 generalises it: cite the mechanism, not the literal. A private
capture corpus is integration evidence and is never committed
(`project-knowledge/CAPTURE_SHARING_POLICY.md`: "Synthetic fixtures remain the
only captures that may be committed/circulated"), so those tests never ran
anywhere -- not locally, not in CI.

THE FIXTURE MUST BE ABLE TO REPRESENT THE FAILURE. That is the whole design
constraint and it is the reason this file is not three lines long. A synthetic
capture that contains no signing parameter makes
"no signing value ever reaches an output" pass trivially -- a VACUOUS PASS,
which is strictly worse than the skip it replaced, because a skip is honest
about having proven nothing. CLAUDE.md section 0 is the general form; section 6
records the same shape in a harness ("a fake curl that answered every URL
identically -- so /api/health and / could never disagree, which is the only
condition the check under test exists to detect").

TWO KINDS OF THING LIVE HERE ON PURPOSE.

1. SECURITY HAZARDS -- every hazard a posture/redaction test hunts is present:

    signed query parameters      ?Signature=...&Expires=...&Key-Pair-Id=...
    path-signed segments         /hls/SSS.../poster.jpg  and  t's key=,end=,ip=
    a JWT-shaped token           aaaa.bbbb.cccc
    cookies                      set on the capture and in request headers
    an email-shaped string       in a request body
    multi-capture series         two/three captures of one title, hours apart
    HLS + progressive            so manifest-preference has something to prefer

   EVERY SECRET-SHAPED VALUE IS A ZERO-ENTROPY REPEAT ("AAAA...", "bbbb"), never
   a realistic-looking random string. CLAUDE.md section 7: a document that names
   a secret becomes a place the secret lives, and gitleaks scans the PR's whole
   commit range, so a realistic-looking corpus value fails CI and cannot be fixed
   forward. They are shaped like secrets and contain no entropy -- exactly what a
   detector-under-test needs and what a leak-scanner must ignore.

2. SITE-SPECIFIC RECOGNIZER MARKERS -- each recognizer keys on a real-page shape,
   so each synthetic capture genuinely CONTAINS that shape rather than a value
   planted to satisfy an assertion:

    bros      an m3u8 whose path carries an 11-segment CDN-sharded hash run
              (3-char opaque segments), so `_sharded_run_map` collapses it to ONE
              sharded identity slot. The manifest is the goal so `classify_url`
              buckets it hls_manifest.
    t         path-embedded signing (key=,s=,end=,ip=) plus routing/storage
              scaffolding (state=, reftag=, ssd1, 12) around a single numeric
              content id, so signing is recognized-and-masked and everything but
              the id demotes to a literal.
    nubile    a progressive mp4 at /videos/<slug>/lilsis_<slug>_3840.mp4 whose
              readable title slug is ECHOED in the asset filename (the
              filename_echo signal that recovers the lost identity) and a bare
              _3840 resolution suffix on the filename (the rendition signal).
              Its e=/st= query values are PRESENT and DIFFER across the two
              sessions, so temporal signing drift is measurable.
    filthy    a progressive mp4 at /fame/hls/<uuid>/1080p.mp4 -- a UUID content
              id, `fame`/`hls` structural literals, NO manifest captured (the
              transparent highest-seq-media fallback).
    ultrafilms a progressive mp4 at /videos/<8hex>/<rendition>.mp4 -- an opaque
              8-hex content id, redacted (`<scrubbed>`) CloudFront signing so
              signing drift reads UNDETERMINABLE, rendition varying across the
              same-title series.
    miruro    a real DOM capture (rrweb node tree) of a vidstack-over-hls page:
              dense vds- markup, the vidstack script, plyr-confusable skin
              classes, hls.js, an <a> nav link, NO <media-player> element, and
              vds-player: localStorage keys -- so player recognition promotes
              vidstack over plyr/hls.js on the storage/script tell, and the drift
              provider can read a redacted DOM with a matching+broken selector.
    bang247   an Aylo download API (GET .../v1/video-download/{id}) on a host
              distinct from the content CDN, so the multi-host recognizer
              surfaces a templated api_template.
    wow247    a direct-link (wowgirls) DOM: repeating a.ct_dl_button rows to a
              signed .mp4, NO download API -- so rows are derived and no
              api_template is fabricated.

DO NOT reverse-engineer an assertion and plant whatever makes it green. If a
marker exists only because the test wants it, the test proves this generator and
nothing else. Where a marker cannot be honestly constructed, leave the test
failing.

USAGE
    venv/bin/python tools/make_synthetic_capture_corpus.py --out tests/capture_corpus_synthetic
    venv/bin/python tools/make_synthetic_capture_corpus.py --check   # verify hazards survive
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import zipfile

# --------------------------------------------------------------------------
# Zero-entropy secret-shaped constants. See the module docstring before editing.
# --------------------------------------------------------------------------
SIG = "A" * 40                      # signature-shaped, one repeated char
KEYPAIR = "K" * 14
POLICY = "P" * 32
JWT = "%s.%s.%s" % ("a" * 24, "b" * 24, "c" * 24)
COOKIE_VAL = "C" * 32
PATH_SIGN = "S" * 44                # base64-ish path segment, zero entropy
EMAIL = "user@example.invalid"      # RFC 2606 reserved TLD, not a real address

# Zero-entropy stand-ins for t's path-embedded signing material. The real
# capture's values are deliberately NOT reproduced (they are what
# test_v3_66_85's posture test asserts are ABSENT); these are masked before they
# reach any skeleton field anyway.
T_KEY = "K" * 22
T_EXPIRY = "1700000000"             # unix-ts shaped, not the real expiry
T_IP = "10.0.0.0"                   # RFC 5737-ish placeholder, not the real ip
T_STATE = "T" * 16
T_REFTAG = "R" * 8

# Site content identifiers. These are the per-title IDENTITIES the recognizers
# are meant to surface -- fictional, not secrets, and deliberately shaped like
# each site's real id form (opaque 8-hex / uuid / readable slug / numeric).
ULTRA_ID = "1a2b3c4d"                      # 8-hex opaque content id (title1)
ULTRA_SERIES_ID = "9f8e7d6c"               # the title2/title14 same-identity series
FILTHY_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"   # zero-entropy uuid, 36 chars
NUBILE_SLUG = "stepsis_gives_me_the_best_birthday_ever"
NUBILE_FILE = "lilsis_%s_3840.mp4" % NUBILE_SLUG
T_ID = "493498581"                         # single numeric content id
# CDN path-sharding: 11 contiguous short opaque segments, each carrying a digit
# so each is admitted as an opaque identity and the run collapses to ONE slot.
# NOT copied from any real capture.
BROS_SHARDS = ["3a1", "9b2", "1c8", "7d4", "4e0", "0f8",
               "2a7", "8b3", "6c9", "b5d", "e1f"]

CAPTURE_VERSION = 3


def _signed_url(host: str, path: str) -> str:
    return (f"https://{host}{path}"
            f"?Signature={SIG}&Expires=1700000000&Key-Pair-Id={KEYPAIR}"
            f"&Policy={POLICY}")


def _entry(seq, url, ctype="application/json", status=200, method="GET",
           body=None, req_headers=None, resp_headers=None, etype="xhr"):
    return {
        "seq": seq,
        "iso": "2026-01-01T00:00:%02dZ" % (seq % 60),
        "timestamp": 1767225600 + seq,
        "method": method,
        "url": url,
        "type": etype,
        "duration_ms": 10 + seq,
        "error": None,
        "request_headers": req_headers or {"Cookie": f"sessionid={COOKIE_VAL}"},
        "request_body": body,
        "response_status": status,
        "response_status_text": "OK" if status == 200 else "Error",
        "response_headers": resp_headers or {"content-type": ctype},
        "response_body": None,
        "response_body_truncated": False,
        "response_body_skipped_reason": None,
    }


def _boilerplate(host: str, title: str, seq0: int):
    """The three hazard-bearing requests every media-site capture carries: a
    signed document (Signature), an API call (JWT + email + cookie), and a
    path-signed poster thumbnail (PATH_SIGN). None is media, so none is ever
    selected as the download goal -- the site-specific media entries, added at a
    higher seq, own that."""
    return [
        _entry(seq0, _signed_url(host, "/video/%s" % title),
               ctype="text/html", etype="document"),
        _entry(seq0 + 1, f"https://{host}/api/v1/title/{title}",
               req_headers={"Authorization": f"Bearer {JWT}",
                            "Cookie": f"sessionid={COOKIE_VAL}"},
               body=json.dumps({"login": EMAIL, "title": title})),
        _entry(seq0 + 2, f"https://cdn.{host}/hls/{PATH_SIGN}/poster.jpg",
               ctype="image/jpeg", etype="image"),
    ]


def _capture(host, title, media, *, kind="page_visit", page_path=None,
             seq0=0, dom_log=None, storage_snapshot=None, boilerplate=True,
             cookies=None):
    """Assemble one capture dict. ``media`` is the list of site-specific
    request entries (the recognizer subject); ``boilerplate`` prepends the
    hazard-bearing document/api/poster requests."""
    page_path = page_path or ("/video/%s" % title)
    log = (_boilerplate(host, title, seq0) if boilerplate else []) + list(media)
    return {
        "capture_version": CAPTURE_VERSION,
        "capture_kind": kind,
        "captured_at": "2026-01-01T00:00:00.000000+00:00",
        "session_start": 1767225600,
        "url": f"https://{host}{page_path}",
        "origin": f"https://{host}",
        "host": host,
        "pathname": page_path,
        "search": f"?Signature={SIG}" if boilerplate else "",
        "title": title,
        "user_agent": "Mozilla/5.0 (synthetic corpus)",
        "cookies": cookies if cookies is not None
        else (f"sessionid={COOKIE_VAL}" if boilerplate else ""),
        "network_log": log,
        "network_log_count": len(log),
        "dom_log": dom_log or [],
        "dom_log_count": len(dom_log or []),
        "storage_snapshot": storage_snapshot if storage_snapshot is not None
        else ({"localStorage": {"token": JWT}} if boilerplate else {}),
        "storage_deltas": [],
        "fingerprint_detection": {"checked": True, "signals": []},
    }


# ── site-specific media builders ─────────────────────────────────────────────
def _ultrafilms(title, content_id, rendition, seq0):
    """Progressive mp4, opaque 8-hex content id, CloudFront signing REDACTED
    (`<scrubbed>`) so temporal signing drift reads undeterminable."""
    q = "?Signature=<scrubbed>&Expires=<scrubbed>&Key-Pair-Id=<scrubbed>"
    url = f"https://cdn.ultrafilms.example/videos/{content_id}/{rendition}{q}"
    return _capture("ultrafilms.example", title,
                    [_entry(seq0 + 10, url, ctype="video/mp4", etype="media")],
                    seq0=seq0)


def _filthy(title, seq0):
    """Progressive mp4 with a UUID content id, `fame`/`hls` structural literals,
    query signing PRESENT (Signature). No manifest is captured."""
    url = (f"https://cdn.filthy.example/fame/hls/{FILTHY_UUID}/1080p.mp4"
           f"?Signature={SIG}&Expires=1700000000")
    return _capture("filthy.example", title,
                    [_entry(seq0 + 10, url, ctype="video/mp4", etype="media")],
                    seq0=seq0)


def _nubile(title, expiry, token, seq0):
    """Progressive mp4 at /videos/<slug>/lilsis_<slug>_3840.mp4. The slug is
    echoed in the filename (filename_echo -> identity) and _3840 is a bare
    resolution suffix on the filename (rendition). The e=/st= query values are
    PRESENT and vary per session so signing drift is measurable."""
    url = (f"https://cdn.nubile.example/videos/{NUBILE_SLUG}/{NUBILE_FILE}"
           f"?e={expiry}&st={token}")
    return _capture("nubile.example", title,
                    [_entry(seq0 + 10, url, ctype="video/mp4", etype="media")],
                    seq0=seq0)


def _bros(title, seq0):
    """HLS whose manifest path carries an 11-segment CDN-sharded hash run, so the
    manifest is preferred as the goal and the run collapses to ONE sharded
    identity. Two .ts segments make the segment-candidate count non-zero."""
    shp = "/".join(BROS_SHARDS)
    base = f"https://cdn.bros.example/hls/{shp}/video"
    return _capture("bros.example", title, [
        _entry(seq0 + 10, f"{base}/master.m3u8",
               ctype="application/vnd.apple.mpegurl", etype="fetch"),
        _entry(seq0 + 11, f"{base}/seg-1.ts", ctype="video/mp2t", etype="media"),
        _entry(seq0 + 12, f"{base}/seg-2.ts", ctype="video/mp2t", etype="media"),
    ], seq0=seq0)


def _t_site(title, seq0):
    """HLS manifest whose PATH carries signing (key=,s=,end=,ip=) and routing /
    storage scaffolding (state=, reftag=, ssd1, 12) wrapped around a single
    numeric content id. Signing is recognized+masked; everything but the id
    demotes to a literal."""
    seg_sign = f"key={T_KEY},s=,end={T_EXPIRY},ip={T_IP}"
    path = (f"/hls/{seg_sign}/ssd1/12/state={T_STATE}/reftag={T_REFTAG}"
            f"/{T_ID}/master.m3u8")
    url = f"https://cdn.t.example{path}"
    return _capture("t.example", title,
                    [_entry(seq0 + 10, url,
                            ctype="application/vnd.apple.mpegurl",
                            etype="fetch")],
                    seq0=seq0)


# ── DOM node helpers (rrweb serialized-node form) ────────────────────────────
def _el(tag, attrs=None, kids=None):
    return {"type": 2, "tagName": tag, "attributes": attrs or {},
            "childNodes": kids or []}


def _txt(s):
    return {"type": 3, "textContent": s}


def _full_snapshot(body_children, body_attrs=None):
    body = _el("body", body_attrs or {"class": "app"}, body_children)
    root = {"type": 0, "childNodes": [_el("html", {}, [body])]}
    return [{"type": "full_snapshot", "data": {"node": root}}]


def _vidstack(host):
    """A vidstack-over-hls DOM capture (miruro shape). Dense vds- markup + the
    vidstack script + plyr-confusable skin + hls.js, an <a> nav link, NO
    <media-player> element, and vds-player: localStorage keys. Player
    recognition must promote vidstack over plyr/hls.js on the script/storage
    tell; the drift provider must read this redacted DOM with a matching (`a`)
    and a broken (`button`) selector."""
    vds_divs = [_el("div", {"class": f"vds-c{i} vds-button"}, [_txt("x")])
                for i in range(150)]
    scripts = [
        _el("script", {"src": "https://cdn.example/vidstack-player.js"}),
        _el("script", {"src": "https://cdn.example/plyr.min.js"}),
        _el("script", {"src": "https://cdn.example/hls.min.js"}),
    ]
    player = _el("div",
                 {"class": "plyr plyr__controls plyr__menu", "data-plyr": "x"},
                 vds_divs + scripts)
    nav = _el("a", {"class": "nav-link", "href": f"https://{host}/watch/anime-1"},
              [_txt("Watch")])
    dom_log = _full_snapshot([nav, player])
    # `_net` shape (url/method/resourceType) -- the builder's network_patterns
    # reads HAR/list-style headers, so the dict-header `_entry` shape trips it.
    net = [
        _net("https://cdn.example/vidstack-player.js", rtype="script"),
        _net(f"https://cdn.{host}/hls/anime/master.m3u8", rtype="fetch"),
    ]
    storage = {"local_storage": {"vds-player:display-bg": "1",
                                 "vds-player:font-size": "18"},
               "session_storage": {}}
    # page url is the site ROOT -- the drift provider surfaces cap["url"] as
    # page_url, and the drift-repair CFG starts from https://www.miruro.tv/.
    return _capture(host, "Miruro", net, kind="dom",
                    page_path="/", dom_log=dom_log,
                    storage_snapshot=storage, boilerplate=False, cookies="")


def _net(url, method="GET", rtype="xhr"):
    return {"url": url, "method": method, "resourceType": rtype}


def _aylo_bang():
    """Aylo/Project1Service: a download API (GET .../v1/video-download/{id}) on
    a host DISTINCT from the content CDN, so the multi-host recognizer surfaces a
    templated api_template candidate."""
    dom_log = _full_snapshot(
        [_el("span", {"class": "vjs-download", "title": "Download"},
             [_txt("Download")])],
        body_attrs={"class": "site user_logged"})
    net = [
        _net("https://site-api.project1service.com/v1/videos/12345"),
        _net("https://site-api.project1service.com/v1/video-download/12345"),
        _net("https://project1content.com/m/abc/master.m3u8", rtype="fetch"),
        _net("https://project1content.com/m/abc/seg-1.ts", rtype="media"),
    ]
    cap = _capture("members.bangbros.com", "video", net, kind="dom",
                   page_path="/video/12345/title", dom_log=dom_log,
                   boilerplate=False, cookies="")
    return cap


def _aylo_wow():
    """wowgirls direct-link: repeating a.ct_dl_button rows pointing at signed
    .mp4 files, NO download API -- rows are derived, no api_template fabricated."""
    dom_log = _full_snapshot([
        _el("a", {"class": "ct_dl_button", "data-framerate": "60",
                  "href": "https://content-video2.wowgirls.com/download/abc/"
                          "x1080_60FPS.mp4"}, [_txt("1080p")]),
        _el("a", {"class": "ct_dl_button", "data-framerate": "60",
                  "href": "https://content-video2.wowgirls.com/download/abc/"
                          "x720_60FPS.mp4"}, [_txt("720p")]),
    ], body_attrs={"class": "site user_logged"})
    net = [
        _net("https://content-video2.wowgirls.com/download/abc/x1080_60FPS.mp4",
             rtype="media"),
        _net("https://content-video2.wowgirls.com/download/abc/x720_60FPS.mp4",
             rtype="media"),
        _net("https://cdn.wowgirls.com/m/abc/master.m3u8", rtype="fetch"),
    ]
    return _capture("auth.wowgirls.com", "film", net, kind="dom",
                    page_path="/film/abc", dom_log=dom_log,
                    boilerplate=False, cookies="")


def _write_wacz(dest: pathlib.Path, capture: dict) -> None:
    """A WACZ is a zip: archive/capture.json, pages/pages.jsonl, datapackage.json,
    datapackage-digest.json. Shape taken from a real capture's namelist."""
    body = json.dumps(capture, indent=1, sort_keys=True).encode()
    pages = "\n".join(json.dumps(p) for p in (
        {"format": "json-pages-1.0", "id": "syn", "title": capture["title"]},
        {"id": "syn", "url": capture["url"], "ts": capture["captured_at"]},
    )).encode()
    dp = json.dumps({
        "profile": "data-package",
        "wacz_version": "1.1.1",
        "created": capture["captured_at"],
        "mainPageURL": capture["url"],
        "software": "bd-synthetic-corpus",
        "resources": [{"name": "capture.json", "path": "archive/capture.json",
                       "hash": "sha256:" + hashlib.sha256(body).hexdigest(),
                       "bytes": len(body)}],
    }, indent=1, sort_keys=True).encode()
    digest = json.dumps({
        "path": "datapackage.json",
        "hash": "sha256:" + hashlib.sha256(dp).hexdigest(),
    }, indent=1, sort_keys=True).encode()
    dest.parent.mkdir(parents=True, exist_ok=True)
    # DETERMINISTIC: a fixed member timestamp. `writestr` with a bare name
    # stamps each entry with the CURRENT time, so regenerating an unchanged
    # corpus produced different bytes every run -- measured, the tree hash moved
    # 67dbd82c3cb9b0e9 -> 9717779786e64bc1 with no input change. This corpus is
    # COMMITTED, so that is permanent diff churn and it would defeat any
    # regenerate-and-compare check over it.
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (("archive/capture.json", body),
                           ("pages/pages.jsonl", pages),
                           ("datapackage.json", dp),
                           ("datapackage-digest.json", digest)):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)


def _json_har(items):
    body = json.dumps({"items": items})
    return {"log": {"version": "1.2",
                    "creator": {"name": "bd-synthetic-corpus", "version": "1"},
                    "entries": [{
                        "request": {"method": "GET",
                                    "url": "https://api.example/items",
                                    "headers": []},
                        "response": {"status": 200, "headers": [],
                                     "content": {"mimeType": "application/json",
                                                 "text": body}}}]}}


def _html_har(title, url="https://site.example/page"):
    html = ("<!doctype html><html><head><title>%s</title></head>"
            "<body><h1>%s</h1></body></html>" % (title, title))
    return {"log": {"version": "1.2",
                    "creator": {"name": "bd-synthetic-corpus", "version": "1"},
                    "entries": [{
                        "request": {"method": "GET", "url": url, "headers": []},
                        "response": {"status": 200, "headers": [],
                                     "content": {"mimeType": "text/html",
                                                 "text": html}}}]}}


def _catalog_items():
    return [{"id": i, "title": "Mock Item %03d" % i,
             "media_url": "https://cdn.example/v/%d.mp4" % i} for i in range(10)]


# name -> how to build it. Names measured from the 13 consuming test files.
def _plan():
    ultra_t1 = _ultrafilms("title1", ULTRA_ID, "1080p.mp4", 0)
    return {
        # ultrafilms title1 -- opaque 8-hex identity, progressive, redacted signing
        "capA.json":            ("json", ultra_t1),
        "capture.json":         ("json", _ultrafilms("title1", ULTRA_ID, "1080p.mp4", 0)),
        "cap.wacz":             ("wacz", _ultrafilms("title1", ULTRA_ID, "1080p.mp4", 0)),
        # rendition VARIES across the same-title N3 series (1080 / 720 / 1080)
        "ultrafilms_title1_later.wacz":   ("wacz", _ultrafilms("title1", ULTRA_ID, "720p.mp4", 100)),
        "yultrafilms_title1_later.wacz":  ("wacz", _ultrafilms("title1", ULTRA_ID, "1080p.mp4", 100)),
        # title2/title14 -- one video, two sessions: SAME identity, different rendition
        "ultrafilms_title2.wacz":         ("wacz", _ultrafilms("title2", ULTRA_SERIES_ID, "1080p.mp4", 0)),
        "ultrafilms_title14_later.wacz":  ("wacz", _ultrafilms("title14", ULTRA_SERIES_ID, "720p.mp4", 100)),
        # bros -- sharded HLS manifest (11-segment run collapses to one identity)
        "bros_title1_1.wacz":    ("wacz", _bros("title1", 0)),
        "bros_title1_cap2.wacz": ("wacz", _bros("title1", 50)),
        # filthy -- UUID identity, fame/hls literals, no manifest captured
        "filthy_title1_cap1.wacz": ("wacz", _filthy("title1", 0)),
        "filthy_title1_cap2.wacz": ("wacz", _filthy("title1", 50)),
        # nubile -- readable slug echoed in filename + _3840 rendition; e/st vary
        "nubile_title1_cap1.wacz": ("wacz", _nubile("title1", "1700000000", "T" * 24, 0)),
        "nubile_title1_cap2.wacz": ("wacz", _nubile("title1", "1700000001", "U" * 24, 50)),
        # t -- path-embedded signing + routing/storage scaffolding around one id
        "t_title1_cap2.wacz":    ("wacz", _t_site("title1", 50)),
        # miruro vidstack DOM captures (also written under tests/fixtures/vidstack)
        "miruro.redacted.wacz":  ("wacz", _vidstack("www.miruro.tv")),
        "mirurow.redacted.wacz": ("wacz", _vidstack("www.miruro.tv")),
        # aylo strict-corpus captures
        "wow247_redacted_strict.wacz":  ("wacz", _aylo_wow()),
        "bang247_redacted_strict.wacz": ("wacz", _aylo_bang()),
    }


def build(out: pathlib.Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    made = {}
    for name, (kind, cap) in _plan().items():
        dest = out / name
        if kind == "wacz":
            _write_wacz(dest, cap)
        elif kind == "json":
            dest.write_text(json.dumps(cap, indent=1, sort_keys=True), encoding="utf-8")
        made[name] = dest.stat().st_size

    # SUBDIRECTORY LAYOUTS. Several consumers do not use `lane.path(name)` --
    # they build `lane.root / "tests" / "fixtures" / "vidstack" / name`, or
    # `lane.root / "fixtures" / "<site>" / "canary.har"`. A flat corpus leaves
    # those skipping with the artifact "not present", which reads like a missing
    # file rather than a missing DIRECTORY. Measured from the skip messages.
    vid = out / "tests" / "fixtures" / "vidstack"
    _write_wacz(vid / "miruro.redacted.wacz", _vidstack("www.miruro.tv"))
    _write_wacz(vid / "mirurow.redacted.wacz", _vidstack("www.miruro.tv"))

    # fixturesite2 canary HARs: the JSON sites (api/spa) carry an items[] body
    # the json_path assertions walk; the HTML sites (scene/infinite) carry a
    # <title> the title_selector reads.
    items = _catalog_items()
    site_hars = {
        "fixturesite2_api": _json_har(items),
        "fixturesite2_spa": _json_har(items),
        "fixturesite2_scene": _html_har("Mock Item 000 - Scene",
                                        "https://scene.example/watch/000"),
        "fixturesite2_infinite": _html_har("Catalog - Infinite Scroll",
                                           "https://infinite.example/browse"),
    }
    for site, har in site_hars.items():
        d = out / "fixtures" / site
        d.mkdir(parents=True, exist_ok=True)
        (d / "canary.har").write_text(json.dumps(har, indent=1, sort_keys=True),
                                      encoding="utf-8")

    # Small hand-built companions the tests name directly.
    (out / "datapackage.json").write_text(json.dumps(
        {"profile": "data-package", "wacz_version": "1.1.1",
         "resources": []}, indent=1), encoding="utf-8")
    (out / "health_snapshot.json").write_text(json.dumps(
        {"capture_health": {"ok": True, "pages": 1},
         "redaction_profile": "floor"}, indent=1), encoding="utf-8")
    (out / "corpus_candidate_entry.json").write_text(json.dumps(
        {"site_id": "ultrafilms", "suggested": True, "promoted": False}, indent=1),
        encoding="utf-8")
    (out / "canary.assertions.json").write_text(json.dumps(
        {"expect": {"hls": True, "signed": True}}, indent=1), encoding="utf-8")
    # Deliberately malformed -- a test asserts the loader REFUSES it. If this
    # parsed, that test would pass without exercising its subject.
    (out / "broken.assertions.json").write_text("{ this is not json",
                                                encoding="utf-8")
    return made


def check(out: pathlib.Path) -> int:
    """Assert the hazards SURVIVED into the artifacts.

    Without this the corpus can silently become inert -- every consuming test
    would still pass, over a fixture that can no longer represent any failure.
    """
    hazards = {"signature": SIG, "jwt": JWT, "cookie": COOKIE_VAL,
               "path_sign": PATH_SIGN, "email": EMAIL}
    found = {k: 0 for k in hazards}
    scanned = 0
    for p in sorted(out.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix == ".wacz":
            with zipfile.ZipFile(p) as z:
                text = z.read("archive/capture.json").decode("utf-8", "replace")
        else:
            text = p.read_text(encoding="utf-8", errors="replace")
        scanned += 1
        for k, v in hazards.items():
            if v in text:
                found[k] += 1
    print("scanned %d artifact(s)" % scanned)
    for k, n in sorted(found.items()):
        print("  %-10s present in %d" % (k, n))
    missing = [k for k, n in found.items() if n == 0]
    if scanned == 0:
        print("BD-GATE-UNRUNNABLE: no artifacts scanned -- an empty denominator "
              "reports 'all hazards present' over nothing", file=sys.stderr)
        return 2
    if missing:
        print("HAZARD ABSENT: %s -- every test hunting these would pass "
              "vacuously" % missing, file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tests/capture_corpus_synthetic")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    out = pathlib.Path(a.out)
    if a.check:
        return check(out)
    made = build(out)
    print("wrote %d artifact(s) to %s" % (len(made), out))
    return check(out)


if __name__ == "__main__":
    sys.exit(main())
