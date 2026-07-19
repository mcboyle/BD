"""v3.66.492 K6 (plugin-v3 kind): source / watcher plugins.

Plugins that PRODUCE URLs to enqueue (RSS, folder-watch, search, account feed),
polled on a declared interval via bg_scheduler. Turns BD from pull-only into an
automation hub. A source's emitted URLs MUST pass through the K2 pre-enqueue
filter chain (the gate in front is load-bearing -- a source must not flood
unfiltered work).

Contract:
  poll(state, ctx) -> {urls: [...], next_state}

run_sources / poll_sources semantics:
  * the framework threads per-source ``state`` across polls (``next_state``
    persists) so a source dedupes its own emissions;
  * every emitted URL is routed through plugins.run_prefilters (K2): dropped
    URLs are filtered out, rewrites applied, tags carried;
  * a throwing / hung poll is isolated + bounded (rides the R1 timeout in
    _call_guarded); a sustained-failing source is quarantined (R2);
  * scheduling honors each source's interval via bg_scheduler.register.

K6 raises PLUGIN_API_MAX to 7.

Runner-safe: zero-arg fns, no pytest builtins, globals restored in try/finally.
"""
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def _reset():
    P.reset()
    P.clear_quarantine()


def test_api_max_raised_to_7_keeps_prior_compatible():
    assert P.PLUGIN_API_MAX >= 7
    for v in (2, 3, 4, 5, 6, 7):
        ok, _ = P.api_compatible({"api_version": v})
        assert ok


def test_source_capability_documented():
    assert getattr(P, "CAP_SOURCE", None) == "source"
    ke = P.known_events()
    assert P.CAP_SOURCE in ke["capabilities"]
    assert ke["api_max"] >= 7


def test_register_list_status_reset():
    _reset()
    try:
        P.register_source(lambda s, c: {"urls": [], "next_state": s},
                          name="rss", interval_seconds=900)
        rows = P.list_sources()
        assert "rss" in [r["name"] for r in rows]
        assert rows[0]["interval_seconds"] == 900
        assert "sources" in P.status()
        P.reset()
        assert P.list_sources() == []
    finally:
        _reset()


# ── (a) emitted URLs pass THROUGH the K2 filter chain ─────────────────
def test_emitted_urls_pass_through_k2_filter():
    _reset()
    try:
        def gate(u, m, c):
            if u == "u2":
                return {"action": "drop", "reason": "blocked"}
            if u == "u3":
                return {"action": "rewrite", "url": "u3x"}
            return {"action": "keep"}
        P.register_prefilter(gate, name="gate")
        P.register_source(lambda s, c: {"urls": ["u1", "u2", "u3"], "next_state": s},
                          name="src", interval_seconds=60)
        out = P.poll_sources({})
        assert out["src"]["emitted"] == ["u1", "u3x"]   # u2 dropped, u3 rewritten
        assert "u2" in out["src"]["dropped"]
    finally:
        _reset()


def test_enqueue_fn_receives_filtered_urls():
    _reset()
    try:
        enq = []
        P.register_source(lambda s, c: {"urls": ["a", "b"], "next_state": s},
                          name="s", interval_seconds=60)
        P.poll_sources({}, enqueue_fn=lambda url, meta: enq.append(url))
        assert enq == ["a", "b"]
    finally:
        _reset()


# ── (b) next_state dedupes across polls (no re-emit) ──────────────────
def test_next_state_dedupes_across_polls():
    _reset()
    try:
        def feed(state, ctx):
            seen = set(state.get("seen", []))
            allurls = ["u1", "u2", "u3"]
            new = [u for u in allurls if u not in seen]
            return {"urls": new, "next_state": {"seen": sorted(seen | set(allurls))}}
        P.register_source(feed, name="feed", interval_seconds=60)
        out1 = P.poll_sources({})
        out2 = P.poll_sources({})
        assert out1["feed"]["emitted"] == ["u1", "u2", "u3"]
        assert out2["feed"]["emitted"] == []   # state deduped
    finally:
        _reset()


# ── (c) throwing / hung poll isolated + bounded ───────────────────────
def test_throwing_poll_isolated():
    _reset()
    try:
        def boom(s, c):
            raise RuntimeError("nope")
        P.register_source(boom, name="boom", interval_seconds=60)
        P.register_source(lambda s, c: {"urls": ["g"], "next_state": s},
                          name="good", interval_seconds=60)
        out = P.poll_sources({})   # must not raise
        assert out["good"]["emitted"] == ["g"]
        assert out["boom"]["ok"] is False
    finally:
        _reset()


def test_hung_poll_bounded_by_timeout():
    _reset()
    try:
        def hang(s, c):
            time.sleep(1.0)
            return {"urls": ["late"], "next_state": s}
        P.register_source(hang, name="hang", interval_seconds=60, timeout=0.2)
        out = P.poll_sources({})
        assert out["hang"]["ok"] is False        # bounded, not hung
        assert out["hang"]["emitted"] == []
    finally:
        _reset()


# ── (d) interval honored via bg_scheduler ─────────────────────────────
def test_sources_scheduled_at_their_interval():
    _reset()
    try:
        P.register_source(lambda s, c: {"urls": [], "next_state": s},
                          name="rss", interval_seconds=900)
        registered = []

        def fake_register(name, fn, *, interval_seconds):
            registered.append((name, interval_seconds))
        P.schedule_sources(register=fake_register, ctx={})
        assert any(iv == 900 for _n, iv in registered)
    finally:
        _reset()


# ── (e) source quarantine on sustained failure (rides R2) ─────────────
def test_source_quarantined_on_sustained_failure():
    _reset()
    try:
        calls = {"n": 0}

        def bad(s, c):
            calls["n"] += 1
            raise RuntimeError("x")
        P.register_source(bad, name="bad", interval_seconds=60)
        for _ in range(P._FAIL_BUDGET):
            P.poll_sources({})
        before = calls["n"]
        out = P.poll_sources({})   # now quarantined -> skipped
        assert calls["n"] == before
        row = out.get("bad")
        assert row is None or row.get("skipped") is True
    finally:
        _reset()


# ── decorator parity ──────────────────────────────────────────────────
def test_source_decorator():
    _reset()
    try:
        @P.source(name="deco", interval_seconds=120)
        def _s(state, ctx):
            return {"urls": ["d"], "next_state": state}
        out = P.poll_sources({})
        assert out["deco"]["emitted"] == ["d"]
    finally:
        _reset()
