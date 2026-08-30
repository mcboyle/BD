"""v3.66.820 (#36) -- deleting a site must reap its auth_health row.

`auth_health` is a LAST-KNOWN-STATE row that is read as a CURRENT signal:
`cookie_health.status_all()` feeds /api/auth_health/status, and
`app_data_layer.collect_site_health()` builds its site set as a UNION of
auth_health keys and session-history keys with no intersection against
the configured sites. So a row that outlives its site is a permanent
phantom site in the Site Health report -- it never ages out, unlike the
session-history arm which expires at `lookback_days`.

That is why THIS table gets a reaper and the neighbouring site-keyed
tables do not. `history`, `session_history` and `retention_audit` are
historical records that `dev_suite.db_tools.orphan_rows` (D-7) documents
as retained BY DESIGN. Do not "consistently" reap the audit trail.

Two guards here are green on pristine source and stay green after the
fix. They are not RED tests and are not counted as such; they exist to
catch an over-firing fix:

  * test_api_delete_leaves_other_sites_auth_health_intact -- catches an
    unscoped `DELETE FROM auth_health`.
  * test_queue_replace_does_not_reap_auth_health -- catches the tempting
    "DRY" fix of hanging the reap off db.queue_delete_site, which is
    ALSO the queue-REPLACE primitive for LIVE sites (app_sites_queue.py,
    app_queue_templates.py, perf_lab.py -- AST-measured Call nodes with
    callee-name == "queue_delete_site"). That fix would destroy a live
    site's auth health on every queue replace, which is strictly worse
    than the phantom row it removes.

The module wipe is required: cookie_health caches `_TABLE_READY` at
module scope, so without it a later test resolves a fresh tmp DB with
the flag already True, `_ensure_table` is skipped, and `_record` (which
swallows) silently writes nothing.
"""
import logging

import pytest

pytestmark = pytest.mark.bd_module_wipe


def _bd():
    """Import the package fresh. The per-test module wipe drops every
    `bulk_downloader.*` from sys.modules, so module-level imports in this
    file would hand back stale module objects."""
    import bulk_downloader.app_state as st
    from bulk_downloader.app import app
    from bulk_downloader import app_sites_id_core as core
    from bulk_downloader import cookie_health as ch
    from bulk_downloader import db as _db
    return st, app, core, ch, _db


def _ids(ch):
    return {r["site_id"] for r in ch.status_all()}


class _FakeRunner:
    """Minimal stand-in for SiteRunner: the bulk delete branch skips any
    sid that is not in `runners`, so a runner object is required to reach
    the teardown at all."""
    def retire_scheduler(self, timeout=12.0):
        return True

    def retire_auto_retry(self, timeout=2.0):
        return True

    def retire_workers(self, timeout=5.0):
        self.stop()
        return True

    def stop(self):
        pass

    def _stop_auto_retry(self):
        pass


# ---------------------------------------------------------------- reaper


def test_forget_site_removes_only_the_named_row():
    _st, _app, _core, ch, _db = _bd()
    ch._record("bd36_a", status="green", note="seed")
    ch._record("bd36_b", status="red", note="seed")
    assert {"bd36_a", "bd36_b"} <= _ids(ch), (
        "seeding failed -- the rows were never written, so nothing below "
        "would be evidence about the reaper")

    removed = ch.forget_site("bd36_a")

    assert removed == 1, f"expected 1 row reaped, got {removed!r}"
    assert _ids(ch) == {"bd36_b"}, (
        "forget_site must remove exactly the named site's row")


def test_forget_site_reports_zero_when_there_was_no_row():
    _st, _app, _core, ch, _db = _bd()
    ch._record("bd36_present", status="green", note="seed")
    assert "bd36_never_checked" not in _ids(ch)

    assert ch.forget_site("bd36_never_checked") == 0, (
        "a site with no row must report 0 reaped, not a fabricated count")
    assert ch.forget_site("bd36_present") == 1
    assert _ids(ch) == set()


def test_forget_site_does_not_swallow_a_db_failure(monkeypatch):
    """A swallowed DB failure would return the same 0 as a clean no-op,
    so the caller could not tell "nothing to reap" from "the reap did not
    happen". The caller decides what an unverifiable reap means -- and
    test_api_delete_logs_when_the_reap_fails pins that it decides to say
    so out loud."""
    _st, _app, _core, ch, _db = _bd()

    def _boom():
        raise RuntimeError("db is gone")

    monkeypatch.setattr(ch._db, "db_conn", _boom)
    with pytest.raises(RuntimeError, match="db is gone"):
        ch.forget_site("bd36_whatever")


# ------------------------------------------------------- the delete paths


def test_api_delete_reaps_auth_health_row():
    _st, app, core, ch, _db = _bd()
    gone = "bd36_gone"
    ch._record(gone, status="red", note="seed")
    assert gone in _ids(ch), "seeding failed"
    _st.s_cfg[gone] = {"url": "http://example.invalid", "name": "gone"}
    _st.s_meta[gone] = {"status": "idle"}
    _st.runners.pop(gone, None)          # idle: no runner, still a real delete

    with app.test_request_context(f"/api/sites/{gone}", method="DELETE"):
        core.api_delete(gone)

    assert gone not in _st.s_cfg, (
        "precondition failed: the site was never deleted, so the row "
        "surviving proves nothing about the reap")
    assert gone not in _ids(ch), (
        "deleted site kept its auth_health row -- a permanent phantom "
        "site in the Site Health report")


def test_api_delete_leaves_other_sites_auth_health_intact():
    """Green on pristine (nothing is reaped at all today). Guard against
    an unscoped `DELETE FROM auth_health`."""
    _st, app, core, ch, _db = _bd()
    gone, live = "bd36_scope_gone", "bd36_scope_live"
    ch._record(gone, status="red", note="seed")
    ch._record(live, status="green", note="seed")
    assert {gone, live} <= _ids(ch), "seeding failed"
    for sid in (gone, live):
        _st.s_cfg[sid] = {"url": "http://example.invalid", "name": sid}
        _st.s_meta[sid] = {"status": "idle"}
    _st.runners.pop(gone, None)

    with app.test_request_context(f"/api/sites/{gone}", method="DELETE"):
        core.api_delete(gone)

    assert live in _ids(ch), (
        "deleting one site destroyed another site's auth health -- the "
        "reap is unscoped")


def test_bulk_delete_reaps_auth_health_row(monkeypatch):
    _st, app, core, ch, _db = _bd()
    gone = "bd36_bulk_gone"
    ch._record(gone, status="red", note="seed")
    assert gone in _ids(ch), "seeding failed"
    _st.s_cfg[gone] = {"url": "http://example.invalid", "name": "gone"}
    _st.s_meta[gone] = {"status": "idle"}
    _st.runners[gone] = _FakeRunner()    # bulk skips any sid with no runner
    monkeypatch.setattr(core, "_check_csrf", lambda *a, **k: None)
    monkeypatch.setattr(core, "_rate_check", lambda *a, **k: True)

    with app.test_request_context(
            "/api/sites/v2/bulk", method="POST",
            json={"action": "delete", "site_ids": [gone]}):
        core.api_sites_v2_bulk()

    assert gone not in _st.s_cfg, (
        "precondition failed: the bulk handler never ran the delete "
        "branch for this sid")
    assert gone not in _ids(ch), (
        "api_sites_v2_bulk inlines the whole teardown a second time and "
        "must carry the reap too -- a reap in one path only is a half-fix")


def test_site_health_report_drops_the_deleted_site():
    """The operator-visible consequence, end-to-end through the real
    report provider rather than through the table."""
    _st, app, core, ch, _db = _bd()
    from bulk_downloader import app_data_layer as adl
    _db.db_init()
    gone, live = "bd36_rep_gone", "bd36_rep_live"
    for sid in (gone, live):
        ch._record(sid, status="green", note="seed")
        _st.s_cfg[sid] = {"url": "http://example.invalid", "name": sid}
        _st.s_meta[sid] = {"status": "idle"}
    _st.runners.pop(gone, None)

    before = adl.collect_site_health()
    assert before["site_count"] == 2, (
        "precondition failed: the report cannot see both sites, so its "
        "silence about one of them afterwards would prove nothing "
        f"(saw {[s['site_id'] for s in before['sites']]})")

    with app.test_request_context(f"/api/sites/{gone}", method="DELETE"):
        core.api_delete(gone)

    after = [s["site_id"] for s in adl.collect_site_health()["sites"]]
    assert gone not in after, (
        f"deleted site is still a row in the Site Health report: {after}")
    assert live in after, "the surviving site fell out of the report"


def test_queue_replace_does_not_reap_auth_health():
    """Green on pristine and after the fix. Guard against the "DRY" fix
    of hanging the reap off db.queue_delete_site, which is also the
    queue-REPLACE primitive for LIVE sites."""
    _st, app, core, ch, _db = _bd()
    _db.db_init()
    live = "bd36_queue_live"
    ch._record(live, status="green", note="seed")
    assert live in _ids(ch), "seeding failed"

    _db.queue_delete_site(live)          # what a queue "replace" does

    assert live in _ids(ch), (
        "clearing a LIVE site's queue destroyed its auth health -- the "
        "reap is wired to the queue primitive instead of the site delete")


# ----------------------------------------------------- observability (C1)


def test_api_delete_logs_when_the_reap_fails(monkeypatch):
    """`forget_site` deliberately does not swallow, but both call sites
    sit inside the delete teardown's `try/except` so a DB failure cannot
    turn a successful delete into a 500. That only stays honest if the
    failure is written down: otherwise the route returns {"ok": True},
    the site leaves s_cfg, the orphan survives, and nothing anywhere
    records that the reap did not happen."""
    _st, app, core, ch, _db = _bd()

    def _boom(_sid):
        raise RuntimeError("bd36 reap exploded")

    monkeypatch.setattr(ch, "forget_site", _boom, raising=False)

    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture()
    # bulk_downloader.log sets propagate=False on the package logger, so a
    # root-attached capture (caplog) would see nothing. Attach here.
    pkg_logger = logging.getLogger("bulk_downloader")
    pkg_logger.addHandler(handler)
    sid = "bd36_log_probe"
    try:
        _st.s_cfg[sid] = {"url": "http://example.invalid", "name": sid}
        _st.s_meta[sid] = {"status": "idle"}
        _st.runners.pop(sid, None)
        with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            resp = core.api_delete(sid)
    finally:
        pkg_logger.removeHandler(handler)

    status = resp[1] if isinstance(resp, tuple) else getattr(
        resp, "status_code", 200)
    assert status == 200, "a failed reap must not turn the delete into an error"
    assert sid not in _st.s_cfg, "precondition failed: the site was not deleted"

    hits = [r for r in records
            if r.levelno >= logging.WARNING
            and sid in r.getMessage()]
    assert hits, (
        "the reap failed and nothing was logged -- 'the reap did not "
        "happen' is byte-indistinguishable from 'there was nothing to "
        f"reap'. Captured: {[r.getMessage() for r in records]}")


# ---------------------------------------------------------- source parity


@pytest.mark.parametrize("handler_name",
                         ["api_delete", "api_sites_v2_bulk"])
def test_both_delete_paths_carry_the_reap(handler_name):
    """Both delete paths inline the same teardown; a reap in only one of
    them is a half-fix. Presence, not placement -- an ill-placed reap is
    caught by the behavioural tests above, not here."""
    import inspect
    from bulk_downloader import app_sites_id_core as core
    handler = getattr(core, handler_name)      # raises loudly on a rename
    body = inspect.getsource(handler)
    if handler_name == "api_delete":
        # The public route is a striped-lock wrapper; the transaction remains
        # the implementation surface whose cleanup parity this guard audits.
        body += inspect.getsource(core._api_delete_transaction)
    assert body.strip(), f"{handler_name} source came back empty"
    assert "forget_site(sid)" in body, (
        f"{handler_name} does not reap auth_health")
