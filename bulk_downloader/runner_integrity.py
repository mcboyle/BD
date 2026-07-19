"""runner_integrity -- dedup, hash/integrity verify, metadata embed, quality pref

Extracted from runner.py (SiteRunner) @v3.66.399, PHASE 3 runner cut 3.
Mixin: methods reference self.* only; NO __init__. Import block derived by AST
free-name scan of the moved bodies (the seams doc omitted the dedup +
mp4_metadata conditionals). Cycle rule: imports nothing from .runner.
"""
import sys, shutil

from .db import db_log
from .fname import format_duration_for_filename
from .integrity import verify_media_integrity

# dedup soft import (moved verbatim from runner.py; flat sibling). _dedup + _DEDUP_AVAILABLE.
try:
    from . import dedup as _dedup
    _DEDUP_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_integrity] dedup import failed (degraded): {_e}\n")
    _dedup = None
    _DEDUP_AVAILABLE = False

# mp4_metadata soft import (moved verbatim). _mp4_metadata + _MP4_METADATA_AVAILABLE.
try:
    from . import mp4_metadata as _mp4_metadata
    _MP4_METADATA_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_integrity] mp4_metadata import failed (degraded): {_e}\n")
    _mp4_metadata = None
    _MP4_METADATA_AVAILABLE = False


class IntegrityMixin:
    def _dedup_hash_worker(self, file_path: str, source_url: str) -> None:
        """v3.43.72: background worker that pHashes a finished download
        and registers it. Also checks for duplicates immediately and
        emits a `dedup_match` event if any are found. Fail-open.

        Runs on a daemon thread spawned from _update_job. Don't call
        directly from worker thread — this blocks for 10-30s.
        """
        if not _DEDUP_AVAILABLE or _dedup is None:
            return
        try:
            res = _dedup.compute_hash(file_path)
        except Exception as e:
            sys.stderr.write(
                f"[{self.site_id}] dedup compute raised: {e}\n")
            return
        if not res.ok:
            self.log_event(
                "dedup_skip",
                f"hash skipped: {res.error}", url=source_url,
            )
            return
        try:
            reg = _dedup.get_default_registry(
                self.config.get("dedup_db_path", "video_hashes.db")
            )
            reg.add(res, notes=f"queue_done:{self.site_id}")
        except Exception as e:
            sys.stderr.write(
                f"[{self.site_id}] dedup add raised: {e}\n")
            return
        # Look for duplicates
        try:
            distance = int(self.config.get("dedup_distance", 4) or 4)
            distance = max(0, min(32, distance))  # clamp to sensible range
            dups = reg.find_duplicates(
                res.hash_hex, distance=distance,
                exclude_path=file_path,
            )
        except Exception as e:
            sys.stderr.write(
                f"[{self.site_id}] dedup find raised: {e}\n")
            return
        if dups:
            # Top match for the event message
            top = dups[0]
            top_path = top.get("path", "?")
            top_dist = top.get("distance", "?")
            self.log_event(
                "dedup_match",
                f"{len(dups)} match(es); closest: dist={top_dist} "
                f"path={top_path[-60:]}",
                url=source_url,
            )
        else:
            self.log_event(
                "dedup_unique",
                f"hash {res.hash_hex} unique in registry "
                f"({res.elapsed_s:.1f}s)",
                url=source_url,
            )
    def _apply_quality_preference(self, best, qpref):
        """Phase 67 (v3.38.x): explicit quality preference order. `qpref` is
        a comma-separated list of resolutions to prefer in order (e.g.
        '1080,720,best'). Special value 'best' means take whatever scored
        highest. Returns the candidate that wins, or the original `best`
        unchanged when no preference matches.

        Extracted from _process_one in v3.43.18. Behavior identical.

        v3.43.65: proportional tolerance. Previously a flat ±50 was used
        across all tiers, which was too tight at the top (off-by-mod-16
        encodings like 4080 or DCI-4K 4096 missed 4320 ± 50) and too
        loose at the bottom (a 540p preview slipped past 480 + 50). Now
        `max(50, target * 0.05)` so 8K gets ±216, 4K gets ±108, 1440
        gets ±72, 1080p stays at ±54, 480p stays at ±50.
        """
        preferences = [p.strip() for p in qpref.split(",") if p.strip()]
        candidates = best.get("_all_candidates", [])
        chosen = None
        for pref in preferences:
            if pref.lower() == "best":
                if candidates:
                    chosen = max(candidates, key=lambda c: c.get("score", 0))
                    break
            else:
                try:
                    target_height = int(pref.rstrip('p'))
                except ValueError:
                    continue
                # v3.43.65: scale tolerance with target height. Lower
                # bound is 50 so legacy behavior at <=1000p stays the
                # same; high tiers get the slack they need for non-
                # standard encodings.
                tol = max(50, int(target_height * 0.05))
                matches = [c for c in candidates
                           if abs(c.get("score", 0) - target_height) <= tol]
                if matches:
                    chosen = max(matches, key=lambda c: c.get("score", 0))
                    break
        if chosen and chosen.get("locator"):
            # v3.65.2: preserve _all_candidates (and other metadata the
            # caller may have set on the original `best`, like
            # _via_learned / expected_hash_*) on the swapped-in chosen
            # dict. Without this, downstream consumers that read
            # best.get("_all_candidates") for diagnostics see an empty
            # list whenever the preference actually swapped the winner.
            for k in ("_all_candidates", "_via_learned", "_learned_sel",
                      "expected_hash_algo", "expected_hash_value",
                      "_honeypot_score", "_honeypot_reason"):  # P5-2b
                if k in best and k not in chosen:
                    chosen[k] = best[k]
            return chosen
        return best
    def _dedup_preflight(self, url, job):
        """F1.5: pre-download history-match dedup. Returns a message string
        if this URL should be skipped as a duplicate (status
        skipped_duplicate), else None.

        - Exact-URL match is default-ON (config dedup_exact_url): the exact
          URL already has a 'done' row in history.
        - Fuzzy filename+size match is opt-in (config dedup_fuzzy, default
          off), reusing db_find_filename_duplicate.
        - An explicit force_download (Approve) bypasses dedup entirely — a
          deliberate re-download must never be skipped.
        - Fail-soft: any error -> None (the download proceeds; dedup never
          blocks a legitimate download). HEAD content-length probe is a
          future opt-in (intentionally not wired this cut).
        """
        if job.get("force_download"):
            return None
        try:
            if self.config.get("dedup_exact_url", True):
                from .db import db_find_url_in_history
                hit = db_find_url_in_history(url)
                if hit:
                    return (f"Duplicate of history #{hit['id']} "
                            f"({hit.get('filename') or 'prior download'}"
                            f"{', ' + hit['ts'] if hit.get('ts') else ''})")
            if self.config.get("dedup_fuzzy", False):
                fn = job.get("filename") or ""
                if fn:
                    from .db import db_find_filename_duplicate
                    dup = db_find_filename_duplicate(
                        fn, file_size=job.get("file_size") or None)
                    if dup:
                        return (f"Likely duplicate of history #{dup['id']} "
                                f"({dup.get('filename')}"
                                f"{', ' + dup['ts'] if dup.get('ts') else ''})")
        except Exception as e:
            self.log.warning("dedup preflight failed (proceeding): %s", e)
        return None
    def _verify_hash_or_quarantine(self, page_url, expected_algo, expected_hash,
                                   final_path, filename, downloaded_size):
        """Verify the downloaded file's hash matches `expected_algo:expected_hash`.

        Returns True if the file passes (caller continues), False if mismatched
        (caller should `return` — file has been quarantined to _failed/, job
        marked failed, db_log written).

        Extracted from _do_download in v3.43.17 to reduce that function's
        complexity. Behavior is identical to the previous inline block."""
        try:
            import hashlib as _hl
            h = _hl.new(expected_algo)
            with open(final_path, "rb") as f:
                while True:
                    buf = f.read(8 * 1024 * 1024)  # 8 MB chunks
                    if not buf: break
                    h.update(buf)
            actual = h.hexdigest().lower()
            if actual != expected_hash.lower():
                quarantine = final_path.parent / "_failed"
                quarantine.mkdir(exist_ok=True)
                try: shutil.move(str(final_path), str(quarantine/final_path.name))
                except Exception: pass
                msg = f"Hash mismatch: {expected_algo} expected {expected_hash[:12]}…, got {actual[:12]}…; moved to _failed/"
                self._update_job(page_url, "failed", msg,
                                 filename=filename, file_size=downloaded_size)
                db_log(self.site_id, self.config.get("name","?"), page_url,
                       "failed", filename, downloaded_size, f"hash mismatch ({expected_algo})")
                return False
            self.log_event("hash", f"verified {expected_algo} ✓", url=page_url)
            return True
        except Exception as e:
            # Hash extension we don't support, or I/O error — log but
            # don't fail the download. The integrity check still runs.
            sys.stderr.write(f"  hash verify error (non-fatal): {str(e)[:80]}\n")
            return True  # let the download stand; integrity check is the backstop
    def _verify_integrity_or_quarantine(self, page_url, final_path,
                                        filename, downloaded_size):
        """Verify the downloaded media file passes ffprobe.

        Returns (ok, retry, reason):
          - ok=True, retry=False  → continue (file passed)
          - ok=False, retry=True  → caller returns; job re-queued for fresh retry
          - ok=False, retry=False → caller returns; file quarantined

        Extracted from _do_download in v3.43.17. Behavior identical to the
        previous inline block, including the Phase 72 retry-on-corruption
        path."""
        ok, reason = verify_media_integrity(final_path)
        if ok:
            return True, False, ""
        # Phase 72 (v3.41.0): smart-retry on corruption. Before
        # quarantining and giving up, optionally retry the
        # download from scratch ONCE — corruption is often a
        # transport hiccup, not a fundamental problem. Tracked
        # via a per-job counter so we don't loop forever.
        corruption_retries = 0
        with self._lock:
            j = self.jobs.get(page_url, {})
            corruption_retries = int(j.get("corruption_retries", 0) or 0)
        if (self.config.get("retry_on_corruption", False)
                and corruption_retries < 1):
            try:
                final_path.unlink(missing_ok=True)
            except Exception: pass
            # Also blow away any .part + .meta sidecars
            try:
                tmp_path = final_path.with_suffix(final_path.suffix + ".part")
                tmp_path.unlink(missing_ok=True)
                meta = tmp_path.with_suffix(tmp_path.suffix + ".meta")
                meta.unlink(missing_ok=True)
            except Exception: pass
            with self._lock:
                if page_url in self.jobs:
                    self.jobs[page_url]["corruption_retries"] = corruption_retries + 1
            self._update_job(page_url, "pending",
                f"Integrity failed ({reason}); retrying from scratch",
                retries=0, retry_after=0)
            self.log_event("corruption_retry",
                f"Retrying after integrity failure: {reason}", url=page_url)
            return False, True, reason
        # Either retry_on_corruption disabled or we already retried once
        quarantine = final_path.parent / "_failed"
        quarantine.mkdir(exist_ok=True)
        try: shutil.move(str(final_path), str(quarantine/final_path.name))
        except Exception: pass
        self._update_job(page_url, "failed",
                         f"Saved but failed integrity check ({reason}); moved to _failed/",
                         filename=filename, file_size=downloaded_size)
        db_log(self.site_id, self.config.get("name","?"), page_url, "failed",
               filename, downloaded_size, f"integrity: {reason}")
        return False, False, reason
    def _embed_metadata_if_mp4(
        self,
        path,
        *,
        title="", performer="", site_name="", upload_date="",
        source_url="", thumbnail_url="", quality="", duration_sec=0,
        extractor_name="",
    ) -> bool:
        """v3.43.64: post-download hook. If the file at `path` is an MP4
        AND the site has `embed_metadata` enabled (default ON), write
        title/performer/album/date/comment/cover atoms via mutagen.

        Returns True if a tag write happened, False otherwise (file
        wasn't MP4, mutagen missing, site opted out, or write failed).
        Never raises — the download itself succeeded; tagging is best-
        effort and not allowed to break job state.

        Most kwargs are optional. The minimum useful set is title and
        source_url; with just those we can still write \\xa9nam +
        \\xa9cmt + \\xa9too and produce a library-server-friendly file.
        """
        # Feature-gate first — cheap config read before any work
        if not self.config.get("embed_metadata", True):
            return False
        if not _MP4_METADATA_AVAILABLE or _mp4_metadata is None:
            return False
        if not _mp4_metadata.is_available():
            return False
        if not _mp4_metadata.is_mp4_path(path):
            return False
        # Build the context
        try:
            # v3.43.64 version is hardcoded here — the encoder atom value
            # is informational, not load-bearing, so a one-line literal
            # is fine. Bump in lockstep with preflight.py.
            encoder = "BulkDownloader v3.43.64"
            ctx = _mp4_metadata.MetadataContext(
                title=(title or "")[:255],
                artist=(performer or "")[:255],
                album=(site_name or self.config.get("name", ""))[:255],
                date=_mp4_metadata._normalize_date(upload_date),
                comment=(source_url or "")[:1024],
                encoder=encoder,
                genre=(self.config.get("metadata_genre") or "")[:64],
                description=(self.config.get("metadata_description") or "")[:1024],
                cover_url=(thumbnail_url or ""),
            )
            # Stash extractor + quality + duration in extras so the user
            # has them queryable from any MP4 inspector (ffprobe, mp4info)
            # without needing custom atoms. \xa9wrt (composer) holds
            # extractor name as an audit trail; \xa9grp groups by quality.
            if extractor_name:
                ctx.extras["\xa9wrt"] = f"extracted:{extractor_name}"
            if quality:
                ctx.extras["\xa9grp"] = f"quality:{quality}"
            if duration_sec:
                # Duration is a derived field; embed as a description
                # extension rather than overwriting the description.
                hms = format_duration_for_filename(duration_sec)
                if hms:
                    ctx.extras["\xa9des"] = hms
        except Exception as e:
            sys.stderr.write(f"  metadata: context build failed: {e}\n")
            return False
        # Cover fetch (optional, gated by config)
        cover_bytes = None
        if (self.config.get("embed_cover_art", True)
                and thumbnail_url):
            try:
                ua = self.config.get("user_agent", "") or (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
                cover_bytes = _mp4_metadata.fetch_cover(
                    thumbnail_url,
                    timeout=float(self.config.get("metadata_cover_timeout_s", 10.0) or 10.0),
                    referer=source_url,
                    user_agent=ua,
                )
            except Exception as e:
                # fetch_cover already returns None on failure, but a
                # surprise (e.g. bad URL parse) shouldn't fail the tag.
                sys.stderr.write(f"  metadata: cover fetch raised {e}\n")
                cover_bytes = None
        # Write the tags
        try:
            ok = _mp4_metadata.tag_mp4(path, ctx, cover_bytes=cover_bytes)
        except Exception as e:
            sys.stderr.write(f"  metadata: tag_mp4 raised {e}\n")
            return False
        if ok:
            try:
                # Emit a one-line event for the per-site log; users find
                # this helpful when troubleshooting "did my Plex scan
                # pick up the metadata?". Same kind family as the
                # library_extract_failed / library_hls_failed events.
                cover_note = " +cover" if cover_bytes else ""
                self.log_event(
                    "metadata_tagged",
                    f"MP4 metadata written{cover_note}: title='{ctx.title[:60]}' "
                    f"performer='{ctx.artist[:40]}'",
                    url=source_url,
                )
            except Exception:
                pass
        return ok
