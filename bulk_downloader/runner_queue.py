"""runner_queue -- URL queue: load/reorder/priority/bulk/clear/export

Extracted from runner.py (SiteRunner) @v3.66.401, PHASE 3 runner cut 4.
Mixin: methods reference self.* only; NO __init__. Import block derived by AST
free-name scan of the moved bodies (the seams doc omitted the playlist +
yt_dlp_archive conditionals). Cycle rule: kernel from .runner_util, nothing
from .runner.
"""
import contextlib, queue, sqlite3, sys, threading, time
from pathlib import Path

from .runner_util import _ts
from .db import (
    queue_load, queue_upsert, queue_bulk_upsert, queue_bulk_delete,
    queue_bulk_update, queue_delete_status, queue_reorder, queue_set_priority,
)

# playlist_extractor soft import (moved verbatim from runner.py; flat sibling).
try:
    from . import playlist_extractor as _playlist
    _PLAYLIST_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_queue] playlist_extractor import failed (degraded): {_e}\n")
    _playlist = None
    _PLAYLIST_AVAILABLE = False

# yt_dlp_archive soft import (moved verbatim; flat sibling).
try:
    from . import yt_dlp_archive as _ytdlp_arch
    _YTDLP_ARCH_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_queue] yt_dlp_archive import failed (degraded): {_e}\n")
    _ytdlp_arch = None
    _YTDLP_ARCH_AVAILABLE = False


_JOB_STATUS_BOOTSTRAP_LOCK = threading.Lock()


@contextlib.contextmanager
def job_status_writer(runner):
    """Guard an eligibility/completion mutation and invalidate its token.

    Combined lock order is lifecycle -> jobs -> heartbeats -> queue. The
    yielded callback must be invoked after an actual in-memory mutation;
    persistence and plugin/network effects belong outside this scope.
    """
    lifecycle_lock = getattr(runner, "_run_lifecycle_lock", None)
    if lifecycle_lock is None:
        with _JOB_STATUS_BOOTSTRAP_LOCK:
            lifecycle_lock = getattr(runner, "_run_lifecycle_lock", None)
            if lifecycle_lock is None:
                lifecycle_lock = threading.RLock()
                runner._run_lifecycle_lock = lifecycle_lock
    with lifecycle_lock:
        with runner._lock:
            changed = [False]

            def mark_changed():
                changed[0] = True

            try:
                yield mark_changed
            finally:
                if changed[0]:
                    runner._job_status_version = (
                        getattr(runner, "_job_status_version", 0) + 1)
                    runner._completion_notification_token = None


class QueueMixin:
    def _job_status_writer(self):
        return job_status_writer(self)

    def _restore_queue(self):
        """Load persisted queue rows and rebuild self.urls / self.jobs."""
        rows = queue_load(self.site_id)
        if not rows: return
        # v3.49 (#127b): track which URLs were in "running" state at the
        # prior shutdown — those were mid-download when the process died.
        # The UI surfaces this as a small "recovered" badge so the
        # operator knows which jobs to keep an eye on (they may have
        # partial files on disk, partial DB state, etc).
        crash_recovered_urls = []
        for r in rows:
            url = r["url"]
            status = r["status"]
            was_running = (status == "running")
            # Reset in-flight states — we can't resume mid-Playwright work
            if was_running:
                status = "pending"
                crash_recovered_urls.append(url)
                queue_upsert(self.site_id, url, status="pending",
                             message="Restored after restart "
                                     "— was mid-download at last shutdown")
            self.jobs[url] = {
                "status": status,
                "message": r.get("message",""),
                "ts": "",
                "priority": r.get("priority","") or "normal",
                "retries": r.get("retries",0),
                "retry_after": r.get("retry_after",0),
                "screenshot": r.get("screenshot",""),
                "filename": r.get("filename",""),
                "file_size": r.get("file_size",0),
                # v3.49 (#127b): badge flag — frontend renders a small
                # "recovered" pill on these rows for a few minutes after
                # boot so the operator can identify them at a glance
                "_recovered_from_crash": was_running,
            }
            if r.get("force_download"):
                self.jobs[url]["force_download"] = True
            self.urls.append(url)
        sys.stderr.write(f"[{self.site_id}] restored {len(rows)} queue entries"
                         + (f" ({len(crash_recovered_urls)} mid-download)"
                            if crash_recovered_urls else "") + "\n")
    def load_urls(self,urls,dedupe=True,folder_scan=False):
        """Phase 7.4: when folder_scan=True, walk the configured download_dir
        and pre-mark URLs as `done — already on disk` if a file with a
        matching basename exists. Saves wasted page visits when re-running
        an old URL list. Filename matching is conservative (last path
        segment of URL, with common extensions stripped) — false negatives
        just mean the URL gets queued normally and the existing
        skip_if_exists check fires later.

        Phase 68 (v3.38.x): per-URL custom headers. URLs can carry tab-
        separated `header=value` pairs after the URL:
            https://example.com/v.mp4\\tReferer=https://example.com/page\\tCookie=session=abc
        These get stored on the job and applied at request time. Handles
        sites where a specific Referer or one-shot cookie is required."""
        added=dupes=skipped_on_disk=0
        new_urls=[]
        # Phase 68: separate URL strings into (url, extra_headers) tuples
        parsed_urls = []
        for raw in urls:
            if not raw: continue
            if '\t' in raw:
                parts = raw.split('\t')
                url_part = parts[0].strip()
                hdrs = {}
                for hdr_str in parts[1:]:
                    if '=' in hdr_str:
                        k, v = hdr_str.split('=', 1)
                        hdrs[k.strip()] = v.strip()
                parsed_urls.append((url_part, hdrs))
            else:
                parsed_urls.append((raw.strip(), {}))

        # Phase 80 (v3.40.0): bidirectional pre_url_added webhook. Each URL
        # gets a chance to be skipped, rewritten, or repriortized by a
        # remote webhook BEFORE it lands in the queue. Only fires when the
        # site subscribes to 'pre_url_added'. Errors are non-fatal.
        url_priorities = {}  # url → priority override
        webhook_subs = (self.config.get("webhook_events") or "").lower()
        if "pre_url_added" in webhook_subs and self.config.get("webhook_urls"):
            try:
                from .hooks import webhook_pre_url_added
                filtered = []
                for u, hdrs in parsed_urls:
                    allowed, final_u, prio = webhook_pre_url_added(self.config, u)
                    if not allowed:
                        self.log_event("webhook_skip", "Webhook skipped URL", url=u)
                        continue
                    if final_u != u:
                        self.log_event("webhook_rewrite",
                                       f"Webhook rewrote URL: {u} → {final_u}", url=final_u)
                    if prio:
                        url_priorities[final_u] = prio
                    filtered.append((final_u, hdrs))
                parsed_urls = filtered
            except Exception as e:
                self.log.warning("pre_url_added webhook failed: %s", e)

        # v3.43.75: yt-dlp download_archive skip. If the URL maps to
        # an extractor+id that's already in the configured archive,
        # mark as a dupe so it doesn't queue. Fail-open: any error
        # falls through (URL still queued).
        if (self.config.get("use_ytdlp_archive", False)
                and _YTDLP_ARCH_AVAILABLE and _ytdlp_arch is not None):
            archive_path = (self.config.get("ytdlp_archive_path", "") or "").strip()
            if archive_path:
                filtered = []
                ytdlp_skipped = 0
                for u, hdrs in parsed_urls:
                    try:
                        if _ytdlp_arch.try_skip_for_url(archive_path, u):
                            ytdlp_skipped += 1
                            continue
                    except Exception:
                        pass  # never block on archive error
                    filtered.append((u, hdrs))
                if ytdlp_skipped > 0:
                    self.log_event(
                        "ytdlp_archive_skip",
                        f"skipped {ytdlp_skipped} URLs already in archive",
                    )
                parsed_urls = filtered

        # v3.43.75: playlist URL fan-out. For each URL that looks like
        # a listing page, expand it into the individual scene URLs by
        # navigating with Playwright and extracting links. Fail-open:
        # if extraction returns nothing, the literal URL stays in the
        # queue (no-op).
        if (self.config.get("use_playlist_extractor", False)
                and _PLAYLIST_AVAILABLE and _playlist is not None
                and parsed_urls):
            template = self.config  # site config can carry the hints
            expanded: list = []
            for u, hdrs in parsed_urls:
                try:
                    is_listing = _playlist.is_likely_listing_url(
                        u, template=template)
                except Exception:
                    is_listing = False
                if not is_listing:
                    expanded.append((u, hdrs))
                    continue
                # Need a Playwright page. Spin up a transient context
                # using the same anti-detection settings as workers.
                try:
                    expanded_children = self._playlist_expand_one(u)
                except Exception as e:
                    self.log_event(
                        "playlist_expand_failed",
                        f"{u}: {type(e).__name__}:{str(e)[:80]}",
                    )
                    expanded_children = []
                if expanded_children:
                    self.log_event(
                        "playlist_expanded",
                        f"{u} → {len(expanded_children)} scene URLs",
                    )
                    for child_url in expanded_children:
                        expanded.append((child_url, hdrs))
                else:
                    # Couldn't extract — queue the literal URL
                    expanded.append((u, hdrs))
            parsed_urls = expanded

        existing_files=set()
        if folder_scan:
            dl_dir=self.config.get("download_dir","")
            if dl_dir and Path(dl_dir).exists():
                # Walk shallow + 1 level deep (template-organized subfolders)
                try:
                    for p in Path(dl_dir).rglob("*"):
                        if p.is_file():
                            existing_files.add(p.stem.lower())
                except Exception as e:
                    self.log.error("folder scan failed: %s", e)
        prepared = []
        for u, hdrs in parsed_urls:
            # Count URLs dropped as already-present before potentially slow
            # policy/file work; the status-writer transaction repeats the
            # duplicate check before publication.
            with self._lock:
                if u in self.jobs:
                    dupes+=1
                    continue
            # v3.45.0 Phase 194: content-rights checks may touch persistence,
            # so they deliberately remain outside the lifecycle transaction.
            try:
                from . import content_rights as _cr
                block = _cr.url_is_blocked(u)
                if block:
                    _cr.record_refusal(u,
                        f"blocklist id {block.get('id')}: "
                        f"{block.get('reason','')[:100]}")
                    dupes += 1
                    continue
            except Exception:
                pass
            pre_done=False
            if folder_scan and existing_files:
                try:
                    from urllib.parse import urlparse
                    last=urlparse(u).path.rstrip("/").split("/")[-1] or ""
                    for ext in (".html",".php",".htm",".aspx",""):
                        if last.lower().endswith(ext):
                            stem=last[:-len(ext)] if ext else last
                            if stem.lower() in existing_files:
                                pre_done=True; break
                except Exception: pass
            status="done" if pre_done else "pending"
            msg="Already on disk (folder scan)" if pre_done else ""
            prepared.append((u, hdrs, pre_done, status, msg))

        # Publish the prepared intake as one lifecycle/job transaction. Slow
        # policy and filesystem checks above never hold either lock.
        with self._job_status_writer() as mark_status_changed:
            ord_start=len(self.urls)
            for u, hdrs, pre_done, status, msg in prepared:
                if u in self.jobs:
                    dupes+=1
                    continue
                self.jobs[u]={"status":status,"message":msg,"ts":_ts() if pre_done else "",
                              "priority":url_priorities.get(u, "normal"),"retries":0,"retry_after":0,
                              "filename":"","file_size":0,
                              # v3.43.23: stamp creation time so a newly-added URL doesn't
                              # immediately flag as stuck. Updated by _update_job on every
                              # state change or byte advance.
                              "last_progress_at": time.time()}
                if hdrs:
                    self.jobs[u]["custom_headers"] = hdrs
                self.urls.append(u); new_urls.append(u)
                if pre_done: skipped_on_disk+=1
                else: added+=1
            if new_urls:
                mark_status_changed()
        # Phase 4.2: bulk-insert into queue table outside the lock
        if new_urls:
            try: queue_bulk_upsert(self.site_id, new_urls, ord_start=ord_start)
            except Exception as e: self.log.error("bulk persist failed: %s", e)
            # Persist done-state for folder-scan pre-marked URLs in ONE
            # bulk update (they all carry the same "already on disk"
            # message) — previously a per-URL queue_upsert loop.
            done_urls=[u for u in new_urls
                       if self.jobs.get(u,{}).get("status")=="done"]
            if done_urls:
                try:
                    queue_bulk_update(self.site_id, done_urls, status="done",
                                      message="Already on disk (folder scan)")
                except sqlite3.Error as e:
                    # Phase 34: narrow + log. Persist failures leave
                    # the in-memory state inconsistent with disk —
                    # on next restart, the URL would be re-queued.
                    self.log.warning("queue_bulk_update (done) failed: %s", e)
        # Phase 13: log the bulk add as a single summary event (rather than
        # spamming N transitions). This shows up in the Events tab as a
        # clear "n URLs imported" entry.
        try:
            self.log_event("import",
                f"Imported {added} URL(s); {dupes} duplicate(s); {skipped_on_disk} skipped (already on disk)",
                extra={"added": added, "dupes": dupes, "skipped": skipped_on_disk})
        except Exception as e:
            self.log.debug("import event log failed: %s", e)
        # E1 (v3.66.494): queue intake event through the isolated emit seam.
        # Fires only when new URLs actually landed (added>0) so a pure
        # all-dupes import is a no-op. A throwing consumer never breaks intake.
        if added:
            try:
                from . import plugins as _pl
                _pl.emit("queue.enqueued",
                         {"site_id": self.site_id, "added": added,
                          "dupes": dupes, "skipped": skipped_on_disk,
                          "ts": _ts()})
            except Exception:
                pass
        return added,dupes,skipped_on_disk
    def reorder_urls(self,ordered):
        with self._lock:
            ex=set(self.urls)
            self.urls=[u for u in ordered if u in ex]
            for u in ex:
                if u not in self.urls: self.urls.append(u)
        # Persist new ordering — one transaction (was a per-URL loop)
        try:
            queue_reorder(self.site_id,
                          {u: i for i, u in enumerate(self.urls)})
        except sqlite3.Error as e:
            self.log.warning("reorder persist failed: %s", e)
    def set_priority(self,url,priority):
        with self._lock:
            if url in self.jobs:
                self.jobs[url]["priority"]=priority
                if priority=="high":
                    if url in self.urls: self.urls.remove(url)
                    self.urls.insert(0,url)
        try: queue_upsert(self.site_id,url,priority=priority)
        except sqlite3.Error as e:
            self.log.warning("set_priority persist failed: %s", e)
    def bulk_priority(self,urls,priority):
        """Apply priority to many URLs at once. High-priority URLs are
        moved to the front of the queue, preserving their relative order."""
        urls=list(urls)
        applied=[]
        with self._lock:
            promote=[]
            for u in urls:
                if u in self.jobs:
                    self.jobs[u]["priority"]=priority; applied.append(u)
                    if priority=="high":
                        if u in self.urls: self.urls.remove(u)
                        promote.append(u)
            for u in reversed(promote): self.urls.insert(0,u)
        try:
            queue_set_priority(self.site_id, applied, priority)
        except Exception: pass
        return len(urls)
    def bulk_delete(self,urls):
        """Remove URLs from the queue and the job map. Does NOT touch
        history (those rows persist). Returns the count actually removed.

        E1 (v3.66.494): a removed job that was in ``needs_review`` is a review
        *skip* (the operator dismissed it without downloading) -- counted and
        surfaced as ``review.skipped`` so a plugin can mirror the decision."""
        urls=set(urls)
        removed=0
        skipped_review=0
        with self._job_status_writer() as mark_status_changed:
            for u in list(self.jobs.keys()):
                if u in urls:
                    if (self.jobs.get(u) or {}).get("status") == "needs_review":
                        skipped_review+=1
                    del self.jobs[u]; removed+=1
            self.urls=[u for u in self.urls if u not in urls]
            if removed:
                mark_status_changed()
        try:
            queue_bulk_delete(self.site_id, list(urls))
        except Exception: pass
        if skipped_review:
            try:
                from . import plugins as _pl
                _pl.emit("review.skipped",
                         {"site_id": self.site_id, "count": skipped_review,
                          "ts": _ts()})
            except Exception:
                pass
        return removed
    def bulk_approve(self,urls):
        """Approve needs_review URLs to bypass the min_resolution threshold.
        Sets force_download=True on each job and re-queues as pending."""
        urls=set(urls); n=0; approved=[]
        with self._job_status_writer() as mark_status_changed:
            for u in urls:
                j=self.jobs.get(u)
                if not j: continue
                j.update({"status":"pending","message":"Approved — will force-download below threshold",
                          "ts":_ts(),"force_download":True,"retries":0,"retry_after":0})
                if u not in self.urls: self.urls.append(u)
                approved.append(u); n+=1
            if n:
                mark_status_changed()
        try:
            queue_bulk_update(self.site_id, approved, status="pending",
                              force_download=1, retries=0, retry_after=0,
                              message="Approved — will force-download below threshold")
        except Exception: pass
        # E1 (v3.66.494): a needs_review->pending approval is a review decision.
        if n:
            try:
                from . import plugins as _pl
                _pl.emit("review.approved",
                         {"site_id": self.site_id, "count": n, "ts": _ts()})
            except Exception:
                pass
        return n
    def bulk_pause(self, urls):
        """v3.49 (#55): pause pending jobs without removing them. Pauses
        only affect pending-state jobs — running jobs continue (we don't
        kill in-flight downloads from a pause), done/failed are untouched.

        A paused job moves to status='stopped' with a marker message so
        the UI can show it's user-paused, not system-stopped."""
        urls = set(urls); n = 0; paused = []
        with self._job_status_writer() as mark_status_changed:
            for u in urls:
                j = self.jobs.get(u)
                if not j: continue
                if j.get("status") not in ("pending",): continue
                j.update({"status": "stopped",
                          "message": "Paused by user",
                          "ts": _ts(),
                          "_paused_by_user": True})
                paused.append(u); n += 1
            if n:
                mark_status_changed()
        try:
            queue_bulk_update(self.site_id, paused, status="stopped",
                              message="Paused by user")
        except Exception: pass
        return n
    def bulk_resume(self, urls):
        """v3.49 (#55): un-pause stopped jobs. The inverse of bulk_pause.
        Re-queues them as pending. Idempotent on jobs that aren't stopped."""
        urls = set(urls); n = 0; resumed = []
        with self._job_status_writer() as mark_status_changed:
            for u in urls:
                j = self.jobs.get(u)
                if not j: continue
                if j.get("status") not in ("stopped",): continue
                j.update({"status": "pending", "message": "Resumed",
                          "ts": _ts(), "retries": 0, "retry_after": 0,
                          "_paused_by_user": False})
                if u not in self.urls: self.urls.append(u)
                resumed.append(u); n += 1
            if n:
                mark_status_changed()
        try:
            queue_bulk_update(self.site_id, resumed, status="pending",
                              message="Resumed", retries=0, retry_after=0)
        except Exception: pass
        return n
    def bulk_retry(self, urls):
        """v3.49: retry failed jobs in bulk. Resets retries counter so the
        full retry budget is available again. Skips jobs not in failed
        state (returning 'retry pending' on a successful job would be
        confusing)."""
        urls = set(urls); n = 0; retried = []
        with self._job_status_writer() as mark_status_changed:
            for u in urls:
                j = self.jobs.get(u)
                if not j: continue
                if j.get("status") not in ("failed", "needs_review"): continue
                j.update({"status": "pending", "message": "Retry requested",
                          "ts": _ts(), "retries": 0, "retry_after": 0})
                if u not in self.urls: self.urls.append(u)
                retried.append(u); n += 1
            if n:
                mark_status_changed()
        try:
            queue_bulk_update(self.site_id, retried, status="pending",
                              message="Retry requested", retries=0, retry_after=0)
        except Exception: pass
        return n
    def bulk_reorder(self, ordered_urls):
        """v3.49 (#56): rewrite the queue's order to match the supplied
        sequence. URLs not in the sequence keep their relative order
        AFTER the explicitly-ordered ones (i.e. front-load the given list
        without dropping anything). Used by drag-to-reorder in the UI.

        Returns the new total order length."""
        with self._lock:
            seen = set()
            front = []
            for u in ordered_urls:
                if u in self.jobs and u not in seen:
                    front.append(u)
                    seen.add(u)
            # Append the untouched tail (everything not in the request)
            tail = [u for u in self.urls if u not in seen]
            self.urls = front + tail
            # Persist ord = index across the table — one transaction
            try:
                queue_reorder(self.site_id,
                              {u: i for i, u in enumerate(self.urls)})
            except Exception:
                pass
            return len(self.urls)
    def bulk_url_transform(self, transforms):
        """Phase 18.25: rewrite URLs in-place from a list of (old, new) pairs.

        Skips a transform if the new URL already exists in the queue (would
        create a duplicate row). Persists each change to the queue table by
        deleting the old row and inserting the new one with the same
        status/message/etc.

        Returns the count of URLs successfully renamed."""
        from .db import db_conn
        n = 0
        with self._lock:
            for old_url, new_url in transforms:
                if old_url == new_url: continue
                if old_url not in self.jobs: continue
                if new_url in self.jobs: continue  # would duplicate — skip
                # Update in-memory state
                self.jobs[new_url] = self.jobs.pop(old_url)
                if old_url in self.urls:
                    idx = self.urls.index(old_url)
                    self.urls[idx] = new_url
                n += 1
        # Persist (outside the lock — DB has its own locking, slow ops here
        # would block the worker if held inside the runner lock)
        try:
            with db_conn() as cx:
                for old_url, new_url in transforms:
                    if old_url == new_url: continue
                    # Move the queue row: copy old to new, delete old. We
                    # use UPDATE OR IGNORE first; if a duplicate exists we
                    # just keep the original.
                    cx.execute(
                        "UPDATE OR IGNORE queue SET url = ? "
                        "WHERE site_id = ? AND url = ?",
                        (new_url, self.site_id, old_url))
                    # If the UPDATE was IGNOREd because new_url already exists,
                    # delete the leftover old row so we don't keep a duplicate.
                    cx.execute(
                        "DELETE FROM queue WHERE site_id = ? AND url = ?",
                        (self.site_id, old_url))
        except Exception as e:
            self.log.error("bulk_url_transform persist failed: %s", e)
        self.log_event("transform",
                       f"Renamed {n} URL(s) via regex transform",
                       extra={"count": n})
        return n
    def clear_completed(self):
        """Drop URLs in `done` or `stopped` status from both the in-memory
        job map and the queue table. History rows (in the separate `history`
        table) are NOT affected — those persist for audit/reporting."""
        with self._job_status_writer() as mark_status_changed:
            removed=[u for u,j in self.jobs.items() if j["status"] in ("done","stopped")]
            self.jobs={u:j for u,j in self.jobs.items() if u not in removed}
            self.urls=[u for u in self.urls if u not in removed]
            if removed:
                mark_status_changed()
        try:
            queue_delete_status(self.site_id,"done")
            queue_delete_status(self.site_id,"stopped")
        except Exception: pass
    def retry_failed(self):
        """Reset every failed job back to pending so the scheduler picks
        them up again. Clears retries/retry_after counters too — the
        retry budget restarts fresh."""
        retried=[]
        with self._job_status_writer() as mark_status_changed:
            for u,j in self.jobs.items():
                if j["status"]=="failed":
                    j.update({"status":"pending","message":"","ts":"","retries":0,"retry_after":0})
                    retried.append(u)
            if retried:
                mark_status_changed()
        try:
            queue_bulk_update(self.site_id, retried, status="pending",
                              message="", retries=0, retry_after=0)
        except Exception: pass
    def retry(self):
        return self.retry_failed()
    def clear(self):
        return self.clear_completed()
    def export_urls(self,status_filter=None):
        """Return newline-joined URLs from the job map. Pass `status_filter`
        (e.g. 'done', 'failed') to limit to one status; None returns all.
        Used by the UI's Export button."""
        with self._lock:
            return "\n".join(u for u,j in self.jobs.items() if status_filter is None or j["status"]==status_filter)
    def _drain_url_queue(self):
        """Drain leftover items from a previous run, repaying
        unfinished_tasks for each (F1). Without the task_done() repayment a
        stop->start mid-queue leaves the counter permanently > 0, so
        _watch_done's `unfinished_tasks == 0` gate (see _watch_done) is never
        reached -> sentinels never sent -> worker threads + Chromium leak.
        task_done() is ValueError-guarded so an over-drain can't raise."""
        while not self._url_queue.empty():
            try:
                self._url_queue.get_nowait()
                try:
                    self._url_queue.task_done()
                except ValueError:
                    pass
            except queue.Empty:
                break
