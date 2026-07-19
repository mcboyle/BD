"""Tests for F3.1 watch rules: saved-search -> enqueue (v3.66.223).

Harness conventions: zero-arg functions, no pytest builtins, restore module
globals in try/finally. The enqueue handler is injected via the enqueue_fn
seam (run_one) or set_enqueue_handler so no app boot / pipeline is needed.
DB is the harness temp DB (BD_HOME + db_init()).
"""
import time

from bulk_downloader.db import db_init, db_log
from bulk_downloader import saved_searches as ss


def _fresh():
    db_init()
    # wipe any prior rows so name-uniqueness / counts are deterministic
    try:
        from bulk_downloader import db as _db
        with _db.db_conn() as cx:
            cx.execute("DROP TABLE IF EXISTS saved_searches")
    except Exception:
        pass
    ss._ensure_table()
    ss._reset_enqueue_handler()


# ── schema (additive columns) ────────────────────────────────────────────────
def test_schema_has_action_and_cap_with_defaults():
    _fresh()
    sid = ss.add(name="r1", query="alpha")
    assert sid is not None
    row = [r for r in ss.list_all() if r["id"] == sid][0]
    assert row["action"] == "notify"
    assert row["daily_cap"] == ss.DEFAULT_DAILY_CAP
    assert row["enqueued_total"] == 0


def test_add_bad_action_coerces_notify():
    _fresh()
    sid = ss.add(name="r2", query="alpha", action="download_now")
    row = [r for r in ss.list_all() if r["id"] == sid][0]
    assert row["action"] == "notify"


def test_add_enqueue_action_persists_and_clamps_cap():
    _fresh()
    sid = ss.add(name="r3", query="alpha", action="enqueue", daily_cap=-5)
    row = [r for r in ss.list_all() if r["id"] == sid][0]
    assert row["action"] == "enqueue"
    assert row["daily_cap"] == 0  # clamped >= 0


def test_update_action_validated():
    _fresh()
    sid = ss.add(name="r4", query="alpha")
    assert ss.update(sid, action="enqueue") is True
    assert [r for r in ss.list_all() if r["id"] == sid][0]["action"] == "enqueue"
    # bad action is dropped, not coerced -> no change reported
    assert ss.update(sid, action="bogus") is False
    assert [r for r in ss.list_all() if r["id"] == sid][0]["action"] == "enqueue"


# ── _do_enqueue: cap / dedup / provenance / no-handler ───────────────────────
def _rows(*urls):
    return [{"url": u, "filename": u.split("/")[-1]} for u in urls]


def test_do_enqueue_no_handler_silent():
    _fresh()
    sid = ss.add(name="e0", query="a", action="enqueue")
    s = [r for r in ss.list_all() if r["id"] == sid][0]
    n, capped = ss._do_enqueue(s, _rows("http://x/1"))  # no handler set
    assert (n, capped) == (0, False)


def test_do_enqueue_calls_handler_with_urls():
    _fresh()
    sid = ss.add(name="e1", query="a", action="enqueue", daily_cap=25)
    s = [r for r in ss.list_all() if r["id"] == sid][0]
    seen = {}
    def spy(urls):
        seen["urls"] = list(urls)
        return len(urls)
    n, capped = ss._do_enqueue(s, _rows("http://x/1", "http://x/2"),
                               enqueue_fn=spy)
    assert n == 2 and capped is False
    assert seen["urls"] == ["http://x/1", "http://x/2"]


def test_do_enqueue_daily_cap_limits_batch():
    _fresh()
    sid = ss.add(name="e2", query="a", action="enqueue", daily_cap=2)
    s = [r for r in ss.list_all() if r["id"] == sid][0]
    got = {}
    def spy(urls):
        got["n"] = len(urls)
        return len(urls)
    n, capped = ss._do_enqueue(
        s, _rows("http://x/1", "http://x/2", "http://x/3", "http://x/4"),
        enqueue_fn=spy)
    assert n == 2 and capped is True
    assert got["n"] == 2


def test_do_enqueue_within_batch_dedup():
    _fresh()
    sid = ss.add(name="e3", query="a", action="enqueue", daily_cap=25)
    s = [r for r in ss.list_all() if r["id"] == sid][0]
    got = {}
    def spy(urls):
        got["urls"] = list(urls)
        return len(urls)
    ss._do_enqueue(s, _rows("http://x/1", "http://x/1", "http://x/2"),
                   enqueue_fn=spy)
    assert got["urls"] == ["http://x/1", "http://x/2"]


def test_do_enqueue_persists_provenance_counters():
    _fresh()
    sid = ss.add(name="e4", query="a", action="enqueue", daily_cap=25)
    s = [r for r in ss.list_all() if r["id"] == sid][0]
    ss._do_enqueue(s, _rows("http://x/1", "http://x/2"),
                   enqueue_fn=lambda u: len(u))
    row = [r for r in ss.list_all() if r["id"] == sid][0]
    assert row["enqueued_count"] == 2
    assert row["enqueued_total"] == 2
    assert row["enqueued_day"] == ss._today_bucket()


def test_do_enqueue_counter_accumulates_same_day():
    _fresh()
    sid = ss.add(name="e5", query="a", action="enqueue", daily_cap=25)
    s = [r for r in ss.list_all() if r["id"] == sid][0]
    ss._do_enqueue(s, _rows("http://x/1"), enqueue_fn=lambda u: len(u))
    s2 = [r for r in ss.list_all() if r["id"] == sid][0]
    ss._do_enqueue(s2, _rows("http://x/2"), enqueue_fn=lambda u: len(u))
    row = [r for r in ss.list_all() if r["id"] == sid][0]
    assert row["enqueued_total"] == 2
    assert row["enqueued_count"] == 2


def test_do_enqueue_cap_exhausted_no_call():
    _fresh()
    sid = ss.add(name="e6", query="a", action="enqueue", daily_cap=1)
    s = [r for r in ss.list_all() if r["id"] == sid][0]
    ss._do_enqueue(s, _rows("http://x/1"), enqueue_fn=lambda u: len(u))
    s2 = [r for r in ss.list_all() if r["id"] == sid][0]
    called = {"n": 0}
    def spy(urls):
        called["n"] += 1
        return len(urls)
    n, capped = ss._do_enqueue(s2, _rows("http://x/2"), enqueue_fn=spy)
    assert n == 0 and capped is True
    assert called["n"] == 0  # cap exhausted -> handler not called


# ── run_one branching (notify vs enqueue) ────────────────────────────────────
def test_run_one_notify_does_not_enqueue():
    _fresh()
    sid = ss.add(name="n1", query="zzqunique", action="notify")
    db_log("s1", "Site", "http://x/zzqunique1", "done",
           filename="zzqunique1.mp4")
    called = {"n": 0}
    def spy(urls):
        called["n"] += 1
        return len(urls)
    res = ss.run_one(sid, enqueue_fn=spy)
    assert res["ok"] is True
    assert res["action"] == "notify"
    assert res["enqueued"] == 0
    assert called["n"] == 0


def test_run_one_enqueue_feeds_matches():
    _fresh()
    sid = ss.add(name="n2", query="qqxtoken", action="enqueue", daily_cap=25)
    db_log("s1", "Site", "http://x/qqxtoken-a", "failed",
           filename="qqxtoken-a.mp4")
    db_log("s1", "Site", "http://x/qqxtoken-b", "failed",
           filename="qqxtoken-b.mp4")
    got = {"urls": None}
    def spy(urls):
        got["urls"] = list(urls)
        return len(urls)
    res = ss.run_one(sid, enqueue_fn=spy)
    assert res["ok"] is True
    assert res["action"] == "enqueue"
    assert res["enqueued"] == res["new_matches"] >= 1
    assert got["urls"] is not None and len(got["urls"]) >= 1
    assert all("qqxtoken" in u for u in got["urls"])


# ── run_due passes through the registered handler ────────────────────────────
def test_run_due_uses_registered_handler():
    _fresh()
    calls = {"n": 0}
    ss.set_enqueue_handler(lambda urls: (calls.__setitem__("n", calls["n"] + len(urls)) or len(urls)))
    try:
        sid = ss.add(name="d1", query="ddwtoken", action="enqueue",
                     schedule="hourly", daily_cap=25)
        # force it due
        ss.update(sid, enabled=1)
        from bulk_downloader import db as _db
        with _db.db_conn() as cx:
            cx.execute("UPDATE saved_searches SET last_run_ts=0 WHERE id=?", (sid,))
        db_log("s1", "Site", "http://x/ddwtoken-1", "failed",
               filename="ddwtoken-1.mp4")
        out = ss.run_due()
        assert out["ran"] >= 1
        assert calls["n"] >= 1
    finally:
        ss._reset_enqueue_handler()
