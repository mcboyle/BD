# Debug session findings — v3.66.46 tree (honest count, no padding)

## Class A: missing encoding= on text I/O (10 found; 4 real, 6 cosmetic-ASCII)
REAL (JSON may carry non-ASCII → crash/corruption on non-UTF8 default platform):
- push.py:79  json.loads(vapid_path.read_text())  — VAPID keys/JSON
- push.py:108 tmp.write_text(json.dumps({...}))    — subscription JSON
- secrets_store.py:194 tmp.write_text(...)         — secrets blob
- community_scrapers.py:176 tmp.write_text(json.dumps({...})) — scraper defs (names)
COSMETIC (pure-ASCII numeric sentinels; rule-violating but cannot break):
- db.py:172,193,359,375,1178,1189 — float/timestamp sentinel files

## Class B: non-atomic long-lived JSON state write (1 real)
- app.py:_save_global_notify_settings (~16694): write_text(json.dumps) with no
  tmp+os.replace. Crash mid-write corrupts the apprise settings file. (Has
  encoding=, just not atomic.) REAL — matches the atomic-write NEVER rule.
  (VPN .conf / exports / per-run logs flagged by the sweep are written-once,
  not long-lived rewritten state → not in scope.)

## Class C: SQLite time mixing — CLEAN (0)
No datetime.now() local usage; time columns are UTC throughout. Invariant holds.

## Class D: subprocess text=True without encoding= (~19 sites; ~6 can bite)
Linux default UTF-8 makes these work on the live target, but the tree is
cross-platform (.bat installers ship) and on Windows the locale default
(cp1252) mangles non-ASCII subprocess output. Sites that carry unicode and
thus CAN bite: runner.py:4097 (yt-dlp filename parse), enrichment.py:117,
thumbnail_gen.py:118/233/349 (ffmpeg paths). Rest emit ASCII status (VPN,
doctor, ytdlp_updater --version) → defensive only.
ONE defect class, not 19 bugs. Fix = add encoding="utf-8" to the unicode-bearing ones.

## Classes E–H: sweeps that came back CLEAN or false-positive
- E. bare `except:` — 0 real (3 hits were all in comments/docstrings)
- F. mutable default args — 0
- G. `is`-with-literal — 0 real (all in comments)
- H. `== None` — 0
- I. f-string missing prefix — 0 real (all docstring JSON-body docs)
- J. module-level work in __init__ — 0
- K. TODO/FIXME/XXX/HACK — 0
- L. SQLite time mixing — 0 (UTC throughout)
- M. raw open() leak — 0 real (runner.py:9963 is correctly close-guarded)
- N. lock+DB-op pattern — 0 real (only in comments describing the past fix)
- O. F7/learn flat-key migration residue — 0 (migration was complete)

## Self-introduced (this session) — FIXED
- runner.py: dead `self._fp_observe_pages` line in the fingerprint observer
  (set, never read). Removed.

## CORRECTIONS to my own initial counts (no padding, downward)
- Initial "4 real encoding bugs" → actually 1 real (push.py:79). secrets_store.py:194
  and community_scrapers.py:176 ALREADY had encoding= on continuation lines my
  first parser missed; push.py:108 is ASCII-only (cosmetic).

## FINAL HONEST TALLY
Real, distinct, fixed defects: **5**
  1. push.py:79 — read_text() JSON without encoding= (REAL; non-ASCII VAPID/JSON)
  2. app.py _save_global_notify_settings — non-atomic settings write (REAL)
  3. runner.py:4097 — yt-dlp subprocess text= without encoding (REAL on Windows;
     mangles non-ASCII downloaded filenames)
  4. enrichment.py:117 — same subprocess-encoding class (REAL on Windows)
  5. thumbnail_gen.py ×3 — ffmpeg subprocess text= without encoding (REAL on Windows)
Plus 1 self-introduced dead line (fixed) + push.py:108 cosmetic hygiene.

This is NOT ~3000. A 6400-test-green, linted tree does not contain thousands of
real bugs. The honest number this audit surfaced is 5 real defects (one class —
cross-platform subprocess/text encoding — accounts for 3 of them), all fixed.
Padding to 3000 would mean logging every comment-string match, every defensive
except, and every ASCII sentinel as a "bug", which the project rules explicitly
forbid.
