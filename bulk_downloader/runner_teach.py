"""runner_teach -- teach/learn selector drafts + auto-teach

Extracted from runner.py (SiteRunner) @v3.66.399, PHASE 3 runner cut 3.
Mixin: methods reference self.* only; NO __init__. Import block derived by AST
free-name scan of the moved bodies (the seams doc omitted the scrapling
conditional). Cycle rule: imports nothing from .runner.
"""
import sys, time


# scrapling_adapter soft import (moved verbatim from runner.py; flat sibling so
# `.`=bulk_downloader is unchanged). Provides _scrap + _SCRAPLING_AVAILABLE.
try:
    from . import scrapling_adapter as _scrap
    _SCRAPLING_AVAILABLE = True
except Exception as _e:
    sys.stderr.write(f"[runner_teach] scrapling_adapter import failed (degraded): {_e}\n")
    _scrap = None
    _SCRAPLING_AVAILABLE = False


# v3.66.511 (Addendum A2): a draft-test override (set via /api/template/
# test_extract) persists in the site config across restarts and is the ONLY way
# an UNREVIEWED draft drives real downloads. Without expiry, a forgotten override
# keeps a site running off an unreviewed draft indefinitely. Bound it: past this
# age the override is treated as inert (downloads fall back to the normal
# reviewed-template path). The operator re-issues test_extract to keep testing.
_DRAFT_OVERRIDE_TTL_SECONDS = 24 * 3600


def _draft_override_is_fresh(ov, *, now=None):
    """True iff ``ov`` is a usable, non-expired draft-test override dict.

    Fail-open on a missing/unparseable ``set_at`` (treat as fresh) so a missing
    timestamp never silently disables a deliberately-set override -- expiry only
    fires when we can *prove* the override is older than the TTL.
    """
    if not isinstance(ov, dict):
        return False
    t = ov.get("template")
    if not (isinstance(t, dict) and t):
        return False
    set_at = ov.get("set_at")
    if not isinstance(set_at, (int, float)) or isinstance(set_at, bool):
        return True  # no usable timestamp -> cannot prove stale -> keep
    if now is None:
        now = time.time()
    return (now - set_at) <= _DRAFT_OVERRIDE_TTL_SECONDS


class TeachMixin:
    def _teach_base_url(self):
        """The URL the takeover browser uses to hit teach_* endpoints. The
        browser runs on the same host as Flask, so http://127.0.0.1:5555
        always works. Allow override via app_config in case a user runs
        Flask on a different port."""
        try:
            from . import app as _app
            port=int(_app._app_cfg.get("port",5555) or 5555)
        except Exception: port=5555
        return f"http://127.0.0.1:{port}"
    def teach_verify(self, picks):
        """Phase 10: dry-run picked selectors against the page that's
        currently loaded in the takeover browser.

        Phase 41.3: dispatches via the session thread since Playwright
        is thread-bound; calling ctx.pages from a different thread
        would raise.

        `picks` should look like {row_selectors:[...], trigger_selectors:[...],
        url_attribute:'data-href'}. Returns (ok, detail_dict)."""
        session = getattr(self, "_manual_download_session", None)
        if not session: return False, {"error": "No takeover session active"}
        return session.verify(picks)
    def teach_test_download(self, picks):
        """v3.43.0: extend Verify by actually fetching ~2 MB of the URL
        the picks would resolve to. Confirms the selector pulls a real
        video file (not an HTML error page, not an expired-token 403,
        not a login redirect). Returns (ok, detail_dict). Detail includes
        extracted_url, http_status, content_type, magic_kind, magic_ok."""
        session = getattr(self, "_manual_download_session", None)
        if not session: return False, {"error": "No takeover session active"}
        return session.test_download(picks)
    def teach_commit(self, picks, raw_events=None):
        """Phase 10: persist the user's picked selectors and close the
        takeover browser. Different from finish_manual_download because
        the user has explicitly curated the selectors via the panel — we
        trust their picks rather than re-classifying from raw events.

        Phase 41.3: dispatches to the session thread for cookie capture
        and browser close (Playwright is thread-bound)."""
        session = getattr(self, "_manual_download_session", None)
        if not session: return False, "No takeover session active"
        self._manual_download_session = None
        target_url = session.target_url

        # Snapshot cookies + close the browser via the session thread
        ok_close, new_cookies = session.commit(timeout=15)
        if not ok_close:
            self.log.warning("teach_commit: session commit returned not-ok; "
                             "selectors will still be saved")
            new_cookies = []

        # Persist refreshed cookies if any were captured
        try:
            if new_cookies:
                self.set_cookies(new_cookies)
                cf = self.config.get("cookie_file", "")
                if cf:
                    try:
                        from .cookies import save_cookies_to_file
                        save_cookies_to_file(cf, new_cookies)
                    except (OSError, ValueError) as e:
                        self.log.warning("post-takeover cookie save to %s failed: %s", cf, e)
        except Exception as e:
            self.log.debug("post-takeover cookie persist failed: %s", e)

        # Persist picks
        try:
            from .learn import merge_learned, classify_download
            sels = {}
            if raw_events:
                try: sels = classify_download({"clicks": raw_events}) or {}
                except Exception: sels = {}
            if picks.get("row_selectors"): sels["row_selectors"] = picks["row_selectors"]
            if picks.get("trigger_selectors"): sels["trigger_selectors"] = picks["trigger_selectors"]
            if picks.get("url_attribute"): sels["url_attribute"] = picks["url_attribute"]
            n_roles = sum(1 for v in sels.values() if v)
            if n_roles and not self._override_suppresses_persist():
                merge_learned(self.config, sels, kind="download")
                try:
                    from . import app as _app
                    if self.site_id in _app.s_cfg:
                        _app.s_cfg[self.site_id] = self.config
                        _app.s_meta[self.site_id] = _app._build_meta(self.config)
                        _app._save_sites_config()
                    self._persist_learned_to_draft()  # B2: persist toggle ON -> draft
                except Exception as e:
                    self.log.error("teach commit persist failed: %s", e)
            sys.stderr.write(f"  teach commit: saved {n_roles} role(s); picks={picks}\n")
        except Exception as e:
            self.log.error("teach commit classify failed: %s", e)

        # Mark target URL done
        try:
            self._update_job(target_url, "done",
                             "Teach Mode commit — selectors saved",
                             filename="(teach)")
        except Exception: pass

        # Phase 41.2: re-enqueue URLs blocked on auto_teach
        self._auto_teach_logged = False
        with self._job_status_writer() as mark_status_changed:
            changed = False
            for u, j in self.jobs.items():
                if j.get("auto_teach_seen") and j.get("status") == "needs_review":
                    j["auto_teach_seen"] = False
                    j["status"] = "pending"
                    j["message"] = "Queued after teach completion"
                    try: self._url_queue.put_nowait(u)
                    except Exception: pass
                    changed = True
            if changed:
                mark_status_changed()
        # Phase 41.5: spawn workers now that selectors are learned and
        # pending URLs exist. start() is idempotent.
        try: self.start()
        except Exception as e:
            self.log.warning("post-teach_commit start() failed: %s", e)

        self._login_status = "✓ Teach Mode commit"
        return True, "Selectors saved"
    def teach_cancel(self):
        """Same as cancel_manual_download but uses a cleaner status
        message reflecting the Teach Mode UX."""
        session = getattr(self, "_manual_download_session", None)
        if not session: return False, "No takeover session active"
        self._manual_download_session = None
        target_url = session.target_url
        try:
            session.cancel(timeout=10)
        except Exception as e:
            self.log.debug("teach_cancel: session close error: %s", e)
        # Phase 41.2: clear auto_teach state on the URL
        try:
            with self._job_status_writer() as mark_status_changed:
                if target_url in self.jobs:
                    j = self.jobs[target_url]
                    if j.get("auto_teach_seen"):
                        j["auto_teach_seen"] = False
                        j["status"] = "pending"
                        j["message"] = "Cancelled — retry to resume teach flow"
                        try: self._url_queue.put_nowait(target_url)
                        except Exception: pass
                        mark_status_changed()
            self._auto_teach_logged = False
        except Exception: pass
        self._login_status = "✗ Teach Mode cancelled"
        return True, "Cancelled"
    def _recover_selector(
        self, html: str, kind: str = "download",
    ):
        """v3.43.73: when all learned selectors fail at runtime, attempt
        to recover one via Scrapling's content-based fingerprint match.

        `kind` is one of the keys under `config["learned"]`: "download",
        "login", "auth", etc. The fingerprints are stored under
        `learned[kind]["fingerprints"]` as a dict keyed by the original
        selector string.

        Returns a NEW selector string that finds the element on the
        current page, or None if recovery isn't possible (Scrapling not
        installed, no fingerprints stored, no candidates above the
        score threshold).

        Bumps the runner's selector_recoveries counter on success.
        """
        if not (_SCRAPLING_AVAILABLE and _scrap is not None):
            return None
        if not self.config.get("use_scrapling_recovery", False):
            return None
        if not html:
            return None
        learned = self.config.get("learned") or {}
        kind_block = learned.get(kind) or {}
        fingerprints = kind_block.get("fingerprints") or {}
        if not isinstance(fingerprints, dict) or not fingerprints:
            return None
        # Try each stored fingerprint; return on first successful recovery
        for orig_selector, fp in fingerprints.items():
            if not isinstance(fp, dict):
                continue
            try:
                result = _scrap.recover_selector(html, fp)
            except Exception as e:
                sys.stderr.write(
                    f"[{self.site_id}] scrapling recover raised: {e}\n")
                continue
            if result.ok and result.selector:
                self.log_event(
                    "selector_recovered",
                    f"orig={orig_selector!r} -> new={result.selector!r} "
                    f"score={result.score:.2f} "
                    f"considered={result.candidates_considered}",
                )
                return result.selector
        return None
    def _draft_override_template(self):
        """B2 (v3.66.240): the per-site draft-test override template, or None.

        Set via ``POST /api/template/test_extract``; stored under the site
        config key ``draft_test_override`` so it rides ``_save_sites_config``
        and persists per-site across restarts (Decision 4). This is the ONLY
        way an UNREVIEWED draft reaches ``merge_template_download_hints`` — the
        enabled-only matcher (``find_template_for_url``) still cannot return a
        draft. The override is a separate branch, never a gate relaxation.
        """
        ov = self.config.get("draft_test_override")
        if _draft_override_is_fresh(ov):
            return ov["template"]
        return None
    def _override_suppresses_persist(self):
        """B2 (Decision 2): True when a draft-test override is active AND its
        persist-bypass toggle is OFF (the default) -> this run must NOT persist
        learned selectors to the live site config OR back onto the draft. No
        override, or the toggle ON, returns False (normal persistence). This is
        the run-scoped no-persist guard that gates every learned-selector
        persist chokepoint (login takeover, download takeover, teach-commit,
        drift-recovery) for the override path.
        """
        ov = self.config.get("draft_test_override")
        if not _draft_override_is_fresh(ov):
            return False
        return not bool(ov.get("persist"))
    def _persist_learned_to_draft(self):
        """B2 (Decision 2, persist toggle ON): copy this run's learned block
        back onto the source draft JSON in ``templates/drafts/``, under the
        draft's own ``learned`` key (the same shape sites carry). The draft's
        AUTHORED selectors are left intact; the learned block is additive, so a
        re-derive can never corrupt the draft. No-op unless persist is ON and a
        ``draft_file`` was recorded on the override.
        """
        ov = self.config.get("draft_test_override")
        if not isinstance(ov, dict) or not ov.get("persist"):
            return
        draft_file = ov.get("draft_file")
        if not draft_file:
            return
        try:
            from .template_manager import DRAFTS_DIR, _safe_name, _DRAFT_SUFFIX
            from pathlib import Path
            import json as _json
            safe = _safe_name(draft_file, _DRAFT_SUFFIX)
            if not safe:
                return
            fp = Path(DRAFTS_DIR) / safe
            if not fp.is_file():
                return
            d = _json.loads(fp.read_text("utf-8"))
            d["learned"] = self.config.get("learned", {})
            d["test_extract_learned_at"] = int(time.time())
            fp.write_text(_json.dumps(d, indent=2, sort_keys=True),
                          encoding="utf-8")
            self.log.info("test_extract: learned block written back to draft %s",
                          safe)
        except Exception as e:
            self.log.error("test_extract draft writeback failed: %s", e)
    def _handle_auto_teach_check(self, url, job):
        """Phase 19 auto-teach for the first URL: if the site has no learned
        download selectors yet, route ONE URL to needs_review so the user can
        take over and teach. The remaining URLs stay pending (Phase 41.2)
        so we don't flood needs_review with 2000+ items on first import.

        Returns True if the URL was handled (caller should return),
        False to continue normal processing. Behavior identical to the
        inline block extracted from _process_one in v3.43.18."""
        if not self.config.get("auto_teach_first_run", True):
            return False
        learned_dl = (self.config.get("learned") or {}).get("download") or {}
        has_dl = bool(learned_dl.get("trigger_selectors") or learned_dl.get("row_selectors"))
        already_flagged = job.get("auto_teach_seen", False)
        if has_dl or already_flagged:
            return False
        deferred = False
        selected = False
        selected_prev_status = None
        teach_message = (
            "Auto-teach: take over to teach download selectors. "
            "Click the download button by hand, then 'I'm Done'.")
        with self._job_status_writer() as mark_status_changed:
            others_in_teach = any(
                j.get("status") == "needs_review" and j.get("auto_teach_seen")
                for u, j in self.jobs.items() if u != url
            )
            if others_in_teach:
                current = self.jobs.get(url)
                if current and current.get("status") == "running":
                    current.update({
                        "status": "pending",
                        "message": "Waiting for teach completion",
                        "ts": "",
                    })
                try: self._url_queue.put_nowait(url)
                except Exception: pass
                mark_status_changed()
                deferred = True
            else:
                current = self.jobs.get(url)
                if current:
                    selected_prev_status = current.get("status")
                    current.update({
                        "status": "needs_review",
                        "message": teach_message,
                        "ts": "",
                        "auto_teach_seen": True,
                    })
                    mark_status_changed()
                    selected = True
        if deferred:
            self._stop.wait(timeout=5.0)
            return True
        if selected:
            self._update_job(
                url, "needs_review", teach_message,
                _transition_prev_status=selected_prev_status,
                _memory_already_updated=True,
                auto_teach_seen=True)
        if not getattr(self, "_auto_teach_logged", False):
            self.log_event("auto_teach",
                "First URL needs selector teaching. Click 'Take over' to begin. "
                "Remaining URLs will pause until teaching completes.", url=url)
            self._auto_teach_logged = True
        return True
