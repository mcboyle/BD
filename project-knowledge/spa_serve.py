#!/usr/bin/env python3
"""Boot the BulkDownloader Flask app serving frontend/dist on 127.0.0.1:5599
for the SPA render harnesses. Empty instance: BD_HOME / BD_INSTALL_DIR / cwd are
all pointed at a throwaway temp dir so NO runtime DB (downloader_history.db,
video_hashes.db, *-wal/-shm) ever leaks into the work tree (active_footgun[0]).
The dist root resolves package-relative (app.py: Path(__file__).parent.parent /
frontend/dist), so the chdir does not affect SPA serving."""
import os, sys, tempfile

# --- isolate ALL writable state OUTSIDE the work tree (footgun[0]) -----------
_ISO = os.environ.get("BD_SERVE_HOME") or tempfile.mkdtemp(prefix="bd_spa_serve_")
os.environ["BD_HOME"] = _ISO            # cookies/json stores
os.environ["BD_INSTALL_DIR"] = _ISO     # db.py/push.py resolution wins here -> both .db files land in _ISO
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
os.makedirs(_ISO, exist_ok=True)
os.chdir(_ISO)                          # belt+suspenders: any bare-cwd write (screenshots/) stays out of the tree

sys.path.insert(0, "/home/claude/work")
from bulk_downloader.app import app  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

srv = make_server("127.0.0.1", 5599, app, threaded=True)
print(f"SPA backend up on http://127.0.0.1:5599 (dist=frontend/dist, BD_HOME={_ISO})", flush=True)
srv.serve_forever()
