"""runner_integrations -- stash/plex/jellyfin/qbittorrent/jdownloader

Extracted from runner.py (SiteRunner). Mixin: methods reference self.* only;
NO __init__. Methods reference self.* only; the integration-bridge modules
(stash_deep/plex_deep/jellyfin_deep/qb_bridge/jd_bridge), db_log, library,
load_cookies_from_file and Path-as-_Path are imported INLINE inside the methods
that use them (flat sibling of runner.py -> `.` = bulk_downloader, unchanged), so
those imports moved verbatim and still resolve. The only module-level name used as
a global (not inline-imported) is `time`. No kernel (runner_util) import is needed;
NEVER `from .runner import X` (would cycle).
"""
import time

from .website_title import history_title_kwargs


class IntegrationsMixin:
    def _get_stash_client(self):
        """Lazy Stash client constructor. Returns None when Stash
        isn't configured for this site OR when the import fails."""
        try:
            from . import stash_deep
        except Exception:
            return None
        client = stash_deep.get_client_for_site(self.config)
        return client if client.configured else None
    def _stash_dedup_check(self, url: str) -> bool:
        """Check if `url` is already in Stash's library. If so, mark
        the job as done with a clear message and return True (caller
        should `return` immediately).

        Returns False when:
          - Site doesn't have deep Stash enabled
          - The dedup check feature is off for this site
          - Stash is unreachable (fail open — better to download than
            block on a network error)
          - URL not in Stash

        This is a HOT PATH — runs before every URL. Keeps overhead
        bounded: ~1 GraphQL call (~200ms on LAN), short-circuits
        immediately when the site doesn't opt in.
        """
        try:
            from . import stash_deep
        except Exception:
            return False
        cfg = self.config
        if not stash_deep.deep_enabled(cfg):
            return False
        if not cfg.get("stash_dedup_check"):
            return False
        client = self._get_stash_client()
        if client is None:
            return False
        try:
            existing = client.find_scene_by_url(url)
        except Exception as e:
            # Fail open — log and continue. We'd rather redownload
            # than have a Stash outage block the entire queue.
            self.log_event("stash_dedup",
                f"Dedup check failed (continuing with download): "
                f"{type(e).__name__}", url=url)
            return False
        if not existing:
            return False
        # Match found — skip the download.
        title = existing.get("title") or "(no title)"
        scene_id = existing.get("id") or "?"
        files = existing.get("files") or []
        path = files[0].get("path", "") if files else ""
        self.log_event("stash_dedup",
            f"Already in Stash (scene {scene_id}: '{title}') — skipping",
            url=url,
            extra={"stash_scene_id": scene_id, "stash_path": path})
        self._update_job(url, "done",
            f"Already in Stash: {title}",
            filename=path)
        # Persist to DB so the history shows this completion
        try:
            from .db import db_log
            db_log(self.site_id, self.config.get("name", "?"),
                   url, "done", path, 0,
                   f"Skipped (in Stash as scene {scene_id})",
                   bytes_fetched=0,
                   **history_title_kwargs(self, url))  # no bytes fetched
        except Exception:
            pass
        return True
    def _stash_scrape_preview(self, url: str) -> dict | None:
        """Pre-download metadata preview. Surfaces what Stash would
        extract from the URL without committing the bandwidth to
        download. Optional feature; only called by the per-URL
        preview endpoint, not the runner's main loop."""
        try:
            from . import stash_deep
        except Exception:
            return None
        cfg = self.config
        if not stash_deep.deep_enabled(cfg):
            return None
        if not cfg.get("stash_scrape_preview"):
            return None
        client = self._get_stash_client()
        if client is None:
            return None
        try:
            return client.scrape_url(url)
        except Exception:
            return None
    def _stash_enrich_after_scan(self, url: str, file_path: str,
                                   tags_to_add: list[str] | None = None):
        """Post-download enrichment. Called after the Stash scan
        completes. Finds the freshly-imported scene, pushes the site's
        configured studio + any URL-pattern-derived tags onto it.

        Best-effort: failures are logged but never re-raise. The
        download already succeeded; this is gravy."""
        try:
            from . import stash_deep
        except Exception:
            return
        cfg = self.config
        if not stash_deep.deep_enabled(cfg):
            return
        client = self._get_stash_client()
        if client is None:
            return
        # The scan is async on Stash's side. Poll for the scene to
        # appear; give up after ~10s.
        scene = None
        for _ in range(20):
            try:
                scene = client.find_scene_by_path(file_path)
                if scene:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not scene:
            self.log_event("stash_enrich",
                "Scan complete but scene not found in Stash within 10s",
                url=url, extra={"path": file_path})
            return
        scene_id = scene.get("id")
        # Resolve studio. Priority: configured stash_studio_id; else
        # auto-create from the site name (when stash_auto_studio is on).
        studio_id = cfg.get("stash_studio_id") or None
        if not studio_id and cfg.get("stash_auto_studio"):
            site_name = cfg.get("name") or self.site_id
            try:
                s = client.find_studio_by_name(site_name)
                if not s:
                    s = client.create_studio(site_name,
                                              cfg.get("login_url", ""))
                if s:
                    studio_id = s.get("id")
            except Exception:
                pass
        # Resolve tag IDs from names. Tags come from the site's
        # `stash_tags` (configurable per-site) plus any tags_to_add
        # the caller computed from URL patterns.
        all_tag_names = []
        configured_tags = (cfg.get("stash_tags") or "").strip()
        if configured_tags:
            all_tag_names.extend(
                t.strip() for t in configured_tags.split(",") if t.strip())
        if tags_to_add:
            all_tag_names.extend(tags_to_add)
        # Dedup, preserve order
        seen = set()
        unique_tags = []
        for t in all_tag_names:
            if t not in seen:
                seen.add(t)
                unique_tags.append(t)
        tag_ids = []
        if unique_tags:
            try:
                tag_ids = client.find_or_create_tags(unique_tags)
            except Exception:
                pass
        # Push the update. Skip the call if nothing to do.
        if not studio_id and not tag_ids:
            self.log_event("stash_enrich",
                "Scene found but no metadata to push (no studio_id, "
                "no tags)",
                url=url, extra={"scene_id": scene_id})
            return
        try:
            updated = client.update_scene(
                scene_id,
                studio_id=studio_id,
                tag_ids=tag_ids if tag_ids else None,
                url=url,  # always set the URL so dedup works next time
            )
            if updated:
                parts = []
                if studio_id: parts.append(f"studio={studio_id}")
                if tag_ids: parts.append(f"{len(tag_ids)} tag(s)")
                self.log_event("stash_enrich",
                    f"Enriched scene {scene_id}: {', '.join(parts)}",
                    url=url, extra={"scene_id": scene_id})
        except Exception as e:
            self.log_event("stash_enrich",
                f"Update failed: {type(e).__name__}",
                url=url, extra={"scene_id": scene_id})
    def _get_plex_client(self):
        """Lazy Plex client constructor. Returns None when Plex isn't
        configured for this site OR the module import fails."""
        try:
            from . import plex_deep
        except Exception:
            return None
        client = plex_deep.get_client_for_site(self.config)
        return client if client.configured else None
    def _plex_enrich_after_scan(self, url: str, file_path: str):
        """Post-download Plex enrichment. Runs in a background thread
        after the basic plex_refresh fires.

        Workflow:
          1. Path-scoped refresh (faster than the v3.20 full refresh)
          2. Poll for the new file to appear in Plex (~30s timeout)
          3. If unmatched, log a warning so the user can see it
          4. Bump addedAt if recently-added-boost is enabled
          5. Add to per-site collection if configured

        Best-effort: every step's failure is logged but never re-raises.
        """
        try:
            from . import plex_deep
        except Exception:
            return
        cfg = self.config
        if not plex_deep.deep_enabled(cfg):
            return
        client = self._get_plex_client()
        if client is None:
            return
        section_id = (cfg.get("plex_section_id") or "").strip()
        if not section_id:
            self.log_event("plex_enrich",
                "Skipping: plex_section_id not set for this site",
                url=url)
            return

        # Step 1: path-scoped refresh. The v3.20 basic refresh fired
        # earlier in fire_event() may have done the whole library;
        # this targeted one is faster and idempotent.
        ok = client.refresh_path(section_id, file_path)
        if not ok:
            self.log_event("plex_enrich",
                "Path-scoped refresh failed (continuing anyway)",
                url=url)

        # Step 2: poll for the new item. Plex's match pipeline takes
        # a few seconds; we give it 30s before giving up.
        item = client.find_by_path(section_id, file_path)
        if not item:
            self.log_event("plex_enrich",
                "Refresh complete but file not found in Plex within 30s",
                url=url, extra={"path": file_path})
            return

        rating_key = item.get("ratingKey")
        if not rating_key:
            return

        # Step 3: match-confirmation warning
        if cfg.get("plex_match_confirm") and item.get("unmatched"):
            self.log_event("plex_enrich",
                f"Plex imported the file but didn't match it to "
                f"metadata (title='{item.get('title')}'). Fix in "
                f"Plex Web UI.",
                url=url, extra={"rating_key": rating_key,
                                 "title": item.get("title")})

        # Step 4: recently-added boost
        if cfg.get("plex_recently_added_boost"):
            try:
                if client.update_added_at(rating_key, section_id):
                    self.log_event("plex_enrich",
                        f"Bumped addedAt for {rating_key} "
                        f"({item.get('title', '?')[:40]}) so it "
                        f"appears in Recently Added",
                        url=url)
            except Exception:
                pass

        # Step 5: collection routing
        collection_name = (cfg.get("plex_collection") or "").strip()
        if collection_name:
            try:
                existing = client.find_collection_by_title(
                    section_id, collection_name)
                if existing:
                    collection_key = existing["ratingKey"]
                elif cfg.get("plex_auto_create_collection"):
                    created = client.create_collection(
                        section_id, collection_name, rating_key)
                    collection_key = (created or {}).get("ratingKey")
                else:
                    collection_key = None
                    self.log_event("plex_enrich",
                        f"Collection '{collection_name}' doesn't exist "
                        f"and auto-create is off; skipping",
                        url=url)
                if collection_key:
                    if client.add_to_collection(collection_key,
                                                  section_id,
                                                  rating_key):
                        self.log_event("plex_enrich",
                            f"Added to Plex collection '{collection_name}'",
                            url=url, extra={"collection": collection_name})
            except Exception as e:
                self.log_event("plex_enrich",
                    f"Collection routing failed: {type(e).__name__}",
                    url=url)
    def _get_jellyfin_client(self):
        """Lazy Jellyfin client constructor. None when not configured."""
        try:
            from . import jellyfin_deep
        except Exception:
            return None
        client = jellyfin_deep.get_client_for_site(self.config)
        return client if client.configured else None
    def _jellyfin_enrich_after_scan(self, url: str, file_path: str):
        """Post-download Jellyfin enrichment. Background thread after
        basic jellyfin_refresh fires.

        Workflow:
          1. Refresh library (so the new file is picked up)
          2. Poll for the new item by path (~30s timeout)
          3. Per-item refresh to ensure metadata is populated
          4. Match-confirmation warning if year/match info is empty
          5. Add to per-site collection if configured

        Fail-open: every step's failure logs and continues.
        """
        try:
            from . import jellyfin_deep
        except Exception:
            return
        cfg = self.config
        if not jellyfin_deep.deep_enabled(cfg):
            return
        client = self._get_jellyfin_client()
        if client is None:
            return
        user_id = (cfg.get("jellyfin_user_id") or "").strip()
        if not user_id:
            self.log_event("jellyfin_enrich",
                "Skipping: jellyfin_user_id not set for this site",
                url=url)
            return

        # Step 1: library refresh (idempotent, hooked already by basic
        # path; running again is cheap and ensures we catch sites that
        # skipped the basic hook)
        client.refresh_library()

        # Step 2: poll for the item
        item = client.find_item_by_path(user_id, file_path)
        if not item:
            self.log_event("jellyfin_enrich",
                "Refresh complete but file not found in Jellyfin "
                "within 30s",
                url=url, extra={"path": file_path})
            return

        item_id = item.get("id")
        if not item_id:
            return

        # Step 3: per-item refresh to fully populate metadata
        client.refresh_item(item_id)

        # Step 4: match confirmation
        if cfg.get("jellyfin_match_confirm") and item.get("unmatched"):
            self.log_event("jellyfin_enrich",
                f"Jellyfin imported the file but didn't match it to "
                f"metadata (name='{item.get('name')}'). Fix in "
                f"Jellyfin's identify dialog.",
                url=url, extra={"item_id": item_id,
                                 "name": item.get("name")})

        # Step 5: collection routing
        collection_name = (cfg.get("jellyfin_collection") or "").strip()
        if collection_name:
            try:
                existing = client.find_collection_by_name(
                    user_id, collection_name)
                if existing:
                    coll_id = existing["id"]
                elif cfg.get("jellyfin_auto_create_collection"):
                    created = client.create_collection(
                        collection_name, item_id)
                    coll_id = (created or {}).get("id")
                else:
                    coll_id = None
                    self.log_event("jellyfin_enrich",
                        f"Collection '{collection_name}' doesn't "
                        f"exist and auto-create is off; skipping",
                        url=url)
                if coll_id:
                    if client.add_to_collection(coll_id, item_id):
                        self.log_event("jellyfin_enrich",
                            f"Added to Jellyfin collection "
                            f"'{collection_name}'",
                            url=url,
                            extra={"collection": collection_name})
            except Exception as e:
                self.log_event("jellyfin_enrich",
                    f"Collection routing failed: {type(e).__name__}",
                    url=url)
    def _get_qb_client(self):
        """Lazy qB client constructor. Returns None when httpx isn't
        installed. The runner treats None as 'qB unavailable, fall
        through to teach'."""
        try:
            from . import qb_bridge
        except Exception:
            return None
        if self._qb_client is None:
            self._qb_client = qb_bridge.get_client_for_site(self.config)
        return self._qb_client
    def _record_qb_outcome(self, succeeded: bool):
        """Append a qB outcome to the rolling window. The ratio drives
        the insight-strip warning when qB is consistently failing."""
        self._qb_recent_outcomes.append(bool(succeeded))
    def qb_health(self) -> dict:
        """Return current qB bridge health for the site. Surfaces in
        /api/status. Empty/disabled when the site doesn't use qB at
        all (saves UI render cost)."""
        backend = (self.config.get("backend") or "teach").lower()
        # Enabled if the site explicitly uses qb backend, OR if any
        # URL in the queue is a torrent (auto-routes to qB).
        explicit = (backend == "qbittorrent")
        if not explicit:
            return {"backend": backend, "enabled": False}
        outcomes = list(self._qb_recent_outcomes)
        ok = sum(1 for x in outcomes if x)
        n = len(outcomes)
        rate = (ok / n) if n else None
        warn = (n >= 5 and rate is not None and rate < 0.5)
        return {
            "backend": "qbittorrent",
            "enabled": True,
            "samples": n,
            "success_rate": rate,
            "warn_broken": warn,
            "host": self.config.get("qb_host") or "127.0.0.1",
            "port": int(self.config.get("qb_port") or 8080),
        }
    def _try_qb_download(self, url: str, dl_dir: str) -> tuple[bool, str]:
        """Attempt a qB-backed download. Returns (succeeded, reason).
        Same contract as _try_jd_download: success means file is at
        dl_dir and job state is updated; failure means caller falls
        through to the next backend.

        Retry budget: 1 cookie/auth refresh + qB resubmit on auth
        failure. Anything else → return False, let teach pick up.
        """
        try:
            from . import qb_bridge
        except Exception as e:
            return False, f"qb_bridge import failed: {e}"

        client = self._get_qb_client()
        if client is None:
            return False, "qB client unavailable"

        if not client.is_reachable():
            self.log_event("qb_unreachable",
                f"qB not reachable at {client.host}:{client.port}; "
                f"using fallback", url=url)
            return False, "qB unreachable"

        # Submit. Single retry on auth (qB sometimes expires the SID
        # cookie mid-day; a re-login fixes it).
        torrent_hash = None
        last_error = ""
        for attempt in range(2):
            try:
                torrent_hash = client.submit(url, dest_dir=dl_dir)
                break
            except qb_bridge.QBError as e:
                last_error = f"{e.kind}: {e.message}"
                if e.kind == "auth" and attempt == 0:
                    self.log_event("qb_auth_refresh",
                        "qB returned auth error — re-logging in and "
                        "retrying", url=url)
                    # Force re-auth on next submit by clearing the flag
                    if client._client is not None:
                        client._logged_in = False
                    continue
                self.log_event("qb_fallback",
                    f"qB submit failed ({last_error}); falling back",
                    url=url)
                return False, last_error

        if not torrent_hash:
            return False, last_error or "no torrent_hash"

        # Poll. Torrent downloads can be slow (DHT bootstrapping,
        # peer discovery), so the timeout is more generous than JD's:
        # 4 hours. Stuck detection: no progress in 15 min → cancel and
        # fall back. Torrents are different from HTTP — even healthy
        # ones can stall briefly waiting for peers — so the stuck
        # threshold is shorter than the actual file timeout.
        deadline = time.time() + 4 * 3600
        last_seen_bytes = 0
        last_progress_at = time.time()
        while time.time() < deadline:
            if self._stop.is_set():
                client.cancel(torrent_hash, delete_files=True)
                return False, "stopped"
            try:
                st = client.poll(torrent_hash, timeout=10)
            except qb_bridge.QBError as e:
                self.log_event("qb_fallback",
                    f"qB poll failed ({e.kind}: {e.message}); "
                    f"falling back", url=url)
                client.cancel(torrent_hash, delete_files=True)
                return False, f"poll: {e.message}"

            if st["status"] == "running":
                if st["bytes_done"] > last_seen_bytes:
                    last_seen_bytes = st["bytes_done"]
                    last_progress_at = time.time()
                    pct = ((st["bytes_done"] / st["bytes_total"]) * 100
                            if st["bytes_total"] else 0)
                    speed_mb = st["speed"] / 1_000_000 if st["speed"] else 0
                    eta = ""
                    if st["speed"] > 0 and st["bytes_total"]:
                        eta_s = (st["bytes_total"] - st["bytes_done"]) / st["speed"]
                        if eta_s < 60:
                            eta = f", ETA {int(eta_s)}s"
                        elif eta_s < 3600:
                            eta = f", ETA {int(eta_s/60)}m"
                        else:
                            eta = f", ETA {int(eta_s/3600)}h"
                    msg = (f"qB: {pct:.1f}% "
                           f"({st['bytes_done']//1024//1024} / "
                           f"{st['bytes_total']//1024//1024} MB"
                           + (f", {speed_mb:.1f} MB/s" if speed_mb else "")
                           + eta + ")")
                    self._update_job(url, "running", msg,
                        filename=st["filename"] or "",
                        file_size=st["bytes_done"])
                elif time.time() - last_progress_at > 900:
                    # 15 min no progress — torrent is stuck (no peers,
                    # tracker dead, magnet not bootstrapping). Cancel
                    # and fall back.
                    self.log_event("qb_fallback",
                        "qB download stuck (no progress in 15 min); "
                        "falling back", url=url)
                    client.cancel(torrent_hash, delete_files=True)
                    return False, "stuck"
            elif st["status"] == "done":
                self.log_event("qb_done",
                    f"qB download complete: {st['filename']} "
                    f"({st['bytes_total']//1024//1024} MB)", url=url)
                self._update_job(url, "done",
                    f"qB: Saved: {st['filename']}",
                    filename=st["filename"] or "",
                    file_size=st["bytes_total"] or st["bytes_done"])
                # 15.11 (option b): st['filename'] is a bare NAME
                # (qb_bridge.py poll(): t.get("name")), and for a multi-file
                # torrent it names a DIRECTORY. Resolve it to the absolute
                # path of the largest media file inside so the v3.66.837
                # forward path (db_log keys on absoluteness) records a real
                # row. In its OWN try, SEPARATE from db_log's below: a
                # resolution failure must degrade to file_path=None, never
                # cost the history row.
                _lib_path = None
                try:
                    from . import library as _library
                    _lib_path = _library.library_path_for_completion(
                        dl_dir, st["filename"] or "")
                except Exception:
                    _lib_path = None
                try:
                    from .db import db_log
                    db_log(self.site_id, self.config.get("name", "?"),
                           url, "done", st["filename"] or "",
                           st["bytes_total"] or st["bytes_done"],
                           "qBittorrent backend",
                           # An external backend moved these bytes, but they
                           # genuinely crossed the wire for this job and the
                           # backend reports the count. bytes_done is what was
                           # transferred; bytes_total can be the advertised size.
                           bytes_fetched=st["bytes_done"],
                           file_path=_lib_path,
                           **history_title_kwargs(self, url))
                except Exception:
                    pass
                return True, ""
            elif st["status"] == "failed":
                self.log_event("qb_fallback",
                    f"qB download failed ({st['error']}); "
                    f"falling back", url=url)
                return False, st["error"]
            time.sleep(2.0)

        self.log_event("qb_fallback",
            "qB download exceeded 4h budget; falling back", url=url)
        client.cancel(torrent_hash, delete_files=True)
        return False, "timeout"
    def _get_jd_client(self):
        """Lazy JD client constructor. Returns None when JD is not
        configured or httpx isn't installed. Caller treats None as
        "JD unavailable → fall through to teach"."""
        try:
            from . import jd_bridge
        except Exception:
            return None
        if self._jd_client is None:
            self._jd_client = jd_bridge.get_client_for_site(self.config)
        return self._jd_client
    def _read_cookies_for_jd(self) -> str:
        """Read the latest cookies for this site and convert to JD's
        `name=value; name=value` string format. The session keeper
        (v3.43.16) persists fresh cookies after every successful
        heartbeat (~5 min cadence), so reading from disk here gets the
        most-recent set. Falls back to self.cookies (in-memory) if the
        file isn't available."""
        from . import jd_bridge
        # Prefer the on-disk file (kept current by the session keeper)
        # over our in-memory copy, which is loaded once at startup and
        # only refreshed on re-login.
        cookies_list = []
        try:
            from .cookies import load_cookies_from_file
            from pathlib import Path as _Path
            cookie_path = _Path("cookies") / f"{self.site_id}.json"
            if cookie_path.exists():
                cookies_list = load_cookies_from_file(str(cookie_path)) or []
        except Exception:
            pass
        if not cookies_list and self.cookies:
            cookies_list = self.cookies
        return jd_bridge.cookies_playwright_to_jd(cookies_list)
    def _record_jd_outcome(self, succeeded: bool):
        """Append a JD outcome to the rolling window. Triggered by the
        runner regardless of which path won — succeeded=True when JD
        actually downloaded, False when we fell back to teach. The
        ratio drives the insight-strip warning."""
        self._jd_recent_outcomes.append(bool(succeeded))
    def jd_health(self) -> dict:
        """Return current JD bridge health for the site. Called from
        /api/status to populate the per-site insight card and the
        header health pill. Cheap — no network IO in the common path,
        just reads the in-memory outcome window."""
        if (self.config.get("backend") or "teach").lower() != "jd":
            return {"backend": "teach", "enabled": False}
        outcomes = list(self._jd_recent_outcomes)
        ok = sum(1 for x in outcomes if x)
        n = len(outcomes)
        rate = (ok / n) if n else None
        # Warn band: <50% success over a meaningful sample (>=5).
        # Below 5 samples we don't have enough data; render as "ok"
        # to avoid flapping false-positives on cold start.
        warn = (n >= 5 and rate is not None and rate < 0.5)
        return {
            "backend": "jd",
            "enabled": True,
            "samples": n,
            "success_rate": rate,
            "warn_plugin_broken": warn,
            "host": self.config.get("jd_host") or "127.0.0.1",
            "port": int(self.config.get("jd_port") or 3128),
        }
    def _try_jd_download(self, url: str, dl_dir: str) -> tuple[bool, str]:
        """Attempt a JD-backed download for `url`. Returns
        (succeeded, reason). On success, the file is already in dl_dir
        and the job state has been updated. On failure, the caller
        falls back to the teach-based path.

        Retry budget: 1 cookie refresh + JD resubmit if the first
        attempt returns an auth-class error. Anything else (plugin
        defect, network, unknown) → return False immediately and let
        teach pick up. This matches the v3.43.21 design contract.
        """
        # INTEROP-GOV-1 / JD-1: when interop governance is enabled, a URL is
        # routed to a JDownloader hoster plugin ONLY if that plugin -- keyed by
        # the URL host, since JD's plugins are per-hoster -- is registered +
        # risk-acknowledged + enabled in the interop_registry. An un-acked host
        # is refused here and falls through to the teach path, exactly like an
        # unreachable JD. Default-OFF: with the toggle absent the gate is skipped
        # and JD behaves as before (v3.43.21 contract unchanged). Mirrors the
        # runner_browser chromium_extension gate. One registry read; cheap.
        if self.config.get("interop_governance_enabled", False):
            from urllib.parse import urlparse
            from . import interop_registry as _ir
            host = (urlparse(url).hostname or "").lower()
            if not _ir.is_permitted("jd_plugin", host):
                self.log_event("jd_governance_blocked",
                    f"JD plugin for {host or '?'} not permitted by interop "
                    f"governance (register + acknowledge + enable it first); "
                    f"using teach fallback", url=url)
                return False, (f"JD plugin for {host or '?'} not permitted "
                               f"(interop governance)")

        try:
            from . import jd_bridge
        except Exception as e:
            return False, f"jd_bridge import failed: {e}"

        client = self._get_jd_client()
        if client is None:
            return False, "JD client unavailable"

        # Fast pre-check. If JD isn't even reachable, skip submission
        # entirely and emit a single line of context so the user can
        # diagnose (vs. silent fall-through which makes "why isn't JD
        # working" hard to investigate).
        if not client.is_reachable():
            self.log_event("jd_unreachable",
                f"JD not reachable at {client.host}:{client.port}; "
                f"using teach fallback", url=url)
            return False, "JD unreachable"

        cookies = self._read_cookies_for_jd()
        # Submit with retry budget: 1 cookie-refresh + resubmit on auth
        # failure. The cookie refresh path delegates to the session
        # keeper (which the runner already coordinates with for its
        # own re-login decisions).
        attempts = 0
        last_error = ""
        link_id = None
        while attempts < 2:
            attempts += 1
            try:
                link_id = client.submit(url, cookies=cookies, dest_dir=dl_dir)
                break
            except jd_bridge.JDError as e:
                last_error = f"{e.kind}: {e.message}"
                if e.kind == "auth" and attempts < 2:
                    # Force a session refresh. The login flow updates
                    # self._cookies_updated_at and re-persists the
                    # cookie file; we re-read and resubmit once.
                    self.log_event("jd_auth_refresh",
                        "JD returned auth error — refreshing cookies "
                        "and retrying", url=url)
                    try:
                        self.login_async()
                        # Wait briefly for the login thread to update
                        # cookies. If it doesn't complete in time we
                        # resubmit with what we have; worst case we
                        # fall through to teach on the next failure.
                        for _ in range(30):  # 6 seconds max
                            time.sleep(0.2)
                            if self._cookies_updated_at > 0:
                                break
                        cookies = self._read_cookies_for_jd()
                        continue
                    except Exception as relog_err:
                        self.log_event("jd_auth_refresh",
                            f"Cookie refresh failed: {relog_err}",
                            url=url)
                # Non-auth, or auth-retry budget exhausted
                self.log_event("jd_fallback",
                    f"JD submit failed ({last_error}); falling back to teach",
                    url=url)
                return False, last_error

        if not link_id:
            return False, last_error or "no link_id"

        # Poll loop. Mirror progress into the job state so the queue
        # row shows a progress bar. Cadence: 2s while running. Total
        # budget: 60 minutes (configurable in future via cfg if
        # needed; for now a sane upper bound).
        deadline = time.time() + 3600
        last_seen_bytes = 0
        last_progress_at = time.time()
        while time.time() < deadline:
            if self._stop.is_set():
                # Runner is stopping — try to cancel cleanly so JD
                # doesn't keep a phantom job. Best-effort.
                client.cancel(link_id)
                return False, "stopped"
            try:
                st = client.poll(link_id, timeout=10)
            except jd_bridge.JDError as e:
                self.log_event("jd_fallback",
                    f"JD poll failed ({e.kind}: {e.message}); "
                    f"falling back to teach", url=url)
                client.cancel(link_id)
                return False, f"poll: {e.message}"

            # Mirror status → job state
            if st["status"] == "running":
                if st["bytes_total"] > 0 and st["bytes_done"] > last_seen_bytes:
                    pct = (st["bytes_done"] / st["bytes_total"]) * 100
                    speed_mb = st["speed"] / 1_000_000 if st["speed"] else 0
                    msg = (f"JD: {pct:.1f}% "
                           f"({st['bytes_done']//1024//1024} / "
                           f"{st['bytes_total']//1024//1024} MB"
                           + (f", {speed_mb:.1f} MB/s)" if speed_mb else ")"))
                    self._update_job(url, "running", msg,
                        filename=st["filename"] or "",
                        file_size=st["bytes_done"])
                    last_seen_bytes = st["bytes_done"]
                    last_progress_at = time.time()
                elif time.time() - last_progress_at > 600:
                    # No progress for 10 minutes — JD is stuck. Cancel
                    # and fall back. The user's brazzers/bangbros
                    # session-timeout case manifests as this.
                    self.log_event("jd_fallback",
                        "JD download stuck (no progress in 10 min); "
                        "falling back to teach", url=url)
                    client.cancel(link_id)
                    return False, "stuck"
            elif st["status"] == "done":
                self.log_event("jd_done",
                    f"JD download complete: {st['filename']} "
                    f"({st['bytes_total']//1024//1024} MB)", url=url)
                self._update_job(url, "done",
                    f"JD: Saved: {st['filename']}",
                    filename=st["filename"] or "",
                    file_size=st["bytes_total"] or st["bytes_done"])
                # Persist to DB for History tab consistency.
                # 15.11 (option b): st['filename'] is a bare NAME
                # (jd_bridge.py poll(): row.get("name")), and for a multi-file
                # package it names a DIRECTORY. Same contract as the qB
                # done-site above: resolve in an OWN try, degrade to None.
                _lib_path = None
                try:
                    from . import library as _library
                    _lib_path = _library.library_path_for_completion(
                        dl_dir, st["filename"] or "")
                except Exception:
                    _lib_path = None
                try:
                    from .db import db_log
                    db_log(self.site_id, self.config.get("name","?"),
                           url, "done", st["filename"] or "",
                           st["bytes_total"] or st["bytes_done"],
                           "JD backend",
                           # An external backend moved these bytes, but they
                           # genuinely crossed the wire for this job and the
                           # backend reports the count. bytes_done is what was
                           # transferred; bytes_total can be the advertised size.
                           bytes_fetched=st["bytes_done"],
                           file_path=_lib_path,
                           **history_title_kwargs(self, url))
                except Exception:
                    pass
                return True, ""
            elif st["status"] == "failed":
                kind = jd_bridge.classify_jd_error(st["error"])
                if kind == "auth":
                    # Same path as submit-time auth failure.
                    self.log_event("jd_fallback",
                        f"JD download failed with auth error "
                        f"({st['error'][:100]}); falling back to teach",
                        url=url)
                else:
                    self.log_event("jd_fallback",
                        f"JD download failed ({kind}: "
                        f"{st['error'][:100]}); falling back to teach",
                        url=url)
                return False, st["error"]
            # pending or no-status: short sleep, poll again
            time.sleep(2.0)

        # Timeout
        self.log_event("jd_fallback",
            "JD download exceeded 60min budget; falling back to teach",
            url=url)
        client.cancel(link_id)
        return False, "timeout"
