#!/usr/bin/env python3
"""Diagnostic for the v3.62.6 CSRF-bootstrap test group (failures 8-10).

The tests fail on stash but pass in the sandbox. Same source, same
template, opposite result. Three hypotheses remain after reading the
code:

  (A) The bulk_downloader package on stash resolves to a different
      location (e.g. a site-packages install) than the source tree
      the operator just grep'd. The grep checks ~/BulkDownloader/...
      but the import goes to a different copy that lacks the marker.

  (B) The on-disk templates/index.html on stash differs from the
      sandbox in a subtle way (BOM, line endings, marker spelling)
      that breaks the substring replace.

  (C) Something in _load_index_html()'s ?v=... regex post-process
      eats the marker on stash but not the sandbox.

This script prints the runtime state needed to identify which (if
any) of these is real. Run on stash from ~/BulkDownloader:

    BD_DISABLE_KEEPALIVE=1 venv/bin/python tools/diag_csrf_bootstrap.py

Output goes to stdout; nothing is changed.
"""
import os
import sys
import hashlib
from pathlib import Path

# tools/ may be on sys.path[0] when run as `python tools/diag.py`,
# but bulk_downloader lives at the repo root next to tools/. Prepend
# the parent of this file's dir so the import resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("=" * 72)
print("CSRF-bootstrap diagnostic")
print("=" * 72)

print(f"\ncwd                : {os.getcwd()}")
print(f"sys.executable     : {sys.executable}")
print(f"sys.path[:5]       : {sys.path[:5]}")

# 1. Where does the bulk_downloader package actually resolve to?
print("\n--- bulk_downloader package resolution ---")
import bulk_downloader
print(f"bulk_downloader.__file__   : {bulk_downloader.__file__}")
print(f"bulk_downloader.__version__: {bulk_downloader.__version__}")

from bulk_downloader import app as bd_app
print(f"bulk_downloader.app.__file__: {bd_app.__file__}")

# 2. Where does index.html actually get read from?
import re
src = open(bd_app.__file__, encoding="utf-8").read()
m = re.search(r'path = here / "templates" / "index\.html"', src)
print(f"\n_load_index_html reads from <pkg dir>/templates/index.html: "
      f"{'YES' if m else 'NO — code differs!'}")

pkg_dir = Path(bd_app.__file__).parent
template_path = pkg_dir / "templates" / "index.html"
print(f"resolved template path     : {template_path}")
print(f"  exists                   : {template_path.exists()}")
print(f"  is symlink               : {template_path.is_symlink()}")
if template_path.exists():
    raw = template_path.read_bytes()
    print(f"  size bytes               : {len(raw)}")
    print(f"  sha256                   : {hashlib.sha256(raw).hexdigest()}")
    print(f"  has BOM (\\xef\\xbb\\xbf)  : "
          f"{'YES' if raw.startswith(b'\\xef\\xbb\\xbf') else 'no'}")
    text = raw.decode("utf-8", "replace")
    has_marker = "<!--CSRF_META-->" in text
    print(f"  contains '<!--CSRF_META-->': {has_marker}")
    if not has_marker:
        # search for partial matches that might indicate corruption
        for pat in ("CSRF_META", "<!--CSRF", "csrf_meta", "CSRF-META"):
            n = text.count(pat)
            if n:
                print(f"    fuzzy: '{pat}' appears {n} time(s)")
    # show the region around line 15 (where the marker should be)
    lines = text.splitlines()
    print(f"  total lines              : {len(lines)}")
    if len(lines) >= 20:
        print(f"  lines 13-17 (marker area):")
        for i in range(12, 17):
            if i < len(lines):
                ln = lines[i]
                # show as repr to expose hidden chars
                print(f"    L{i+1:>3}: {ln[:120]!r}")

# 3. What does test_client return?
print("\n--- test_client GET / ---")
# minimal init the same way conftest's fresh_app does
os.environ.setdefault("BD_TEST_MODE", "1")
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
from bulk_downloader.db import db_init
db_init()
bd_app.app.config["TESTING"] = True
client = bd_app.app.test_client()
resp = client.get("/")
print(f"status: {resp.status_code}")
print(f"body length: {len(resp.data)}")
body = resp.get_data(as_text=True)
META_PROBE = '<meta name="csrf-token"'
print(f"contains '<meta name=\"csrf-token\"': {META_PROBE in body}")
print(f"contains un-substituted marker   : "
      f"{'<!--CSRF_META-->' in body}")
set_cookies = [v for k, v in resp.headers.items()
               if k.lower() == "set-cookie"]
print(f"Set-Cookie headers                 : {len(set_cookies)}")
for c in set_cookies:
    print(f"  {c[:120]}")

# 4. If the marker IS in the file but NOT in the response, the
#    substitution failed for some reason — show the first 800 chars
#    of the response so we can see what _load_index_html actually
#    produced
if META_PROBE not in body:
    print("\n--- response head (first 800 chars) ---")
    print(body[:800])
    print("\n--- response middle (around where meta should be) ---")
    # the marker is on line 15 of the template; that's roughly here
    head_end = body.find("</head>")
    if head_end > 0:
        print(body[max(0, head_end - 400):head_end + 20])
    else:
        print("(no </head> in response)")

print("\n" + "=" * 72)
print("END diagnostic")
print("=" * 72)
