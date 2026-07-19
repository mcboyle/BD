"""T11 (v3.66.264) — pending-approval PERSIST + self-clearing reader.

The auto-submit / post-reveal approval candidates a deep_detect run
surfaces are ephemeral — before this, only the operator's *decisions*
survived a run, so the React SPA had no per-site source to render the
approval gate from. T11 adds:

  * learn.record_pending_approvals(config, report) — persists the
    CURRENT pending candidates into learned.deep_detect.pending_approvals
    (markers only, no secret value — F2).
  * learn.pending_approvals(config) — returns the undecided pending list,
    self-clearing any entry whose key already has a recorded decision.

RED on pristine v3.66.263 (proven before implementing): both functions
do not exist yet — record_pending_approvals / pending_approvals raise
AttributeError.

run_tests.py conventions: zero-arg functions; repo root via __file__;
no pytest builtins.
"""
from pathlib import Path
import sys

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_BOT_FORM = (
    '<html><body><form method="POST" action="/login">'
    '<input name="username"><input type="password" name="pw">'
    '<div class="cf-turnstile" data-sitekey="x"></div>'
    '<button>Sign in</button></form></body></html>'
)


def _report_with_pending_login():
    from bulk_downloader import deep_detect as dd
    return dd.deep_detect(_BOT_FORM, base_url="https://site.test/")


def test_record_pending_persists_a_pending_login():
    from bulk_downloader import learn
    cfg = {}
    rep = _report_with_pending_login()
    learn.record_pending_approvals(cfg, rep, base_url="https://site.test/")
    pend = learn.pending_approvals(cfg)
    assert len(pend) == 1, pend
    e = pend[0]
    assert e["surface"] == "auto_submit"
    assert e["key"] == "site.test/login"
    # kind/why carry the marker LABEL, not a secret value.
    assert "turnstile" in (e["kind"] + " " + e["why"]).lower()


def test_pending_reader_self_clears_on_approve():
    """An approve recorded against the same key removes the gate row on
    the next read — no fresh deep_detect run needed."""
    from bulk_downloader import learn
    cfg = {}
    rep = _report_with_pending_login()
    learn.record_pending_approvals(cfg, rep, base_url="https://site.test/")
    assert len(learn.pending_approvals(cfg)) == 1
    learn.record_auto_submit_decision(cfg, "site.test/login", "approve")
    assert learn.pending_approvals(cfg) == [], (
        "approved entry must self-clear from the pending read")


def test_pending_reader_self_clears_on_decline():
    from bulk_downloader import learn
    cfg = {}
    rep = _report_with_pending_login()
    learn.record_pending_approvals(cfg, rep, base_url="https://site.test/")
    learn.record_auto_submit_decision(cfg, "site.test/login", "decline")
    assert learn.pending_approvals(cfg) == [], (
        "declined entry must self-clear from the pending read")


def test_record_pending_is_idempotent():
    """Re-running the same report must not duplicate the entry."""
    from bulk_downloader import learn
    cfg = {}
    rep = _report_with_pending_login()
    learn.record_pending_approvals(cfg, rep, base_url="https://site.test/")
    learn.record_pending_approvals(cfg, rep, base_url="https://site.test/")
    assert len(learn.pending_approvals(cfg)) == 1


def test_record_pending_no_secret_value_stored():
    """The persisted block must hold marker labels only — no form input
    values, cookies, tokens, or sitekeys (F2)."""
    from bulk_downloader import learn
    import json
    cfg = {}
    rep = _report_with_pending_login()
    learn.record_pending_approvals(cfg, rep, base_url="https://site.test/")
    blob = json.dumps(cfg["learned"]["deep_detect"]["pending_approvals"])
    # the data-sitekey value from the form must never be persisted
    assert "data-sitekey" not in blob
    assert '"x"' not in blob  # the sitekey value
    assert "password" not in blob.lower()


def test_record_pending_tolerates_garbage():
    from bulk_downloader import learn
    assert learn.record_pending_approvals(None, {}) is None
    assert learn.record_pending_approvals({}, None) == {}
    assert learn.pending_approvals(None) == []
    assert learn.pending_approvals({}) == []


def test_record_pending_bounded():
    """The pending set is capped (oldest evicted) so a churny page can't
    grow the config unboundedly."""
    from bulk_downloader import learn
    cap = learn._DD_MAX_PENDING
    cfg = {"learned": {"deep_detect": learn._dd_init_block()}}
    pend = cfg["learned"]["deep_detect"]["pending_approvals"]
    for i in range(cap + 5):
        pend["auto_submit|h%d.test/login" % i] = {
            "surface": "auto_submit", "key": "h%d.test/login" % i,
            "kind": "challenge", "why": "x",
            "at": "2026-01-%02dT00:00:00" % ((i % 28) + 1)}
    # a fresh record call must trim back to <= cap
    rep = _report_with_pending_login()
    learn.record_pending_approvals(cfg, rep, base_url="https://site.test/")
    total = len(cfg["learned"]["deep_detect"]["pending_approvals"])
    assert total <= cap, "pending_approvals not bounded: %d > %d" % (total, cap)


# ── HTTP surface: GET /api/sites/<sid>/pending_approvals ─────────────

import pytest  # noqa: E402

pytestmark = pytest.mark.bd_module_wipe


def _client_with_site(sid="t11r.test"):
    from bulk_downloader.db import db_init
    from bulk_downloader import app as A
    from bulk_downloader import learn
    db_init()
    cfg = {"url": "https://%s/" % sid,
           "learned": {"deep_detect": learn._dd_init_block()}}
    A.s_cfg[sid] = cfg
    return A, A.app.test_client(), sid


def test_pending_route_unknown_site_404():
    A, c, _ = _client_with_site()
    try:
        r = c.get("/api/sites/does-not-exist/pending_approvals")
        assert r.status_code == 404, r.get_json()
    finally:
        A.s_cfg.pop("t11r.test", None)


def test_pending_route_returns_pending_then_self_clears():
    """The route surfaces a persisted pending candidate, and a recorded
    decision removes it on the next GET — the exact loop the SPA gate
    drives (read -> approve -> re-read shows it gone)."""
    from bulk_downloader import learn
    A, c, sid = _client_with_site(sid="t11route.test")
    try:
        rep = _report_with_pending_login()
        learn.record_pending_approvals(
            A.s_cfg[sid], rep, base_url="https://site.test/")
        r = c.get("/api/sites/%s/pending_approvals" % sid)
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["ok"] is True
        assert body["count"] == 1, body
        assert body["pending"][0]["surface"] == "auto_submit"
        key = body["pending"][0]["key"]

        # decide it -> next GET self-clears
        learn.record_auto_submit_decision(A.s_cfg[sid], key, "approve")
        r2 = c.get("/api/sites/%s/pending_approvals" % sid)
        assert r2.get_json()["count"] == 0, r2.get_json()
    finally:
        A.s_cfg.pop("t11route.test", None)
