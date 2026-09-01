"""Capture what the BROWSER sees on a members page, plus its media links.

WHY THIS EXISTS. On 2026-08-29 BD chose a download link on a nubilefilms scene
page, saved 5,102,802,950 bytes, and recorded it under the requested scene's
title. The file was a DIFFERENT SCENE. Nothing in the history row, the library
title or the candidate ranking said so -- history 121 read `done`, library 103
read 'Nubile Films - Seeing Red - S50:E30', and both were wrong. One screenshot
answered it: below the scene's own six download tiers sat a Related Videos grid
of ~25 scenes, each card exposing its own direct .mp4 links. 159 media links on
one page, SIX of them the requested work.

In the same hour it also CLEARED three rows a filename audit had called
mis-filed: the teenmegaworld page showed performer Adell and studio tag
TeenSexMania, which is exactly what TeenSexMania_Adell_3840x2160.mp4 encodes,
so the files were right and the comparison was wrong.

  usage: bd-shoot.py <site_id> <url> <out.png>

Prints the page title and every media link it can see, then writes a full-page
screenshot. Read-only: it loads cookies, it never queues, downloads or writes to
the app's state.
"""

import json, pathlib, sys, time
H = pathlib.Path.home(); sys.path.insert(0, str(H / "BulkDownloader"))
from bulk_downloader.cloak import cloaked_page
SID, URL, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
jar = json.loads((H / "BulkDownloader" / "cookies" / f"{SID}.json").read_text())
pw = []
for c in jar:
    n, v, d = c.get("name"), c.get("value"), c.get("domain")
    if not (n and d): continue
    e = {"name": n, "value": v or "", "domain": d, "path": c.get("path") or "/",
         "secure": bool(c.get("secure", True)), "httpOnly": bool(c.get("httpOnly", False))}
    x = c.get("expirationDate")
    if isinstance(x, (int, float)) and x > 0: e["expires"] = float(x)
    s = c.get("sameSite"); e["sameSite"] = s if s in ("Strict","Lax","None") else "Lax"
    pw.append(e)
with cloaked_page(headless=True) as page:
    page.context.add_cookies(pw)
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(7)
    print("PAGE TITLE:", page.title()[:110])
    page.screenshot(path=OUT, full_page=True)
    print("shot ->", OUT)
    # DENOMINATOR FIRST.  The original filter matched only `content2a|.mp4`,
    # which is nubilefilms' shape; on ultrafilms it printed 0 for a page BD had
    # just pulled 7.3 GB from, because the tiers hang off `data-signed-url-key`
    # rather than an <a href>.  A filtered zero read as a page fact.  So print
    # the total first, enumerate every download AFFORDANCE (the same attribute
    # set detect.py trusts), and report the media subset as a labelled subset.
    ATTRS = ("href", "data-href", "data-url", "data-src", "data-download",
             "data-signed-url-key", "data-link", "onclick")
    rows = page.evaluate(
        """(attrs) => {
             const out = [];
             for (const e of document.querySelectorAll('*')) {
               for (const a of attrs) {
                 const v = e.getAttribute && e.getAttribute(a);
                 if (v) out.push({tag: e.tagName, attr: a, val: String(v),
                                  txt: (e.innerText || '').trim().slice(0, 70)});
               }
             }
             return out;
           }""", list(ATTRS))
    print(f"ANCHORS ON PAGE: {page.eval_on_selector_all('a[href]', 'e => e.length')}")
    print(f"AFFORDANCES ON PAGE: {len(rows)}")
    import re as _re
    MEDIA = _re.compile(r"content2a|\.mp4|\.m3u8|\.mkv|\d{3,4}x\d{3,4}|signed", _re.I)
    media, seen = [], set()
    for r in rows:
        k = (r["attr"], r["val"])
        if k in seen:
            continue
        seen.add(k)
        if MEDIA.search(r["val"]) or MEDIA.search(r["txt"]):
            media.append(r)
    print(f"MEDIA AFFORDANCES: {len(media)}  (of {len(rows)} total)")
    for r in media[:40]:
        print("   %-4s %-22s %-40s %s" % (r["tag"], r["attr"], r["txt"][:40],
                                          r["val"][:110]))
    if not rows:
        print("UNKNOWN: zero affordances of ANY kind -- the page did not render "
              "or the session is not authenticated.  Do not read this as 'no "
              "downloads on the page'.")
