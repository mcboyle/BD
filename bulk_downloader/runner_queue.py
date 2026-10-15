"""runner_queue -- URL queue: load/reorder/priority/bulk/clear/export

Extracted from runner.py (SiteRunner) @v3.66.401, PHASE 3 runner cut 4.
Mixin: methods reference self.* only; NO __init__. Import block derived by AST
free-name scan of the moved bodies (the seams doc omitted the playlist +
yt_dlp_archive conditionals). Cycle rule: kernel from .runner_util, nothing
from .runner.
"""
import contextlib, queue, sqlite3, sys, threading, time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .runner_util import _ts, _ts_iso, _utc_iso_to_local_iso
from .db import (
    queue_headers_decode, queue_headers_encode,
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

# The statuses bulk_retry re-queues. retryable_urls reports exactly these, so an
# "offer a requeue" caller never offers a retry bulk_retry would then skip.
# dl95-dfxtra-1: dead_letter too -- retries exhausted, History shows it "failed", so a
# re-add must offer the same Requeue (bulk_retry clears retries/retry_after like
# db_queue_requeue_dead_letter does).
BULK_RETRY_STATUSES = ("failed", "needs_review", "dead_letter")

# ddr-fix F6: the job statuses a persisted queue row may carry. A row with any
# other status was never claimed and never cleared; _restore_queue parks it as
# "failed" (visible, retryable) instead. Local copy: nothing comes from .runner.
_RESTORABLE_STATUSES = frozenset({
    "pending", "running", "done", "failed", "error", "stopped", "cancelled",
    "needs_review", "dead_letter", "skipped_duplicate", "tombstone",
})


def _restored_retries(value):
    """ddr-fix F6: a NULL/garbage retries column restores as 0, never None
    (the next failure compared None >= int and raised TypeError)."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _is_queueable_url(url):
    """ddr-fix F8 (RULING adjB 0443Z): only http/https with a non-empty host
    is queued -- the rule app_import.is_http_url already applies on import."""
    try:
        parts = urlsplit(url)
        return parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
    except (TypeError, ValueError):
        return False


def _dedupe_key(url):
    """ddr-fix F10 (RULING adjB 0443Z): the dedupe KEY is the RFC 3986
    6.2.2/6.2.3 form -- scheme and host lowercased (userinfo kept as given),
    default port dropped; path, trailing slash, query and fragment exactly as
    given. Only the key: the job keeps the first-seen string. A URL the parser
    rejects keys as itself. An empty query or fragment keeps its delimiter
    (6.2.3: "/a?" is not "/a"); urlunsplit alone would drop it."""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        host = parts.hostname
        port = parts.port
    except (TypeError, ValueError):
        return url
    if not host:
        return url
    if ":" in host:
        host = f"[{host}]"
    userinfo = parts.netloc.rpartition("@")[0] if "@" in parts.netloc else ""
    netloc = (userinfo + "@" if userinfo else "") + host
    if port is not None and _DEFAULT_PORTS.get(scheme) != port:
        netloc += f":{port}"
    key = urlunsplit((scheme, netloc, parts.path, "", ""))
    if "?" in url.partition("#")[0]:
        key += "?" + parts.query
    if "#" in url:
        key += "#" + parts.fragment
    return key


_DEFAULT_PORTS = {"http": 80, "https": 443}


class LoadResult(tuple):
    """load_urls' (added, dupes, skipped) with the counts that are neither,
    as attributes, so every caller that unpacks three values keeps working:
    ``invalid`` (F8, not an http(s) URL with a host; never a dupe) and
    ``header_conflicts`` (F11, a dupe of a live job carrying different
    headers; the live job keeps its own)."""
    def __new__(cls, added, dupes, skipped, invalid=0, header_conflicts=0):
        self = super().__new__(cls, (added, dupes, skipped))
        self.invalid = invalid
        self.header_conflicts = header_conflicts
        return self


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
        from . import run_history
        run_history.reconcile_restored_runs(self.site_id)
        if not rows: return
        # v3.49 (#127b): track which URLs were in "running" state at the
        # prior shutdown — those were mid-download when the process died.
        # The UI surfaces this as a small "recovered" badge so the
        # operator knows which jobs to keep an eye on (they may have
        # partial files on disk, partial DB state, etc).
        crash_recovered_urls = []
        try:
            max_ret = int(self.config.get("max_retries", 2))
        except (TypeError, ValueError, AttributeError):
            max_ret = 2
        for r in rows:
            url = r["url"]
            status = r["status"]
            message = r.get("message","")
            retries = _restored_retries(r.get("retries"))
            was_running = (status == "running")
            # Reset in-flight states — we can't resume mid-Playwright work
            if was_running:
                crash_recovered_urls.append(url)
                # ddr-fix F5: a crash mid-download is a failed attempt, charged
                # like _handle_failure charges one, so a URL that kills the
                # process is not retried forever.
                if retries >= max_ret:
                    status = "failed"
                    message = (f"Failed: mid-download at shutdown with "
                               f"retries exhausted ({retries}/{max_ret})")
                else:
                    status = "pending"
                    retries += 1
                    message = ("Restored after restart "
                               "— was mid-download at last shutdown")
                queue_upsert(self.site_id, url, status=status,
                             message=message, retries=retries)
            elif status not in _RESTORABLE_STATUSES:
                # ddr-fix F6: unknown status -> visible and retryable.
                message = f"Restored with unknown status {str(status)[:40]!r}"
                status = "failed"
                queue_upsert(self.site_id, url, status=status, message=message)
            self.jobs[url] = {
                "status": status,
                "message": message,
                "ts": "",
                # CUT #31: carry a date-comparable stamp across restarts, or
                # every job completed before the last restart silently drops
                # out of the day-window count. `ts_updated` is the queue
                # table's ISO completion time (SQLite
                # strftime('%Y-%m-%dT%H:%M:%S','now'), UTC); "" when the
                # column is null or missing.
                # CUT #42: converted to LOCAL, not copied verbatim. All four
                # day-window consumers compare a LOCAL date, so a verbatim UTC
                # copy compared two different clocks and silently dropped
                # completed jobs on any non-UTC host.
                "ts_iso": _utc_iso_to_local_iso(r.get("ts_updated", "") or ""),
                "priority": r.get("priority","") or "normal",
                "retries": retries,
                "retry_after": r.get("retry_after",0),
                "screenshot": r.get("screenshot",""),
                "filename": r.get("filename",""),
                "listing_title": r.get("listing_title", ""),
                "file_size": r.get("file_size",0),
                # v3.49 (#127b): badge flag — frontend renders a small
                # "recovered" pill on these rows for a few minutes after
                # boot so the operator can identify them at a glance
                "_recovered_from_crash": was_running,
            }
            if r.get("force_download"):
                self.jobs[url]["force_download"] = True
            # qti-queue-f6: per-URL headers come back with the job
            restored_headers = queue_headers_decode(r.get("custom_headers"))
            if restored_headers:
                self.jobs[url]["custom_headers"] = restored_headers
            self.urls.append(url)
        sys.stderr.write(f"[{self.site_id}] restored {len(rows)} queue entries"
                         + (f" ({len(crash_recovered_urls)} mid-download)"
                            if crash_recovered_urls else "") + "\n")
    def load_urls(self,urls,dedupe=True,folder_scan=False,restore_status=None):
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
        sites where a specific Referer or one-shot cookie is required.

        restore_status: optional {url: (status, message)} for a queue import
        (q-import-export M2). A NEW job for such a url is created already in
        that status, inside the same transaction, so a cancelled/done row from
        an export never exists as claimable "pending" on its way back in."""
        if not dedupe:
            # ddr-fix F12: the job map holds one job per URL, so dedupe=False
            # cannot be honoured; refuse it rather than ignore it.
            raise ValueError("load_urls(dedupe=False) is not supported: "
                             "the queue holds one job per URL")
        restore_status = restore_status or {}
        restored_urls=set()
        added=dupes=skipped_on_disk=refused=invalid=0
        conflicts=[]  # F11: (url, stored url) dupes of a live job with other headers
        rearm_headers={}  # F11: re-armed url -> its new queue.custom_headers text
        new_urls=[]
        new_headers={}  # url -> custom headers of a job created by this call
        created={}  # url -> the job dict this call created (F2 rollback)
        rearmed=[]  # dl95-africancasting-1: stopped jobs re-armed by this add
        # QueueMixin also has lightweight/direct hosts that predate website
        # title capture. Preserve their enqueue contract with an empty metadata
        # map while SiteRunner supplies the real shared dict.
        listing_titles = getattr(self, "_listing_titles", {})
        if not isinstance(listing_titles, dict):
            listing_titles = {}
        # Phase 68: separate URL strings into (url, extra_headers) tuples
        parsed_urls = []
        def _refuse_invalid(u):
            # ddr-fix F8: refused visibly and counted; never queued, never a dupe.
            nonlocal invalid
            invalid += 1
            self.log_event("invalid_url",
                           f"Refused enqueue: not an http(s) URL with a host: {u[:200]!r}",
                           url=u[:200])
        for raw in urls:
            if not isinstance(raw, str): continue
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
        # F8: gate before the webhook, archive and playlist stages see the URL;
        # the prepare loop gates again for what those stages rewrite or add.
        gated = []
        for u, hdrs in parsed_urls:
            if _is_queueable_url(u): gated.append((u, hdrs))
            else: _refuse_invalid(u)
        parsed_urls = gated

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
        def _live_dupe(u, stored, hdrs):
            # ddr-fix F11: the live job keeps its headers; a dupe carrying
            # different ones is reported, never merged. Logged after the locks.
            if hdrs and hdrs != (self.jobs[stored].get("custom_headers") or {}):
                conflicts.append((u, stored))
        def _report_conflicts(start):
            for u, stored in conflicts[start:]:
                self.log_event("header_conflict",
                               "Duplicate of a queued job with different headers; "
                               "the queued job keeps its own" + (
                                   f" (queued as {stored[:200]!r})" if stored != u else ""),
                               url=u[:200])
        prepared = []
        for u, hdrs in parsed_urls:
            if not _is_queueable_url(u):  # a webhook rewrite or playlist child
                _refuse_invalid(u)
                continue
            # Count URLs dropped as already-present before potentially slow
            # policy/file work; the status-writer transaction repeats the
            # duplicate check (by dedupe key, F10) before publication.
            with self._lock:
                if u in self.jobs and self.jobs[u].get("status") != "stopped":
                    dupes+=1
                    _live_dupe(u, u, hdrs)
                    continue
            # v3.45.0 Phase 194: content-rights checks may touch persistence,
            # so they deliberately remain outside the lifecycle transaction.
            from . import content_rights as _cr
            try:
                block = _cr.url_is_blocked(u)
            except Exception as e:
                # The policy reader itself is a measurement boundary.  An
                # escaped exception is UNKNOWN, never a no-match.
                block = {
                    "blocked": None,
                    "unknown": True,
                    "error": f"content-rights check unavailable: {e}"[:200],
                }
            if block:
                if block.get("unknown"):
                    reason = block.get("error") or "blocklist unavailable"
                    self.log_event(
                        "content_rights_unknown",
                        f"Refused enqueue: {reason}",
                        url=u,
                    )
                else:
                    reason = (
                        f"blocklist id {block.get('id')}: "
                        f"{block.get('reason', '')[:100]}"
                    )
                try:
                    _cr.record_refusal(u,
                                       reason)
                except Exception as e:
                    self.log.error("content-rights refusal audit failed: %s", e)
                # ddr-fix F9: a refusal is not a duplicate.
                refused += 1
                continue
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
            if not pre_done and u in restore_status:
                status, msg = restore_status[u]
                restored_urls.add(u)
            prepared.append((u, hdrs, pre_done, status, msg))
        _report_conflicts(0)
        n_conflicts = len(conflicts)

        # Publish the prepared intake as one lifecycle/job transaction. Slow
        # policy and filesystem checks above never hold either lock.
        with self._job_status_writer() as mark_status_changed:
            # A global folder/saved-search router can capture this runner
            # before DELETE detaches it, then arrive here after retirement.
            # The lifecycle lock serializes the verdict: a retired identity
            # may never repopulate its in-memory or durable queue.
            if getattr(self, "_run_retired", False):
                return LoadResult(0, dupes+refused, skipped_on_disk,
                                  invalid=invalid, header_conflicts=n_conflicts)
            ord_start=len(self.urls)
            # ddr-fix F10: a job is found by its dedupe key and stays under
            # the string first seen for it (the first key holder wins).
            keymap = {}
            for x in self.jobs:
                keymap.setdefault(_dedupe_key(x), x)
            for u, hdrs, pre_done, status, msg in prepared:
                key = _dedupe_key(u)
                stored = keymap.get(key)
                if stored is not None:
                    job = self.jobs[stored]
                    # dl95-africancasting-1: a STOPPED job (site Stop, Cancel,
                    # Pause) never finished, so re-adding its URL re-arms it
                    # (bulk_resume's transition) instead of counting a dupe
                    # that nothing could ever retry. Other states stay dupes.
                    if job.get("status") == "stopped" and not pre_done:
                        job.update({"status": "pending", "message": "Re-added",
                                    "ts": _ts(), "retries": 0, "retry_after": 0,
                                    "_paused_by_user": False,
                                    "last_progress_at": time.time()})
                        # F11 / row 722: a re-armed job is a fresh job and
                        # takes this entry's headers, none when it has none.
                        if hdrs:
                            job["custom_headers"] = hdrs
                            if queue_headers_encode(hdrs) is None:
                                self.log.warning(
                                    "custom headers for %s cannot be stored and "
                                    "will not survive a restart", stored)
                        else:
                            job.pop("custom_headers", None)
                        rearm_headers[stored] = queue_headers_encode(hdrs)
                        if stored not in self.urls: self.urls.append(stored)
                        rearmed.append(stored); added+=1
                    else:
                        dupes+=1
                        _live_dupe(u, stored, hdrs)
                    continue
                keymap[key] = u
                self.jobs[u]={"status":status,"message":msg,"ts":_ts() if status!="pending" else "",
                              # CUT #41: a pre_done job is already "done"; without
                              # ts_iso no day-window consumer can ever count it.
                              "ts_iso":_ts_iso() if status!="pending" else "",
                              "priority":url_priorities.get(u, "normal"),"retries":0,"retry_after":0,
                              "filename":"","file_size":0,
                              # v3.43.23: stamp creation time so a newly-added URL doesn't
                              # immediately flag as stuck. Updated by _update_job on every
                              # state change or byte advance.
                              "last_progress_at": time.time()}
                listing_title = listing_titles.get(u, "")
                if listing_title:
                    self.jobs[u]["listing_title"] = listing_title
                if hdrs:
                    self.jobs[u]["custom_headers"] = hdrs
                    new_headers[u] = hdrs
                    if queue_headers_encode(hdrs) is None:
                        # e.g. an empty header name: usable now, not storable
                        self.log.warning(
                            "custom headers for %s cannot be stored and will "
                            "not survive a restart", u)
                created[u] = self.jobs[u]
                self.urls.append(u); new_urls.append(u)
                if pre_done: skipped_on_disk+=1
                else: added+=1
            if new_urls or rearmed:
                mark_status_changed()
        _report_conflicts(n_conflicts)
        if rearmed:
            # F11: one update per distinct header text (None -> NULL).
            by_headers = {}
            for u in rearmed:
                by_headers.setdefault(rearm_headers.get(u), []).append(u)
            try:
                for enc, r_urls in by_headers.items():
                    queue_bulk_update(self.site_id, r_urls, status="pending",
                                      message="Re-added", retries=0, retry_after=0,
                                      custom_headers=enc)
            except sqlite3.Error as e:
                self.log.warning("queue_bulk_update (re-added) failed: %s", e)
                sys.stderr.write(f"  queue: {len(rearmed)} re-added job(s) are pending in "
                                 f"memory only until the next queue write\n")
        # Phase 4.2: bulk-insert into queue table outside the lock
        if new_urls:
            try:
                queue_bulk_upsert(
                    self.site_id, new_urls, ord_start=ord_start,
                    listing_titles={
                        u: listing_titles.get(u, "") for u in new_urls
                    },
                    custom_headers=new_headers,
                )
            except Exception as e:
                self.log.error("bulk persist failed: %s", e)
                # ddr-fix F2: the jobs are not durable, so this call did not
                # queue them. Withdraw what it published (a job a worker has
                # already claimed stays with that worker) and raise, so the
                # caller cannot report them added and a re-send is not a dupe.
                withdrawn = set()
                with self._job_status_writer() as mark_status_changed:
                    for u in new_urls:
                        j = self.jobs.get(u)
                        if j is created[u] and j.get("status") != "running":
                            del self.jobs[u]
                            withdrawn.add(u)
                    if withdrawn:
                        self.urls[:] = [u for u in self.urls if u not in withdrawn]
                        mark_status_changed()
                sys.stderr.write(f"  queue: {len(new_urls)} new URL(s) not persisted "
                                 f"({type(e).__name__}: {e}); {len(withdrawn)} withdrawn\n")
                raise
            # Persist restored import statuses, one bulk update per (status, message).
            restored={}
            for u in new_urls:
                if u in restored_urls:
                    restored.setdefault(restore_status[u], []).append(u)
            for (r_status, r_msg), r_urls in restored.items():
                try:
                    queue_bulk_update(self.site_id, r_urls, status=r_status, message=r_msg)
                except sqlite3.Error as e:
                    self.log.warning("queue_bulk_update (restored) failed: %s", e)
            # Persist done-state for folder-scan pre-marked URLs in ONE
            # bulk update (they all carry the same "already on disk"
            # message) — previously a per-URL queue_upsert loop.
            done_urls=[u for u in new_urls if u not in restored_urls
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
                f"Imported {added} URL(s); {dupes} duplicate(s); {skipped_on_disk} skipped (already on disk)"
                + (f"; {refused} refused (content rights)" if refused else "")
                + (f"; {invalid} invalid (not an http(s) URL with a host)" if invalid else "")
                + (f"; {len(conflicts)} duplicate(s) with different headers (kept the queued job's)"
                   if conflicts else ""),
                extra={"added": added, "dupes": dupes, "skipped": skipped_on_disk,
                       "refused": refused, "invalid": invalid,
                       "header_conflicts": len(conflicts)})
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
        # idle-site-wake r1 (vip4k 2026-10-14): the jobs this call created or
        # re-armed reach a worker without an operator Start. load_urls never
        # fed _url_queue (start() is its only producer), so an import into a
        # running or drained site sat "pending" until a service restart.
        if added:
            try:
                self._wake_for_new_jobs(list(new_urls) + list(rearmed))
            except Exception as e:
                self.log.warning("wake for new jobs failed: %s", e)
        # F9 (S11): refusals are reported apart from duplicates in the import
        # event above; slot 1 of the return stays "not queued" (dupes +
        # refused) because callers unpack exactly three values and existing
        # rights tests pin a refusal there. F8/F11: invalid URLs and header
        # conflicts ride as attributes (LoadResult); an invalid URL is not in
        # slot 1, it is not a duplicate.
        return LoadResult(added, dupes+refused, skipped_on_disk,
                          invalid=invalid, header_conflicts=len(conflicts))
    def reorder_urls(self,ordered):
        with self._lock:
            ex=set(self.urls)
            self.urls[:]=[u for u in ordered if u in ex]
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
            self.urls[:]=[u for u in self.urls if u not in urls]
            if removed:
                mark_status_changed()
        # dl-f4: deleting the teach target must not strand parked URLs.
        # Lightweight QueueMixin hosts carry no TeachMixin and park nothing.
        release = getattr(self, "_release_teach_waiters_if_unblocked", None)
        if release is not None:
            release()
        try:
            queue_bulk_delete(self.site_id, list(urls))
        except Exception as del_exc:
            sys.stderr.write(f"  queue: {len(urls)} removed job(s) not deleted from the "
                             f"queue table ({type(del_exc).__name__})\n")
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
        Sets force_download=True on each job and re-queues as pending.

        dl-runner #2: only needs_review jobs are approved, like the sibling
        verbs (bulk_pause: pending, bulk_resume: stopped). A stale review list
        must not reset a running or done job to pending with force_download."""
        urls=set(urls); n=0; approved=[]
        with self._job_status_writer() as mark_status_changed:
            for u in urls:
                j=self.jobs.get(u)
                if not j: continue
                if j.get("status") != "needs_review": continue
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
        except Exception as e:
            self._log_queue_persist_failure("bulk_pause", paused, e)
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
        except Exception as e:
            self._log_queue_persist_failure("bulk_resume", resumed, e)
        return n
    def retryable_urls(self, urls):
        """The given URLs whose existing job bulk_retry would re-queue (in
        input order, once each). load_urls counts such a URL as a dupe and
        leaves the failed job alone; callers use this to say so and offer
        the retry."""
        with self._lock:
            return [u for u in dict.fromkeys(urls)
                    if (self.jobs.get(u) or {}).get("status") in BULK_RETRY_STATUSES]
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
                if j.get("status") not in BULK_RETRY_STATUSES: continue
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
            self.urls[:] = front + tail
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
        applied = []
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
                applied.append((old_url, new_url))
        # Persist (outside the lock — DB has its own locking, slow ops here
        # would block the worker if held inside the runner lock)
        try:
            with db_conn() as cx:
                for old_url, new_url in applied:
                    # Only renames applied in memory are persisted: a skipped pair
                    # (target already queued) must leave its old row intact.
                    cx.execute(
                        "UPDATE OR IGNORE queue SET url = ? "
                        "WHERE site_id = ? AND url = ?",
                        (new_url, self.site_id, old_url))
                    # The table can already hold new_url while memory did not
                    # (the UPDATE was IGNOREd): drop the old row so it does not
                    # come back as a second job on reload.
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
        """Drop URLs in `done` status from both the in-memory job map and the
        queue table. History rows (in the separate `history` table) are NOT
        affected — those persist for audit/reporting.

        ddr-fix F1: `stopped` is unfinished work (bulk_pause "Paused by user",
        Stop's "Stopped"); clearing completed jobs must keep it."""
        with self._job_status_writer() as mark_status_changed:
            removed=[u for u,j in self.jobs.items() if j["status"]=="done"]
            # Row 1021: mutate in place -- runner.jobs/urls alias queue_subsystem.jobs/urls.
            for u in removed: del self.jobs[u]
            self.urls[:]=[u for u in self.urls if u not in removed]
            if removed:
                mark_status_changed()
        try:
            queue_delete_status(self.site_id,"done")
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
        except Exception as e:
            self._log_queue_persist_failure("retry_failed", retried, e)
    def _log_queue_persist_failure(self, verb, urls, exc):
        """ddr-fix F7: a bulk verb's queue write failed. Memory already moved,
        so say so: a restart restores the queue table's old status."""
        self.log.warning("%s: %d job(s) changed in memory only, queue table "
                         "not updated (%s: %s); a restart reverts them",
                         verb, len(urls), type(exc).__name__, exc)
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

    def filter_jobs_by_paused_lanes(self, jobs):
        """Row 935: Filter jobs whose lane is currently paused due to challenge detection."""
        from .challenge_detector import filter_jobs_by_paused_lanes as _filter_jobs
        return _filter_jobs(jobs)

    def is_lane_paused(self, lane=None):
        """Row 935: Query if site lane is paused."""
        from .challenge_detector import is_lane_paused as _is_paused
        return _is_paused(lane or self.site_id)
