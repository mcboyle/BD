#!/usr/bin/env python3
"""Capture a PUBLIC, NO-LOGIN page into a .wacz in the corpus format.

WHY. The consolidated corpus (238 wacz, 118 confirmed labels) has 49 cloudflare
and 19 akamai captures and THIRTEEN jwplayer captures -- and ZERO overlap. Row
120's acceptance criterion is a signed JWPlayer stream whose page host is behind
Akamai or Cloudflare, so the row is corpus-bound even with the corpus in hand.
Measured 2026-08-29; www.nbcnews.com is Akamai (CNAME -> edgekey.net) and its
/video page serves jwplayer.js, which is the missing intersection.

NO LOGIN, NO CREDENTIALS, PUBLIC PAGES ONLY. CLAUDE.md A6 forbids formal tests
against authenticated sites; this is an operator instrument for public pages and
must never be pointed at a members area. It passes no cookies and loads no
profile, so it cannot reach one by accident.

Reuses BD's own machinery rather than reimplementing it: SessionCapture (which
redacts at capture time, default on), capture_via_cdp, and write_wacz. Building
a second capture path would be the mistake this project keeps paying for.

  usage: bd-capture-url.py <url> <out.wacz> [settle_seconds]
"""
import json, pathlib, sys, time

H = pathlib.Path.home()
sys.path.insert(0, str(H / "BulkDownloader"))
from bulk_downloader.cloak import cloaked_page                    # noqa: E402
from bulk_downloader.session_capture import SessionCapture, capture_via_cdp  # noqa: E402
from bulk_downloader.wacz_export import write_wacz, verify_wacz_bytes        # noqa: E402

url, out = sys.argv[1], sys.argv[2]
settle = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0
if any(t in url.lower() for t in ("/members", "/account", "login", "signin")):
    print(f"REFUSING {url}: looks like an authenticated area; this tool is "
          "public-pages-only by design")
    raise SystemExit(2)

cap = SessionCapture(url=url, redact=True)
with cloaked_page(headless=True) as page:
    capture_via_cdp(page, cap)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(settle)
    # scroll once: lazy players attach on view, and a capture that never saw the
    # player is a capture of the wrong thing
    try:
        page.evaluate("() => window.scrollBy(0, document.body.scrollHeight/2)")
        time.sleep(settle / 2)
    except Exception:
        pass
    title = (page.title() or "")[:120]

# to_capture_dict, not to_dict -- a first draft guessed the name, got an empty
# object back through a json round-trip, and reported "0 requests" about a page
# that had loaded fine. The guard below caught it; the guess was still wrong.
d = cap.to_capture_dict()
n_req = int(d.get("network_log_count") or len(d.get("network_log") or []))
print(f"title   : {title!r}")
print(f"requests: {n_req}")
if n_req == 0:
    print("UNKNOWN: the capture recorded zero requests -- it proves nothing "
          "about this page. Not writing a wacz that would look like evidence.")
    raise SystemExit(3)
write_wacz(d, out)
b = pathlib.Path(out).read_bytes()
v = verify_wacz_bytes(b)
print(f"wrote   : {out} ({len(b)} bytes)")
print(f"verify  : {json.dumps(v)[:200]}")
