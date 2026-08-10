"""@1012. Two operator routes that could not answer in 8 seconds on a quiet app.

MEASURED ON THE BOX, the v3.66.1011 capture (2026-08-10). L34's verdict, after
@1010 made it able to sweep the whole operator surface for the first time:

    checked 264 operator in 68s: 0 5xx, 0 unreachable, 2 exceeded,
      156 recovered-on-serial (probe-induced, not findings), 0 unconfirmed,
      0 unprobed
    EXCEEDED  /api/cleanup/summary (> 8s SERIAL, on a quiet app)
    EXCEEDED  /api/community_scrapers/index (> 8s SERIAL, on a quiet app)

"WHEN PROBED ALONE" is the load-bearing part. Phase 1's concurrent sweep flags a
route as a SUSPECT; phase 2 re-probes it serially against a quiet app at the
full budget, and only what still fails there is reported. 156 of the 158 flags
recovered. These two did not. They are findings, not artifacts -- and they were
inside the 92 routes nobody probed at all before @1010, so this cut exists
because the previous one stopped hiding them.

WHY EACH IS SLOW, read from source:

  * `/api/cleanup/summary` -> `cleanup_helpers.summary()` runs FIVE full walks of
    the operator's download directories on a plain GET -- find_tinies,
    find_stale_partials, find_broken_symlinks, find_empty_dirs,
    find_orphans_for. The box reports 1959.6 GB free on that volume. Nothing
    caches, and `summary()` has exactly one caller, so the cache belongs there.
  * `/api/community_scrapers/index` -> `community_scrapers.fetch_index()`, whose
    signature is `timeout_s: float = 30.0`. The route passes no timeout, so a
    slow GitHub holds a worker for up to 30s -- nearly four times L34's budget
    and far past any operator's patience. The library's default is fine for a
    CLI; a route serving a UI has to bound it.

WHAT THIS CUT DOES NOT DO. It does not raise `_L34_ROUTE_BUDGET_S`, and
live_tests/checks.py says why in its own words: "Raising this number to make the
check stop complaining is the wrong lever; it hides the defect." Nor does it
declare either route in `_L34_STREAMING_SKIP` -- that list is for endpoints that
200 on connect and stream forever, and neither of these does. The check offered
both escapes in its failure message and both would have been dishonest.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# ── /api/cleanup/summary ──────────────────────────────────────────

@pytest.fixture
def ch(monkeypatch, tmp_path):
    """cleanup_helpers with every finder replaced by a counting stub.

    The finders are the subject's COST, not its behaviour: this file asserts how
    often they run, never what they return. Stubbing them also keeps the test
    off the real filesystem, which is the thing that made the route slow.
    """
    from bulk_downloader import cleanup_helpers as _ch
    calls = {"n": 0}

    def _counted(*a, **k):
        calls["n"] += 1
        return []

    # ALL SIX. The first draft stubbed five and asserted five -- but summary()
    # also calls find_missing_metadata(), which would then have run for real
    # against the filesystem while the test claimed to have replaced every
    # finder. A stub set that excludes one of its own subjects is CLAUDE.md
    # section 0 in the fixture.
    for name in ("find_tinies", "find_stale_partials", "find_broken_symlinks",
                 "find_empty_dirs", "find_orphans_for", "find_missing_metadata"):
        monkeypatch.setattr(_ch, name, _counted)
    _ch.summary_cache_clear()
    return _ch, calls


def test_the_helper_exposes_a_cache_control():
    from bulk_downloader import cleanup_helpers as _ch
    for name in ("summary_cache_clear", "SUMMARY_TTL_S"):
        assert hasattr(_ch, name), "cleanup_helpers.%s is missing" % name


def test_a_second_call_inside_the_ttl_does_NOT_re_walk(ch):
    _ch, calls = ch
    _ch.summary(s_cfg={})
    first = calls["n"]
    assert first == 6, "expected six finders on a cold call, got %d" % first
    _ch.summary(s_cfg={})
    assert calls["n"] == first, (
        "the second call re-ran %d finder(s) inside the TTL -- every operator "
        "page load pays five filesystem walks" % (calls["n"] - first))


def test_force_bypasses_the_cache(ch):
    """A cached snapshot the operator cannot refresh is worse than no cache.
    `force` mirrors the convention /api/community_scrapers/index already uses."""
    _ch, calls = ch
    _ch.summary(s_cfg={})
    n = calls["n"]
    _ch.summary(s_cfg={}, force=True)
    assert calls["n"] == n + 6, "force did not re-run the finders"


def test_the_cache_EXPIRES(ch, monkeypatch):
    """THE OTHER DIRECTION. A cache that never expires is a stale snapshot
    presented as current -- the operator cleans up, reloads, and sees the old
    numbers. Time is injected rather than slept: a test that waits out a real
    TTL is a slow test that also pins the TTL to whatever it waited."""
    _ch, calls = ch
    now = [1000.0]
    monkeypatch.setattr(_ch.time, "monotonic", lambda: now[0])
    _ch.summary_cache_clear()
    _ch.summary(s_cfg={})
    n = calls["n"]
    now[0] += _ch.SUMMARY_TTL_S + 1.0
    _ch.summary(s_cfg={})
    assert calls["n"] == n + 6, (
        "nothing re-walked after the TTL elapsed -- the cache never expires")


def test_a_DIFFERENT_config_is_not_served_the_previous_answer(ch):
    """The cache key has to contain the directories being summarised. Keyed on
    nothing, a second site's summary would be answered with the first's."""
    _ch, calls = ch
    _ch.summary(s_cfg={"a": {"download_dir": "/tmp/one"}})
    n = calls["n"]
    _ch.summary(s_cfg={"b": {"download_dir": "/tmp/two"}})
    assert calls["n"] == n + 6, (
        "a different download_dir was served the cached answer for another one")


def test_the_cached_value_is_the_same_SHAPE_as_a_fresh_one(ch):
    """Caching must not change the contract. Compared by key set, not by value:
    the stubs return [] so the counts are all zero either way, and asserting
    equal values would pass on two empty dicts."""
    _ch, _calls = ch
    a = _ch.summary(s_cfg={})
    b = _ch.summary(s_cfg={})
    assert set(a) == set(b) and set(a) >= {
        "tinies", "stale_partials", "broken_symlinks", "empty_dirs", "orphans"}


# ── /api/community_scrapers/index ─────────────────────────────────

def _index_view_source():
    src = (REPO / "bulk_downloader" / "app_community_scrapers.py").read_text(
        encoding="utf-8")
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef)
               and n.name == "api_community_scrapers_index"), None)
    assert fn is not None, "api_community_scrapers_index not found"
    return fn


def test_the_index_route_bounds_its_github_fetch():
    """`fetch_index` defaults to timeout_s=30.0. A route that does not override
    it can hold a worker for 30 seconds on a third party's latency."""
    fn = _index_view_source()
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and ((isinstance(n.func, ast.Name) and n.func.id == "fetch_index")
                  or (isinstance(n.func, ast.Attribute)
                      and n.func.attr == "fetch_index"))]
    assert calls, "the scan found no fetch_index call -- it went blind"
    for c in calls:
        kw = {k.arg for k in c.keywords}
        assert "timeout_s" in kw, (
            "fetch_index is called without timeout_s, so it inherits the "
            "library default of 30.0s")


def test_the_route_timeout_is_under_L34s_budget():
    """Bounded is not enough -- bounded BELOW the gate. A 10s timeout would
    satisfy the test above and still fail the capture."""
    from live_tests import checks
    from bulk_downloader import app_community_scrapers as acs
    assert hasattr(acs, "INDEX_FETCH_TIMEOUT_S"), (
        "the timeout is inline; name it so the relationship below can be "
        "asserted rather than re-derived by a reader")
    assert acs.INDEX_FETCH_TIMEOUT_S < checks._L34_ROUTE_BUDGET_S, (
        "route timeout %.1fs is not under L34's %.1fs per-route budget"
        % (acs.INDEX_FETCH_TIMEOUT_S, checks._L34_ROUTE_BUDGET_S))
    assert acs.INDEX_FETCH_TIMEOUT_S > 0


def test_the_bound_is_ACTUALLY_PASSED_at_runtime(monkeypatch):
    """The AST says the argument is written; this says the value arrives.

    Both, because they fail differently: a rename of the module-level constant
    leaves the AST check green, and an inline literal leaves the runtime check
    green while the naming test above is what catches it.
    """
    import importlib
    from bulk_downloader import app_community_scrapers as acs
    seen = {}

    class _FakeCS:
        @staticmethod
        def fetch_index(**kwargs):
            seen.update(kwargs)
            return [], ""

    app_mod = importlib.import_module("bulk_downloader.app")
    monkeypatch.setattr(app_mod, "_community_scrapers", _FakeCS, raising=False)
    monkeypatch.setattr(app_mod, "_COMMUNITY_SCRAPERS_AVAILABLE", True,
                        raising=False)
    with app_mod.app.test_request_context("/api/community_scrapers/index"):
        acs.api_community_scrapers_index()
    assert seen.get("timeout_s") == acs.INDEX_FETCH_TIMEOUT_S, (
        "the view called fetch_index with timeout_s=%r" % (seen.get("timeout_s"),))


# ── neither escape hatch was taken ────────────────────────────────

def test_L34s_per_route_budget_was_not_raised():
    """The check's failure message offers two ways out and both would be
    dishonest. This pins the one that would have been silent."""
    from live_tests import checks
    assert checks._L34_ROUTE_BUDGET_S <= 8, (
        "the per-route budget rose to %s -- checks.py: 'Raising this number to "
        "make the check stop complaining is the wrong lever; it hides the "
        "defect.'" % checks._L34_ROUTE_BUDGET_S)


@pytest.mark.parametrize("route", ["/api/cleanup/summary",
                                   "/api/community_scrapers/index"])
def test_neither_route_was_declared_a_stream(route):
    """_L34_STREAMING_SKIP is for endpoints that 200 on connect and stream
    forever. Neither of these does; both return jsonify(...)."""
    from live_tests import checks
    assert route not in checks._L34_STREAMING_SKIP, (
        "%s was declared a stream to silence the check; it is a plain "
        "jsonify view" % route)
