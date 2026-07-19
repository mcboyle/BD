"""v3.66.489 K2 (plugin-v3 kind): pre-enqueue filter plugins.

Decide / rewrite / tag a URL BEFORE it is queued. Fills the real pipeline gap
(post-download processors exist; there was no pre-enqueue gate). This is the
filter chain that K6 source plugins will later route their emitted URLs through.

Contract:
  prefilter(url, meta, ctx) -> {action: keep|drop|rewrite, url?, tags?, reason?}

Chain semantics (plugins.run_prefilters):
  * ordered by priority (lower first);
  * first ``drop`` wins and SHORT-CIRCUITS (later filters do not run);
  * ``rewrite`` mutates the threaded URL and COMPOSES (a later filter sees the
    rewritten URL); tags accumulate;
  * ``keep`` / no-opinion passes through unchanged;
  * a THROWING filter fails OPEN -- the URL still enqueues (a buggy filter must
    never silently swallow work) -- and is quarantine-counted.

K2 raises PLUGIN_API_MAX to 4 (range model; api 2/3 plugins keep loading).

Runner-safe: zero-arg fns, no pytest builtins, globals restored in try/finally.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


# ── api range model: K2 raises PLUGIN_API_MAX to 4 ────────────────────
def test_api_max_raised_to_4_keeps_prior_compatible():
    assert P.PLUGIN_API_MAX >= 4
    for v in (2, 3, 4):
        ok, _ = P.api_compatible({"api_version": v})
        assert ok, f"api={v} must stay compatible under the range model"


def test_prefilter_capability_documented():
    assert getattr(P, "CAP_PREFILTER", None) == "prefilter"
    ke = P.known_events()
    assert P.CAP_PREFILTER in ke["capabilities"]
    assert ke["api_max"] >= 4


# ── registry / introspection ──────────────────────────────────────────
def test_register_list_status_reset():
    P.reset()
    try:
        P.register_prefilter(lambda u, m, c: {"action": "keep"},
                             name="f1", priority=10)
        assert "f1" in [f["name"] for f in P.list_prefilters()]
        assert "prefilters" in P.status()
        P.reset()
        assert P.list_prefilters() == []
    finally:
        P.reset()


# ── (a) drop removes a URL pre-enqueue with a logged reason ───────────
def test_drop_removes_url_with_reason():
    P.reset()
    try:
        def in_library(url, meta, ctx):
            return {"action": "drop", "reason": "already in library"}
        P.register_prefilter(in_library, name="lib")
        res = P.run_prefilters("https://x/v/1", {}, {})
        assert res["action"] == "drop"
        assert "already in library" in res.get("reasons", [])
    finally:
        P.reset()


# ── (b) rewrite mutates the enqueued URL ──────────────────────────────
def test_rewrite_mutates_url():
    P.reset()
    try:
        def to_desktop(url, meta, ctx):
            return {"action": "rewrite", "url": url.replace("m.", "www.")}
        P.register_prefilter(to_desktop, name="desktop")
        res = P.run_prefilters("https://m.site/v/1", {}, {})
        assert res["action"] == "keep"
        assert res["url"] == "https://www.site/v/1"
    finally:
        P.reset()


# ── (c) keep passes through unchanged ─────────────────────────────────
def test_keep_passes_through():
    P.reset()
    try:
        P.register_prefilter(lambda u, m, c: {"action": "keep"}, name="k")
        P.register_prefilter(lambda u, m, c: {}, name="noop")  # no opinion
        res = P.run_prefilters("https://site/v/1", {}, {})
        assert res["action"] == "keep"
        assert res["url"] == "https://site/v/1"
    finally:
        P.reset()


# ── (d) priority order respected + first drop short-circuits ──────────
def test_priority_order_and_first_drop_wins():
    P.reset()
    try:
        order = []

        def rw_low(url, meta, ctx):
            order.append("low")
            return {"action": "rewrite", "url": url + "?a"}

        def rw_high(url, meta, ctx):
            order.append("high")
            # sees the low-priority rewrite already applied (compose)
            assert url.endswith("?a")
            return {"action": "rewrite", "url": url + "b"}
        P.register_prefilter(rw_high, name="high", priority=20)
        P.register_prefilter(rw_low, name="low", priority=10)
        res = P.run_prefilters("https://s/v", {}, {})
        assert order == ["low", "high"], "lower priority runs first"
        assert res["url"] == "https://s/v?ab"
    finally:
        P.reset()


def test_first_drop_short_circuits():
    P.reset()
    try:
        ran = []

        def early_drop(url, meta, ctx):
            return {"action": "drop", "reason": "blocked"}

        def later(url, meta, ctx):
            ran.append("later")
            return {"action": "rewrite", "url": url + "?x"}
        P.register_prefilter(early_drop, name="drop", priority=10)
        P.register_prefilter(later, name="later", priority=20)
        res = P.run_prefilters("https://s/v", {}, {})
        assert res["action"] == "drop"
        assert ran == [], "filters after the first drop must not run"
    finally:
        P.reset()


# ── (e) a throwing filter fails OPEN (URL still enqueues) ──────────────
def test_throwing_filter_fails_open():
    P.reset()
    try:
        def boom(url, meta, ctx):
            raise RuntimeError("buggy")

        def tagit(url, meta, ctx):
            return {"action": "keep", "tags": ["seen"]}
        P.register_prefilter(boom, name="boom", priority=10)
        P.register_prefilter(tagit, name="tag", priority=20)
        res = P.run_prefilters("https://s/v", {}, {})
        # buggy filter did NOT drop the URL
        assert res["action"] == "keep"
        assert res["url"] == "https://s/v"
        # the following filter still ran
        assert "seen" in res.get("tags", [])
    finally:
        P.reset()


# ── tags accumulate across the chain ──────────────────────────────────
def test_tags_accumulate():
    P.reset()
    try:
        P.register_prefilter(lambda u, m, c: {"action": "keep", "tags": ["a"]},
                             name="t1", priority=10)
        P.register_prefilter(lambda u, m, c: {"action": "rewrite",
                                              "url": u, "tags": ["b"]},
                             name="t2", priority=20)
        res = P.run_prefilters("https://s/v", {}, {})
        assert set(res.get("tags", [])) == {"a", "b"}
    finally:
        P.reset()


# ── decorator parity ──────────────────────────────────────────────────
def test_prefilter_decorator():
    P.reset()
    try:
        @P.prefilter(priority=5, name="deco")
        def _f(url, meta, ctx):
            return {"action": "drop", "reason": "deco"}
        res = P.run_prefilters("https://s/v", {}, {})
        assert res["action"] == "drop"
    finally:
        P.reset()
